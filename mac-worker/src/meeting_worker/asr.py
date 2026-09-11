from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .config import Settings
from .schemas import Transcript, TranscriptSegment


class ASRError(RuntimeError):
    pass


class ASRAdapter(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, language: str) -> Transcript: ...


class MLXWhisperAdapter(ASRAdapter):
    def __init__(self, model: str):
        self.model = model

    def transcribe(self, audio_path: Path, language: str) -> Transcript:
        try:
            import mlx_whisper  # type: ignore
        except ImportError as exc:
            raise ASRError("mlx-whisper is not installed") from exc
        kwargs: dict[str, object] = {
            "path_or_hf_repo": self.model,
            "word_timestamps": True,
            "verbose": False,
        }
        if language != "auto":
            kwargs["language"] = language
        result = mlx_whisper.transcribe(str(audio_path), **kwargs)
        segments = [
            TranscriptSegment(
                id=f"seg_{idx:05d}",
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item["text"]).strip(),
                language=result.get("language", language),
            )
            for idx, item in enumerate(result.get("segments", []), start=1)
            if str(item.get("text", "")).strip()
        ]
        return Transcript(
            language=result.get("language", language),
            model=self.model,
            raw_text=" ".join(item.text for item in segments),
            segments=segments,
        )


class TransformersWhisperAdapter(ASRAdapter):
    def __init__(self, model: str, offline: bool):
        self.model = model
        self.offline = offline

    def transcribe(self, audio_path: Path, language: str) -> Transcript:
        try:
            import torch
            from transformers import pipeline
        except ImportError as exc:
            raise ASRError("torch and transformers are not installed") from exc
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        pipe = pipeline(
            "automatic-speech-recognition",
            model=self.model,
            device=device,
            model_kwargs={"local_files_only": self.offline},
        )
        generate_kwargs = {"task": "transcribe"}
        if language != "auto":
            generate_kwargs["language"] = language
        result = pipe(
            str(audio_path),
            return_timestamps=True,
            chunk_length_s=30,
            generate_kwargs=generate_kwargs,
        )
        chunks = result.get("chunks") or []
        segments = []
        for idx, chunk in enumerate(chunks, start=1):
            start, end = chunk.get("timestamp", (None, None))
            segments.append(
                TranscriptSegment(
                    id=f"seg_{idx:05d}", start=start, end=end,
                    text=str(chunk.get("text", "")).strip(), language=language,
                )
            )
        if not segments:
            segments = [TranscriptSegment(id="seg_00001", text=str(result["text"]).strip())]
        return Transcript(
            language=language, model=self.model,
            raw_text=str(result["text"]).strip(), segments=segments,
        )


class GigaAMAdapter(ASRAdapter):
    def __init__(self, model: str, revision: str, offline: bool):
        self.model = model
        self.revision = revision
        self.offline = offline

    def transcribe(self, audio_path: Path, language: str) -> Transcript:
        try:
            from transformers import AutoModel
        except ImportError as exc:
            raise ASRError("transformers is not installed") from exc
        model = AutoModel.from_pretrained(
            self.model, revision=self.revision, trust_remote_code=True,
            local_files_only=self.offline,
        )
        text = str(model.transcribe(str(audio_path))).strip()
        return Transcript(
            language=language,
            model=f"{self.model}@{self.revision}",
            raw_text=text,
            segments=[TranscriptSegment(id="seg_00001", text=text, language=language)],
        )


def get_adapter(profile: str, config: Settings) -> ASRAdapter:
    if profile == "mlx-distil-whisper":
        return MLXWhisperAdapter(config.distil_model)
    if profile == "shyngys":
        return TransformersWhisperAdapter(config.shyngys_model, config.hf_offline)
    if profile == "gigaam":
        return GigaAMAdapter(config.gigaam_model, config.gigaam_revision, config.hf_offline)
    raise ASRError(f"Unknown ASR profile: {profile}")
