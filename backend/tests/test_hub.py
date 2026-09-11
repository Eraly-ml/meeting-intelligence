import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from meetingbox.config import Settings, local_model_url
from meetingbox.inference import InferenceError, OllamaReasoner
from meetingbox.main import Subscribers, create_app
from meetingbox.models import Report, Segment
from meetingbox.store import Store, StoreError
from meetingbox.worker import Worker


TOKEN = "testing-only-token-12345"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def segment(sequence=1, text="Dana will finish the report by Friday."):
    return {"sequence": sequence, "start": (sequence - 1) * 5.0, "end": sequence * 5.0, "speaker": "local", "text": text}


def report(evidence=1):
    return {"summary": "The team assigned the report.", "decisions": [],
            "action_items": [{"task": "Finish the report", "owner": "Dana", "due": "Friday", "evidence": [evidence]}],
            "open_questions": [], "topics": ["Report"], "risks": []}


class NoModel:
    async def close(self):
        pass

    async def reconcile(self, *args):
        raise InferenceError("Model intentionally unavailable for this test")


@pytest.fixture
def settings(tmp_path):
    return Settings(token=TOKEN, database=tmp_path / "hub.sqlite3", max_attempts=1)


@pytest.fixture
def client(settings):
    app = create_app(settings, reasoner=NoModel(), start_worker=False)
    with TestClient(app) as client:
        client.headers.update(HEADERS)
        yield client


def start(client):
    meeting_id = str(uuid4())
    response = client.post("/meetings/start", json={"meeting_id": meeting_id, "title": "Planning"})
    assert response.status_code == 200
    return meeting_id


def test_auth_required_before_body_parsing(client):
    response = client.post("/meetings/start", headers={"Authorization": "wrong"}, content="malformed")
    assert response.status_code == 401
    assert client.get("/meetings", headers={"Authorization": ""}).status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 404


def test_replays_ack_gaps_and_end_are_durable(client, settings):
    meeting_id = start(client)
    base = "/meetings/" + meeting_id
    assert client.post("/meetings/start", json={"meeting_id": meeting_id, "title": "Planning"}).status_code == 200
    assert client.post("/meetings/start", json={"meeting_id": meeting_id, "title": "Different"}).status_code == 409
    assert client.post(base + "/segments", json={"segments": [segment(2)]}).json() == {"ack_sequence": 0}
    assert client.post(base + "/end", json={"last_sequence": 2}).status_code == 409
    assert client.post(base + "/segments", json={"segments": [segment(1)]}).json() == {"ack_sequence": 2}
    assert client.post(base + "/end", json={"last_sequence": 1}).status_code == 409
    assert client.post(base + "/end", json={"last_sequence": 2}).json()["status"] == "processing"
    assert client.post(base + "/end", json={"last_sequence": 2}).status_code == 200
    assert client.post(base + "/segments", json={"segments": [segment(1), segment(2)]}).json()["ack_sequence"] == 2
    assert client.post(base + "/segments", json={"segments": [segment(3)]}).status_code == 409
    assert client.post(base + "/segments", json={"segments": [segment(1, "Changed")]}).status_code == 409
    reopened = Store(settings.database)
    try:
        assert reopened.snapshot(meeting_id)["last_sequence"] == 2
        assert len(reopened.snapshot(meeting_id)["segments"]) == 2
        assert reopened.connection.execute("SELECT COUNT(*) FROM jobs WHERE kind='final'").fetchone()[0] == 1
    finally:
        reopened.close()


def test_batch_conflict_rolls_back_all_segments(client):
    meeting_id = start(client)
    base = "/meetings/" + meeting_id
    client.post(base + "/segments", json={"segments": [segment()]})
    response = client.post(base + "/segments", json={"segments": [segment(2), segment(1, "Conflicting")]})
    assert response.status_code == 409
    assert len(client.get(base).json()["segments"]) == 1


def test_input_validation_and_body_bound(client):
    meeting_id = start(client)
    for invalid in [dict(segment(), sequence=True), dict(segment(), end=-1), dict(segment(), end=0, start=1), dict(segment(), speaker="Dana"), dict(segment(), text="   ")]:
        assert client.post("/meetings/" + meeting_id + "/segments", json={"segments": [invalid]}).status_code == 422
    assert client.post("/meetings/start", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413


def test_export_history_and_literal_search(client):
    meeting_id = start(client)
    client.post("/meetings/" + meeting_id + "/segments", json={"segments": [segment(text="We are 100% local.")]})
    assert client.get("/search", params={"q": "100%"}).json()["results"][0]["meeting_id"] == meeting_id
    assert client.get("/search", params={"q": "missing%"}).json()["results"] == []
    assert client.get("/meetings").json()["meetings"][0]["meeting_id"] == meeting_id
    exported = client.get("/meetings/" + meeting_id + "/export")
    assert "100% local" in exported.text
    assert "attachment" in exported.headers["content-disposition"]
    assert client.get("/meetings/" + meeting_id + "/export?format=json").json()["segments"][0]["sequence"] == 1


def test_websocket_auth_and_reconnect_snapshot(client):
    meeting_id = start(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/" + meeting_id, headers={"Authorization": "Bearer invalid"}):
            pass
    with client.websocket_connect("/ws/" + meeting_id, headers=HEADERS) as websocket:
        assert websocket.receive_json()["meeting"]["last_sequence"] == 0
        client.post("/meetings/" + meeting_id + "/segments", json={"segments": [segment()]})
        assert websocket.receive_json()["meeting"]["last_sequence"] == 1
    with client.websocket_connect("/ws/" + meeting_id, headers=HEADERS) as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["meeting"]["segments"][0]["text"] == segment()["text"]


def test_subscriber_notifications_are_bounded():
    async def check():
        listeners = Subscribers()
        queue = listeners.subscribe("meeting")
        for _ in range(1000):
            listeners.publish("meeting")
        assert queue.qsize() == 1
        listeners.unsubscribe("meeting", queue)
        assert not listeners.meetings

    asyncio.run(check())


def test_recover_running_final_after_process_restart(settings):
    meeting_id = str(uuid4())
    store = Store(settings.database)
    store.start(meeting_id, "Recovery")
    store.append(meeting_id, [Segment(**segment())])
    store.end(meeting_id, 1)
    first_job = store.claim()
    assert first_job["kind"] == "final"
    store.close()
    store = Store(settings.database)
    try:
        store.recover()
        recovered = store.claim()
        assert recovered["id"] == first_job["id"]
        assert store.snapshot(meeting_id)["status"] == "processing"
        store.complete(recovered, Report(**report()))
        assert store.snapshot(meeting_id)["status"] == "complete"
    finally:
        store.close()


def test_recovery_cannot_overwrite_final_with_old_incremental(settings):
    store = Store(settings.database, incremental_seconds=0)
    meeting_id = str(uuid4())
    try:
        store.start(meeting_id, "Recovery")
        store.append(meeting_id, [Segment(**segment())])
        store.schedule_due()
        running = store.claim()
        assert running["kind"] == "incremental"
        store.end(meeting_id, 1)
        store.recover()
        final = store.claim()
        assert final["kind"] == "final"
        store.complete(final, Report(**report()))
        assert store.claim() is None
    finally:
        store.close()


def test_incremental_jobs_coalesce_and_final_has_priority(settings):
    store = Store(settings.database, incremental_seconds=0)
    first, second = str(uuid4()), str(uuid4())
    try:
        store.start(first, "Live")
        store.append(first, [Segment(**segment())])
        store.schedule_due()
        store.append(first, [Segment(**segment(2))])
        store.schedule_due()
        jobs = store.connection.execute("SELECT * FROM jobs WHERE status='queued'").fetchall()
        assert len(jobs) == 1 and jobs[0]["through_sequence"] == 2
        store.start(second, "Final")
        store.end(second, 0)
        assert store.claim()["kind"] == "final"
    finally:
        store.close()


def test_worker_failure_is_visible_and_retry_preserves_transcript(settings):
    store = Store(settings.database)
    meeting_id = str(uuid4())
    notifications = []
    try:
        store.start(meeting_id, "Failure")
        store.append(meeting_id, [Segment(**segment())])
        store.end(meeting_id, 1)
        worker = Worker(store, NoModel(), settings, notifications.append)
        assert asyncio.run(worker.run_once()) is True
        failed = store.snapshot(meeting_id)
        assert failed["status"] == "failed"
        assert failed["report"] is None
        assert len(failed["segments"]) == 1
        assert failed["error"]
        assert notifications == [meeting_id]
        assert store.retry(meeting_id)["status"] == "processing"
        assert store.claim()["kind"] == "final"
    finally:
        store.close()


@pytest.mark.parametrize("url", ["http://example.com:11434", "https://ollama.com", "http://192.168.1.1:11434", "http://127.0.0.1/path", "http://user:pass@localhost", "http://127.0.0.1?redirect=x", "file:///tmp/model"])
def test_cloud_or_nonloopback_endpoints_rejected(url):
    with pytest.raises(ValueError):
        local_model_url(url)


def test_configuration_requires_token_and_local_model():
    with pytest.raises(ValueError):
        Settings(token="")
    with pytest.raises(ValueError):
        Settings(token=TOKEN, model="qwen3:cloud")
    assert local_model_url("http://localhost:11434") == "http://127.0.0.1:11434"
    assert local_model_url("http://[::1]:11434") == "http://[::1]:11434"


def test_schema_rejects_missing_and_fabricated_evidence():
    with pytest.raises(ValueError):
        Report(**report(999)).check_evidence({1})
    value = report()
    value["action_items"][0]["evidence"] = []
    with pytest.raises(ValidationError):
        Report(**value)
    value["action_items"][0]["evidence"] = [True]
    with pytest.raises(ValidationError):
        Report(**value)


def local_metadata():
    return {"details": {"format": "gguf"}, "model_info": {"general.parameter_count": 3000000000}}


def test_model_schema_payload_and_no_proxy_or_redirect(settings, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://cloud.example:8080")
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_metadata())
        payload = json.loads(request.content)
        assert payload["format"]["type"] == "object"
        assert payload["stream"] is False
        assert payload["truncate"] is False
        assert payload["think"] is False
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(report())}})

    async def check():
        model = OllamaReasoner(settings, transport=httpx.MockTransport(handle))
        try:
            result = await model.reconcile({"report": None, "report_sequence": 0, "segments": [segment()]}, 1, True)
            assert result.action_items[0].evidence == [1]
            assert model.client.trust_env is False
            assert model.client.follow_redirects is False
        finally:
            await model.close()

    asyncio.run(check())
    assert [request.url.path for request in seen] == ["/api/show", "/api/chat"]


@pytest.mark.parametrize("mode", ["redirect", "cloud", "invalid", "evidence", "truncated", "timeout"])
def test_model_errors_never_produce_fallback_reports(settings, mode):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if mode == "redirect":
            return httpx.Response(307, headers={"Location": "https://cloud.example/api/chat"})
        if request.url.path == "/api/show":
            return httpx.Response(200, json=dict(local_metadata(), remote_model="remote") if mode == "cloud" else local_metadata())
        if mode == "timeout":
            raise httpx.ReadTimeout("timeout")
        content = "not json" if mode == "invalid" else json.dumps(report(999) if mode == "evidence" else report())
        return httpx.Response(200, json={"done": True, "done_reason": "length" if mode == "truncated" else "stop", "message": {"content": content}})

    async def check():
        model = OllamaReasoner(settings, transport=httpx.MockTransport(handle))
        try:
            with pytest.raises(InferenceError):
                await model.reconcile({"report": None, "report_sequence": 0, "segments": [segment()]}, 1, True)
        finally:
            await model.close()

    asyncio.run(check())
    if mode in ("redirect", "cloud"):
        assert paths == ["/api/show"]


def test_progressive_reconciliation_visits_every_character(settings):
    texts = ["Dana " * 1500, "Actually Timur owns it. " * 500, "Срок понедельник. " * 300]
    observed = {1: "", 2: "", 3: ""}
    calls = []

    def handle(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_metadata())
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        calls.append(data)
        for piece in data["transcript"]:
            observed[piece["sequence"]] += piece["text"]
        result = {"summary": "Progress", "decisions": [], "action_items": [], "open_questions": [], "topics": [], "risks": []}
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(result)}})

    async def check():
        model = OllamaReasoner(settings, transport=httpx.MockTransport(handle))
        try:
            await model.reconcile({"report": None, "report_sequence": 0, "segments": [segment(i + 1, text) for i, text in enumerate(texts)]}, 3, True)
        finally:
            await model.close()

    asyncio.run(check())
    assert [observed[i] for i in (1, 2, 3)] == texts
    assert len(calls) > 3
    assert calls[-1]["final_transcript_batch"] is True
    assert all(call["previous_report"] is not None for call in calls[1:])


def test_second_hub_process_is_refused(settings):
    first = create_app(settings, reasoner=NoModel(), start_worker=False)
    second = create_app(settings, reasoner=NoModel(), start_worker=False)
    with TestClient(first):
        with pytest.raises(RuntimeError, match="Another MeetingBox"):
            with TestClient(second):
                pass


def test_final_reconciliation_uses_speech_clock_not_delivery_sequence(settings):
    captured = []

    def handle(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_metadata())
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        captured.extend(data["transcript"])
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(report(1))}})

    async def check():
        model = OllamaReasoner(settings, transport=httpx.MockTransport(handle))
        try:
            speech = [dict(segment(1, "Actually Timur owns it"), start=15, end=20), dict(segment(2, "Dana owns it"), start=0, end=5)]
            await model.reconcile({"report": None, "report_sequence": 0, "segments": speech}, 2, True)
        finally:
            await model.close()

    asyncio.run(check())
    assert [piece["sequence"] for piece in captured] == [2, 1]


def test_failure_backoff_does_not_block_other_meetings(settings):
    settings = Settings(token=TOKEN, database=settings.database, max_attempts=3)
    store = Store(settings.database)
    first, second = str(uuid4()), str(uuid4())
    try:
        for meeting_id in (first, second):
            store.start(meeting_id, "Queue")
            store.append(meeting_id, [Segment(**segment())])
            store.end(meeting_id, 1)
        worker = Worker(store, NoModel(), settings, lambda _: None)
        asyncio.run(worker.run_once())
        assert store.snapshot(first)["status"] == "processing"
        assert store.snapshot(first)["error"]
        next_job = store.claim()
        assert next_job["meeting_id"] == second
    finally:
        store.close()


def test_successful_worker_stores_validated_report(settings):
    class WorkingModel(NoModel):
        async def reconcile(self, meeting, through_sequence, final):
            assert through_sequence == 1 and final is True
            return Report(**report())

    store = Store(settings.database)
    meeting_id = str(uuid4())
    try:
        store.start(meeting_id, "Working")
        store.append(meeting_id, [Segment(**segment())])
        store.end(meeting_id, 1)
        worker = Worker(store, WorkingModel(), settings, lambda _: None)
        asyncio.run(worker.run_once())
        snapshot = store.snapshot(meeting_id)
        assert snapshot["status"] == "complete"
        assert snapshot["report_sequence"] == 1
        assert snapshot["report"]["action_items"][0]["owner"] == "Dana"
        assert snapshot["error"] is None
    finally:
        store.close()
