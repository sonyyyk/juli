
from app.models.observation import Observation


DEFAULT_THRESHOLDS: dict[str, float] = {
    "cpu_usage": 90.0,
    "memory_usage": 90.0,
    "disk_usage": 90.0,
}


def detect_threshold_anomalies(
    observations: list[Observation],
    thresholds: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    """Detect metric values that exceed configured thresholds.

    This function detects anomalies only.
    It does not evaluate incident significance or create incidents.
    """
    configured_thresholds = (
        DEFAULT_THRESHOLDS if thresholds is None else thresholds
    )

    anomalies: list[dict[str, object]] = []

    for observation in observations:
        threshold = configured_thresholds.get(observation.metric)

        if threshold is None:
            continue

        if observation.value > threshold:
            anomalies.append({
                "timestamp": observation.timestamp,
                "source": observation.source,
                "metric": observation.metric,
                "value": observation.value,
                "unit": observation.unit,
                "threshold": threshold,
                "reason": (
                    f"{observation.metric} exceeded "
                    f"the threshold of {threshold} "
                    f"{observation.unit}"
                ),
            })

    return anomalies
