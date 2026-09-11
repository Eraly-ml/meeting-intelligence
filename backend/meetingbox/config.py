import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def local_model_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("OLLAMA_URL must use a literal loopback address or localhost") from exc
    if not address.is_loopback or parsed.scheme not in ("http", "https"):
        raise ValueError("OLLAMA_URL must be an HTTP(S) loopback endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("OLLAMA_URL must not contain credentials, a path, query, or fragment")
    host = "[{}]".format(host) if address.version == 6 else host
    netloc = "{}:{}".format(host, parsed.port) if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def private_engine_url(value: str) -> str:
    """Pin the engine to a literal private address; never resolve arbitrary DNS."""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("MEETINGBOX_ENGINE_URL must use a private LAN IP address or localhost") from exc
    private_ranges = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    allowed = address.is_loopback or address.is_link_local or any(address in ipaddress.ip_network(network) for network in private_ranges)
    if not allowed or parsed.scheme not in ("http", "https"):
        raise ValueError("MEETINGBOX_ENGINE_URL must be an HTTP(S) private LAN or loopback endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or "%" in host:
        raise ValueError("MEETINGBOX_ENGINE_URL must not contain credentials, a path, query, fragment, or zone ID")
    host = "[{}]".format(host) if address.version == 6 else host
    netloc = "{}:{}".format(host, parsed.port) if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


@dataclass(frozen=True)
class Settings:
    token: str
    database: Path = Path("data/meetingbox.sqlite3")
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:3b"
    incremental_seconds: float = 45.0
    model_timeout: float = 180.0
    context_tokens: int = 8192
    output_tokens: int = 1536
    max_attempts: int = 3
    poll_seconds: float = 0.5
    engine_url: str = ""
    engine_token: str = ""
    engine_timeout: float = 1800.0
    archive: Path = None
    max_upload_bytes: int = 512 * 1024 * 1024
    alsa_device: str = "default"

    def __post_init__(self):
        if len(self.token) < 16 or self.token.strip() != self.token:
            raise ValueError("MEETINGBOX_TOKEN is required and must contain at least 16 characters, with no outer whitespace")
        object.__setattr__(self, "ollama_url", local_model_url(self.ollama_url))
        object.__setattr__(self, "archive", self.archive or self.database.parent / "recordings")
        if self.engine_url:
            object.__setattr__(self, "engine_url", private_engine_url(self.engine_url))
            if len(self.engine_token) < 16 or self.engine_token.strip() != self.engine_token:
                raise ValueError("MEETINGBOX_ENGINE_TOKEN must contain at least 16 characters, with no outer whitespace")
        if self.engine_timeout <= 0 or self.max_upload_bytes < 1:
            raise ValueError("Engine timeout and upload limit must be positive")
        if not self.model or "cloud" in self.model.lower() or "://" in self.model:
            raise ValueError("MEETINGBOX_MODEL must name an installed local model, not a cloud model")
        if self.context_tokens < 8192 or self.output_tokens < 256 or self.output_tokens >= self.context_tokens // 2:
            raise ValueError("Use a context of at least 8192 tokens and an output budget below half of it")
        if self.incremental_seconds <= 0 or self.max_attempts < 1 or self.poll_seconds <= 0:
            raise ValueError("Worker timing and retry settings must be positive")

    @classmethod
    def from_env(cls):
        return cls(
            token=os.getenv("MEETINGBOX_TOKEN", ""),
            database=Path(os.getenv("MEETINGBOX_DATABASE", "data/meetingbox.sqlite3")),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            model=os.getenv("MEETINGBOX_MODEL", "qwen2.5:3b"),
            incremental_seconds=float(os.getenv("MEETINGBOX_INCREMENTAL_SECONDS", "45")),
            model_timeout=float(os.getenv("MEETINGBOX_MODEL_TIMEOUT", "180")),
            context_tokens=int(os.getenv("MEETINGBOX_CONTEXT_TOKENS", "8192")),
            engine_url=os.getenv("MEETINGBOX_ENGINE_URL", ""),
            engine_token=os.getenv("MEETINGBOX_ENGINE_TOKEN", ""),
            engine_timeout=float(os.getenv("MEETINGBOX_ENGINE_TIMEOUT", "1800")),
            archive=Path(os.environ["MEETINGBOX_ARCHIVE"]) if os.getenv("MEETINGBOX_ARCHIVE") else None,
            max_upload_bytes=int(os.getenv("MEETINGBOX_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
            alsa_device=os.getenv("MEETINGBOX_ALSA_DEVICE", "default"),
        )
