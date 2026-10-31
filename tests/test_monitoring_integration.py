
import unittest
from unittest.mock import patch

from app.monitoring.history_reader import load_recent_snapshots
from app.monitoring.temporal_analyzer import detect_persistent_anomalies
from app.monitoring.analysis_service import (
    analyze_system_history,
    run_monitoring_cycle,
)


class TestMonitoringIntegration(unittest.TestCase):
    def run_analysis(self, snapshots):
        with patch(
            "app.monitoring.history_reader.get_connection"
        ) as mock_connection:
            connection = mock_connection.return_value
            connection.execute.return_value.fetchall.return_value = [
                (
                    item["timestamp"],
                    item["cpu_usage"],
                    item["memory_usage"],
                    item["disk_usage"],
                )
                for item in reversed(snapshots)
            ]

            loaded = load_recent_snapshots(limit=1000)

        return detect_persistent_anomalies(loaded)

    def test_normal_state_has_no_anomalies(self):
        snapshots = [
            {
                "timestamp": f"2026-10-11T10:00:{second:02d}+00:00",
                "cpu_usage": 20.0,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            }
            for second in (0, 6, 12)
        ]
        self.assertEqual(self.run_analysis(snapshots), [])

    def test_short_spike_is_not_persistent(self):
        snapshots = [
            {
                "timestamp": f"2026-10-11T10:00:{second:02d}+00:00",
                "cpu_usage": value,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            }
            for second, value in ((0, 20.0), (6, 95.0), (12, 20.0))
        ]
        self.assertEqual(self.run_analysis(snapshots), [])

    def test_persistent_cpu_exceedance_is_detected(self):
        snapshots = [
            {
                "timestamp": f"2026-10-11T10:00:{second:02d}+00:00",
                "cpu_usage": 95.0,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            }
            for second in (0, 6, 12)
        ]
        anomalies = self.run_analysis(snapshots)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["metric"], "cpu_usage")
        self.assertEqual(anomalies[0]["samples"], 3)

    def test_analysis_service_returns_structured_result(self):
        snapshots = [
            {
                "timestamp": f"2026-10-11T10:00:{second:02d}+00:00",
                "cpu_usage": 20.0,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            }
            for second in (0, 6, 12)
        ]
        with patch(
            "app.monitoring.analysis_service.load_recent_snapshots",
            return_value=snapshots,
        ), patch(
            "app.monitoring.analysis_service.get_system_pressure",
            return_value={
                "tool": "get_system_pressure",
                "status": "error",
                "data": None,
                "error": {"code": "PSI_UNAVAILABLE", "message": "No PSI data"},
            },
        ):
            result = analyze_system_history(limit=3)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["snapshots_analyzed"], 3)
        self.assertEqual(result["anomalies_found"], 0)
        self.assertEqual(result["anomalies"], [])
        self.assertEqual(result["significance_assessments"], [])

    def test_analysis_service_detects_persistent_anomaly(self):
        snapshots = [
            {
                "timestamp": f"2026-10-11T10:00:{second:02d}+00:00",
                "cpu_usage": 95.0,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            }
            for second in (0, 6, 12)
        ]
        with patch(
            "app.monitoring.analysis_service.load_recent_snapshots",
            return_value=snapshots,
        ):
            result = analyze_system_history(limit=3)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["snapshots_analyzed"], 3)
        self.assertEqual(result["anomalies_found"], 1)
        self.assertEqual(result["anomalies"][0]["metric"], "cpu_usage")

    def test_short_spike_is_threshold_anomaly_but_not_persistent(self):
        snapshots = [
            {
                "timestamp": f"2026-10-11T10:00:{second:02d}+00:00",
                "cpu_usage": value,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            }
            for second, value in ((0, 20.0), (6, 95.0), (12, 20.0))
        ]
        with patch(
            "app.monitoring.analysis_service.load_recent_snapshots",
            return_value=snapshots,
        ):
            result = run_monitoring_cycle(limit=3)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["threshold_anomalies_found"], 1)
        self.assertEqual(result["threshold_anomalies"][0]["metric"], "cpu_usage")
        self.assertEqual(result["persistent_anomalies_found"], 0)

    def test_missing_metric_is_reported_as_unavailable(self):
        snapshots = [
            {
                "timestamp": "2026-10-11T10:00:00+00:00",
                "cpu_usage": None,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            },
        ]
        with patch(
            "app.monitoring.analysis_service.load_recent_snapshots",
            return_value=snapshots,
        ):
            result = run_monitoring_cycle(limit=1)

        self.assertEqual(result["status"], "success")
        self.assertTrue(
            any(
                issue.get("issue") == "cpu_usage_unavailable"
                for issue in result["data_issues"]
            )
        )
        self.assertEqual(result["threshold_anomalies_found"], 0)
        self.assertEqual(result["persistent_anomalies_found"], 0)

    def pressure_result(self, resources, unavailable=None, malformed=None, status="success"):
        return {
            "tool": "get_system_pressure",
            "status": status,
            "data": {
                "resources": resources,
                "unavailable": unavailable or {},
                "malformed": malformed or {},
            },
            "error": None,
        }

    def complete_pressure_resources(self):
        return {
            resource: {
                stall_type: {
                    window: 1.25
                    for window in ("avg10", "avg60", "avg300")
                }
                for stall_type in ("some", "full")
            }
            for resource in ("cpu", "memory", "io")
        }

    def test_monitoring_cycle_includes_all_valid_pressure_observations(self):
        with (
            patch("app.monitoring.analysis_service.load_recent_snapshots", return_value=[]),
            patch(
                "app.monitoring.analysis_service.get_system_pressure",
                return_value=self.pressure_result(self.complete_pressure_resources()),
            ),
        ):
            result = run_monitoring_cycle()

        observations = result["pressure_observations"]
        self.assertEqual(len(observations), 18)
        self.assertEqual(
            {item.metric for item in observations},
            {
                f"psi_{resource}_{stall_type}_{window}"
                for resource in ("cpu", "memory", "io")
                for stall_type in ("some", "full")
                for window in ("avg10", "avg60", "avg300")
            },
        )
        self.assertTrue(all(item.source == "get_system_pressure" for item in observations))
        self.assertTrue(all(item.unit == "%" for item in observations))
        self.assertEqual(
            observations[0].context,
            {"resource": "cpu", "stall_type": "some", "window": "avg10"},
        )
        self.assertEqual(result["threshold_anomalies"], [])
        self.assertEqual(result["persistent_anomalies"], [])

    def test_unavailable_pressure_resource_is_reported_without_zero_values(self):
        resources = self.complete_pressure_resources()
        del resources["memory"]
        with (
            patch("app.monitoring.analysis_service.load_recent_snapshots", return_value=[]),
            patch(
                "app.monitoring.analysis_service.get_system_pressure",
                return_value=self.pressure_result(
                    resources, unavailable={"memory": "Permission denied"}, status="partial"
                ),
            ),
        ):
            result = run_monitoring_cycle()

        self.assertFalse(any(item.context["resource"] == "memory" for item in result["pressure_observations"]))
        self.assertTrue(any(
            issue["issue"] == "psi_unavailable" and issue["resource"] == "memory"
            for issue in result["pressure_issues"]
        ))

    def test_unsupported_pressure_metrics_are_reported(self):
        resources = self.complete_pressure_resources()
        del resources["cpu"]["full"]
        del resources["io"]["some"]["avg300"]
        with (
            patch("app.monitoring.analysis_service.load_recent_snapshots", return_value=[]),
            patch(
                "app.monitoring.analysis_service.get_system_pressure",
                return_value=self.pressure_result(resources),
            ),
        ):
            result = run_monitoring_cycle()

        self.assertTrue(any(
            issue["issue"] == "psi_unsupported_metric"
            and issue.get("resource") == "cpu"
            and issue.get("metric") == "full"
            for issue in result["pressure_issues"]
        ))
        self.assertTrue(any(
            issue["issue"] == "psi_unsupported_metric"
            and issue.get("resource") == "io"
            and issue.get("metric") == "some_avg300"
            for issue in result["pressure_issues"]
        ))

    def test_malformed_pressure_values_are_reported_and_excluded(self):
        resources = self.complete_pressure_resources()
        resources["cpu"]["some"]["avg10"] = float("nan")
        with (
            patch("app.monitoring.analysis_service.load_recent_snapshots", return_value=[]),
            patch(
                "app.monitoring.analysis_service.get_system_pressure",
                return_value=self.pressure_result(resources),
            ),
        ):
            result = run_monitoring_cycle()

        self.assertFalse(any(item.metric == "psi_cpu_some_avg10" for item in result["pressure_observations"]))
        self.assertTrue(any(
            issue["issue"] == "psi_malformed"
            and issue.get("metric") == "some_avg10"
            for issue in result["pressure_issues"]
        ))

    def test_pressure_tool_error_does_not_stop_snapshot_analysis(self):
        snapshots = [
            {
                "timestamp": f"2026-10-11T10:00:{second:02d}+00:00",
                "cpu_usage": 95.0,
                "memory_usage": 40.0,
                "disk_usage": 70.0,
            }
            for second in (0, 6, 12)
        ]
        tool_error = {
            "tool": "get_system_pressure",
            "status": "error",
            "data": None,
            "error": {"code": "PSI_UNAVAILABLE", "message": "No PSI data"},
        }
        with (
            patch("app.monitoring.analysis_service.load_recent_snapshots", return_value=snapshots),
            patch("app.monitoring.analysis_service.get_system_pressure", return_value=tool_error),
        ):
            result = run_monitoring_cycle()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["snapshots_analyzed"], 3)
        self.assertEqual(result["pressure_observations"], [])
        self.assertTrue(any(
            issue["issue"] == "psi_tool_error"
            and "No PSI data" in issue["detail"]
            for issue in result["pressure_issues"]
        ))

        self.assertIsInstance(result["threshold_anomalies"], list)
        self.assertEqual(len(result["threshold_anomalies"]), 3)
        self.assertEqual(result["threshold_anomalies_found"], 3)
        self.assertEqual(
            {item["metric"] for item in result["threshold_anomalies"]},
            {"cpu_usage"},
        )
        self.assertIsInstance(result["persistent_anomalies"], list)
        self.assertEqual(len(result["persistent_anomalies"]), 1)
        self.assertEqual(result["persistent_anomalies_found"], 1)
        self.assertEqual(result["persistent_anomalies"][0]["metric"], "cpu_usage")
        self.assertEqual(len(result["significance_assessments"]), 3)
        self.assertTrue(all(
            profile["persistence"]["status"] == "confirmed"
            for profile in result["significance_assessments"]
        ))
        self.assertTrue(all(
            profile["corroborating_evidence"]["psi_context"]["status"] == "unavailable"
            for profile in result["significance_assessments"]
        ))


if __name__ == "__main__":
    unittest.main()
