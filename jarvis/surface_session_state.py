from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SurfaceSessionStateStore:
    """Tracks continuity anchors for one Jarvis across many OpenClaw surfaces."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS surface_session_state (
                session_key TEXT PRIMARY KEY,
                surface_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                session_id TEXT NOT NULL,
                operator_identity TEXT,
                paired_node_id TEXT,
                last_relationship_mode TEXT,
                last_consciousness_revision TEXT,
                last_seen_contract_hash TEXT,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_surface_session_state_status
            ON surface_session_state(status, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_surface_session_state_surface
            ON surface_session_state(surface_id, updated_at DESC)
            """
        )
        self.conn.commit()

    def touch_event(
        self,
        *,
        surface_id: str,
        channel_type: str,
        session_id: str,
        operator_identity: str | None = None,
        paired_node_id: str | None = None,
        relationship_mode: str | None = None,
        consciousness_revision: str | None = None,
        contract_hash: str | None = None,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_surface = str(surface_id or "").strip() or "openclaw"
        normalized_session = str(session_id or "").strip() or "default"
        normalized_key = f"{normalized_surface}:{normalized_session}"
        normalized_channel = str(channel_type or "").strip().lower() or "surface"
        normalized_status = str(status or "").strip().lower() or "active"

        existing = self.get(normalized_key)
        now = _utc_now_iso()
        merged_metadata = dict(existing.get("metadata") or {}) if existing else {}
        merged_metadata.update(dict(metadata or {}))
        first_seen_at = str(existing.get("first_seen_at") or now) if existing else now
        last_relationship_mode = (
            str(relationship_mode).strip().lower()
            if relationship_mode
            else str(existing.get("last_relationship_mode") or "").strip().lower() or None
        )
        last_revision = (
            str(consciousness_revision).strip()
            if consciousness_revision
            else (str(existing.get("last_consciousness_revision") or "").strip() if existing else "") or None
        )
        last_contract_hash = (
            str(contract_hash).strip()
            if contract_hash
            else (str(existing.get("last_seen_contract_hash") or "").strip() if existing else "") or None
        )

        self.conn.execute(
            """
            INSERT INTO surface_session_state(
                session_key, surface_id, channel_type, session_id, operator_identity, paired_node_id,
                last_relationship_mode, last_consciousness_revision, last_seen_contract_hash, status,
                metadata_json, first_seen_at, last_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_key) DO UPDATE SET
                surface_id = excluded.surface_id,
                channel_type = excluded.channel_type,
                session_id = excluded.session_id,
                operator_identity = COALESCE(excluded.operator_identity, surface_session_state.operator_identity),
                paired_node_id = COALESCE(excluded.paired_node_id, surface_session_state.paired_node_id),
                last_relationship_mode = COALESCE(excluded.last_relationship_mode, surface_session_state.last_relationship_mode),
                last_consciousness_revision = COALESCE(excluded.last_consciousness_revision, surface_session_state.last_consciousness_revision),
                last_seen_contract_hash = COALESCE(excluded.last_seen_contract_hash, surface_session_state.last_seen_contract_hash),
                status = excluded.status,
                metadata_json = excluded.metadata_json,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
            """,
            (
                normalized_key,
                normalized_surface,
                normalized_channel,
                normalized_session,
                str(operator_identity or "").strip() or None,
                str(paired_node_id or "").strip() or None,
                last_relationship_mode,
                last_revision,
                last_contract_hash,
                normalized_status,
                json.dumps(merged_metadata, sort_keys=True),
                first_seen_at,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get(normalized_key) or {}

    def get(self, session_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM surface_session_state WHERE session_key = ?",
            (str(session_key),),
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_sessions(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_status = str(status or "").strip().lower()
        if normalized_status and normalized_status != "all":
            rows = self.conn.execute(
                """
                SELECT *
                FROM surface_session_state
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (normalized_status, max(1, int(limit))),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM surface_session_state
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def close(self) -> None:
        self.conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_key": row["session_key"],
            "surface_id": row["surface_id"],
            "channel_type": row["channel_type"],
            "session_id": row["session_id"],
            "operator_identity": row["operator_identity"],
            "paired_node_id": row["paired_node_id"],
            "last_relationship_mode": row["last_relationship_mode"],
            "last_consciousness_revision": row["last_consciousness_revision"],
            "last_seen_contract_hash": row["last_seen_contract_hash"],
            "status": row["status"],
            "metadata": json.loads(row["metadata_json"]),
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "updated_at": row["updated_at"],
        }
