import os
from dataclasses import dataclass
from pathlib import Path

from meetingbox.config import Settings


@dataclass(frozen=True)
class EngineSettings:
    token: str
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:4b"
    context_tokens: int = 16384
    output_tokens: int = 2048
    model_timeout: float = 600
    whisper_binary: str = ""
    whisper_model: str = ""
    ffmpeg_binary: str = ""
    segmentation_model: str = ""
    embedding_model: str = ""
    pdf_font: str = "/System/Library/Fonts/Supplemental/Arial.ttf"
    upload_bytes: int = 512 * 1024 * 1024
    upload_timeout: float = 300
    process_timeout: float = 1800
    max_audio_seconds: float = 4 * 3600
    temp_directory: str = ""

    def __post_init__(self):
        # Reuse the hub's strict loopback and local model policy.
        checked = self.reasoner_settings()
        object.__setattr__(self, "ollama_url", checked.ollama_url)
        if min(self.upload_bytes, self.upload_timeout, self.process_timeout, self.max_audio_seconds) <= 0:
            raise ValueError("Engine upload and processing limits must be positive")

    def reasoner_settings(self):
        return Settings(token=self.token, ollama_url=self.ollama_url, model=self.model,
                        context_tokens=self.context_tokens, output_tokens=self.output_tokens,
                        model_timeout=self.model_timeout)

    @classmethod
    def from_env(cls):
        return cls(
            token=os.getenv("MEETINGBOX_ENGINE_TOKEN", ""),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            model=os.getenv("MEETINGBOX_MODEL", "qwen3.5:4b"),
            context_tokens=int(os.getenv("MEETINGBOX_CONTEXT_TOKENS", "16384")),
            model_timeout=float(os.getenv("MEETINGBOX_MODEL_TIMEOUT", "600")),
            whisper_binary=os.getenv("MEETINGBOX_WHISPER_BINARY", ""),
            whisper_model=os.getenv("MEETINGBOX_WHISPER_MODEL", ""),
            ffmpeg_binary=os.getenv("MEETINGBOX_FFMPEG_BINARY", ""),
            segmentation_model=os.getenv("MEETINGBOX_SEGMENTATION_MODEL", ""),
            embedding_model=os.getenv("MEETINGBOX_EMBEDDING_MODEL", ""),
            pdf_font=os.getenv("MEETINGBOX_PDF_FONT", "/System/Library/Fonts/Supplemental/Arial.ttf"),
            upload_bytes=int(os.getenv("MEETINGBOX_MAX_AUDIO_BYTES", str(512 * 1024 * 1024))),
            process_timeout=float(os.getenv("MEETINGBOX_AUDIO_TIMEOUT", "1800")),
            max_audio_seconds=float(os.getenv("MEETINGBOX_MAX_AUDIO_SECONDS", "14400")),
            temp_directory=os.getenv("MEETINGBOX_ENGINE_TEMP", ""),
        )


def local_file(path):
    return bool(path) and Path(path).is_file()


def executable(path):
    return local_file(path) and os.access(path, os.X_OK)
