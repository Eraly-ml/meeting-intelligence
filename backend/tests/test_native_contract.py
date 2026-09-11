"""Exercise the wire sequence used by AppModel.syncNow and HubClient.

The only replacement is the local inference service; HTTP, SQLite, queue lifecycle,
and WebSocket notifications use the real hub implementation.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from meetingbox.config import Settings
from meetingbox.inference import InferenceError
from meetingbox.main import create_app
from meetingbox.models import Report


class RecoverableLocalModel:
    def __init__(self):
        self.calls = 0

    async def close(self):
        pass

    async def reconcile(self, meeting, through_sequence, final):
        self.calls += 1
        assert through_sequence == 101 and final
        assert len(meeting["segments"]) == 101
        if self.calls == 1:
            raise InferenceError("Local model is not running")
        return Report(
            summary="The team agreed to prepare the report.",
            decisions=[],
            action_items=[{"task": "Prepare the report", "owner": None, "due": None, "evidence": [101]}],
            open_questions=["Who owns the report?"], topics=["Планирование"], risks=[],
        )


def test_native_batching_lost_ack_end_replay_retry_and_final_websocket(tmp_path):
    settings = Settings(token="native-contract-token-12345", database=tmp_path / "hub.sqlite3", max_attempts=1)
    app = create_app(settings, reasoner=RecoverableLocalModel(), start_worker=False)
    headers = {"Authorization": "Bearer " + settings.token}
    meeting_id = str(uuid4())
    base = "/meetings/" + meeting_id
    title = "Native import test"
    segments = [{"sequence": sequence, "start": (sequence - 1) * 2.0,
                 "end": sequence * 2.0, "speaker": "unknown",
                 "text": "Prepare the report." if sequence == 101 else "Planning discussion."}
                for sequence in range(1, 102)]
    local_ack = 0
    local_end_synced = False

    with TestClient(app) as client:
        client.headers.update(headers)

        def native_start_and_snapshot():
            assert client.post("/meetings/start", json={"meeting_id": meeting_id, "title": title}).status_code == 200
            response = client.get(base)
            assert response.status_code == 200
            snapshot = response.json()
            # These are the required fields decoded by native HubMeeting.CodingKeys.
            assert {"meeting_id", "title", "status", "last_sequence", "segments", "report", "error"} <= snapshot.keys()
            assert snapshot["meeting_id"] == meeting_id
            assert 0 <= snapshot["last_sequence"] <= len(segments)
            return snapshot

        # First sync sends exactly AppModel's prefix(100). Pretend its ACK was lost.
        snapshot = native_start_and_snapshot()
        local_ack = snapshot["last_sequence"]
        pending = [item for item in segments if item["sequence"] > local_ack][:100]
        assert client.post(base + "/segments", json={"segments": pending}).json()["ack_sequence"] == 100
        assert local_ack == 0

        # Reconnect learns committed ACK from GET, then sends the last segment.
        local_ack = native_start_and_snapshot()["last_sequence"]
        assert local_ack == 100
        pending = [item for item in segments if item["sequence"] > local_ack][:100]
        local_ack = client.post(base + "/segments", json={"segments": pending}).json()["ack_sequence"]
        assert local_ack == len(segments)
        assert client.post(base + "/end", json={"last_sequence": len(segments)}).json()["status"] == "processing"

        # Simulate losing the /end reply before persisting endSynced locally.
        assert local_end_synced is False
        snapshot = native_start_and_snapshot()
        assert snapshot["status"] == "processing"
        assert client.post(base + "/end", json={"last_sequence": len(segments)}).status_code == 200
        local_end_synced = True
        assert client.post(base + "/segments", json={"segments": segments[:100]}).json()["ack_sequence"] == 101
        assert client.portal.call(app.state.worker.run_once) is True
        assert client.get(base).json()["status"] == "failed"

        # Native retries with an authenticated POST with no JSON body.
        with client.websocket_connect("/ws/" + meeting_id, headers=headers) as socket:
            failed = socket.receive_json()
            assert failed["meeting"]["status"] == "failed"
            assert failed["meeting"]["report"] is None
            assert client.post(base + "/retry").status_code == 200
            assert socket.receive_json()["meeting"]["status"] == "processing"
            assert client.portal.call(app.state.worker.run_once) is True
            final = socket.receive_json()
            assert final["type"] == "snapshot"
            assert final["meeting"]["status"] == "complete"
            assert final["meeting"]["error"] is None
            assert final["meeting"]["last_sequence"] == 101
            action = final["meeting"]["report"]["action_items"][0]
            assert action["owner"] is None and action["due"] is None
            assert action["evidence"] == [101]
            assert local_end_synced is True
