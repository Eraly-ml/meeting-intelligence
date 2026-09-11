import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def private_url(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("MI_STATION_WORKER_URL must use a private LAN IP address or localhost") from exc
    ranges = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    allowed = address.is_loopback or address.is_link_local or any(address in ipaddress.ip_network(network) for network in ranges)
    if not allowed or parsed.scheme not in ("http", "https"):
        raise ValueError("MI_STATION_WORKER_URL must be an HTTP(S) private LAN endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or "%" in host:
        raise ValueError("Worker URL must not contain credentials, paths, query, fragment, or a zone ID")
    host = "[{}]".format(host) if address.version == 6 else host
    netloc = "{}:{}".format(host, parsed.port) if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


@dataclass(frozen=True)
class Settings:
    token: str
    worker_token: str
    worker_url: str = "http://127.0.0.1:8765"
    data_dir: Path = Path("data/station")
    max_upload_bytes: int = 512 * 1024 * 1024
    poll_seconds: float = 2.0
    request_timeout: float = 120.0
    alsa_device: str = "default"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8766

    def __post_init__(self):
        for name, token in (("MI_STATION_TOKEN", self.token), ("MI_STATION_WORKER_TOKEN", self.worker_token)):
            if len(token) < 16 or token != token.strip():
                raise ValueError(name + " must contain at least 16 characters, with no outer whitespace")
        object.__setattr__(self, "worker_url", private_url(self.worker_url))
        if self.max_upload_bytes < 1 or self.poll_seconds <= 0 or self.request_timeout <= 0:
            raise ValueError("Upload size, polling interval and request timeout must be positive")

    @classmethod
    def from_env(cls):
        return cls(token=os.getenv("MI_STATION_TOKEN", ""), worker_token=os.getenv("MI_STATION_WORKER_TOKEN", ""),
                   worker_url=os.getenv("MI_STATION_WORKER_URL", "http://127.0.0.1:8765"),
                   data_dir=Path(os.getenv("MI_STATION_DATA_DIR", "data/station")),
                   max_upload_bytes=int(os.getenv("MI_STATION_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
                   alsa_device=os.getenv("MI_STATION_ALSA_DEVICE", "default"),
                   bind_host=os.getenv("MI_STATION_BIND_HOST", "127.0.0.1"),
                   bind_port=int(os.getenv("MI_STATION_BIND_PORT", "8766")))
