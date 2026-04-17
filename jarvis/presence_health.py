from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PresenceHealthStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_bridge_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                connection_status TEXT NOT NULL,
                connected INTEGER NOT NULL,
                last_event_type TEXT,
                last_event_at TEXT,
                details_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_heartbeats (
                heartbeat_id TEXT PRIMARY KEY,
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO presence_bridge_state(
                id, connection_status, connected, last_event_type, last_event_at, details_json, updated_at
            ) VALUES (1, 'disconnected', 0, NULL, NULL, ?, ?)
            """,
            (json.dumps({}, sort_keys=True), now),
        )
        self.conn.commit()

    def set_bridge_status(
        self,
        *,
        connection_status: str,
        connected: bool,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE presence_bridge_state
            SET connection_status = ?, connected = ?, details_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                str(connection_status).strip().lower() or "unknown",
                1 if connected else 0,
                json.dumps(dict(details or {}), sort_keys=True),
                now,
            ),
        )
        self.conn.commit()
        return self.get_bridge_state()

    def record_gateway_event(
        self,
        *,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        current = self.get_bridge_state()
        merged = dict(current.get("details") or {})
        merged.update(dict(details or {}))
        self.conn.execute(
            """
            UPDATE presence_bridge_state
            SET last_event_type = ?, last_event_at = ?, details_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (str(event_type), now, json.dumps(merged, sort_keys=True), now),
        )
        self.conn.commit()
        return self.get_bridge_state()

    def get_bridge_state(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM presence_bridge_state WHERE id = 1").fetchone()
        if not row:
            return {
                "connection_status": "disconnected",
                "connected": False,
                "last_event_type": None,
                "last_event_at": None,
                "details": {},
                "updated_at": _utc_now_iso(),
            }
        return {
            "connection_status": row["connection_status"],
            "connected": bool(row["connected"]),
            "last_event_type": row["last_event_type"],
            "last_event_at": row["last_event_at"],
            "details": json.loads(row["details_json"]),
            "updated_at": row["updated_at"],
        }

    def record_heartbeat(self, *, heartbeat_id: str, summary: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO presence_heartbeats(heartbeat_id, summary_json, created_at)
            VALUES (?, ?, ?)
            """,
            (str(heartbeat_id), json.dumps(dict(summary or {}), sort_keys=True), now),
        )
        self.conn.commit()
        return {"heartbeat_id": heartbeat_id, "summary": dict(summary or {}), "created_at": now}

    def latest_heartbeat(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM presence_heartbeats
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return {
            "heartbeat_id": row["heartbeat_id"],
            "summary": json.loads(row["summary_json"]),
            "created_at": row["created_at"],
        }

    def list_heartbeats(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM presence_heartbeats
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [
            {
                "heartbeat_id": row["heartbeat_id"],
                "summary": json.loads(row["summary_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()
