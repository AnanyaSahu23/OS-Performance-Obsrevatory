import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from config import STORAGE_CONFIG
from dashboard_storage import storage_call
from storage import (initialize_database, save_snapshot, save_health, save_diagnoses,
                     get_recent_metrics, get_recent_health, get_recent_diagnoses)


class StorageReliabilityTests(unittest.TestCase):
    def test_concurrent_initialization_and_duplicate_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "concurrent.sqlite3")
            stamp = datetime.now(timezone.utc).isoformat()
            barrier = Barrier(4)

            def writer(_):
                barrier.wait(timeout=10)
                initialize_database(database)
                save_snapshot({"sampled_at": stamp, "cpu_percent": 85}, database)
                save_health({"status": "Warning"}, stamp, database)
                return save_diagnoses([{"condition": "cpu_pressure",
                                       "status_code": "possible_sustained_pressure"}],
                                      stamp, database_path=database)

            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(sum(pool.map(writer, range(4))), 1)
            self.assertEqual(len(get_recent_metrics(60, database)), 1)
            self.assertEqual(len(get_recent_health(60, database)), 1)
            self.assertEqual(len(get_recent_diagnoses(20, database)), 1)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_lock_timeout_warns_then_connection_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "locked.sqlite3")
            initialize_database(database)
            sample = {"sampled_at": datetime.now(timezone.utc).isoformat()}
            warnings = []
            with closing(sqlite3.connect(database)) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                with patch.dict(STORAGE_CONFIG, connection_timeout_seconds=0.01):
                    storage_call(save_snapshot, sample, database, on_error=warnings.append)
                blocker.rollback()
            self.assertEqual(len(warnings), 1)
            self.assertIn("locked", warnings[0])
            save_snapshot(sample, database)
            self.assertEqual(len(get_recent_metrics(60, database)), 1)
