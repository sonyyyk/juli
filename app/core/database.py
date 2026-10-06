import sqlite3
from pathlib import Path


DATABASE_PATH = Path("data/juli.db")


def get_connection() -> sqlite3.Connection:
    """Return a connection to the JULI SQLite database."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DATABASE_PATH)


def initialize_database() -> None:
    """Create the initial JULI database schema."""
    connection = get_connection()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cpu_usage REAL NOT NULL,
            memory_usage REAL NOT NULL,
            disk_usage REAL NOT NULL
        )
        """
    )

    connection.commit()
    connection.close()
