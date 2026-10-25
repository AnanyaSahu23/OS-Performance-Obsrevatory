"""History table and CSV download; no sampling, classification or persistence."""

import streamlit as st

from dashboard_storage import storage_call
from reporting import build_diagnosis_rows, diagnoses_to_csv
from storage import get_recent_diagnoses


def render_diagnosis_reports(storage_ready):
    st.subheader("Diagnosis History and Reports")
    if not storage_ready:
        st.info("Diagnosis history is unavailable while storage is unavailable.")
        return
    limit = st.selectbox("History entries", [20, 100, 500], index=1, key="diagnosis_report_limit")
    events = storage_call(get_recent_diagnoses, limit, include_metrics=True,
                          on_error=st.warning, default=None)
    if events is None:
        st.info("Diagnosis history could not be loaded. No report is available.")
        return
    rows = build_diagnosis_rows(events)
    if not rows:
        st.info("No diagnosis history has been stored yet.")
        return
    st.caption(
        f"Most recent {len(rows)} stored transitions, newest first. "
        "Processes are observed consumers, not confirmed causes. Blank fields are unavailable. "
        "Metrics match the latest stored observation, or transition time for legacy records."
    )
    visible = ("Recorded at (UTC)", "Last observed (UTC)", "Condition", "Status", "Severity",
               "CPU (%)", "Memory (%)", "Observed contributors (not proven causes)",
               "Supporting evidence", "Recommendation")
    st.dataframe([{key: row[key] for key in visible} for row in rows],
                 hide_index=True, use_container_width=True)
    with st.expander("Full stored details and incident lifecycle"):
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Incident elapsed time and metric-window duration have different scopes; neither is recomputed.")
    st.download_button("Download diagnosis CSV", data=diagnoses_to_csv(events),
                       file_name="diagnosis_history.csv", mime="text/csv", key="diagnosis_report_download")
