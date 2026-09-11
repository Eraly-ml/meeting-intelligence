import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .schemas import JobRecord, JobStage


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def put(self, job: JobRecord) -> None:
        payload = job.model_dump_json()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO jobs(id, meeting_id, stage, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET stage=excluded.stage,
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (
                    job.id,
                    job.meeting_id,
                    job.stage.value,
                    payload,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return JobRecord.model_validate_json(row["payload"]) if row else None

    def list(self, limit: int = 100) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [JobRecord.model_validate_json(row["payload"]) for row in rows]

    def update(self, job_id: str, stage: JobStage, **fields: object) -> JobRecord:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        data = job.model_dump()
        data.update(fields)
        data["stage"] = stage
        data["updated_at"] = datetime.now(UTC)
        updated = JobRecord.model_validate(data)
        self.put(updated)
        return updated

    def recover_interrupted(self) -> None:
        terminal = {JobStage.COMPLETED.value, JobStage.FAILED.value, JobStage.CANCELLED.value}
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM jobs").fetchall()
        for row in rows:
            job = JobRecord.model_validate_json(row["payload"])
            if job.stage.value not in terminal:
                self.update(
                    job.id,
                    JobStage.FAILED,
                    error_code="WORKER_RESTARTED",
                    error_message="Worker restarted while this job was running; retry is safe.",
                )
