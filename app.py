"""Live Streamlit dashboard for OS Performance Observatory."""

from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from analyzer import assess_bottlenecks
from attribution import assess_process_attribution
from baseline import detect_anomalies
from collector import collect_snapshot
from config import BASELINE_CONFIG, PROCESS_ATTRIBUTION_CONFIG
from diagnosis import build_diagnoses
from dashboard_storage import storage_call
from health import evaluate_health
from recommendations import build_recommendations
from stress_ui import render_stress_controls
from storage import (
    get_recent_diagnoses,
    get_recent_metrics,
    get_recent_health,
    ensure_database_initialized,
    save_diagnoses,
    save_snapshot,
    save_health,
)


HISTORY_LIMIT = 60
REFRESH_SECONDS = 2

st.set_page_config(
    page_title="OS Performance Observatory",
    page_icon="📊",
    layout="wide",
)
st.title("OS Performance Observatory")
st.caption(
    "Evidence-based indicators of possible resource pressure. "
    "Findings do not confirm a performance bottleneck."
)


def _format_gb(value: int) -> str:
    return f"{value / (1024 ** 3):.2f} GB"


def _process_table(processes: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "PID": process["pid"],
            "Process": process["name"],
            "CPU (%)": process["cpu_percent"],
            "Memory (%)": process["memory_percent"],
            "RAM (MB)": round(process["memory_rss"] / (1024 ** 2), 2),
            "Status": process["status"],
        }
        for process in processes
    ]
    return pd.DataFrame(
        rows,
        columns=["PID", "Process", "CPU (%)", "Memory (%)", "RAM (MB)", "Status"],
    )


def _show_attribution_findings(
    title: str, findings: List[Dict[str, Any]], explanation: str
) -> None:
    st.subheader(title)
    st.caption(explanation)
    if not findings:
        st.info(
            "No statistically interpretable process relationships were found "
            "for this metric in the current history."
        )
        return
    rows = [
        {
            "Process": finding["name"],
            "PID": finding["pid"],
            "Pearson r": finding["correlation"],
            "Strength": finding["strength"],
            "Direction": finding["direction"],
            "Samples": finding["sample_count"],
            "Top 3 during pressure": (
                "Not applicable (no pressure samples)"
                if finding["pressure_sample_count"] == 0
                else f'{finding["top_pressure_samples"]} '
                f'({finding["pressure_overlap_percent"]:.1f}%)'
            ),
        }
        for finding in findings
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    for finding in findings:
        with st.expander(
            f'{finding["name"]} (PID {finding["pid"]}) - supporting evidence'
        ):
            st.write(finding["evidence"])
            st.write(f'Interpretation: {finding["interpretation"]}.')


@st.fragment(run_every=REFRESH_SECONDS)
def render_dashboard() -> None:
    render_stress_controls()
    st.divider()
    snapshot = collect_snapshot()

    if "system_sample_history" not in st.session_state:
        st.session_state.system_sample_history = []
    sample_history = st.session_state.system_sample_history
    sample_history.append(snapshot)
    max_samples = PROCESS_ATTRIBUTION_CONFIG["max_history_samples"]
    st.session_state.system_sample_history = sample_history[-max_samples:]
    sample_history = st.session_state.system_sample_history

    if "metric_history" not in st.session_state:
        st.session_state.metric_history = []
    history = st.session_state.metric_history
    history.append(
        {
            "sampled_at": snapshot["sampled_at"],
            "CPU (%)": snapshot["cpu_percent"],
            "Memory (%)": snapshot["memory"]["percent"],
            "Available RAM (%)": (
                snapshot["memory"]["available"] / snapshot["memory"]["total"] * 100
                if snapshot["memory"]["total"] > 0
                else None
            ),
            "Swap (%)": snapshot["swap"]["percent"],
        }
    )
    del history[:-HISTORY_LIMIT]

    health = evaluate_health(snapshot, history)
    bottlenecks = assess_bottlenecks(snapshot, history)
    diagnoses = build_diagnoses(snapshot, health, bottlenecks, history)
    recommendations = build_recommendations(diagnoses)
    storage_ready = storage_call(
        ensure_database_initialized, st.session_state, on_error=st.warning, default=False
    ) is not False
    baseline_history = (
        storage_call(get_recent_metrics, BASELINE_CONFIG["window_size"],
                     before=snapshot["sampled_at"], on_error=st.warning, default=[])
        if storage_ready else []
    )
    anomalies = detect_anomalies(snapshot, baseline_history)
    if storage_ready:
        storage_call(save_snapshot, snapshot, on_error=st.warning)
        storage_call(save_health, health, snapshot["sampled_at"], on_error=st.warning)
        storage_call(
            save_diagnoses, diagnoses, snapshot["sampled_at"],
            recommendations=recommendations, on_error=st.warning,
        )
    attribution = assess_process_attribution(sample_history)
    st.subheader("System Health")
    status = health["status"]
    if status == "Critical":
        st.error("CRITICAL: Significant resource pressure detected.")
    elif status == "Warning":
        st.warning("WARNING: Possible resource pressure detected.")
    elif status == "Healthy":
        st.success("HEALTHY: No configured pressure thresholds breached.")
    else:
        st.info("UNKNOWN: Health could not be evaluated.")
    st.caption(
        "This status reflects configurable resource thresholds. "
        "It is not a universal measure of system health."
    )
    st.markdown("**Contributing indicators**")
    indicator_cols = st.columns(4)
    for col, (name, value) in zip(indicator_cols, health["indicators"].items()):
        col.metric(
            name,
            "Unavailable" if value is None else f"{value:.1f}%",
        )
    with st.expander("Why was this status assigned?"):
        for reason in health["reasons"]:
            st.write(f"- {reason}")

    st.divider()
    st.subheader("CPU and Memory Bottleneck Assessment")
    st.caption(
        "Resource pressure is an indicator, not proof of a performance bottleneck."
    )
    cpu_assessment_col, memory_assessment_col = st.columns(2)
    for col, title, resource, result in (
        (cpu_assessment_col, "CPU Assessment", "cpu", bottlenecks["cpu"]),
        (memory_assessment_col, "Memory Assessment", "memory", bottlenecks["memory"]),
    ):
        with col:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                status = result["status"]
                if status in {
                    "Possible CPU pressure",
                    "Possible transient CPU pressure",
                    "Possible memory pressure",
                    "Low available memory",
                    "Memory indicators elevated",
                }:
                    st.warning(status)
                elif status not in {
                    "No sustained CPU pressure detected",
                    "No significant memory pressure detected",
                }:
                    st.info(status)
                else:
                    st.success(status)
                diagnosis = next(
                    item
                    for item in diagnoses
                    if item["condition"] == f"{resource}_pressure"
                )
                st.caption(
                    f'Severity: {diagnosis["severity"]} · '
                    f'Evidence status: {diagnosis["evidence_status"]}'
                )
                st.markdown("**Evidence**")
                for item in diagnosis["evidence"]:
                    st.write(f"- {item}")
                recommendation = next(
                    item
                    for item in recommendations
                    if item["condition"] == diagnosis["condition"]
                )
                st.markdown(f'**Next step · {recommendation["priority"]}**')
                st.write(recommendation["message"])
                if diagnosis["duration_seconds"] is not None:
                    st.caption(
                        f'Observed {diagnosis["duration_scope"].replace("_", " ")} duration: '
                        f'{diagnosis["duration_seconds"]:.1f} seconds.'
                    )
                if diagnosis["observed_processes"]:
                    st.markdown("**Observed resource consumers (not proven causes)**")
                    st.dataframe(
                        _process_table(diagnosis["observed_processes"]),
                        hide_index=True,
                        use_container_width=True,
                    )
                st.caption(diagnosis["limitation"])
    st.caption(bottlenecks["overall_note"])

    st.divider()
    st.subheader("Baseline-based anomaly detection")
    st.caption(
        "Compares utilization with strictly earlier stored samples. Unusual activity "
        "is not a confirmed bottleneck and does not change the fixed-threshold health status."
    )
    for col, resource, label in zip(st.columns(2), ("cpu", "memory"), ("CPU", "Memory")):
        finding = anomalies[resource]
        with col:
            if finding["status"] == "unusual":
                st.warning(f"{label}: unusual utilization")
            elif finding["status"] == "normal":
                st.success(f"{label}: within recent baseline")
            elif finding["status"] == "unavailable":
                st.info(f"{label}: current reading unavailable")
            else:
                st.info(f"{label}: insufficient historical data")
            st.write(finding["explanation"])

    st.divider()
    st.header("Statistical Process Attribution")
    st.caption(
        "Finds processes whose activity statistically moves with system CPU, "
        "available memory, or swap usage. Association does not establish cause."
    )
    context = attribution["pressure_context"]
    attribution_col1, attribution_col2, attribution_col3 = st.columns(3)
    attribution_col1.metric("Samples collected", attribution["window_samples"])
    attribution_col2.metric("CPU-pressure samples", context["cpu_pressure_samples"])
    attribution_col3.metric(
        "Memory-pressure samples", context["memory_pressure_samples"]
    )
    if attribution["status"] == "insufficient_data":
        st.info(
            f"Collect at least {attribution['minimum_samples']} samples before "
            "interpreting correlations."
        )
    else:
        _show_attribution_findings(
            "CPU attribution",
            attribution["cpu_findings"],
            "Pearson correlation between system CPU utilization and each process's "
            "CPU utilization across the full observation window.",
        )
        _show_attribution_findings(
            "Memory attribution",
            attribution["memory_findings"],
            "Pearson correlation between per-interval process RSS changes and "
            "available RAM changes. A negative relationship means RSS growth "
            "tends to coincide with falling available RAM.",
        )
        _show_attribution_findings(
            "Swap attribution",
            attribution["swap_findings"],
            "Pearson correlation between per-interval process RSS changes and "
            "system swap-used changes.",
        )
    with st.expander("How to interpret these results"):
        st.write(attribution["message"])
        for limitation in attribution["limitations"]:
            st.write(f"- {limitation}")

    cpu_card, memory_card, swap_card, process_card = st.columns(4)
    cpu_card.metric("CPU", f'{snapshot["cpu_percent"]:.1f}%')
    memory_card.metric("RAM used", f'{snapshot["memory"]["percent"]:.1f}%')
    swap_percent = snapshot["swap"]["percent"]
    swap_card.metric(
        "Swap used",
        "Unavailable" if swap_percent is None else f"{swap_percent:.1f}%",
    )
    process_card.metric("Running processes", snapshot["running_process_count"])

    memory_col, available_col, swap_detail_col = st.columns(3)
    memory_col.metric(
        "RAM used / total",
        f'{_format_gb(snapshot["memory"]["used"])} / '
        f'{_format_gb(snapshot["memory"]["total"])}',
    )
    available_col.metric("RAM available", _format_gb(snapshot["memory"]["available"]))
    swap_used = snapshot["swap"]["used"]
    swap_total = snapshot["swap"]["total"]
    swap_col_text = (
        "Unavailable"
        if swap_used is None or swap_total is None
        else f"{_format_gb(swap_used)} / {_format_gb(swap_total)}"
    )
    swap_detail_col.metric("Swap used / total", swap_col_text)
    st.caption(f'Latest sample: {snapshot["sampled_at"]} (UTC)')

    st.subheader("Recent resource trends")
    stored_metrics = (storage_call(get_recent_metrics, HISTORY_LIMIT,
                                  on_error=st.warning, default=[]) if storage_ready else [])
    if stored_metrics:
        chart_data = pd.DataFrame(stored_metrics).set_index("sampled_at")
        chart_data = chart_data.rename(
            columns={
                "cpu_percent": "CPU (%)",
                "memory_percent": "Memory (%)",
                "swap_percent": "Swap (%)",
            }
        )
        chart_data = chart_data.apply(pd.to_numeric, errors="coerce")
        chart_columns = [
            name
            for name in ("CPU (%)", "Memory (%)", "Swap (%)")
            if chart_data[name].notna().any()
        ]
        st.line_chart(
            chart_data,
            y=chart_columns,
            height=260,
        )
    else:
        st.info("Historical samples will appear after the first sample is stored.")
    st.caption(
        f"Showing up to {HISTORY_LIMIT} most recent stored samples (about "
        f"{HISTORY_LIMIT * REFRESH_SECONDS // 60} minutes). "
        "CPU and per-process CPU readings may be zero on their first sample."
    )

    st.subheader("Historical health status")
    health_samples = (storage_call(get_recent_health, HISTORY_LIMIT,
                                  on_error=st.warning, default=[]) if storage_ready else [])
    if health_samples:
        st.dataframe(
            [{"Time (UTC)": item["sampled_at"], "Health": item["status"]}
             for item in health_samples],
            hide_index=True, use_container_width=True,
        )
    else:
        st.write("No historical health samples are available.")

    st.subheader("Recent diagnosis changes")
    diagnosis_events = (storage_call(get_recent_diagnoses, on_error=st.warning,
                                    default=[]) if storage_ready else [])
    if diagnosis_events:
        st.dataframe(
            [
                {
                    "Time (UTC)": event["recorded_at"],
                    "Condition": event["condition"],
                    "Status": event["status"],
                    "Severity": event["severity"],
                    "Evidence": event["evidence_status"],
                    "Recommendation": event["recommendation"],
                }
                for event in diagnosis_events
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.write("No diagnosis state changes have been recorded yet.")

    cpu_col, memory_process_col = st.columns(2)
    with cpu_col:
        st.subheader("Top processes by CPU")
        st.dataframe(
            _process_table(snapshot["top_cpu"]),
            hide_index=True,
            use_container_width=True,
        )
    with memory_process_col:
        st.subheader("Top processes by RAM")
        st.dataframe(
            _process_table(snapshot["top_memory"]),
            hide_index=True,
            use_container_width=True,
        )


render_dashboard()
