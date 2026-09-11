"""Private LAN transport. The station never downloads models or calls cloud APIs."""
import asyncio
from pathlib import Path

import anyio
import httpx
from pydantic import ValidationError

from .inference import InferenceError
from .models import Report, Segment


class EngineClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.engine_url or "http://127.0.0.1:8765",
            headers={"Authorization": "Bearer " + settings.engine_token},
            timeout=httpx.Timeout(settings.engine_timeout, connect=5.0),
            trust_env=False, follow_redirects=False, transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def _request(self, path, *, max_bytes=16 * 1024 * 1024, **kwargs):
        if not self.settings.engine_url:
            raise InferenceError("Mac AI engine is not configured; audio remains archived. Set MEETINGBOX_ENGINE_URL and retry")
        try:
            async with self.client.stream("POST", path, **kwargs) as response:
                if response.is_redirect:
                    raise InferenceError("Mac AI engine returned a redirect; refusing to follow it")
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise InferenceError("Mac AI engine response exceeds the configured result limit; audio remains archived")
                return httpx.Response(response.status_code, headers=response.headers, content=bytes(content))
        except httpx.TimeoutException as exc:
            raise InferenceError("Mac AI engine timed out; recording and transcript are preserved. Retry when the Mac is ready") from exc
        except httpx.HTTPStatusError as exc:
            raise InferenceError("Mac AI engine returned HTTP {}; check its local model configuration and retry".format(exc.response.status_code)) from exc
        except httpx.RequestError as exc:
            raise InferenceError("Mac AI engine is offline; recording and transcript are preserved. Reconnect the Mac and retry") from exc

    async def reconcile(self, meeting, through_sequence, final):
        # Archive filesystem locations and unrelated metadata stay on the station.
        payload = {key: value for key, value in meeting.items() if key != "audio"}
        response = await self._request("/v1/reconcile", json={"meeting": payload, "through_sequence": through_sequence, "final": final})
        try:
            report = Report.model_validate_json(response.content)
            return report.check_evidence({segment["sequence"] for segment in meeting["segments"] if segment["sequence"] <= through_sequence})
        except (ValueError, TypeError, ValidationError) as exc:
            raise InferenceError("Mac AI engine report failed schema or transcript evidence validation") from exc

    async def transcribe(self, path: Path, meeting_id):
        async def chunks():
            async with await anyio.open_file(path, "rb") as source:
                while True:
                    chunk = await source.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk

        response = await self._request(
            "/v1/transcribe", params={"meeting_id": meeting_id, "language": "auto"},
            content=chunks(), headers={"Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size), "X-Audio-Filename": path.name},
        )
        try:
            result = response.json()
            segments = [Segment.model_validate(item) for item in result["segments"]]
            if [item.sequence for item in segments] != list(range(1, len(segments) + 1)):
                raise ValueError("Non-contiguous transcript sequences")
            metadata = {key: result[key] for key in ("duration", "diarization") if key in result}
            return segments, metadata
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise InferenceError("Mac AI engine transcript failed validation; recording remains archived") from exc

    async def pdf(self, meeting):
        response = await self._request("/v1/pdf", json={"meeting": {key: value for key, value in meeting.items() if key != "audio"}})
        if not response.content.startswith(b"%PDF-"):
            raise InferenceError("Mac AI engine did not return a valid PDF")
        return response.content


EngineReasoner = EngineClient


class AudioWorker:
    """One durable audio job at a time; transcription commits together with finalization."""
    def __init__(self, store, engine, settings, publish):
        self.store, self.engine, self.settings, self.publish = store, engine, settings, publish

    async def run_once(self):
        job = self.store.claim_audio()
        if job is None:
            return False
        self.publish(job["meeting_id"])
        try:
            path = self.settings.archive / job["archive_name"]
            segments, metadata = await self.engine.transcribe(path, job["meeting_id"])
            self.store.complete_audio(job, segments, metadata)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, InferenceError) else "Audio processing failed ({}); recording remains archived".format(type(exc).__name__)
            self.store.fail_audio(job, message, self.settings.max_attempts)
        self.publish(job["meeting_id"])
        return True

    async def run(self):
        while True:
            if not await self.run_once():
                await asyncio.sleep(self.settings.poll_seconds)
