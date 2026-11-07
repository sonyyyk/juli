from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.monitoring.analysis_service import (
    analyze_system_history,
    run_monitoring_cycle,
)


NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
OLD_KEYS = {
    "status", "snapshots_analyzed", "threshold_anomalies", "threshold_anomalies_found",
    "persistent_anomalies", "persistent_anomalies_found", "data_issues",
    "temporal_errors", "pressure_observations", "pressure_issues",
    "significance_assessments",
}
PSI_ERROR = {
    "tool": "get_system_pressure",
    "status": "error",
    "data": None,
    "error": {"code": "PSI_UNAVAILABLE", "message": "no PSI in test"},
}


@pytest.fixture(autouse=True)
def default_environment(monkeypatch):
    monkeypatch.delenv("JULI_RECENT_WINDOW_MINUTES", raising=False)
    monkeypatch.delenv("JULI_COLLECTOR_INTERVAL_SECONDS", raising=False)


def iso(moment):
    return moment.isoformat()


def snap(moment, cpu=20.0, memory=40.0, disk=70.0):
    return {
        "timestamp": iso(moment),
        "cpu_usage": cpu,
        "memory_usage": memory,
        "disk_usage": disk,
    }


def run(snapshots, function=run_monitoring_cycle, **kwargs):
    kwargs.setdefault("now", NOW)
    with (
        patch(
            "app.monitoring.analysis_service.load_recent_snapshots",
            return_value=snapshots,
        ),
        patch(
            "app.monitoring.analysis_service.get_system_pressure",
            return_value=PSI_ERROR,
        ),
    ):
        return function(**kwargs)


def series_of(end, values):
    """Snapshots 6 s apart ending at `end` with the given CPU values."""
    count = len(values)
    return [
        snap(end - timedelta(seconds=6 * (count - 1 - index)), cpu=value)
        for index, value in enumerate(values)
    ]


# --- freshness --------------------------------------------------------------


def test_fresh_data():
    latest = NOW - timedelta(seconds=8)
    result = run([snap(latest - timedelta(seconds=6), cpu=10.0), snap(latest, cpu=33.5, memory=44.0, disk=55.0)])

    assert result["freshness"] == {
        "latest_snapshot_timestamp": iso(latest),
        "latest_snapshot_age_seconds": 8.0,
        "stale_after_seconds": 30,
        "state": "fresh",
    }
    assert result["status"] == "success"
    assert result["current"] == {
        "timestamp": iso(latest),
        "cpu_usage": 33.5,
        "memory_usage": 44.0,
        "disk_usage": 55.0,
    }
    assert not any(i["issue"] in {"stale_data", "no_data"} for i in result["data_issues"])


@pytest.mark.parametrize(
    ("age_seconds", "state"),
    [(30.0, "fresh"), (30.001, "stale")],
)
def test_freshness_boundary_is_inclusive(age_seconds, state):
    result = run([snap(NOW - timedelta(seconds=age_seconds))])

    assert result["freshness"]["state"] == state


def test_stale_data_is_partial_and_keeps_current_with_timestamp():
    latest = NOW - timedelta(days=3)
    result = run([snap(latest, cpu=12.0)])

    assert result["freshness"]["state"] == "stale"
    assert result["freshness"]["latest_snapshot_age_seconds"] == 3 * 86400.0
    assert result["status"] == "partial"
    issue = next(i for i in result["data_issues"] if i["issue"] == "stale_data")
    assert issue["latest_snapshot_timestamp"] == iso(latest)
    assert result["current"]["timestamp"] == iso(latest)
    assert result["current"]["cpu_usage"] == 12.0


def test_no_data_on_empty_history():
    result = run([])

    assert result["freshness"] == {
        "latest_snapshot_timestamp": None,
        "latest_snapshot_age_seconds": None,
        "stale_after_seconds": 30,
        "state": "no_data",
    }
    assert result["current"] is None
    assert result["status"] == "partial"
    assert any(i["issue"] == "no_data" for i in result["data_issues"])
    assert result["historical"]["analyzed_from"] is None
    assert result["historical"]["analyzed_to"] is None


def test_snapshots_without_valid_timestamps_are_no_data_not_an_error():
    broken = [
        {"timestamp": "not-a-time", "cpu_usage": 1.0, "memory_usage": 1.0, "disk_usage": 1.0},
        {"timestamp": None, "cpu_usage": 1.0, "memory_usage": 1.0, "disk_usage": 1.0},
    ]

    result = run(broken)

    assert result["freshness"]["state"] == "no_data"
    assert result["current"] is None
    assert result["historical"]["analyzed_from"] is None


def test_snapshot_from_the_future_is_stale_with_issue():
    result = run([snap(NOW + timedelta(minutes=5))])

    assert result["freshness"]["state"] == "stale"
    assert result["freshness"]["latest_snapshot_age_seconds"] == -300.0
    assert result["status"] == "partial"
    assert any(i["issue"] == "snapshot_in_future" for i in result["data_issues"])


def test_latest_snapshot_uses_collection_order_not_sorting():
    first = snap(NOW - timedelta(seconds=5), cpu=11.0)
    last_in_order = snap(NOW - timedelta(seconds=50), cpu=22.0)

    result = run([first, last_in_order])

    assert result["freshness"]["latest_snapshot_timestamp"] == last_in_order["timestamp"]
    assert result["current"]["cpu_usage"] == 22.0


def test_invalid_current_values_become_none_not_zero():
    latest = NOW - timedelta(seconds=2)
    result = run([snap(latest, cpu=None, memory=150.0, disk=True)])

    assert result["current"] == {
        "timestamp": iso(latest),
        "cpu_usage": None,
        "memory_usage": None,
        "disk_usage": None,
    }


def test_stale_limit_follows_collector_interval(monkeypatch):
    monkeypatch.setenv("JULI_COLLECTOR_INTERVAL_SECONDS", "20")
    result = run([snap(NOW - timedelta(seconds=45))])

    assert result["freshness"]["stale_after_seconds"] == 60
    assert result["freshness"]["state"] == "fresh"


# --- recent / historical ----------------------------------------------------


def test_anomaly_inside_window_is_recent_and_outside_is_only_historical():
    old_end = NOW - timedelta(hours=2)
    new_end = NOW - timedelta(seconds=10)
    snapshots = series_of(old_end, [95.0]) + series_of(new_end, [20.0, 96.0])

    result = run(snapshots)

    recent = result["recent"]
    assert recent["window_minutes"] == 15.0
    assert recent["window_start"] == iso(NOW - timedelta(minutes=15))
    assert [a["timestamp"] for a in recent["threshold_anomalies"]] == [iso(new_end)]
    assert recent["threshold_anomalies_found"] == 1
    assert [p["anomaly"]["timestamp"] for p in recent["significance_assessments"]] == [iso(new_end)]

    historical = result["historical"]
    assert historical["threshold_anomalies_found"] == 1
    assert historical["last_anomaly_timestamp"] == iso(old_end)
    assert historical["metrics"] == ["cpu_usage"]
    # The full lists on the top level are untouched.
    assert result["threshold_anomalies_found"] == 2
    assert len(result["significance_assessments"]) == 2


def test_only_historical_anomalies_leave_recent_empty():
    snapshots = series_of(NOW - timedelta(days=3), [95.0, 96.0, 97.0])

    result = run(snapshots)

    assert result["recent"]["threshold_anomalies"] == []
    assert result["recent"]["persistent_anomalies"] == []
    assert result["recent"]["significance_assessments"] == []
    assert result["historical"]["threshold_anomalies_found"] == 3
    assert result["historical"]["persistent_anomalies_found"] == 1
    assert result["freshness"]["state"] == "stale"


def test_series_overlapping_the_window_boundary_is_recent():
    window_start = NOW - timedelta(minutes=15)
    # Run of 3 samples: the first is before the window, the last inside it.
    snapshots = [
        snap(window_start - timedelta(seconds=6), cpu=95.0),
        snap(window_start, cpu=95.0),
        snap(window_start + timedelta(seconds=6), cpu=95.0),
    ]

    result = run(snapshots)

    assert result["persistent_anomalies_found"] == 1
    assert result["recent"]["persistent_anomalies_found"] == 1
    assert result["historical"]["persistent_anomalies_found"] == 0
    # Per-anomaly placement: the point before the window is historical.
    assert result["recent"]["threshold_anomalies_found"] == 2
    assert result["historical"]["threshold_anomalies_found"] == 1


def test_window_is_configurable_by_argument_and_environment(monkeypatch):
    snapshots = series_of(NOW - timedelta(minutes=10), [95.0])

    assert run(snapshots)["recent"]["threshold_anomalies_found"] == 1
    narrow = run(snapshots, recent_window_minutes=5)
    assert narrow["recent"]["window_minutes"] == 5.0
    assert narrow["recent"]["threshold_anomalies_found"] == 0
    assert narrow["historical"]["threshold_anomalies_found"] == 1

    monkeypatch.setenv("JULI_RECENT_WINDOW_MINUTES", "5")
    assert run(snapshots)["recent"]["threshold_anomalies_found"] == 0


def test_anomaly_with_future_timestamp_stays_visible_in_recent():
    result = run(series_of(NOW + timedelta(minutes=3), [95.0]))

    assert result["recent"]["threshold_anomalies_found"] == 1


# --- analyzed period --------------------------------------------------------


def test_historical_reports_analyzed_period():
    oldest = NOW - timedelta(days=3)
    newest = NOW - timedelta(seconds=4)
    snapshots = [snap(oldest), snap(oldest + timedelta(seconds=6)), snap(newest)]

    result = run(snapshots)

    assert result["historical"]["analyzed_from"] == iso(oldest)
    assert result["historical"]["analyzed_to"] == iso(newest)


def test_analyzed_period_ignores_invalid_timestamps_and_uses_min_max():
    early = NOW - timedelta(hours=1)
    late = NOW - timedelta(minutes=2)
    snapshots = [
        snap(late),
        {"timestamp": "garbage", "cpu_usage": 1.0, "memory_usage": 1.0, "disk_usage": 1.0},
        snap(early),
    ]

    result = run(snapshots)

    assert result["historical"]["analyzed_from"] == iso(early)
    assert result["historical"]["analyzed_to"] == iso(late)


def test_single_snapshot_period_has_equal_bounds():
    only = NOW - timedelta(seconds=3)

    result = run([snap(only)])

    assert result["historical"]["analyzed_from"] == iso(only)
    assert result["historical"]["analyzed_to"] == iso(only)


# --- compatibility and invariants -------------------------------------------


def test_old_keys_are_still_present_with_new_ones_added():
    result = run(series_of(NOW - timedelta(seconds=5), [20.0]))

    assert OLD_KEYS <= set(result)
    assert {"freshness", "current", "recent", "historical"} <= set(result)


def test_significance_profiles_do_not_depend_on_freshness():
    snapshots = series_of(NOW - timedelta(seconds=20), [95.0, 96.0, 97.0])

    fresh = run(snapshots)
    stale = run(snapshots, now=NOW + timedelta(days=1))

    assert fresh["freshness"]["state"] == "fresh"
    assert stale["freshness"]["state"] == "stale"
    assert fresh["significance_assessments"] == stale["significance_assessments"]
    assert fresh["significance_assessments"]


def test_analyze_system_history_forwards_new_parameters_and_keeps_aliases():
    snapshots = series_of(NOW - timedelta(minutes=10), [95.0, 96.0, 97.0])

    result = run(snapshots, function=analyze_system_history, recent_window_minutes=5)

    assert result["recent"]["window_minutes"] == 5.0
    assert result["recent"]["threshold_anomalies_found"] == 0
    assert result["anomalies"] == result["persistent_anomalies"]
    assert result["anomalies_found"] == result["persistent_anomalies_found"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"recent_window_minutes": 0},
        {"recent_window_minutes": -1},
        {"recent_window_minutes": float("nan")},
        {"recent_window_minutes": float("inf")},
        {"recent_window_minutes": True},
        {"recent_window_minutes": "15"},
        {"recent_window_minutes": 1e30},
        {"now": datetime(2026, 11, 3, 12, 0, 0)},
        {"now": "2026-11-03T12:00:00+00:00"},
    ],
)
def test_invalid_window_or_now_is_rejected_before_loading(kwargs):
    with patch("app.monitoring.analysis_service.load_recent_snapshots") as loader:
        with pytest.raises(ValueError):
            run_monitoring_cycle(**kwargs)

    loader.assert_not_called()


def test_aware_non_utc_now_is_normalized():
    plus_two = timezone(timedelta(hours=2))
    latest = NOW - timedelta(seconds=5)

    result = run([snap(latest)], now=NOW.astimezone(plus_two))

    assert result["freshness"]["latest_snapshot_age_seconds"] == 5.0
    assert result["freshness"]["state"] == "fresh"


def test_result_reports_the_thresholds_and_max_gap_it_applied():
    from app.monitoring.anomaly_detector import DEFAULT_THRESHOLDS

    result = run(series_of(NOW - timedelta(seconds=5), [20.0]))

    assert result["thresholds"] == DEFAULT_THRESHOLDS
    assert result["thresholds"] is not DEFAULT_THRESHOLDS
    assert result["max_gap_seconds"] == 15.0
