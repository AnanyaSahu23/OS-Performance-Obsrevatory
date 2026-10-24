import unittest
from datetime import datetime, timedelta, timezone

from analyzer import assess_bottlenecks
from diagnosis import build_diagnoses
from health import evaluate_health


def snapshot(cpu=50.0, available=800, memory_percent=20.0, swap_percent=0.0):
    return {
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "cpu_percent": cpu,
        "memory": {
            "total": 1000,
            "used": 1000 - available,
            "available": available,
            "percent": memory_percent,
        },
        "swap": {"total": 1000, "used": swap_percent * 10, "percent": swap_percent},
        "running_process_count": 1,
        "top_cpu": [{"pid": 12, "name": "worker", "cpu_percent": cpu}],
        "top_memory": [{"pid": 12, "name": "worker", "memory_rss": 700}],
    }


class DiagnosisTests(unittest.TestCase):
    def test_sustained_cpu_finding_has_evidence_duration_and_process_context(self):
        now = datetime.now(timezone.utc)
        history = [
            {
                "sampled_at": (now - timedelta(seconds=4 - 2 * i)).isoformat(),
                "CPU (%)": 85.0,
                "Available RAM (%)": 80.0,
                "Swap (%)": 0.0,
            }
            for i in range(3)
        ]
        current = snapshot(cpu=85.0)
        assessment = assess_bottlenecks(current, history)
        health = evaluate_health(current, history)

        result = build_diagnoses(current, health, assessment, history)
        cpu = next(item for item in result if item["condition"] == "cpu_pressure")

        self.assertEqual(cpu["status_code"], "possible_sustained_pressure")
        self.assertEqual(cpu["severity"], "warning")
        self.assertEqual(cpu["evidence_status"], "corroborated_resource_indicators")
        self.assertEqual(cpu["observed_processes"][0]["pid"], 12)
        self.assertEqual(cpu["duration_seconds"], 4.0)
        self.assertIn("not proof", cpu["interpretation"])

    def test_insufficient_samples_do_not_invent_duration_or_process_cause(self):
        current = snapshot(cpu=30.0)
        history = [{"CPU (%)": 30.0, "Available RAM (%)": 80.0, "Swap (%)": 0.0}]
        result = build_diagnoses(
            current,
            evaluate_health(current, history),
            assess_bottlenecks(current, history),
            history,
        )
        cpu = next(item for item in result if item["condition"] == "cpu_pressure")

        self.assertEqual(cpu["status_code"], "insufficient_evidence")
        self.assertIsNone(cpu["duration_seconds"])
        self.assertEqual(cpu["observed_processes"], [])

    def test_critical_memory_condition_uses_memory_severity(self):
        current = snapshot(available=50, memory_percent=97.0, swap_percent=85.0)
        history = [
            {"CPU (%)": 30.0, "Available RAM (%)": 5.0, "Swap (%)": 80.0}
            for _ in range(3)
        ]
        result = build_diagnoses(
            current,
            evaluate_health(current, history),
            assess_bottlenecks(current, history),
            history,
        )
        memory = next(item for item in result if item["condition"] == "memory_pressure")

        self.assertEqual(memory["status_code"], "possible_corroborated_pressure")
        self.assertEqual(memory["severity"], "critical")
        self.assertEqual(memory["observed_processes"][0]["pid"], 12)


if __name__ == "__main__":
    unittest.main()
