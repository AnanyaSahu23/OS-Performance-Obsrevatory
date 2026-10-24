import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import (
    get_recent_diagnoses,
    get_recent_metrics,
    initialize_database,
    save_diagnoses,
    save_snapshot,
)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.database = str(Path(self.temp_dir.name) / "history.sqlite3")
        initialize_database(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_snapshots_are_persisted_and_returned_oldest_first(self):
        base = datetime.now(timezone.utc)
        for seconds in (0, 2, 4):
            sampled_at = (base + timedelta(seconds=seconds)).isoformat()
            save_snapshot(
                {
                    "sampled_at": sampled_at,
                    "cpu_percent": 40 + seconds,
                    "memory": {"total": 1000, "used": 600, "available": 400, "percent": 60},
                    "swap": {"total": 500, "used": 20, "percent": 4},
                    "running_process_count": 3,
                    "top_cpu": [{"pid": 7, "name": "worker"}],
                    "top_memory": [],
                },
                self.database,
            )

        result = get_recent_metrics(2, self.database)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["cpu_percent"], 42)
        self.assertEqual(result[1]["cpu_percent"], 44)

    def test_diagnosis_events_are_written_for_state_changes_only(self):
        timestamp = datetime.now(timezone.utc).isoformat()
        diagnosis = {
            "condition": "cpu_pressure",
            "status_code": "possible_sustained_pressure",
            "status": "Possible CPU pressure",
            "severity": "warning",
            "evidence_status": "corroborated_resource_indicators",
            "duration_seconds": 4.0,
            "evidence": ["CPU was high in consecutive samples."],
            "observed_processes": [{"pid": 7, "name": "worker"}],
            "interpretation": "Observed contributor, not proven cause.",
        }
        recommendation = {
            "condition": "cpu_pressure",
            "priority": "Review",
            "message": "Review expected workload.",
        }

        self.assertEqual(
            save_diagnoses([diagnosis], timestamp, [recommendation], self.database), 1
        )
        self.assertEqual(
            save_diagnoses([diagnosis], timestamp, [recommendation], self.database), 0
        )
        resolved = dict(
            diagnosis,
            status_code="no_persistent_pressure",
            status="No sustained CPU pressure detected",
            severity="info",
            evidence_status="no_pressure_indicated",
        )
        self.assertEqual(
            save_diagnoses([resolved], timestamp, [recommendation], self.database), 1
        )

        events = get_recent_diagnoses(10, self.database)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["status_code"], "no_persistent_pressure")
        self.assertEqual(events[1]["recommendation"], recommendation["message"])
        self.assertEqual(events[1]["observed_processes"][0]["pid"], 7)

    def test_snapshot_requires_timestamp(self):
        with self.assertRaises(ValueError):
            save_snapshot({}, self.database)


if __name__ == "__main__":
    unittest.main()
