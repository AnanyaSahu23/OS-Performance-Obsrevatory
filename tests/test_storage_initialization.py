import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from dashboard_storage import storage_call
from storage import (ensure_database_initialized, initialize_database,
                     get_recent_metrics)
from config import STORAGE_CONFIG


class StorageInitializationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "history.sqlite3")

    def test_refresh_skips_migrations_and_new_session_initializes(self):
        state = {}
        with patch("storage.initialize_database", wraps=initialize_database) as initialize:
            ensure_database_initialized(state, self.database)
            ensure_database_initialized(state, self.database)
            ensure_database_initialized(state, self.database)
            self.assertEqual(initialize.call_count, 1)
            ensure_database_initialized({}, self.database)
            self.assertEqual(initialize.call_count, 2)

    def test_changed_path_deleted_file_and_changed_schema_reinitialize(self):
        state = {}
        other = str(Path(self.directory.name) / "other.sqlite3")
        with patch("storage.initialize_database", wraps=initialize_database) as initialize:
            ensure_database_initialized(state, self.database)
            ensure_database_initialized(state, other)
            self.assertEqual(initialize.call_count, 2)
            # Only this test's temporary database is removed.
            Path(other).unlink()
            ensure_database_initialized(state, other)
            self.assertEqual(initialize.call_count, 3)
            with closing(sqlite3.connect(other)) as connection, connection:
                connection.execute("DROP TABLE health_samples")
            ensure_database_initialized(state, other)
            self.assertEqual(initialize.call_count, 4)
            with closing(sqlite3.connect(other)) as connection:
                self.assertIsNotNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='health_samples'"
                ).fetchone())
            replacement = str(Path(self.directory.name) / "replacement.sqlite3")
            initialize_database(replacement)
            Path(replacement).replace(other)
            ensure_database_initialized(state, other)
            self.assertEqual(initialize.call_count, 5)

    def test_initialized_refresh_does_not_require_a_writer_lock(self):
        state = {}
        ensure_database_initialized(state, self.database)
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with patch.dict(STORAGE_CONFIG, connection_timeout_seconds=0.01):
                ensure_database_initialized(state, self.database)
            writer.rollback()

    def test_failed_initialization_is_not_cached_and_retry_succeeds(self):
        state, warnings = {}, []
        with patch("storage.initialize_database", side_effect=sqlite3.OperationalError("locked")):
            self.assertFalse(storage_call(ensure_database_initialized, state, self.database,
                                          on_error=warnings.append, default=False))
        self.assertEqual(state, {})
        self.assertEqual(len(warnings), 1)
        ensure_database_initialized(state, self.database)
        self.assertEqual(get_recent_metrics(60, self.database), [])
        self.assertTrue(state)

    def test_concurrent_sessions_initialize_independently(self):
        barrier = Barrier(4)

        def session(_):
            state = {}
            barrier.wait(timeout=10)
            ensure_database_initialized(state, self.database)
            ensure_database_initialized(state, self.database)
            return bool(state)

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(session, range(4))))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
