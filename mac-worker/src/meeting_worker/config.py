from pathlib import Path
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MI_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    allowed_origins: str = "http://localhost:8080,http://127.0.0.1:8080"
    api_token: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    # A two-minute, evidence-rich transcript and the strict protocol schema fit
    # in one conservative request at 32K. Smaller contexts can strand a valid
    # transcript once the accumulated protocol itself consumes the next chunk.
    ollama_context: int = 32768
    protocol_chunk_chars: int = 24000
    asr_kk_ru: str = "whisper-cpp"
    asr_en: str = "whisper-cpp"
    shyngys_model: str = "shyngys879/kazakh-whisper-large-v3-turbo"
    gigaam_model: str = "ai-sage/GigaAM-Multilingual"
    gigaam_revision: str = "large_ctc"
    distil_model: str = "mlx-community/distil-whisper-large-v3"
    enable_diarization: bool = False
    diarization_backend: str = "sherpa-onnx"
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    hf_token: str = ""
    hf_offline: bool = True
    ffmpeg_binary: str = "ffmpeg"
    whisper_binary: str = ""
    whisper_model: str = ""
    segmentation_model: str = ""
    embedding_model: str = ""
    pdf_font: str = ""
    max_upload_bytes: int = 512 * 1024 * 1024
    upload_timeout: float = 300
    audio_timeout: float = 1800
    max_audio_seconds: float = 14400
    ollama_timeout: float = 600
    semantic_verification: bool = True

    @field_validator("ollama_url")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        host = "127.0.0.1" if host == "localhost" else host
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("Ollama must use a literal loopback address") from exc
        if not address.is_loopback or parsed.scheme not in ("http", "https"):
            raise ValueError("Ollama must use a loopback endpoint")
        if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Ollama URL may not include credentials, paths or query parameters")
        host = "[{}]".format(host) if address.version == 6 else host
        return urlunsplit((parsed.scheme, "{}:{}".format(host, parsed.port) if parsed.port else host, "", "", ""))

    @field_validator("ollama_model")
    @classmethod
    def local_model(cls, value: str) -> str:
        if not value or "cloud" in value.lower() or "://" in value:
            raise ValueError("Select an installed local Ollama model")
        return value

    @field_validator("max_upload_bytes", "upload_timeout", "audio_timeout", "max_audio_seconds", "ollama_timeout")
    @classmethod
    def positive(cls, value):
        if value <= 0:
            raise ValueError("Resource limits must be positive")
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]

    def prepare(self) -> None:
        if len(self.api_token) < 16 or self.api_token.strip() != self.api_token:
            raise ValueError("MI_API_TOKEN must contain at least 16 characters with no outer whitespace")
        if not self.hf_offline:
            raise ValueError("Runtime model downloads are disabled; provision models first and use MI_HF_OFFLINE=true")
        for child in ("sources", "work", "results", "exports"):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)
