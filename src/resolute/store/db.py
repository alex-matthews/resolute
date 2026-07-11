"""Durable decision/feedback/audit history: SQLite (WAL) on a PVC.

Chosen over JSONL because calibration needs queries (override clusters,
agreement rates) and over Postgres because this is a single small service.
`export_jsonl` provides the append-only escape hatch for backups/portability.

A single connection is shared and serialized with an RLock: FastAPI runs sync
handlers on a thread pool and sqlite3 connections are not thread-safe.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from ..ids import new_id
from ..schemas import Decision, FeedbackIn, FeedbackRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT,
    year INTEGER,
    trigger TEXT NOT NULL,
    mode TEXT NOT NULL,
    seerr_request_id INTEGER,
    tmdb_id INTEGER,
    tvdb_id INTEGER,
    final_resolution TEXT NOT NULL,
    confidence TEXT NOT NULL,
    score REAL NOT NULL,
    model_used INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_seerr ON decisions(seerr_request_id);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    created_at TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reason_tag TEXT,
    comment TEXT,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_decision ON feedback(decision_id);

CREATE TABLE IF NOT EXISTS audits (
    audit_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    decision_id TEXT,
    tvdb_id INTEGER,
    expected_profile TEXT,
    actual_profile TEXT,
    matches INTEGER,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    notification_type TEXT,
    outcome TEXT NOT NULL,
    decision_id TEXT,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    actions TEXT NOT NULL,
    operator TEXT
);

-- ADR-0002 write-ahead audit: one row per Costanza downgrade decision,
-- inserted BEFORE any Sonarr write. UNIQUE + executed makes execution
-- exactly-once per *successful* run; per-step flags keep the row truthful
-- when an attempt is interrupted mid-plan (and allow idempotent resume).
CREATE TABLE IF NOT EXISTS downgrades (
    downgrade_id TEXT PRIMARY KEY,
    costanza_decision_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    tvdb_id INTEGER NOT NULL,
    series_id INTEGER,
    target_profile TEXT,
    estimated_gb_reclaimed REAL,
    operator TEXT,
    profile_set INTEGER NOT NULL DEFAULT 0,
    search_triggered INTEGER NOT NULL DEFAULT 0,
    executed INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ping(self) -> bool:
        """Cheap readiness probe: proves the DB is reachable without
        deserializing stored payloads (which could fail after schema changes)."""
        with self._lock:
            self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
        return True

    # -- decisions ---------------------------------------------------------

    def save_decision(self, decision: Decision) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO decisions
                   (decision_id, created_at, title, year, trigger, mode, seerr_request_id,
                    tmdb_id, tvdb_id, final_resolution, confidence, score, model_used, payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision.decision_id,
                    decision.created_at.isoformat(),
                    decision.title,
                    decision.year,
                    decision.trigger.value,
                    decision.mode.value,
                    decision.request.seerr_request_id,
                    decision.evidence.facts.tmdb_id,
                    decision.evidence.facts.tvdb_id,
                    decision.final_resolution.value,
                    decision.confidence.value,
                    decision.score,
                    int(decision.model_involvement.used),
                    decision.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_decision(self, decision_id: str) -> Decision | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
        return Decision.model_validate_json(row[0]) if row else None

    def last_decision(self) -> Decision | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM decisions ORDER BY created_at DESC, decision_id DESC LIMIT 1"
            ).fetchone()
        return Decision.model_validate_json(row[0]) if row else None

    def list_decisions(self, limit: int = 50) -> list[Decision]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM decisions ORDER BY created_at DESC, decision_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Decision.model_validate_json(r[0]) for r in rows]

    def mark_executed(
        self, decision_id: str, actions: list[str], operator: str | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO executions (execution_id, decision_id, executed_at, actions, operator)"
                " VALUES (?,?,?,?,?)",
                (new_id(), decision_id, _now(), json.dumps(actions), operator),
            )
            self._conn.commit()

    def executions(self, decision_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT executed_at, actions, operator FROM executions"
                " WHERE decision_id=? ORDER BY executed_at",
                (decision_id,),
            ).fetchall()
        return [
            {"executed_at": r[0], "actions": json.loads(r[1]), "operator": r[2]} for r in rows
        ]

    # -- feedback ----------------------------------------------------------

    def save_feedback(self, feedback: FeedbackIn) -> FeedbackRecord:
        record = FeedbackRecord(**feedback.model_dump(), feedback_id=new_id())
        with self._lock:
            self._conn.execute(
                "INSERT INTO feedback (feedback_id, decision_id, created_at, verdict,"
                " reason_tag, comment, source) VALUES (?,?,?,?,?,?,?)",
                (
                    record.feedback_id,
                    record.decision_id,
                    record.created_at.isoformat(),
                    record.verdict.value,
                    record.reason_tag,
                    record.comment,
                    record.source,
                ),
            )
            self._conn.commit()
        return record

    # -- audits / webhooks ---------------------------------------------------

    def save_audit(self, audit_payload: dict, decision_id: str | None = None) -> str:
        audit_id = new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO audits (audit_id, created_at, decision_id, tvdb_id,"
                " expected_profile, actual_profile, matches, payload) VALUES (?,?,?,?,?,?,?,?)",
                (
                    audit_id,
                    _now(),
                    decision_id,
                    audit_payload.get("tvdb_id"),
                    audit_payload.get("expected_profile"),
                    audit_payload.get("actual_profile"),
                    None
                    if audit_payload.get("matches") is None
                    else int(audit_payload["matches"]),
                    json.dumps(audit_payload),
                ),
            )
            self._conn.commit()
        return audit_id

    def save_webhook_event(
        self, payload: dict, outcome: str, decision_id: str | None = None
    ) -> str:
        event_id = new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO webhook_events (event_id, received_at, notification_type,"
                " outcome, decision_id, payload) VALUES (?,?,?,?,?,?)",
                (
                    event_id,
                    _now(),
                    str(payload.get("notification_type")),
                    outcome,
                    decision_id,
                    json.dumps(payload),
                ),
            )
            self._conn.commit()
        return event_id

    # -- downgrades (ADR-0002) -------------------------------------------------

    def save_downgrade(self, report, operator: str | None = None) -> bool:
        """Write-ahead audit row. Returns False if this Costanza decision id
        already has a row (the exactly-once refusal), True on insert."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO downgrades (downgrade_id, costanza_decision_id,"
                    " created_at, tvdb_id, series_id, target_profile,"
                    " estimated_gb_reclaimed, operator, executed, payload)"
                    " VALUES (?,?,?,?,?,?,?,?,0,?)",
                    (
                        new_id(),
                        report.costanza_decision_id,
                        _now(),
                        report.tvdb_id,
                        report.series_id,
                        report.target_profile_name,
                        report.estimated_gb_reclaimed,
                        operator,
                        report.model_dump_json(),
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                return False
        return True

    _DOWNGRADE_STEPS = ("profile_set", "search_triggered")

    def mark_downgrade_step(self, costanza_decision_id: str, step: str) -> None:
        """Record a completed Sonarr step as it happens, so an interrupted
        attempt leaves a row that tells the truth about downstream state."""
        if step not in self._DOWNGRADE_STEPS:
            raise ValueError(f"unknown downgrade step '{step}'")
        with self._lock:
            self._conn.execute(
                f"UPDATE downgrades SET {step}=1 WHERE costanza_decision_id=?",
                (costanza_decision_id,),
            )
            self._conn.commit()

    def mark_downgrade_executed(self, costanza_decision_id: str, report) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE downgrades SET executed=1, payload=? WHERE costanza_decision_id=?",
                (report.model_dump_json(), costanza_decision_id),
            )
            self._conn.commit()

    def get_downgrade(self, costanza_decision_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at, operator, executed, profile_set, search_triggered,"
                " payload FROM downgrades WHERE costanza_decision_id=?",
                (costanza_decision_id,),
            ).fetchone()
        if row is None:
            return None
        steps = [
            step for step, done in zip(self._DOWNGRADE_STEPS, (row[3], row[4])) if done
        ]
        return {
            "created_at": row[0],
            "operator": row[1],
            "executed": bool(row[2]),
            "steps": steps,
            "report": json.loads(row[5]),
        }

    # -- calibration ---------------------------------------------------------

    def calibration_summary(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            by_resolution = dict(
                self._conn.execute(
                    "SELECT final_resolution, COUNT(*) FROM decisions GROUP BY final_resolution"
                ).fetchall()
            )
            feedback_total = self._conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            by_verdict = dict(
                self._conn.execute(
                    "SELECT verdict, COUNT(*) FROM feedback GROUP BY verdict"
                ).fetchall()
            )
            by_reason = dict(
                self._conn.execute(
                    "SELECT COALESCE(reason_tag,'(none)'), COUNT(*) FROM feedback"
                    " WHERE verdict != 'agree' GROUP BY reason_tag"
                ).fetchall()
            )
        agreements = by_verdict.get("agree", 0)
        return {
            "decisions": total,
            "decisions_by_resolution": by_resolution,
            "feedback": feedback_total,
            "feedback_by_verdict": by_verdict,
            "override_reason_tags": by_reason,
            "agreement_rate": round(agreements / feedback_total, 3) if feedback_total else None,
        }

    def overrides(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT f.created_at, f.verdict, f.reason_tag, f.comment,
                          d.title, d.final_resolution, d.confidence, d.decision_id
                   FROM feedback f JOIN decisions d ON d.decision_id = f.decision_id
                   WHERE f.verdict != 'agree' ORDER BY f.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        keys = [
            "created_at",
            "verdict",
            "reason_tag",
            "comment",
            "title",
            "final_resolution",
            "confidence",
            "decision_id",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def export_jsonl(self, out_path: str | Path) -> int:
        """Append-only export of all decisions for backup/portability."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM decisions ORDER BY decision_id"
            ).fetchall()
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            for (payload,) in rows:
                fh.write(json.dumps(json.loads(payload)) + "\n")
        return len(rows)
