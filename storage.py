"""SQLite persistence for metric samples and diagnosis state changes."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from config import STORAGE_CONFIG


def _database_path(database_path: Optional[str] = None) -> Path:
    configured = database_path or STORAGE_CONFIG["database_path"]
    path = Path(configured)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


@contextmanager
def _connection(database_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path,
        timeout=float(STORAGE_CONFIG.get("connection_timeout_seconds", 5)),
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database_path: Optional[str] = None) -> None:
    """Create the schema if needed; safe to call on every app startup."""
    with _connection(database_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metric_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sampled_at TEXT NOT NULL UNIQUE,
                cpu_percent REAL,
                memory_percent REAL,
                memory_total_bytes INTEGER,
                memory_used_bytes INTEGER,
                memory_available_bytes INTEGER,
                swap_percent REAL,
                swap_total_bytes INTEGER,
                swap_used_bytes INTEGER,
                running_process_count INTEGER,
                top_cpu_json TEXT NOT NULL,
                top_memory_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_metric_samples_sampled_at
                ON metric_samples(sampled_at);

            CREATE TABLE IF NOT EXISTS diagnosis_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                condition TEXT NOT NULL,
                status_code TEXT NOT NULL,
                status TEXT NOT NULL,
                severity TEXT NOT NULL,
                evidence_status TEXT NOT NULL,
                duration_seconds REAL,
                evidence_json TEXT NOT NULL,
                observed_processes_json TEXT NOT NULL,
                interpretation TEXT NOT NULL,
                recommendation_priority TEXT NOT NULL,
                recommendation TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_diagnosis_events_recorded_at
                ON diagnosis_events(recorded_at);
            CREATE INDEX IF NOT EXISTS idx_diagnosis_events_condition_id
                ON diagnosis_events(condition, id);
            """
        )


def save_snapshot(
    snapshot: Dict[str, Any], database_path: Optional[str] = None
) -> None:
    """Persist one collector snapshot, including top-process observations."""
    sampled_at = snapshot.get("sampled_at")
    if not sampled_at:
        raise ValueError("snapshot must include sampled_at")
    memory = snapshot.get("memory") or {}
    swap = snapshot.get("swap") or {}
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO metric_samples (
                sampled_at, cpu_percent, memory_percent, memory_total_bytes,
                memory_used_bytes, memory_available_bytes, swap_percent,
                swap_total_bytes, swap_used_bytes, running_process_count,
                top_cpu_json, top_memory_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sampled_at,
                snapshot.get("cpu_percent"),
                memory.get("percent"),
                memory.get("total"),
                memory.get("used"),
                memory.get("available"),
                swap.get("percent"),
                swap.get("total"),
                swap.get("used"),
                snapshot.get("running_process_count"),
                json.dumps(snapshot.get("top_cpu", []), ensure_ascii=False),
                json.dumps(snapshot.get("top_memory", []), ensure_ascii=False),
            ),
        )


def save_diagnoses(
    diagnoses: Iterable[Dict[str, Any]],
    recorded_at: str,
    recommendations: Optional[Iterable[Dict[str, Any]]] = None,
    database_path: Optional[str] = None,
) -> int:
    """Save only diagnosis state transitions; return the number of events added."""
    recommendation_by_condition = {
        item.get("condition"): item for item in (recommendations or [])
    }
    inserted = 0
    with _connection(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for diagnosis in diagnoses:
            condition = diagnosis.get("condition")
            status_code = diagnosis.get("status_code", "unknown")
            if not condition:
                continue
            previous = connection.execute(
                """
                SELECT status_code, severity, evidence_status
                FROM diagnosis_events
                WHERE condition = ?
                ORDER BY id DESC LIMIT 1
                """,
                (condition,),
            ).fetchone()
            current_state = (
                status_code,
                diagnosis.get("severity", "info"),
                diagnosis.get("evidence_status", "unknown"),
            )
            if previous and tuple(previous) == current_state:
                continue

            recommendation = recommendation_by_condition.get(condition, {})
            connection.execute(
                """
                INSERT INTO diagnosis_events (
                    recorded_at, condition, status_code, status, severity,
                    evidence_status, duration_seconds, evidence_json,
                    observed_processes_json, interpretation,
                    recommendation_priority, recommendation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recorded_at,
                    condition,
                    status_code,
                    diagnosis.get("status", "Unknown"),
                    current_state[1],
                    current_state[2],
                    diagnosis.get("duration_seconds"),
                    json.dumps(diagnosis.get("evidence", []), ensure_ascii=False),
                    json.dumps(
                        diagnosis.get("observed_processes", []), ensure_ascii=False
                    ),
                    diagnosis.get("interpretation", ""),
                    recommendation.get("priority", "Informational"),
                    recommendation.get("message", ""),
                ),
            )
            inserted += 1
        _prune_expired(connection)
    return inserted


def get_recent_metrics(
    limit: int = 60, database_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return recent metric rows oldest-first for plotting."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT sampled_at, cpu_percent, memory_percent, swap_percent
            FROM metric_samples ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def get_recent_diagnoses(
    limit: int = 20, database_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return diagnosis transitions newest-first, with JSON fields decoded."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT recorded_at, condition, status_code, status, severity,
                   evidence_status, duration_seconds, evidence_json,
                   observed_processes_json, interpretation,
                   recommendation_priority, recommendation
            FROM diagnosis_events ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        item["observed_processes"] = json.loads(
            item.pop("observed_processes_json")
        )
        results.append(item)
    return results


def _prune_expired(connection: sqlite3.Connection) -> int:
    retention_days = STORAGE_CONFIG.get("retention_days")
    if retention_days is None:
        return 0
    retention_days = float(retention_days)
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    metrics = connection.execute(
        "DELETE FROM metric_samples WHERE sampled_at < ?", (cutoff,)
    ).rowcount
    diagnoses = connection.execute(
        "DELETE FROM diagnosis_events WHERE recorded_at < ?", (cutoff,)
    ).rowcount
    return metrics + diagnoses


def prune_history(database_path: Optional[str] = None) -> int:
    """Apply configured retention to both tables; None disables pruning."""
    with _connection(database_path) as connection:
        return _prune_expired(connection)
