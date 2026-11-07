"""The conftest guard keeps every test away from the real data/juli.db."""

import hashlib
import sqlite3

import pytest

from conftest import REAL_DB_PATH

from app.config import PROJECT_ROOT
from app.core import database
from app.memory import MemoryRepository


def fingerprint():
    if not REAL_DB_PATH.exists():
        return None
    return hashlib.sha256(REAL_DB_PATH.read_bytes()).hexdigest(), REAL_DB_PATH.stat().st_mtime_ns


def test_default_database_is_temporary(_temporary_database_only):
    path = database.get_db_path()
    assert path == _temporary_database_only
    assert path.resolve() != REAL_DB_PATH
    assert PROJECT_ROOT not in path.resolve().parents


def test_unset_path_would_fall_back_to_the_real_database_and_fails(monkeypatch):
    before = fingerprint()
    monkeypatch.delenv("JULI_DB_PATH")  # the code default is data/juli.db

    with pytest.raises(pytest.fail.Exception, match="real database"):
        database.get_connection()
    with pytest.raises(pytest.fail.Exception, match="real database"):
        database.ensure_schema()

    assert fingerprint() == before


@pytest.mark.parametrize("spelling", [
    str(REAL_DB_PATH),
    str(PROJECT_ROOT / "data" / ".." / "data" / "juli.db"),
    "data/juli.db",
])
def test_pointing_at_the_real_database_fails(monkeypatch, spelling):
    before = fingerprint()
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setenv("JULI_DB_PATH", spelling)

    with pytest.raises(pytest.fail.Exception, match="real database"):
        MemoryRepository().list_incidents()

    assert fingerprint() == before


def test_monkeypatch_undo_in_a_test_does_not_remove_the_guard(monkeypatch, tmp_path):
    """The scenario from M1: undo() reset JULI_DB_PATH and a read hit data/juli.db."""
    monkeypatch.setenv("JULI_DB_PATH", str(tmp_path / "own.db"))
    monkeypatch.setattr(database, "MIGRATIONS", list(database.MIGRATIONS))
    monkeypatch.undo()

    assert database.get_db_path().resolve() != REAL_DB_PATH  # back to the guard's temp file
    monkeypatch.delenv("JULI_DB_PATH")
    with pytest.raises(pytest.fail.Exception, match="real database"):
        database.get_connection()


def test_application_code_cannot_swallow_the_failure(monkeypatch):
    """``except Exception`` (as in AgentCore) must not hide a real-database access."""
    monkeypatch.delenv("JULI_DB_PATH")

    def careless():
        try:
            database.get_connection()
        except Exception:  # noqa: BLE001 - deliberately broad, like application code
            return "swallowed"
        return "opened"

    with pytest.raises(pytest.fail.Exception):
        careless()


def test_reading_the_real_file_directly_is_still_possible(_temporary_database_only):
    """Only JULI's database path is guarded; a test may still read a copy source read-only."""
    if not REAL_DB_PATH.exists():
        pytest.skip("data/juli.db is not present on this machine")
    connection = sqlite3.connect(f"file:{REAL_DB_PATH}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] >= 0
    finally:
        connection.close()
