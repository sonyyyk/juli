
import math

from app.monitoring.anomaly_detector import DEFAULT_THRESHOLDS
from app.monitoring.data_quality import parse_snapshot_timestamp, parse_usage_percent


def detect_persistent_anomalies(
    snapshots: list[dict[str, object]],
    thresholds: dict[str, float] | None = None,
    min_samples: int = 3,
    max_gap_seconds: float = 15.0,
) -> list[dict[str, object]]:
    """Detect threshold exceedances that persist across snapshots."""
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

    configured_thresholds = (
        DEFAULT_THRESHOLDS if thresholds is None else thresholds
    )
    if not isinstance(configured_thresholds, dict):
        raise ValueError("thresholds must be a mapping")

    # Preserve collection order. Invalid, duplicate, or reversed timestamps
    # make sequence evidence unreliable, so do not silently sort it.
    ordered = []
    previous_time = None
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            return []
        timestamp = parse_snapshot_timestamp(snapshot.get("timestamp"))
        if timestamp is None or (previous_time is not None and timestamp <= previous_time):
            return []
        ordered.append((snapshot, timestamp))
        previous_time = timestamp

    results = []

    for metric, threshold in configured_thresholds.items():
        threshold_value = parse_usage_percent(threshold)
        if threshold_value is None:
            raise ValueError(f"threshold for {metric} must be a finite percentage")
        current_run = []
        current_times = []

        def save_run():
            if len(current_run) < min_samples:
                return

            results.append({
                "metric": metric,
                "threshold": threshold_value,
                "start": current_run[0]["timestamp"],
                "end": current_run[-1]["timestamp"],
                "samples": len(current_run),
                "max_value": max(item[metric] for item in current_run),
                "observed_span_seconds": (current_times[-1] - current_times[0]).total_seconds(),
            })

        for snapshot, timestamp in ordered:
            value = parse_usage_percent(snapshot.get(metric))
            if value is None:
                save_run()
                current_run = []
                current_times = []
                continue

            if value <= threshold_value:
                save_run()
                current_run = []
                current_times = []
                continue

            if current_run:
                gap = (timestamp - current_times[-1]).total_seconds()

                if gap > max_gap_seconds:
                    save_run()
                    current_run = []
                    current_times = []

            current_run.append({**snapshot, metric: value})
            current_times.append(timestamp)

        save_run()

    return results
