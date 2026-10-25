import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import initialize_database, save_diagnoses, get_recent_diagnoses


class IncidentChronologyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "incidents.sqlite3")
        initialize_database(self.database)
        self.base = datetime.now(timezone.utc)

    def stamp(self, seconds):
        return (self.base + timedelta(seconds=seconds)).isoformat(timespec="microseconds")

    def observe(self, seconds, *, recovery=False, critical=False, condition="cpu_pressure", evidence=None):
        diagnosis = {
            "condition": condition,
            "status_code": "no_persistent_pressure" if recovery else "possible_sustained_pressure",
            "severity": "info" if recovery else "critical" if critical else "warning",
            "evidence_status": "no_pressure_indicated" if recovery else "corroborated_resource_indicators",
            "evidence": evidence or [f"observed at {seconds}"],
            "duration_seconds": None if recovery else seconds,
        }
        return save_diagnoses([diagnosis], self.stamp(seconds), database_path=self.database)

    def events(self):
        return get_recent_diagnoses(100, self.database)

    def assert_closed(self, events, *, pressure_last_seen=10, recovery_last_seen=20):
        self.assertEqual(len(events), 2)
        recovery, pressure = events
        for event in events:
            self.assertEqual(event["incident_started_at"], self.stamp(0))
            self.assertEqual(event["resolved_at"], self.stamp(20))
            self.assertEqual(event["incident_duration_seconds"], 20)
        self.assertEqual(pressure["last_seen_at"], self.stamp(pressure_last_seen))
        self.assertEqual(recovery["last_seen_at"], self.stamp(recovery_last_seen))
        self.assertEqual(pressure["duration_seconds"], pressure_last_seen)
        self.assertIsNone(recovery["duration_seconds"])

    def test_ordered_pressure_continuation_and_recovery(self):
        self.assertEqual(self.observe(0), 1)
        self.assertEqual(self.observe(10), 0)
        self.assertEqual(self.observe(20, recovery=True), 1)
        self.assert_closed(self.events())

    def test_late_pressure_cannot_mutate_completed_incident(self):
        self.observe(0)
        self.observe(10)
        self.observe(20, recovery=True)
        completed = self.events()
        for seconds in (-1, 5, 15):
            self.assertEqual(self.observe(seconds), 0)
            self.assertEqual(self.events(), completed)
        self.assert_closed(self.events())
        self.assertEqual(self.observe(30), 1)
        self.assertEqual(self.events()[0]["incident_started_at"], self.stamp(30))
        self.assertIsNone(self.events()[0]["resolved_at"])

    def test_late_recovery_cannot_precede_latest_pressure(self):
        self.observe(0)
        self.observe(10)
        current = self.events()
        self.assertEqual(self.observe(5, recovery=True), 0)
        self.assertEqual(self.events(), current)
        self.assertEqual(current[0]["incident_started_at"], self.stamp(0))
        self.assertEqual(current[0]["last_seen_at"], self.stamp(10))
        self.assertEqual(current[0]["incident_duration_seconds"], 10)
        self.assertIsNone(current[0]["resolved_at"])
        self.observe(20, recovery=True)
        self.assert_closed(self.events())

    def test_equal_timestamp_is_first_accepted_wins(self):
        self.observe(0)
        original = self.events()
        for recovery in (False, True):
            self.assertEqual(self.observe(0, recovery=recovery, evidence=["conflicting"]), 0)
            self.assertEqual(self.events(), original)
        self.observe(10)
        self.observe(20, recovery=True)
        completed = self.events()
        self.assertEqual(self.observe(20), 0)
        self.assertEqual(self.observe(20, recovery=True), 0)
        self.assertEqual(self.events(), completed)
        self.assert_closed(self.events())

    def test_ordered_severity_and_repeated_recovery_preserve_final_duration(self):
        self.observe(0)
        self.observe(10, critical=True)
        self.observe(20, recovery=True)
        self.observe(30, recovery=True)
        events = self.events()
        self.assertEqual(len(events), 3)
        for event in events:
            self.assertEqual(event["incident_started_at"], self.stamp(0))
            self.assertEqual(event["resolved_at"], self.stamp(20))
            self.assertEqual(event["incident_duration_seconds"], 20)
        self.assertEqual([event["last_seen_at"] for event in events],
                         [self.stamp(30), self.stamp(10), self.stamp(0)])

    def test_chronology_is_per_condition_and_preserves_microseconds(self):
        self.observe(10, condition="memory_pressure")
        self.observe(0)
        self.observe(0.000001)
        cpu = next(event for event in self.events() if event["condition"] == "cpu_pressure")
        self.assertEqual(cpu["incident_started_at"], self.stamp(0))
        self.assertEqual(cpu["last_seen_at"], self.stamp(0.000001))
        self.assertEqual(cpu["incident_duration_seconds"], 0.000001)
        self.assertIsNone(cpu["resolved_at"])
