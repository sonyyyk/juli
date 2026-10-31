
from app.models.observation import Observation
from app.monitoring.data_quality import parse_usage_percent


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

        value = parse_usage_percent(observation.value)
        threshold_value = parse_usage_percent(threshold)
        if value is None or threshold_value is None:
            continue

        if value > threshold_value:
            anomalies.append({
                "timestamp": observation.timestamp,
                "source": observation.source,
                "metric": observation.metric,
                "value": value,
                "unit": observation.unit,
                "threshold": threshold_value,
                "reason": (
                    f"{observation.metric} exceeded "
                    f"the threshold of {threshold_value} "
                    f"{observation.unit}"
                ),
            })

    return anomalies
