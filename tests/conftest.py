from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REAL_DB_PATH = (Path(__file__).resolve().parent.parent / "data" / "juli.db").resolve()


class RealDatabaseAccess(AssertionError):
    """Raised when a test is about to open the real project database."""


@pytest.fixture(autouse=True)
def _temporary_database_only(tmp_path_factory):
    """Every test works with a temporary database; touching data/juli.db fails it.

    The guard uses its own MonkeyPatch, so a test's ``monkeypatch.undo()``
    cannot remove it (that is how a test once fell back to the real database).
    ``JULI_DB_PATH`` defaults to a fresh temporary file; tests may point it at
    their own temporary path. Every database connection and backup goes
    through ``app.core.database.get_db_path``, which now refuses the real file.
    """
    from app.core import database

    guard = pytest.MonkeyPatch()
    default = tmp_path_factory.mktemp("juli-db") / "juli.db"
    guard.setenv("JULI_DB_PATH", str(default))
    original = database.get_db_path

    def guarded_get_db_path():
        path = original()
        if Path(path).resolve() == REAL_DB_PATH:
            # pytest.fail raises a BaseException, so no `except Exception` in
            # application code can swallow it; the database is never opened.
            pytest.fail(
                f"test tried to use the real database {REAL_DB_PATH}; "
                "tests must use a temporary JULI_DB_PATH",
                pytrace=True,
            )
        return path

    guard.setattr(database, "get_db_path", guarded_get_db_path)
    try:
        yield default
    finally:
        guard.undo()


class StepClock:
    """A clock that moves one second forward on every call."""

    def __init__(self, start=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)):
        self.moment = start

    def __call__(self) -> str:
        self.moment += timedelta(seconds=1)
        return self.moment.isoformat(timespec="microseconds")


@pytest.fixture
def memory_db(tmp_path, monkeypatch):
    """A temporary database migrated to the current schema."""
    from app.core.database import ensure_schema

    path = tmp_path / "juli.db"
    monkeypatch.setenv("JULI_DB_PATH", str(path))
    ensure_schema()
    return path


@pytest.fixture
def repo(memory_db):
    from app.memory import MemoryRepository

    return MemoryRepository(clock=StepClock())


SUCCESS = {"tool": "get_disk_usage", "status": "success", "data": {"percent": 89.3}, "error": None}


def disk_check(repo, incident_id, hypothesis_id=None, data=None):
    """Run the full read-check chain and return (action, tool_result, evidence)."""
    action = repo.plan_action(incident_id, "read_check", "get_disk_usage", actor="rule",
                              hypothesis_id=hypothesis_id)
    repo.change_action_status(action.id, "running", actor="rule")
    result = repo.record_tool_result({**SUCCESS, **({"data": data} if data else {})}, actor="rule")
    action = repo.change_action_status(action.id, "succeeded", actor="rule",
                                       tool_result_id=result.id)
    evidence = repo.add_evidence(incident_id, "Disk usage is 89.3%", "tool_result", "fresh",
                                 actor="rule", source_ref=result.id)
    return action, result, evidence


def investigating_incident(repo):
    incident = repo.open_incident("Disk almost full", "Диск майже заповнений", actor="user")
    return repo.change_incident_status(incident.id, "investigating", actor="user")


def hypothesis_in_testing(repo, incident_id, actor="llm", **kwargs):
    hypothesis = repo.propose_hypothesis(incident_id, "Journal logs fill the disk",
                                         actor=actor, **kwargs)
    return repo.change_hypothesis_status(hypothesis.id, "testing", actor=actor)
