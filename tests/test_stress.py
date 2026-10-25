import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import stress
from stress import StressTestController, _cpu_worker


class FakeProcess:
    def __init__(self, pid, *, ignore_terminate=False, unkillable=False):
        self.pid = pid
        self.returncode = None
        self.ignore_terminate = ignore_terminate
        self.unkillable = unkillable
        self.terminated = self.killed = self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate and not self.unkillable:
            self.returncode = -15

    def kill(self):
        self.killed = True
        if not self.unkillable:
            self.returncode = -9

    def wait(self, timeout):
        self.waited = True
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake worker", timeout)
        return self.returncode


class StressControllerTests(unittest.TestCase):
    def setUp(self):
        for target, kwargs in (("stress.atexit.register", {}), ("stress.Thread", {}),
                               ("stress.os.cpu_count", {"return_value": 4})):
            patcher = patch(target, **kwargs)
            value = patcher.start()
            self.addCleanup(patcher.stop)
            if target == "stress.Thread":
                self.thread = value
        self.now = 100.0
        self.processes = []
        self.launch = Mock(side_effect=self.make_process)
        self.controller = StressTestController(process_factory=self.launch, clock=lambda: self.now)
        self.addCleanup(self.controller.close)

    def make_process(self, *args, **kwargs):
        process = FakeProcess(1000 + len(self.processes))
        self.processes.append(process)
        return process

    def test_start_launches_only_bounded_owned_python_workers(self):
        result = self.controller.start(10, 2)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["worker_pids"], [1000, 1001])
        self.assertEqual(result["remaining_seconds"], 10)
        for args, kwargs in self.launch.call_args_list:
            self.assertEqual(args[0][-2:], ["--worker", "110.0"])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_manual_stop_reaps_only_owned_processes_and_is_idempotent(self):
        unrelated = FakeProcess(999)
        self.controller.start(10, 2)
        self.now = 103
        result = self.controller.stop()
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["active"])
        self.assertEqual(result["elapsed_seconds"], 3)
        self.assertTrue(all(process.terminated and process.waited for process in self.processes))
        self.assertFalse(unrelated.terminated or unrelated.waited)
        self.now = 200
        self.assertEqual(self.controller.stop()["elapsed_seconds"], 3)

    def test_duration_expiry_finishes_and_cleans_up(self):
        self.controller.start(10, 1)
        self.now = 110
        result = self.controller.status()
        self.assertEqual(result["status"], "finished")
        self.assertFalse(result["active"])
        self.assertEqual(result["remaining_seconds"], 0)
        self.assertTrue(self.processes[0].waited)

    def test_overlapping_starts_are_rejected_even_from_concurrent_callers(self):
        def attempt(_):
            try:
                self.controller.start(10, 1)
                return True
            except RuntimeError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, range(2))), 1)
        self.assertEqual(self.launch.call_count, 1)

    def test_invalid_duration_and_worker_count_do_not_launch(self):
        for duration in (0, -1, 31, True, "10", float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                self.controller.start(duration, 1)
        for workers in (0, -1, 3, True, 1.5, "1"):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                self.controller.start(10, workers)
        self.launch.assert_not_called()

    def test_limits_remain_capped_when_configuration_is_increased(self):
        with patch.dict(stress.STRESS_TEST_CONFIG, max_duration_seconds=1000, max_workers=100):
            self.assertEqual(self.controller.limits["max_duration_seconds"], 30)
            self.assertEqual(self.controller.limits["max_workers"], 2)
        with patch("stress.os.cpu_count", return_value=1):
            self.assertEqual(self.controller.limits["max_workers"], 1)

    def test_worker_failure_stops_and_reaps_other_workers(self):
        self.controller.start(10, 2)
        self.processes[0].returncode = 1
        result = self.controller.status()
        self.assertEqual(result["status"], "failed")
        self.assertIn("Worker failure", result["error"])
        self.assertFalse(result["active"])
        self.assertTrue(self.processes[1].terminated and self.processes[1].waited)

    def test_early_successful_exit_is_still_unexpected(self):
        self.controller.start(10, 1)
        self.processes[0].returncode = 0
        result = self.controller.status()
        self.assertEqual(result["status"], "failed")
        self.assertIn("before the requested duration", result["error"])

    def test_natural_completion_is_reaped_without_termination(self):
        self.controller.start(10, 1)
        self.processes[0].returncode = 0
        self.now = 110
        self.assertEqual(self.controller.status()["status"], "finished")
        self.assertTrue(self.processes[0].waited)
        self.assertFalse(self.processes[0].terminated)

    def test_partial_start_failure_cleans_up_already_started_worker(self):
        first = self.make_process()
        self.launch.side_effect = [first, OSError("spawn failed")]
        result = self.controller.start(10, 2)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["active"])
        self.assertTrue(first.terminated and first.waited)

    def test_unresponsive_worker_is_killed_and_reaped(self):
        process = FakeProcess(1000, ignore_terminate=True)
        self.launch.side_effect = None
        self.launch.return_value = process
        self.controller.start(10, 1)
        result = self.controller.stop()
        self.assertFalse(result["active"])
        self.assertTrue(process.terminated and process.killed and process.waited)

    def test_cleanup_failure_retains_ownership_and_blocks_new_test(self):
        process = FakeProcess(1000, unkillable=True)
        self.launch.side_effect = None
        self.launch.return_value = process
        self.controller.start(10, 1)
        result = self.controller.stop()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["active"])
        self.assertIn("Cleanup incomplete", result["error"])
        with self.assertRaises(RuntimeError):
            self.controller.start(10, 1)
        process.returncode = 0  # Allow fixture cleanup without losing ownership.

    def test_supervisor_start_failure_is_cleaned_up_and_close_is_safe(self):
        self.thread.return_value.start.side_effect = RuntimeError("thread unavailable")
        self.thread.return_value.is_alive.return_value = False
        result = self.controller.start(10, 1)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["active"])
        self.controller.close()

    def test_old_supervisor_cannot_stop_a_new_run(self):
        self.controller.start(10, 1)
        previous_run = self.controller._run_id
        self.controller.stop()
        self.controller.start(10, 1)
        self.controller._supervise(previous_run, Event())
        self.assertEqual(self.controller.status()["status"], "running")
        self.assertFalse(self.processes[-1].terminated)

    def test_dashboard_sessions_share_one_lazy_controller(self):
        with patch("stress._controller", None), patch("stress.StressTestController") as factory:
            self.assertIs(stress.get_stress_controller(), stress.get_stress_controller())
            factory.assert_called_once_with()


class WorkerSafeguardTests(unittest.TestCase):
    def test_worker_has_an_independent_hard_exit_watchdog(self):
        with patch("stress.monotonic", side_effect=[100, 100, 100.1]), \
             patch("stress.Timer") as timer:
            _cpu_worker(100.05)
            self.assertAlmostEqual(timer.call_args.args[0], 0.3)
            self.assertIs(timer.call_args.args[1], stress.os._exit)
            self.assertEqual(timer.call_args.kwargs["args"], (124,))
            timer.return_value.start.assert_called_once()
            timer.return_value.cancel.assert_called_once()

    def test_expired_and_invalid_deadlines_do_not_start_a_workload(self):
        with patch("stress.monotonic", return_value=100), patch("stress.Timer") as timer:
            _cpu_worker(99)
            for deadline in (float("inf"), float("nan"), 131):
                with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                    _cpu_worker(deadline)
            timer.assert_not_called()
