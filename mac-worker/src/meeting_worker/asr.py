from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import importlib.util
import shutil
import tempfile
from types import SimpleNamespace

from .config import Settings
from .schemas import Transcript, TranscriptSegment


class ASRError(RuntimeError):
    pass


class ASRAdapter(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, language: str) -> Transcript: ...


def local_audio_settings(config: Settings, diarization: bool = False):
    return SimpleNamespace(
        ffmpeg_binary=shutil.which(config.ffmpeg_binary) or config.ffmpeg_binary,
        whisper_binary=shutil.which(config.whisper_binary) or config.whisper_binary,
        whisper_model=config.whisper_model,
        whisper_prompt=config.whisper_prompt,
        whisper_vad_model=config.whisper_vad_model,
        segmentation_model=config.segmentation_model if diarization else "",
        embedding_model=config.embedding_model if diarization else "",
        process_timeout=config.audio_timeout, max_audio_seconds=config.max_audio_seconds)


class WhisperCPPAdapter(ASRAdapter):
    def __init__(self, config: Settings):
        self.config = config

    def transcribe(self, audio_path: Path, language: str) -> Transcript:
        from .local_audio import AudioProcessor
        model = self.config.whisper_model_en if language == "en" and self.config.whisper_model_en else self.config.whisper_model
        settings = self.config.model_copy(update={"whisper_model": model})
        with tempfile.TemporaryDirectory(prefix="asr-", dir=self.config.data_dir / "work") as directory:
            source = Path(directory) / "source.wav"
            shutil.copyfile(audio_path, source)
            output = AudioProcessor(local_audio_settings(settings)).transcribe(source, language)
        segments = [TranscriptSegment(id="seg_{:05d}".format(item["sequence"]), start=item["start"],
                                      end=item["end"], text=item["text"], language=language,
                                      tokens=item["tokens"], needs_review=item["needs_review"])
                    for item in output["segments"]]
        detected = output.get("language") or language
        for segment in segments:
            segment.language = detected
        transcript = Transcript(language=detected, model="whisper.cpp:" + Path(model).name,
                          raw_text=" ".join(item.text for item in segments), segments=segments)
        flag_repetition(transcript)
        return transcript


def flag_repetition(transcript: Transcript) -> None:
    """Flag repeated ASR loops without deleting potentially spoken material."""
    import re
    run = []
    previous = None
    suspicious = False
    for segment in transcript.segments:
        words = re.findall(r"[^\W_]+", segment.text.casefold())
        key = tuple(words)
        run = run + [segment] if key == previous else [segment]
        previous = key
        if len(words) >= 3 and len(run) >= 3:
            suspicious = True
            for repeated in run:
                repeated.needs_review = True
    if suspicious:
        warning = "Repeated recognition output detected. Check the original audio; repeated text may be a transcription error."
        if warning not in transcript.warnings:
            transcript.warnings.append(warning)


def capabilities(config: Settings):
    from .local_audio import AudioProcessor, local_file
    local = AudioProcessor(local_audio_settings(config, True)).capabilities()
    def configured_model(package, model):
        return {"ready": importlib.util.find_spec(package) is not None and Path(model).is_dir(),
                "detail": "Requires installed package and explicit local model directory"}
    return {"whisper-cpp": local["transcription"],
            "shyngys": configured_model("transformers", config.shyngys_model),
            "gigaam": configured_model("transformers", config.gigaam_model),
            "mlx-distil-whisper": configured_model("mlx_whisper", config.distil_model),
            "diarization": local["diarization"]}


class MLXWhisperAdapter(ASRAdapter):
    def __init__(self, model: str):
        self.model = model

    def transcribe(self, audio_path: Path, language: str) -> Transcript:
        if not Path(self.model).is_dir():
            raise ASRError("MLX runtime requires an explicit local model directory; provision it first")
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
    if profile == "whisper-cpp":
        return WhisperCPPAdapter(config)
    if profile == "mlx-distil-whisper":
        return MLXWhisperAdapter(config.distil_model)
    if profile == "shyngys":
        return TransformersWhisperAdapter(config.shyngys_model, config.hf_offline)
    if profile == "gigaam":
        return GigaAMAdapter(config.gigaam_model, config.gigaam_revision, config.hf_offline)
    raise ASRError(f"Unknown ASR profile: {profile}")
