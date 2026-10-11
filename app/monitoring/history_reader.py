
from app.core.database import get_connection


def load_recent_snapshots(
    limit: int = 1000,
) -> list[dict[str, object]]:
    """Load recent system snapshots from SQLite in chronological order."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT timestamp, cpu_usage, memory_usage, disk_usage
            FROM system_snapshots
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        connection.close()

    rows.reverse()

    return [
        {
            "timestamp": row[0],
            "cpu_usage": row[1],
            "memory_usage": row[2],
            "disk_usage": row[3],
        }
        for row in rows
    ]
