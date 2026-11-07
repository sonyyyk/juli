"""Host context for a new incident (specification 2.1): where its knowledge applies.

Read-only: Python's ``platform`` module and ``/etc/os-release``; no external
commands. Unknown values are ``None``, never guessed.
"""

from collections.abc import Callable
from pathlib import Path
import platform
from typing import Any

from app import __version__
from app.memory.models import utc_now

OS_RELEASE = Path("/etc/os-release")


def collect_host_context(
    schema_version: int | None,
    node: Callable[[], str] = platform.node,
    release: Callable[[], str] = platform.release,
    machine: Callable[[], str] = platform.machine,
    python_version: Callable[[], str] = platform.python_version,
    os_release: Path = OS_RELEASE,
    clock: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Return hostname, OS, kernel, architecture, Python, JULI and schema versions."""
    return {
        "hostname": _safe(node),
        "os": _pretty_name(os_release),
        "kernel": _safe(release),
        "architecture": _safe(machine),
        "python": _safe(python_version),
        "juli_version": __version__,
        "schema_version": schema_version,
        "collected_at": clock(),
    }


def _safe(read: Callable[[], str]) -> str | None:
    try:
        value = read()
    except Exception:  # platform probes can fail in restricted environments
        return None
    return value.strip() or None if isinstance(value, str) else None


def _pretty_name(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == "PRETTY_NAME":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value.strip() or None
    return None
