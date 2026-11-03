from pathlib import Path

import pytest

from app import config


ENV_NAMES = [
    "JULI_DB_PATH",
    "JULI_COLLECTOR_INTERVAL_SECONDS",
    "JULI_RECENT_WINDOW_MINUTES",
    "JULI_MONITORING_CONTEXT_MAX_CHARS",
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    assert config.get_db_path() == config.PROJECT_ROOT / "data" / "juli.db"
    assert config.get_collector_interval_seconds() == 10
    assert config.get_recent_window_minutes() == 15
    assert config.get_monitoring_context_max_chars() == 4000


def test_project_root_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert config.PROJECT_ROOT == Path(config.__file__).resolve().parent.parent
    assert (config.PROJECT_ROOT / "app" / "config.py").is_file()
    assert config.get_db_path() == config.PROJECT_ROOT / "data" / "juli.db"


def test_environment_overrides_are_read_at_call_time(tmp_path, monkeypatch):
    db_file = tmp_path / "custom.db"
    monkeypatch.setenv("JULI_DB_PATH", str(db_file))
    monkeypatch.setenv("JULI_COLLECTOR_INTERVAL_SECONDS", " 3 ")
    monkeypatch.setenv("JULI_RECENT_WINDOW_MINUTES", "2.5")
    monkeypatch.setenv("JULI_MONITORING_CONTEXT_MAX_CHARS", "1200")

    assert config.get_db_path() == db_file.resolve()
    assert config.get_collector_interval_seconds() == 3
    assert config.get_recent_window_minutes() == 2.5
    assert config.get_monitoring_context_max_chars() == 1200


def test_relative_db_path_is_resolved_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JULI_DB_PATH", "rel/juli.db")

    assert config.get_db_path() == (tmp_path / "rel" / "juli.db").resolve()


def test_no_module_level_setting_constants():
    for name in ("DB_PATH", "COLLECTOR_INTERVAL_SECONDS", "RECENT_WINDOW_MINUTES"):
        assert not hasattr(config, name)


@pytest.mark.parametrize("value", ["", "   ", "abc", "0", "-3", "1.5", "true", "nan", "inf"])
@pytest.mark.parametrize(
    ("variable", "getter"),
    [
        ("JULI_COLLECTOR_INTERVAL_SECONDS", config.get_collector_interval_seconds),
        ("JULI_MONITORING_CONTEXT_MAX_CHARS", config.get_monitoring_context_max_chars),
    ],
)
def test_invalid_integer_settings_name_the_variable(monkeypatch, variable, getter, value):
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=variable):
        getter()


@pytest.mark.parametrize("value", ["", "abc", "0", "-1", "nan", "inf", "-inf", "true"])
def test_invalid_recent_window_names_the_variable(monkeypatch, value):
    monkeypatch.setenv("JULI_RECENT_WINDOW_MINUTES", value)

    with pytest.raises(ValueError, match="JULI_RECENT_WINDOW_MINUTES"):
        config.get_recent_window_minutes()


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_db_path_is_rejected(monkeypatch, value):
    monkeypatch.setenv("JULI_DB_PATH", value)

    with pytest.raises(ValueError, match="JULI_DB_PATH"):
        config.get_db_path()
