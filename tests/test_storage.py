import tempfile
import sqlite3
from contextlib import closing
from unittest.mock import patch
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import (
    get_recent_diagnoses,
    get_recent_metrics,
    initialize_database,
    save_diagnoses,
    save_snapshot,
    save_health,
    get_recent_health,
    prune_history,
)
from config import STORAGE_CONFIG


class StorageTests(unittest.TestCase):
    def test_recent_history_queries_do_not_sort_full_tables(self):
        with closing(sqlite3.connect(self.database)) as connection:
            for table, timestamp, tie in (
                ("metric_samples", "sampled_at", "id"),
                ("health_samples", "sampled_at", "sampled_at"),
                ("diagnosis_events", "recorded_at", "id"),
            ):
                plan = connection.execute(
                    f"EXPLAIN QUERY PLAN SELECT * FROM {table} "
                    f"ORDER BY julianday({timestamp}) DESC, {tie} DESC LIMIT 60"
                ).fetchall()
                self.assertFalse(any("TEMP B-TREE" in row[3] for row in plan), plan)

    def test_utc_normalization_equivalent_duplicates_and_invalid_input(self):
        for stamp in ("2026-10-24T10:00:00+05:30", "2026-10-24T04:30:00Z",
                      "2026-10-24T04:30:00"):
            save_snapshot({"sampled_at": stamp}, self.database)
            save_health({"status": "Healthy"}, stamp, self.database)
        self.assertEqual(len(get_recent_metrics(60, self.database)), 1)
        self.assertEqual(get_recent_health(60, self.database)[0]["sampled_at"],
                         "2026-10-24T04:30:00.000000+00:00")
        save_snapshot({"sampled_at": "2026-10-24T04:30:00.000001Z"}, self.database)
        self.assertEqual(len(get_recent_metrics(60, self.database)), 2)
        for stamp in ("not-a-date", None, 123):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                save_snapshot({"sampled_at": stamp}, self.database)

    def test_retention_handles_legacy_offsets_and_keeps_observed_incident(self):
        base = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        old = (base - timedelta(hours=25)).isoformat()
        fresh = (base - timedelta(hours=23)).isoformat()
        with patch.dict(STORAGE_CONFIG, retention_days=None):
            for stamp in (old, fresh):
                save_snapshot({"sampled_at": stamp}, self.database)
                save_health({"status": "Warning"}, stamp, self.database)
            diagnosis = {"condition": "cpu_pressure", "status_code": "possible_sustained_pressure"}
            save_diagnoses([diagnosis], old, database_path=self.database)
            save_diagnoses([diagnosis], fresh, database_path=self.database)
        # Simulate existing records with noncanonical but valid offsets.
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for table in ("metric_samples", "health_samples"):
                changed = connection.execute(f"UPDATE {table} SET sampled_at=? WHERE julianday(sampled_at)=julianday(?)",
                                   ((base - timedelta(hours=25)).astimezone(timezone(timedelta(hours=14))).isoformat(),
                                    old))
                self.assertEqual(changed.rowcount, 1)
                changed = connection.execute(f"UPDATE {table} SET sampled_at=? WHERE julianday(sampled_at)=julianday(?)",
                                   ((base - timedelta(hours=23)).astimezone(timezone(timedelta(hours=-12))).isoformat(),
                                    fresh))
                self.assertEqual(changed.rowcount, 1)
        with patch.dict(STORAGE_CONFIG, retention_days=1), patch("storage.datetime", wraps=datetime) as clock:
            clock.now.return_value = base
            self.assertEqual(prune_history(self.database), 2)
        self.assertEqual(len(get_recent_metrics(60, self.database)), 1)
        self.assertEqual(len(get_recent_health(60, self.database)), 1)
        self.assertEqual(len(get_recent_diagnoses(20, self.database)), 1)

    def test_incident_progression_recovery_and_restart(self):
        base = datetime.now(timezone.utc)
        stamp = lambda seconds: (base + timedelta(seconds=seconds)).isoformat()
        diagnosis = {"condition": "cpu_pressure", "status_code": "possible_sustained_pressure",
                     "severity": "warning", "evidence_status": "corroborated_resource_indicators",
                     "duration_seconds": 4, "evidence": ["onset"],
                     "observed_processes": [{"pid": 1}], "duration_scope": "high_cpu_utilization"}
        self.assertEqual(save_diagnoses([diagnosis], stamp(0), database_path=self.database), 1)
        diagnosis.update(duration_seconds=10, evidence=["latest"], observed_processes=[{"pid": 2}])
        guidance = [{"condition": "cpu_pressure", "message": "updated guidance"}]
        self.assertEqual(save_diagnoses([diagnosis], stamp(6), guidance, self.database), 0)
        event = get_recent_diagnoses(20, self.database)[0]
        self.assertEqual(event["onset_evidence"], ["onset"])
        self.assertEqual(event["evidence"], ["latest"])
        self.assertEqual(event["observed_processes"], [{"pid": 2}])
        self.assertEqual(event["recommendation"], "updated guidance")
        self.assertEqual(event["duration_seconds"], 10)
        self.assertEqual(event["last_seen_at"], stamp(6))
        diagnosis["severity"] = "critical"
        self.assertEqual(save_diagnoses([diagnosis], stamp(8), database_path=self.database), 1)
        recovery = dict(diagnosis, status_code="no_persistent_pressure", severity="info",
                        evidence_status="no_pressure_indicated", duration_seconds=None)
        self.assertEqual(save_diagnoses([recovery], stamp(12), database_path=self.database), 1)
        self.assertEqual(save_diagnoses([recovery], stamp(14), database_path=self.database), 0)
        initialize_database(self.database)
        events = get_recent_diagnoses(20, self.database)
        self.assertEqual(len(events), 3)
        for event in events:
            self.assertEqual(event["incident_started_at"], stamp(0))
            self.assertEqual(event["resolved_at"], stamp(12))
            self.assertEqual(event["incident_duration_seconds"], 12)
        self.assertEqual(save_diagnoses([diagnosis], stamp(16), database_path=self.database), 1)
        self.assertEqual(get_recent_diagnoses(1, self.database)[0]["incident_started_at"], stamp(16))

    def test_legacy_schema_migration_preserves_events(self):
        legacy = str(Path(self.temp_dir.name) / "legacy.sqlite3")
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("""CREATE TABLE diagnosis_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL,
                condition TEXT NOT NULL, status_code TEXT NOT NULL, status TEXT NOT NULL,
                severity TEXT NOT NULL, evidence_status TEXT NOT NULL, duration_seconds REAL,
                evidence_json TEXT NOT NULL, observed_processes_json TEXT NOT NULL,
                interpretation TEXT NOT NULL, recommendation_priority TEXT NOT NULL,
                recommendation TEXT NOT NULL)""")
            connection.execute("INSERT INTO diagnosis_events VALUES (1, ?, 'cpu_pressure', "
                               "'possible_sustained_pressure', 'Possible CPU pressure', "
                               "'warning', 'corroborated_resource_indicators', 4, "
                               "'[\"legacy\"]', '[]', '', 'Review', 'keep me')",
                               (datetime.now(timezone.utc).isoformat(),))
        initialize_database(legacy)
        initialize_database(legacy)
        event = get_recent_diagnoses(20, legacy)[0]
        self.assertEqual(event["recommendation"], "keep me")
        self.assertEqual(event["onset_evidence"], ["legacy"])
        self.assertEqual(event["last_seen_at"], event["recorded_at"])

    def test_out_of_order_observations_use_timestamp_not_insertion_id(self):
        base = datetime.now(timezone.utc)
        for seconds in (4, 0, 2):
            stamp = (base + timedelta(seconds=seconds)).isoformat()
            save_snapshot({"sampled_at": stamp, "cpu_percent": seconds}, self.database)
            save_health({"status": str(seconds)}, stamp, self.database)
            # Independent conditions exercise query ordering without attempting
            # to mutate one condition's lifecycle with delayed observations.
            save_diagnoses([{"condition": f"cpu_pressure_{seconds}", "status_code": str(seconds)}],
                           stamp, database_path=self.database)
        self.assertEqual([row["cpu_percent"] for row in get_recent_metrics(2, self.database)], [2, 4])
        self.assertEqual([row["status"] for row in get_recent_health(2, self.database)], ["2", "4"])
        self.assertEqual([row["status_code"] for row in get_recent_diagnoses(2, self.database)], ["4", "2"])

    def test_health_changes_are_saved_without_diagnosis_changes(self):
        from analyzer import assess_bottlenecks
        from diagnosis import build_diagnoses
        from health import evaluate_health

        base = datetime.now(timezone.utc)
        states = []
        for i, percent in enumerate((80, 85)):
            stamp = (base + timedelta(seconds=i * 2)).isoformat()
            current = {"cpu_percent": 30, "memory": {"total": 1000, "available": 500,
                       "percent": percent}, "swap": {"percent": 60}}
            history = [{"CPU (%)": 30, "Swap (%)": 60}] * 3
            health = evaluate_health(current, history)
            diagnosis = build_diagnoses(current, health, assess_bottlenecks(current, history), history)
            states.append(tuple(diagnosis[1][key] for key in
                                ("status_code", "severity", "evidence_status")))
            save_health(health, stamp, self.database)
            save_health(health, stamp, self.database)
        self.assertEqual(states[0], states[1])
        self.assertEqual([item["status"] for item in get_recent_health(60, self.database)],
                         ["Healthy", "Warning"])
        initialize_database(self.database)
        self.assertEqual(len(get_recent_health(60, self.database)), 2)

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
            save_diagnoses([resolved], (datetime.fromisoformat(timestamp) + timedelta(seconds=2)).isoformat(),
                           [recommendation], self.database), 1
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
