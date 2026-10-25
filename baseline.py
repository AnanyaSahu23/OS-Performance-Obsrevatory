"""Explain unusually high utilization relative to prior observed samples."""

import math
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any, Dict, Optional, Sequence

from config import BASELINE_CONFIG


def _percentage(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _sample_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def detect_anomalies(
    snapshot: Dict[str, Any],
    history: Sequence[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Compare CPU/RAM with the latest window strictly before sampled_at.

    The population standard deviation describes the observed window. A positive
    floor regularizes the Z-score for constant/near-constant data. Only unusually
    high values are flagged; this does not establish a bottleneck or change health.
    Invalid metric values reduce each metric's sample count within the window.
    """
    settings = dict(BASELINE_CONFIG)
    if config is not None:
        settings.update(config)
    window = settings["window_size"]
    minimum = settings["minimum_samples"]
    threshold = settings["anomaly_threshold"]
    floor = settings["minimum_stddev"]
    if (type(window) is not int or type(minimum) is not int
            or not 2 <= minimum <= window):
        raise ValueError("baseline requires integer window_size >= minimum_samples >= 2")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in (threshold, floor)):
        raise ValueError("anomaly_threshold and minimum_stddev must be finite and positive")

    current_time = _sample_time(snapshot.get("sampled_at"))
    eligible = []
    for sample in history:
        if not isinstance(sample, dict):
            continue
        observed = _sample_time(sample.get("sampled_at"))
        if current_time is not None and observed is not None and observed < current_time:
            eligible.append((observed, sample))
    eligible.sort(key=lambda item: item[0])
    prior_window = [sample for _, sample in eligible[-window:]]
    memory = snapshot.get("memory")
    current_values = {
        "cpu": snapshot.get("cpu_percent"),
        "memory": memory.get("percent") if isinstance(memory, dict) else None,
    }
    results = {}
    for resource, metric in (("cpu", "cpu_percent"), ("memory", "memory_percent")):
        values = [_percentage(sample.get(metric)) for sample in prior_window]
        values = [value for value in values if value is not None]
        count = len(values)
        current = _percentage(current_values[resource])
        mean = fmean(values) if values else None
        deviation = pstdev(values) if values else None
        effective = max(deviation, floor) if deviation is not None else None
        score = None
        unusual = None
        if current is None or current_time is None:
            status = "unavailable"
            explanation = "A valid current utilization and sample timestamp are required."
        elif count < minimum:
            status = "insufficient_history"
            explanation = f"Only {count} valid prior samples; at least {minimum} are required."
        else:
            score = (current - mean) / effective
            unusual = score >= threshold
            status = "unusual" if unusual else "normal"
            explanation = (
                f"Current {current:.2f}%; prior mean {mean:.2f}%, standard deviation "
                f"{deviation:.2f} percentage points across {count} samples. "
                f"High-side Z-score {score:.2f}; anomaly threshold {threshold:g}."
            )
            if deviation < floor:
                explanation += f" The score uses a {floor:g}-point deviation floor."
        results[resource] = {
            "metric": metric,
            "current_value": current,
            "baseline_mean": mean,
            "baseline_stddev": deviation,
            "effective_stddev": effective,
            "anomaly_score": score,
            "sample_count": count,
            "window_size": window,
            "minimum_samples": minimum,
            "anomaly_threshold": threshold,
            "status": status,
            "is_anomaly": unusual,
            "explanation": explanation,
        }
    return results
