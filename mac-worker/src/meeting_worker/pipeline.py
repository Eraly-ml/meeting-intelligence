from __future__ import annotations

import subprocess
import threading
import os
import wave
from datetime import UTC, datetime
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
from .local_audio import command


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
        self.active_job: str | None = None

    def run(self, job_id: str) -> None:
        with self._inference_lock:
            self.active_job = job_id
            try:
                self._run_locked(job_id)
            finally:
                self.active_job = None

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
                command([self.config.ffmpeg_binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-protocol_whitelist", "file,pipe", "-format_whitelist", "wav,mp3,mov,matroska,webm,ogg,caf,flac",
                    "-i", str(source), "-map", "0:a:0", "-vn", "-t", str(self.config.max_audio_seconds + 1),
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                    self.config.data_dir / "work", min(300, self.config.audio_timeout))
                with wave.open(str(wav), "rb") as audio:
                    duration = audio.getnframes() / audio.getframerate()
                if duration <= 0 or duration > self.config.max_audio_seconds:
                    raise RuntimeError("Audio is empty or exceeds the configured duration limit")
                job = self.store.update(job.id, JobStage.TRANSCRIBING)
                profile = self.config.asr_en if job.manifest.language_mode == "en" else self.config.asr_kk_ru
                language = "en" if job.manifest.language_mode == "en" else "auto"
                transcript = get_adapter(profile, self.config).transcribe(wav, language)

                if job.manifest.diarization:
                    if not self.config.enable_diarization:
                        raise RuntimeError("Diarization was requested but is disabled on this worker")
                    job = self.store.update(job.id, JobStage.DIARIZING)
                    transcript = diarize(wav, transcript, self.config, job.manifest.min_speakers, job.manifest.max_speakers)

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
                duration_seconds=duration if job.source_kind == "audio" else None,
            )
            # call_ollama already validated citations and reviewed final claims.
            # Revalidating here would erase semantic review failures.

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
            export_pdf(paths["pdf"], protocol, font_path=self.config.pdf_font or None)

            result_path = self.config.data_dir / "results" / f"{job.id}.json"
            completed = job.model_copy(update={"stage": JobStage.COMPLETED, "result_path": str(result_path),
                                               "updated_at": datetime.now(UTC)})
            result = JobResult(
                job=completed, transcript=transcript, protocol=protocol,
                exports={name: str(path) for name, path in paths.items()},
            )
            temporary = result_path.with_suffix(".partial")
            with temporary.open("w", encoding="utf-8") as output:
                output.write(result.model_dump_json(indent=2))
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(result_path)
            # Publish completion only after the result and exports are readable.
            self.store.update(job.id, JobStage.COMPLETED, result_path=str(result_path))
        except Exception as exc:
            current = self.store.get(job.id)
            if current and current.stage != JobStage.CANCELLED:
                self.store.update(
                    job.id, JobStage.FAILED,
                    error_code=type(exc).__name__.upper(),
                    error_message=str(exc)[:1000] if isinstance(exc, RuntimeError) else "Local processing failed; saved source can be retried.",
                )
