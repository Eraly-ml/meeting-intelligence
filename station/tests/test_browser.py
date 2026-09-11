import asyncio
import importlib.util
import io
from dataclasses import replace
from pathlib import Path
import wave

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meeting_station.browser import BrowserClient, COOKIE, validate_meeting_url
from meeting_station.main import create_app
from meeting_station.store import StoreError
from test_station import FakeMac, TOKEN, settings


def wav_bytes():
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x01\x00" * 1600)
    return target.getvalue()


class FakeBrowser:
    def __init__(self):
        self.recordings = {}
        self.active = None
        self.fail_download = False
        self.audio = wav_bytes()
        self.calls = []
        self.ended = False
        self.capture_error = None

    async def close(self):
        pass

    async def json(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/v1/status":
            return {"available": True, "state": "opened", "active_recording_id": self.active,
                'recordings':[{'id':id, 'state':'stopped' if self.ended else 'recording'} for id in self.recordings]}
        if path == "/v1/open":
            return {"available": True, "state": "opened", "message": "Join manually"}
        if path.endswith("/start") or path == '/v1/join':
            self.active = kwargs["json"]["id"]
            self.recordings[self.active] = self.audio
            return {"id": self.active, "state": "recording"}
        if path.endswith("/stop"):
            self.active = None
            return {"id": path.split("/")[3], "state": "stopped", "error": self.capture_error}
        raise AssertionError(path)

    async def download(self, recording_id, temporary):
        if self.fail_download:
            raise StoreError("Interrupted download; original retained", 503)
        temporary.write_bytes(self.recordings[recording_id])
        return len(self.audio), "test-only"


@pytest.fixture
def browser_client(settings):
    app = create_app(replace(settings, browser_public_prefix="", browser_token="browser-private-token-123456"), mac=FakeMac(), start_worker=False, browser=FakeBrowser())
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        yield client


@pytest.mark.parametrize("url", [
    "http://meet.google.com/qzi-ybjj-uzt", "https://meet.google.com.evil.test/qzi-ybjj-uzt",
    "https://meet.google.com@evil.test/qzi-ybjj-uzt", "https://meet.google.com:1234/qzi-ybjj-uzt",
    "file:///etc/passwd", "https://meet.google.com/", "https://zoom.us.evil.test/j/12345",
    "https://teams.microsoft.com/download", "https://meet.google.com/qzi-ybjj-uzt\n",
    "https://meet.google.com/qzi-ybjj-uzt#javascript", "https://127.0.0.1:9222/json",
])
def test_only_supported_meeting_links_navigate(browser_client, url):
    assert browser_client.post("/v1/browser/open", json={"url": url}).status_code == 422
    assert browser_client.app.state.browser.calls == []


@pytest.mark.parametrize("url,platform", [
    ("https://meet.google.com/qzi-ybjj-uzt", "meet"),
    ("https://us02web.zoom.us/j/123456789?pwd=meeting-password", "zoom"),
    ("https://teams.microsoft.com/l/meetup-join/meeting-id?context=opaque", "teams"),
])
def test_allowed_links_never_claim_participant_joined(browser_client, url, platform):
    assert validate_meeting_url(url) == platform
    result = browser_client.post("/v1/browser/open", json={"url": url})
    assert result.status_code == 200
    assert result.json()["state"] == "opened"
    assert browser_client.app.state.browser.calls[-1][2]["json"] == {"url": url}


def test_view_cookie_cannot_authorize_station_api_or_new_sessions(browser_client):
    response = browser_client.post("/v1/browser/session")
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=1800" in cookie
    assert "Secure" not in cookie  # Same-LAN HTTP fallback remains usable.
    path = response.json()["viewer_path"]
    assert "path=v1/browser/view/websockify" in path
    assert TOKEN not in path and browser_client.cookies[COOKIE] not in path
    browser_client.headers.pop("Authorization")
    forged = {"Cookie": COOKIE + "=" + browser_client.cookies[COOKIE]}
    assert browser_client.get("/v1/jobs", headers=forged).status_code == 401
    assert browser_client.post("/v1/browser/session", headers=forged).status_code == 401
    assert browser_client.post("/v1/browser/open", headers=forged, json={"url": "https://meet.google.com/qzi-ybjj-uzt"}).status_code == 401
    assert browser_client.get("/v1/browser/view/secret.env", headers=forged).status_code == 404
    browser_client.app.state.browser_sessions.sessions.clear()
    assert browser_client.get("/v1/browser/view/vnc_lite.html", headers=forged).status_code == 401


def test_secure_cookie_for_https(settings):
    with TestClient(create_app(settings, mac=FakeMac(), start_worker=False), base_url="https://station.local") as client:
        response = client.post("/v1/browser/session", headers={"Authorization": "Bearer " + TOKEN})
        assert "Secure" in response.headers["set-cookie"]


def test_view_requires_session_and_uses_private_token_instead_of_browser_credentials(browser_client):
    assert browser_client.get("/v1/browser/view/vnc_lite.html").status_code == 401
    browser_client.post("/v1/browser/session")
    observed = []

    def handler(request):
        observed.append(request)
        assert request.headers["Authorization"] == "Bearer browser-private-token-123456"
        assert "Cookie" not in request.headers
        return httpx.Response(200, text="viewer fixture", headers={"Content-Type": "text/html"})

    previous = browser_client.app.state.viewer
    browser_client.portal.call(previous.aclose)
    browser_client.app.state.viewer = httpx.AsyncClient(base_url="http://127.0.0.1:6080", headers=previous.headers,
                                                        transport=httpx.MockTransport(handler), trust_env=False)
    result = browser_client.get("/v1/browser/view/vnc_lite.html")
    assert result.status_code == 200 and result.text == "viewer fixture"
    assert result.headers["x-frame-options"] == "SAMEORIGIN"
    assert str(observed[0].url) == "http://127.0.0.1:6080/vnc_lite.html"


def test_websocket_rejects_cross_origin_and_missing_cookie(browser_client):
    browser_client.post("/v1/browser/session")
    with pytest.raises(WebSocketDisconnect) as error:
        with browser_client.websocket_connect("/v1/browser/view/websockify", headers={"Origin": "https://evil.test"}):
            pass
    assert error.value.code == 1008
    browser_client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with browser_client.websocket_connect("/v1/browser/view/websockify", headers={"Origin": "http://testserver"}):
            pass


def test_websocket_authenticated_binary_bridge_has_fixed_local_destination(browser_client, monkeypatch):
    import websockets.asyncio.client
    browser_client.post("/v1/browser/session")
    seen = []

    class Remote:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, data):
            pass

        async def __aiter__(self):
            yield b"RFB 003.008\n"
            await asyncio.Event().wait()

    def connect(url, **kwargs):
        seen.append((url, kwargs))
        return Remote()

    monkeypatch.setattr(websockets.asyncio.client, "connect", connect)
    with browser_client.websocket_connect("/v1/browser/view/websockify", headers={"Origin": "http://testserver"}, subprotocols=["binary"]) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
    assert seen[0][0] == "ws://127.0.0.1:6080/websockify"
    assert seen[0][1]["proxy"] is None
    assert seen[0][1]["additional_headers"] == {"Authorization": "Bearer browser-private-token-123456"}


def test_browser_capture_archives_audio_before_worker_receives_it(browser_client):
    response = browser_client.post("/v1/browser/recordings/start", json={"title": "Meet capture", "diarization": True})
    assert response.status_code == 202
    job = response.json()
    assert job["stage"] == "recording" and not job["station"]["archived"]
    assert browser_client.get("/v1/jobs/" + job["id"] + "/audio").status_code == 409
    assert browser_client.post("/v1/browser/recordings/start", json={}).status_code == 409
    result = browser_client.post("/v1/browser/recordings/" + job["id"] + "/stop")
    assert result.status_code == 200
    archived = result.json()
    assert archived["stage"] == "queued" and archived["station"]["archived"]
    assert archived["source_sha256"] and archived["station"]["source_bytes"] == len(wav_bytes())
    assert browser_client.get("/v1/jobs/" + job["id"] + "/audio").content == wav_bytes()
    asyncio.run(browser_client.app.state.worker.run_once())
    assert browser_client.app.state.mac.uploads == [(job["id"], wav_bytes())]
    assert browser_client.post("/v1/browser/recordings/" + job["id"] + "/stop").status_code == 200


def test_one_link_joins_and_archives_on_call_end_without_ui_polling(browser_client):
    response = browser_client.post('/v1/browser/join', json={'url':'https://meet.google.com/qzi-ybjj-uzt','title':'Automatic meeting'})
    assert response.status_code == 202
    job_id = response.json()['id']
    assert response.json()['stage'] == 'recording'
    browser = browser_client.app.state.browser
    assert browser.calls[-1][1] == '/v1/join'
    assert browser.calls[-1][2]['json']['id'] == job_id
    assert browser_client.post('/v1/browser/join', json={'url':'https://meet.google.com/qzi-ybjj-uzt'}).status_code == 409
    browser.ended = True
    browser.active = None
    asyncio.run(browser_client.app.state.browser_capture.reconcile_once())
    job = browser_client.get('/v1/jobs/'+job_id).json()
    assert job['stage'] == 'queued' and job['station']['archived']
    assert browser_client.get('/v1/jobs/'+job_id+'/audio').content == wav_bytes()
    asyncio.run(browser_client.app.state.worker.run_once())
    assert browser_client.app.state.mac.uploads == [(job_id,wav_bytes())]


def test_silent_capture_is_archived_with_error_and_never_sent_as_success(browser_client):
    browser = browser_client.app.state.browser
    browser.capture_error = 'No sound was captured. The original file is retained.'
    job_id = browser_client.post('/v1/browser/recordings/start', json={}).json()['id']
    result = browser_client.post('/v1/browser/recordings/' + job_id + '/stop').json()
    assert result['stage'] == 'failed' and result['station']['archived']
    assert result['error_message'] == browser.capture_error
    assert browser_client.get('/v1/jobs/' + job_id + '/audio').content == browser.audio
    asyncio.run(browser_client.app.state.worker.run_once())
    assert browser_client.app.state.mac.uploads == []


def test_automatic_join_rejects_untrusted_links_before_creating_jobs(browser_client):
    response = browser_client.post('/v1/browser/join', json={'url':'https://127.0.0.1:9222/json'})
    assert response.status_code == 422
    assert not browser_client.app.state.browser.calls
    assert browser_client.get('/v1/jobs').json() == []


def test_interrupted_import_retains_audio_and_stop_can_retry(browser_client):
    job_id = browser_client.post("/v1/browser/recordings/start", json={}).json()["id"]
    browser = browser_client.app.state.browser
    browser.fail_download = True
    assert browser_client.post("/v1/browser/recordings/" + job_id + "/stop").status_code == 503
    job = browser_client.get("/v1/jobs/" + job_id).json()
    assert job["stage"] == "recording" and job["error_code"] == "BROWSER_IMPORT_PENDING" and not job["station"]["archived"]
    assert browser.recordings[job_id] == wav_bytes()
    assert not list(browser_client.app.state.store.incoming.iterdir())
    browser.fail_download = False
    assert browser_client.post("/v1/browser/recordings/" + job_id + "/stop").json()["stage"] == "queued"


def test_controller_transport_auth_size_bound_and_no_redirect(settings, tmp_path):
    observed = []

    def handler(request):
        observed.append(request)
        assert request.headers["Authorization"] == "Bearer browser-private-token-123456"
        if request.url.path == "/v1/status":
            return httpx.Response(200, json={"available": True})
        if request.url.path == "/redirect":
            return httpx.Response(307, headers={"Location": "https://evil.test/"})
        return httpx.Response(200, content=b"x" * 5000)

    async def run():
        browser = BrowserClient(replace(settings, browser_token="browser-private-token-123456", max_upload_bytes=100), transport=httpx.MockTransport(handler))
        try:
            assert (await browser.json("GET", "/v1/status"))["available"]
            with pytest.raises(StoreError, match="redirect"):
                await browser.json("GET", "/redirect")
            with pytest.raises(StoreError, match="limit"):
                await browser.download("test", tmp_path / "audio.wav")
            assert not browser.client.trust_env and not browser.client.follow_redirects
        finally:
            await browser.close()
    asyncio.run(run())
    assert len(observed) == 3


def test_controller_recording_id_cannot_read_outside_archive(tmp_path):
    path = Path(__file__).parents[2] / "deploy" / "meeting-browser" / "controller.py"
    specification = importlib.util.spec_from_file_location("browser_controller", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    controller = module.Controller(tmp_path)
    with pytest.raises(ValueError):
        controller.path("../../etc/passwd", ".wav")
    with pytest.raises(ValueError):
        controller.start("../../etc/passwd")
    assert list(controller.directory.iterdir()) == []


def test_browser_pending_import_survives_station_restart(browser_client):
    from meeting_station.store import Store
    job_id = browser_client.post("/v1/browser/recordings/start", json={}).json()["id"]
    store = Store(browser_client.app.state.store.data_dir)
    try:
        store.recover()
        job = store.get(job_id)
        assert job["stage"] == "recording" and job["error_code"] == "BROWSER_IMPORT_PENDING"
        assert store.work() is None
    finally:
        store.close()
    assert browser_client.post("/v1/browser/recordings/" + job_id + "/stop").json()["stage"] == "queued"


def test_controller_preserves_ffmpeg_failure_even_when_partial_wav_is_readable(tmp_path):
    from uuid import uuid4
    from types import SimpleNamespace
    path = Path(__file__).parents[2] / "deploy" / "meeting-browser" / "controller.py"
    specification = importlib.util.spec_from_file_location("browser_controller_exit", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    controller = module.Controller(tmp_path)
    job_id = str(uuid4())
    controller.save({"id": job_id, "state": "recording", "error": None})
    controller.path(job_id, ".wav").write_bytes(wav_bytes())
    controller.active = job_id
    controller.process = SimpleNamespace(returncode=1, poll=lambda: 1)
    result = controller.stop(job_id)
    assert result["state"] == "interrupted" and result["error"]
    assert controller.path(job_id, ".wav").read_bytes() == wav_bytes()
