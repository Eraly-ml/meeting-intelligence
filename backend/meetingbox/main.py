import asyncio
import contextlib
import fcntl
import hmac
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

import anyio
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import Settings
from .inference import OllamaReasoner
from .models import MeetingEnd, MeetingStart, SegmentBatch
from .store import Store, StoreError
from .worker import Worker


class BoundaryMiddleware:
    """Reject unauthenticated traffic before parsing bodies; cap actual request bytes."""

    def __init__(self, app, token, max_bytes=2 * 1024 * 1024):
        self.app = app
        self.expected = ("Bearer " + token).encode("utf-8")
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        authorization = dict(scope["headers"]).get(b"authorization", b"")
        if not hmac.compare_digest(authorization, self.expected):
            return await JSONResponse({"detail": "Valid Bearer token required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        chunks = []
        length = 0
        while True:
            chunk = await receive()
            if chunk["type"] == "http.disconnect":
                return
            length += len(chunk.get("body", b""))
            if length > self.max_bytes:
                return await JSONResponse({"detail": "Request body exceeds 2 MiB"}, status_code=413)(scope, receive, send)
            chunks.append(chunk.get("body", b""))
            if not chunk.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class Subscribers:
    def __init__(self):
        self.meetings = {}

    def subscribe(self, meeting_id):
        queue = asyncio.Queue(maxsize=1)
        self.meetings.setdefault(meeting_id, set()).add(queue)
        return queue

    def unsubscribe(self, meeting_id, queue):
        listeners = self.meetings.get(meeting_id, set())
        listeners.discard(queue)
        if not listeners:
            self.meetings.pop(meeting_id, None)

    def publish(self, meeting_id):
        for queue in self.meetings.get(meeting_id, ()):
            # A notification means fetch the latest durable snapshot; coalescing is lossless.
            if not queue.full():
                queue.put_nowait(True)


def markdown(meeting):
    def clean(value):
        return str(value).replace("\r", " ").replace("\n", " ")

    lines = ["# " + clean(meeting["title"]), "", "Status: " + meeting["status"], ""]
    report = meeting["report"]
    if report:
        lines += ["## Summary", "", report["summary"], "", "## Decisions", ""]
        for item in report["decisions"]:
            lines.append("- {} (segments {})".format(clean(item["text"]), ", ".join(map(str, item["evidence"]))))
        lines += ["", "## Action items", ""]
        for item in report["action_items"]:
            lines.append("- {} — Owner: {}; Due: {} (segments {})".format(clean(item["task"]), clean(item["owner"] or "Unassigned"), clean(item["due"] or "Unspecified"), ", ".join(map(str, item["evidence"]))))
        for label, key in [("Open questions", "open_questions"), ("Topics", "topics"), ("Risks", "risks")]:
            lines += ["", "## " + label, ""] + ["- " + clean(item) for item in report[key]]
    if meeting["error"]:
        lines += ["Processing error: " + meeting["error"], ""]
    lines += ["", "## Transcript", ""]
    for item in meeting["segments"]:
        lines.append("[{}] {:.2f}–{:.2f}s {}: {}".format(item["sequence"], item["start"], item["end"], item["speaker"], clean(item["text"])))
    return "\n".join(lines) + "\n"


def create_app(settings=None, reasoner=None, start_worker=True):
    settings = settings or Settings.from_env()
    subscribers = Subscribers()

    @asynccontextmanager
    async def lifespan(app):
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        lock_path = settings.database.with_suffix(settings.database.suffix + ".lock")
        lock_file = lock_path.open("a")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            raise RuntimeError("Another MeetingBox hub owns this database. Run exactly one Uvicorn worker")
        store = Store(settings.database, settings.incremental_seconds)
        model = reasoner or OllamaReasoner(settings)
        app.state.store = store
        app.state.worker = Worker(store, model, settings, subscribers.publish)
        store.recover()
        task = asyncio.create_task(app.state.worker.run()) if start_worker else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await model.close()
            store.close()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    app = FastAPI(title="MeetingBox Hub", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BoundaryMiddleware, token=settings.token)

    @app.exception_handler(StoreError)
    async def store_error_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": settings.model, "model_transport": "loopback", "version": "0.1.0"}

    @app.post("/meetings/start")
    async def start(body: MeetingStart):
        return app.state.store.start(str(body.meeting_id), body.title)

    @app.post("/meetings/{meeting_id}/segments")
    async def segments(meeting_id: UUID, body: SegmentBatch):
        meeting_id = str(meeting_id)
        ack = app.state.store.append(meeting_id, body.segments)
        subscribers.publish(meeting_id)
        return {"ack_sequence": ack}

    @app.post("/meetings/{meeting_id}/end")
    async def end(meeting_id: UUID, body: MeetingEnd):
        meeting_id = str(meeting_id)
        result = app.state.store.end(meeting_id, body.last_sequence)
        subscribers.publish(meeting_id)
        return result

    @app.post("/meetings/{meeting_id}/retry")
    async def retry(meeting_id: UUID):
        meeting_id = str(meeting_id)
        result = app.state.store.retry(meeting_id)
        subscribers.publish(meeting_id)
        return result

    @app.get("/meetings")
    async def history(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
        return {"meetings": app.state.store.history(limit, offset)}

    @app.get("/search")
    async def search(q: str = Query(min_length=1, max_length=200), limit: int = Query(default=50, ge=1, le=200)):
        return {"results": app.state.store.search(q, limit)}

    @app.get("/meetings/{meeting_id}")
    async def get(meeting_id: UUID):
        return app.state.store.snapshot(str(meeting_id))

    @app.get("/meetings/{meeting_id}/export")
    async def export(meeting_id: UUID, format: Literal["markdown", "json"] = "markdown"):
        result = app.state.store.snapshot(str(meeting_id))
        suffix = "md" if format == "markdown" else "json"
        headers = {"Content-Disposition": 'attachment; filename="meeting-{}.{}"'.format(meeting_id, suffix)}
        if format == "json":
            return JSONResponse(result, headers=headers)
        return PlainTextResponse(markdown(result), media_type="text/markdown", headers=headers)

    @app.websocket("/ws/{meeting_id}")
    async def websocket(websocket: WebSocket, meeting_id: UUID):
        actual = websocket.headers.get("authorization", "").encode("utf-8")
        if not hmac.compare_digest(actual, ("Bearer " + settings.token).encode("utf-8")):
            await websocket.close(code=1008)
            return
        meeting_id = str(meeting_id)
        try:
            app.state.store.snapshot(meeting_id)
        except StoreError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = subscribers.subscribe(meeting_id)

        async def send_snapshots():
            while True:
                await asyncio.wait_for(websocket.send_json({"type": "snapshot", "meeting": app.state.store.snapshot(meeting_id)}), timeout=10)
                await queue.get()

        async def receive_disconnect():
            while True:
                event = await websocket.receive()
                if event["type"] == "websocket.disconnect":
                    return

        sender = asyncio.create_task(send_snapshots())
        receiver = asyncio.create_task(receive_disconnect())
        try:
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            # ASGI servers cancel a disconnected connection's scope during shutdown.
            pass
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1013)
        finally:
            subscribers.unsubscribe(meeting_id, queue)
            sender.cancel()
            receiver.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(sender, receiver, return_exceptions=True)

    return app
