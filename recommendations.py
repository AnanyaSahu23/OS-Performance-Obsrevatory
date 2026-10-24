"""Generate safe, configurable recommendations from diagnosis records."""

from typing import Any, Dict, Iterable, List, Optional

from config import DEFAULT_RECOMMENDATION, RECOMMENDATION_RULES


def build_recommendations(
    diagnoses: Iterable[Dict[str, Any]],
    rules: Optional[Dict] = None,
) -> List[Dict[str, str]]:
    """Return one informational recommendation for each diagnosis.

    Rules are keyed by (condition, status_code), keeping the recommendation
    engine independent from display text and tunable without code changes.
    """
    configured_rules = RECOMMENDATION_RULES if rules is None else rules
    results = []
    for diagnosis in diagnoses:
        condition = diagnosis.get("condition", "unknown")
        status_code = diagnosis.get("status_code", "unknown")
        rule = configured_rules.get(
            (condition, status_code), DEFAULT_RECOMMENDATION
        )
        results.append(
            {
                "condition": condition,
                "priority": str(rule.get("priority", "Informational")),
                "message": str(rule.get("message", DEFAULT_RECOMMENDATION["message"])),
                "safety": "Informational only; no process or system setting is changed.",
            }
        )
    return results
