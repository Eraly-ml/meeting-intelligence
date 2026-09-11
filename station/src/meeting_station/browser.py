"""Authenticated browser view and durable import of isolated meeting audio."""
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import time
import wave
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import anyio
import httpx
from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect

from .models import Manifest, RecordingStart
from .store import StoreError


COOKIE = "meeting_browser_view"
VIEW_PREFIX = "/v1/browser/view/"


def validate_meeting_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError("Provide a valid HTTPS meeting link")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError("Meeting links must use HTTPS without credentials or fragments")
    host = (parsed.hostname or "").lower()
    if host == "meet.google.com" and re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}/?", parsed.path):
        return "meet"
    if (host == "zoom.us" or host.endswith(".zoom.us")) and re.match(r"/(j|wc|w)/[0-9]+(?:/|$)", parsed.path):
        return "zoom"
    if host in {"teams.microsoft.com", "teams.live.com"} and parsed.path.startswith(("/l/meetup-join/", "/meet/")):
        return "teams"
    raise ValueError("Use a Google Meet, Zoom or Microsoft Teams meeting link")


class BrowserSessions:
    def __init__(self):
        self.sessions = {}

    def issue(self):
        now = time.monotonic()
        self.sessions = {key: end for key, end in self.sessions.items() if end > now}
        if len(self.sessions) >= 128:
            del self.sessions[min(self.sessions, key=self.sessions.get)]
        value = secrets.token_urlsafe(32)
        self.sessions[value] = now + 1800
        return value

    def remaining(self, headers):
        try:
            cookie = SimpleCookie()
            cookie.load(headers.get(b"cookie", b"").decode("latin-1"))
            token = cookie[COOKIE].value
            return max(0, self.sessions.get(token, 0) - time.monotonic())
        except (KeyError, ValueError, CookieError):
            return 0

    def valid(self, scope):
        return scope["path"].startswith(VIEW_PREFIX) and self.remaining(dict(scope["headers"])) > 0


class BrowserClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.browser_url,
                                       headers={"Authorization": "Bearer " + settings.browser_token},
                                       trust_env=False, follow_redirects=False, timeout=httpx.Timeout(30, connect=2), transport=transport)

    async def close(self):
        await self.client.aclose()

    def check(self, response):
        if response.is_redirect:
            raise StoreError("Browser controller returned an unexpected redirect", 502)
        if response.status_code >= 400:
            message = "Meeting browser returned HTTP {}; retained recordings are preserved".format(response.status_code)
            raise StoreError(message, response.status_code)

    async def json(self, method, path, **kwargs):
        if not self.settings.browser_token:
            raise StoreError("Meeting browser is not configured on this station", 503)
        try:
            async with self.client.stream(method, path, **kwargs) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        raise StoreError("Browser controller response exceeds its size limit", 502)
                self.check(response)
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError("Expected object")
                return result
        except httpx.RequestError as exc:
            raise StoreError("Meeting browser is unavailable; saved recordings remain on the board", 503) from exc
        except (ValueError, TypeError) as exc:
            raise StoreError("Browser controller returned an invalid response", 502) from exc

    async def download(self, recording_id, temporary):
        try:
            async with self.client.stream("GET", "/v1/recordings/{}/audio".format(recording_id)) as response:
                if response.status_code >= 400:
                    await response.aread()
                self.check(response)
                size = 0
                digest = hashlib.sha256()
                async with await anyio.open_file(temporary, "xb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.settings.max_upload_bytes:
                            raise StoreError("Browser audio exceeds the station archive limit; original audio is retained", 413)
                        digest.update(chunk)
                        await output.write(chunk)
                if size <= 44:
                    raise StoreError("Browser capture produced no audio; original file is retained", 422)
            return size, digest.hexdigest()
        except httpx.RequestError as exc:
            raise StoreError("Browser audio import was interrupted; retry Stop and archive to import the retained recording", 503) from exc


class BrowserCapture:
    def __init__(self, store, browser):
        self.store, self.browser = store, browser
        self.lock = asyncio.Lock()

    async def join(self, body):
        async with self.lock:
            state = await self.browser.json('GET', '/v1/status')
            if state.get('active_recording_id'):
                raise StoreError('A meeting is already joining or recording')
            job_id = str(uuid4())
            manifest = Manifest(meeting_id=job_id, **body.model_dump(exclude={'url'})).model_dump(mode='json')
            self.store.create(job_id, manifest, 'audio', 'meeting-browser.wav', job_id + '.wav', 0, '', recording=True)
            try:
                await self.browser.json('POST', '/v1/join', json={'url':body.url, 'id':job_id})
            except StoreError:
                # A lost response may follow successful capture startup. Keep the
                # durable import pending so the reconciler can recover its WAV.
                self.store.browser_import_pending(job_id)
                with contextlib.suppress(StoreError):
                    confirmed = await self.browser.json('GET', '/v1/status')
                    if confirmed.get('available') and confirmed.get('active_recording_id') != job_id and not any(r.get('id') == job_id for r in confirmed.get('recordings', [])):
                        self.store.finish_recording(job_id, 'The meeting browser could not start joining. Check the station browser and try the link again.')
                raise
            return self.store.get(job_id)

    async def reconcile_once(self):
        state = await self.browser.json('GET', '/v1/status')
        for record in state.get('recordings', []):
            if record.get('state') == 'recording':
                continue
            try:
                job = self.store.get(record['id'])
            except StoreError:
                continue
            if job['stage'] == 'recording' and not job['station'].get('archived'):
                await self.stop(record['id'])

    async def run(self):
        while True:
            try:
                await self.reconcile_once()
            except (StoreError, OSError):
                pass  # Original audio stays in the controller until import succeeds.
            await asyncio.sleep(2)

    async def start(self, body):
        async with self.lock:
            state = await self.browser.json("GET", "/v1/status")
            if state.get("active_recording_id"):
                raise StoreError("A browser recording is already active")
            if not state.get("available"):
                raise StoreError("Meeting browser is not ready", 503)
            job_id = str(uuid4())
            manifest = Manifest(meeting_id=job_id, **body.model_dump()).model_dump(mode="json")
            self.store.create(job_id, manifest, "audio", "meeting-browser.wav", job_id + ".wav", 0, "", recording=True)
            try:
                await self.browser.json("POST", "/v1/recordings/start", json={"id": job_id})
            except StoreError:
                self.store.finish_recording(job_id, "Browser capture could not confirm startup. Check browser status, then retry Stop and archive if a recording exists")
                raise
            return self.store.get(job_id)

    async def stop(self, job_id):
        async with self.lock:
            job = self.store.get(job_id)
            if job["station"]["filename"] != "meeting-browser.wav":
                raise StoreError("This job is not a browser recording")
            if job["station"].get("archived"):
                return job
            temporary = self.store.incoming / (str(uuid4()) + ".browser.wav")
            try:
                stopped = await self.browser.json("POST", "/v1/recordings/{}/stop".format(job_id))
                await self.browser.download(job_id, temporary)
                await asyncio.to_thread(self.validate_wav, temporary)
                os.replace(temporary, self.store.source(job_id))
                self.store.source(job_id).chmod(0o600)
                error = stopped.get("error")
                return await asyncio.to_thread(self.store.finish_recording, job_id, error)
            except (StoreError, OSError, EOFError, wave.Error) as exc:
                self.store.browser_import_pending(job_id)
                if isinstance(exc, StoreError):
                    raise
                raise StoreError("Browser audio could not be validated or archived. Original audio is retained; retry Stop and archive", 502) from exc
            finally:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def validate_wav(path):
        with wave.open(str(path), "rb") as stream:
            if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2 or stream.getnframes() < 1:
                raise StoreError("Browser audio is not a valid mono 16 kHz PCM recording; original retained", 422)
            # A finalized header must describe actual audio bytes, not an open-ended recording.
            remaining = stream.getnframes()
            while remaining:
                chunk = stream.readframes(min(remaining, 16000))
                if not chunk:
                    raise StoreError("Browser WAV is incomplete; original audio is retained", 422)
                remaining -= len(chunk) // 2


class OpenMeeting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)


class JoinMeeting(RecordingStart):
    url: str = Field(min_length=1, max_length=4096)


def install_browser_routes(app, settings, sessions):
    @app.get("/v1/browser/status")
    async def status():
        try:
            return await app.state.browser.json("GET", "/v1/status")
        except StoreError as exc:
            return {"available": False, "state": "unavailable", "platform": None, "active_recording_id": None,
                    "recordings": [], "message": str(exc), "viewer_path": "/v1/browser/view/vnc_lite.html"}

    @app.post("/v1/browser/open")
    async def open_meeting(body: OpenMeeting):
        try:
            validate_meeting_url(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await app.state.browser.json("POST", "/v1/open", json={"url": body.url})

    @app.post("/v1/browser/session")
    async def session(request: Request):
        value = sessions.issue()
        public_path = settings.browser_public_prefix + VIEW_PREFIX
        response = JSONResponse({"viewer_path": public_path + "vnc_lite.html?path=" + public_path.lstrip("/") + "websockify", "expires_in": 1800})
        response.set_cookie(COOKIE, value, max_age=1800, path=public_path, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post('/v1/browser/join', status_code=202)
    async def join_meeting(body: JoinMeeting):
        try:
            validate_meeting_url(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await app.state.browser_capture.join(body)

    @app.post("/v1/browser/recordings/start", status_code=202)
    async def start(body: RecordingStart):
        return await app.state.browser_capture.start(body)

    @app.post("/v1/browser/recordings/{job_id}/stop")
    async def stop(job_id: UUID):
        return await app.state.browser_capture.stop(str(job_id))

    @app.get("/v1/browser/view/{asset:path}")
    async def view(request: Request, asset: str):
        if not sessions.valid(request.scope):
            raise HTTPException(401, "Open a new authenticated browser viewing session")
        if ".." in asset.split("/") or "\\" in asset or not re.fullmatch(r"[A-Za-z0-9_./-]+", asset):
            raise HTTPException(404, "Viewer asset not found")
        if asset != "vnc_lite.html" and not asset.startswith(("app/", "core/", "vendor/")):
            raise HTTPException(404, "Viewer asset not found")
        try:
            upstream = await app.state.viewer.send(app.state.viewer.build_request("GET", "/" + asset), stream=True)
        except httpx.RequestError as exc:
            raise HTTPException(503, "Meeting browser view is unavailable") from exc
        if upstream.status_code != 200:
            await upstream.aclose()
            raise HTTPException(404, "Viewer asset not found")
        return StreamingResponse(upstream.aiter_bytes(), media_type=upstream.headers.get("content-type", "application/octet-stream"),
                                 headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
                                          "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'"},
                                 background=BackgroundTask(upstream.aclose))

    @app.websocket("/v1/browser/view/websockify")
    async def viewer_socket(websocket: WebSocket):
        headers = dict(websocket.scope["headers"])
        expected_origin = ("https" if websocket.url.scheme == "wss" else "http") + "://" + websocket.headers.get("host", "")
        if not sessions.valid(websocket.scope) or websocket.headers.get("origin") != expected_origin:
            return await websocket.close(code=1008)
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidHandshake
        try:
            async with connect("ws://127.0.0.1:6080/websockify", subprotocols=["binary"], max_size=4 * 1024 * 1024, open_timeout=3, proxy=None,
                               additional_headers={"Authorization": "Bearer " + settings.browser_token}) as remote:
                offered = websocket.scope.get("subprotocols", [])
                await websocket.accept(subprotocol="binary" if "binary" in offered else None)

                async def client_to_browser():
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        data = message.get("bytes") if message.get("bytes") is not None else message.get("text", "")
                        if len(data) > 4 * 1024 * 1024:
                            await websocket.close(code=1009)
                            return
                        await remote.send(data)

                async def browser_to_client():
                    async for message in remote:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                tasks = [asyncio.create_task(client_to_browser()), asyncio.create_task(browser_to_client())]
                try:
                    await asyncio.wait(tasks, timeout=sessions.remaining(headers), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, TimeoutError, WebSocketDisconnect, ConnectionClosed, InvalidHandshake):
            pass
        finally:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()
