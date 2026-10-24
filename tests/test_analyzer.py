import unittest

from analyzer import assess_bottlenecks


def make_metrics(
    cpu=30.0,
    memory_percent=50.0,
    available_percent=50.0,
    swap_percent=0.0,
):
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


def make_history(cpu_values, swap_values=None):
    if swap_values is None:
        swap_values = [0.0] * len(cpu_values)
    return [
        {
            "CPU (%)": cpu,
            "Swap (%)": swap,
            "Available RAM (%)": 50.0,
        }
        for cpu, swap in zip(cpu_values, swap_values)
    ]


class BottleneckAssessmentTests(unittest.TestCase):
    def test_sustained_cpu_pressure(self):
        result = assess_bottlenecks(
            make_metrics(cpu=90.0),
            make_history([82.0, 85.0, 90.0]),
        )
        self.assertEqual(result["cpu"]["status"], "Possible CPU pressure")
        self.assertTrue(any("3 consecutive samples" in item for item in result["cpu"]["evidence"]))
        self.assertIn("does not confirm", result["cpu"]["limitation"])

    def test_transient_cpu_spike_is_not_called_sustained_pressure(self):
        result = assess_bottlenecks(
            make_metrics(cpu=98.0),
            make_history([40.0, 45.0, 98.0]),
        )
        self.assertEqual(result["cpu"]["status"], "Possible transient CPU pressure")
        self.assertNotEqual(result["cpu"]["status"], "Possible CPU pressure")

    def test_low_available_memory_with_high_ram_usage(self):
        result = assess_bottlenecks(
            make_metrics(memory_percent=94.0, available_percent=8.0),
            make_history([30.0, 35.0, 32.0]),
        )
        self.assertEqual(result["memory"]["status"], "Possible memory pressure")
        self.assertTrue(any("Available RAM is 8.0%" in item for item in result["memory"]["evidence"]))

    def test_low_memory_with_increasing_swap_is_corroborated(self):
        result = assess_bottlenecks(
            make_metrics(memory_percent=82.0, available_percent=15.0, swap_percent=55.0),
            make_history([30.0, 32.0, 34.0], [30.0, 40.0, 55.0]),
        )
        self.assertEqual(result["memory"]["status"], "Possible memory pressure")
        self.assertTrue(any("swap utilization increased" in item for item in result["memory"]["evidence"]))

    def test_normal_readings_show_no_significant_pressure(self):
        result = assess_bottlenecks(
            make_metrics(),
            make_history([30.0, 35.0, 32.0]),
        )
        self.assertEqual(result["cpu"]["status"], "No sustained CPU pressure detected")
        self.assertEqual(result["memory"]["status"], "No significant memory pressure detected")

    def test_insufficient_cpu_samples_and_invalid_memory_metrics(self):
        metrics = make_metrics(cpu=30.0)
        metrics["memory"]["total"] = 0
        result = assess_bottlenecks(metrics, make_history([30.0]))
        self.assertEqual(result["cpu"]["status"], "Insufficient evidence")
        self.assertEqual(result["memory"]["status"], "Insufficient evidence")


if __name__ == "__main__":
    unittest.main()
