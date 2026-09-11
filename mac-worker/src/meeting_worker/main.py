from __future__ import annotations
import asyncio
import contextlib
import fcntl
import hashlib
import hmac
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from . import __version__
from .config import Settings
from .pipeline import Pipeline
from .schemas import JobManifest, JobRecord, JobResult, JobStage
from .store import JobStore
from .asr import capabilities as asr_capabilities


class Boundary:
    def __init__(self, app, token, maximum, timeout):
        self.app, self.expected = app, ('Bearer ' + token).encode()
        self.maximum, self.timeout = maximum, timeout

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] == '/health' or scope['method'] == 'OPTIONS':
            return await self.app(scope, receive, send)
        actual = dict(scope['headers']).get(b'authorization', b'')
        if not hmac.compare_digest(actual, self.expected):
            return await JSONResponse({'detail': 'Valid worker Bearer token required'}, status_code=401)(scope, receive, send)
        try:
            if int(dict(scope['headers']).get(b'content-length', b'0')) > self.maximum + 65536:
                return await JSONResponse({'detail': 'Upload exceeds configured size limit'}, status_code=413)(scope, receive, send)
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length'}, status_code=400)(scope, receive, send)
        total, deadline = 0, asyncio.get_running_loop().time() + self.timeout
        async def bounded():
            nonlocal total
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise MultiPartException('Upload timed out')
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                raise MultiPartException('Upload timed out')
            total += len(message.get('body', b''))
            if total > self.maximum + 65536:
                # Multipart parser closes every spooled file on this exception.
                raise MultiPartException('Upload exceeds configured size limit')
            return message
        await self.app(scope, bounded, send)


def create_app(config: Settings | None = None, pipeline_factory=Pipeline, start_worker=True):
    config = config or Settings()
    config.prepare()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0')

    @asynccontextmanager
    async def lifespan(app):
        lock = (config.data_dir / 'worker.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('Run exactly one Mac worker process per data directory')
        store = JobStore(config.data_dir / 'worker.sqlite3')
        store.recover_interrupted()
        pipeline = pipeline_factory(config, store)
        app.state.store, app.state.pipeline = store, pipeline
        app.state.submission_lock = asyncio.Lock()
        stopping = asyncio.Event()
        async def work():
            while not stopping.is_set():
                job = await asyncio.to_thread(store.claim)
                if job:
                    await asyncio.to_thread(pipeline.run, job.id)
                else:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(stopping.wait(), 0.25)
        task = asyncio.create_task(work()) if start_worker else None
        try:
            yield
        finally:
            stopping.set()
            if task:
                await task
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    app = FastAPI(title='Meeting Intelligence Worker', version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Boundary, token=config.api_token, maximum=config.max_upload_bytes, timeout=config.upload_timeout)
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins, allow_credentials=False,
                      allow_methods=['GET', 'POST'], allow_headers=['Authorization', 'Content-Type', 'Idempotency-Key'])

    @app.exception_handler(StarletteHTTPException)
    async def boundary_error(request, exc):
        status = 413 if exc.detail == 'Upload exceeds configured size limit' else 408 if exc.detail == 'Upload timed out' else exc.status_code
        return JSONResponse({'detail': exc.detail}, status_code=status, headers=exc.headers)

    @app.get('/health')
    def health():
        return {'status': 'ok', 'version': __version__}

    @app.get('/v1/capabilities')
    def capabilities():
        ready = asr_capabilities(config)
        return {'asr_profiles': [name for name in ready if name != 'diarization'],
                'language_modes': ['kk_ru', 'en', 'auto'], 'ollama_model': config.ollama_model,
                'diarization': config.enable_diarization and ready['diarization']['ready'],
                'offline': True, 'readiness': ready, 'selected_profiles': {'kk_ru': config.asr_kk_ru, 'en': config.asr_en},
                'semantic_verification': config.semantic_verification,
                'verification_scope': 'cited topics, decisions, questions, actions and risks; summary prose is not independently reviewed',
                'ollama_readiness': 'local weights checked at inference time'}

    @app.post('/v1/jobs', status_code=202)
    async def create_job(manifest_json: str = Form(...), audio: UploadFile | None = File(default=None),
                         transcript: str | None = Form(default=None),
                         idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')) -> JobRecord:
        if (audio is None) == (transcript is None):
            raise HTTPException(400, 'Provide exactly one of audio or transcript')
        if len(manifest_json) > 16384:
            raise HTTPException(413, 'Manifest exceeds size limit')
        try:
            manifest = JobManifest.model_validate_json(manifest_json)
            job_id = str(uuid.UUID(idempotency_key)) if idempotency_key else str(uuid.uuid4())
        except ValueError:
            raise HTTPException(422, 'Invalid manifest or Idempotency-Key; job keys must be UUIDs')
        suffix = Path(audio.filename or 'audio.bin').suffix.lower() if audio else '.txt'
        if audio and suffix not in {'.mp3', '.wav', '.m4a', '.webm', '.ogg', '.caf', '.flac'}:
            raise HTTPException(415, 'Supported audio: MP3, WAV, M4A, WebM, OGG, CAF, FLAC')
        kind, path = 'audio' if audio else 'text', None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix='upload-', dir=config.data_dir / 'sources')
            path = Path(temporary)
            digest, size = hashlib.sha256(), 0
            with os.fdopen(descriptor, 'wb') as output:
                while True:
                    chunk = await audio.read(1024 * 1024) if audio else (transcript or '').encode('utf-8')
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > (config.max_upload_bytes if audio else min(config.max_upload_bytes, 4 * 1024 * 1024)):
                        raise HTTPException(413, 'Source exceeds configured size limit')
                    digest.update(chunk)
                    await asyncio.to_thread(output.write, chunk)
                    if not audio:
                        break
                await asyncio.to_thread(output.flush)
                await asyncio.to_thread(os.fsync, output.fileno())
            if size == 0 or (transcript is not None and not transcript.strip()):
                raise HTTPException(400, 'Source is empty')
            async with app.state.submission_lock:
                existing = app.state.store.get(job_id)
                if existing:
                    if existing.source_sha256 != digest.hexdigest() or existing.manifest != manifest or existing.source_kind != kind:
                        raise HTTPException(409, 'Idempotency-Key already belongs to different content')
                    return existing
                source = config.data_dir / 'sources' / (job_id + suffix)
                path.replace(source)
                path = source
                now = datetime.now(UTC)
                job = JobRecord(id=job_id, meeting_id=manifest.meeting_id, stage=JobStage.QUEUED,
                    source_kind=kind, source_path=str(source), source_sha256=digest.hexdigest(),
                    manifest=manifest, created_at=now, updated_at=now)
                commit = asyncio.create_task(asyncio.to_thread(app.state.store.put, job))
                try:
                    await asyncio.shield(commit)
                except asyncio.CancelledError:
                    # A disconnect/shutdown cannot remove a source after SQLite accepts it.
                    await commit
                    path = None
                    raise
                path = None
                return job
        finally:
            if path:
                path.unlink(missing_ok=True)
            if audio:
                await audio.close()

    def job_or_404(job_id):
        job = app.state.store.get(str(job_id))
        if job is None:
            raise HTTPException(404, 'Job not found')
        return job

    @app.get('/v1/jobs/{job_id}')
    def get_job(job_id: uuid.UUID) -> JobRecord:
        return job_or_404(job_id)

    @app.get('/v1/jobs')
    def list_jobs(limit: int = 50) -> list[JobRecord]:
        return app.state.store.list(max(1, min(limit, 200)))

    @app.post('/v1/jobs/{job_id}/retry', status_code=202)
    def retry_job(job_id: uuid.UUID) -> JobRecord:
        job = job_or_404(job_id)
        if getattr(app.state.pipeline, 'active_job', None) == job.id:
            raise HTTPException(409, 'Cancelled inference is still finishing; retry after it exits')
        try:
            return app.state.store.update(job.id, JobStage.QUEUED, allowed={JobStage.FAILED, JobStage.CANCELLED},
                                          error_code=None, error_message=None, result_path=None)
        except ValueError:
            raise HTTPException(409, 'Only failed or cancelled jobs can be retried')

    @app.post('/v1/jobs/{job_id}/cancel')
    def cancel_job(job_id: uuid.UUID) -> JobRecord:
        job = job_or_404(job_id)
        if job.stage in {JobStage.COMPLETED, JobStage.FAILED, JobStage.CANCELLED}:
            return job
        try:
            return app.state.store.update(job.id, JobStage.CANCELLED,
                allowed=set(JobStage) - {JobStage.COMPLETED, JobStage.FAILED, JobStage.CANCELLED})
        except ValueError:
            return app.state.store.get(job.id)

    @app.get('/v1/jobs/{job_id}/result')
    def get_result(job_id: uuid.UUID) -> JobResult:
        job = job_or_404(job_id)
        if job.stage != JobStage.COMPLETED or not job.result_path:
            raise HTTPException(409, 'Result is not ready')
        try:
            return JobResult.model_validate_json(Path(job.result_path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise HTTPException(503, 'Saved result is unavailable; source remains archived')

    @app.get('/v1/jobs/{job_id}/export/{format_name}')
    def get_export(job_id: uuid.UUID, format_name: str) -> FileResponse:
        result = get_result(job_id)
        path = result.exports.get(format_name)
        if not path or not Path(path).is_file():
            raise HTTPException(404, 'Export format not found')
        return FileResponse(path, filename=Path(path).name)
    return app


def run():
    import uvicorn
    config = Settings()
    uvicorn.run(create_app(config), host=config.bind_host, port=config.bind_port, workers=1)
