"""Configuration for the local Ollama provider."""

from dataclasses import dataclass
import math
import os
from urllib.parse import urlparse


@dataclass(frozen=True)
class OllamaConfig:
    """Connection settings; all values may be overridden with JULI env vars."""

    host: str = "http://127.0.0.1:11434"
    model: str = "llama3.2"
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not isinstance(self.host, str):
            raise ValueError("Ollama host must be an absolute HTTP(S) URL")
        parsed = urlparse(self.host)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama host must be an absolute HTTP(S) URL")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Ollama model must be a non-empty string")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise ValueError("Ollama timeout must be a finite positive number")
        try:
            valid_timeout = math.isfinite(self.timeout_seconds) and self.timeout_seconds > 0
        except (OverflowError, TypeError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("Ollama timeout must be a finite positive number")

    @classmethod
    def from_environment(cls) -> "OllamaConfig":
        """Load provider settings from the JULI_OLLAMA_* environment."""
        host = os.environ.get("JULI_OLLAMA_HOST", "http://127.0.0.1:11434")
        model = os.environ.get("JULI_OLLAMA_MODEL", "llama3.2")
        raw_timeout = os.environ.get("JULI_OLLAMA_TIMEOUT_SECONDS", "120")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("JULI_OLLAMA_TIMEOUT_SECONDS must be numeric") from None
        return cls(host=host, model=model, timeout_seconds=timeout)

    @property
    def chat_url(self) -> str:
        return f"{self.host.rstrip('/')}/api/chat"
