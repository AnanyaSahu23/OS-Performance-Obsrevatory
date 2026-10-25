import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from analyzer import assess_bottlenecks
from attribution import assess_process_attribution
from config import STORAGE_CONFIG
from diagnosis import build_diagnoses
from health import evaluate_health
from recommendations import build_recommendations
from storage import (initialize_database, save_snapshot, save_health, save_diagnoses,
                     get_recent_metrics, get_recent_health, get_recent_diagnoses)


class PipelineTests(unittest.TestCase):
    def test_analysis_recommendations_history_and_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "pipeline.sqlite3")
            initialize_database(database)
            base = datetime.now(timezone.utc)
            samples, history = [], []
            for i in range(17):
                pressure = i < 16
                cpu = 82 + i % 8 if pressure else 30
                sample = {
                    "sampled_at": (base + timedelta(seconds=2 * i)).isoformat(),
                    "cpu_percent": cpu,
                    "memory": {"total": 1000, "used": 850 if pressure else 500,
                               "available": 150 if pressure else 500, "percent": 85 if pressure else 50},
                    "swap": {"total": 1000, "used": i * 10 if pressure else 0,
                             "percent": i if pressure else 0},
                    "running_process_count": 1,
                    "processes": [{"pid": 7, "create_time": 1000, "name": "worker",
                                   "cpu_percent": cpu, "memory_rss": 700 + i}],
                }
                sample["top_cpu"] = sample["processes"]
                sample["top_memory"] = sample["processes"]
                samples.append(sample)
                history.append({"sampled_at": sample["sampled_at"], "CPU (%)": cpu,
                                "Memory (%)": sample["memory"]["percent"],
                                "Available RAM (%)": sample["memory"]["available"] / 10,
                                "Swap (%)": sample["swap"]["percent"]})
                health = evaluate_health(sample, history)
                diagnoses = build_diagnoses(sample, health, assess_bottlenecks(sample, history), history)
                recommendations = build_recommendations(diagnoses)
                attribution = assess_process_attribution(samples)
                save_snapshot(sample, database)
                save_health(health, sample["sampled_at"], database)
                save_diagnoses(diagnoses, sample["sampled_at"], recommendations, database)
                if i == 15:
                    self.assertEqual(diagnoses[0]["status_code"], "possible_sustained_pressure")
                    self.assertEqual(diagnoses[1]["status_code"], "possible_corroborated_pressure")
                    self.assertIn("swap usage is increasing", recommendations[1]["message"])
            self.assertEqual(attribution["status"], "analyzed")
            self.assertEqual(diagnoses[0]["status_code"], "no_persistent_pressure")
            self.assertTrue(any(event["resolved_at"] for event in get_recent_diagnoses(100, database)))
            script = ("import json,sys; from storage import get_recent_metrics,get_recent_health; "
                      "print(json.dumps([len(get_recent_metrics(100,sys.argv[1])), "
                      "len(get_recent_health(100,sys.argv[1]))]))")
            counts = json.loads(subprocess.check_output([sys.executable, "-B", "-c", script, database]))
            self.assertEqual(counts, [17, 17])


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit is not installed in this interpreter")
class DashboardTests(unittest.TestCase):
    def test_refresh_initializes_once_and_new_session_initializes_again(self):
        from streamlit.testing.v1 import AppTest

        def sample():
            return {"sampled_at": datetime.now(timezone.utc).isoformat(), "cpu_percent": 30,
                    "memory": {"total": 1000, "used": 500, "available": 500, "percent": 50},
                    "swap": {"total": 500, "used": 0, "percent": 0},
                    "running_process_count": 0, "processes": [], "top_cpu": [], "top_memory": []}

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "refresh.sqlite3")
            with patch.dict(STORAGE_CONFIG, database_path=database), \
                 patch("collector.collect_snapshot", side_effect=sample), \
                 patch("storage.initialize_database", wraps=initialize_database) as initialize:
                source = str(Path(__file__).resolve().parents[1] / "app.py")
                app = AppTest.from_file(source)
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(initialize.call_count, 1)
                restarted = AppTest.from_file(source)
                restarted.run(timeout=30)
                self.assertEqual(len(restarted.exception), 0)
                self.assertEqual(initialize.call_count, 2)
            self.assertEqual(len(get_recent_metrics(60, database)), 3)

    def test_real_collection_renders_and_persists_without_exception(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "dashboard.sqlite3")
            with patch.dict(STORAGE_CONFIG, database_path=database):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(get_recent_metrics(60, database)), 1)
            self.assertEqual(len(get_recent_health(60, database)), 1)

    def test_storage_failure_still_renders_live_metrics(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "file"
            blocker.write_text("not a directory", encoding="utf-8")
            with patch.dict(STORAGE_CONFIG, database_path=str(blocker / "db")):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any("History unavailable" in warning.value for warning in app.warning))
                self.assertTrue(any(metric.label == "CPU" for metric in app.metric))
