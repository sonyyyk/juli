from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.agent.context_builder import (
    MIN_CONTEXT_CHARS,
    UNKNOWN_STATE_WARNING,
    build_monitoring_context,
)
from app.models.observation import Observation


BASE = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
PSI_SAMPLED_AT = "2026-11-03T12:00:05+00:00"


def ts(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat()


def psi_observations(value: float = 0.25) -> list[Observation]:
    return [
        Observation(
            PSI_SAMPLED_AT,
            "get_system_pressure",
            f"psi_{resource}_{stall}_{window}",
            value,
            "%",
            {"resource": resource, "stall_type": stall, "window": window},
        )
        for resource in ("cpu", "memory", "io")
        for stall in ("some", "full")
        for window in ("avg10", "avg60", "avg300")
    ]


def anomaly(seconds: float, metric: str = "cpu_usage", value: float = 97.2) -> dict:
    return {
        "timestamp": ts(seconds),
        "source": "system_snapshots",
        "metric": metric,
        "value": value,
        "unit": "%",
        "threshold": 90.0,
        "reason": f"{metric} exceeded the threshold of 90.0 %",
    }


def profile(item: dict, psi_value: float = 0.25, persistence: str = "unknown") -> dict:
    return {
        "profile_version": 1,
        "anomaly": deepcopy(item),
        "magnitude": {"status": "available", "value": item["value"] - 90.0, "unit": "%",
                      "basis": "value - threshold"},
        "persistence": {
            "status": persistence,
            "series": None,
            "matching_series": [],
            "detail": "No matching persistent series was returned, and available history "
                      "cannot establish why.",
        },
        "resource_impact": {
            "status": "resource_identified",
            "resource": {"name": "cpu", "scope": "host_metric"},
            "affected_services": {"status": "unknown", "items": None},
            "affected_users": {"status": "unknown", "items": None},
        },
        "corroborating_evidence": {
            "same_snapshot_anomalies": [],
            "psi_context": {
                "status": "available_unlinked",
                "time_relationship": "not_assessed",
                "observations": [asdict(o) for o in psi_observations(psi_value)],
                "issues": [],
            },
        },
        "data_quality": {"status": "clear", "issues": []},
    }


def make_result(
    state: str = "fresh",
    recent_anomalies: int = 0,
    historical_anomalies: int = 0,
    profile_psi_value: float = 0.25,
) -> dict:
    """Build a result shaped like run_monitoring_cycle()."""
    recent = [anomaly(-10 * i) for i in range(recent_anomalies)]
    historical = [anomaly(-86400 - 10 * i) for i in range(historical_anomalies)]
    profiles = [profile(item, profile_psi_value) for item in historical + recent]
    age = 5.0 if state == "fresh" else 3 * 86400.0
    return {
        "status": "success" if state == "fresh" else "partial",
        "snapshots_analyzed": 1000,
        "threshold_anomalies": historical + recent,
        "threshold_anomalies_found": len(historical) + len(recent),
        "persistent_anomalies": [],
        "persistent_anomalies_found": 0,
        "data_issues": [
            {"snapshot_index": "3", "issue": "large_time_gap", "gap_seconds": "60.0"},
        ] + ([{"issue": "stale_data", "detail": "old"}] if state != "fresh" else []),
        "temporal_errors": [],
        "pressure_observations": psi_observations(),
        "pressure_issues": [],
        "significance_assessments": profiles,
        "freshness": {
            "latest_snapshot_timestamp": ts(0),
            "latest_snapshot_age_seconds": age,
            "stale_after_seconds": 30,
            "state": state,
        },
        "current": {"timestamp": ts(0), "cpu_usage": 12.3, "memory_usage": 45.0,
                    "disk_usage": 60.1},
        "recent": {
            "window_minutes": 15.0,
            "window_start": ts(-900),
            "threshold_anomalies": recent,
            "threshold_anomalies_found": len(recent),
            "persistent_anomalies": [],
            "persistent_anomalies_found": 0,
            "significance_assessments": profiles[len(historical):],
        },
        "historical": {
            "analyzed_from": ts(-2 * 86400),
            "analyzed_to": ts(0),
            "threshold_anomalies_found": len(historical),
            "persistent_anomalies_found": 1 if historical else 0,
            "last_anomaly_timestamp": historical[0]["timestamp"] if historical else None,
            "metrics": ["cpu_usage"] if historical else [],
            "unplaced_threshold_anomalies": 0,
            "unplaced_persistent_series": 0,
        },
    }


def block(text: str, header_start: str) -> str:
    for part in text.split("\n\n"):
        if part.startswith(header_start):
            return part
    raise AssertionError(f"block {header_start!r} not found")


def test_fresh_result_starts_with_freshness_and_shows_current_values():
    text = build_monitoring_context(make_result("fresh"), 4000)
    assert text.splitlines()[0].startswith("Data freshness: fresh;")
    assert f"Latest stored values ({ts(0)}): CPU 12.3 %, RAM 45.0 %, Disk 60.1 %." in text
    assert UNKNOWN_STATE_WARNING not in text


@pytest.mark.parametrize("result", [
    make_result("stale"),
    {**make_result("fresh"), "freshness": {"state": "no_data"}, "current": None},
    {key: value for key, value in make_result("fresh").items() if key != "freshness"},
    None,
    "not a mapping",
])
def test_non_fresh_or_missing_freshness_marks_current_state_unknown(result):
    lines = build_monitoring_context(result, 4000).splitlines()
    assert lines[0].startswith("Data freshness: ")
    assert not lines[0].startswith("Data freshness: fresh")
    assert UNKNOWN_STATE_WARNING in lines[:3]


def test_stale_age_is_human_readable():
    first = build_monitoring_context(make_result("stale"), 4000).splitlines()[0]
    assert "age 3.0 days" in first
    assert "freshness limit 30 s" in first


def test_future_snapshot_warns_about_clock():
    result = make_result("stale")
    result["freshness"]["latest_snapshot_age_seconds"] = -120.0
    text = build_monitoring_context(result, 4000)
    assert "in the future" in text.splitlines()[0]
    assert "clock is unreliable" in text


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "12", True])
def test_missing_or_non_finite_current_values_are_unknown_not_zero(bad):
    result = make_result()
    result["current"]["cpu_usage"] = bad
    text = build_monitoring_context(result, 4000)
    assert "CPU unknown" in text
    assert "CPU 0" not in text
    assert "Latest stored values for: CPU." in text


def test_fifty_anomalies_with_full_psi_profiles_fit_default_limit():
    result = make_result("stale", recent_anomalies=50, historical_anomalies=50)
    raw_size = len(json.dumps(result, default=lambda o: o.__dict__))
    text = build_monitoring_context(result, 4000)
    assert raw_size > 50_000
    assert len(text) <= 4000
    assert "items omitted)" in text
    assert text.startswith("Data freshness: stale")
    assert "Unknown or not established by monitoring:" in text


def test_psi_is_rendered_once_from_top_level_observations_only():
    result = make_result(recent_anomalies=5, profile_psi_value=42.4242)
    result["pressure_observations"] = psi_observations(7.77)
    text = build_monitoring_context(result, 4000)
    assert text.count("PSI (Pressure Stall Information), sampled at") == 1
    assert "42.42" not in text
    assert text.count("avg10 (10 s) 7.77 / avg60 (60 s) 7.77 / avg300 (300 s) 7.77 %") == 6
    assert "NOT temporally linked" in text


def test_missing_psi_values_are_na_and_unavailable_psi_lists_reasons():
    result = make_result()
    result["pressure_observations"] = psi_observations()[:3]
    text = build_monitoring_context(result, 4000)
    assert "- cpu some: avg10 (10 s) 0.25 / avg60 (60 s) 0.25 / avg300 (300 s) 0.25 %" in text
    assert "- io full: avg10 (10 s) n/a / avg60 (60 s) n/a / avg300 (300 s) n/a %" in text

    result["pressure_observations"] = []
    result["pressure_issues"] = [{"issue": "psi_unavailable", "resource": "cpu"}] * 2
    text = build_monitoring_context(result, 4000)
    assert "PSI (Pressure Stall Information): unavailable." in text
    assert "- reason: psi_unavailable (2)" in text


def test_recent_anomalies_are_newest_first_and_capped_at_ten():
    result = make_result(recent_anomalies=15)
    result["recent"]["threshold_anomalies"].reverse()  # input order must not matter
    recent = block(build_monitoring_context(result, 4000), "Recent window")
    lines = [line for line in recent.splitlines() if line.startswith("- threshold:")]
    assert len(lines) == 10
    assert lines[0].startswith(f"- threshold: {ts(0)} cpu_usage 97.2 % > 90.0 %")
    assert lines[-1].startswith(f"- threshold: {ts(-90)} ")
    assert recent.endswith("- (5 items omitted)")
    assert "Recent window (last 15 min, since" in recent


def test_persistent_series_line():
    result = make_result()
    result["recent"]["persistent_anomalies"] = [{
        "metric": "cpu_usage", "threshold": 90.0, "start": ts(-50), "end": ts(0),
        "samples": 6, "max_value": 98.1, "observed_span_seconds": 50.0,
    }]
    text = build_monitoring_context(result, 4000)
    assert (
        f"- persistent series: cpu_usage > 90.0 % from {ts(-50)} to {ts(0)}, 6 samples, "
        "max 98.1 %, span 50 s"
    ) in text


def test_significance_lines_and_summary():
    result = make_result(recent_anomalies=2)
    profiles = result["recent"]["significance_assessments"]
    profiles[0]["persistence"]["status"] = "confirmed"
    profiles[0]["corroborating_evidence"]["same_snapshot_anomalies"] = [
        {"metric": "memory_usage", "timestamp": ts(0), "relationship": "same_snapshot_timestamp",
         "causal_relationship": "not_established"},
    ]
    profiles[1]["data_quality"] = {"status": "affected", "issues": [{"issue": "x"}]}
    significance = block(build_monitoring_context(result, 4000), "Significance evidence")
    assert "no severity assigned" in significance
    assert (
        "- summary: 2 assessment(s); persistence unknown for 1, data quality unknown for 0; "
        "persistence is not confirmed for those anomalies"
    ) in significance
    assert (
        f"- {ts(0)} cpu_usage 97.2 %: +7.2 % over threshold; persistence confirmed; "
        "data quality clear; same-snapshot anomalies: memory_usage (causality not established)"
    ) in significance
    assert "persistence unknown: No matching persistent series" in significance
    assert "data quality affected (1 issues)" in significance
    assert "severity:" not in significance.lower()


def test_data_issues_are_counted_by_type_without_details():
    result = make_result("stale")
    result["data_issues"].append({"snapshot_index": "4", "issue": "large_time_gap"})
    result["temporal_errors"] = [{"issue": "temporal_sequence_unreliable", "detail": "secret"}]
    issues = block(build_monitoring_context(result, 4000), "Data quality issues")
    assert issues.splitlines()[1:] == [
        "- data: large_time_gap (2): gap in data collection: no snapshot was stored for longer "
        "than the allowed interval, i.e. the Collector was not running (or the machine was off); "
        "not a network problem; longest gap 60 s",
        "- data: stale_data (1)",
        "- temporal: temporal_sequence_unreliable (1)",
    ]
    assert "secret" not in issues


def test_historical_summary_line():
    text = build_monitoring_context(make_result(historical_anomalies=4), 4000)
    assert (
        f"- analyzed {ts(-2 * 86400)} to {ts(0)}: 4 threshold anomalies, 1 persistent series, "
        f"metrics: cpu_usage, last at {ts(-86400)}"
    ) in text


def test_trim_order_is_historical_then_issues_then_significance_then_recent():
    result = make_result("stale", recent_anomalies=10, historical_anomalies=4)
    full = build_monitoring_context(result, 100_000)
    order = ["Historical", "Data quality issues", "Significance evidence", "Recent window"]
    first_trimmed = {}
    for limit in range(len(full), MIN_CONTEXT_CHARS - 1, -1):
        text = build_monitoring_context(result, limit)
        assert len(text) <= limit
        for name in order:
            if name not in first_trimmed and "omitted" in block(text, name):
                first_trimmed[name] = limit
                # Every earlier block is already empty: only its header and marker remain.
                for earlier in order[: order.index(name)]:
                    assert not [
                        line for line in block(text, earlier).splitlines()[1:]
                        if not line.startswith("- (")
                    ]
    assert list(first_trimmed) == order
    assert sorted(first_trimmed.values(), reverse=True) == list(first_trimmed.values())


def test_minimum_limit_keeps_freshness_and_unknown_blocks_on_worst_input():
    long = "x" * 10_000
    result = make_result("stale", recent_anomalies=200, historical_anomalies=200)
    result["freshness"]["latest_snapshot_timestamp"] = long
    result["freshness"]["latest_snapshot_age_seconds"] = -1e300
    result["freshness"]["stale_after_seconds"] = 1e300
    result["status"] = long
    result["snapshots_analyzed"] = 1e300
    result["current"] = {"timestamp": long}
    result["recent"]["window_start"] = long
    result["recent"]["window_minutes"] = 1e300
    result["historical"]["metrics"] = [long] * 50
    result["pressure_observations"] = []
    result["pressure_issues"] = [{"issue": f"{long}{i}"} for i in range(50)]
    result["data_issues"] = [{"issue": f"{long}{i}"} for i in range(50)]
    for item in result["recent"]["significance_assessments"]:
        item["persistence"]["detail"] = long
        item["data_quality"]["status"] = "unknown"

    text = build_monitoring_context(result, MIN_CONTEXT_CHARS)
    assert len(text) <= MIN_CONTEXT_CHARS
    assert text.startswith("Data freshness: stale")
    assert UNKNOWN_STATE_WARNING in text
    assert "clock is unreliable" in text
    assert "Unknown or not established by monitoring:" in text
    assert "Causes of the anomalies" in text
    assert "- Recent anomalies (200): persistence of 200, data quality of 200." in text


def test_long_input_strings_are_truncated():
    result = make_result(recent_anomalies=1)
    result["recent"]["threshold_anomalies"][0]["metric"] = "m" * 500
    text = build_monitoring_context(result, 4000)
    assert "m" * 117 + "..." in text
    assert "m" * 121 not in text


@pytest.mark.parametrize("bad", [True, 999, 3.5, "4000", MIN_CONTEXT_CHARS - 1])
def test_invalid_max_chars_raises(bad):
    with pytest.raises(ValueError):
        build_monitoring_context(make_result(), bad)


def test_none_max_chars_reads_environment(monkeypatch):
    result = make_result("stale", recent_anomalies=50, historical_anomalies=50)
    monkeypatch.setenv("JULI_MONITORING_CONTEXT_MAX_CHARS", "1500")
    assert len(build_monitoring_context(result)) <= 1500
    monkeypatch.setenv("JULI_MONITORING_CONTEXT_MAX_CHARS", "500")
    with pytest.raises(ValueError):
        build_monitoring_context(result)


def test_output_is_deterministic_and_input_is_not_modified():
    result = make_result("stale", recent_anomalies=20, historical_anomalies=5)
    before = deepcopy(result)
    first = build_monitoring_context(result, 2000)
    second = build_monitoring_context(result, 2000)
    assert first == second
    assert result == before


@pytest.mark.parametrize("result", [
    {},
    {"status": "partial", "metric": float("nan")},
    {"freshness": "bad", "current": [1, 2], "recent": 5, "historical": "x",
     "pressure_observations": "bad", "data_issues": [None, 3, "x"]},
    {"recent": {"threshold_anomalies": [{"timestamp": None, "value": float("inf")}],
                "significance_assessments": [{"anomaly": None}]}},
])
def test_malformed_input_does_not_raise(result):
    text = build_monitoring_context(result, 4000)
    assert text.startswith("Data freshness: unknown")
    assert "Unknown or not established by monitoring:" in text
    assert "nan" not in text.lower().replace("monitoring", "")


def test_context_is_several_times_shorter_than_json_on_realistic_result():
    result = make_result("stale", historical_anomalies=4)
    raw = json.dumps(result, default=lambda o: o.__dict__, separators=(",", ":"))
    text = build_monitoring_context(result, 4000)
    assert len(text) * 4 < len(raw)


# --- Task 7: thresholds, trend analysis, collection gaps ----------------------

THRESHOLDS = {"cpu_usage": 90.0, "memory_usage": 90.0, "disk_usage": 90.0}
THRESHOLDS_LINE = (
    "Anomaly thresholds (a value above the threshold is a threshold anomaly, not an incident; "
    "a threshold is not a safe limit): CPU 90.0 %, RAM 90.0 %, Disk 90.0 %."
)
DISTANCE_HEADER = "Distance of the latest stored values to the thresholds (pp = percentage points):"


def test_thresholds_are_stated_explicitly_after_latest_values():
    result = make_result("fresh")
    result["thresholds"] = dict(THRESHOLDS)
    blocks = build_monitoring_context(result, 4000).split("\n\n")
    assert blocks[1].startswith("Latest stored values")
    assert blocks[2].splitlines() == [
        THRESHOLDS_LINE,
        DISTANCE_HEADER,
        "- CPU 12.3 %, 77.7 pp below threshold 90.0 %",
        "- RAM 45.0 %, 45.0 pp below threshold 90.0 %",
        "- Disk 60.1 %, 29.9 pp below threshold 90.0 %",
    ]


def test_thresholds_fit_the_default_limit_with_fifty_anomalies():
    result = make_result("stale", recent_anomalies=50, historical_anomalies=50)
    result["thresholds"] = dict(THRESHOLDS)
    text = build_monitoring_context(result, 4000)
    assert len(text) <= 4000
    assert THRESHOLDS_LINE in text


@pytest.mark.parametrize("thresholds", [None, {}, "90", {"cpu_usage": None}])
def test_missing_thresholds_are_reported_not_invented(thresholds):
    result = make_result("fresh")
    if thresholds is not None:
        result["thresholds"] = thresholds
    text = build_monitoring_context(result, 4000)
    assert "Anomaly thresholds: not reported." in text
    assert "90.0 %, RAM" not in text


@pytest.mark.parametrize("state", ["fresh", "stale", "no_data"])
def test_unknown_block_always_says_trend_analysis_is_not_available(state):
    text = build_monitoring_context(make_result(state), 4000)
    assert "trend analysis is not available" in block(text, "Unknown or not established")


def test_trend_line_survives_minimum_limit_and_missing_result():
    assert "trend analysis is not available" in build_monitoring_context(None, MIN_CONTEXT_CHARS)
    result = make_result("stale", recent_anomalies=200, historical_anomalies=200)
    result["thresholds"] = dict(THRESHOLDS)
    text = build_monitoring_context(result, MIN_CONTEXT_CHARS)
    assert len(text) <= MIN_CONTEXT_CHARS
    assert "trend analysis is not available" in text


def test_large_time_gap_is_described_as_a_collection_gap_with_longest_gap():
    result = make_result("fresh")
    result["data_issues"] = [
        {"snapshot_index": "3", "issue": "large_time_gap", "gap_seconds": "60.0"},
        {"snapshot_index": "9", "issue": "large_time_gap", "gap_seconds": "334800.5"},
        {"snapshot_index": "12", "issue": "large_time_gap", "gap_seconds": "bad"},
    ]
    line = block(build_monitoring_context(result, 4000), "Data quality issues").splitlines()[1]
    assert line.startswith("- data: large_time_gap (3): gap in data collection")
    assert "the Collector was not running" in line
    assert "not a network problem" in line
    assert line.endswith("longest gap 3.9 days")


def test_large_time_gap_without_gap_values_has_no_longest_gap():
    result = make_result("fresh")
    result["data_issues"] = [{"issue": "large_time_gap"}]
    issues = block(build_monitoring_context(result, 4000), "Data quality issues")
    assert "Collector was not running" in issues
    assert "longest gap" not in issues


def test_other_issue_types_get_no_explanation():
    result = make_result("stale")
    issues = block(build_monitoring_context(result, 4000), "Data quality issues")
    assert "- data: stale_data (1)" in issues.splitlines()


@pytest.mark.parametrize(
    ("disk", "expected"),
    [
        (89.3, "- Disk 89.3 %, 0.7 pp below threshold 90.0 %"),
        (89.96, "- Disk 90.0 %, <0.1 pp below threshold 90.0 %"),
        (90.0, "- Disk 90.0 %, at threshold 90.0 % (not above it)"),
        (90.04, "- Disk 90.0 %, <0.1 pp above threshold 90.0 %"),
        (92.04, "- Disk 92.0 %, 2.0 pp above threshold 90.0 %"),
    ],
)
def test_distance_to_threshold_is_stated_and_self_consistent(disk, expected):
    result = make_result("fresh")
    result["thresholds"] = dict(THRESHOLDS)
    result["current"]["disk_usage"] = disk
    assert expected in block(build_monitoring_context(result, 4000), "Anomaly thresholds").splitlines()


def test_distance_is_skipped_for_unknown_values_and_stale_values_are_still_shown():
    result = make_result("stale")
    result["thresholds"] = {"cpu_usage": 90.0, "memory_usage": "bad", "disk_usage": 90.0}
    result["current"]["cpu_usage"] = None
    lines = block(build_monitoring_context(result, 4000), "Anomaly thresholds").splitlines()
    assert lines[0].endswith("CPU 90.0 %, Disk 90.0 %.")
    assert lines[1:] == [DISTANCE_HEADER, "- Disk 60.1 %, 29.9 pp below threshold 90.0 %"]


def test_no_distance_header_without_current_values():
    result = make_result("fresh")
    result["thresholds"] = dict(THRESHOLDS)
    result["current"] = None
    thresholds = block(build_monitoring_context(result, 4000), "Anomaly thresholds")
    assert thresholds == THRESHOLDS_LINE
