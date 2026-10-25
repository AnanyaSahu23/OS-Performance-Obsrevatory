"""SQLite persistence for metric samples and diagnosis state changes."""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from config import PRESSURE_STATUS_CODES, STORAGE_CONFIG


def _utc_timestamp(value: str) -> str:
    """Canonical UTC; legacy naive ISO timestamps are interpreted as UTC."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be a valid ISO-8601 string") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _timestamp_exists(connection, table, timestamp):
    # julianday narrows legacy equivalents; Python retains microsecond precision.
    return any(_utc_timestamp(row[0]) == timestamp for row in connection.execute(
        f"SELECT sampled_at FROM {table} WHERE julianday(sampled_at)=julianday(?)",
        (timestamp,),
    ))


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


def _enable_wal(connection: sqlite3.Connection) -> None:
    # Journal-mode changes can report BUSY immediately despite busy_timeout.
    deadline = time.monotonic() + float(STORAGE_CONFIG.get("connection_timeout_seconds", 5))
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            remaining = deadline - time.monotonic()
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or remaining <= 0:
                raise
            time.sleep(min(0.05, remaining))


def initialize_database(database_path: Optional[str] = None) -> None:
    """Create the schema if needed; safe to call on every app startup."""
    with _connection(database_path) as connection:
        _enable_wal(connection)
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

            CREATE TABLE IF NOT EXISTS health_samples (
                sampled_at TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                indicators_json TEXT NOT NULL
            );
            """
        )
        # Additive migration: retain existing rows and tolerate repeated startup.
        connection.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(diagnosis_events)")}
        for name, definition in {
            "incident_started_at": "TEXT", "last_seen_at": "TEXT",
            "resolved_at": "TEXT", "incident_duration_seconds": "REAL",
            "duration_scope": "TEXT", "onset_evidence_json": "TEXT",
        }.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE diagnosis_events ADD COLUMN {name} {definition}")
        connection.execute(
            "UPDATE diagnosis_events SET last_seen_at = recorded_at, "
            "onset_evidence_json = evidence_json WHERE last_seen_at IS NULL"
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_metrics_observed_time ON metric_samples(julianday(sampled_at) DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_health_observed_time ON health_samples(julianday(sampled_at) DESC, sampled_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_diagnosis_observed_time ON diagnosis_events(julianday(recorded_at) DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_diagnosis_condition_time ON diagnosis_events(condition, julianday(recorded_at) DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_diagnosis_last_seen ON diagnosis_events(julianday(last_seen_at))",
        ):
            connection.execute(statement)


def _schema_identity(path: Path):
    """Read a file/schema identity without taking a migration write transaction."""
    with _connection(str(path)) as connection:
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        metadata = path.stat()
    return (str(path.resolve()), metadata.st_dev, metadata.st_ino, schema_version)


def ensure_database_initialized(session_state, database_path: Optional[str] = None) -> None:
    """Initialize once per session and database identity; retry failed attempts.

    A cheap schema-cookie read detects replacement, deletion, path changes and
    schema changes. A new session (including after restart) initializes afresh.
    Only successful initialization is remembered; no global cache is shared.
    """
    path = _database_path(database_path)
    key = "_observatory_initialized_schema_v1"
    identity = _schema_identity(path)
    if session_state.get(key) != identity:
        session_state.pop(key, None)
        initialize_database(str(path))
        session_state[key] = _schema_identity(path)


def save_snapshot(
    snapshot: Dict[str, Any], database_path: Optional[str] = None
) -> None:
    """Persist one collector snapshot, including top-process observations."""
    sampled_at = snapshot.get("sampled_at")
    if not sampled_at:
        raise ValueError("snapshot must include sampled_at")
    sampled_at = _utc_timestamp(sampled_at)
    memory = snapshot.get("memory") or {}
    swap = snapshot.get("swap") or {}
    with _connection(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _timestamp_exists(connection, "metric_samples", sampled_at):
            return
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


def save_health(
    health: Dict[str, Any], sampled_at: str, database_path: Optional[str] = None
) -> None:
    """Store actual health output independently of diagnosis transitions."""
    if not sampled_at:
        raise ValueError("health sample must include sampled_at")
    sampled_at = _utc_timestamp(sampled_at)
    with _connection(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _timestamp_exists(connection, "health_samples", sampled_at):
            return
        connection.execute(
            "INSERT OR IGNORE INTO health_samples VALUES (?, ?, ?, ?)",
            (sampled_at, health["status"], json.dumps(health.get("reasons", [])),
             json.dumps(health.get("indicators", {}))),
        )


def get_recent_health(
    limit: int = 60, database_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return actual health samples oldest-first, including unchanged states."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    with _connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM health_samples ORDER BY julianday(sampled_at) DESC, sampled_at DESC LIMIT ?", (limit,)
        ).fetchall()
    results = []
    for row in reversed(rows):
        item = dict(row)
        item["reasons"] = json.loads(item.pop("reasons_json"))
        item["indicators"] = json.loads(item.pop("indicators_json"))
        results.append(item)
    return results


def save_diagnoses(
    diagnoses: Iterable[Dict[str, Any]],
    recorded_at: str,
    recommendations: Optional[Iterable[Dict[str, Any]]] = None,
    database_path: Optional[str] = None,
) -> int:
    """Insert transitions, refresh ongoing evidence, and close observed incidents.

    recorded_at and onset_evidence remain immutable. Latest evidence and last_seen
    advance in place. Incident elapsed time spans observed onset to recovery;
    duration_seconds retains the diagnosis's separately labelled metric window.
    Per condition, older/equal timestamps are ignored for lifecycle mutation:
    the first accepted observation at a timestamp wins. Metric/health history
    can still store independently received observations.
    """
    recommendation_by_condition = {
        item.get("condition"): item for item in (recommendations or [])
    }
    recorded_at = _utc_timestamp(recorded_at)
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
                SELECT *
                FROM diagnosis_events
                WHERE condition = ?
                ORDER BY julianday(last_seen_at) DESC, id DESC LIMIT 1
                """,
                (condition,),
            ).fetchone()
            if previous and _elapsed_seconds(previous["last_seen_at"], recorded_at) <= 0:
                continue
            current_state = (
                status_code,
                diagnosis.get("severity", "info"),
                diagnosis.get("evidence_status", "unknown"),
            )
            recommendation = recommendation_by_condition.get(condition, {})
            evidence = json.dumps(diagnosis.get("evidence", []), ensure_ascii=False)
            processes = json.dumps(diagnosis.get("observed_processes", []), ensure_ascii=False)
            active = status_code in PRESSURE_STATUS_CODES
            previous_active = previous and previous["status_code"] in PRESSURE_STATUS_CODES
            previous_open = previous and not previous["resolved_at"] and (
                previous_active or previous["incident_started_at"]
            )
            incident_start = (
                (previous["incident_started_at"] or previous["recorded_at"])
                if previous_open
                else recorded_at if active else None
            )
            elapsed = _elapsed_seconds(incident_start, recorded_at) if incident_start else None
            if previous and tuple(previous[key] for key in
                                  ("status_code", "severity", "evidence_status")) == current_state:
                # A delayed sample must not overwrite more recent observations.
                if _elapsed_seconds(previous["last_seen_at"], recorded_at) >= 0:
                    if previous["resolved_at"] and not active:
                        incident_start = previous["incident_started_at"]
                        elapsed = previous["incident_duration_seconds"]
                    connection.execute(
                        "UPDATE diagnosis_events SET last_seen_at=?, duration_seconds=?, "
                        "duration_scope=?, evidence_json=?, observed_processes_json=?, "
                        "interpretation=?, recommendation_priority=?, recommendation=?, "
                        "incident_started_at=?, incident_duration_seconds=? WHERE id=?",
                        (recorded_at, diagnosis.get("duration_seconds"), diagnosis.get("duration_scope"),
                         evidence, processes, diagnosis.get("interpretation", ""),
                         recommendation.get("priority", "Informational"), recommendation.get("message", ""),
                         incident_start, elapsed, previous["id"]),
                    )
                continue

            recovered = bool(previous_open and status_code in {
                "no_persistent_pressure", "no_significant_pressure"
            })
            if recovered:
                incident_start = previous["incident_started_at"] or previous["recorded_at"]
                elapsed = _elapsed_seconds(incident_start, recorded_at)
                connection.execute(
                    "UPDATE diagnosis_events SET resolved_at=?, incident_duration_seconds=? "
                    "WHERE condition=? AND (incident_started_at=? OR id=?) "
                    "AND julianday(recorded_at)<=julianday(?) AND resolved_at IS NULL",
                    (recorded_at, elapsed, condition, incident_start, previous["id"], recorded_at),
                )
            connection.execute(
                """
                INSERT INTO diagnosis_events (
                    recorded_at, condition, status_code, status, severity,
                    evidence_status, duration_seconds, evidence_json,
                    observed_processes_json, interpretation,
                    recommendation_priority, recommendation,
                    incident_started_at, last_seen_at, resolved_at,
                    incident_duration_seconds, duration_scope, onset_evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    incident_start, recorded_at,
                    recorded_at if recovered else None,
                    elapsed, diagnosis.get("duration_scope"), evidence,
                ),
            )
            inserted += 1
        _prune_expired(connection)
    return inserted


def _elapsed_seconds(start: str, end: str) -> float:
    return (datetime.fromisoformat(_utc_timestamp(end))
            - datetime.fromisoformat(_utc_timestamp(start))).total_seconds()


def get_recent_metrics(
    limit: int = 60, database_path: Optional[str] = None, *, before: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return recent rows oldest-first, optionally strictly before a sample.

    The optional cutoff prevents current/future observations entering a baseline.
    UTC normalization preserves legacy offsets and microsecond comparisons.
    Existing calls without a cutoff retain the plotting contract.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    with _connection(database_path) as connection:
        if before is not None:
            cutoff = _utc_timestamp(before)
            connection.create_function("_observatory_utc", 1, _utc_timestamp, deterministic=True)
            rows = connection.execute(
                """
                SELECT sampled_at, cpu_percent, memory_percent, swap_percent
                FROM metric_samples
                WHERE julianday(sampled_at) <= julianday(?)
                  AND _observatory_utc(sampled_at) < ?
                ORDER BY julianday(sampled_at) DESC, _observatory_utc(sampled_at) DESC, id DESC
                LIMIT ?
                """, (cutoff, cutoff, limit),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]
        rows = connection.execute(
            """
            SELECT sampled_at, cpu_percent, memory_percent, swap_percent
            FROM metric_samples ORDER BY julianday(sampled_at) DESC, id DESC LIMIT ?
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
                    recommendation_priority, recommendation,
                    incident_started_at, last_seen_at, resolved_at,
                    incident_duration_seconds, duration_scope, onset_evidence_json
            FROM diagnosis_events ORDER BY julianday(recorded_at) DESC, id DESC LIMIT ?
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
        item["onset_evidence"] = json.loads(item.pop("onset_evidence_json") or "[]")
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
        "DELETE FROM metric_samples WHERE julianday(sampled_at) < julianday(?)", (cutoff,)
    ).rowcount
    diagnoses = connection.execute(
        "DELETE FROM diagnosis_events WHERE julianday(last_seen_at) < julianday(?)", (cutoff,)
    ).rowcount
    health = connection.execute(
        "DELETE FROM health_samples WHERE julianday(sampled_at) < julianday(?)", (cutoff,)
    ).rowcount
    return metrics + diagnoses + health


def prune_history(database_path: Optional[str] = None) -> int:
    """Apply configured retention to both tables; None disables pruning."""
    with _connection(database_path) as connection:
        return _prune_expired(connection)
