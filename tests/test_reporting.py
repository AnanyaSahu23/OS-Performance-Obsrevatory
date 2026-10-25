import copy
import csv
from io import StringIO
import unittest

from reporting import REPORT_COLUMNS, build_diagnosis_rows, diagnoses_to_csv


def stored_event(**updates):
    event = {
        "event_id": 1, "recorded_at": "2026-10-25T12:00:00Z",
        "last_seen_at": "2026-10-25T12:00:10Z", "condition": "cpu_pressure",
        "status": "Possible sustained CPU pressure", "status_code": "possible_sustained_pressure",
        "severity": "warning", "evidence_status": "resource_indicators",
        "metrics_sampled_at": "2026-10-25T12:00:10Z", "cpu_percent": 85,
        "memory_percent": 50, "swap_percent": 0,
        "observed_processes": [{"pid": 7, "name": 'worker, "α"', "cpu_percent": 83,
                                "memory_rss": 1024}],
        "evidence": ["CPU elevated", "No cause confirmed"], "onset_evidence": ["Initial reading"],
        "recommendation_priority": "Investigate", "recommendation": 'Inspect "worker", then\nobserve recovery',
        "interpretation": "Observed consumption does not prove causation.",
        "incident_started_at": "2026-10-25T12:00:00Z", "resolved_at": "2026-10-25T12:00:20Z",
        "incident_duration_seconds": 20, "duration_seconds": 14,
        "duration_scope": "high_cpu_utilization",
    }
    event.update(updates)
    return event


class ReportTests(unittest.TestCase):
    def test_csv_neutralizes_formula_text_without_changing_display_or_numbers(self):
        for text in ("=1+1", "+1+1", "-1+1", "@SUM(1)", "  =1+1", "\tvalue", "\rvalue", "\nvalue"):
            with self.subTest(text=text):
                event = stored_event(recommendation=text, observed_processes=[{"name": text}],
                                     duration_seconds=-1)
                original = copy.deepcopy(event)
                row = next(csv.DictReader(StringIO(diagnoses_to_csv([event]).decode("utf-8-sig"))))
                self.assertEqual(row["Recommendation"], "'" + text)
                self.assertEqual(row["Observed contributors (not proven causes)"], "'" + text)
                self.assertEqual(row["Metric duration (s)"], "-1.0")
                self.assertEqual(build_diagnosis_rows([event])[0]["Recommendation"], text)
                self.assertEqual(event, original)

    def test_csv_headers_unicode_quoting_and_stored_values(self):
        event = stored_event()
        reader = csv.DictReader(StringIO(diagnoses_to_csv([event]).decode("utf-8-sig")))
        self.assertEqual(reader.fieldnames, list(REPORT_COLUMNS))
        rows = list(reader)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["Recommendation"], event["recommendation"])
        self.assertEqual(row["Supporting evidence"], "CPU elevated | No cause confirmed")
        self.assertEqual(row["CPU (%)"], "85.0")
        self.assertEqual(row["Swap (%)"], "0.0")
        self.assertIn('worker, "α" (PID 7; CPU 83%; RSS 1024 B)',
                      row["Observed contributors (not proven causes)"])
        self.assertEqual(row["Interpretation"], event["interpretation"])

    def test_order_uses_utc_then_stored_id_and_deduplicates_only_ids(self):
        events = [stored_event(event_id=1, recorded_at="2026-10-25T13:00:00+01:00"),
                  stored_event(event_id=2), stored_event(event_id=3, recorded_at="2026-10-25T12:00:01Z")]
        rows = build_diagnosis_rows(events + [copy.deepcopy(events[0])])
        self.assertEqual([row["Event ID"] for row in rows], ["3", "2", "1"])
        self.assertEqual(rows[-1]["Recorded at (UTC)"], "2026-10-25T12:00:00.000000+00:00")

    def test_empty_history_exports_headers_only(self):
        self.assertEqual(build_diagnosis_rows([]), [])
        reader = csv.DictReader(StringIO(diagnoses_to_csv([]).decode("utf-8-sig")))
        self.assertEqual(reader.fieldnames, list(REPORT_COLUMNS))
        self.assertEqual(list(reader), [])

    def test_missing_and_invalid_values_are_blank_but_zero_is_preserved(self):
        event = stored_event(cpu_percent=None, memory_percent=float("nan"), swap_percent=0,
                             observed_processes=None, evidence=None, onset_evidence=None,
                             recorded_at="invalid", last_seen_at=None, duration_seconds=float("inf"))
        row = build_diagnosis_rows([event])[0]
        for key in ("CPU (%)", "Memory (%)", "Observed contributors (not proven causes)",
                    "Supporting evidence", "Onset evidence", "Recorded at (UTC)",
                    "Last observed (UTC)", "Metric duration (s)"):
            self.assertEqual(row[key], "", key)
        self.assertEqual(row["Swap (%)"], 0.0)

    def test_incomplete_records_are_retained_without_inventing_values(self):
        rows = build_diagnosis_rows([{}, {"condition": "memory_pressure"}, None])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Event ID"], "")
        self.assertEqual(rows[1]["Condition"], "memory_pressure")
        self.assertEqual(rows[1]["CPU (%)"], "")

    def test_lifecycle_and_duration_scopes_remain_distinct(self):
        row = build_diagnosis_rows([stored_event()])[0]
        self.assertEqual(row["Incident elapsed (s)"], 20)
        self.assertEqual(row["Metric duration (s)"], 14)
        self.assertEqual(row["Duration scope"], "high_cpu_utilization")
        self.assertEqual(row["Recovery at (UTC)"], "2026-10-25T12:00:20.000000+00:00")
        self.assertEqual(row["Onset evidence"], "Initial reading")
        self.assertNotEqual(row["Supporting evidence"], row["Onset evidence"])

    def test_partial_process_details_and_structured_evidence(self):
        row = build_diagnosis_rows([stored_event(observed_processes=[{"pid": 0, "cpu_percent": None}, None],
                                                 evidence=[{"available": 10}, None])])[0]
        self.assertEqual(row["Observed contributors (not proven causes)"], "Unknown process (PID 0)")
        self.assertEqual(row["Supporting evidence"], '{"available": 10}')

    def test_rendering_does_not_mutate_stored_records(self):
        events = [stored_event()]
        original = copy.deepcopy(events)
        build_diagnosis_rows(events)
        diagnoses_to_csv(events)
        self.assertEqual(events, original)
