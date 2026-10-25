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

# Stable machine-readable labels for the analyzer's human-readable statuses.
DIAGNOSIS_STATUS_CODES = {
    "Possible CPU pressure": "possible_sustained_pressure",
    "Possible transient CPU pressure": "possible_transient_pressure",
    "No sustained CPU pressure detected": "no_persistent_pressure",
    "Possible memory pressure": "possible_corroborated_pressure",
    "Low available memory": "low_available_memory",
    "Memory indicators elevated": "elevated_indicators_only",
    "No significant memory pressure detected": "no_significant_pressure",
    "Insufficient evidence": "insufficient_evidence",
}

PRESSURE_STATUS_CODES = {
    "possible_sustained_pressure",
    "possible_transient_pressure",
    "possible_corroborated_pressure",
    "low_available_memory",
    "elevated_indicators_only",
}

# Informational guidance only: the application never performs these actions.
# Keys are (diagnosis condition, status code), values are editable rules.
RECOMMENDATION_RULES = {
    ("memory_pressure", "low_memory_increasing_swap"): {
        "priority": "Review",
        "message": (
            "Available RAM is low and swap usage is increasing. Review memory-heavy "
            "workloads and compare swap activity with application response times "
            "before deciding whether intervention is needed."
        ),
    },
    ("memory_pressure", "low_memory_elevated_swap"): {
        "priority": "Review",
        "message": (
            "Available RAM is low and swap usage is elevated. Review the largest "
            "memory consumers and monitor swap activity and application performance; "
            "occupied swap alone does not establish active paging or a bottleneck."
        ),
    },
    ("cpu_pressure", "possible_sustained_pressure"): {
        "priority": "Review",
        "message": (
            "Check whether the top CPU-consuming workloads are expected. "
            "Compare CPU activity with application response times or task completion times."
        ),
    },
    ("cpu_pressure", "possible_transient_pressure"): {
        "priority": "Monitor",
        "message": (
            "Continue monitoring. Check application timings if the high CPU reading "
            "persists or performance is affected."
        ),
    },
    ("cpu_pressure", "no_persistent_pressure"): {
        "priority": "Informational",
        "message": (
            "No persistent CPU pressure is indicated by these rules. If slowness remains, "
            "compare application timings with other resource indicators."
        ),
    },
    ("memory_pressure", "possible_corroborated_pressure"): {
        "priority": "Review",
        "message": (
            "Review memory use by the largest observed processes and check application "
            "response times before attributing the issue to memory pressure."
        ),
    },
    ("memory_pressure", "low_available_memory"): {
        "priority": "Monitor",
        "message": (
            "Monitor available RAM and swap. Review memory-heavy workloads before "
            "deciding whether action is needed."
        ),
    },
    ("memory_pressure", "elevated_indicators_only"): {
        "priority": "Monitor",
        "message": (
            "Continue monitoring RAM, swap, and application performance. Elevated "
            "indicators alone do not prove a bottleneck."
        ),
    },
    ("memory_pressure", "no_significant_pressure"): {
        "priority": "Informational",
        "message": (
            "No combined memory-pressure indicators are present. If slowness remains, "
            "compare application timings with other resource indicators."
        ),
    },
    ("cpu_pressure", "insufficient_evidence"): {
        "priority": "Collect evidence",
        "message": "Collect more valid CPU samples before drawing a conclusion.",
    },
    ("memory_pressure", "insufficient_evidence"): {
        "priority": "Collect evidence",
        "message": "Collect valid RAM and swap samples before drawing a conclusion.",
    },
}

DEFAULT_RECOMMENDATION = {
    "priority": "Informational",
    "message": "Review the available evidence and continue monitoring.",
}

STORAGE_CONFIG = {
    # Relative paths are resolved from the repository root (the config.py folder).
    "database_path": "data/observatory.sqlite3",
    # Set to None to retain history indefinitely.
    "retention_days": 30,
    "connection_timeout_seconds": 5,
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

STRESS_TEST_CONFIG = {
    "default_duration_seconds": 10,
    "default_workers": 1,
    "max_duration_seconds": 30,
    "max_workers": 2,
    "cleanup_timeout_seconds": 0.5,
}
