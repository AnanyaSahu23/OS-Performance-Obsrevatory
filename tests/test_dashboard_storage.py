import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from dashboard_storage import storage_call
from storage import initialize_database, get_recent_metrics


class DashboardStorageTests(unittest.TestCase):
    def test_filesystem_and_sqlite_failures_report_incomplete_history(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "file"
            blocker.write_text("not a directory", encoding="utf-8")
            warnings = []
            self.assertFalse(storage_call(initialize_database, str(blocker / "db"),
                                          on_error=warnings.append, default=False))
            self.assertEqual(storage_call(get_recent_metrics, database_path=str(Path(directory) / "empty.db"),
                                          on_error=warnings.append, default=[]), [])
            self.assertEqual(len(warnings), 2)
            self.assertTrue(all("history may be incomplete" in message for message in warnings))

    def test_unexpected_programming_errors_are_not_suppressed(self):
        def broken():
            raise TypeError("programming error")
        with self.assertRaises(TypeError):
            storage_call(broken, on_error=lambda message: None)

        def invalid_binding():
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.execute("SELECT ?", ())

        with self.assertRaises(sqlite3.ProgrammingError):
            storage_call(invalid_binding, on_error=lambda message: None)

    def test_success_returns_result_without_warning(self):
        warnings = []
        self.assertEqual(storage_call(lambda: 42, on_error=warnings.append), 42)
        self.assertEqual(warnings, [])
