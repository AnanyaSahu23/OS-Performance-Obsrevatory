import math
import unittest
from datetime import datetime, timedelta, timezone

from analyzer import assess_bottlenecks
from baseline import detect_anomalies
from health import evaluate_health


SETTINGS = {"window_size": 5, "minimum_samples": 3,
            "anomaly_threshold": 3.0, "minimum_stddev": 0.5}
BASE_TIME = datetime(2026, 10, 25, tzinfo=timezone.utc)


def stamp(seconds):
    return (BASE_TIME + timedelta(seconds=seconds)).isoformat()


def snapshot(cpu=10, memory=40):
    return {"sampled_at": stamp(100), "cpu_percent": cpu,
            "memory": {"total": 1000, "used": memory * 10,
                       "available": 1000 - memory * 10, "percent": memory},
            "swap": {"total": 500, "used": 0, "percent": 0}}


def observations(values):
    return [{"sampled_at": stamp(index), "cpu_percent": value,
             "memory_percent": value + 30 if isinstance(value, (int, float)) else None}
            for index, value in enumerate(values)]


class BaselineTests(unittest.TestCase):
    def test_normal_values_have_explainable_population_statistics(self):
        results = detect_anomalies(snapshot(), observations([8, 9, 10, 11, 12]), SETTINGS)
        for resource, mean in (("cpu", 10), ("memory", 40)):
            finding = results[resource]
            self.assertEqual(finding["status"], "normal")
            self.assertFalse(finding["is_anomaly"])
            self.assertEqual(finding["sample_count"], 5)
            self.assertEqual(finding["baseline_mean"], mean)
            self.assertAlmostEqual(finding["baseline_stddev"], math.sqrt(2))
            self.assertEqual(finding["anomaly_score"], 0)

    def test_sudden_increases_below_fixed_thresholds_do_not_change_health(self):
        current = snapshot(cpu=30, memory=60)
        prior = observations([8, 9, 10, 11, 12])
        fixed_history = [{"CPU (%)": item["cpu_percent"], "Swap (%)": 0} for item in prior]
        health = evaluate_health(current, fixed_history)
        assessment = assess_bottlenecks(current, fixed_history)
        results = detect_anomalies(current, prior, SETTINGS)
        self.assertEqual(health["status"], "Healthy")
        self.assertEqual(assessment["cpu"]["status"], "No sustained CPU pressure detected")
        self.assertEqual(assessment["memory"]["status"], "No significant memory pressure detected")
        for finding in results.values():
            self.assertEqual(finding["status"], "unusual")
            self.assertGreater(finding["anomaly_score"], 3)
        self.assertEqual(evaluate_health(current, fixed_history), health)
        self.assertEqual(assess_bottlenecks(current, fixed_history), assessment)

    def test_insufficient_and_empty_history_do_not_claim_normality(self):
        for prior in ([], observations([10, 11])):
            with self.subTest(count=len(prior)):
                results = detect_anomalies(snapshot(), prior, SETTINGS)
                for finding in results.values():
                    self.assertEqual(finding["status"], "insufficient_history")
                    self.assertIsNone(finding["is_anomaly"])
                    self.assertIsNone(finding["anomaly_score"])
                    self.assertEqual(finding["sample_count"], len(prior))

    def test_invalid_current_metrics_are_unavailable(self):
        for invalid in (None, True, "invalid", float("nan"), float("inf"), -1, 101):
            with self.subTest(value=invalid):
                current = snapshot()
                current["cpu_percent"] = invalid
                current["memory"]["percent"] = invalid
                for finding in detect_anomalies(current, observations([10] * 5), SETTINGS).values():
                    self.assertEqual(finding["status"], "unavailable")
                    self.assertIsNone(finding["current_value"])
                    self.assertIsNone(finding["anomaly_score"])

    def test_missing_memory_and_invalid_historical_values_are_independent(self):
        prior = observations([10] * 5)
        for item, invalid in zip(prior, (None, True, "bad", float("nan"), 101)):
            item["cpu_percent"] = invalid
        results = detect_anomalies(snapshot(), prior, SETTINGS)
        self.assertEqual(results["cpu"]["status"], "insufficient_history")
        self.assertEqual(results["cpu"]["sample_count"], 0)
        self.assertEqual(results["memory"]["status"], "normal")
        current = snapshot()
        current.pop("memory")
        results = detect_anomalies(current, observations([10] * 5), SETTINGS)
        self.assertEqual(results["memory"]["status"], "unavailable")
        self.assertEqual(results["cpu"]["status"], "normal")

    def test_constant_and_near_constant_history_use_a_finite_deviation_floor(self):
        for values in ([10] * 5, [10, 10 + 1e-10, 10, 10 - 1e-10, 10]):
            with self.subTest(values=values):
                normal = detect_anomalies(snapshot(), observations(values), SETTINGS)["cpu"]
                unusual = detect_anomalies(snapshot(cpu=20), observations(values), SETTINGS)["cpu"]
                self.assertEqual(normal["status"], "normal")
                self.assertEqual(unusual["status"], "unusual")
                self.assertEqual(unusual["effective_stddev"], 0.5)
                self.assertTrue(math.isfinite(unusual["anomaly_score"]))
                self.assertIn("deviation floor", unusual["explanation"])

    def test_lower_utilization_is_not_a_high_side_anomaly(self):
        result = detect_anomalies(snapshot(cpu=0), observations([50] * 5), SETTINGS)["cpu"]
        self.assertEqual(result["status"], "normal")
        self.assertLess(result["anomaly_score"], 0)

    def test_latest_window_is_chronological_and_does_not_backfill_invalid_values(self):
        prior = observations([0, 0, 10, 10, 10, 10, 10])
        result = detect_anomalies(snapshot(), list(reversed(prior)), SETTINGS)["cpu"]
        self.assertEqual(result["baseline_mean"], 10)
        self.assertEqual(result["sample_count"], 5)
        for item in prior[-4:]:
            item["cpu_percent"] = None
        result = detect_anomalies(snapshot(), prior, SETTINGS)["cpu"]
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["status"], "insufficient_history")

    def test_current_equivalent_and_future_timestamps_are_excluded(self):
        prior = observations([8, 9, 10, 11, 12])
        prior += [{"sampled_at": value, "cpu_percent": 99, "memory_percent": 99}
                  for value in (stamp(100), stamp(101),
                                (BASE_TIME + timedelta(seconds=100)).astimezone(
                                    timezone(timedelta(hours=5, minutes=30))).isoformat())]
        result = detect_anomalies(snapshot(), prior, SETTINGS)["cpu"]
        self.assertEqual(result["sample_count"], 5)
        self.assertEqual(result["baseline_mean"], 10)

    def test_invalid_timestamps_and_non_sample_rows_are_ignored(self):
        prior = observations([8, 9, 10]) + [None, {}, {"sampled_at": "bad", "cpu_percent": 99}]
        self.assertEqual(detect_anomalies(snapshot(), prior, SETTINGS)["cpu"]["sample_count"], 3)
        current = snapshot()
        current.pop("sampled_at")
        self.assertEqual(detect_anomalies(current, prior, SETTINGS)["cpu"]["status"], "unavailable")

    def test_threshold_and_default_warmup_are_configurable(self):
        prior = observations([8, 9, 10, 11, 12])
        self.assertEqual(detect_anomalies(snapshot(cpu=14), prior, SETTINGS)["cpu"]["status"], "normal")
        custom = dict(SETTINGS, anomaly_threshold=2)
        self.assertEqual(detect_anomalies(snapshot(cpu=14), prior, custom)["cpu"]["status"], "unusual")
        result = detect_anomalies(snapshot(cpu=20), observations([10] * 15))["cpu"]
        self.assertEqual(result["window_size"], 60)
        self.assertEqual(result["minimum_samples"], 15)
        self.assertEqual(result["status"], "unusual")

    def test_invalid_configuration_is_rejected(self):
        for override in ({"window_size": True}, {"minimum_samples": 1},
                         {"minimum_samples": 6}, {"anomaly_threshold": float("inf")},
                         {"anomaly_threshold": 0}, {"minimum_stddev": 0}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                detect_anomalies(snapshot(), [], dict(SETTINGS, **override))
