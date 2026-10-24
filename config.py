HEALTH_CONFIG = {
    # CPU thresholds
    "cpu_warning": 80.0,
    "cpu_critical": 95.0,
    # Required consecutive samples for CPU pressure
    "cpu_warning_samples": 3,
    "cpu_critical_samples": 3,
    # Memory thresholds
    "available_memory_warning": 20.0,
    "available_memory_critical": 10.0,
    "memory_warning": 90.0,
    "memory_critical": 95.0,
    # Swap thresholds, used alongside RAM pressure
    "swap_warning": 50.0,
    "swap_critical": 80.0,
    "swap_warning_memory": 85.0,
    "swap_critical_memory": 90.0,
}

BOTTLENECK_CONFIG = {
    # CPU pressure
    "cpu_pressure_threshold": 80.0,
    "cpu_pressure_samples": 3,
    # Available memory as a percentage of total RAM
    "available_memory_warning": 20.0,
    "available_memory_critical": 10.0,
    # Corroborating memory indicators
    "memory_usage_high": 90.0,
    "swap_usage_high": 50.0,
    # Number of recent samples required for a trend comparison
    "swap_trend_samples": 3,
}

PROCESS_ATTRIBUTION_CONFIG = {
    # A system sample counts as CPU pressure above this level.
    "cpu_pressure_threshold": 80.0,
    # Memory-pressure context thresholds.
    "available_memory_threshold": 20.0,
    "memory_usage_threshold": 90.0,
    "swap_usage_threshold": 50.0,
    # Statistical requirements.
    "minimum_samples": 15,
    "max_history_samples": 120,
    # Display settings.
    "top_findings": 5,
    "top_process_count": 3,
    # Heuristic correlation-strength boundaries.
    "strong_correlation": 0.70,
    "moderate_correlation": 0.40,
}
