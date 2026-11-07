import sqlite3

import pytest

from app.core import database


LATEST = database.MIGRATIONS[-1][0]

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "juli.db"
    monkeypatch.setenv("JULI_DB_PATH", str(path))
    return path


LEGACY_SCHEMA = """
    CREATE TABLE system_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        cpu_usage REAL NOT NULL,
        memory_usage REAL NOT NULL,
        disk_usage REAL NOT NULL
    )
"""


def raw(path, sql, params=()):
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(sql, params).fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()


def test_ensure_schema_creates_table_and_version_on_fresh_database(db_path):
    assert not db_path.parent.exists()

    assert database.ensure_schema() == LATEST

    assert db_path.is_file()
    assert raw(db_path, "PRAGMA user_version") == [(LATEST,)]
    tables = raw(db_path, "SELECT name FROM sqlite_master WHERE type='table'")
    assert ("system_snapshots",) in tables


def test_ensure_schema_twice_changes_nothing(db_path):
    database.ensure_schema()
    raw(
        db_path,
        "INSERT INTO system_snapshots (timestamp, cpu_usage, memory_usage, disk_usage)"
        " VALUES (?, ?, ?, ?)",
        ("2026-11-01T00:00:00+00:00", 1.0, 2.0, 3.0),
    )
    schema_before = raw(db_path, "SELECT sql FROM sqlite_master ORDER BY name")

    assert database.ensure_schema() == LATEST
    assert database.ensure_schema() == LATEST

    assert raw(db_path, "SELECT COUNT(*) FROM system_snapshots") == [(1,)]
    assert raw(db_path, "SELECT sql FROM sqlite_master ORDER BY name") == schema_before


def test_existing_unversioned_database_keeps_data_and_schema(db_path):
    db_path.parent.mkdir(parents=True)
    raw(db_path, LEGACY_SCHEMA)
    raw(
        db_path,
        "INSERT INTO system_snapshots (timestamp, cpu_usage, memory_usage, disk_usage)"
        " VALUES (?, ?, ?, ?)",
        ("2026-10-31T14:23:12+00:00", 10.5, 20.5, 30.5),
    )
    assert raw(db_path, "PRAGMA user_version") == [(0,)]
    snapshots_schema = "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = 'system_snapshots'"
    schema_before = raw(db_path, snapshots_schema)

    assert database.get_schema_version() == 0
    assert database.ensure_schema() == LATEST

    assert raw(db_path, "PRAGMA user_version") == [(LATEST,)]
    assert raw(
        db_path, "SELECT timestamp, cpu_usage, memory_usage, disk_usage FROM system_snapshots"
    ) == [("2026-10-31T14:23:12+00:00", 10.5, 20.5, 30.5)]
    assert raw(db_path, snapshots_schema) == schema_before


def test_newer_schema_version_is_refused_and_left_untouched(db_path):
    db_path.parent.mkdir(parents=True)
    raw(db_path, "PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="newer"):
        database.ensure_schema()

    assert raw(db_path, "PRAGMA user_version") == [(99,)]
    assert raw(db_path, "SELECT name FROM sqlite_master") == []


def test_failed_migration_rolls_back_schema_and_version(db_path, monkeypatch):
    def failing_migration(connection):
        connection.execute("CREATE TABLE half_done (id INTEGER)")
        raise sqlite3.OperationalError("injected failure")

    monkeypatch.setattr(
        database,
        "MIGRATIONS",
        [*database.MIGRATIONS, (LATEST + 1, failing_migration)],
    )

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        database.ensure_schema()

    assert raw(db_path, "PRAGMA user_version") == [(0,)]
    assert raw(db_path, "SELECT name FROM sqlite_master") == []


def test_later_migration_applies_on_top_of_current_version(db_path, monkeypatch):
    database.ensure_schema()
    monkeypatch.setattr(
        database,
        "MIGRATIONS",
        [
            *database.MIGRATIONS,
            (LATEST + 1, lambda connection: connection.execute("CREATE TABLE extra (id INTEGER)")),
        ],
    )

    assert database.ensure_schema() == LATEST + 1
    assert raw(db_path, "PRAGMA user_version") == [(LATEST + 1,)]
    assert ("extra",) in raw(db_path, "SELECT name FROM sqlite_master")


def test_initialize_database_is_a_compatible_alias(db_path):
    database.initialize_database()
    database.initialize_database()

    assert raw(db_path, "PRAGMA user_version") == [(LATEST,)]


def test_get_connection_uses_configured_path_and_creates_directory(db_path):
    connection = database.get_connection()
    connection.close()

    assert db_path.parent.is_dir()
