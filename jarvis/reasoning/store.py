from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import utc_now_iso


class ReasoningStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_traces (
                trace_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                action_class TEXT NOT NULL,
                proposed_action TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_trace_events (
                event_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_candidates (
                candidate_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                candidate_kind TEXT NOT NULL,
                candidate_ref TEXT NOT NULL,
                rationale TEXT,
                expected_value REAL,
                confidence REAL,
                cost REAL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_selected_actions (
                selected_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL UNIQUE,
                candidate_id TEXT NOT NULL,
                selected_reason TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_traces_plan_step_created
            ON decision_traces(plan_id, step_id, created_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_trace_events_trace_created
            ON decision_trace_events(trace_id, created_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_candidates_trace_created
            ON decision_candidates(trace_id, created_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_selected_actions_trace_created
            ON decision_selected_actions(trace_id, created_at DESC)
            """
        )
        self.conn.commit()

    def create_trace(
        self,
        *,
        plan_id: str,
        step_id: str,
        action_class: str,
        proposed_action: str,
        status: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        now = str(created_at or utc_now_iso())
        resolved_trace_id = str(trace_id or f"dtrc_{uuid4().hex}")
        self.conn.execute(
            """
            INSERT INTO decision_traces (
                trace_id, plan_id, step_id, action_class, proposed_action,
                status, summary, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_trace_id,
                str(plan_id),
                str(step_id),
                str(action_class),
                str(proposed_action),
                str(status),
                str(summary) if summary is not None else None,
                json.dumps(dict(metadata or {}), sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()
        return resolved_trace_id

    def append_event(
        self,
        *,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        resolved_event_id = str(event_id or f"dtev_{uuid4().hex}")
        self.conn.execute(
            """
            INSERT INTO decision_trace_events (
                event_id, trace_id, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                resolved_event_id,
                str(trace_id),
                str(event_type),
                json.dumps(dict(payload or {}), sort_keys=True),
                str(created_at or utc_now_iso()),
            ),
        )
        self.conn.commit()
        return resolved_event_id

    def add_candidate(
        self,
        *,
        trace_id: str,
        candidate_kind: str,
        candidate_ref: str,
        rationale: str | None = None,
        expected_value: float | None = None,
        confidence: float | None = None,
        cost: float | None = None,
        metadata: dict[str, Any] | None = None,
        candidate_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        resolved_candidate_id = str(candidate_id or f"dcnd_{uuid4().hex}")
        self.conn.execute(
            """
            INSERT INTO decision_candidates (
                candidate_id, trace_id, candidate_kind, candidate_ref, rationale,
                expected_value, confidence, cost, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_candidate_id,
                str(trace_id),
                str(candidate_kind),
                str(candidate_ref),
                str(rationale) if rationale is not None else None,
                float(expected_value) if expected_value is not None else None,
                float(confidence) if confidence is not None else None,
                float(cost) if cost is not None else None,
                json.dumps(dict(metadata or {}), sort_keys=True),
                str(created_at or utc_now_iso()),
            ),
        )
        self.conn.commit()
        return resolved_candidate_id

    def select_candidate(
        self,
        *,
        trace_id: str,
        candidate_id: str,
        selected_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        selected_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        resolved_selected_id = str(selected_id or f"dsel_{uuid4().hex}")
        self.conn.execute(
            """
            INSERT INTO decision_selected_actions (
                selected_id, trace_id, candidate_id, selected_reason, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                selected_id = excluded.selected_id,
                candidate_id = excluded.candidate_id,
                selected_reason = excluded.selected_reason,
                metadata_json = excluded.metadata_json,
                created_at = excluded.created_at
            """,
            (
                resolved_selected_id,
                str(trace_id),
                str(candidate_id),
                str(selected_reason) if selected_reason is not None else None,
                json.dumps(dict(metadata or {}), sort_keys=True),
                str(created_at or utc_now_iso()),
            ),
        )
        self.conn.commit()
        return resolved_selected_id

    def list_candidates(self, *, trace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM decision_candidates
            WHERE trace_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (str(trace_id), max(1, int(limit))),
        ).fetchall()
        return [
            {
                "candidate_id": row["candidate_id"],
                "trace_id": row["trace_id"],
                "candidate_kind": row["candidate_kind"],
                "candidate_ref": row["candidate_ref"],
                "rationale": row["rationale"],
                "expected_value": row["expected_value"],
                "confidence": row["confidence"],
                "cost": row["cost"],
                "metadata": json.loads(str(row["metadata_json"] or "{}")),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_selected_action(self, *, trace_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM decision_selected_actions
            WHERE trace_id = ?
            LIMIT 1
            """,
            (str(trace_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "selected_id": row["selected_id"],
            "trace_id": row["trace_id"],
            "candidate_id": row["candidate_id"],
            "selected_reason": row["selected_reason"],
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "created_at": row["created_at"],
        }

    def update_trace(
        self,
        *,
        trace_id: str,
        status: str | None = None,
        summary: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
        updated_at: str | None = None,
    ) -> None:
        existing = self.conn.execute(
            "SELECT metadata_json, summary, status FROM decision_traces WHERE trace_id = ?",
            (str(trace_id),),
        ).fetchone()
        if not existing:
            raise KeyError(f"Trace not found: {trace_id}")
        metadata = json.loads(str(existing["metadata_json"] or "{}"))
        metadata.update(dict(metadata_patch or {}))
        resolved_status = str(status or existing["status"])
        resolved_summary = summary if summary is not None else existing["summary"]
        self.conn.execute(
            """
            UPDATE decision_traces
            SET status = ?, summary = ?, metadata_json = ?, updated_at = ?
            WHERE trace_id = ?
            """,
            (
                resolved_status,
                resolved_summary,
                json.dumps(metadata, sort_keys=True),
                str(updated_at or utc_now_iso()),
                str(trace_id),
            ),
        )
        self.conn.commit()

    def list_traces(
        self,
        *,
        plan_id: str | None = None,
        step_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        clauses: list[str] = []
        params: list[Any] = []
        if plan_id is not None:
            clauses.append("plan_id = ?")
            params.append(str(plan_id))
        if step_id is not None:
            clauses.append("step_id = ?")
            params.append(str(step_id))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM decision_traces
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, resolved_limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "trace_id": row["trace_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "action_class": row["action_class"],
                    "proposed_action": row["proposed_action"],
                    "status": row["status"],
                    "summary": row["summary"],
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM decision_traces WHERE trace_id = ?",
            (str(trace_id),),
        ).fetchone()
        if not row:
            return None
        events = self.conn.execute(
            """
            SELECT event_id, event_type, payload_json, created_at
            FROM decision_trace_events
            WHERE trace_id = ?
            ORDER BY created_at ASC
            """,
            (str(trace_id),),
        ).fetchall()
        candidates = self.list_candidates(trace_id=str(trace_id), limit=1000)
        selected_action = self.get_selected_action(trace_id=str(trace_id))
        return {
            "trace_id": row["trace_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "action_class": row["action_class"],
            "proposed_action": row["proposed_action"],
            "status": row["status"],
            "summary": row["summary"],
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "events": [
                {
                    "event_id": item["event_id"],
                    "event_type": item["event_type"],
                    "payload": json.loads(str(item["payload_json"] or "{}")),
                    "created_at": item["created_at"],
                }
                for item in events
            ],
            "candidates": candidates,
            "selected_action": selected_action,
        }

    def close(self) -> None:
        self.conn.close()
