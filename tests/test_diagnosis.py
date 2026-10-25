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
    def test_low_ram_only_duration_and_recovery(self):
        current = snapshot(available=150, memory_percent=85)
        history = [
            {"CPU (%)": 30, "Available RAM (%)": available, "Swap (%)": 0}
            for available in (50, 15, 15, 15)
        ]
        memory = build_diagnoses(current, {}, assess_bottlenecks(current, history), history)[1]
        self.assertEqual(memory["status_code"], "low_available_memory")
        self.assertEqual(memory["duration_seconds"], 4)
        self.assertEqual(memory["duration_scope"], "low_available_memory")
        current["memory"]["available"] = 500
        history.append({"CPU (%)": 30, "Available RAM (%)": 50, "Swap (%)": 0})
        memory = build_diagnoses(current, {}, assess_bottlenecks(current, history), history)[1]
        self.assertIsNone(memory["duration_seconds"])
        self.assertIsNone(memory["duration_scope"])

    def test_null_empty_missing_and_valid_process_lists(self):
        for value in (None, [], [{"pid": 42, "name": "worker"}]):
            with self.subTest(value=value):
                current = snapshot(cpu=90, available=50, memory_percent=95)
                current.update(top_cpu=value, top_memory=value)
                result = build_diagnoses(
                    current, {}, assess_bottlenecks(current, []), []
                )
                for diagnosis in result:
                    self.assertEqual(diagnosis["observed_processes"], value or [])
        current.pop("top_cpu")
        current.pop("top_memory")
        for diagnosis in build_diagnoses(current, {}, assess_bottlenecks(current, [])):
            self.assertEqual(diagnosis["observed_processes"], [])

    def test_unknown_status_is_not_no_pressure_and_uses_safe_guidance(self):
        from recommendations import build_recommendations

        diagnosis = build_diagnoses({}, {}, {"cpu": {"status": "New status"}})[0]
        self.assertEqual(diagnosis["status"], "New status")
        self.assertEqual(diagnosis["status_code"], "unknown")
        self.assertEqual(diagnosis["evidence_status"], "unknown")
        self.assertEqual(build_recommendations([diagnosis])[0]["priority"], "Informational")

    def test_memory_duration_labels_low_ram_before_corroboration(self):
        current = snapshot(available=150, memory_percent=85, swap_percent=60)
        history = [
            {"CPU (%)": 30, "Available RAM (%)": 15, "Swap (%)": swap}
            for swap in (0, 0, 60)
        ]
        memory = build_diagnoses(
            current, {}, assess_bottlenecks(current, history), history
        )[1]
        self.assertEqual(memory["status_code"], "possible_corroborated_pressure")
        self.assertEqual(memory["duration_seconds"], 4)
        self.assertEqual(memory["duration_scope"], "low_available_memory")

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
