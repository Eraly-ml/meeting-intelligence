import asyncio
import contextlib
import hmac
import importlib.util
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from meetingbox.inference import InferenceError
from meetingbox.models import Report
from .audio import AudioError, AudioProcessor, EngineUnavailable
from .config import EngineSettings, local_file
from .pdf import render_pdf
from .reasoner import VerifiedReasoner


class EngineSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sequence: int = Field(ge=1, strict=True)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str = Field(pattern=r"^(local|remote|unknown|speaker_[0-9]{2,4})$")
    text: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def interval(self):
        if self.end < self.start or not self.text.strip():
            raise ValueError("Invalid transcript segment")
        return self


class MeetingSnapshot(BaseModel):
    # Existing station snapshots carry timestamps/status and later station metadata.
    model_config = ConfigDict(extra="ignore")
    meeting_id: Optional[UUID] = None
    title: str = Field(default="Meeting report", max_length=200)
    segments: List[EngineSegment] = Field(max_length=100000)
    report: Optional[Report] = None
    report_sequence: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def sequences(self):
        ids = [segment.sequence for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate transcript sequences")
        return self


class MeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meeting: MeetingSnapshot


class ReconcileRequest(MeetingRequest):
    through_sequence: int = Field(ge=0, strict=True)
    final: bool

    @model_validator(mode="after")
    def complete_transcript(self):
        supplied = {item.sequence for item in self.meeting.segments if item.sequence <= self.through_sequence}
        if len(supplied) != self.through_sequence:
            raise ValueError("Transcript sequence contains gaps")
        return self


class Boundary:
    """Authenticate before reading bodies; stream audio without buffering it."""
    def __init__(self, app, token, audio_bytes):
        self.app, self.expected, self.audio_bytes = app, ("Bearer " + token).encode(), audio_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        actual = dict(scope["headers"]).get(b"authorization", b"")
        if not hmac.compare_digest(actual, self.expected):
            return await JSONResponse({"detail": "Valid engine Bearer token required"}, status_code=401)(scope, receive, send)
        maximum = self.audio_bytes if scope["path"] == "/v1/transcribe" else 16 * 1024 * 1024
        received = 0

        async def bounded():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                raise HTTPException(413, "Request exceeds the configured engine size limit")
            return message
        await self.app(scope, bounded, send)


def create_app(settings=None, reasoner=None, audio_processor=None):
    settings = settings or EngineSettings.from_env()
    processor = audio_processor or AudioProcessor(settings)

    @asynccontextmanager
    async def lifespan(app):
        # One audio subprocess chain and one Qwen chain at a time; API health stays responsive.
        app.state.audio_lock = asyncio.Lock()
        app.state.reasoner_lock = asyncio.Lock()
        app.state.upload_slots = asyncio.Semaphore(4)
        app.state.reasoner = reasoner or VerifiedReasoner(settings.reasoner_settings())
        try:
            yield
        finally:
            await app.state.reasoner.close()

    app = FastAPI(title="MeetingBox Mac AI Engine", version="0.2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Boundary, token=settings.token, audio_bytes=settings.upload_bytes)

    @app.exception_handler(EngineUnavailable)
    async def unavailable(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(AudioError)
    async def audio_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(InferenceError)
    async def inference_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.get("/health")
    async def health():
        return {"status": "ok", "role": "mac-ai-engine", "model": settings.model,
                "model_transport": "loopback", "inference_readiness": "checked per request",
                "audio_busy": app.state.audio_lock.locked(), "reasoner_busy": app.state.reasoner_lock.locked(),
                "capabilities": dict(processor.capabilities(),
                    pdf={"ready": local_file(settings.pdf_font) and importlib.util.find_spec("reportlab") is not None},
                    verification={"enabled": True, "scope": "final decisions and action items against cited evidence"})}

    @app.post("/v1/reconcile")
    async def reconcile(body: ReconcileRequest):
        async with app.state.reasoner_lock:
            result = await app.state.reasoner.reconcile(body.meeting.model_dump(mode="json"), body.through_sequence, body.final)
            return result.model_dump()

    @app.post("/v1/transcribe")
    async def transcribe(request: Request, language: str = Query(default="auto", pattern=r"^(auto|[a-z]{2,3})$"),
                         meeting_id: Optional[UUID] = None):
        if request.headers.get("content-type", "").startswith("multipart/"):
            raise HTTPException(415, "Send raw audio bytes, not multipart")
        capabilities = processor.capabilities()
        if not capabilities["transcription"]["ready"]:
            raise EngineUnavailable("ASR unavailable: configure " + ", ".join(capabilities["transcription"]["missing"]))
        suffix = Path(request.headers.get("x-audio-filename", "audio.bin")).suffix.lower()
        if suffix not in (".wav", ".mp3", ".m4a", ".webm", ".ogg", ".caf", ".flac", ".bin"):
            raise HTTPException(415, "Unsupported audio filename extension")
        async with app.state.upload_slots:
            with tempfile.TemporaryDirectory(prefix="meetingbox-engine-", dir=settings.temp_directory or None) as folder:
                source = Path(folder) / ("input" + suffix)

                async def save():
                    total = 0
                    with source.open("wb") as output:
                        async for chunk in request.stream():
                            total += len(chunk)
                            if total > settings.upload_bytes:
                                raise HTTPException(413, "Audio upload exceeds the configured size limit")
                            # Bounded ASGI chunks keep local file writes short.
                            await asyncio.to_thread(output.write, chunk)
                        await asyncio.to_thread(output.flush)
                    if total == 0:
                        raise HTTPException(400, "Audio upload is empty")
                try:
                    await asyncio.wait_for(save(), timeout=settings.upload_timeout)
                except asyncio.TimeoutError:
                    raise HTTPException(408, "Audio upload timed out")
                async with app.state.audio_lock:
                    # Shield the worker until it exits even if the caller disconnects;
                    # otherwise cleanup could race the still-running subprocess.
                    task = asyncio.create_task(asyncio.to_thread(processor.transcribe, source, language))
                    try:
                        return await asyncio.shield(task)
                    except asyncio.CancelledError:
                        with contextlib.suppress(Exception):
                            await task
                        raise

    @app.post("/v1/pdf")
    async def pdf(body: MeetingRequest):
        result = await asyncio.to_thread(render_pdf, body.meeting.model_dump(mode="json"), settings.pdf_font)
        return Response(result, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="meeting-report.pdf"'})

    return app
