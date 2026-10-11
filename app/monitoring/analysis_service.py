
from app.monitoring.history_reader import load_recent_snapshots
from app.monitoring.temporal_analyzer import detect_persistent_anomalies


def analyze_system_history(
    limit: int = 1000,
    min_samples: int = 3,
    max_gap_seconds: float = 15.0,
) -> dict[str, object]:
    """Analyze stored system history and return a structured result.

    This function reads historical snapshots and detects persistent
    threshold exceedances. It does not evaluate incident significance
    and does not create incidents.
    """
    snapshots = load_recent_snapshots(limit=limit)

    anomalies = detect_persistent_anomalies(
        snapshots=snapshots,
        min_samples=min_samples,
        max_gap_seconds=max_gap_seconds,
    )

    return {
        "status": "success",
        "snapshots_analyzed": len(snapshots),
        "anomalies_found": len(anomalies),
        "anomalies": anomalies,
    }
