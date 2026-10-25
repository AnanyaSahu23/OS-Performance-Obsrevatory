from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
import importlib.util
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from config import STORAGE_CONFIG
from reporting import build_diagnosis_rows, diagnoses_to_csv
from storage import get_recent_diagnoses, initialize_database, save_diagnoses, save_snapshot


class ReportStorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = str(Path(directory.name) / "history.sqlite3")
        self.configuration = patch.dict(STORAGE_CONFIG, database_path=self.database, retention_days=None)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        initialize_database(self.database)

    def stamp(self, seconds=0, microseconds=0):
        return (datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
                + timedelta(seconds=seconds, microseconds=microseconds)).isoformat(timespec="microseconds")

    def record(self, seconds, cpu=85, recovered=False, condition="cpu_pressure", microseconds=0):
        stamp = self.stamp(seconds, microseconds)
        save_snapshot({"sampled_at": stamp, "cpu_percent": cpu, "memory": {"percent": 60}}, self.database)
        diagnosis = {"condition": condition,
                     "status_code": "no_persistent_pressure" if recovered else "possible_sustained_pressure",
                     "status": "Recovered" if recovered else "Possible pressure",
                     "severity": "info" if recovered else "warning",
                     "evidence_status": "observed", "evidence": [f"Stored CPU {cpu}"],
                     "observed_processes": [] if recovered else [{"pid": 7, "name": "worker", "cpu_percent": cpu}],
                     "duration_seconds": None if recovered else seconds + 4,
                     "duration_scope": "high_cpu_utilization", "interpretation": "Observed, not proven cause"}
        return save_diagnoses([diagnosis], stamp,
                              [{"condition": condition, "priority": "Review", "message": f"Stored guidance {cpu}"}],
                              self.database)

    def test_progression_recovery_and_latest_metrics_match_actual_records(self):
        self.assertEqual(self.record(0), 1)
        self.assertEqual(self.record(10, cpu=90), 0)
        self.assertEqual(self.record(20, cpu=20, recovered=True), 1)
        events = get_recent_diagnoses(100, self.database, include_metrics=True)
        self.assertEqual(len(events), 2)
        recovery, pressure = events
        self.assertEqual(pressure["recorded_at"], self.stamp())
        self.assertEqual(pressure["last_seen_at"], self.stamp(10))
        self.assertEqual(pressure["metrics_sampled_at"], self.stamp(10))
        self.assertEqual(pressure["cpu_percent"], 90)
        self.assertEqual(pressure["evidence"], ["Stored CPU 90"])
        self.assertEqual(pressure["onset_evidence"], ["Stored CPU 85"])
        self.assertEqual(pressure["observed_processes"][0]["cpu_percent"], 90)
        self.assertEqual(pressure["recommendation"], "Stored guidance 90")
        for event in events:
            self.assertEqual(event["resolved_at"], self.stamp(20))
            self.assertEqual(event["incident_duration_seconds"], 20)
        self.assertEqual(recovery["cpu_percent"], 20)
        rows = list(csv.DictReader(StringIO(diagnoses_to_csv(events).decode("utf-8-sig"))))
        self.assertEqual(rows[1]["Metric duration (s)"], "14.0")
        self.assertEqual(rows[1]["Incident elapsed (s)"], "20.0")
        self.assertEqual(rows[0]["Status code"], "no_persistent_pressure")

    def test_retrieval_order_and_limit_ignore_insertion_order(self):
        for seconds in (4, 0, 2):
            self.record(seconds, condition=f"condition_{seconds}")
        events = get_recent_diagnoses(2, self.database, include_metrics=True)
        self.assertEqual([event["recorded_at"] for event in events], [self.stamp(4), self.stamp(2)])

    def test_microseconds_and_legacy_offsets_match_exactly(self):
        for microseconds in (2, 0, 1):
            self.record(0, cpu=80 + microseconds, condition=f"condition_{microseconds}", microseconds=microseconds)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE metric_samples SET sampled_at=? WHERE sampled_at=?",
                               ("2026-10-25T13:00:00.000001+01:00", self.stamp(microseconds=1)))
        events = get_recent_diagnoses(20, self.database, include_metrics=True)
        self.assertEqual([event["cpu_percent"] for event in events], [82, 81, 80])
        self.assertEqual([row["CPU (%)"] for row in build_diagnosis_rows(events)], [82, 81, 80])

    def test_missing_exact_metric_does_not_use_nearest_or_current_sample(self):
        self.record(0)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM metric_samples")
        for seconds in (-1, 1):
            save_snapshot({"sampled_at": self.stamp(seconds), "cpu_percent": 99}, self.database)
        event = get_recent_diagnoses(20, self.database, include_metrics=True)[0]
        self.assertIsNone(event["cpu_percent"])
        self.assertIsNone(event["metrics_sampled_at"])

    def test_ambiguous_legacy_metric_aliases_leave_metrics_blank_without_duplicate_events(self):
        self.record(0)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("INSERT INTO metric_samples (sampled_at, cpu_percent, top_cpu_json, top_memory_json) "
                               "VALUES (?, 99, '[]', '[]')", ("2026-10-25T13:00:00+01:00",))
        events = get_recent_diagnoses(20, self.database, include_metrics=True)
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["cpu_percent"])

    def test_incomplete_legacy_json_null_metrics_and_transition_time_fallback(self):
        self.record(0)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE diagnosis_events SET last_seen_at=NULL, observed_processes_json='null', "
                               "evidence_json='invalid', onset_evidence_json=NULL")
            connection.execute("UPDATE metric_samples SET cpu_percent=NULL, memory_percent=NULL")
        event = get_recent_diagnoses(20, self.database, include_metrics=True)[0]
        self.assertEqual(event["metrics_sampled_at"], self.stamp())
        self.assertIsNone(event["observed_processes"])
        self.assertIsNone(event["evidence"])
        row = build_diagnosis_rows([event])[0]
        self.assertEqual(row["CPU (%)"], "")
        self.assertEqual(row["Observed contributors (not proven causes)"], "")
        self.assertEqual(row["Supporting evidence"], "")

    def test_default_interface_and_database_schema_are_preserved(self):
        self.record(0)
        with closing(sqlite3.connect(self.database)) as connection:
            before = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        original = get_recent_diagnoses(20, self.database)
        report = get_recent_diagnoses(20, self.database, include_metrics=True)
        self.assertNotIn("event_id", original[0])
        self.assertNotIn("cpu_percent", original[0])
        self.assertEqual({key: report[0][key] for key in original[0]}, original[0])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall(), before)
        self.assertEqual(get_recent_diagnoses(20, self.database), original)

    def test_empty_history_and_invalid_limit(self):
        self.assertEqual(get_recent_diagnoses(100, self.database, include_metrics=True), [])
        with self.assertRaises(ValueError):
            get_recent_diagnoses(0, self.database, include_metrics=True)


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit is not installed in this interpreter")
class ReportDashboardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = str(Path(directory.name) / "dashboard.sqlite3")
        configuration = patch.dict(STORAGE_CONFIG, database_path=self.database, retention_days=None)
        configuration.start()
        self.addCleanup(configuration.stop)
        initialize_database(self.database)

    def report_app(self, ready=True):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_string("from reporting_ui import render_diagnosis_reports\n"
                                   f"render_diagnosis_reports({ready!r})").run(timeout=30)

    def test_full_dashboard_displays_stored_history_and_download(self):
        from streamlit.testing.v1 import AppTest
        stamp = datetime.now(timezone.utc).isoformat()
        save_snapshot({"sampled_at": stamp, "cpu_percent": 87}, self.database)
        save_diagnoses([{"condition": "cpu_pressure", "status_code": "possible_sustained_pressure",
                         "severity": "warning", "evidence": ["Stored history evidence"],
                         "observed_processes": [{"pid": 7, "name": "worker"}]}], stamp,
                       [{"condition": "cpu_pressure", "message": "Stored history recommendation"}], self.database)
        sample = {"sampled_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(), "cpu_percent": 30,
                  "memory": {"total": 1000, "used": 500, "available": 500, "percent": 50},
                  "swap": {"total": 500, "used": 0, "percent": 0},
                  "running_process_count": 0, "processes": [], "top_cpu": [], "top_memory": []}
        with patch("collector.collect_snapshot", return_value=sample):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(section.value == "Diagnosis History and Reports" for section in app.subheader))
        report_tables = [table.value for table in app.dataframe if "Supporting evidence" in table.value.columns]
        self.assertEqual(len(report_tables), 2)
        for table in report_tables:
            self.assertEqual(table["Memory (%)"].dtype.kind, "f")
            self.assertTrue(table["Memory (%)"].isna().any())
        self.assertIn("Stored history evidence", report_tables[0]["Supporting evidence"].tolist())
        self.assertIn("Stored history recommendation", report_tables[0]["Recommendation"].tolist())
        self.assertEqual(len(app.get("download_button")), 1)
        self.assertTrue(any(metric.label == "CPU" for metric in app.metric))

    def test_empty_history_has_no_download(self):
        app = self.report_app()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("No diagnosis history" in item.value for item in app.info))
        self.assertEqual(len(app.get("download_button")), 0)

    def test_unavailable_storage_has_no_download(self):
        with patch("reporting_ui.get_recent_diagnoses") as retrieve:
            app = self.report_app(ready=False)
        retrieve.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("storage is unavailable" in item.value for item in app.info))
        self.assertEqual(len(app.get("download_button")), 0)

    def test_database_read_failure_is_reported_without_exception_or_download(self):
        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("database unavailable")

        with patch("reporting_ui.get_recent_diagnoses", new=unavailable):
            app = self.report_app()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("History unavailable" in item.value for item in app.warning))
        self.assertTrue(any("could not be loaded" in item.value for item in app.info))
        self.assertEqual(len(app.get("download_button")), 0)
