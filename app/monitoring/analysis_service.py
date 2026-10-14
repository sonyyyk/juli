
from app.monitoring.history_reader import load_recent_snapshots
from app.monitoring.anomaly_detector import detect_threshold_anomalies
from app.monitoring.temporal_analyzer import detect_persistent_anomalies
from app.models.observation import Observation


def run_monitoring_cycle(
    limit: int = 1000,
    min_samples: int = 3,
    max_gap_seconds: float = 15.0,
) -> dict[str, object]:
    """Run threshold and temporal anomaly detection on stored snapshots."""
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

    return {
        "status": "partial" if temporal_errors else "success",
        "snapshots_analyzed": len(snapshots),
        "threshold_anomalies": threshold_anomalies,
        "threshold_anomalies_found": len(threshold_anomalies),
        "persistent_anomalies": persistent_anomalies,
        "persistent_anomalies_found": len(persistent_anomalies),
        "data_issues": data_issues,
        "temporal_errors": temporal_errors,
    }


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
