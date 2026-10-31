from datetime import datetime
from unittest.mock import Mock, patch

from app.core import collector, database
from app.monitoring.analysis_service import run_monitoring_cycle
from app.monitoring.history_reader import load_recent_snapshots
from app.tools.system import state as system_state


def successful_tool_result(tool, usage_percent):
    return {
        "tool": tool,
        "status": "success",
        "data": {"usage_percent": usage_percent},
        "error": None,
    }


def test_collector_history_reader_and_monitoring_analysis_end_to_end(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "monitoring-integration.db",
    )
    database.initialize_database()

    cpu_values = [95.0, 96.0, 94.5]
    memory_values = [42.25, 43.5, 44.75]
    disk_values = [60.5, 61.0, 61.5]

    cpu_tool = Mock(side_effect=[
        successful_tool_result("get_cpu_usage", value)
        for value in cpu_values
    ])
    memory_tool = Mock(side_effect=[
        successful_tool_result("get_memory_usage", value)
        for value in memory_values
    ])
    disk_tool = Mock(side_effect=[
        successful_tool_result("get_disk_usage", value)
        for value in disk_values
    ])
    monkeypatch.setattr(system_state, "get_cpu_usage", cpu_tool)
    monkeypatch.setattr(system_state, "get_memory_usage", memory_tool)
    monkeypatch.setattr(system_state, "get_disk_usage", disk_tool)

    psi_error = {
        "tool": "get_system_pressure",
        "status": "error",
        "data": None,
        "error": {
            "code": "PSI_UNAVAILABLE",
            "message": "PSI is unavailable in this integration test.",
        },
    }

    for _ in cpu_values:
        collector.collect_system_snapshot()

    assert cpu_tool.call_count == len(cpu_values)
    assert memory_tool.call_count == len(memory_values)
    assert disk_tool.call_count == len(disk_values)

    snapshots = load_recent_snapshots(limit=10)
    assert len(snapshots) == 3
    timestamps = [datetime.fromisoformat(item["timestamp"]) for item in snapshots]
    assert timestamps == sorted(timestamps)
    assert [item["cpu_usage"] for item in snapshots] == cpu_values
    assert [item["memory_usage"] for item in snapshots] == memory_values
    assert [item["disk_usage"] for item in snapshots] == disk_values

    with patch(
        "app.monitoring.analysis_service.get_system_pressure",
        return_value=psi_error,
    ):
        result = run_monitoring_cycle(
            limit=10,
            min_samples=3,
            max_gap_seconds=15.0,
        )

    assert result["status"] == "success"
    assert result["snapshots_analyzed"] == 3

    threshold_anomalies = result["threshold_anomalies"]
    assert result["threshold_anomalies_found"] == 3
    assert [item["metric"] for item in threshold_anomalies] == [
        "cpu_usage",
        "cpu_usage",
        "cpu_usage",
    ]
    assert [item["value"] for item in threshold_anomalies] == cpu_values
    assert [item["timestamp"] for item in threshold_anomalies] == [
        item["timestamp"] for item in snapshots
    ]

    persistent_anomalies = result["persistent_anomalies"]
    assert result["persistent_anomalies_found"] == 1
    assert len(persistent_anomalies) == 1
    assert persistent_anomalies[0]["metric"] == "cpu_usage"
    assert persistent_anomalies[0]["threshold"] == 90.0
    assert persistent_anomalies[0]["samples"] == 3
    assert persistent_anomalies[0]["max_value"] == 96.0
    assert persistent_anomalies[0]["start"] == snapshots[0]["timestamp"]
    assert persistent_anomalies[0]["end"] == snapshots[-1]["timestamp"]

    assessments = result["significance_assessments"]
    assert len(assessments) == 3
    assert [item["magnitude"]["value"] for item in assessments] == [
        5.0,
        6.0,
        4.5,
    ]
    assert all(item["persistence"]["status"] == "confirmed" for item in assessments)
    assert all(
        item["persistence"]["series"]["samples"] == 3
        for item in assessments
    )
    assert all(item["data_quality"]["status"] == "clear" for item in assessments)
    assert all(
        item["corroborating_evidence"]["psi_context"]["status"] == "unavailable"
        for item in assessments
    )
