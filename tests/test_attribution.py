import unittest

from attribution import assess_process_attribution, pearson_correlation


def make_snapshot(
    index,
    system_cpu,
    process_cpu,
    rss_mb,
    available_mb,
    swap_mb,
    pid=100,
    create_time=12345.0,
):
    total_bytes = 1000 * 1024 * 1024
    available_bytes = available_mb * 1024 * 1024
    swap_bytes = swap_mb * 1024 * 1024
    process = {
        "pid": pid,
        "create_time": create_time,
        "name": "test-process",
        "cpu_percent": process_cpu,
        "memory_rss": rss_mb * 1024 * 1024,
        "memory_percent": rss_mb / 10,
        "status": "running",
    }
    return {
        "sampled_at": f"sample-{index}",
        "cpu_percent": system_cpu,
        "memory": {
            "total": total_bytes,
            "used": total_bytes - available_bytes,
            "available": available_bytes,
            "percent": 100 - available_mb / 10,
        },
        "swap": {
            "total": total_bytes,
            "used": swap_bytes,
            "percent": swap_mb / 10,
        },
        "running_process_count": 1,
        "processes": [process],
        "top_cpu": [process],
        "top_memory": [process],
    }


class PearsonCorrelationTests(unittest.TestCase):
    def test_perfect_positive_correlation(self):
        self.assertAlmostEqual(
            pearson_correlation([1, 2, 3], [2, 4, 6], minimum_samples=3),
            1.0,
        )

    def test_perfect_negative_correlation(self):
        self.assertAlmostEqual(
            pearson_correlation([1, 2, 3], [6, 4, 2], minimum_samples=3),
            -1.0,
        )

    def test_insufficient_and_constant_series_are_undefined(self):
        self.assertIsNone(pearson_correlation([1, 2], [2, 4], minimum_samples=3))
        self.assertIsNone(pearson_correlation([1, 1, 1], [2, 4, 6], minimum_samples=3))


class ProcessAttributionTests(unittest.TestCase):
    def test_insufficient_history_is_reported(self):
        result = assess_process_attribution(
            [make_snapshot(0, 10, 4, 100, 800, 0) for _ in range(5)]
        )
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["window_samples"], 5)
        self.assertEqual(result["cpu_findings"], [])

    def test_attribution_analyzes_sufficient_history(self):
        history = [
            make_snapshot(
                index=i,
                system_cpu=20 + i * 2,
                process_cpu=5 + i,
                rss_mb=100 + 2 * i * i,
                available_mb=800 - 2 * i * i,
                swap_mb=2 * i * i,
            )
            for i in range(20)
        ]
        result = assess_process_attribution(history)
        self.assertEqual(result["status"], "analyzed")
        self.assertEqual(result["window_samples"], 20)
        self.assertTrue(result["cpu_findings"])
        self.assertTrue(result["memory_findings"])
        self.assertTrue(result["swap_findings"])
        self.assertGreater(result["cpu_findings"][0]["correlation"], 0.9)
        self.assertLess(result["memory_findings"][0]["correlation"], -0.9)
        self.assertGreater(result["swap_findings"][0]["correlation"], 0.9)

    def test_process_instances_are_separated_by_creation_time(self):
        history = [
            make_snapshot(
                i,
                30 + i,
                10 + i,
                100 + i,
                800 - i,
                i,
                create_time=12345.0 if i < 10 else 54321.0,
            )
            for i in range(20)
        ]
        result = assess_process_attribution(history)
        self.assertEqual(len(result["cpu_findings"]), 2)
        self.assertEqual(
            {finding["create_time"] for finding in result["cpu_findings"]},
            {12345.0, 54321.0},
        )

    def test_pressure_context_and_supporting_overlap_are_reported(self):
        history = [
            make_snapshot(
                i,
                85 + i,
                20 + i,
                100 + 2 * i * i,
                150 - i * 5,
                60 + i,
            )
            for i in range(20)
        ]
        result = assess_process_attribution(history)
        self.assertEqual(result["pressure_context"]["cpu_pressure_samples"], 20)
        self.assertGreater(result["pressure_context"]["memory_pressure_samples"], 0)
        finding = result["cpu_findings"][0]
        self.assertIn("top_pressure_samples", finding)
        self.assertIn("pressure_overlap_percent", finding)
        self.assertIn("sample_count", finding)
        self.assertTrue(result["limitations"])

    def test_pressure_overlap_is_not_applicable_when_no_pressure_was_observed(self):
        history = [
            make_snapshot(i, 20 + i, 3 + i, 100 + 2 * i, 700 - i, 10 + i)
            for i in range(20)
        ]
        result = assess_process_attribution(history)
        finding = result["cpu_findings"][0]

        self.assertEqual(result["pressure_context"]["cpu_pressure_samples"], 0)
        self.assertEqual(finding["pressure_sample_count"], 0)
        self.assertIsNone(finding["pressure_overlap_percent"])
        self.assertIn("overlap with pressure is not applicable", finding["evidence"])


if __name__ == "__main__":
    unittest.main()
