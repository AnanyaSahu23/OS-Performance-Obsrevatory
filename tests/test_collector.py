import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from collector import collect_snapshot, get_top_processes


def make_process(pid, name, cpu, memory_percent, rss, status=psutil.STATUS_SLEEPING):
    process = SimpleNamespace(
        info={
            "pid": pid,
            "create_time": 1000.0 + pid,
            "name": name,
            "memory_percent": memory_percent,
            "memory_info": SimpleNamespace(rss=rss),
            "status": status,
        },
        cpu_percent=lambda interval=None: cpu,
    )
    return process


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.processes = [
            make_process(10, "cpu-heavy", 75.0, 5.0, 100, psutil.STATUS_RUNNING),
            make_process(20, "memory-heavy", 10.0, 20.0, 500),
            make_process(30, "small", 2.0, 1.0, 50, psutil.STATUS_RUNNING),
        ]

    @patch("collector.psutil.cpu_percent", return_value=42.5)
    @patch("collector.psutil.virtual_memory")
    @patch("collector.psutil.swap_memory")
    @patch("collector.psutil.process_iter")
    def test_snapshot_contains_metrics_and_ranked_processes(
        self, process_iter, swap_memory, virtual_memory, cpu_percent
    ):
        process_iter.return_value = iter(self.processes)
        virtual_memory.return_value = SimpleNamespace(
            total=1000, used=600, available=400, percent=60.0
        )
        swap_memory.return_value = SimpleNamespace(total=500, used=50, percent=10.0)

        snapshot = collect_snapshot(limit=2)

        self.assertTrue(snapshot["sampled_at"].endswith("+00:00"))
        self.assertEqual(snapshot["cpu_percent"], 42.5)
        self.assertEqual(snapshot["memory"]["available"], 400)
        self.assertEqual(snapshot["swap"]["percent"], 10.0)
        self.assertEqual(snapshot["running_process_count"], 2)
        self.assertEqual(len(snapshot["processes"]), 3)
        self.assertEqual(snapshot["processes"][0]["create_time"], 1010.0)
        self.assertIn("memory_rss", snapshot["processes"][0])
        self.assertEqual([p["pid"] for p in snapshot["top_cpu"]], [10, 20])
        self.assertEqual([p["pid"] for p in snapshot["top_memory"]], [20, 10])

    @patch("collector.psutil.process_iter")
    def test_processes_that_disappear_are_skipped(self, process_iter):
        disappearing_process = SimpleNamespace(
            info={"pid": 99, "name": "gone", "memory_percent": 0, "status": "running"},
            cpu_percent=lambda interval=None: (_ for _ in ()).throw(
                psutil.NoSuchProcess(99)
            ),
        )
        process_iter.return_value = iter([disappearing_process, self.processes[0]])

        ranked = get_top_processes()

        self.assertEqual([p["pid"] for p in ranked["top_cpu"]], [10])
        self.assertEqual([p["pid"] for p in ranked["top_memory"]], [10])

    def test_limit_must_be_positive(self):
        with self.assertRaises(ValueError):
            get_top_processes(limit=0)

    @patch("collector.psutil.cpu_percent", return_value=42.5)
    @patch("collector.psutil.virtual_memory")
    @patch("collector.psutil.swap_memory", side_effect=RuntimeError("counter unavailable"))
    @patch("collector.psutil.process_iter")
    def test_unavailable_swap_does_not_discard_other_metrics(
        self, process_iter, swap_memory, virtual_memory, cpu_percent
    ):
        process_iter.return_value = iter(self.processes)
        virtual_memory.return_value = SimpleNamespace(
            total=1000, used=600, available=400, percent=60.0
        )

        snapshot = collect_snapshot()

        self.assertEqual(snapshot["cpu_percent"], 42.5)
        self.assertEqual(snapshot["memory"]["percent"], 60.0)
        self.assertEqual(
            snapshot["swap"], {"total": None, "used": None, "percent": None}
        )


if __name__ == "__main__":
    unittest.main()
