import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class StoreError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Store:
    """Transactions serialize mutations; acknowledgements only follow durable commits."""

    def __init__(self, path: Path, incremental_seconds=45.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.incremental_seconds = incremental_seconds
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'recording',
                last_sequence INTEGER NOT NULL DEFAULT 0,
                final_sequence INTEGER,
                report TEXT, report_sequence INTEGER NOT NULL DEFAULT 0,
                error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                next_incremental REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS segments (
                meeting_id TEXT NOT NULL REFERENCES meetings(id),
                sequence INTEGER NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                speaker TEXT NOT NULL, text TEXT NOT NULL,
                PRIMARY KEY (meeting_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id TEXT NOT NULL REFERENCES meetings(id),
                kind TEXT NOT NULL, through_sequence INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL, error TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status,available_at,id);
            CREATE UNIQUE INDEX IF NOT EXISTS one_queued_kind
                ON jobs(meeting_id,kind) WHERE status='queued';
            CREATE TABLE IF NOT EXISTS audio_jobs (
                meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
                archive_name TEXT NOT NULL, filename TEXT NOT NULL,
                bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL, error TEXT, metadata TEXT
            );
        """)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def close(self):
        self.connection.close()

    def _meeting(self, db, meeting_id):
        row = db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if row is None:
            raise StoreError("Meeting not found", 404)
        return row

    def start(self, meeting_id, title):
        with self.transaction() as db:
            row = db.execute("SELECT title FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if row and row["title"] != title:
                raise StoreError("Meeting ID already has a different title")
            now = time.time()
            if row is None:
                db.execute("INSERT INTO meetings(id,title,created_at,updated_at,next_incremental) VALUES(?,?,?,?,?)",
                           (meeting_id, title, now, now, now + self.incremental_seconds))
        return self.snapshot(meeting_id)

    def append(self, meeting_id, segments):
        with self.transaction() as db:
            meeting = self._meeting(db, meeting_id)
            for segment in segments:
                item = segment.model_dump()
                existing = db.execute("SELECT sequence,start,end,speaker,text FROM segments WHERE meeting_id=? AND sequence=?",
                                      (meeting_id, item["sequence"])).fetchone()
                if existing:
                    if dict(existing) != item:
                        raise StoreError("Conflicting content for sequence {}".format(item["sequence"]))
                    continue
                if meeting["status"] != "recording":
                    raise StoreError("Meeting has ended; only identical replays are accepted")
                if item["sequence"] > meeting["last_sequence"] + 10000:
                    raise StoreError("Sequence is too far ahead of the acknowledged transcript")
                db.execute("INSERT INTO segments VALUES(?,?,?,?,?,?)", (meeting_id, item["sequence"], item["start"], item["end"], item["speaker"], item["text"]))
            ack = meeting["last_sequence"]
            rows = db.execute("SELECT sequence FROM segments WHERE meeting_id=? AND sequence>? ORDER BY sequence", (meeting_id, ack))
            for row in rows:
                if row["sequence"] != ack + 1:
                    break
                ack += 1
            db.execute("UPDATE meetings SET last_sequence=?,updated_at=? WHERE id=?", (ack, time.time(), meeting_id))
        return ack

    def _enqueue(self, db, meeting_id, kind, sequence, now):
        row = db.execute("SELECT id FROM jobs WHERE meeting_id=? AND kind=? AND status='queued'", (meeting_id, kind)).fetchone()
        if row:
            db.execute("UPDATE jobs SET through_sequence=MAX(through_sequence,?) WHERE id=?", (sequence, row["id"]))
        else:
            db.execute("INSERT INTO jobs(meeting_id,kind,through_sequence,available_at,created_at) VALUES(?,?,?,?,?)", (meeting_id, kind, sequence, now, now))

    def end(self, meeting_id, final_sequence):
        with self.transaction() as db:
            meeting = self._meeting(db, meeting_id)
            if meeting["final_sequence"] is not None:
                if meeting["final_sequence"] != final_sequence:
                    raise StoreError("End sequence conflicts with the previously finalized transcript")
            else:
                maximum = db.execute("SELECT COALESCE(MAX(sequence),0) FROM segments WHERE meeting_id=?", (meeting_id,)).fetchone()[0]
                if meeting["last_sequence"] != final_sequence or maximum != final_sequence:
                    raise StoreError("Transcript is incomplete or last_sequence does not match; replay missing segments before ending")
                now = time.time()
                db.execute("UPDATE meetings SET status='processing',final_sequence=?,updated_at=?,error=NULL WHERE id=?", (final_sequence, now, meeting_id))
                db.execute("UPDATE jobs SET status='superseded' WHERE meeting_id=? AND kind='incremental' AND status='queued'", (meeting_id,))
                self._enqueue(db, meeting_id, "final", final_sequence, now)
        return self.snapshot(meeting_id)

    def retry(self, meeting_id):
        with self.transaction() as db:
            meeting = self._meeting(db, meeting_id)
            if meeting["status"] != "failed":
                raise StoreError("Only failed audio or final report jobs can be retried")
            now = time.time()
            db.execute("UPDATE meetings SET status='processing',error=NULL,updated_at=? WHERE id=?", (now, meeting_id))
            audio = db.execute("SELECT status FROM audio_jobs WHERE meeting_id=?", (meeting_id,)).fetchone()
            if audio and audio["status"] == "failed":
                db.execute("UPDATE audio_jobs SET status='queued',attempts=0,error=NULL,available_at=? WHERE meeting_id=?", (now, meeting_id))
            else:
                self._enqueue(db, meeting_id, "final", meeting["final_sequence"], now)
        return self.snapshot(meeting_id)

    def _audio_snapshot(self, db, meeting_id):
        row = db.execute("SELECT filename,bytes,status,attempts,error,metadata FROM audio_jobs WHERE meeting_id=?", (meeting_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"]) if result["metadata"] else None
        return result

    def snapshot(self, meeting_id):
        with self.lock:
            row = self._meeting(self.connection, meeting_id)
            result = dict(row)
            result["meeting_id"] = result.pop("id")
            result.pop("next_incremental")
            result["report"] = json.loads(result["report"]) if result["report"] else None
            result["segments"] = [dict(item) for item in self.connection.execute("SELECT sequence,start,end,speaker,text FROM segments WHERE meeting_id=? ORDER BY sequence", (meeting_id,))]
            result["audio"] = self._audio_snapshot(self.connection, meeting_id)
            return result

    def history(self, limit=100, offset=0):
        with self.lock:
            results = [dict(row) for row in self.connection.execute("SELECT id AS meeting_id,title,status,last_sequence,created_at,updated_at,error FROM meetings ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))]
            for result in results:
                result["audio"] = self._audio_snapshot(self.connection, result["meeting_id"])
            return results

    def search(self, query, limit=50):
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.lock:
            return [dict(row) for row in self.connection.execute("SELECT s.meeting_id,m.title,s.sequence,s.start,s.end,s.speaker,s.text FROM segments s JOIN meetings m ON m.id=s.meeting_id WHERE s.text LIKE ? ESCAPE '\\' ORDER BY m.created_at DESC,s.sequence LIMIT ?", (pattern, limit))]

    def recover(self):
        with self.transaction() as db:
            db.execute("UPDATE audio_jobs SET status='queued',attempts=0,available_at=? WHERE status='transcribing'", (time.time(),))
            interrupted = db.execute("SELECT meeting_id FROM audio_jobs WHERE status='recording'").fetchall()
            for row in interrupted:
                message = "Recording was interrupted by a station restart; archived audio is preserved. Retry to transcribe the available audio"
                db.execute("UPDATE audio_jobs SET status='failed',error=? WHERE meeting_id=?", (message, row["meeting_id"]))
                db.execute("UPDATE meetings SET status='failed',error=? WHERE id=?", (message, row["meeting_id"]))
            db.execute("UPDATE jobs SET status='superseded' WHERE kind='incremental' AND status IN ('queued','running') AND meeting_id IN (SELECT id FROM meetings WHERE status!='recording')")
            # A graceful stop or crash leaves running jobs durable. Merge any coalesced successor.
            for job in db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                queued = db.execute("SELECT id,through_sequence FROM jobs WHERE meeting_id=? AND kind=? AND status='queued'", (job["meeting_id"], job["kind"])).fetchone()
                if queued:
                    db.execute("UPDATE jobs SET through_sequence=MAX(through_sequence,?),attempts=0 WHERE id=?", (job["through_sequence"], queued["id"]))
                    db.execute("UPDATE jobs SET status='superseded' WHERE id=?", (job["id"],))
                else:
                    db.execute("UPDATE jobs SET status='queued',attempts=0,available_at=? WHERE id=?", (time.time(), job["id"]))

    def schedule_due(self):
        now = time.time()
        with self.transaction() as db:
            for meeting in db.execute("SELECT * FROM meetings WHERE status='recording' AND next_incremental<=? AND last_sequence>report_sequence", (now,)).fetchall():
                self._enqueue(db, meeting["id"], "incremental", meeting["last_sequence"], now)
                db.execute("UPDATE meetings SET next_incremental=? WHERE id=?", (now + self.incremental_seconds, meeting["id"]))

    def claim(self):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='queued' AND available_at<=? ORDER BY CASE kind WHEN 'final' THEN 0 ELSE 1 END,id LIMIT 1", (time.time(),)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (row["id"],))
            result = dict(row)
            result["attempts"] += 1
            return result

    def complete(self, job, report):
        now = time.time()
        with self.transaction() as db:
            db.execute("UPDATE jobs SET status='done',error=NULL WHERE id=?", (job["id"],))
            db.execute("UPDATE meetings SET report=?,report_sequence=?,error=NULL,updated_at=?,status=CASE WHEN ?='final' THEN 'complete' ELSE status END WHERE id=?", (report.model_dump_json(), job["through_sequence"], now, job["kind"], job["meeting_id"]))

    def fail(self, job, message, max_attempts):
        with self.transaction() as db:
            meeting = self._meeting(db, job["meeting_id"])
            successor = db.execute("SELECT id FROM jobs WHERE meeting_id=? AND kind=? AND status='queued'", (job["meeting_id"], job["kind"])).fetchone()
            retry = job["attempts"] < max_attempts and not successor and not (job["kind"] == "incremental" and meeting["status"] != "recording")
            db.execute("UPDATE jobs SET status=?,error=?,available_at=? WHERE id=?", ("queued" if retry else "failed", message, time.time() + min(60, 2 ** job["attempts"]), job["id"]))
            db.execute("UPDATE meetings SET error=?,updated_at=?,status=CASE WHEN ?='final' AND ?=0 THEN 'failed' ELSE status END WHERE id=?", (message, time.time(), job["kind"], int(retry), job["meeting_id"]))

    def create_audio(self, meeting_id, title, archive_name, filename, byte_count, recording=False):
        now = time.time()
        with self.transaction() as db:
            db.execute("INSERT INTO meetings(id,title,status,created_at,updated_at,next_incremental) VALUES(?,?,?,?,?,?)",
                       (meeting_id, title, "recording" if recording else "processing", now, now, now + self.incremental_seconds))
            db.execute("INSERT INTO audio_jobs(meeting_id,archive_name,filename,bytes,status,available_at) VALUES(?,?,?,?,?,?)",
                       (meeting_id, archive_name, filename, byte_count, "recording" if recording else "queued", now))
        return self.snapshot(meeting_id)

    def audio_archive_name(self, meeting_id):
        with self.lock:
            self._meeting(self.connection, meeting_id)
            row = self.connection.execute("SELECT archive_name,status FROM audio_jobs WHERE meeting_id=?", (meeting_id,)).fetchone()
            if row is None:
                raise StoreError("No recording archived for this meeting", 404)
            if row["status"] == "recording":
                raise StoreError("Stop the recording before downloading its audio")
            return row["archive_name"]

    def queue_recording(self, meeting_id, byte_count):
        with self.transaction() as db:
            self._meeting(db, meeting_id)
            row = db.execute("SELECT status FROM audio_jobs WHERE meeting_id=?", (meeting_id,)).fetchone()
            if row is None or row["status"] != "recording":
                raise StoreError("Meeting is not an active board recording")
            now = time.time()
            db.execute("UPDATE audio_jobs SET status='queued',bytes=?,available_at=?,error=NULL WHERE meeting_id=?", (byte_count, now, meeting_id))
            db.execute("UPDATE meetings SET status='processing',error=NULL,updated_at=? WHERE id=?", (now, meeting_id))
        return self.snapshot(meeting_id)

    def claim_audio(self):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM audio_jobs WHERE status='queued' AND available_at<=? ORDER BY available_at,meeting_id LIMIT 1", (time.time(),)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE audio_jobs SET status='transcribing',attempts=attempts+1 WHERE meeting_id=?", (row["meeting_id"],))
            result = dict(row)
            result["attempts"] += 1
            return result

    def complete_audio(self, job, segments, metadata):
        with self.transaction() as db:
            meeting_id = job["meeting_id"]
            self._meeting(db, meeting_id)
            current = db.execute("SELECT status FROM audio_jobs WHERE meeting_id=?", (meeting_id,)).fetchone()
            if current["status"] == "done":
                return  # Replay after a committed result is harmless.
            if current["status"] != "transcribing":
                raise StoreError("Audio job is not running")
            if db.execute("SELECT COUNT(*) FROM segments WHERE meeting_id=?", (meeting_id,)).fetchone()[0]:
                raise StoreError("Audio import already has a transcript; refusing to overwrite it")
            for sequence, segment in enumerate(segments, start=1):
                if segment.sequence != sequence:
                    raise StoreError("Audio transcript sequences must be contiguous")
                db.execute("INSERT INTO segments VALUES(?,?,?,?,?,?)", (meeting_id, sequence, segment.start, segment.end, segment.speaker, segment.text))
            now = time.time()
            final_sequence = len(segments)
            db.execute("UPDATE meetings SET status='processing',last_sequence=?,final_sequence=?,updated_at=?,error=NULL WHERE id=?", (final_sequence, final_sequence, now, meeting_id))
            db.execute("UPDATE audio_jobs SET status='done',error=NULL,metadata=? WHERE meeting_id=?", (json.dumps(metadata, ensure_ascii=False, allow_nan=False), meeting_id))
            self._enqueue(db, meeting_id, "final", final_sequence, now)

    def fail_audio(self, job, message, max_attempts):
        retry = job.get("attempts", 1) < max_attempts
        with self.transaction() as db:
            now = time.time()
            db.execute("UPDATE audio_jobs SET status=?,error=?,available_at=? WHERE meeting_id=?", ("queued" if retry else "failed", message, now + min(60, 2 ** job.get("attempts", 1)), job["meeting_id"]))
            db.execute("UPDATE meetings SET status=?,error=?,updated_at=? WHERE id=?", ("processing" if retry else "failed", message, now, job["meeting_id"]))
