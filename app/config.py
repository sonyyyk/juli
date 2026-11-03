"""Central JULI configuration.

Every setting is read from its ``JULI_*`` environment variable when the getter
is called, not at import time, so changes to the environment take effect
immediately. Invalid values raise ``ValueError`` naming the variable.
"""

import math
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_COLLECTOR_INTERVAL_SECONDS = 10
DEFAULT_RECENT_WINDOW_MINUTES = 15
DEFAULT_MONITORING_CONTEXT_MAX_CHARS = 4000


def get_db_path() -> Path:
    """Return the SQLite path: ``JULI_DB_PATH`` or ``PROJECT_ROOT/data/juli.db``.

    A relative ``JULI_DB_PATH`` is resolved against the current directory.
    """
    raw = os.environ.get("JULI_DB_PATH")
    if raw is None:
        return PROJECT_ROOT / "data" / "juli.db"
    if not raw.strip():
        raise ValueError("JULI_DB_PATH must be a non-empty path")
    return Path(raw.strip()).expanduser().resolve()


def get_collector_interval_seconds() -> int:
    """Return the Collector interval in seconds (``JULI_COLLECTOR_INTERVAL_SECONDS``)."""
    return _positive_int(
        "JULI_COLLECTOR_INTERVAL_SECONDS", DEFAULT_COLLECTOR_INTERVAL_SECONDS
    )


def get_recent_window_minutes() -> float:
    """Return the "recent" monitoring window in minutes (``JULI_RECENT_WINDOW_MINUTES``)."""
    name = "JULI_RECENT_WINDOW_MINUTES"
    raw = os.environ.get(name)
    if raw is None:
        return DEFAULT_RECENT_WINDOW_MINUTES
    try:
        value = float(raw.strip())
    except (ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite positive number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def get_monitoring_context_max_chars() -> int:
    """Return the monitoring context size limit (``JULI_MONITORING_CONTEXT_MAX_CHARS``)."""
    return _positive_int(
        "JULI_MONITORING_CONTEXT_MAX_CHARS", DEFAULT_MONITORING_CONTEXT_MAX_CHARS
    )


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
