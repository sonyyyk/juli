import sqlite3
from typing import Callable

from app.config import get_db_path


def get_connection() -> sqlite3.Connection:
    """Return a connection to the JULI SQLite database."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _migration_1_system_snapshots(connection: sqlite3.Connection) -> None:
    """Create the original snapshot table; a no-op on databases that have it."""
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


# Ordered (version, migration) pairs. The schema version is stored in
# ``PRAGMA user_version``. A migration must use ``connection.execute`` only,
# because ensure_schema() runs it inside a transaction.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_1_system_snapshots),
]


def ensure_schema() -> int:
    """Bring the database schema up to date and return its version.

    Safe to call repeatedly: when the schema is current nothing is written.
    Each run applies the pending migrations in one transaction together with
    the ``user_version`` update, so a failure leaves the database unchanged.
    """
    connection = get_connection()
    connection.isolation_level = None  # explicit transaction control below
    try:
        latest = MIGRATIONS[-1][0]
        current = _check_version(_read_version(connection), latest)
        if current == latest:
            return current

        connection.execute("BEGIN IMMEDIATE")
        try:
            # Re-read under the write lock: another process may have migrated.
            current = _check_version(_read_version(connection), latest)
            for version, migrate in MIGRATIONS:
                if version > current:
                    migrate(connection)
                    connection.execute(f"PRAGMA user_version = {int(version)}")
                    current = version
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return current
    finally:
        connection.close()


def initialize_database() -> int:
    """Compatibility alias for ensure_schema()."""
    return ensure_schema()


def get_schema_version() -> int:
    """Return the current ``user_version`` without changing the database."""
    connection = get_connection()
    try:
        return _read_version(connection)
    finally:
        connection.close()


def _read_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _check_version(current: int, latest: int) -> int:
    if current > latest:
        raise RuntimeError(
            f"Database schema version {current} is newer than the version "
            f"{latest} supported by this JULI build; refusing to modify it."
        )
    return current
