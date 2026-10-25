"""Bounded CPU demonstration workers; never signal processes not launched here."""

import atexit
import math
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, RLock, Thread, Timer, current_thread
from time import monotonic

from config import STRESS_TEST_CONFIG


HARD_MAX_SECONDS = 30.0
HARD_MAX_WORKERS = 2


def _cpu_worker(deadline):
    """Exit independently of the controller, even if its server disappears."""
    remaining = deadline - monotonic()
    if not math.isfinite(deadline) or remaining > HARD_MAX_SECONDS:
        raise ValueError("invalid worker deadline")
    if remaining <= 0:
        return
    watchdog = Timer(remaining + 0.25, os._exit, args=(124,))
    watchdog.daemon = True
    watchdog.start()
    try:
        value = 1
        while monotonic() < deadline:
            for _ in range(10000):
                value = (value * 1664525 + 1013904223) % 4294967296
    finally:
        watchdog.cancel()


class StressTestController:
    """Own worker handles, serialize starts/stops, and supervise without UI refresh.

    Duration includes process startup. The background thread only manages worker
    lifecycle; system metrics continue through the existing collector pipeline.
    """

    def __init__(self, *, process_factory=None, clock=None):
        self._launch = process_factory or subprocess.Popen
        self._clock = clock or monotonic
        self._lock = RLock()
        self._workers = []
        self._monitor = None
        self._stop_event = Event()
        self._run_id = 0
        self._state = "idle"
        self._error = ""
        self._started = None
        self._ended = None
        self._deadline = None
        self._duration = 0.0
        self._worker_count = 0
        self._pids = []
        atexit.register(self.close)

    @property
    def limits(self):
        return {
            "max_duration_seconds": min(HARD_MAX_SECONDS, STRESS_TEST_CONFIG["max_duration_seconds"]),
            "max_workers": min(HARD_MAX_WORKERS, STRESS_TEST_CONFIG["max_workers"], os.cpu_count() or 1),
        }

    def start(self, duration_seconds, worker_count):
        limits = self.limits
        if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float))
                or not math.isfinite(duration_seconds)
                or not 0 < duration_seconds <= limits["max_duration_seconds"]):
            raise ValueError(f"duration must be positive and at most {limits['max_duration_seconds']:g} seconds")
        if type(worker_count) is not int or not 1 <= worker_count <= limits["max_workers"]:
            raise ValueError(f"worker count must be an integer from 1 to {limits['max_workers']}")
        with self._lock:
            self._refresh_locked()
            if self._workers or self._state == "running":
                raise RuntimeError("A stress test is already active or still requires cleanup")
            self._run_id += 1
            self._duration = float(duration_seconds)
            self._worker_count = worker_count
            self._started = self._clock()
            self._ended = None
            self._deadline = self._started + self._duration
            self._state, self._error, self._pids = "running", "", []
            self._stop_event = Event()
            try:
                for _ in range(worker_count):
                    if self._clock() >= self._deadline:
                        break
                    process = self._launch(
                        [sys.executable, "-B", str(Path(__file__).resolve()),
                         "--worker", repr(self._deadline)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, shell=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    self._workers.append(process)
                    self._pids.append(process.pid)
                self._monitor = Thread(target=self._supervise,
                                       args=(self._run_id, self._stop_event), daemon=True)
                self._monitor.start()
            except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                self._finish_locked("failed", f"Could not start test: {error}")
            self._refresh_locked()
            return self._snapshot_locked()

    def _supervise(self, run_id, stop_event):
        while not stop_event.is_set():
            with self._lock:
                if run_id != self._run_id or self._state != "running":
                    return
                self._refresh_locked()
                if self._state != "running":
                    return
                remaining = max(0.0, self._deadline - self._clock())
            stop_event.wait(min(0.1, remaining))

    def _refresh_locked(self):
        if self._state != "running":
            return
        codes = [process.poll() for process in self._workers]
        if any(code not in (None, 0) for code in codes):
            self._finish_locked("failed", f"Worker failure (exit codes: {codes})")
        elif self._clock() >= self._deadline:
            self._finish_locked("finished")
        elif not self._workers or any(code is not None for code in codes):
            self._finish_locked("failed", "A worker exited before the requested duration")

    def _finish_locked(self, state, error=""):
        self._stop_event.set()
        grace = min(1.0, STRESS_TEST_CONFIG["cleanup_timeout_seconds"])
        for process in self._workers:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass  # It may have exited between poll and terminate.
        survivors = []
        for process in self._workers:
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=grace)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            except OSError:
                pass
            if process.poll() is None:
                survivors.append(process)
        self._workers = survivors
        self._ended = self._clock()
        self._state, self._error = state, error
        if survivors:
            self._state = "failed"
            self._error = (error + " Cleanup incomplete; another test cannot start.").strip()

    def _snapshot_locked(self):
        end = self._ended if self._ended is not None else self._clock()
        elapsed = max(0.0, end - self._started) if self._started is not None else 0.0
        return {
            "status": self._state,
            "active": bool(self._workers),
            "duration_seconds": self._duration,
            "worker_count": self._worker_count,
            "worker_pids": list(self._pids),
            "elapsed_seconds": min(elapsed, self._duration),
            "remaining_seconds": max(0.0, self._deadline - self._clock()) if self._state == "running" else 0.0,
            "error": self._error,
        }

    def status(self):
        with self._lock:
            self._refresh_locked()
            return self._snapshot_locked()

    def stop(self):
        with self._lock:
            self._refresh_locked()
            if self._workers or self._state == "running":
                self._finish_locked("stopped")
            return self._snapshot_locked()

    def close(self):
        self.stop()
        monitor = self._monitor
        if monitor is not None and monitor is not current_thread() and monitor.is_alive():
            monitor.join(timeout=1.0)


_controller = None
_controller_lock = RLock()


def get_stress_controller():
    """Share one controller across reruns and sessions of this server process."""
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = StressTestController()
        return _controller


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("This module is launched by the dashboard stress-test controller.")
    _cpu_worker(float(sys.argv[2]))
