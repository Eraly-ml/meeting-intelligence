import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone


TERMINAL = {"completed", "failed", "cancelled"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def durable_file(path):
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StoreError(Exception):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


class Store:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.sources = data_dir / "sources"
        self.exports = data_dir / "exports"
        self.incoming = data_dir / "incoming"
        for directory in (data_dir, self.sources, self.exports, self.incoming):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(data_dir / "station.sqlite3"), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, stage TEXT NOT NULL, payload TEXT NOT NULL,
                source_name TEXT NOT NULL, submitted INTEGER NOT NULL DEFAULT 0,
                action TEXT, available_at REAL NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, result TEXT
            );
        """)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        self.db.close()

    def _row(self, db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise StoreError("Job not found", 404)
        return row

    def _write(self, db, row, payload, **fields):
        payload["updated_at"] = now_iso()
        fields.update(stage=payload["stage"], payload=json.dumps(payload, ensure_ascii=False, allow_nan=False))
        names = list(fields)
        db.execute("UPDATE jobs SET " + ",".join(name + "=?" for name in names) + " WHERE id=?", [fields[name] for name in names] + [row["id"]])

    def create(self, job_id, manifest, source_kind, filename, source_name, byte_count, digest, temporary=None, recording=False):
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if existing:
                original = json.loads(existing["payload"])
                if original["manifest"] != manifest or original["source_sha256"] != digest or original["source_kind"] != source_kind:
                    raise StoreError("Idempotency-Key already belongs to different meeting content")
                return original
            if temporary is not None:
                target = self.sources / source_name
                os.replace(temporary, target)
                target.chmod(0o600)
                durable_file(target)
            timestamp = now_iso()
            job = {"id": job_id, "meeting_id": manifest["meeting_id"], "stage": "recording" if recording else "queued",
                   "progress_current": 0, "progress_total": 0, "source_kind": source_kind,
                   "source_path": source_name, "source_sha256": digest, "manifest": manifest,
                   "created_at": timestamp, "updated_at": timestamp, "error_code": None, "error_message": None, "result_path": None,
                   "station": {"archived": not recording, "source_bytes": byte_count, "filename": filename, "worker_submitted": False}}
            db.execute("INSERT INTO jobs(id,stage,payload,source_name) VALUES(?,?,?,?)", (job_id, job["stage"], json.dumps(job, ensure_ascii=False), source_name))
        return job

    def get(self, job_id):
        with self.lock:
            return json.loads(self._row(self.db, job_id)["payload"])

    def list(self, limit=50, offset=0):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM jobs ORDER BY json_extract(payload,'$.created_at') DESC LIMIT ? OFFSET ?", (limit, offset))]

    def work(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE available_at<=? AND (action IS NOT NULL OR stage NOT IN ('recording','completed','failed','cancelled')) ORDER BY action IS NULL,submitted DESC,rowid LIMIT 1", (time.time(),)).fetchone()
            return dict(row) if row else None

    def apply_remote(self, job_id, remote, *, submitted=True, action=None, delay=2):
        allowed = {"queued", "preprocessing", "transcribing", "diarizing", "extracting", "validating", "exporting", "completed", "failed", "cancelled"}
        if remote.get("id") != job_id or remote.get("stage") not in allowed:
            raise StoreError("Mac worker returned an invalid job state", 502)
        with self.transaction() as db:
            row = self._row(db, job_id)
            payload = json.loads(row["payload"])
            if payload["stage"] == "cancelled":
                # A cancellation racing an upload is delivered after the upload ACK.
                action = "cancel" if remote["stage"] not in TERMINAL else None
            else:
                for key in ("stage", "progress_current", "progress_total", "error_code", "error_message"):
                    if key in remote:
                        payload[key] = remote[key]
                if payload["stage"] == "completed":
                    payload["stage"] = "exporting"  # Completion follows durable local result + export files.
            payload["station"]["worker_submitted"] = bool(submitted)
            self._write(db, row, payload, submitted=int(submitted), action=action, attempts=0, available_at=time.time() + delay)

    def defer(self, job_id, message, code="ENGINE_OFFLINE"):
        with self.transaction() as db:
            row = self._row(db, job_id)
            payload = json.loads(row["payload"])
            payload["error_code"], payload["error_message"] = code, message
            attempts = row["attempts"] + 1
            self._write(db, row, payload, attempts=attempts, available_at=time.time() + min(60, 2 ** min(attempts, 6)))

    def complete(self, job_id, result):
        with self.transaction() as db:
            row = self._row(db, job_id)
            payload = json.loads(row["payload"])
            if payload["stage"] == "cancelled":
                return
            payload.update(stage="completed", error_code=None, error_message=None, result_path=job_id + "/result.json")
            result["job"] = payload
            result["exports"] = {format_name: "/v1/jobs/{}/export/{}".format(job_id, format_name) for format_name in ("json", "csv", "pdf")}
            self._write(db, row, payload, result=json.dumps(result, ensure_ascii=False, allow_nan=False), action=None)

    def result(self, job_id):
        with self.lock:
            row = self._row(self.db, job_id)
            if row["stage"] != "completed" or not row["result"]:
                raise StoreError("Result is not ready; stage=" + row["stage"])
            result = json.loads(row["result"])
            result["job"] = json.loads(row["payload"])
            return result

    def source(self, job_id):
        with self.lock:
            row = self._row(self.db, job_id)
            return self.sources / row["source_name"]

    def retry(self, job_id):
        with self.transaction() as db:
            row = self._row(db, job_id)
            if row["stage"] not in {"failed", "cancelled"}:
                raise StoreError("Only failed or cancelled jobs can be retried")
            source = self.sources / row["source_name"]
            if not source.exists() or source.stat().st_size == 0:
                raise StoreError("No source audio or text is available; make a new recording or upload a file")
            payload = json.loads(row["payload"])
            payload.update(stage="queued", error_code=None, error_message=None)
            self._write(db, row, payload, action="retry" if row["submitted"] else None, attempts=0, available_at=0)
        return self.get(job_id)

    def remote_missing(self, job_id):
        """A reset Mac can rebuild its job from the board's original durable source."""
        with self.transaction() as db:
            row = self._row(db, job_id)
            payload = json.loads(row["payload"])
            payload["station"]["worker_submitted"] = False
            if payload["stage"] != "cancelled":
                payload.update(stage="queued", error_code="ENGINE_JOB_MISSING",
                               error_message="The Mac no longer has this job; the station will resend its archived source")
            self._write(db, row, payload, submitted=0, action=None, available_at=time.time() + 2)

    def cancel(self, job_id):
        with self.transaction() as db:
            row = self._row(db, job_id)
            if row["stage"] in TERMINAL:
                return json.loads(row["payload"])
            if row["stage"] == "recording":
                raise StoreError("Stop the active recording before cancelling its processing")
            payload = json.loads(row["payload"])
            payload.update(stage="cancelled", error_code=None, error_message=None)
            self._write(db, row, payload, action="cancel" if row["submitted"] else None, available_at=0)
        return self.get(job_id)

    def finish_recording(self, job_id, error=None):
        path = self.source(job_id)
        size = path.stat().st_size if path.exists() else 0
        digest = file_sha256(path) if size else ""
        if size:
            durable_file(path)
        with self.transaction() as db:
            row = self._row(db, job_id)
            payload = json.loads(row["payload"])
            payload.update(stage="failed" if error else "queued", source_sha256=digest,
                           error_code="RECORDING_INTERRUPTED" if error else None, error_message=error)
            payload["station"].update(archived=bool(size), source_bytes=size)
            self._write(db, row, payload, available_at=0)
        return self.get(job_id)

    def recover(self):
        with self.lock:
            interrupted = [row[0] for row in self.db.execute("SELECT id FROM jobs WHERE stage='recording'")]
        for job_id in interrupted:
            if self.get(job_id)["station"].get("filename") == "meeting-browser.wav":
                self.browser_import_pending(job_id)
            else:
                self.finish_recording(job_id, "Station restarted during recording; available audio is archived. Retry to transcribe it")

    def browser_import_pending(self, job_id):
        with self.transaction() as db:
            row = self._row(db, job_id)
            payload = json.loads(row["payload"])
            payload.update(stage="recording", error_code="BROWSER_IMPORT_PENDING",
                           error_message="Browser audio has not been imported. The browser retains its recording; retry Stop and archive")
            self._write(db, row, payload)
