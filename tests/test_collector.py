from datetime import datetime, timedelta
import sqlite3
from unittest.mock import Mock, patch

import pytest

from app.core import collector, database
from app.tools.system import state as system_state


@pytest.fixture
def temporary_database(monkeypatch, tmp_path):
    database_path = tmp_path / "collector-test.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    database.initialize_database()
    return database_path


def valid_system_state():
    return {
        "tool": "get_system_state",
        "status": "success",
        "data": {
            "cpu": {"usage_percent": 37.25},
            "memory": {"usage_percent": 48.5},
            "disk": {"usage_percent": 61.75},
        },
        "error": None,
    }


def read_snapshots():
    connection = database.get_connection()
    try:
        return connection.execute(
            """
            SELECT timestamp, cpu_usage, memory_usage, disk_usage
            FROM system_snapshots
            ORDER BY id
            """
        ).fetchall()
    finally:
        connection.close()


def test_collect_system_snapshot_writes_complete_utc_snapshot(
    temporary_database, monkeypatch
):
    monkeypatch.setattr(
        collector, "get_system_state", valid_system_state
    )

    collector.collect_system_snapshot()

    rows = read_snapshots()
    assert len(rows) == 1
    timestamp, cpu_usage, memory_usage, disk_usage = rows[0]
    parsed_timestamp = datetime.fromisoformat(timestamp)

    assert parsed_timestamp.tzinfo is not None
    assert parsed_timestamp.utcoffset() == timedelta(0)
    assert cpu_usage == 37.25
    assert memory_usage == 48.5
    assert disk_usage == 61.75


def test_tool_error_does_not_write_snapshot(temporary_database, monkeypatch):
    monkeypatch.setattr(
        collector,
        "get_system_state",
        lambda: {
            "tool": "get_system_state",
            "status": "error",
            "data": None,
            "error": {"code": "SYSTEM_READ_ERROR", "message": "read failed"},
        },
    )

    with pytest.raises(RuntimeError, match="Failed to collect system state"):
        collector.collect_system_snapshot()

    assert read_snapshots() == []


@pytest.mark.parametrize(
    "state",
    [
        {
            "tool": "get_system_state",
            "status": "success",
            "data": {
                "cpu": {"usage_percent": 10.0},
                "memory": {"usage_percent": 20.0},
                # Missing disk resource.
            },
            "error": None,
        },
        {
            "tool": "get_system_state",
            "status": "success",
            "data": {
                "cpu": {"usage_percent": 10.0},
                "memory": {},  # Missing usage_percent.
                "disk": {"usage_percent": 30.0},
            },
            "error": None,
        },
    ],
)
def test_incomplete_aggregate_response_does_not_write_snapshot(
    temporary_database, monkeypatch, state
):
    monkeypatch.setattr(collector, "get_system_state", lambda: state)

    with pytest.raises(ValueError):
        collector.collect_system_snapshot()

    assert read_snapshots() == []


def test_child_tool_error_is_wrapped_as_success_with_incomplete_data(
    temporary_database, monkeypatch
):
    monkeypatch.setattr(
        system_state,
        "get_cpu_usage",
        lambda: {
            "tool": "get_cpu_usage",
            "status": "error",
            "data": {},
            "error": {"code": "CPU_READ_ERROR", "message": "read failed"},
        },
    )
    monkeypatch.setattr(
        system_state,
        "get_memory_usage",
        lambda: {
            "tool": "get_memory_usage",
            "status": "success",
            "data": {"usage_percent": 20.0},
            "error": None,
        },
    )
    monkeypatch.setattr(
        system_state,
        "get_disk_usage",
        lambda: {
            "tool": "get_disk_usage",
            "status": "success",
            "data": {"usage_percent": 30.0},
            "error": None,
        },
    )

    aggregate = system_state.get_system_state()
    assert aggregate["status"] == "success"
    assert aggregate["data"]["cpu"] == {}

    with pytest.raises(ValueError):
        collector.collect_system_snapshot()

    assert read_snapshots() == []


@pytest.mark.parametrize(
    ("invalid_value", "case_name"),
    [
        (None, "none"),
        ("not-a-number", "string"),
        (float("nan"), "nan"),
        (float("inf"), "positive-infinity"),
        (float("-inf"), "negative-infinity"),
        (True, "boolean"),
        (-1.0, "below-zero"),
        (101.0, "above-100"),
    ],
)
@pytest.mark.parametrize("metric", ["cpu", "memory", "disk"])
def test_invalid_metric_value_does_not_write_snapshot(
    temporary_database, monkeypatch, invalid_value, case_name, metric
):
    state = valid_system_state()
    state["data"][metric]["usage_percent"] = invalid_value
    monkeypatch.setattr(collector, "get_system_state", lambda: state)

    with pytest.raises(ValueError):
        collector.collect_system_snapshot()

    assert read_snapshots() == [], (
        f"Collector/SQLite stored a snapshot for invalid input {case_name!r}"
    )


@pytest.mark.parametrize("value", [0, 100])
@pytest.mark.parametrize("metric", ["cpu", "memory", "disk"])
def test_usage_percent_range_includes_zero_and_one_hundred(
    temporary_database, monkeypatch, value, metric
):
    state = valid_system_state()
    state["data"][metric]["usage_percent"] = value
    monkeypatch.setattr(collector, "get_system_state", lambda: state)

    collector.collect_system_snapshot()

    rows = read_snapshots()
    assert len(rows) == 1
    column_index = {"cpu": 1, "memory": 2, "disk": 3}[metric]
    assert rows[0][column_index] == float(value)


class FailingConnection:
    def __init__(self, connection, failure_stage):
        self.connection = connection
        self.failure_stage = failure_stage
        self.closed = False

    def execute(self, *args, **kwargs):
        if self.failure_stage == "execute":
            raise sqlite3.OperationalError("injected insert failure")
        return self.connection.execute(*args, **kwargs)

    def commit(self):
        if self.failure_stage == "commit":
            raise sqlite3.OperationalError("injected commit failure")
        return self.connection.commit()

    def close(self):
        self.closed = True
        self.connection.close()


@pytest.mark.parametrize("failure_stage", ["execute", "commit"])
def test_database_failure_closes_connection_and_writes_no_snapshot(
    temporary_database, monkeypatch, failure_stage
):
    connection = FailingConnection(database.get_connection(), failure_stage)
    monkeypatch.setattr(collector, "get_system_state", valid_system_state)
    monkeypatch.setattr(collector, "get_connection", lambda: connection)

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        collector.collect_system_snapshot()

    assert connection.closed
    assert read_snapshots() == []


@pytest.mark.parametrize("interval_seconds", [0, -1, 1.5, True])
def test_invalid_collector_interval_is_rejected_before_collection(interval_seconds):
    collect_snapshot = Mock()
    sleep = Mock(side_effect=KeyboardInterrupt)

    with (
        patch.object(collector, "collect_system_snapshot", collect_snapshot),
        patch.object(collector.time, "sleep", sleep),
    ):
        with pytest.raises(ValueError):
            collector.run_collector(interval_seconds)

    collect_snapshot.assert_not_called()
    sleep.assert_not_called()


def test_positive_integer_collector_interval_runs_until_controlled_stop():
    collect_snapshot = Mock()
    sleep = Mock(side_effect=KeyboardInterrupt)

    with (
        patch.object(collector, "collect_system_snapshot", collect_snapshot),
        patch.object(collector.time, "sleep", sleep),
    ):
        collector.run_collector(5)

    collect_snapshot.assert_called_once_with()
    sleep.assert_called_once_with(5)
