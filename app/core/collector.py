from datetime import datetime, timezone

from app.tools.system.state import get_system_state
from app.core.database import get_connection


def collect_system_snapshot() -> None:
    """Collect the current system state and store it in SQLite."""
    system_state = get_system_state()

    if system_state["status"] != "success":
        raise RuntimeError("Failed to collect system state")

    data = system_state["data"]

    timestamp = datetime.now(timezone.utc).isoformat()

    connection = get_connection()

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
            data["cpu"]["usage_percent"],
            data["memory"]["usage_percent"],
            data["disk"]["usage_percent"],
        ),
    )

    connection.commit()
    connection.close()

    print(f"Snapshot collected at {timestamp}")
