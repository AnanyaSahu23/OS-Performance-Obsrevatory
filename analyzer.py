"""Evidence-based assessment of possible CPU and memory pressure."""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import BOTTLENECK_CONFIG


Finding = Dict[str, Any]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _assess_cpu(
    metrics: Dict[str, Any],
    history: Sequence[Dict[str, Any]],
    config: Dict[str, Any],
) -> Finding:
    threshold = config["cpu_pressure_threshold"]
    required_samples = config["cpu_pressure_samples"]
    current_cpu = _number(metrics.get("cpu_percent"))
    if current_cpu is None:
        return {
            "status": "Insufficient evidence",
            "evidence": ["Current CPU utilization is unavailable or invalid."],
            "recommendation": "Collect valid CPU readings before assessing sustained pressure.",
        }

    recent_samples = history[-required_samples:]
    recent_values = [
        _number(sample.get("CPU (%)")) if isinstance(sample, dict) else None
        for sample in recent_samples
    ]
    enough_samples = len(recent_values) >= required_samples
    sustained_high_cpu = enough_samples and all(
        value is not None and value >= threshold for value in recent_values
    )
    evidence = [f"Current CPU utilization is {current_cpu:.1f}%."]

    if sustained_high_cpu:
        values = ", ".join(f"{value:.1f}%" for value in recent_values if value is not None)
        return {
            "status": "Possible CPU pressure",
            "evidence": [
                f"CPU utilization was at or above {threshold:.1f}% for "
                f"{required_samples} consecutive samples ({values}).",
                "High CPU utilization shows the processor is busy; it does not establish "
                "that an application is experiencing a performance impact.",
            ],
            "recommendation": (
                "Check application response times and task completion times, then compare "
                "them with CPU activity and the top CPU-consuming processes."
            ),
        }

    if current_cpu >= threshold:
        if enough_samples:
            values = ", ".join(
                "unavailable" if value is None else f"{value:.1f}%"
                for value in recent_values
            )
            evidence.append(
                f"The latest {required_samples} samples do not all meet the "
                f"{threshold:.1f}% threshold ({values})."
            )
        else:
            evidence.append(
                f"Only {len(recent_values)} of {required_samples} required recent "
                "samples are available."
            )
        evidence.append(
            "A high current reading may be transient and does not confirm a bottleneck."
        )
        return {
            "status": "Possible transient CPU pressure",
            "evidence": evidence,
            "recommendation": (
                "Continue monitoring CPU readings and check whether application response "
                "times or task completion times are affected."
            ),
        }

    if not enough_samples or any(value is None for value in recent_values):
        evidence.append(
            f"Fewer than {required_samples} valid consecutive CPU samples are available "
            "to assess sustained pressure."
        )
        return {
            "status": "Insufficient evidence",
            "evidence": evidence,
            "recommendation": (
                "Collect more consecutive CPU samples and compare them with observed "
                "application performance."
            ),
        }

    values = ", ".join(f"{value:.1f}%" for value in recent_values if value is not None)
    evidence.append(
        f"The latest {required_samples} samples were {values}; sustained pressure at "
        f"{threshold:.1f}% was not observed."
    )
    return {
        "status": "No sustained CPU pressure detected",
        "evidence": evidence,
        "recommendation": (
            "If performance is still poor, check application-level timings and other "
            "possible resource constraints."
        ),
    }


def _swap_is_increasing(
    history: Sequence[Dict[str, Any]], required_samples: int
) -> Tuple[bool, bool]:
    recent = history[-required_samples:]
    values = [
        _number(sample.get("Swap (%)")) if isinstance(sample, dict) else None
        for sample in recent
    ]
    enough_samples = required_samples >= 2 and len(values) >= required_samples and all(
        value is not None for value in values
    )
    increasing = enough_samples and all(
        values[index] > values[index - 1]
        for index in range(1, len(values))
        if values[index] is not None and values[index - 1] is not None
    )
    return increasing, enough_samples


def _assess_memory(
    metrics: Dict[str, Any],
    history: Sequence[Dict[str, Any]],
    config: Dict[str, Any],
) -> Finding:
    memory = metrics.get("memory")
    swap = metrics.get("swap")
    if not isinstance(memory, dict) or not isinstance(swap, dict):
        return {
            "status": "Insufficient evidence",
            "evidence": ["Required RAM or swap metrics are unavailable."],
            "recommendation": "Collect valid RAM and swap metrics before assessing memory pressure.",
        }

    total_ram = _number(memory.get("total"))
    available_ram = _number(memory.get("available"))
    memory_percent = _number(memory.get("percent"))
    swap_percent = _number(swap.get("percent"))
    if (
        total_ram is None
        or available_ram is None
        or memory_percent is None
        or swap_percent is None
        or total_ram <= 0
        or available_ram < 0
        or available_ram > total_ram
        or not 0 <= memory_percent <= 100
        or not 0 <= swap_percent <= 100
    ):
        return {
            "status": "Insufficient evidence",
            "evidence": ["Required RAM or swap metrics are missing or invalid."],
            "recommendation": "Collect valid RAM and swap metrics before assessing memory pressure.",
        }

    available_percent = (available_ram / total_ram) * 100.0
    warning_threshold = config["available_memory_warning"]
    critical_threshold = config["available_memory_critical"]
    memory_threshold = config["memory_usage_high"]
    swap_threshold = config["swap_usage_high"]
    trend_samples = config["swap_trend_samples"]
    swap_increasing, trend_available = _swap_is_increasing(history, trend_samples)

    evidence = [
        f"Available RAM is {available_percent:.1f}% of total memory.",
        f"RAM utilization is {memory_percent:.1f}%.",
        f"Swap utilization is {swap_percent:.1f}%.",
    ]
    corroborating = []
    if memory_percent >= memory_threshold:
        corroborating.append(f"RAM utilization is at or above {memory_threshold:.1f}%")
    if swap_percent >= swap_threshold:
        corroborating.append(f"swap utilization is at or above {swap_threshold:.1f}%")
    if swap_increasing:
        corroborating.append(f"swap utilization increased over {trend_samples} samples")

    low_available = available_percent <= warning_threshold
    if low_available and corroborating:
        strength = (
            "Low available RAM is at or below the critical threshold"
            if available_percent <= critical_threshold
            else "Low available RAM is at or below the warning threshold"
        )
        evidence.append(f"{strength}, with corroborating indicators: " + "; ".join(corroborating) + ".")
        if not trend_available:
            evidence.append(
                "There are not yet enough valid samples to assess the swap trend."
            )
        return {
            "status": "Possible memory pressure",
            "swap_evidence": (
                (["increasing"] if swap_increasing else [])
                + (["elevated"] if swap_percent >= swap_threshold else [])
            ),
            "evidence": evidence
            + [
                "These resource indicators do not establish that an application is "
                "experiencing a performance impact.",
            ],
            "recommendation": (
                "Check application response times and memory use, and investigate which "
                "processes are consuming RAM before concluding memory is the cause."
            ),
        }

    if low_available:
        evidence.append(
            f"Available RAM is at or below the {warning_threshold:.1f}% warning threshold, "
            "but no corroborating RAM or swap indicator is present."
        )
        if not trend_available:
            evidence.append(
                "There are not yet enough valid samples to assess whether swap usage is increasing."
            )
        return {
            "status": "Low available memory",
            "evidence": evidence,
            "recommendation": (
                "Monitor available RAM and swap over time, and check application response "
                "times for an actual performance impact."
            ),
        }

    if corroborating:
        evidence.append(
            "RAM or swap indicators are elevated, but available RAM is not low enough "
            "to support a combined memory-pressure finding."
        )
        return {
            "status": "Memory indicators elevated",
            "evidence": evidence,
            "recommendation": (
                "Observe available RAM and application performance; high RAM or swap "
                "utilization alone does not establish a memory bottleneck."
            ),
        }

    evidence.append("No combined low-memory and corroborating pressure indicators were observed.")
    return {
        "status": "No significant memory pressure detected",
        "evidence": evidence,
        "recommendation": (
            "If performance issues remain, investigate application-level timings and "
            "other possible resource constraints."
        ),
    }


def assess_bottlenecks(
    metrics: Dict[str, Any],
    history: Sequence[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assess possible CPU and memory pressure without claiming causation."""
    if not isinstance(metrics, dict):
        metrics = {}
    if not isinstance(history, (list, tuple)):
        history = []
    settings = BOTTLENECK_CONFIG if config is None else config
    cpu_result = _assess_cpu(metrics, history, settings)
    memory_result = _assess_memory(metrics, history, settings)
    limitation = (
        "Resource utilization alone does not confirm a performance bottleneck; "
        "confirmation requires evidence of actual application performance impact."
    )
    cpu_result["limitation"] = limitation
    memory_result["limitation"] = limitation
    return {
        "cpu": cpu_result,
        "memory": memory_result,
        "overall_note": (
            "These findings indicate possible resource pressure, not a confirmed "
            "performance bottleneck. Confirmation requires evidence of actual performance "
            "impact and further investigation."
        ),
    }
