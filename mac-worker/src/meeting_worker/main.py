from __future__ import annotations

import hashlib
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import settings
from .pipeline import Pipeline
from .schemas import JobManifest, JobRecord, JobResult, JobStage
from .store import JobStore


settings.prepare()
store = JobStore(settings.data_dir / "worker.sqlite3")
pipeline = Pipeline(settings, store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.recover_interrupted()
    yield


app = FastAPI(title="Meeting Intelligence Worker", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


def require_token(authorization: str | None = Header(default=None)) -> None:
    if settings.api_token and authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=401, detail="Invalid worker token")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/v1/capabilities", dependencies=[Depends(require_token)])
def capabilities() -> dict:
    return {
        "asr_profiles": ["shyngys", "gigaam", "mlx-distil-whisper"],
        "language_modes": ["kk_ru", "en", "auto"],
        "ollama_model": settings.ollama_model,
        "diarization": settings.enable_diarization,
        "offline": settings.hf_offline,
    }


@app.post("/v1/jobs", status_code=202, dependencies=[Depends(require_token)])
async def create_job(
    background: BackgroundTasks,
    manifest_json: str = Form(...),
    audio: UploadFile | None = File(default=None),
    transcript: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobRecord:
    if (audio is None) == (transcript is None):
        raise HTTPException(400, "Provide exactly one of audio or transcript")
    try:
        manifest = JobManifest.model_validate_json(manifest_json)
    except ValueError as exc:
        raise HTTPException(422, f"Invalid manifest: {exc}") from exc
    job_id = idempotency_key or str(uuid.uuid4())
    existing = store.get(job_id)
    if existing:
        return existing
    if audio is not None:
        suffix = Path(audio.filename or "audio.bin").suffix.lower()
        if suffix not in {".mp3", ".wav", ".m4a"}:
            raise HTTPException(415, "Supported audio formats: MP3, WAV, M4A")
        source = settings.data_dir / "sources" / f"{job_id}{suffix}"
        with source.open("wb") as stream:
            shutil.copyfileobj(audio.file, stream)
        source_kind = "audio"
    else:
        source = settings.data_dir / "sources" / f"{job_id}.txt"
        source.write_text(transcript or "", encoding="utf-8")
        source_kind = "text"
    if source.stat().st_size == 0:
        source.unlink(missing_ok=True)
        raise HTTPException(400, "Source is empty")
    now = datetime.now(UTC)
    job = JobRecord(
        id=job_id, meeting_id=manifest.meeting_id, stage=JobStage.QUEUED,
        source_kind=source_kind, source_path=str(source), source_sha256=_sha256(source),
        manifest=manifest, created_at=now, updated_at=now,
    )
    store.put(job)
    background.add_task(pipeline.run, job.id)
    return job


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> JobRecord:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/v1/jobs", dependencies=[Depends(require_token)])
def list_jobs(limit: int = 50) -> list[JobRecord]:
    return store.list(max(1, min(limit, 200)))


@app.post("/v1/jobs/{job_id}/retry", status_code=202, dependencies=[Depends(require_token)])
def retry_job(job_id: str, background: BackgroundTasks) -> JobRecord:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.stage not in {JobStage.FAILED, JobStage.CANCELLED}:
        raise HTTPException(409, f"Only failed or cancelled jobs can be retried; stage={job.stage}")
    queued = store.update(
        job_id, JobStage.QUEUED, error_code=None, error_message=None, result_path=None
    )
    background.add_task(pipeline.run, job_id)
    return queued


@app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
def cancel_job(job_id: str) -> JobRecord:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.stage in {JobStage.COMPLETED, JobStage.FAILED, JobStage.CANCELLED}:
        return job
    # Cooperative cancellation: queued work will be skipped; active ML calls finish safely.
    return store.update(job_id, JobStage.CANCELLED)


@app.get("/v1/jobs/{job_id}/result", dependencies=[Depends(require_token)])
def get_result(job_id: str) -> JobResult:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.stage != JobStage.COMPLETED or not job.result_path:
        raise HTTPException(409, f"Result is not ready; stage={job.stage}")
    return JobResult.model_validate_json(Path(job.result_path).read_text(encoding="utf-8"))


@app.get("/v1/jobs/{job_id}/export/{format_name}", dependencies=[Depends(require_token)])
def get_export(job_id: str, format_name: str) -> FileResponse:
    result = get_result(job_id)
    path = result.exports.get(format_name)
    if not path:
        raise HTTPException(404, "Export format not found")
    return FileResponse(path, filename=Path(path).name)


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)
