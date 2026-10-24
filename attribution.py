"""Statistical process attribution for OS Performance Observatory."""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import PROCESS_ATTRIBUTION_CONFIG


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def pearson_correlation(
    x_values: Sequence[Any],
    y_values: Sequence[Any],
    minimum_samples: int = 15,
) -> Optional[float]:
    """Calculate Pearson's r, returning None for insufficient/constant data."""
    pairs = []
    for x_value, y_value in zip(x_values, y_values):
        x = _finite_number(x_value)
        y = _finite_number(y_value)
        if x is not None and y is not None:
            pairs.append((x, y))
    if minimum_samples < 2 or len(pairs) < minimum_samples:
        return None

    mean_x = sum(pair[0] for pair in pairs) / len(pairs)
    mean_y = sum(pair[1] for pair in pairs) / len(pairs)
    deviations_x = [pair[0] - mean_x for pair in pairs]
    deviations_y = [pair[1] - mean_y for pair in pairs]
    sum_x_squared = sum(value**2 for value in deviations_x)
    sum_y_squared = sum(value**2 for value in deviations_y)
    if sum_x_squared == 0 or sum_y_squared == 0:
        return None

    numerator = sum(
        x_deviation * y_deviation
        for x_deviation, y_deviation in zip(deviations_x, deviations_y)
    )
    correlation = numerator / math.sqrt(sum_x_squared * sum_y_squared)
    return max(-1.0, min(1.0, correlation))


def _process_identity(process: Dict[str, Any]) -> Optional[Tuple[Any, Any]]:
    pid = process.get("pid")
    if pid is None:
        return None
    create_time = _finite_number(process.get("create_time"))
    return pid, create_time


def _memory_values(snapshot: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    memory = snapshot.get("memory")
    swap = snapshot.get("swap")
    if not isinstance(memory, dict) or not isinstance(swap, dict):
        return None, None
    return _finite_number(memory.get("available")), _finite_number(swap.get("used"))


def _pressure_context(
    history: Sequence[Dict[str, Any]], config: Dict[str, Any]
) -> Dict[str, int]:
    cpu_pressure_samples = 0
    memory_pressure_samples = 0
    for snapshot in history:
        if not isinstance(snapshot, dict):
            continue
        cpu = _finite_number(snapshot.get("cpu_percent"))
        if cpu is not None and cpu >= config["cpu_pressure_threshold"]:
            cpu_pressure_samples += 1

        memory = snapshot.get("memory")
        swap = snapshot.get("swap")
        if not isinstance(memory, dict) or not isinstance(swap, dict):
            continue
        total = _finite_number(memory.get("total"))
        available = _finite_number(memory.get("available"))
        memory_percent = _finite_number(memory.get("percent"))
        swap_percent = _finite_number(swap.get("percent"))
        if (
            total is None
            or available is None
            or total <= 0
            or available < 0
            or available > total
        ):
            continue
        low_available = available / total * 100 <= config["available_memory_threshold"]
        corroborated = (
            memory_percent is not None
            and swap_percent is not None
            and memory_percent >= config["memory_usage_threshold"]
            and swap_percent >= config["swap_usage_threshold"]
        )
        if low_available or corroborated:
            memory_pressure_samples += 1
    return {
        "cpu_pressure_samples": cpu_pressure_samples,
        "memory_pressure_samples": memory_pressure_samples,
    }


def _correlation_description(
    correlation: float, config: Dict[str, Any]
) -> Tuple[str, str, str]:
    magnitude = abs(correlation)
    if magnitude >= config["strong_correlation"]:
        strength = "strong"
    elif magnitude >= config["moderate_correlation"]:
        strength = "moderate"
    else:
        strength = "weak"
    if correlation > 0:
        direction = "positive"
        interpretation = "the variables tend to increase together"
    elif correlation < 0:
        direction = "negative"
        interpretation = "one variable tends to decrease as the other increases"
    else:
        direction = "none"
        interpretation = "there is no linear direction"
    return strength, direction, interpretation


def _top_pressure_overlap(
    identity: Tuple[Any, Any],
    history: Sequence[Dict[str, Any]],
    resource: str,
    config: Dict[str, Any],
) -> Tuple[int, int]:
    pressure_count = 0
    top_count = 0
    for snapshot in history:
        if not isinstance(snapshot, dict):
            continue
        if resource == "cpu":
            value = _finite_number(snapshot.get("cpu_percent"))
            is_pressure = (
                value is not None and value >= config["cpu_pressure_threshold"]
            )
            metric = "cpu_percent"
        else:
            context = _pressure_context([snapshot], config)
            is_pressure = context["memory_pressure_samples"] > 0
            metric = "memory_rss"
        if not is_pressure:
            continue
        pressure_count += 1
        processes = snapshot.get("processes")
        if not isinstance(processes, list):
            continue
        ranked = []
        for process in processes:
            if not isinstance(process, dict):
                continue
            process_key = _process_identity(process)
            process_value = _finite_number(process.get(metric))
            if process_key is not None and process_value is not None:
                ranked.append((process_value, process_key))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if identity in {key for _, key in ranked[: config["top_process_count"]]}:
            top_count += 1
    return top_count, pressure_count


def _make_finding(
    process: Dict[str, Any],
    correlation: float,
    sample_count: int,
    history: Sequence[Dict[str, Any]],
    resource: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    strength, direction, interpretation = _correlation_description(correlation, config)
    identity = _process_identity(process)
    pressure_top_count, pressure_count = _top_pressure_overlap(
        identity, history, resource, config
    )
    overlap = pressure_top_count / pressure_count * 100 if pressure_count else None
    evidence = (
        f"{process.get('name') or 'Unknown'} had a {strength} {direction} "
        f"relationship across {sample_count} paired observations."
    )
    if pressure_count:
        evidence += (
            f" It ranked among the top {config['top_process_count']} "
            f"{resource} consumers in {pressure_top_count} of {pressure_count} "
            "relevant pressure samples."
        )
    else:
        evidence += (
            f" No {resource.lower()} pressure samples were observed, so "
            "process overlap with pressure is not applicable."
        )
    return {
        "name": process.get("name") or "Unknown",
        "pid": process.get("pid"),
        "create_time": process.get("create_time"),
        "correlation": round(correlation, 4),
        "strength": strength,
        "direction": direction,
        "interpretation": interpretation,
        "sample_count": sample_count,
        "top_pressure_samples": pressure_top_count,
        "pressure_sample_count": pressure_count,
        "pressure_overlap_percent": round(overlap, 1) if overlap is not None else None,
        "evidence": evidence,
    }


def _cpu_findings(
    history: Sequence[Dict[str, Any]], config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    process_data: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    processes_by_sample = []
    for snapshot in history:
        process_by_identity = {}
        processes = snapshot.get("processes", [])
        if isinstance(processes, list):
            for process in processes:
                if not isinstance(process, dict):
                    continue
                identity = _process_identity(process)
                if identity is not None:
                    process_by_identity[identity] = process
                    process_data[identity] = process
        processes_by_sample.append(process_by_identity)

    process_cpu: Dict[Tuple[Any, Any], List[Optional[float]]] = {}
    for identity in process_data:
        values = []
        for process_by_identity in processes_by_sample:
            process = process_by_identity.get(identity)
            # A process absent from an otherwise collected snapshot is treated
            # as not running; access-denied processes cannot be distinguished.
            values.append(
                _finite_number(process.get("cpu_percent")) if process else 0.0
            )
        process_cpu[identity] = values

    system_cpu = [_finite_number(snapshot.get("cpu_percent")) for snapshot in history]
    findings = []
    for identity, values in process_cpu.items():
        correlation = pearson_correlation(
            system_cpu, values, config["minimum_samples"]
        )
        if correlation is None:
            continue
        findings.append(
            _make_finding(
                process_data[identity],
                correlation,
                sum(
                    x is not None and y is not None
                    for x, y in zip(system_cpu, values)
                ),
                history,
                "CPU",
                config,
            )
        )
    findings.sort(
        key=lambda finding: (
            abs(finding["correlation"]),
            finding["pressure_overlap_percent"],
            finding["name"].lower(),
        ),
        reverse=True,
    )
    return findings[: config["top_findings"]]


def _memory_findings(
    history: Sequence[Dict[str, Any]],
    config: Dict[str, Any],
    system_variable: str,
    resource_label: str,
) -> List[Dict[str, Any]]:
    process_data: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    samples_processes = []
    system_deltas: List[Optional[float]] = []

    for previous, current in zip(history, history[1:]):
        previous_available, previous_swap = _memory_values(previous)
        current_available, current_swap = _memory_values(current)
        previous_system = (
            previous_available if system_variable == "available" else previous_swap
        )
        current_system = (
            current_available if system_variable == "available" else current_swap
        )
        system_deltas.append(
            current_system - previous_system
            if current_system is not None and previous_system is not None
            else None
        )

        previous_processes = {}
        current_processes = {}
        for target, snapshot in (
            (previous_processes, previous),
            (current_processes, current),
        ):
            processes = snapshot.get("processes", [])
            if not isinstance(processes, list):
                continue
            for process in processes:
                if not isinstance(process, dict):
                    continue
                identity = _process_identity(process)
                rss = _finite_number(process.get("memory_rss"))
                if identity is not None and rss is not None and rss >= 0:
                    target[identity] = (rss, process)
                    process_data[identity] = process

        for identity in set(previous_processes) & set(current_processes):
            process_data[identity] = current_processes[identity][1]
        samples_processes.append((previous_processes, current_processes))

    findings = []
    for identity in process_data:
        values = []
        for previous_processes, current_processes in samples_processes:
            if identity in previous_processes and identity in current_processes:
                values.append(
                    current_processes[identity][0] - previous_processes[identity][0]
                )
            else:
                values.append(None)
        correlation = pearson_correlation(
            system_deltas, values, config["minimum_samples"]
        )
        if correlation is None:
            continue
        findings.append(
            _make_finding(
                process_data[identity],
                correlation,
                sum(
                    x is not None and y is not None
                    for x, y in zip(system_deltas, values)
                ),
                history,
                resource_label,
                config,
            )
        )
    findings.sort(
        key=lambda finding: (
            abs(finding["correlation"]),
            finding["pressure_overlap_percent"],
            finding["name"].lower(),
        ),
        reverse=True,
    )
    return findings[: config["top_findings"]]


def assess_process_attribution(
    history: Sequence[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyze process/system CPU and memory relationships in snapshot history."""
    settings = PROCESS_ATTRIBUTION_CONFIG if config is None else config
    if not isinstance(history, (list, tuple)):
        history = []
    history = [sample for sample in history if isinstance(sample, dict)]
    minimum_samples = settings["minimum_samples"]
    if minimum_samples < 2:
        raise ValueError("minimum_samples must be at least 2")

    pressure_context = _pressure_context(history, settings)
    base = {
        "window_samples": len(history),
        "minimum_samples": minimum_samples,
        "pressure_context": pressure_context,
    }
    if len(history) < minimum_samples:
        return {
            **base,
            "status": "insufficient_data",
            "message": (
                f"Collect at least {minimum_samples} samples before interpreting "
                "correlations."
            ),
            "cpu_findings": [],
            "memory_findings": [],
            "swap_findings": [],
            "limitations": [
                "Correlation does not establish causation.",
                "At least 15 varied, valid observations are required by default.",
            ],
        }

    return {
        **base,
        "status": "analyzed",
        "message": (
            "Correlations describe statistical relationships in this observation "
            "window; they do not confirm that a process caused performance pressure."
        ),
        "cpu_findings": _cpu_findings(history, settings),
        "memory_findings": _memory_findings(
            history, settings, "available", "memory"
        ),
        "swap_findings": _memory_findings(history, settings, "swap", "memory"),
        "limitations": [
            "Correlation does not establish causation or prove user-visible performance impact.",
            "Memory RSS, available RAM, and swap are related through system memory accounting.",
            "CPU samples for absent processes are treated as zero; inaccessible processes may be missing.",
            "Memory change correlations use adjacent observations where the process exists in both.",
            "Results depend on the observation window, sampling frequency, process visibility, "
            "and variability of the metrics.",
        ],
    }
