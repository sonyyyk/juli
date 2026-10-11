
import unittest
from unittest.mock import patch

from app.monitoring.history_reader import load_recent_snapshots
from app.monitoring.temporal_analyzer import detect_persistent_anomalies
from app.monitoring.analysis_service import analyze_system_history


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
        ):
            result = analyze_system_history(limit=3)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["snapshots_analyzed"], 3)
        self.assertEqual(result["anomalies_found"], 0)
        self.assertEqual(result["anomalies"], [])

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
        self.assertEqual(
            result["anomalies"][0]["metric"],
            "cpu_usage",
        )


if __name__ == "__main__":
    unittest.main()
