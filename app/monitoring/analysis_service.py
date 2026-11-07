
from datetime import datetime, timedelta, timezone
import math

from app.config import get_collector_interval_seconds, get_recent_window_minutes
from app.monitoring.history_reader import load_recent_snapshots
from app.monitoring.anomaly_detector import DEFAULT_THRESHOLDS, detect_threshold_anomalies
from app.monitoring.temporal_analyzer import detect_persistent_anomalies
from app.monitoring.significance_evaluator import evaluate_significance
from app.monitoring.data_quality import parse_snapshot_timestamp, parse_usage_percent
from app.models.observation import Observation
from app.tools.system.pressure import get_system_pressure


PSI_RESOURCES = ("cpu", "memory", "io")
PSI_STALL_TYPES = ("some", "full")
PSI_WINDOWS = ("avg10", "avg60", "avg300")


def run_monitoring_cycle(
    limit: int = 1000,
    min_samples: int = 3,
    max_gap_seconds: float = 15.0,
    recent_window_minutes: float | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Run snapshot analysis and collect current PSI observations.

    Besides the original keys the result carries ``freshness``, ``current``,
    ``recent`` and ``historical`` sections. ``recent_window_minutes`` defaults
    to JULI_RECENT_WINDOW_MINUTES and ``now`` to the current UTC time.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 1:
        raise ValueError("min_samples must be a positive integer")
    if (
        isinstance(max_gap_seconds, bool)
        or not isinstance(max_gap_seconds, (int, float))
    ):
        raise ValueError("max_gap_seconds must be a finite positive number")
    try:
        max_gap_seconds = float(max_gap_seconds)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("max_gap_seconds must be a finite positive number") from None
    if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be a finite positive number")
    window_minutes, now, window_start = _resolve_window(recent_window_minutes, now)
    stale_after_seconds = 3 * get_collector_interval_seconds()

    snapshots = load_recent_snapshots(limit=limit)

    observations: list[Observation] = []
    data_issues: list[dict[str, str]] = []
    temporal_errors: list[dict[str, str]] = []

    required_metrics = ("cpu_usage", "memory_usage", "disk_usage")

    parsed_times = []
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            data_issues.append({
                "snapshot_index": str(index),
                "issue": "invalid_snapshot_structure",
            })
            temporal_errors.append({
                "snapshot_index": str(index),
                "issue": "invalid_snapshot_structure",
            })
            parsed_times.append(None)
            continue

        timestamp = snapshot.get("timestamp")
        parsed_time = parse_snapshot_timestamp(timestamp)
        parsed_times.append(parsed_time)
        if parsed_time is None:
            issue = "missing_or_invalid_timestamp"
            data_issues.append({"snapshot_index": str(index), "issue": issue})
            temporal_errors.append({"snapshot_index": str(index), "issue": issue})

        for metric in required_metrics:
            value = snapshot.get(metric)
            numeric_value = parse_usage_percent(value)
            if numeric_value is None:
                issue = f"{metric}_unavailable" if value is None else f"{metric}_invalid"
                entry = {"snapshot_index": str(index), "issue": issue}
                if isinstance(timestamp, str):
                    entry["timestamp"] = timestamp
                data_issues.append(entry)
                continue

            observations.append(Observation(
                timestamp=timestamp if isinstance(timestamp, str) else "",
                source="system_snapshots",
                metric=metric,
                value=numeric_value,
                unit="%",
            ))

    # Collection order is the only ordering evidence available from the
    # schema. Report duplicate/reversed timestamps and make temporal results
    # unavailable instead of sorting rows and implying a reliable sequence.
    seen_times: dict[datetime, int] = {}
    chronology_unreliable = False
    previous_time = None
    for index, parsed_time in enumerate(parsed_times):
        if parsed_time is None:
            chronology_unreliable = True
            continue
        if parsed_time in seen_times:
            chronology_unreliable = True
            for affected_index in (seen_times[parsed_time], index):
                issue = {"snapshot_index": str(affected_index), "issue": "duplicate_timestamp"}
                data_issues.append(issue)
                temporal_errors.append(issue.copy())
        if previous_time is not None and parsed_time < previous_time:
            chronology_unreliable = True
            issue = {"snapshot_index": str(index), "issue": "out_of_order_timestamp"}
            data_issues.append(issue)
            temporal_errors.append(issue.copy())
        seen_times.setdefault(parsed_time, index)
        previous_time = parsed_time

    if chronology_unreliable:
        temporal_errors.append({
            "issue": "temporal_sequence_unreliable",
            "detail": "Snapshot timestamps are missing, duplicated, or not in collection order.",
        })

    for index in range(1, len(parsed_times)):
        previous_time = parsed_times[index - 1]
        current_time = parsed_times[index]
        if previous_time is None or current_time is None or current_time <= previous_time:
            continue
        gap = (current_time - previous_time).total_seconds()
        if gap > max_gap_seconds:
            timestamp = snapshots[index].get("timestamp")
            data_issues.append({
                "snapshot_index": str(index),
                "timestamp": str(timestamp),
                "previous_timestamp": str(snapshots[index - 1].get("timestamp")),
                "issue": "large_time_gap",
                "gap_seconds": str(gap),
                "max_gap_seconds": str(max_gap_seconds),
            })

    threshold_anomalies = detect_threshold_anomalies(observations)

    pressure_observations, pressure_issues = _collect_pressure_observations()

    if chronology_unreliable:
        persistent_anomalies = []
    else:
        try:
            persistent_anomalies = detect_persistent_anomalies(
                snapshots=snapshots,
                min_samples=min_samples,
                max_gap_seconds=max_gap_seconds,
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            persistent_anomalies = []
            temporal_errors.append({
                "issue": "temporal_analysis_failed",
                "detail": str(exc),
            })

    significance_assessments = evaluate_significance(
        threshold_anomalies=threshold_anomalies,
        persistent_anomalies=persistent_anomalies,
        snapshots=snapshots,
        pressure_observations=pressure_observations,
        data_issues=data_issues,
        temporal_errors=temporal_errors,
        pressure_issues=pressure_issues,
    )

    freshness, current, freshness_issue = _build_freshness(
        snapshots, parsed_times, now, stale_after_seconds
    )
    recent, historical = _split_recent_and_historical(
        threshold_anomalies,
        persistent_anomalies,
        significance_assessments,
        parsed_times,
        snapshots,
        window_minutes,
        window_start,
    )

    # The freshness issue is added only to the returned list, after the
    # evaluator ran: an unscoped issue would downgrade every assessment.
    returned_data_issues = list(data_issues)
    if freshness_issue is not None:
        returned_data_issues.append(freshness_issue)

    return {
        "status": (
            "partial"
            if temporal_errors or freshness["state"] != "fresh"
            else "success"
        ),
        "snapshots_analyzed": len(snapshots),
        # The thresholds both detectors applied (a value strictly above one is an anomaly).
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "max_gap_seconds": max_gap_seconds,
        "threshold_anomalies": threshold_anomalies,
        "threshold_anomalies_found": len(threshold_anomalies),
        "persistent_anomalies": persistent_anomalies,
        "persistent_anomalies_found": len(persistent_anomalies),
        "data_issues": returned_data_issues,
        "temporal_errors": temporal_errors,
        "pressure_observations": pressure_observations,
        "pressure_issues": pressure_issues,
        "significance_assessments": significance_assessments,
        "freshness": freshness,
        "current": current,
        "recent": recent,
        "historical": historical,
    }


def _resolve_window(
    recent_window_minutes: float | None,
    now: datetime | None,
) -> tuple[float, datetime, datetime]:
    """Validate the window and clock; return (minutes, now in UTC, window start)."""
    if recent_window_minutes is None:
        recent_window_minutes = get_recent_window_minutes()
    if (
        isinstance(recent_window_minutes, bool)
        or not isinstance(recent_window_minutes, (int, float))
        or not math.isfinite(recent_window_minutes)
        or recent_window_minutes <= 0
    ):
        raise ValueError("recent_window_minutes must be a finite positive number")

    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    now = now.astimezone(timezone.utc)

    try:
        window_start = now - timedelta(minutes=recent_window_minutes)
    except OverflowError:
        raise ValueError("recent_window_minutes is too large") from None
    return float(recent_window_minutes), now, window_start


def _build_freshness(
    snapshots: list[dict[str, object]],
    parsed_times: list[datetime | None],
    now: datetime,
    stale_after_seconds: int,
) -> tuple[dict[str, object], dict[str, object] | None, dict[str, str] | None]:
    """Return (freshness, current values, data issue) for the newest snapshot.

    The newest snapshot is the last one in collection order with a usable
    timestamp; rows are never re-sorted.
    """
    latest_index = next(
        (
            index
            for index in range(len(snapshots) - 1, -1, -1)
            if parsed_times[index] is not None and isinstance(snapshots[index], dict)
        ),
        None,
    )

    if latest_index is None:
        freshness = {
            "latest_snapshot_timestamp": None,
            "latest_snapshot_age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
            "state": "no_data",
        }
        issue = {
            "issue": "no_data",
            "detail": "No stored snapshot with a valid timestamp is available.",
            "stale_after_seconds": str(stale_after_seconds),
        }
        return freshness, None, issue

    snapshot = snapshots[latest_index]
    age = (now - parsed_times[latest_index]).total_seconds()
    if age < 0:
        state, issue_name = "stale", "snapshot_in_future"
        detail = "The newest snapshot is timestamped in the future; the clock is unreliable."
    elif age > stale_after_seconds:
        state, issue_name = "stale", "stale_data"
        detail = "The newest snapshot is older than the freshness limit; current state is unknown."
    else:
        state, issue_name, detail = "fresh", None, ""

    freshness = {
        "latest_snapshot_timestamp": snapshot["timestamp"],
        "latest_snapshot_age_seconds": age,
        "stale_after_seconds": stale_after_seconds,
        "state": state,
    }
    current = {
        "timestamp": snapshot["timestamp"],
        **{
            metric: parse_usage_percent(snapshot.get(metric))
            for metric in ("cpu_usage", "memory_usage", "disk_usage")
        },
    }
    issue = None
    if issue_name is not None:
        issue = {
            "issue": issue_name,
            "detail": detail,
            "latest_snapshot_timestamp": str(snapshot["timestamp"]),
            "age_seconds": str(age),
            "stale_after_seconds": str(stale_after_seconds),
        }
    return freshness, current, issue


def _split_recent_and_historical(
    threshold_anomalies: list[dict[str, object]],
    persistent_anomalies: list[dict[str, object]],
    significance_assessments: list[dict[str, object]],
    parsed_times: list[datetime | None],
    snapshots: list[dict[str, object]],
    window_minutes: float,
    window_start: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    """Place results into the recent window or a compact historical summary.

    Only a lower bound is applied, so items timestamped after ``now`` stay
    visible. A series is recent when it overlaps the window (end >= start).
    Items whose timestamp cannot be parsed are counted as unplaced.
    """
    recent_threshold: list[dict[str, object]] = []
    recent_series: list[dict[str, object]] = []
    historical_items: list[tuple[datetime, str, object]] = []  # (time, metric, timestamp)
    historical_threshold = historical_series = 0
    unplaced_threshold = unplaced_series = 0

    for anomaly in threshold_anomalies:
        moment = parse_snapshot_timestamp(anomaly.get("timestamp"))
        if moment is None:
            unplaced_threshold += 1
        elif moment >= window_start:
            recent_threshold.append(anomaly)
        else:
            historical_threshold += 1
            historical_items.append((moment, str(anomaly.get("metric")), anomaly.get("timestamp")))

    for series in persistent_anomalies:
        moment = parse_snapshot_timestamp(series.get("end"))
        if moment is None:
            unplaced_series += 1
        elif moment >= window_start:
            recent_series.append(series)
        else:
            historical_series += 1
            historical_items.append((moment, str(series.get("metric")), series.get("end")))

    recent_assessments = []
    for assessment in significance_assessments:
        anomaly = assessment.get("anomaly") if isinstance(assessment, dict) else None
        moment = (
            parse_snapshot_timestamp(anomaly.get("timestamp"))
            if isinstance(anomaly, dict)
            else None
        )
        if moment is not None and moment >= window_start:
            recent_assessments.append(assessment)

    valid_times = [
        (moment, snapshot["timestamp"])
        for moment, snapshot in zip(parsed_times, snapshots)
        if moment is not None and isinstance(snapshot, dict)
    ]
    analyzed_from = min(valid_times, key=lambda item: item[0])[1] if valid_times else None
    analyzed_to = max(valid_times, key=lambda item: item[0])[1] if valid_times else None

    recent = {
        "window_minutes": window_minutes,
        "window_start": window_start.isoformat(),
        "threshold_anomalies": recent_threshold,
        "threshold_anomalies_found": len(recent_threshold),
        "persistent_anomalies": recent_series,
        "persistent_anomalies_found": len(recent_series),
        "significance_assessments": recent_assessments,
    }
    historical = {
        "analyzed_from": analyzed_from,
        "analyzed_to": analyzed_to,
        "threshold_anomalies_found": historical_threshold,
        "persistent_anomalies_found": historical_series,
        "last_anomaly_timestamp": (
            max(historical_items, key=lambda item: item[0])[2]
            if historical_items
            else None
        ),
        "metrics": sorted({metric for _, metric, _ in historical_items}),
        "unplaced_threshold_anomalies": unplaced_threshold,
        "unplaced_persistent_series": unplaced_series,
    }
    return recent, historical


def _collect_pressure_observations() -> tuple[list[Observation], list[dict[str, str]]]:
    """Convert available PSI readings into evidence without anomaly scoring."""
    observations: list[Observation] = []
    issues: list[dict[str, str]] = []

    try:
        result = get_system_pressure()
    except Exception as exc:  # Keep optional PSI failures from stopping monitoring.
        return observations, [{
            "issue": "psi_tool_error",
            "detail": str(exc),
        }]

    if not isinstance(result, dict):
        return observations, [{
            "issue": "psi_malformed_result",
            "detail": "PSI tool result must be a mapping.",
        }]

    data = result.get("data")
    if not isinstance(data, dict):
        error = result.get("error")
        issues.append({
            "issue": "psi_tool_error" if result.get("status") == "error" else "psi_unavailable",
            "detail": str(error.get("message", error)) if isinstance(error, dict) else "PSI data is unavailable.",
        })
        return observations, issues

    resources = data.get("resources")
    unavailable = data.get("unavailable", {})
    malformed = data.get("malformed", {})
    if not isinstance(unavailable, dict):
        issues.append({
            "issue": "psi_malformed_result",
            "detail": "PSI unavailable field must be a mapping.",
        })
        unavailable = {}
    if not isinstance(malformed, dict):
        issues.append({
            "issue": "psi_malformed_result",
            "detail": "PSI malformed field must be a mapping.",
        })
        malformed = {}
    if not isinstance(resources, dict):
        issues.append({
            "issue": "psi_malformed_result",
            "detail": "PSI resources must be a mapping.",
        })
        resources = {}

    for resource, detail in unavailable.items():
        issues.append({"issue": "psi_unavailable", "resource": str(resource), "detail": str(detail)})
    for resource, detail in malformed.items():
        issues.append({"issue": "psi_malformed", "resource": str(resource), "detail": str(detail)})

    sampled_at = datetime.now(timezone.utc).isoformat()
    for resource in PSI_RESOURCES:
        if resource in unavailable or resource in malformed:
            continue
        resource_data = resources.get(resource)
        if not isinstance(resource_data, dict):
            issues.append({
                "issue": "psi_unsupported_metric" if resource not in resources else "psi_malformed",
                "resource": resource,
                "detail": "PSI resource data is not available in the tool result.",
            })
            continue

        for stall_type in PSI_STALL_TYPES:
            stall_data = resource_data.get(stall_type)
            if stall_data is None:
                issues.append({
                    "issue": "psi_unsupported_metric",
                    "resource": resource,
                    "metric": stall_type,
                    "detail": f"PSI {stall_type} metric is not provided for {resource}.",
                })
                continue
            if not isinstance(stall_data, dict):
                issues.append({
                    "issue": "psi_malformed",
                    "resource": resource,
                    "metric": stall_type,
                    "detail": "PSI stall metrics must be a mapping.",
                })
                continue

            for window in PSI_WINDOWS:
                if window not in stall_data:
                    issues.append({
                        "issue": "psi_unsupported_metric",
                        "resource": resource,
                        "metric": f"{stall_type}_{window}",
                        "detail": f"PSI {window} value is not provided for {resource} {stall_type}.",
                    })
                    continue
                value = stall_data[window]
                if isinstance(value, bool):
                    numeric_value = math.nan
                else:
                    try:
                        numeric_value = float(value)
                    except (TypeError, ValueError):
                        numeric_value = math.nan
                if not math.isfinite(numeric_value) or not 0 <= numeric_value <= 100:
                    issues.append({
                        "issue": "psi_malformed",
                        "resource": resource,
                        "metric": f"{stall_type}_{window}",
                        "detail": f"PSI value must be a finite percentage from 0 to 100; got {value!r}.",
                    })
                    continue
                observations.append(Observation(
                    timestamp=sampled_at,
                    source="get_system_pressure",
                    metric=f"psi_{resource}_{stall_type}_{window}",
                    value=numeric_value,
                    unit="%",
                    context={
                        "resource": resource,
                        "stall_type": stall_type,
                        "window": window,
                    },
                ))

    return observations, issues


def analyze_system_history(
    limit: int = 1000,
    min_samples: int = 3,
    max_gap_seconds: float = 15.0,
    recent_window_minutes: float | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Keep the existing API compatible with current callers."""
    result = run_monitoring_cycle(
        limit=limit,
        min_samples=min_samples,
        max_gap_seconds=max_gap_seconds,
        recent_window_minutes=recent_window_minutes,
        now=now,
    )

    return {
        **result,
        "anomalies": result["persistent_anomalies"],
        "anomalies_found": result["persistent_anomalies_found"],
    }
