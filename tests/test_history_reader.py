
import sqlite3
import unittest
from unittest.mock import patch

from app.monitoring.history_reader import load_recent_snapshots


class TestHistoryReader(unittest.TestCase):

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")

        self.connection.execute("""
            CREATE TABLE system_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cpu_usage REAL NOT NULL,
                memory_usage REAL NOT NULL,
                disk_usage REAL NOT NULL
            )
        """)

        self.connection.executemany(
            """
            INSERT INTO system_snapshots (
                timestamp, cpu_usage, memory_usage, disk_usage
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                ("2026-10-11T10:00:00+00:00", 20.0, 40.0, 70.0),
                ("2026-10-11T10:00:06+00:00", 30.0, 41.0, 71.0),
                ("2026-10-11T10:00:12+00:00", 95.0, 42.0, 72.0),
            ],
        )
        self.connection.commit()

        self.patcher = patch(
            "app.monitoring.history_reader.get_connection",
            return_value=self.connection,
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.connection.close()

    def test_loads_snapshots_in_chronological_order(self):
        snapshots = load_recent_snapshots()

        self.assertEqual(len(snapshots), 3)
        self.assertEqual(
            snapshots[0]["timestamp"],
            "2026-10-11T10:00:00+00:00",
        )
        self.assertEqual(snapshots[-1]["cpu_usage"], 95.0)

    def test_limit_returns_most_recent_snapshots(self):
        snapshots = load_recent_snapshots(limit=2)

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0]["cpu_usage"], 30.0)
        self.assertEqual(snapshots[1]["cpu_usage"], 95.0)

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            load_recent_snapshots(limit=0)


if __name__ == "__main__":
    unittest.main()
