import time
from datetime import datetime, timezone
import math

from app.tools.system.state import get_system_state
from app.core.database import get_connection


def collect_system_snapshot() -> None:
    """Collect the current system state and store it in SQLite."""
    system_state = get_system_state()

    if not isinstance(system_state, dict):
        raise ValueError("System state response must be a mapping.")
    if "status" not in system_state:
        raise ValueError("System state response is missing status.")
    if system_state["status"] != "success":
        raise RuntimeError("Failed to collect system state")

    data = system_state.get("data")
    if not isinstance(data, dict):
        raise ValueError("Successful system state response must contain a data mapping.")

    values = {
        resource: _validated_usage_percent(data, resource)
        for resource in ("cpu", "memory", "disk")
    }
    timestamp = datetime.now(timezone.utc).isoformat()

    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO system_snapshots (
                timestamp,
                cpu_usage,
                memory_usage,
                disk_usage
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                timestamp,
                values["cpu"],
                values["memory"],
                values["disk"],
            ),
        )
        connection.commit()
    finally:
        connection.close()

    print(
        f"[{timestamp}] "
        f"CPU: {values['cpu']}% | "
        f"RAM: {values['memory']}% | "
        f"Disk: {values['disk']}%"
    )


def _validated_usage_percent(data: dict, resource: str) -> float:
    resource_data = data.get(resource)
    if not isinstance(resource_data, dict):
        raise ValueError(
            f"System state is missing valid {resource} data."
        )
    if "usage_percent" not in resource_data:
        raise ValueError(
            f"System state is missing {resource} usage_percent."
        )

    value = resource_data["usage_percent"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{resource} usage_percent must be an int or float, not bool."
        )
    try:
        is_finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        is_finite = False
    if not is_finite:
        raise ValueError(f"{resource} usage_percent must be finite.")
    if not 0 <= value <= 100:
        raise ValueError(
            f"{resource} usage_percent must be between 0 and 100 inclusive."
        )
    return float(value)


def run_collector(interval_seconds: int = 10) -> None:
    """Continuously collect system snapshots."""
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or interval_seconds <= 0
    ):
        raise ValueError("interval_seconds must be a positive integer.")

    print(f"JULI Collector started. Interval: {interval_seconds}s")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            collect_system_snapshot()
            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        print("\nJULI Collector stopped.")
