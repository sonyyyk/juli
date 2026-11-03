"""Configuration for the local Ollama provider."""

from dataclasses import dataclass
import math
import os
from urllib.parse import urlparse


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 120.0
DEFAULT_OLLAMA_THINK = False
DEFAULT_OLLAMA_NUM_CTX = 16384
DEFAULT_OLLAMA_TEMPERATURE = 0.2
MIN_OLLAMA_NUM_CTX = 2048
MAX_OLLAMA_TEMPERATURE = 2.0

_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


@dataclass(frozen=True)
class OllamaConfig:
    """Connection and generation settings; all values may be overridden with JULI env vars.

    ``think`` is always sent explicitly because thinking models (for example
    Qwen 3.5) reason by default when the field is omitted.
    """

    host: str = DEFAULT_OLLAMA_HOST
    model: str = DEFAULT_OLLAMA_MODEL
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS
    think: bool = DEFAULT_OLLAMA_THINK
    num_ctx: int = DEFAULT_OLLAMA_NUM_CTX
    temperature: float = DEFAULT_OLLAMA_TEMPERATURE
    seed: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.host, str):
            raise ValueError("Ollama host must be an absolute HTTP(S) URL")
        parsed = urlparse(self.host)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama host must be an absolute HTTP(S) URL")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Ollama model must be a non-empty string")
        if not _finite_number(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Ollama timeout must be a finite positive number")
        if not isinstance(self.think, bool):
            raise ValueError("Ollama think must be a boolean")
        if not _integer(self.num_ctx) or self.num_ctx < MIN_OLLAMA_NUM_CTX:
            raise ValueError(f"Ollama num_ctx must be an integer >= {MIN_OLLAMA_NUM_CTX}")
        if not _finite_number(self.temperature) or not (
            0 <= self.temperature <= MAX_OLLAMA_TEMPERATURE
        ):
            raise ValueError(
                f"Ollama temperature must be a finite number from 0 to {MAX_OLLAMA_TEMPERATURE:g}"
            )
        if self.seed is not None and not _integer(self.seed):
            raise ValueError("Ollama seed must be an integer or None")

    @classmethod
    def from_environment(cls) -> "OllamaConfig":
        """Load provider settings from the JULI_OLLAMA_* environment."""
        host = os.environ.get("JULI_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        model = os.environ.get("JULI_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        timeout = _env_float("JULI_OLLAMA_TIMEOUT_SECONDS", DEFAULT_OLLAMA_TIMEOUT_SECONDS)
        think = _env_bool("JULI_OLLAMA_THINK", DEFAULT_OLLAMA_THINK)
        num_ctx = _env_int("JULI_OLLAMA_NUM_CTX", DEFAULT_OLLAMA_NUM_CTX)
        temperature = _env_float("JULI_OLLAMA_TEMPERATURE", DEFAULT_OLLAMA_TEMPERATURE)
        seed = _env_int("JULI_OLLAMA_SEED", None)
        try:
            return cls(
                host=host,
                model=model,
                timeout_seconds=timeout,
                think=think,
                num_ctx=num_ctx,
                temperature=temperature,
                seed=seed,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid JULI_OLLAMA_* configuration: {exc}") from None

    @property
    def chat_url(self) -> str:
        return f"{self.host.rstrip('/')}/api/chat"

    def options(self) -> dict[str, object]:
        """Return the Ollama ``options`` object for a chat request."""
        options: dict[str, object] = {"num_ctx": self.num_ctx, "temperature": self.temperature}
        if self.seed is not None:
            options["seed"] = self.seed
        return options


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be one of: true, false, 1, 0, yes, no")


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be numeric") from None
