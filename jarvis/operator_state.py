from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperatorStateStore:
    """Persistent operator preferences for interruption governance."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                focus_mode_domain TEXT,
                quiet_start_hour INTEGER,
                quiet_end_hour INTEGER,
                suppress_until TEXT,
                suppression_reason TEXT,
                pondering_mode_enabled INTEGER NOT NULL DEFAULT 0,
                pondering_mode_style TEXT NOT NULL DEFAULT 'open_discussion',
                pondering_mode_min_confidence REAL NOT NULL DEFAULT 0.78,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_preference_events (
                event_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Lightweight forward migration for older databases created before pondering-mode fields.
        existing = {
            str(row["name"]): str(row["type"] or "")
            for row in self.conn.execute("PRAGMA table_info(operator_preferences)").fetchall()
        }
        if "pondering_mode_enabled" not in existing:
            self.conn.execute(
                "ALTER TABLE operator_preferences ADD COLUMN pondering_mode_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "pondering_mode_style" not in existing:
            self.conn.execute(
                "ALTER TABLE operator_preferences ADD COLUMN pondering_mode_style TEXT NOT NULL DEFAULT 'open_discussion'"
            )
        if "pondering_mode_min_confidence" not in existing:
            self.conn.execute(
                "ALTER TABLE operator_preferences ADD COLUMN pondering_mode_min_confidence REAL NOT NULL DEFAULT 0.78"
            )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO operator_preferences(
                id, focus_mode_domain, quiet_start_hour, quiet_end_hour,
                suppress_until, suppression_reason,
                pondering_mode_enabled, pondering_mode_style, pondering_mode_min_confidence,
                updated_at
            ) VALUES (1, NULL, NULL, NULL, NULL, NULL, 0, 'open_discussion', 0.78, ?)
            """,
            (_utc_now_iso(),),
        )
        self.conn.commit()

    def _log_event(self, *, action: str, actor: str, details: dict[str, Any]) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO operator_preference_events(event_id, action, actor, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"opr_{uuid4().hex}",
                action,
                actor,
                json.dumps(details, sort_keys=True),
                now,
            ),
        )
        self.conn.commit()

    def get_preferences(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM operator_preferences WHERE id = 1"
        ).fetchone()
        if not row:
            return {
                "focus_mode_domain": None,
                "quiet_hours": None,
                "suppress_until": None,
                "suppression_reason": None,
                "pondering_mode": {
                    "enabled": False,
                    "style": "open_discussion",
                    "min_confidence_for_understood": 0.78,
                },
            }
        quiet = None
        if row["quiet_start_hour"] is not None and row["quiet_end_hour"] is not None:
            quiet = {
                "start_hour": int(row["quiet_start_hour"]),
                "end_hour": int(row["quiet_end_hour"]),
            }
        try:
            min_confidence = float(row["pondering_mode_min_confidence"])
        except (TypeError, ValueError):
            min_confidence = 0.78
        min_confidence = max(0.5, min(0.99, min_confidence))
        return {
            "focus_mode_domain": row["focus_mode_domain"],
            "quiet_hours": quiet,
            "suppress_until": row["suppress_until"],
            "suppression_reason": row["suppression_reason"],
            "pondering_mode": {
                "enabled": bool(row["pondering_mode_enabled"]),
                "style": str(row["pondering_mode_style"] or "open_discussion").strip() or "open_discussion",
                "min_confidence_for_understood": round(min_confidence, 2),
            },
            "updated_at": row["updated_at"],
        }

    def set_focus_mode(self, *, domain: str | None, actor: str = "user") -> dict[str, Any]:
        normalized = str(domain).strip().lower() if domain else None
        if normalized in {"off", "none", "null", ""}:
            normalized = None
        self.conn.execute(
            """
            UPDATE operator_preferences
            SET focus_mode_domain = ?, updated_at = ?
            WHERE id = 1
            """,
            (normalized, _utc_now_iso()),
        )
        self.conn.commit()
        self._log_event(
            action="set_focus_mode",
            actor=actor,
            details={"focus_mode_domain": normalized},
        )
        return self.get_preferences()

    def set_quiet_hours(
        self,
        *,
        start_hour: int | None,
        end_hour: int | None,
        actor: str = "user",
    ) -> dict[str, Any]:
        s = None if start_hour is None else int(start_hour) % 24
        e = None if end_hour is None else int(end_hour) % 24
        self.conn.execute(
            """
            UPDATE operator_preferences
            SET quiet_start_hour = ?, quiet_end_hour = ?, updated_at = ?
            WHERE id = 1
            """,
            (s, e, _utc_now_iso()),
        )
        self.conn.commit()
        self._log_event(
            action="set_quiet_hours",
            actor=actor,
            details={"start_hour": s, "end_hour": e},
        )
        return self.get_preferences()

    def set_suppress_until(
        self,
        *,
        until_iso: str | None,
        reason: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        normalized_until = str(until_iso).strip() if until_iso else None
        if normalized_until:
            if normalized_until.endswith("Z"):
                normalized_until = normalized_until[:-1] + "+00:00"
            # Validate format.
            datetime.fromisoformat(normalized_until)
        self.conn.execute(
            """
            UPDATE operator_preferences
            SET suppress_until = ?, suppression_reason = ?, updated_at = ?
            WHERE id = 1
            """,
            (normalized_until, str(reason or "").strip(), _utc_now_iso()),
        )
        self.conn.commit()
        self._log_event(
            action="set_suppress_until",
            actor=actor,
            details={"suppress_until": normalized_until, "reason": str(reason or "").strip()},
        )
        return self.get_preferences()

    def set_pondering_mode(
        self,
        *,
        enabled: bool | None = None,
        style: str | None = None,
        min_confidence_for_understood: float | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        current = self.get_preferences()
        current_mode = current.get("pondering_mode") if isinstance(current.get("pondering_mode"), dict) else {}
        next_enabled = (
            bool(enabled)
            if enabled is not None
            else bool(current_mode.get("enabled"))
        )
        next_style = (
            str(style or "").strip().lower()
            if style is not None
            else str(current_mode.get("style") or "open_discussion").strip().lower()
        )
        if next_style not in {"open_discussion", "socratic", "guided_clarification"}:
            next_style = "open_discussion"
        if min_confidence_for_understood is None:
            raw_min_confidence = current_mode.get("min_confidence_for_understood")
            try:
                next_min_confidence = float(raw_min_confidence)
            except (TypeError, ValueError):
                next_min_confidence = 0.78
        else:
            next_min_confidence = float(min_confidence_for_understood)
        next_min_confidence = max(0.5, min(0.99, next_min_confidence))

        self.conn.execute(
            """
            UPDATE operator_preferences
            SET pondering_mode_enabled = ?,
                pondering_mode_style = ?,
                pondering_mode_min_confidence = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                1 if next_enabled else 0,
                next_style,
                next_min_confidence,
                _utc_now_iso(),
            ),
        )
        self.conn.commit()
        self._log_event(
            action="set_pondering_mode",
            actor=actor,
            details={
                "enabled": next_enabled,
                "style": next_style,
                "min_confidence_for_understood": round(next_min_confidence, 2),
            },
        )
        return self.get_preferences()

    def list_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM operator_preference_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "event_id": row["event_id"],
                    "action": row["action"],
                    "actor": row["actor"],
                    "details": json.loads(row["details_json"]),
                    "created_at": row["created_at"],
                }
            )
        return out

    def close(self) -> None:
        self.conn.close()
