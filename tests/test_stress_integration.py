import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from config import STORAGE_CONFIG
import stress
from stress import StressTestController


class WorkerProcessTests(unittest.TestCase):
    def quiet_controller(self):
        processes = []

        def launch(_command, **kwargs):
            # Exercise real cleanup using sleeping children, not CPU stress.
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", "import time; time.sleep(5)"], **kwargs
            )
            processes.append(process)
            return process

        controller = StressTestController(process_factory=launch)
        self.addCleanup(controller.close)
        return controller, processes

    def test_supervisor_stops_real_quiet_worker_without_dashboard_polling(self):
        controller, processes = self.quiet_controller()
        controller.start(0.15, 1)
        controller._monitor.join(timeout=3)
        self.assertFalse(controller._monitor.is_alive())
        self.assertEqual(controller.status()["status"], "finished")
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_manual_stop_reaps_a_real_quiet_worker(self):
        controller, processes = self.quiet_controller()
        controller.start(2, 1)
        self.assertEqual(controller.stop()["status"], "stopped")
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_python_worker_exits_on_its_own_deadline_without_a_controller(self):
        # At most 50ms of computation, and often none after interpreter startup.
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(stress.__file__).resolve()),
             "--worker", repr(time.monotonic() + 0.05)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


class FakeController:
    limits = {"max_duration_seconds": 30, "max_workers": 2}

    def __init__(self, state="idle"):
        self.state = state
        self.starts = []
        self.stops = 0

    def status(self):
        return {"status": self.state, "active": self.state == "running",
                "duration_seconds": 10, "worker_count": 1, "worker_pids": [],
                "elapsed_seconds": 1, "remaining_seconds": 9,
                "error": "worker failed" if self.state == "failed" else ""}

    def start(self, duration, workers):
        self.starts.append((duration, workers))
        self.state = "running"
        return self.status()

    def stop(self):
        self.stops += 1
        self.state = "stopped"
        return self.status()


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit is not installed in this interpreter")
class StressDashboardTests(unittest.TestCase):
    def run_dashboard(self, controller):
        from streamlit.testing.v1 import AppTest

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        sample = {"sampled_at": datetime.now(timezone.utc).isoformat(), "cpu_percent": 30,
                  "memory": {"total": 1000, "used": 500, "available": 500, "percent": 50},
                  "swap": {"total": 500, "used": 0, "percent": 0},
                  "running_process_count": 0, "processes": [], "top_cpu": [], "top_memory": []}
        for patcher in (
            patch.dict(STORAGE_CONFIG, database_path=str(Path(directory.name) / "dashboard.sqlite3")),
            patch("stress_ui.get_stress_controller", return_value=controller),
            patch("collector.collect_snapshot", return_value=sample),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        return app

    def test_start_stop_controls_do_not_fabricate_pressure(self):
        controller = FakeController()
        app = self.run_dashboard(controller)
        app.number_input[0].set_value(5)
        app.button[0].click().run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(controller.starts, [(5, 1)])
        self.assertTrue(any("Running:" in item.value for item in app.info))
        self.assertTrue(any(item.value.startswith("HEALTHY:") for item in app.success))
        app.button[1].click().run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(controller.stops, 1)
        self.assertTrue(any("Stress test stopped" in item.value for item in app.success))

    def test_refresh_does_not_start_work_and_finished_state_is_visible(self):
        controller = FakeController("finished")
        app = self.run_dashboard(controller)
        app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(controller.starts, [])
        self.assertTrue(any("Stress test finished" in item.value for item in app.success))

    def test_failure_status_is_visible_without_launching_workers(self):
        controller = FakeController("failed")
        app = self.run_dashboard(controller)
        self.assertTrue(any("worker failed" in item.value for item in app.error))
        self.assertEqual(controller.starts, [])
