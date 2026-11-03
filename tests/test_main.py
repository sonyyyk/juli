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
    assert "Monitoring status: partial" in out
    assert "Data freshness: no_data" in out
    assert "Recent: 0 threshold anomalies, 0 persistent series" in out
    assert "Historical: 0 threshold anomalies, 0 persistent series" in out
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


def test_analysis_summary_shows_freshness_and_periods():
    result = {
        "status": "partial",
        "snapshots_analyzed": 1000,
        "threshold_anomalies_found": 4,
        "persistent_anomalies_found": 1,
        "freshness": {
            "state": "stale",
            "latest_snapshot_timestamp": "2026-10-31T14:23:12+00:00",
            "latest_snapshot_age_seconds": 259200.4,
        },
        "recent": {"threshold_anomalies_found": 0, "persistent_anomalies_found": 0},
        "historical": {
            "threshold_anomalies_found": 4,
            "persistent_anomalies_found": 1,
            "analyzed_from": "2026-10-31T11:00:00+00:00",
            "analyzed_to": "2026-10-31T14:23:12+00:00",
        },
    }

    text = main.format_analysis_summary(result)

    assert "Data freshness: stale (latest 2026-10-31T14:23:12+00:00, age 259200 s)" in text
    assert "Recent: 0 threshold anomalies, 0 persistent series" in text
    assert "Historical: 4 threshold anomalies, 1 persistent series" in text
    assert "Analyzed period: 2026-10-31T11:00:00+00:00 .. 2026-10-31T14:23:12+00:00" in text


PSI_UNAVAILABLE = {
    "tool": "get_system_pressure",
    "status": "error",
    "data": None,
    "error": {"code": "PSI_UNAVAILABLE", "message": "unavailable in test"},
}


def test_analyze_context_prints_llm_context_without_calling_ollama(db_path, capsys):
    with patch(
        "app.monitoring.analysis_service.get_system_pressure", return_value=PSI_UNAVAILABLE
    ), patch("app.llm.ollama.urlopen") as urlopen:
        assert main.main(["analyze", "--context"]) == 0

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].startswith("Monitoring context")
    assert lines[1].startswith("Data freshness: no_data")
    assert "PSI (Pressure Stall Information): unavailable." in captured.out
    assert "Monitoring status:" in captured.out
    assert "Snapshots analyzed: 0" not in captured.out  # summary is not printed
    assert "characters)" in captured.err
    urlopen.assert_not_called()


def test_analyze_context_uses_agent_fallback_for_invalid_limit(db_path, monkeypatch, capsys, caplog):
    monkeypatch.setenv("JULI_MONITORING_CONTEXT_MAX_CHARS", "abc")
    with patch(
        "app.monitoring.analysis_service.get_system_pressure", return_value=PSI_UNAVAILABLE
    ):
        assert main.main(["analyze", "--context"]) == 0
    assert capsys.readouterr().out.startswith("Monitoring context")
    assert "using 4000" in caplog.text


class FakeChatProvider:
    instances = []

    def __init__(self, config):
        from app.llm.interface import LLMError, LLMResult, LLMUsage

        self.config = config
        self.calls = []
        self.results = [
            LLMResult(
                status="success",
                content="Стан невідомий.",
                model=config.model,
                usage=LLMUsage(prompt_eval_count=876, eval_count=50, total_duration_ns=2_500_000_000),
            ),
            LLMResult(
                status="error",
                content=None,
                model=config.model,
                error=LLMError("OLLAMA_TIMEOUT", "Ollama did not respond."),
            ),
        ]
        FakeChatProvider.instances.append(self)

    def generate(self, messages):
        self.calls.append(messages)
        return self.results[len(self.calls) - 1]


def run_chat(inputs, monkeypatch):
    FakeChatProvider.instances.clear()
    answers = iter(inputs)

    def fake_input(prompt=""):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    with patch.object(main, "OllamaProvider", FakeChatProvider), patch.object(
        main, "run_monitoring_cycle", return_value={"status": "success"}
    ):
        return main.main(["chat"])


def test_chat_answers_shows_context_clears_and_exits(db_path, monkeypatch, capsys):
    code = run_chat(
        ["/context", "Що зараз відбувається?", "/context", "  ", "/clear", "/help", "/exit", "never sent"],
        monkeypatch,
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "model qwen3.5:4b, thinking off, num_ctx 16384" in out
    assert "No context has been sent to the model yet." in out
    assert "juli> Стан невідомий." in out
    assert "[model qwen3.5:4b, prompt 876 tokens, output 50 tokens, 2.5 s]" in out
    assert "[system]\nYou are JULI" in out
    assert "[system]\nMonitoring context" in out
    assert "[user]\nЩо зараз відбувається?" in out
    assert "Conversation cleared." in out
    assert "Unknown command." in out
    provider = FakeChatProvider.instances[0]
    assert len(provider.calls) == 1


def test_chat_reports_llm_error_without_traceback_and_ends_on_eof(db_path, monkeypatch, capsys):
    code = run_chat(["first", "second"], monkeypatch)

    out = capsys.readouterr().out
    assert code == 0
    assert "error: OLLAMA_TIMEOUT: Ollama did not respond." in out
    assert "Traceback" not in out
    assert "[model qwen3.5:4b, prompt unknown tokens, output unknown tokens, time unknown]" in out


def test_chat_rejects_invalid_ollama_environment(db_path, monkeypatch, capsys):
    monkeypatch.setenv("JULI_OLLAMA_THINK", "maybe")
    assert main.main(["chat"]) == 1
    assert "JULI_OLLAMA_THINK" in capsys.readouterr().err


def test_format_usage_mentions_thinking_length_only():
    line = main.format_usage("m", {"thinking_chars": 6644, "total_duration_ns": None})
    assert "thinking 6644 chars (not shown)" in line
