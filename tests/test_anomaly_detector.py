
import unittest

from app.models.observation import Observation
from app.monitoring.anomaly_detector import (
    DEFAULT_THRESHOLDS,
    detect_threshold_anomalies,
)


def make_observation(metric: str, value: float) -> Observation:
    return Observation(
        timestamp="2026-10-11T19:30:00+00:00",
        source="test",
        metric=metric,
        value=value,
        unit="percent",
    )


class TestThresholdAnomalyDetection(unittest.TestCase):

    def test_normal_values_produce_no_anomalies(self):
        observations = [
            make_observation("cpu_usage", 45.0),
            make_observation("memory_usage", 70.0),
            make_observation("disk_usage", 85.0),
        ]

        self.assertEqual(
            detect_threshold_anomalies(observations), []
        )

    def test_values_above_threshold_are_detected(self):
        observations = [
            make_observation("cpu_usage", 95.0),
            make_observation("memory_usage", 93.0),
            make_observation("disk_usage", 94.0),
        ]

        anomalies = detect_threshold_anomalies(observations)

        self.assertEqual(len(anomalies), 3)
        self.assertEqual(
            {item["metric"] for item in anomalies},
            {"cpu_usage", "memory_usage", "disk_usage"},
        )

    def test_value_equal_to_threshold_is_not_anomaly(self):
        observations = [
            make_observation("cpu_usage", 90.0),
        ]

        self.assertEqual(
            detect_threshold_anomalies(observations), []
        )

    def test_custom_threshold(self):
        observations = [
            make_observation("cpu_usage", 75.0),
        ]

        anomalies = detect_threshold_anomalies(
            observations,
            thresholds={"cpu_usage": 70.0},
        )

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["threshold"], 70.0)

    def test_unknown_metric_is_ignored(self):
        observations = [
            make_observation("unknown_metric", 99.0),
        ]

        self.assertEqual(
            detect_threshold_anomalies(observations), []
        )

    def test_default_thresholds_are_defined(self):
        self.assertEqual(DEFAULT_THRESHOLDS["cpu_usage"], 90.0)
        self.assertEqual(DEFAULT_THRESHOLDS["memory_usage"], 90.0)
        self.assertEqual(DEFAULT_THRESHOLDS["disk_usage"], 90.0)


if __name__ == "__main__":
    unittest.main()
