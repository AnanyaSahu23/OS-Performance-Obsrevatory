import unittest

from recommendations import build_recommendations
from config import DEFAULT_RECOMMENDATION, RECOMMENDATION_RULES


class RecommendationTests(unittest.TestCase):
    def test_specialized_rule_precedes_generic_custom_rule(self):
        generic = {"priority": "Generic", "message": "generic override"}
        specialized = {"priority": "Specialized", "message": "swap override"}
        for evidence, status in (("increasing", "low_memory_increasing_swap"),
                                 ("elevated", "low_memory_elevated_swap")):
            with self.subTest(evidence=evidence):
                diagnosis = {"condition": "memory_pressure",
                             "status_code": "possible_corroborated_pressure",
                             "swap_evidence": [evidence]}
                rules = {("memory_pressure", "possible_corroborated_pressure"): generic,
                         ("memory_pressure", status): specialized}
                self.assertEqual(build_recommendations([diagnosis], rules)[0]["message"], "swap override")

    def test_generic_custom_rule_survives_swap_specialization(self):
        rules = {("memory_pressure", "possible_corroborated_pressure"):
                 {"priority": "Custom", "message": "configured generic guidance"}}
        for evidence in (["increasing"], ["elevated"], [], None):
            with self.subTest(evidence=evidence):
                diagnosis = {"condition": "memory_pressure",
                             "status_code": "possible_corroborated_pressure", "swap_evidence": evidence}
                result = build_recommendations([diagnosis], rules)[0]
                self.assertEqual(result["message"], "configured generic guidance")
                self.assertEqual(result["priority"], "Custom")
                self.assertEqual(diagnosis["status_code"], "possible_corroborated_pressure")

    def test_missing_specialized_and_generic_rules_use_safe_default(self):
        diagnosis = {"condition": "memory_pressure", "status_code": "possible_corroborated_pressure",
                     "swap_evidence": ["increasing"]}
        result = build_recommendations([diagnosis], {})[0]
        self.assertEqual(result["message"], DEFAULT_RECOMMENDATION["message"])
        self.assertIn("no process or system setting is changed", result["safety"])

    def test_existing_configured_mappings_remain_unchanged(self):
        for (condition, status), rule in RECOMMENDATION_RULES.items():
            with self.subTest(condition=condition, status=status):
                result = build_recommendations([{"condition": condition, "status_code": status}])[0]
                self.assertEqual(result["message"], rule["message"])
                self.assertEqual(result["priority"], rule["priority"])

    def test_swap_guidance_consumes_real_diagnosis_context(self):
        from analyzer import assess_bottlenecks
        from diagnosis import build_diagnoses

        for swaps, expected in (
            ([10, 20, 30], "swap usage is increasing"),
            ([60, 60, 60], "swap usage is elevated"),
            ([None, None, None], "Collect valid RAM and swap"),
            ([0, 0, 0], "Review memory use"),
        ):
            with self.subTest(swaps=swaps):
                current = {
                    "cpu_percent": 30,
                    "memory": {"total": 1000, "available": 150, "percent": 95},
                    "swap": {"percent": swaps[-1]},
                }
                history = [{"CPU (%)": 30, "Swap (%)": swap} for swap in swaps]
                diagnoses = build_diagnoses(current, {}, assess_bottlenecks(current, history), history)
                guidance = build_recommendations(diagnoses)[1]
                self.assertIn(expected, guidance["message"])
                self.assertIn("no process or system setting is changed", guidance["safety"])

    def test_selects_condition_and_status_specific_rule(self):
        diagnoses = [
            {
                "condition": "cpu_pressure",
                "status_code": "possible_sustained_pressure",
            }
        ]

        result = build_recommendations(diagnoses)[0]

        self.assertEqual(result["priority"], "Review")
        self.assertIn("top CPU-consuming", result["message"])
        self.assertIn("Informational only", result["safety"])

    def test_no_pressure_gets_monitoring_guidance(self):
        diagnosis = {
            "condition": "cpu_pressure",
            "status_code": "no_persistent_pressure",
        }

        result = build_recommendations([diagnosis])[0]

        self.assertEqual(result["priority"], "Informational")
        self.assertIn("No persistent CPU pressure", result["message"])

    def test_rules_can_be_overridden_without_changing_engine(self):
        diagnosis = {"condition": "cpu_pressure", "status_code": "custom_rule"}
        rules = {
            ("cpu_pressure", "custom_rule"): {
                "priority": "Custom",
                "message": "Configured message",
            }
        }

        result = build_recommendations([diagnosis], rules)[0]

        self.assertEqual(result["priority"], "Custom")
        self.assertEqual(result["message"], "Configured message")


if __name__ == "__main__":
    unittest.main()
