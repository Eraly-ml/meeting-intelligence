import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_station.config import Settings, private_url
from meeting_station.main import create_app
from meeting_station.store import Store
from meeting_station.worker import MacClient, WorkerUnavailable


TOKEN = "station-test-token-123456"
WORKER_TOKEN = "worker-test-token-1234567"


class FakeMac:
    def __init__(self):
        self.offline = False
        self.jobs = {}
        self.uploads = []

    async def close(self):
        pass

    async def upload(self, job, source):
        if self.offline:
            raise WorkerUnavailable("Mac worker is offline; archive is preserved")
        self.uploads.append((job["id"], source.read_bytes()))
        result = {"id": job["id"], "stage": "queued"}
        self.jobs[job["id"]] = result
        return result

    async def json(self, method, path, **kwargs):
        if self.offline:
            raise WorkerUnavailable("Mac worker is offline; archive is preserved")
        if path == "/v1/capabilities":
            return {"offline": True, "diarization": False, "asr_profiles": ["test-only"]}
        job_id = path.split("/")[3]
        if path.endswith("/result"):
            return {"job": {"id": job_id}, "transcript": {"raw_text": "Meeting test", "segments": []},
                    "protocol": {"metadata": {"title": "Planning"}}, "exports": {}}
        if path.endswith("/cancel"):
            self.jobs[job_id] = {"id": job_id, "stage": "cancelled"}
        if path.endswith("/retry"):
            self.jobs[job_id] = {"id": job_id, "stage": "queued"}
        return self.jobs[job_id]

    async def export(self, job_id, format_name, target):
        target.write_bytes(b"%PDF-1.7 test fixture" if format_name == "pdf" else b"fixture export")


@pytest.fixture
def settings(tmp_path):
    return Settings(token=TOKEN, worker_token=WORKER_TOKEN, worker_url="http://127.0.0.1:8765", data_dir=tmp_path / "station", poll_seconds=0.001)


@pytest.fixture
def client(settings):
    mac = FakeMac()
    app = create_app(settings, mac=mac, start_worker=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        yield client


def submit(client, content=b"test audio", job_id=None):
    job_id = job_id or str(uuid4())
    return client.post("/v1/jobs", headers={"Idempotency-Key": job_id},
                       data={"manifest_json": json.dumps({"meeting_id": job_id, "title": "Planning"})},
                       files={"audio": ("meeting.wav", content, "audio/wav")})


def ready(client):
    client.app.state.store.db.execute("UPDATE jobs SET available_at=0")


def test_auth_before_body_parsing_and_no_token_exposure(client):
    assert client.get("/health", headers={"Authorization": ""}).json()["role"] == "station"
    assert client.post("/v1/jobs", headers={"Authorization": ""}, content="malformed").status_code == 401
    assert client.get("/v1/capabilities", headers={"Authorization": ""}).status_code == 401
    capabilities = client.get("/v1/capabilities").json()
    assert capabilities["station"]["worker_connected"] is True
    assert WORKER_TOKEN not in json.dumps(capabilities)


def test_upload_archive_and_strict_idempotency(client, settings):
    job_id = str(uuid4())
    response = submit(client, job_id=job_id)
    assert response.status_code == 202
    job = response.json()
    assert job["stage"] == "queued" and job["station"]["archived"] is True
    assert client.get("/v1/jobs/" + job_id + "/audio").content == b"test audio"
    assert submit(client, job_id=job_id).json()["id"] == job_id
    assert submit(client, b"changed", job_id).status_code == 409
    assert client.get("/v1/jobs/" + job_id + "/audio").content == b"test audio"
    assert len(client.get("/v1/jobs").json()) == 1
    assert not list((settings.data_dir / "incoming").iterdir())
    assert not client.app.state.mac.uploads  # HTTP acceptance does not wait for or start inference.


def test_replay_of_pre_vocabulary_manifest_keeps_same_archived_meeting(client):
    job_id = submit(client).json()['id']
    store = client.app.state.store
    legacy = store.get(job_id)
    del legacy['manifest']['vocabulary']
    store.db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(legacy), job_id))
    assert submit(client, job_id=job_id).status_code == 202
    changed = dict(legacy['manifest'], vocabulary='Changed spelling hints')
    response = client.post('/v1/jobs', headers={'Idempotency-Key': job_id},
        data={'manifest_json': json.dumps(changed)}, files={'audio': ('meeting.wav', b'test audio', 'audio/wav')})
    assert response.status_code == 409


def test_upload_size_actual_bytes_invalid_id_and_formats(settings):
    app = create_app(replace(settings, max_upload_bytes=32), mac=FakeMac(), start_worker=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        assert submit(client, b"x" * 33).status_code == 413
        assert client.post("/v1/jobs", headers={"Idempotency-Key": "../../outside"}).status_code == 422
        response = client.post("/v1/jobs", data={"manifest_json": '{"meeting_id":"abc"}'}, files={"audio": ("program.exe", b"x")})
        assert response.status_code == 415
        assert client.get("/v1/jobs").json() == []


def test_chunked_upload_body_cap_cleans_parser_files(settings):
    app = create_app(replace(settings, max_upload_bytes=32), mac=FakeMac(), start_worker=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        body = b'--boundary\r\nContent-Disposition: form-data; name="audio"; filename="meeting.wav"\r\n\r\n' + b"x" * 70000 + b"\r\n--boundary--\r\n"
        response = client.post("/v1/jobs", headers={"Content-Type": "multipart/form-data; boundary=boundary"}, content=iter([body[:1000], body[1000:]]))
        assert response.status_code == 413
        assert client.get("/v1/jobs").json() == []


def test_offline_queue_survives_restart_and_completes_with_local_exports(client, settings):
    job_id = submit(client).json()["id"]
    mac = client.app.state.mac
    mac.offline = True
    assert asyncio.run(client.app.state.worker.run_once())
    pending = client.get("/v1/jobs/" + job_id).json()
    assert pending["stage"] == "queued" and pending["error_code"] == "ENGINE_OFFLINE"
    reopened = Store(settings.data_dir)
    try:
        reopened.recover()
        assert reopened.get(job_id)["stage"] == "queued"
        assert reopened.source(job_id).read_bytes() == b"test audio"
    finally:
        reopened.close()
    mac.offline = False
    ready(client)
    asyncio.run(client.app.state.worker.run_once())
    mac.jobs[job_id] = {"id": job_id, "stage": "completed"}
    ready(client)
    asyncio.run(client.app.state.worker.run_once())
    mac.offline = True
    assert client.get("/v1/jobs/" + job_id).json()["stage"] == "completed"
    assert client.get("/v1/jobs/" + job_id + "/result").json()["transcript"]["raw_text"] == "Meeting test"
    assert client.get("/v1/jobs/" + job_id + "/export/pdf").content.startswith(b"%PDF-")
    assert client.get("/v1/jobs/" + job_id + "/export/ics").content == b"fixture export"
    assert len(mac.uploads) == 1


def test_cancel_and_retry_deliver_durable_remote_actions(client):
    job_id = submit(client).json()["id"]
    asyncio.run(client.app.state.worker.run_once())
    assert client.post("/v1/jobs/" + job_id + "/cancel").json()["stage"] == "cancelled"
    asyncio.run(client.app.state.worker.run_once())
    assert client.app.state.mac.jobs[job_id]["stage"] == "cancelled"
    assert client.post("/v1/jobs/" + job_id + "/retry").json()["stage"] == "queued"
    asyncio.run(client.app.state.worker.run_once())
    assert client.app.state.mac.jobs[job_id]["stage"] == "queued"
    assert len(client.app.state.mac.uploads) == 1


def test_cancellation_racing_upload_is_forwarded_after_ack(client):
    job_id = submit(client).json()["id"]
    original = client.app.state.mac.upload

    async def upload_and_cancel(job, source):
        remote = await original(job, source)
        client.app.state.store.cancel(job_id)
        return remote

    client.app.state.mac.upload = upload_and_cancel
    asyncio.run(client.app.state.worker.run_once())
    assert client.app.state.store.get(job_id)["stage"] == "cancelled"
    ready(client)
    asyncio.run(client.app.state.worker.run_once())
    assert client.app.state.mac.jobs[job_id]["stage"] == "cancelled"


@pytest.mark.parametrize("remote_stage", ["transcribing", "completed"])
def test_retry_racing_cancel_or_mac_completion_reuses_existing_job(client, remote_stage):
    job_id = submit(client).json()["id"]
    asyncio.run(client.app.state.worker.run_once())
    mac = client.app.state.mac
    mac.jobs[job_id] = {"id": job_id, "stage": remote_stage}
    original = mac.json

    async def enforce_retry_precondition(method, path, **kwargs):
        if method == "POST" and path.endswith("/retry"):
            raise WorkerUnavailable("Only failed or cancelled jobs can be retried", "ENGINE_HTTP_409")
        return await original(method, path, **kwargs)

    mac.json = enforce_retry_precondition
    client.post("/v1/jobs/" + job_id + "/cancel")
    client.post("/v1/jobs/" + job_id + "/retry")
    asyncio.run(client.app.state.worker.run_once())
    result = client.app.state.store.get(job_id)
    assert result["stage"] == remote_stage
    assert result["error_code"] is None
    assert client.app.state.store.db.execute("SELECT action FROM jobs WHERE id=?", (job_id,)).fetchone()[0] is None
    assert len(mac.uploads) == 1
    if remote_stage == "completed":
        assert client.get("/v1/jobs/" + job_id + "/export/pdf").status_code == 200


def test_missing_job_after_mac_reset_resends_archived_source(client):
    job_id = submit(client).json()["id"]
    asyncio.run(client.app.state.worker.run_once())
    original = client.app.state.mac.json

    async def missing(method, path, **kwargs):
        raise WorkerUnavailable("Mac worker returned HTTP 404", "ENGINE_HTTP_404")

    client.app.state.mac.json = missing
    ready(client)
    asyncio.run(client.app.state.worker.run_once())
    job = client.app.state.store.get(job_id)
    assert job["stage"] == "queued" and not job["station"]["worker_submitted"]
    assert job["error_code"] == "ENGINE_JOB_MISSING"
    client.app.state.mac.json = original
    ready(client)
    asyncio.run(client.app.state.worker.run_once())
    assert len(client.app.state.mac.uploads) == 2
    assert client.app.state.mac.uploads[0] == client.app.state.mac.uploads[1]


def test_text_submission_and_audio_download_type(client):
    response = client.post("/v1/jobs", data={"manifest_json": '{"meeting_id":"text-meeting"}', "transcript": "Айбек подготовит отчёт."})
    assert response.status_code == 202
    job = response.json()
    assert job["source_kind"] == "text"
    assert client.get("/v1/jobs/" + job["id"] + "/audio").status_code == 404


def test_recording_requires_available_hardware(client, monkeypatch):
    from meeting_station.recorder import Recorder
    monkeypatch.setattr(Recorder, "available", property(lambda self: False))
    assert client.post("/v1/recordings/start", json={"title": "Room"}).status_code == 503
    assert client.get("/v1/jobs").json() == []


def test_single_process_owns_station_archive(settings):
    with TestClient(create_app(settings, mac=FakeMac(), start_worker=False)):
        with pytest.raises(RuntimeError, match="Another station"):
            with TestClient(create_app(settings, mac=FakeMac(), start_worker=False)):
                pass


@pytest.mark.parametrize("url", ["http://cloud.example:8765", "https://8.8.8.8", "http://192.168.1.42/path", "http://user:pass@192.168.1.42", "http://0.0.0.0:8765", "http://192.168.1.42?redirect=x"])
def test_engine_endpoint_is_private_and_pinned(url):
    with pytest.raises(ValueError):
        private_url(url)


def test_worker_transport_uses_separate_token_and_refuses_redirect(settings, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.example:8080")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer " + WORKER_TOKEN
        return httpx.Response(307, headers={"Location": "https://cloud.example/"})

    async def run():
        mac = MacClient(settings, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(WorkerUnavailable, match="redirect"):
                await mac.json("GET", "/v1/capabilities")
            assert not mac.client.trust_env and not mac.client.follow_redirects
        finally:
            await mac.close()

    asyncio.run(run())
    assert len(seen) == 1
