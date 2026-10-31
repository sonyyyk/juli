
import unittest
import math

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

    def test_invalid_timestamp_duplicate_and_out_of_order_are_not_sorted_into_evidence(self):
        valid = [snapshot(0, 95.0), snapshot(6, 95.0), snapshot(12, 95.0)]
        duplicate = [snapshot(0, 95.0), snapshot(0, 95.0), snapshot(12, 95.0)]
        reversed_order = [snapshot(12, 95.0), snapshot(6, 95.0), snapshot(0, 95.0)]
        invalid = [snapshot(0, 95.0), {**snapshot(6, 95.0), "timestamp": "bad"}, snapshot(12, 95.0)]

        self.assertEqual(len(detect_persistent_anomalies(valid)), 1)
        for data in (duplicate, reversed_order, invalid):
            with self.subTest(data=data):
                self.assertEqual(detect_persistent_anomalies(data), [])

    def test_invalid_or_missing_metric_breaks_run_without_becoming_zero(self):
        for bad in (None, "95", math.nan, math.inf, -math.inf, True, -1, 101):
            data = [snapshot(0, 95.0), snapshot(6, bad), snapshot(12, 95.0)]
            with self.subTest(value=bad):
                self.assertEqual(detect_persistent_anomalies(data), [])

    def test_missing_one_metric_does_not_erase_other_metric_evidence(self):
        data = [snapshot(0, 95.0), snapshot(6, 95.0), snapshot(12, 95.0)]
        data[1]["memory_usage"] = None
        results = detect_persistent_anomalies(data)
        self.assertEqual([item["metric"] for item in results], ["cpu_usage"])

    def test_large_gap_splits_runs_and_later_contiguous_run_can_confirm(self):
        data = [snapshot(0, 95.0), snapshot(6, 95.0), snapshot(30, 95.0), snapshot(36, 95.0), snapshot(42, 95.0)]
        results = detect_persistent_anomalies(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["start"], data[2]["timestamp"])
        self.assertEqual(results[0]["samples"], 3)

    def test_invalid_analysis_parameters_are_rejected(self):
        valid = [snapshot(0, 95.0)]
        for min_samples in (0, -1, 1.0, True, "2"):
            with self.subTest(min_samples=min_samples), self.assertRaises(ValueError):
                detect_persistent_anomalies(valid, min_samples=min_samples)
        for max_gap in (0, -1, math.nan, math.inf, True, "15"):
            with self.subTest(max_gap=max_gap), self.assertRaises(ValueError):
                detect_persistent_anomalies(valid, max_gap_seconds=max_gap)


if __name__ == "__main__":
    unittest.main()
