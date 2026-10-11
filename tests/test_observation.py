
import unittest
from dataclasses import FrozenInstanceError

from app.models.observation import Observation


class TestObservation(unittest.TestCase):

    def test_creates_observation(self):
        observation = Observation(
            timestamp="2026-10-11T19:16:40+00:00",
            source="get_cpu_usage",
            metric="cpu_usage",
            value=3.6,
            unit="percent",
        )

        self.assertEqual(observation.metric, "cpu_usage")
        self.assertEqual(observation.value, 3.6)
        self.assertEqual(observation.source, "get_cpu_usage")
        self.assertEqual(observation.context, {})

    def test_context_is_independent(self):
        first = Observation(
            timestamp="2026-10-11T19:16:40+00:00",
            source="get_memory_usage",
            metric="memory_usage",
            value=41.6,
            unit="percent",
        )

        second = Observation(
            timestamp="2026-10-11T19:16:46+00:00",
            source="get_memory_usage",
            metric="memory_usage",
            value=42.0,
            unit="percent",
        )

        first.context["note"] = "test"

        self.assertEqual(second.context, {})

    def test_observation_is_immutable(self):
        observation = Observation(
            timestamp="2026-10-11T19:16:40+00:00",
            source="get_disk_usage",
            metric="disk_usage",
            value=75.3,
            unit="percent",
        )

        with self.assertRaises(FrozenInstanceError):
            observation.value = 90.0


if __name__ == "__main__":
    unittest.main()
