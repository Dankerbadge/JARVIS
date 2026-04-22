from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import utc_now_iso


class SuggestionFeedbackStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS suggestion_feedback (
                feedback_id TEXT PRIMARY KEY,
                suggestion_id TEXT NOT NULL,
                source_trace_id TEXT,
                accepted INTEGER NOT NULL,
                action_taken TEXT,
                utility_score REAL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_suggestion_feedback_trace_created
            ON suggestion_feedback(source_trace_id, created_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_suggestion_feedback_suggestion_created
            ON suggestion_feedback(suggestion_id, created_at DESC)
            """
        )
        self.conn.commit()

    def record_feedback(
        self,
        *,
        suggestion_id: str,
        accepted: bool,
        source_trace_id: str | None = None,
        action_taken: str | None = None,
        utility_score: float | None = None,
        metadata: dict[str, Any] | None = None,
        feedback_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        resolved_feedback_id = str(feedback_id or f"sfb_{uuid4().hex}")
        resolved_payload = {
            "feedback_id": resolved_feedback_id,
            "suggestion_id": str(suggestion_id),
            "source_trace_id": str(source_trace_id) if source_trace_id else None,
            "accepted": bool(accepted),
            "action_taken": str(action_taken) if action_taken is not None else None,
            "utility_score": float(utility_score) if utility_score is not None else None,
            "metadata": dict(metadata or {}),
            "created_at": str(created_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO suggestion_feedback (
                feedback_id, suggestion_id, source_trace_id, accepted,
                action_taken, utility_score, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_payload["feedback_id"],
                resolved_payload["suggestion_id"],
                resolved_payload["source_trace_id"],
                1 if resolved_payload["accepted"] else 0,
                resolved_payload["action_taken"],
                resolved_payload["utility_score"],
                json.dumps(resolved_payload["metadata"], sort_keys=True),
                resolved_payload["created_at"],
            ),
        )
        self.conn.commit()
        return resolved_payload

    def list_feedback(
        self,
        *,
        suggestion_id: str | None = None,
        source_trace_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        clauses: list[str] = []
        params: list[Any] = []
        if suggestion_id is not None:
            clauses.append("suggestion_id = ?")
            params.append(str(suggestion_id))
        if source_trace_id is not None:
            clauses.append("source_trace_id = ?")
            params.append(str(source_trace_id))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM suggestion_feedback
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
                    "feedback_id": row["feedback_id"],
                    "suggestion_id": row["suggestion_id"],
                    "source_trace_id": row["source_trace_id"],
                    "accepted": bool(row["accepted"]),
                    "action_taken": row["action_taken"],
                    "utility_score": row["utility_score"],
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "created_at": row["created_at"],
                }
            )
        return out

    def latest_feedback_for_trace(self, trace_id: str) -> dict[str, Any] | None:
        rows = self.list_feedback(source_trace_id=trace_id, limit=1)
        return rows[0] if rows else None

    def close(self) -> None:
        self.conn.close()
