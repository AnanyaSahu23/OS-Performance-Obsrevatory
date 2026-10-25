import unittest

from health import evaluate_health


def make_metrics(cpu=30.0, memory_percent=50.0, available_percent=50.0, swap_percent=0.0):
    total_ram = 1000
    available_ram = total_ram * available_percent / 100
    return {
        "cpu_percent": cpu,
        "memory": {
            "total": total_ram,
            "used": total_ram - available_ram,
            "available": available_ram,
            "percent": memory_percent,
        },
        "swap": {
            "total": 1000,
            "used": swap_percent * 10,
            "percent": swap_percent,
        },
    }


def make_history(cpu_values):
    return [{"CPU (%)": value} for value in cpu_values]


class TestHealth(unittest.TestCase):
    def test_invalid_required_ram_readings_are_unknown(self):
        for field, values in {
            "total": [None, 0, -1, True, float("nan"), float("inf")],
            "available": [None, -1, 1001, True, float("nan"), float("inf")],
            "percent": [None, -1, 101, True, float("nan"), float("inf")],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    metrics = make_metrics()
                    metrics["memory"][field] = value
                    self.assertEqual(evaluate_health(metrics, [])["status"], "Unknown")
            metrics = make_metrics()
            del metrics["memory"][field]
            self.assertEqual(evaluate_health(metrics, [])["status"], "Unknown")
        for value in (None, [], "invalid"):
            self.assertEqual(evaluate_health({"memory": value}, [])["status"], "Unknown")

    def test_invalid_optional_swap_is_unavailable(self):
        for value in (float("nan"), float("inf"), -1, 101, True):
            with self.subTest(value=value):
                metrics = make_metrics(swap_percent=value)
                result = evaluate_health(metrics, [])
                self.assertEqual(result["status"], "Healthy")
                self.assertIsNone(result["indicators"]["Swap utilization"])

    def test_healthy_status(self):
        metrics = make_metrics(cpu=30.0, memory_percent=50.0, available_percent=50.0)
        history = make_history([30, 35, 32])
        result = evaluate_health(metrics, history)
        self.assertEqual(result["status"], "Healthy")

    def test_warning_status_for_memory_pressure(self):
        metrics = make_metrics(cpu=45.0, memory_percent=92.0, available_percent=18.0, swap_percent=20.0)
        result = evaluate_health(metrics, make_history([35, 40, 45]))
        self.assertEqual(result["status"], "Warning")
        self.assertTrue(any("RAM utilization" in reason for reason in result["reasons"]))

    def test_critical_status_for_memory_pressure(self):
        metrics = make_metrics(cpu=60.0, memory_percent=97.0, available_percent=8.0, swap_percent=25.0)
        result = evaluate_health(metrics, make_history([50, 52, 55]))
        self.assertEqual(result["status"], "Critical")

    def test_sustained_cpu_pressure_triggers_warning(self):
        metrics = make_metrics(cpu=85.0, memory_percent=40.0, available_percent=60.0)
        result = evaluate_health(metrics, make_history([82, 84, 85]))
        self.assertEqual(result["status"], "Warning")
        self.assertTrue(any("CPU utilization" in reason for reason in result["reasons"]))

    def test_single_cpu_spike_does_not_trigger_critical_status(self):
        metrics = make_metrics(cpu=98.0, memory_percent=40.0, available_percent=60.0)
        result = evaluate_health(metrics, make_history([98]))
        self.assertEqual(result["status"], "Healthy")

    def test_missing_ram_data_returns_unknown(self):
        metrics = {
            "cpu_percent": 50.0,
            "memory": {"total": 0, "used": 0, "available": 0, "percent": 0},
            "swap": {"total": 0, "used": 0, "percent": 0},
        }
        result = evaluate_health(metrics, [])
        self.assertEqual(result["status"], "Unknown")
        self.assertIn("RAM information is unavailable.", result["reasons"])

    def test_unavailable_swap_is_not_misreported_as_zero(self):
        metrics = make_metrics()
        metrics["swap"] = {"total": None, "used": None, "percent": None}

        result = evaluate_health(metrics, make_history([30, 35, 32]))

        self.assertEqual(result["status"], "Healthy")
        self.assertIsNone(result["indicators"]["Swap utilization"])


if __name__ == "__main__":
    unittest.main()
