"""Shared strict validation for persisted monitoring measurements."""

from datetime import datetime, timezone
import math


def parse_snapshot_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp with an explicit UTC offset."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None


def parse_usage_percent(value: object) -> float | None:
    """Return a finite 0–100 number, excluding booleans and coercions."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0 <= number <= 100:
        return None
    return number
