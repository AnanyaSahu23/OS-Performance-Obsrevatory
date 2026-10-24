"""Turn health and analyzer output into explainable diagnosis records."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from config import BOTTLENECK_CONFIG, HEALTH_CONFIG


PRESSURE_STATUSES = {
    "Possible CPU pressure",
    "Possible transient CPU pressure",
    "Possible memory pressure",
    "Low available memory",
    "Memory indicators elevated",
}


def _as_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _sample_time(sample: Dict[str, Any]) -> Optional[datetime]:
    value = sample.get("sampled_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None


def _duration_seconds(
    history: Sequence[Dict[str, Any]], metric_key: str, threshold: float, *, above: bool
) -> Optional[float]:
    """Return elapsed observed duration for a consecutive threshold run."""
    matching = []
    for sample in reversed(history):
        value = _as_number(sample.get(metric_key)) if isinstance(sample, dict) else None
        if value is None or not (value >= threshold if above else value <= threshold):
            break
        matching.append(sample)

    if len(matching) < 2:
        return None
    first, last = _sample_time(matching[-1]), _sample_time(matching[0])
    if first is not None and last is not None:
        try:
            return max(0.0, (last - first).total_seconds())
        except TypeError:  # Mixed timezone-aware and naive timestamps.
            pass
    interval = _as_number(BOTTLENECK_CONFIG.get("sample_interval_seconds"))
    return (len(matching) - 1) * interval if interval is not None else None


def _critical_for_resource(resource: str, snapshot: Dict[str, Any], history) -> bool:
    if resource == "cpu":
        threshold = HEALTH_CONFIG.get("cpu_critical", 95.0)
        samples = max(1, int(HEALTH_CONFIG.get("cpu_critical_samples", 3)))
        recent = list(history)[-samples:]
        return len(recent) >= samples and all(
            (_as_number(item.get("CPU (%)")) if isinstance(item, dict) else None)
            is not None
            and _as_number(item.get("CPU (%)")) >= threshold
            for item in recent
        )

    memory = snapshot.get("memory") or {}
    swap = snapshot.get("swap") or {}
    total = _as_number(memory.get("total"))
    available = _as_number(memory.get("available"))
    used_percent = _as_number(memory.get("percent"))
    swap_percent = _as_number(swap.get("percent"))
    available_percent = available / total * 100 if total and available is not None else None
    return bool(
        (available_percent is not None and available_percent <= HEALTH_CONFIG.get("available_memory_critical", 10.0))
        or (used_percent is not None and used_percent >= HEALTH_CONFIG.get("memory_critical", 95.0))
        or (
            swap_percent is not None
            and used_percent is not None
            and swap_percent >= HEALTH_CONFIG.get("swap_critical", 80.0)
            and used_percent >= HEALTH_CONFIG.get("swap_critical_memory", 90.0)
        )
    )


def build_diagnoses(
    snapshot: Dict[str, Any],
    health: Dict[str, Any],
    assessment: Dict[str, Any],
    history: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Create structured CPU and memory diagnoses from existing analysis.

    Process lists are reported as observed resource consumers, not asserted
    causes. Duration is only estimated when a consecutive run is observable.
    """
    history = history or []
    records = []
    for resource, process_key in (("cpu", "top_cpu"), ("memory", "top_memory")):
        finding = assessment.get(resource, {})
        status = finding.get("status", "Insufficient evidence")
        pressure = status in PRESSURE_STATUSES
        severity = (
            "critical" if pressure and _critical_for_resource(resource, snapshot, history)
            else "warning" if pressure
            else "info"
        )

        duration = None
        if resource == "cpu" and status == "Possible CPU pressure":
            duration = _duration_seconds(
                history,
                "CPU (%)",
                BOTTLENECK_CONFIG.get("cpu_pressure_threshold", 80.0),
                above=True,
            )
        elif resource == "memory" and status == "Possible memory pressure":
            memory = snapshot.get("memory") or {}
            total = _as_number(memory.get("total"))
            available = _as_number(memory.get("available"))
            if total and available is not None:
                threshold = BOTTLENECK_CONFIG.get("available_memory_warning", 20.0)
                duration = _duration_seconds(
                    history, "Available RAM (%)", threshold, above=False
                )

        processes = snapshot.get(process_key, []) if pressure else []
        records.append(
            {
                "condition": f"{resource}_pressure",
                "status": status,
                "severity": severity,
                "evidence_status": (
                    "corroborated_resource_indicators"
                    if status in {"Possible CPU pressure", "Possible memory pressure"}
                    else "single_or_transient_indicator"
                    if pressure
                    else "insufficient"
                    if status == "Insufficient evidence"
                    else "no_pressure_indicated"
                ),
                "evidence": list(finding.get("evidence", [])),
                "duration_seconds": duration,
                "observed_processes": processes[:5],
                "interpretation": (
                    "These are among the largest observed resource consumers. "
                    "Their activity is not proof of causation or application impact."
                    if processes
                    else "No process contribution is established by this finding."
                ),
                "limitation": finding.get(
                    "limitation",
                    "Resource metrics alone do not confirm a performance bottleneck.",
                ),
                "sampled_at": snapshot.get("sampled_at"),
                "health_status": health.get("status", "Unknown"),
            }
        )
    return records
