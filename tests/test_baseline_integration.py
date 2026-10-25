import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from baseline import detect_anomalies
from config import BASELINE_CONFIG, STORAGE_CONFIG
from storage import initialize_database, get_recent_metrics, save_snapshot
from test_baseline import BASE_TIME, SETTINGS, snapshot, stamp


class BaselineStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "baseline.sqlite3")
        initialize_database(self.database)

    def save(self, seconds, cpu=None, memory=None):
        save_snapshot({"sampled_at": stamp(seconds),
                       "cpu_percent": seconds if cpu is None else cpu,
                       "memory": {"percent": seconds + 20 if memory is None else memory}}, self.database)

    def test_prior_window_is_oldest_first_excludes_current_and_preserves_default_query(self):
        for seconds in (4, 0, 6, 2, 5, 1, 3):
            self.save(seconds)
        prior = get_recent_metrics(3, self.database, before=stamp(5))
        self.assertEqual([item["cpu_percent"] for item in prior], [2, 3, 4])
        current = snapshot(cpu=5, memory=25)
        current["sampled_at"] = stamp(5)
        result = detect_anomalies(current, prior, dict(SETTINGS, window_size=3))["cpu"]
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["baseline_mean"], 3)
        self.assertEqual([item["cpu_percent"] for item in get_recent_metrics(3, self.database)], [4, 5, 6])

    def test_microsecond_cutoff_does_not_include_equal_or_future_samples(self):
        self.save(5, cpu=10)
        self.save(5.000001, cpu=99)
        self.save(5.000002, cpu=99)
        prior = get_recent_metrics(5, self.database, before=stamp(5.000001))
        self.assertEqual([item["cpu_percent"] for item in prior], [10])

    def test_legacy_offset_timestamps_are_ordered_by_observation_time(self):
        for seconds in (4, 2, 3, 5):
            self.save(seconds)
        offset = (BASE_TIME + timedelta(seconds=4)).astimezone(timezone(timedelta(hours=5, minutes=30)))
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE metric_samples SET sampled_at=? WHERE cpu_percent=4", (offset.isoformat(),))
        prior = get_recent_metrics(3, self.database, before=stamp(5))
        self.assertEqual([item["cpu_percent"] for item in prior], [2, 3, 4])

    def test_missing_stored_values_reduce_individual_metric_counts(self):
        for seconds in range(5):
            self.save(seconds, cpu=10, memory=40)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE metric_samples SET cpu_percent=NULL WHERE cpu_percent=10")
        prior = get_recent_metrics(5, self.database, before=stamp(100))
        result = detect_anomalies(snapshot(), prior, SETTINGS)
        self.assertEqual(result["cpu"]["status"], "insufficient_history")
        self.assertEqual(result["memory"]["status"], "normal")


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit is not installed in this interpreter")
class BaselineDashboardTests(unittest.TestCase):
    def test_below_threshold_anomalies_display_without_changing_healthy_status(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "dashboard.sqlite3")
            initialize_database(database)
            for index in range(5):
                save_snapshot({"sampled_at": stamp(index), "cpu_percent": 8 + index,
                               "memory": {"percent": 38 + index}}, database)
            current = dict(snapshot(cpu=30, memory=60), top_cpu=[], top_memory=[],
                           processes=[], running_process_count=0)
            with patch.dict(STORAGE_CONFIG, database_path=database), \
                 patch.dict(BASELINE_CONFIG, SETTINGS), \
                 patch("collector.collect_snapshot", return_value=current):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                warnings = [item.value for item in app.warning]
                self.assertIn("CPU: unusual utilization", warnings)
                self.assertIn("Memory: unusual utilization", warnings)
                self.assertTrue(any(item.value.startswith("HEALTHY:") for item in app.success))
            prior = get_recent_metrics(5, database, before=current["sampled_at"])
            self.assertEqual(len(prior), 5)
            self.assertAlmostEqual(detect_anomalies(current, prior, SETTINGS)["cpu"]["baseline_mean"], 10)

    def test_empty_history_displays_insufficient_data_for_both_metrics(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as directory:
            current = dict(snapshot(), top_cpu=[], top_memory=[], processes=[], running_process_count=0)
            with patch.dict(STORAGE_CONFIG, database_path=str(Path(directory) / "empty.sqlite3")), \
                 patch("collector.collect_snapshot", return_value=current):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                messages = [item.value for item in app.info]
                self.assertIn("CPU: insufficient historical data", messages)
                self.assertIn("Memory: insufficient historical data", messages)
