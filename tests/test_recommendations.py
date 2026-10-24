import unittest

from recommendations import build_recommendations


class RecommendationTests(unittest.TestCase):
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
