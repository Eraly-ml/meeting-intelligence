from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MI_", env_file=".env")

    data_dir: Path = Path("data")
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    allowed_origins: str = "http://localhost:8080,http://127.0.0.1:8080"
    api_token: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b-q4_K_M"
    ollama_context: int = 16384
    protocol_chunk_chars: int = 24000
    asr_kk_ru: str = "shyngys"
    asr_en: str = "mlx-distil-whisper"
    shyngys_model: str = "shyngys879/kazakh-whisper-large-v3-turbo"
    gigaam_model: str = "ai-sage/GigaAM-Multilingual"
    gigaam_revision: str = "large_ctc"
    distil_model: str = "mlx-community/distil-whisper-large-v3"
    enable_diarization: bool = False
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    hf_token: str = ""
    hf_offline: bool = True
    ffmpeg_binary: str = "ffmpeg"

    @property
    def cors_origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]

    def prepare(self) -> None:
        for child in ("sources", "work", "results", "exports"):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)


settings = Settings()
