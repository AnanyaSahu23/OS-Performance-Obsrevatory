"""Readable diagnosis reports made only from stored records supplied by storage."""

import csv
from datetime import datetime, timezone
from io import StringIO
import json
import math


REPORT_COLUMNS = (
    "Event ID", "Recorded at (UTC)", "Last observed (UTC)", "Condition", "Status",
    "Status code", "Severity", "Evidence status", "Metrics at (UTC)", "CPU (%)",
    "Memory (%)", "Swap (%)", "Observed contributors (not proven causes)",
    "Supporting evidence", "Onset evidence", "Recommendation priority", "Recommendation",
    "Interpretation", "Incident start (UTC)", "Recovery at (UTC)",
    "Incident elapsed (s)", "Metric duration (s)", "Duration scope",
)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _time_text(value):
    parsed = _timestamp(value)
    return parsed.isoformat(timespec="microseconds") if parsed is not None else ""


def _number(value):
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    return number if math.isfinite(number) else ""


def _text(value):
    return "" if value is None else str(value)


def _evidence(value):
    if not isinstance(value, list):
        return _text(value) if isinstance(value, str) else ""
    return " | ".join(json.dumps(item, ensure_ascii=False, sort_keys=True)
                      if isinstance(item, (dict, list)) else str(item)
                      for item in value if item is not None)


def _contributors(value):
    if not isinstance(value, list):
        return ""
    consumers = []
    for process in value:
        if not isinstance(process, dict):
            if process is not None:
                consumers.append(str(process))
            continue
        details = []
        if process.get("pid") is not None:
            details.append(f'PID {process["pid"]}')
        for key, label, unit in (("cpu_percent", "CPU", "%"),
                                 ("memory_percent", "RAM", "%"), ("memory_rss", "RSS", " B")):
            number = _number(process.get(key))
            if number != "":
                details.append(f"{label} {number:g}{unit}")
        name = _text(process.get("name")) or "Unknown process"
        consumers.append(name + (" (" + "; ".join(details) + ")" if details else ""))
    return " | ".join(consumers)


def build_diagnosis_rows(events):
    """One row per stored transition, newest-first; blank means unavailable.

    Keep transition, latest observation, metric window and incident lifecycle
    times distinct. Deduplicate only identical stored IDs, never status text.
    """
    ordered = sorted((event for event in events if isinstance(event, dict)),
                     key=lambda event: (_timestamp(event.get("recorded_at"))
                                        or datetime.min.replace(tzinfo=timezone.utc),
                                        event.get("event_id") if type(event.get("event_id")) is int else 0),
                     reverse=True)
    rows, seen = [], set()
    for event in ordered:
        event_id = event.get("event_id")
        if type(event_id) is int:
            if event_id in seen:
                continue
            seen.add(event_id)
        rows.append(dict(zip(REPORT_COLUMNS, (
            _text(event_id), _time_text(event.get("recorded_at")),
            _time_text(event.get("last_seen_at")), _text(event.get("condition")),
            _text(event.get("status")), _text(event.get("status_code")),
            _text(event.get("severity")), _text(event.get("evidence_status")),
            _time_text(event.get("metrics_sampled_at")), _number(event.get("cpu_percent")),
            _number(event.get("memory_percent")), _number(event.get("swap_percent")),
            _contributors(event.get("observed_processes")), _evidence(event.get("evidence")),
            _evidence(event.get("onset_evidence")), _text(event.get("recommendation_priority")),
            _text(event.get("recommendation")), _text(event.get("interpretation")),
            _time_text(event.get("incident_started_at")), _time_text(event.get("resolved_at")),
            _number(event.get("incident_duration_seconds")), _number(event.get("duration_seconds")),
            _text(event.get("duration_scope")),
        ))))
    return rows


def diagnoses_to_csv(events):
    """Export the same report rows as UTF-8 CSV, including headers for empty data."""
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=REPORT_COLUMNS)
    writer.writeheader()
    writer.writerows(build_diagnosis_rows(events))
    return buffer.getvalue().encode("utf-8-sig")
