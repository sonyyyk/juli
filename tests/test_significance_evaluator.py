from copy import deepcopy

import pytest

from app.models.observation import Observation
from app.monitoring.significance_evaluator import evaluate_significance


def anomaly(
    timestamp="2026-10-11T10:00:06+00:00",
    metric="cpu_usage",
    value=95.0,
    threshold=90.0,
    unit="%",
):
    return {
        "timestamp": timestamp,
        "source": "system_snapshots",
        "metric": metric,
        "value": value,
        "unit": unit,
        "threshold": threshold,
        "reason": "threshold exceeded",
    }


def series(start="2026-10-11T10:00:00+00:00", end="2026-10-11T10:00:12+00:00"):
    return {
        "metric": "cpu_usage",
        "threshold": 90.0,
        "start": start,
        "end": end,
        "samples": 3,
        "max_value": 96.0,
        "observed_span_seconds": 12.0,
    }


def evaluate(
    anomalies=None,
    persistent=None,
    snapshots=None,
    pressure_observations=None,
    data_issues=None,
    temporal_errors=None,
    pressure_issues=None,
):
    return evaluate_significance(
        threshold_anomalies=[anomaly()] if anomalies is None else anomalies,
        persistent_anomalies=[] if persistent is None else persistent,
        snapshots=[] if snapshots is None else snapshots,
        pressure_observations=[] if pressure_observations is None else pressure_observations,
        data_issues=[] if data_issues is None else data_issues,
        temporal_errors=[] if temporal_errors is None else temporal_errors,
        pressure_issues=[] if pressure_issues is None else pressure_issues,
    )


def test_magnitude_is_value_minus_threshold():
    profile = evaluate()[0]

    assert profile["magnitude"] == {
        "status": "available",
        "value": 5.0,
        "unit": "%",
        "basis": "value - threshold",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"value": None},
        {"threshold": "not-a-number"},
        {"value": float("nan")},
        {"value": 89.0},
        {"unit": None},
    ],
)
def test_invalid_magnitude_is_unknown_not_zero(changes):
    profile = evaluate(anomalies=[anomaly(**changes)])[0]

    assert profile["magnitude"]["status"] == "unknown"
    assert profile["magnitude"]["value"] is None


def test_temporal_series_matches_metric_threshold_and_timestamp():
    profile = evaluate(persistent=[series()])[0]

    assert profile["persistence"]["status"] == "confirmed"
    assert profile["persistence"]["series"]["samples"] == 3


@pytest.mark.parametrize(
    "timestamp",
    ["2026-10-11T10:00:00+00:00", "2026-10-11T10:00:12+00:00"],
)
def test_temporal_series_boundaries_are_inclusive(timestamp):
    profile = evaluate(
        anomalies=[anomaly(timestamp=timestamp)],
        persistent=[series()],
    )[0]

    assert profile["persistence"]["status"] == "confirmed"


def test_temporal_series_requires_metric_threshold_and_covering_time():
    wrong_metric = {**series(), "metric": "memory_usage"}
    wrong_threshold = {**series(), "threshold": 91.0}
    outside_interval = {**series(), "start": "2026-10-11T10:00:07+00:00"}

    for candidate in (wrong_metric, wrong_threshold, outside_interval):
        profile = evaluate(persistent=[candidate])[0]
        assert profile["persistence"]["status"] == "unknown"
        assert profile["persistence"]["series"] is None


def test_multiple_matching_series_make_persistence_unknown():
    profile = evaluate(persistent=[series(), series()])[0]

    assert profile["persistence"]["status"] == "unknown"
    assert profile["persistence"]["series"] is None
    assert len(profile["persistence"]["matching_series"]) == 2


def test_timestamp_issue_inside_matched_series_keeps_series_but_makes_status_unknown():
    profile = evaluate(
        persistent=[series()],
        data_issues=[{
            "timestamp": "2026-10-11T10:00:06+00:00",
            "issue": "memory_usage_unavailable",
        }],
    )[0]

    assert profile["persistence"]["status"] == "unknown"
    assert profile["persistence"]["series"] is not None
    assert profile["data_quality"]["status"] == "affected"
    assert profile["data_quality"]["issues"][0]["issue"] == "memory_usage_unavailable"


def test_snapshot_index_issue_is_resolved_and_affects_temporal_sequence():
    snapshots = [
        {"timestamp": "2026-10-11T10:00:06+00:00", "cpu_usage": 95.0},
    ]
    profile = evaluate(
        persistent=[series()],
        snapshots=snapshots,
        temporal_errors=[{"snapshot_index": "0", "issue": "invalid_metric_value"}],
    )[0]

    assert profile["persistence"]["status"] == "unknown"
    assert profile["persistence"]["series"] is not None
    assert profile["data_quality"]["status"] == "affected"
    assert profile["data_quality"]["issues"][0]["source"] == "temporal_error"


def test_data_issue_snapshot_index_is_resolved_to_snapshot_timestamp():
    profile = evaluate(
        persistent=[series()],
        snapshots=[{
            "timestamp": "2026-10-11T10:00:06+00:00",
            "cpu_usage": 95.0,
        }],
        data_issues=[{"snapshot_index": "0", "issue": "cpu_usage_invalid"}],
    )[0]

    assert profile["persistence"]["status"] == "unknown"
    assert profile["persistence"]["series"] is not None
    assert profile["data_quality"]["status"] == "affected"
    assert profile["data_quality"]["issues"][0]["source"] == "data_issue"


def test_issue_at_anomaly_timestamp_is_relevant_to_data_quality():
    profile = evaluate(data_issues=[{
        "timestamp": "2026-10-11T10:00:06+00:00",
        "issue": "cpu_usage_invalid",
    }])[0]

    assert profile["data_quality"]["status"] == "affected"
    assert profile["data_quality"]["issues"]


def test_other_metric_issue_at_anomaly_timestamp_makes_persistence_unknown():
    profile = evaluate(data_issues=[{
        "timestamp": "2026-10-11T10:00:06+00:00",
        "issue": "memory_usage_unavailable",
    }])[0]

    assert profile["data_quality"]["status"] == "affected"
    assert profile["persistence"]["status"] == "unknown"


def test_unscoped_quality_issue_makes_quality_and_persistence_unknown():
    profile = evaluate(
        persistent=[series()],
        temporal_errors=[{"issue": "temporal_analysis_failed", "detail": "failure"}],
    )[0]

    assert profile["data_quality"]["status"] == "unknown"
    assert profile["persistence"]["status"] == "unknown"
    assert profile["persistence"]["series"] is not None


def test_resource_is_identified_without_claiming_service_or_user_impact():
    profile = evaluate()[0]

    assert profile["resource_impact"]["status"] == "resource_identified"
    assert profile["resource_impact"]["resource"] == {
        "name": "cpu",
        "scope": "host_metric",
    }
    assert profile["resource_impact"]["affected_services"] == {
        "status": "unknown",
        "items": None,
    }
    assert profile["resource_impact"]["affected_users"]["status"] == "unknown"


def test_same_snapshot_anomalies_are_context_not_causality():
    cpu = anomaly()
    memory = anomaly(metric="memory_usage", value=94.0)
    unrelated = anomaly(timestamp="2026-10-11T10:00:18+00:00", metric="disk_usage")

    profile = evaluate(anomalies=[cpu, memory, unrelated])[0]
    evidence = profile["corroborating_evidence"]["same_snapshot_anomalies"]

    assert len(evidence) == 1
    assert evidence[0]["metric"] == "memory_usage"
    assert evidence[0]["causal_relationship"] == "not_established"


def test_psi_observations_are_unlinked_context_even_with_same_timestamp():
    psi = Observation(
        timestamp="2026-10-11T10:00:06+00:00",
        source="get_system_pressure",
        metric="psi_cpu_some_avg10",
        value=1.25,
        unit="%",
        context={"resource": "cpu", "stall_type": "some", "window": "avg10"},
    )
    profile = evaluate(pressure_observations=[psi])[0]
    context = profile["corroborating_evidence"]["psi_context"]

    assert context["status"] == "available_unlinked"
    assert context["time_relationship"] == "not_assessed"
    assert context["observations"][0]["metric"] == "psi_cpu_some_avg10"


@pytest.mark.parametrize("pressure_issues", [[{"issue": "psi_tool_error"}], [{"issue": "psi_unavailable"}]])
def test_psi_unavailable_or_error_is_reported_as_unavailable_context(pressure_issues):
    context = evaluate(pressure_issues=pressure_issues)[0]["corroborating_evidence"]["psi_context"]

    assert context["status"] == "unavailable"
    assert context["issues"] == pressure_issues


def test_no_psi_observations_or_issues_is_no_data():
    assert evaluate()[0]["corroborating_evidence"]["psi_context"]["status"] == "no_data"


def test_input_records_are_not_mutated_and_output_uses_builtin_containers():
    anomalies = [anomaly()]
    persistent = [series()]
    snapshots = [{"timestamp": "2026-10-11T10:00:06+00:00"}]
    psi = [Observation("2026-10-11T10:00:06+00:00", "psi", "psi_cpu", 1.0, "%", {"k": "v"})]
    inputs_before = deepcopy((anomalies, persistent, snapshots, psi))

    result = evaluate(
        anomalies=anomalies,
        persistent=persistent,
        snapshots=snapshots,
        pressure_observations=psi,
    )

    assert (anomalies, persistent, snapshots, psi) == inputs_before
    assert isinstance(result, list)
    assert isinstance(result[0], dict)
    assert isinstance(result[0]["anomaly"], dict)
    assert isinstance(result[0]["corroborating_evidence"]["psi_context"]["observations"][0], dict)
