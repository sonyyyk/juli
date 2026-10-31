"""Build deterministic evidence profiles for threshold anomalies."""

from copy import deepcopy
from datetime import datetime
import math
from typing import Any

from app.models.observation import Observation
from app.monitoring.data_quality import parse_snapshot_timestamp


RESOURCE_BY_METRIC = {
    "cpu_usage": "cpu",
    "memory_usage": "memory",
    "disk_usage": "disk",
}


def evaluate_significance(
    threshold_anomalies: list[dict[str, object]],
    persistent_anomalies: list[dict[str, object]],
    snapshots: list[dict[str, object]],
    pressure_observations: list[Observation],
    data_issues: list[dict[str, str]],
    temporal_errors: list[dict[str, str]],
    pressure_issues: list[dict[str, str]],
) -> list[dict[str, object]]:
    """Return one evidence profile per threshold anomaly.

    This function does not assign severity, create incidents, or modify its
    input records. PSI is retained as unlinked context because this service
    does not establish temporal alignment between PSI and stored snapshots.
    """
    profiles: list[dict[str, object]] = []

    for anomaly in threshold_anomalies:
        anomaly_copy = deepcopy(anomaly)
        metric = anomaly.get("metric")
        timestamp = anomaly.get("timestamp")
        value = _finite_number(anomaly.get("value"))
        threshold = _finite_number(anomaly.get("threshold"))
        unit = anomaly.get("unit")

        magnitude_status = "available"
        magnitude_value: float | None = None
        if value is None or threshold is None or not isinstance(unit, str):
            magnitude_status = "unknown"
        else:
            delta = value - threshold
            if not math.isfinite(delta) or delta < 0:
                magnitude_status = "unknown"
            else:
                magnitude_value = delta

        scoped_data_issues, unscoped_data_issues = _resolve_issues(
            data_issues, snapshots, "data_issue"
        )
        scoped_temporal_errors, unscoped_temporal_errors = _resolve_issues(
            temporal_errors, snapshots, "temporal_error"
        )

        matching_series: list[dict[str, object]] = []
        invalid_matching_series = False
        parsed_anomaly_time = _parse_timestamp(timestamp)
        valid_candidate = (
            isinstance(metric, str)
            and value is not None
            and threshold is not None
            and value > threshold
            and parsed_anomaly_time is not None
        )

        if valid_candidate:
            for series in persistent_anomalies:
                if series.get("metric") != metric:
                    continue
                series_threshold = _finite_number(series.get("threshold"))
                if series_threshold is None or series_threshold != threshold:
                    continue
                start = _parse_timestamp(series.get("start"))
                end = _parse_timestamp(series.get("end"))
                if start is None or end is None or not _comparable(start, end, parsed_anomaly_time):
                    invalid_matching_series = True
                    continue
                try:
                    if start <= parsed_anomaly_time <= end:
                        matching_series.append(deepcopy(series))
                except TypeError:
                    invalid_matching_series = True

        issue_matches: list[dict[str, str]] = []
        sequence_quality_issues: list[dict[str, str]] = []
        unknown_quality = bool(
            unscoped_data_issues
            or unscoped_temporal_errors
            or not valid_candidate
        )

        for issue, issue_time in scoped_data_issues + scoped_temporal_errors:
            if issue_time is None:
                unknown_quality = True
                continue

            exact_candidate_time = _same_time(issue_time, parsed_anomaly_time)
            in_matching_series = any(
                _timestamp_in_series(issue_time, series)
                for series in matching_series
            )

            # The temporal analyzer drops an entire snapshot if any required
            # metric is missing, so another metric's issue at this timestamp
            # can still affect this anomaly's temporal evidence.
            if exact_candidate_time:
                issue_matches.append(deepcopy(issue))
            if in_matching_series and (
                issue.get("issue") != "large_time_gap" or exact_candidate_time
            ):
                sequence_quality_issues.append(deepcopy(issue))

        persistence_status: str
        persistence_detail: str
        selected_series: dict[str, object] | None = None
        if not valid_candidate:
            persistence_status = "unknown"
            persistence_detail = "The anomaly fields are insufficient or invalid for temporal matching."
        elif invalid_matching_series:
            persistence_status = "unknown"
            persistence_detail = "A candidate temporal series has invalid or incomparable timestamps."
        elif len(matching_series) > 1:
            persistence_status = "unknown"
            persistence_detail = "Multiple temporal series match this anomaly."
        elif len(matching_series) == 1:
            selected_series = matching_series[0]
            if sequence_quality_issues or unscoped_data_issues or unscoped_temporal_errors:
                persistence_status = "unknown"
                persistence_detail = "Data quality issues affect the matched temporal sequence."
            else:
                persistence_status = "confirmed"
                persistence_detail = "A temporal series covers the anomaly timestamp."
        elif unscoped_temporal_errors or unknown_quality or issue_matches:
            persistence_status = "unknown"
            persistence_detail = "Temporal persistence cannot be assessed reliably from the available data."
        else:
            # A missing series can mean a transient spike, too few samples,
            # a gap, or incomplete measurements. Without reliable negative
            # evidence those cases cannot be distinguished.
            persistence_status = "unknown"
            persistence_detail = "No matching persistent series was returned, and available history cannot establish why."

        quality_issues = issue_matches + sequence_quality_issues + unscoped_data_issues + unscoped_temporal_errors
        # Keep each issue once when it is relevant to both the candidate and series.
        quality_issues = _deduplicate_dicts(quality_issues)
        if unknown_quality or invalid_matching_series:
            quality_status = "unknown"
        elif quality_issues:
            quality_status = "affected"
        else:
            quality_status = "clear"

        same_snapshot_anomalies = []
        for other in threshold_anomalies:
            if other is anomaly or other.get("metric") == metric:
                continue
            if _same_timestamp_value(other.get("timestamp"), timestamp):
                same_snapshot_anomalies.append({
                    "metric": other.get("metric"),
                    "timestamp": other.get("timestamp"),
                    "relationship": "same_snapshot_timestamp",
                    "causal_relationship": "not_established",
                })

        psi_context_observations = [
            _observation_to_dict(observation)
            for observation in pressure_observations
        ]
        psi_context = {
            "status": (
                "available_unlinked" if psi_context_observations
                else "unavailable" if pressure_issues
                else "no_data"
            ),
            "time_relationship": "not_assessed",
            "observations": psi_context_observations,
            "issues": deepcopy(pressure_issues),
        }

        resource = RESOURCE_BY_METRIC.get(metric) if isinstance(metric, str) else None
        resource_impact = {
            "status": "resource_identified" if resource else "unknown",
            "resource": (
                {"name": resource, "scope": "host_metric"}
                if resource
                else None
            ),
            "affected_services": {"status": "unknown", "items": None},
            "affected_users": {"status": "unknown", "items": None},
        }

        profiles.append({
            "profile_version": 1,
            "anomaly": anomaly_copy,
            "magnitude": {
                "status": magnitude_status,
                "value": magnitude_value,
                "unit": unit if isinstance(unit, str) else None,
                "basis": "value - threshold",
            },
            "persistence": {
                "status": persistence_status,
                "series": selected_series,
                "matching_series": matching_series if len(matching_series) > 1 else [],
                "detail": persistence_detail,
            },
            "resource_impact": resource_impact,
            "corroborating_evidence": {
                "same_snapshot_anomalies": same_snapshot_anomalies,
                "psi_context": psi_context,
            },
            "data_quality": {
                "status": quality_status,
                "issues": quality_issues,
            },
        })

    return profiles


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_timestamp(value: object) -> datetime | None:
    return parse_snapshot_timestamp(value)


def _comparable(*values: datetime) -> bool:
    return len({value.tzinfo is not None for value in values}) == 1


def _same_time(first: datetime, second: datetime | None) -> bool:
    if second is None or not _comparable(first, second):
        return False
    return first == second


def _same_timestamp_value(first: object, second: object) -> bool:
    parsed_first = _parse_timestamp(first)
    parsed_second = _parse_timestamp(second)
    return (
        parsed_first is not None
        and parsed_second is not None
        and _same_time(parsed_first, parsed_second)
    )


def _timestamp_in_series(timestamp: datetime, series: dict[str, object]) -> bool:
    start = _parse_timestamp(series.get("start"))
    end = _parse_timestamp(series.get("end"))
    if start is None or end is None or not _comparable(timestamp, start, end):
        return False
    return start <= timestamp <= end


def _resolve_issues(
    issues: list[dict[str, str]],
    snapshots: list[dict[str, object]],
    source: str,
) -> tuple[list[tuple[dict[str, str], datetime | None]], list[dict[str, str]]]:
    resolved: list[tuple[dict[str, str], datetime | None]] = []
    unscoped: list[dict[str, str]] = []
    for issue in issues:
        issue_copy = {**deepcopy(issue), "source": source}
        issue_time = _parse_timestamp(issue.get("timestamp"))
        if issue_time is None and "snapshot_index" in issue:
            try:
                snapshot_index = int(issue["snapshot_index"])
                snapshot = snapshots[snapshot_index]
            except (TypeError, ValueError, IndexError):
                snapshot = None
            if isinstance(snapshot, dict):
                issue_time = _parse_timestamp(snapshot.get("timestamp"))
        if issue_time is None:
            unscoped.append(issue_copy)
        resolved.append((issue_copy, issue_time))
    return resolved, unscoped


def _deduplicate_dicts(items: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    seen = set()
    for item in items:
        key = tuple(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _observation_to_dict(observation: Observation | dict[str, Any]) -> dict[str, object]:
    if isinstance(observation, Observation):
        return {
            "timestamp": observation.timestamp,
            "source": observation.source,
            "metric": observation.metric,
            "value": observation.value,
            "unit": observation.unit,
            "context": deepcopy(observation.context),
        }
    return deepcopy(observation)
