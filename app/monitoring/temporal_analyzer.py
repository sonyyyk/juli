
from datetime import datetime

from app.monitoring.anomaly_detector import DEFAULT_THRESHOLDS


def detect_persistent_anomalies(
    snapshots: list[dict[str, object]],
    thresholds: dict[str, float] | None = None,
    min_samples: int = 3,
    max_gap_seconds: float = 15.0,
) -> list[dict[str, object]]:
    """Detect threshold exceedances that persist across snapshots."""
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")

    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")

    configured_thresholds = (
        DEFAULT_THRESHOLDS if thresholds is None else thresholds
    )

    ordered = sorted(
        snapshots,
        key=lambda item: datetime.fromisoformat(
            str(item["timestamp"])
        ),
    )

    results = []

    for metric, threshold in configured_thresholds.items():
        current_run = []

        def save_run():
            if len(current_run) < min_samples:
                return

            results.append({
                "metric": metric,
                "threshold": threshold,
                "start": current_run[0]["timestamp"],
                "end": current_run[-1]["timestamp"],
                "samples": len(current_run),
                "max_value": max(
                    float(item[metric]) for item in current_run
                ),
                "observed_span_seconds": (
                    datetime.fromisoformat(
                        str(current_run[-1]["timestamp"])
                    )
                    - datetime.fromisoformat(
                        str(current_run[0]["timestamp"])
                    )
                ).total_seconds(),
            })

        for snapshot in ordered:
            value = float(snapshot[metric])
            timestamp = datetime.fromisoformat(
                str(snapshot["timestamp"])
            )

            if value <= threshold:
                save_run()
                current_run = []
                continue

            if current_run:
                previous_time = datetime.fromisoformat(
                    str(current_run[-1]["timestamp"])
                )
                gap = (timestamp - previous_time).total_seconds()

                if gap <= 0 or gap > max_gap_seconds:
                    save_run()
                    current_run = []

            current_run.append(snapshot)

        save_run()

    return results
