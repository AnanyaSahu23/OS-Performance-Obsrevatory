from config import HEALTH_CONFIG


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def evaluate_health(metrics, history, config=None):
    """Evaluate resource pressure using configurable rules.

    metrics: current system snapshot from collector.py
    history: recent dashboard samples used for sustained CPU pressure checks
    config: optional threshold configuration override
    """
    if config is None:
        config = HEALTH_CONFIG

    if not isinstance(metrics, dict):
        return {
            "status": "Unknown",
            "reasons": ["RAM information is unavailable."],
            "indicators": {},
        }

    memory = metrics.get("memory") or {}
    swap = metrics.get("swap") or {}
    total_ram = _as_float(memory.get("total"), 0.0)

    if total_ram <= 0:
        return {
            "status": "Unknown",
            "reasons": ["RAM information is unavailable."],
            "indicators": {},
        }

    cpu_percent = _as_float(metrics.get("cpu_percent"), 0.0)
    available_ram = _as_float(memory.get("available"), 0.0)
    memory_percent = _as_float(memory.get("percent"), 0.0)
    swap_percent = _as_float(swap.get("percent"), 0.0)
    available_memory_percent = (available_ram / total_ram) * 100.0

    warnings = []
    critical_reasons = []

    recent_cpu = []
    if isinstance(history, list):
        for sample in history:
            if isinstance(sample, dict):
                recent_cpu.append(_as_float(sample.get("CPU (%)"), 0.0))

    warning_window = config.get("cpu_warning_samples", 3)
    critical_window = config.get("cpu_critical_samples", 3)
    recent_cpu_warning = recent_cpu[-warning_window:]
    recent_cpu_critical = recent_cpu[-critical_window:]

    sustained_cpu_warning = (
        len(recent_cpu_warning) >= warning_window
        and all(value >= config.get("cpu_warning", 80.0) for value in recent_cpu_warning)
    )
    sustained_cpu_critical = (
        len(recent_cpu_critical) >= critical_window
        and all(value >= config.get("cpu_critical", 95.0) for value in recent_cpu_critical)
    )

    if sustained_cpu_critical:
        critical_reasons.append(
            f"CPU utilization has remained at or above {config.get('cpu_critical', 95.0)}% "
            f"for {critical_window} consecutive samples."
        )
    elif sustained_cpu_warning:
        warnings.append(
            f"CPU utilization has remained at or above {config.get('cpu_warning', 80.0)}% "
            f"for {warning_window} consecutive samples."
        )

    if available_memory_percent <= config.get("available_memory_critical", 10.0):
        critical_reasons.append(
            "Available RAM is below the critical threshold "
            f"({available_memory_percent:.1f}% available, <= {config.get('available_memory_critical', 10.0)}%)."
        )
    elif available_memory_percent <= config.get("available_memory_warning", 20.0):
        warnings.append(
            "Available RAM is below the warning threshold "
            f"({available_memory_percent:.1f}% available, <= {config.get('available_memory_warning', 20.0)}%)."
        )

    if memory_percent >= config.get("memory_critical", 95.0):
        critical_reasons.append(
            f"RAM utilization is {memory_percent:.1f}% (critical threshold: >= {config.get('memory_critical', 95.0)}%)."
        )
    elif memory_percent >= config.get("memory_warning", 90.0):
        warnings.append(
            f"RAM utilization is {memory_percent:.1f}% (warning threshold: >= {config.get('memory_warning', 90.0)}%)."
        )

    swap_warning_memory = config.get("swap_warning_memory", 85.0)
    swap_critical_memory = config.get("swap_critical_memory", 90.0)
    if swap_percent >= config.get("swap_critical", 80.0) and memory_percent >= swap_critical_memory:
        critical_reasons.append(
            f"Swap usage is {swap_percent:.1f}% with RAM utilization at {memory_percent:.1f}% "
            f"(critical threshold: >= {config.get('swap_critical', 80.0)}% with RAM utilization >= {swap_critical_memory}%)."
        )
    elif swap_percent >= config.get("swap_warning", 50.0) and memory_percent >= swap_warning_memory:
        warnings.append(
            f"Swap usage is {swap_percent:.1f}% with RAM utilization at {memory_percent:.1f}% "
            f"(warning threshold: >= {config.get('swap_warning', 50.0)}% with RAM utilization >= {swap_warning_memory}%)."
        )

    if critical_reasons:
        status = "Critical"
        reasons = critical_reasons + warnings
    elif warnings:
        status = "Warning"
        reasons = warnings
    else:
        status = "Healthy"
        reasons = ["No configured thresholds were breached."]

    indicators = {
        "CPU": round(cpu_percent, 2),
        "Available RAM": round(available_memory_percent, 2),
        "RAM utilization": round(memory_percent, 2),
        "Swap utilization": round(swap_percent, 2),
    }

    return {
        "status": status,
        "reasons": reasons,
        "indicators": indicators,
    }
