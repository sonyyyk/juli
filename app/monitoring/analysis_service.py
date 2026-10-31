
from datetime import datetime, timezone
import math

from app.monitoring.history_reader import load_recent_snapshots
from app.monitoring.anomaly_detector import detect_threshold_anomalies
from app.monitoring.temporal_analyzer import detect_persistent_anomalies
from app.monitoring.significance_evaluator import evaluate_significance
from app.models.observation import Observation
from app.tools.system.pressure import get_system_pressure


PSI_RESOURCES = ("cpu", "memory", "io")
PSI_STALL_TYPES = ("some", "full")
PSI_WINDOWS = ("avg10", "avg60", "avg300")


def run_monitoring_cycle(
    limit: int = 1000,
    min_samples: int = 3,
    max_gap_seconds: float = 15.0,
) -> dict[str, object]:
    """Run snapshot analysis and collect current PSI observations."""
    snapshots = load_recent_snapshots(limit=limit)

    observations: list[Observation] = []
    data_issues: list[dict[str, str]] = []

    required_metrics = ("cpu_usage", "memory_usage", "disk_usage")

    for index, snapshot in enumerate(snapshots):
        timestamp = snapshot.get("timestamp")

        if not isinstance(timestamp, str) or not timestamp:
            data_issues.append({
                "snapshot_index": str(index),
                "issue": "missing_or_invalid_timestamp",
            })
            continue

        for metric in required_metrics:
            value = snapshot.get(metric)

            if value is None:
                data_issues.append({
                    "timestamp": timestamp,
                    "issue": f"{metric}_unavailable",
                })
                continue

            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                data_issues.append({
                    "timestamp": timestamp,
                    "issue": f"{metric}_invalid",
                })
                continue

            observations.append(Observation(
                timestamp=timestamp,
                source="system_snapshots",
                metric=metric,
                value=numeric_value,
                unit="%",
            ))

    threshold_anomalies = detect_threshold_anomalies(observations)

    pressure_observations, pressure_issues = _collect_pressure_observations()

    valid_snapshots: list[dict[str, object]] = []
    temporal_errors: list[dict[str, str]] = []

    for index, snapshot in enumerate(snapshots):
        timestamp = snapshot.get("timestamp")

        if not isinstance(timestamp, str) or not timestamp:
            continue

        if any(snapshot.get(metric) is None for metric in required_metrics):
            continue

        try:
            for metric in required_metrics:
                float(snapshot[metric])
        except (TypeError, ValueError):
            temporal_errors.append({
                "snapshot_index": str(index),
                "issue": "invalid_metric_value",
            })
            continue

        valid_snapshots.append(snapshot)

    try:
        persistent_anomalies = detect_persistent_anomalies(
            snapshots=valid_snapshots,
            min_samples=min_samples,
            max_gap_seconds=max_gap_seconds,
        )
    except (KeyError, TypeError, ValueError) as exc:
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

    return {
        "status": "partial" if temporal_errors else "success",
        "snapshots_analyzed": len(snapshots),
        "threshold_anomalies": threshold_anomalies,
        "threshold_anomalies_found": len(threshold_anomalies),
        "persistent_anomalies": persistent_anomalies,
        "persistent_anomalies_found": len(persistent_anomalies),
        "data_issues": data_issues,
        "temporal_errors": temporal_errors,
        "pressure_observations": pressure_observations,
        "pressure_issues": pressure_issues,
        "significance_assessments": significance_assessments,
    }


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
    if not isinstance(resources, dict):
        issues.append({
            "issue": "psi_malformed_result",
            "detail": "PSI resources must be a mapping.",
        })
        resources = {}

    for resource, detail in unavailable.items() if isinstance(unavailable, dict) else ():
        issues.append({"issue": "psi_unavailable", "resource": str(resource), "detail": str(detail)})
    for resource, detail in malformed.items() if isinstance(malformed, dict) else ():
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
) -> dict[str, object]:
    """Keep the existing API compatible with current callers."""
    result = run_monitoring_cycle(
        limit=limit,
        min_samples=min_samples,
        max_gap_seconds=max_gap_seconds,
    )

    return {
        **result,
        "anomalies": result["persistent_anomalies"],
        "anomalies_found": result["persistent_anomalies_found"],
    }
