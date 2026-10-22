"""Live Streamlit dashboard for OS Performance Observatory."""

from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from collector import collect_snapshot


HISTORY_LIMIT = 60
REFRESH_SECONDS = 2

st.set_page_config(
    page_title="OS Performance Observatory",
    page_icon="📊",
    layout="wide",
)
st.title("OS Performance Observatory")
st.caption("Real-time system monitoring. This dashboard reports metrics; it does not diagnose bottlenecks.")


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


@st.fragment(run_every=REFRESH_SECONDS)
def render_dashboard() -> None:
    snapshot = collect_snapshot()

    if "metric_history" not in st.session_state:
        st.session_state.metric_history = []
    history = st.session_state.metric_history
    history.append(
        {
            "sampled_at": snapshot["sampled_at"],
            "CPU (%)": snapshot["cpu_percent"],
            "Memory (%)": snapshot["memory"]["percent"],
        }
    )
    del history[:-HISTORY_LIMIT]

    cpu_card, memory_card, swap_card, process_card = st.columns(4)
    cpu_card.metric("CPU", f'{snapshot["cpu_percent"]:.1f}%')
    memory_card.metric("RAM used", f'{snapshot["memory"]["percent"]:.1f}%')
    swap_card.metric("Swap used", f'{snapshot["swap"]["percent"]:.1f}%')
    process_card.metric("Running processes", snapshot["running_process_count"])

    memory_col, available_col, swap_detail_col = st.columns(3)
    memory_col.metric(
        "RAM used / total",
        f'{_format_gb(snapshot["memory"]["used"])} / '
        f'{_format_gb(snapshot["memory"]["total"])}',
    )
    available_col.metric("RAM available", _format_gb(snapshot["memory"]["available"]))
    swap_col_text = (
        f'{_format_gb(snapshot["swap"]["used"])} / '
        f'{_format_gb(snapshot["swap"]["total"])}'
    )
    swap_detail_col.metric("Swap used / total", swap_col_text)
    st.caption(f'Latest sample: {snapshot["sampled_at"]} (UTC)')

    st.subheader("Recent resource trends")
    chart_data = pd.DataFrame(history).set_index("sampled_at")
    st.line_chart(chart_data, y=["CPU (%)", "Memory (%)"], height=260)
    st.caption(
        f"Showing up to {HISTORY_LIMIT} samples (about "
        f"{HISTORY_LIMIT * REFRESH_SECONDS // 60} minutes). "
        "CPU and per-process CPU readings may be zero on their first sample."
    )

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
