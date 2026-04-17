from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DialogueStateStore:
    """Persistent conversation-state substrate for live dialogue quality."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_threads (
                thread_id TEXT PRIMARY KEY,
                surface_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                session_key TEXT,
                status TEXT NOT NULL,
                mode TEXT,
                objective_hint TEXT,
                summary_text TEXT,
                unresolved_questions_json TEXT NOT NULL,
                active_hypotheses_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(surface_id, session_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_turns (
                turn_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                user_text TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                context_refs_json TEXT NOT NULL,
                candidate_reply TEXT,
                final_reply TEXT NOT NULL,
                critique_json TEXT NOT NULL,
                mode TEXT,
                pushback_triggered INTEGER NOT NULL DEFAULT 0,
                continuity_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dialogue_threads_updated ON dialogue_threads(updated_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dialogue_turns_thread_idx ON dialogue_turns(thread_id, turn_index DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dialogue_turns_created ON dialogue_turns(created_at DESC)"
        )
        self.conn.commit()

    @staticmethod
    def _decode_json(raw: str, fallback: Any) -> Any:
        try:
            parsed = json.loads(str(raw or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback
        return parsed

    def _row_to_thread(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        unresolved = self._decode_json(row["unresolved_questions_json"], [])
        hypotheses = self._decode_json(row["active_hypotheses_json"], [])
        return {
            "thread_id": row["thread_id"],
            "surface_id": row["surface_id"],
            "session_id": row["session_id"],
            "session_key": row["session_key"],
            "status": row["status"],
            "mode": row["mode"],
            "objective_hint": row["objective_hint"],
            "summary_text": row["summary_text"],
            "unresolved_questions": unresolved if isinstance(unresolved, list) else [],
            "active_hypotheses": hypotheses if isinstance(hypotheses, list) else [],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_turn(self, row: sqlite3.Row) -> dict[str, Any]:
        intent = self._decode_json(row["intent_json"], {})
        context_refs = self._decode_json(row["context_refs_json"], [])
        critique = self._decode_json(row["critique_json"], {})
        continuity = self._decode_json(row["continuity_json"], {})
        return {
            "turn_id": row["turn_id"],
            "thread_id": row["thread_id"],
            "turn_index": int(row["turn_index"]),
            "user_text": row["user_text"],
            "intent": intent if isinstance(intent, dict) else {},
            "context_refs": context_refs if isinstance(context_refs, list) else [],
            "candidate_reply": row["candidate_reply"],
            "final_reply": row["final_reply"],
            "critique": critique if isinstance(critique, dict) else {},
            "mode": row["mode"],
            "pushback_triggered": bool(row["pushback_triggered"]),
            "continuity": continuity if isinstance(continuity, dict) else {},
            "created_at": row["created_at"],
        }

    def get_thread(self, *, surface_id: str, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM dialogue_threads
            WHERE surface_id = ? AND session_id = ?
            """,
            (str(surface_id), str(session_id)),
        ).fetchone()
        return self._row_to_thread(row)

    def upsert_thread(
        self,
        *,
        surface_id: str,
        session_id: str,
        session_key: str | None = None,
        mode: str | None = None,
        objective_hint: str | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        existing = self.get_thread(surface_id=surface_id, session_id=session_id)
        now = _utc_now_iso()
        normalized_mode = str(mode or "").strip().lower() or None
        if existing:
            self.conn.execute(
                """
                UPDATE dialogue_threads
                SET session_key = COALESCE(?, session_key),
                    mode = COALESCE(?, mode),
                    objective_hint = COALESCE(?, objective_hint),
                    status = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    str(session_key).strip() if session_key else None,
                    normalized_mode,
                    str(objective_hint).strip() if objective_hint else None,
                    str(status or "active").strip() or "active",
                    now,
                    existing["thread_id"],
                ),
            )
            self.conn.commit()
            return self.get_thread(surface_id=surface_id, session_id=session_id) or existing

        thread_id = f"dthread_{uuid4().hex}"
        self.conn.execute(
            """
            INSERT INTO dialogue_threads(
                thread_id, surface_id, session_id, session_key, status, mode,
                objective_hint, summary_text, unresolved_questions_json, active_hypotheses_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                str(surface_id),
                str(session_id),
                str(session_key).strip() if session_key else None,
                str(status or "active").strip() or "active",
                normalized_mode,
                str(objective_hint).strip() if objective_hint else None,
                None,
                json.dumps([], sort_keys=True),
                json.dumps([], sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_thread(surface_id=surface_id, session_id=session_id) or {
            "thread_id": thread_id,
            "surface_id": str(surface_id),
            "session_id": str(session_id),
            "session_key": str(session_key).strip() if session_key else None,
            "status": str(status or "active").strip() or "active",
            "mode": normalized_mode,
            "objective_hint": str(objective_hint).strip() if objective_hint else None,
            "summary_text": None,
            "unresolved_questions": [],
            "active_hypotheses": [],
            "created_at": now,
            "updated_at": now,
        }

    def update_thread_state(
        self,
        *,
        thread_id: str,
        summary_text: str | None = None,
        unresolved_questions: list[str] | None = None,
        active_hypotheses: list[str] | None = None,
        objective_hint: str | None = None,
        mode: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM dialogue_threads WHERE thread_id = ?",
            (str(thread_id),),
        ).fetchone()
        if not row:
            return None
        thread = self._row_to_thread(row) or {}
        next_summary = (
            str(summary_text).strip()
            if summary_text is not None
            else str(thread.get("summary_text") or "").strip() or None
        )
        next_unresolved = (
            [str(item).strip() for item in list(unresolved_questions or []) if str(item).strip()]
            if unresolved_questions is not None
            else list(thread.get("unresolved_questions") or [])
        )
        next_hypotheses = (
            [str(item).strip() for item in list(active_hypotheses or []) if str(item).strip()]
            if active_hypotheses is not None
            else list(thread.get("active_hypotheses") or [])
        )
        next_objective = (
            str(objective_hint).strip()
            if objective_hint is not None
            else str(thread.get("objective_hint") or "").strip() or None
        )
        next_mode = (
            str(mode).strip().lower()
            if mode is not None
            else str(thread.get("mode") or "").strip().lower() or None
        )
        next_status = (
            str(status).strip().lower()
            if status is not None
            else str(thread.get("status") or "active").strip().lower() or "active"
        )
        self.conn.execute(
            """
            UPDATE dialogue_threads
            SET summary_text = ?,
                unresolved_questions_json = ?,
                active_hypotheses_json = ?,
                objective_hint = ?,
                mode = ?,
                status = ?,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (
                next_summary,
                json.dumps(next_unresolved, sort_keys=True),
                json.dumps(next_hypotheses, sort_keys=True),
                next_objective,
                next_mode,
                next_status,
                _utc_now_iso(),
                str(thread_id),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM dialogue_threads WHERE thread_id = ?",
            (str(thread_id),),
        ).fetchone()
        return self._row_to_thread(row)

    def record_turn(
        self,
        *,
        thread_id: str,
        user_text: str,
        intent: dict[str, Any] | None = None,
        context_refs: list[str] | None = None,
        candidate_reply: str | None = None,
        final_reply: str,
        critique: dict[str, Any] | None = None,
        mode: str | None = None,
        pushback_triggered: bool = False,
        continuity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(turn_index), 0) AS max_idx FROM dialogue_turns WHERE thread_id = ?",
            (str(thread_id),),
        ).fetchone()
        next_idx = int(row["max_idx"] or 0) + 1
        turn_id = f"dturn_{uuid4().hex}"
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO dialogue_turns(
                turn_id, thread_id, turn_index, user_text, intent_json, context_refs_json,
                candidate_reply, final_reply, critique_json, mode, pushback_triggered,
                continuity_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                str(thread_id),
                next_idx,
                str(user_text or ""),
                json.dumps(dict(intent or {}), sort_keys=True),
                json.dumps([str(item).strip() for item in list(context_refs or []) if str(item).strip()], sort_keys=True),
                str(candidate_reply or "").strip() or None,
                str(final_reply or "").strip(),
                json.dumps(dict(critique or {}), sort_keys=True),
                str(mode or "").strip().lower() or None,
                1 if bool(pushback_triggered) else 0,
                json.dumps(dict(continuity or {}), sort_keys=True),
                now,
            ),
        )
        self.conn.execute(
            "UPDATE dialogue_threads SET updated_at = ? WHERE thread_id = ?",
            (now, str(thread_id)),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM dialogue_turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        return self._row_to_turn(row) if row else {
            "turn_id": turn_id,
            "thread_id": str(thread_id),
            "turn_index": next_idx,
            "user_text": str(user_text or ""),
            "intent": dict(intent or {}),
            "context_refs": list(context_refs or []),
            "candidate_reply": str(candidate_reply or "").strip() or None,
            "final_reply": str(final_reply or "").strip(),
            "critique": dict(critique or {}),
            "mode": str(mode or "").strip().lower() or None,
            "pushback_triggered": bool(pushback_triggered),
            "continuity": dict(continuity or {}),
            "created_at": now,
        }

    def list_recent_turns(self, *, thread_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM dialogue_turns
            WHERE thread_id = ?
            ORDER BY turn_index DESC
            LIMIT ?
            """,
            (str(thread_id), max(1, int(limit))),
        ).fetchall()
        out = [self._row_to_turn(row) for row in rows]
        out.reverse()
        return out

    def list_threads(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(str(status).strip().lower())
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT * FROM dialogue_threads "
            f"{where_clause} "
            "ORDER BY updated_at DESC "
            "LIMIT ?"
        )
        params.append(max(1, int(limit)))
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_thread(row) for row in rows if row]

    def close(self) -> None:
        self.conn.close()

