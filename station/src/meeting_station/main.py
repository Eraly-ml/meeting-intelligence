import asyncio
import contextlib
import fcntl
import hashlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from .config import Settings
from .models import Manifest, RecordingStart
from .recorder import Recorder
from .store import Store, StoreError
from .worker import MacClient, Worker, WorkerUnavailable
from .browser import BrowserCapture, BrowserClient, BrowserSessions, install_browser_routes


AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".caf", ".flac"}
TOO_LARGE = "Upload exceeds the station request size limit"


class Boundary:
    """Authentication precedes parsing, and incoming uploads stay streamed and bounded."""
    def __init__(self, app, token, max_upload_bytes, browser_sessions=None):
        self.app = app
        self.expected = ("Bearer " + token).encode("utf-8")
        self.max_upload_bytes = max_upload_bytes
        self.browser_sessions = browser_sessions

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        public = scope["path"] == "/health" and scope["method"] == "GET"
        viewer = self.browser_sessions and self.browser_sessions.valid(scope)
        if not public and not viewer and not hmac.compare_digest(headers.get(b"authorization", b""), self.expected):
            return await JSONResponse({"detail": "Valid station Bearer token required"}, status_code=401,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        multipart = scope["path"] == "/v1/jobs" and scope["method"] == "POST"
        maximum = self.max_upload_bytes + 65536 if multipart else 2 * 1024 * 1024
        try:
            if int(headers.get(b"content-length", b"0")) > maximum:
                return await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                # Starlette's multipart parser closes its temporary files on this exception.
                if multipart:
                    raise MultiPartException(TOO_LARGE)
                raise HTTPException(413, TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings=None, mac=None, start_worker=True, browser=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (settings.data_dir / "station.lock").open("a")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("Another station process owns this archive; run one Uvicorn worker")
        store = Store(settings.data_dir)
        store.recover()
        remote = mac or MacClient(settings)
        recorder = Recorder(store, settings)
        app.state.browser = browser or BrowserClient(settings)
        app.state.browser_capture = BrowserCapture(store, app.state.browser)
        app.state.viewer = httpx.AsyncClient(base_url="http://127.0.0.1:6080", trust_env=False,
                                            follow_redirects=False, timeout=10,
                                            headers={"Authorization": "Bearer " + settings.browser_token})
        app.state.store, app.state.mac, app.state.recorder = store, remote, recorder
        app.state.worker = Worker(store, remote, settings)
        task = asyncio.create_task(app.state.worker.run()) if start_worker else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await recorder.close()
            await remote.close()
            await app.state.browser.close()
            await app.state.viewer.aclose()
            store.close()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    app = FastAPI(title="Meeting Intelligence Station", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    browser_sessions = BrowserSessions()
    app.state.browser_sessions = browser_sessions
    app.add_middleware(Boundary, token=settings.token, max_upload_bytes=settings.max_upload_bytes,
                       browser_sessions=browser_sessions)
    install_browser_routes(app, settings, browser_sessions)

    @app.exception_handler(StoreError)
    async def store_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0", "role": "station"}

    @app.get("/v1/capabilities")
    async def capabilities():
        connected = False
        error = None
        try:
            result = await app.state.mac.json("GET", "/v1/capabilities", timeout=2.0)
            connected = True
        except WorkerUnavailable as exc:
            result = {"offline": True, "asr_profiles": [], "language_modes": ["kk_ru", "en", "auto"], "diarization": False}
            error = str(exc)
        result["station"] = {"recording_available": app.state.recorder.available,
                             "active_recording_id": app.state.recorder.active_id,
                             "max_upload_bytes": settings.max_upload_bytes,
                             "accepted_audio": sorted(AUDIO_EXTENSIONS),
                             "worker_connected": connected, "worker_error": error}
        return result

    @app.post("/v1/jobs", status_code=202)
    async def create_job(request: Request):
        key = request.headers.get("idempotency-key")
        try:
            job_id = str(UUID(key)) if key else str(uuid4())
        except ValueError as exc:
            raise HTTPException(422, "Idempotency-Key must be a UUID") from exc
        temporary = app.state.store.incoming / (str(uuid4()) + ".upload")
        try:
            async with request.form(max_files=1, max_fields=2, max_part_size=2 * 1024 * 1024) as form:
                if set(form.keys()) - {"manifest_json", "audio", "transcript"}:
                    raise HTTPException(400, "Unexpected form field")
                if len(form.multi_items()) != len(form):
                    raise HTTPException(400, "Duplicate form fields are not allowed")
                try:
                    manifest = Manifest.model_validate_json(form.get("manifest_json", "")).model_dump(mode="json")
                except (ValueError, TypeError) as exc:
                    raise HTTPException(422, "Invalid meeting manifest") from exc
                audio, transcript = form.get("audio"), form.get("transcript")
                if (audio is None) == (transcript is None):
                    raise HTTPException(400, "Provide exactly one of audio or transcript")
                digest = hashlib.sha256()
                size = 0
                if audio is not None:
                    if not isinstance(audio, UploadFile):
                        raise HTTPException(400, "Audio must be an uploaded file")
                    filename = Path(audio.filename or "audio").name
                    suffix = Path(filename).suffix.lower()
                    if suffix not in AUDIO_EXTENSIONS:
                        raise HTTPException(415, "Supported formats: MP3, WAV, M4A, WebM, OGG, CAF and FLAC")
                    source_kind = "audio"
                    async with await anyio.open_file(temporary, "xb") as output:
                        while True:
                            chunk = await audio.read(256 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > settings.max_upload_bytes:
                                raise HTTPException(413, TOO_LARGE)
                            digest.update(chunk)
                            await output.write(chunk)
                else:
                    if not isinstance(transcript, str) or not transcript.strip():
                        raise HTTPException(400, "Transcript must be nonempty text")
                    source_kind, filename, suffix = "text", "transcript.txt", ".txt"
                    content = transcript.encode("utf-8")
                    size = len(content)
                    if size > min(settings.max_upload_bytes, 2 * 1024 * 1024):
                        raise HTTPException(413, TOO_LARGE)
                    digest.update(content)
                    await anyio.Path(temporary).write_bytes(content)
                if size == 0:
                    raise HTTPException(400, "Uploaded source is empty")
                return app.state.store.create(job_id, manifest, source_kind, filename, job_id + suffix, size, digest.hexdigest(), temporary=temporary)
        except StarletteHTTPException as exc:
            if exc.detail == TOO_LARGE:
                raise HTTPException(413, TOO_LARGE) from exc
            raise
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/v1/jobs")
    async def jobs(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
        return app.state.store.list(limit, offset)

    @app.get("/v1/jobs/{job_id}")
    async def get_job(job_id: UUID):
        return app.state.store.get(str(job_id))

    @app.post("/v1/jobs/{job_id}/retry", status_code=202)
    async def retry(job_id: UUID):
        return app.state.store.retry(str(job_id))

    @app.post("/v1/jobs/{job_id}/cancel")
    async def cancel(job_id: UUID):
        return app.state.store.cancel(str(job_id))

    @app.get("/v1/jobs/{job_id}/result")
    async def result(job_id: UUID):
        return app.state.store.result(str(job_id))

    @app.get("/v1/jobs/{job_id}/export/{format_name}")
    async def export(job_id: UUID, format_name: str):
        if format_name not in {"pdf", "json", "csv", "ics"}:
            raise HTTPException(404, "Export format not found")
        app.state.store.result(str(job_id))
        path = app.state.store.exports / str(job_id) / ("meeting." + format_name)
        if not path.exists():
            raise HTTPException(404, "Export archive is missing")
        return FileResponse(path, filename="meeting-{}.{}".format(job_id, format_name))

    @app.get("/v1/jobs/{job_id}/audio")
    async def audio(job_id: UUID):
        job = app.state.store.get(str(job_id))
        if job["stage"] == "recording":
            raise HTTPException(409, "Stop recording before downloading its audio")
        if job["source_kind"] != "audio":
            raise HTTPException(404, "This meeting was submitted as text")
        path = app.state.store.source(str(job_id))
        if not path.exists():
            raise HTTPException(404, "Source audio is missing")
        return FileResponse(path, filename="meeting-{}{}".format(job_id, path.suffix))

    @app.post("/v1/recordings/start", status_code=202)
    async def record(body: RecordingStart):
        return await app.state.recorder.start(body)

    @app.post("/v1/recordings/{job_id}/stop")
    async def stop_recording(job_id: UUID):
        return await app.state.recorder.stop(str(job_id))

    return app


def run():
    import uvicorn
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.bind_port, workers=1)


if __name__ == "__main__":
    run()
