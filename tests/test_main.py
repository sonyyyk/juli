from unittest.mock import patch

import pytest

import main
from app.core import database


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "juli.db"
    monkeypatch.setenv("JULI_DB_PATH", str(path))
    monkeypatch.delenv("JULI_COLLECTOR_INTERVAL_SECONDS", raising=False)
    return path


def test_init_db_creates_schema_and_prints_version(db_path, capsys):
    assert main.main(["init-db"]) == 0

    out = capsys.readouterr().out
    assert str(db_path) in out
    assert "Schema version: 0 -> 1" in out
    assert database.get_schema_version() == 1

    assert main.main(["init-db"]) == 0
    assert "Schema version: 1 -> 1" in capsys.readouterr().out


def test_collect_passes_interval_to_collector(db_path):
    with patch.object(main, "run_collector") as run_collector:
        assert main.main(["collect", "--interval", "3"]) == 0

    run_collector.assert_called_once_with(3)
    assert database.get_schema_version() == 1


def test_collect_uses_configured_default_interval(db_path, monkeypatch):
    monkeypatch.setenv("JULI_COLLECTOR_INTERVAL_SECONDS", "8")

    with patch.object(main, "run_collector") as run_collector:
        assert main.main(["collect"]) == 0

    run_collector.assert_called_once_with(8)


@pytest.mark.parametrize("value", ["0", "-2", "abc", "1.5"])
def test_collect_rejects_invalid_interval(db_path, value, capsys):
    with patch.object(main, "run_collector") as run_collector:
        with pytest.raises(SystemExit) as exit_info:
            main.main(["collect", "--interval", value])

    assert exit_info.value.code == 2
    run_collector.assert_not_called()


def test_analyze_prints_summary_on_empty_database(db_path, capsys):
    psi_unavailable = {
        "tool": "get_system_pressure",
        "status": "error",
        "data": None,
        "error": {"code": "PSI_UNAVAILABLE", "message": "unavailable in test"},
    }

    with patch(
        "app.monitoring.analysis_service.get_system_pressure",
        return_value=psi_unavailable,
    ):
        assert main.main(["analyze"]) == 0

    out = capsys.readouterr().out
    assert "Monitoring status:" in out
    assert "Snapshots analyzed: 0" in out
    assert "Threshold anomalies: 0" in out
    assert "PSI issues:" in out


def test_no_subcommand_prints_help_and_returns_2(capsys):
    assert main.main([]) == 2

    assert "init-db" in capsys.readouterr().err


def test_schema_error_returns_1_without_traceback(db_path, capsys):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 99")
    connection.close()

    assert main.main(["init-db"]) == 1

    assert "newer" in capsys.readouterr().err


def test_invalid_environment_returns_1(db_path, monkeypatch, capsys):
    monkeypatch.setenv("JULI_COLLECTOR_INTERVAL_SECONDS", "zero")

    with patch.object(main, "run_collector") as run_collector:
        assert main.main(["collect"]) == 1

    run_collector.assert_not_called()
    assert "JULI_COLLECTOR_INTERVAL_SECONDS" in capsys.readouterr().err
