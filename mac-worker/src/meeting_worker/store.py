import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from .schemas import JobRecord, JobStage


class JobStore:
    def __init__(self, path: Path):
        self.path, self._lock = path, threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, stage TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA synchronous=FULL')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, job: JobRecord):
        with self._lock, self._connect() as conn:
            conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', (job.id, job.meeting_id, job.stage.value,
                job.model_dump_json(), job.created_at.isoformat(), job.updated_at.isoformat()))

    def get(self, job_id):
        with self._connect() as conn:
            row = conn.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
        return JobRecord.model_validate_json(row['payload']) if row else None

    def list(self, limit=100):
        with self._connect() as conn:
            rows = conn.execute('SELECT payload FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
        return [JobRecord.model_validate_json(row['payload']) for row in rows]

    def update(self, job_id, stage, allowed=None, **fields):
        with self._lock, self._connect() as conn:
            row = conn.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = JobRecord.model_validate_json(row['payload'])
            if allowed is not None and job.stage not in allowed:
                raise ValueError('Job is not in a state permitting this transition')
            if job.stage == JobStage.CANCELLED and stage != JobStage.QUEUED:
                return job
            data = job.model_dump()
            data.update(fields, stage=stage, updated_at=datetime.now(UTC))
            updated = JobRecord.model_validate(data)
            conn.execute('UPDATE jobs SET stage=?,payload=?,updated_at=? WHERE id=?',
                (stage.value, updated.model_dump_json(), updated.updated_at.isoformat(), job_id))
            return updated

    def claim(self):
        with self._lock:
            with self._connect() as conn:
                row = conn.execute('SELECT id FROM jobs WHERE stage=? ORDER BY created_at LIMIT 1', (JobStage.QUEUED.value,)).fetchone()
            return self.update(row['id'], JobStage.PREPROCESSING, allowed={JobStage.QUEUED}) if row else None

    def recover_interrupted(self):
        terminal = {JobStage.QUEUED, JobStage.COMPLETED, JobStage.FAILED, JobStage.CANCELLED}
        with self._connect() as conn:
            rows = conn.execute('SELECT payload FROM jobs').fetchall()
        for row in rows:
            job = JobRecord.model_validate_json(row['payload'])
            if job.stage not in terminal:
                self.update(job.id, JobStage.FAILED, error_code='WORKER_RESTARTED',
                    error_message='Worker restarted during processing; the saved source can be retried.')
