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
    # Used only when sample timestamps are unavailable for duration estimates
    "sample_interval_seconds": 2,
}
