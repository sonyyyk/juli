
import unittest

from app.monitoring.temporal_analyzer import detect_persistent_anomalies


def snapshot(second: int, cpu: float) -> dict[str, object]:
    return {
        "timestamp": f"2026-10-11T19:00:{second:02d}+00:00",
        "cpu_usage": cpu,
        "memory_usage": 40.0,
        "disk_usage": 75.0,
    }


class TestTemporalAnalyzer(unittest.TestCase):

    def test_short_spike_is_not_persistent(self):
        data = [
            snapshot(0, 30.0),
            snapshot(6, 95.0),
            snapshot(12, 35.0),
        ]

        self.assertEqual(detect_persistent_anomalies(data), [])

    def test_three_consecutive_exceedances_are_detected(self):
        data = [
            snapshot(0, 95.0),
            snapshot(6, 96.0),
            snapshot(12, 94.0),
        ]

        results = detect_persistent_anomalies(data)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["metric"], "cpu_usage")
        self.assertEqual(results[0]["samples"], 3)
        self.assertEqual(results[0]["max_value"], 96.0)

    def test_large_gap_breaks_the_run(self):
        data = [
            snapshot(0, 95.0),
            snapshot(6, 96.0),
            snapshot(30, 94.0),
        ]

        self.assertEqual(detect_persistent_anomalies(data), [])

    def test_normal_values_are_not_reported(self):
        data = [
            snapshot(0, 20.0),
            snapshot(6, 30.0),
            snapshot(12, 40.0),
        ]

        self.assertEqual(detect_persistent_anomalies(data), [])


if __name__ == "__main__":
    unittest.main()
