from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .asr import get_adapter
from .config import Settings
from .exports import export_csv, export_json, export_pdf
from .diarization import diarize
from .protocol import call_ollama, validate_evidence
from .schemas import (
    JobRecord, JobResult, JobStage, MeetingMetadata, Transcript, TranscriptSegment,
)
from .store import JobStore


def _text_transcript(path: Path, language: str) -> Transcript:
    text = path.read_text(encoding="utf-8").strip()
    blocks = [block.strip() for block in text.splitlines() if block.strip()]
    segments = [
        TranscriptSegment(id=f"seg_{idx:05d}", text=block, language=language)
        for idx, block in enumerate(blocks or [text], start=1)
    ]
    return Transcript(language=language, model="provided-text", raw_text=text, segments=segments)


class Pipeline:
    def __init__(self, config: Settings, store: JobStore):
        self.config = config
        self.store = store
        # One heavy model job at a time prevents unified-memory exhaustion on a MacBook Air.
        self._inference_lock = threading.Lock()

    def run(self, job_id: str) -> None:
        with self._inference_lock:
            self._run_locked(job_id)

    def _run_locked(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None or job.stage == JobStage.CANCELLED:
            return
        try:
            source = Path(job.source_path)
            if job.source_kind == "text":
                transcript = _text_transcript(source, job.manifest.language_mode)
            else:
                job = self.store.update(job.id, JobStage.PREPROCESSING)
                wav = self.config.data_dir / "work" / f"{job.id}.wav"
                subprocess.run([
                    self.config.ffmpeg_binary, "-y", "-i", str(source), "-ar", "16000",
                    "-ac", "1", "-c:a", "pcm_s16le", str(wav),
                ], check=True, capture_output=True)
                job = self.store.update(job.id, JobStage.TRANSCRIBING)
                profile = self.config.asr_en if job.manifest.language_mode == "en" else self.config.asr_kk_ru
                language = "en" if job.manifest.language_mode == "en" else "auto"
                transcript = get_adapter(profile, self.config).transcribe(wav, language)

                if job.manifest.diarization:
                    if not self.config.enable_diarization:
                        raise RuntimeError("Diarization was requested but is disabled on this worker")
                    job = self.store.update(job.id, JobStage.DIARIZING)
                    transcript = diarize(wav, transcript, self.config)

            transcript_path = self.config.data_dir / "work" / f"{job.id}.transcript.json"
            transcript_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
            if self.store.get(job.id).stage == JobStage.CANCELLED:
                return
            job = self.store.update(job.id, JobStage.EXTRACTING)
            protocol = call_ollama(transcript, job.manifest, self.config)
            job = self.store.update(job.id, JobStage.VALIDATING)
            protocol.metadata = MeetingMetadata(
                title=job.manifest.title,
                meeting_date=job.manifest.meeting_date,
                timezone=job.manifest.timezone,
                language=transcript.language,
            )
            protocol = validate_evidence(protocol, transcript)

            if self.store.get(job.id).stage == JobStage.CANCELLED:
                return

            job = self.store.update(job.id, JobStage.EXPORTING)
            export_dir = self.config.data_dir / "exports" / job.id
            export_dir.mkdir(parents=True, exist_ok=True)
            paths = {
                "json": export_dir / "meeting.json",
                "csv": export_dir / "action-items.csv",
                "pdf": export_dir / "meeting.pdf",
            }
            export_json(paths["json"], protocol, transcript)
            export_csv(paths["csv"], protocol)
            export_pdf(paths["pdf"], protocol)

            result_path = self.config.data_dir / "results" / f"{job.id}.json"
            completed = self.store.update(job.id, JobStage.COMPLETED, result_path=str(result_path))
            result = JobResult(
                job=completed, transcript=transcript, protocol=protocol,
                exports={name: str(path) for name, path in paths.items()},
            )
            result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:
            current = self.store.get(job.id)
            if current and current.stage != JobStage.CANCELLED:
                self.store.update(
                    job.id, JobStage.FAILED,
                    error_code=type(exc).__name__.upper(), error_message=str(exc)[:2000],
                )
