"""Small dashboard controls; observations remain in the existing A-G pipeline."""

import streamlit as st

from config import STRESS_TEST_CONFIG
from stress import get_stress_controller


def render_stress_controls():
    controller = get_stress_controller()
    status = controller.status()
    limits = controller.limits
    st.subheader("Controlled Stress Test")
    st.caption(
        "Optional bounded CPU workload on the machine running this app. "
        "Results use actual monitoring data; a workload may not cross pressure thresholds."
    )
    duration_col, workers_col = st.columns(2)
    duration = duration_col.number_input(
        "Test duration (seconds)", min_value=1,
        max_value=int(limits["max_duration_seconds"]),
        value=min(STRESS_TEST_CONFIG["default_duration_seconds"], int(limits["max_duration_seconds"])),
        step=1, key="stress_duration", disabled=status["active"],
    )
    workers = workers_col.number_input(
        "Worker count", min_value=1, max_value=limits["max_workers"],
        value=min(STRESS_TEST_CONFIG["default_workers"], limits["max_workers"]),
        step=1, key="stress_workers", disabled=status["active"],
    )
    start_col, stop_col = st.columns(2)
    if start_col.button("Start test", key="stress_start", disabled=status["active"]):
        try:
            status = controller.start(duration, workers)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
    if stop_col.button("Stop test", key="stress_stop", disabled=not status["active"]):
        status = controller.stop()
    if status["status"] == "running":
        st.info(f'Running: {status["elapsed_seconds"]:.1f}s elapsed; '
                f'about {status["remaining_seconds"]:.1f}s remaining.')
    elif status["status"] == "finished":
        st.success("Stress test finished; workers cleaned up.")
    elif status["status"] == "stopped":
        st.success("Stress test stopped; workers cleaned up.")
    elif status["status"] == "failed":
        st.error(f'Stress test failed: {status["error"]}')
    else:
        st.write("No stress test is running.")
    if status["worker_pids"]:
        st.caption("Test worker PIDs: " + ", ".join(map(str, status["worker_pids"])))
