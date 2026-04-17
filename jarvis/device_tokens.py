from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeviceTokenStore:
    """Stores paired-node identity and token references (never raw secrets)."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_nodes (
                node_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                pairing_status TEXT NOT NULL,
                gateway_token_ref TEXT NOT NULL,
                node_token_ref TEXT NOT NULL,
                paired_at TEXT NOT NULL,
                rotated_at TEXT,
                revoked_at TEXT,
                last_seen_at TEXT,
                metadata_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_presence_nodes_status
            ON presence_nodes(pairing_status, updated_at DESC)
            """
        )
        self.conn.commit()

    def upsert_pairing(
        self,
        *,
        node_id: str,
        device_id: str,
        owner_id: str,
        gateway_token_ref: str,
        node_token_ref: str,
        pairing_status: str = "paired",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO presence_nodes(
                node_id, device_id, owner_id, pairing_status, gateway_token_ref, node_token_ref,
                paired_at, rotated_at, revoked_at, last_seen_at, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                device_id = excluded.device_id,
                owner_id = excluded.owner_id,
                pairing_status = excluded.pairing_status,
                gateway_token_ref = excluded.gateway_token_ref,
                node_token_ref = excluded.node_token_ref,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                str(node_id).strip(),
                str(device_id).strip(),
                str(owner_id).strip(),
                str(pairing_status).strip().lower() or "paired",
                str(gateway_token_ref).strip(),
                str(node_token_ref).strip(),
                now,
                now,
                json.dumps(dict(metadata or {}), sort_keys=True),
                now,
            ),
        )
        self.conn.commit()
        return self.get_node(node_id) or {}

    def mark_seen(
        self,
        *,
        node_id: str,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_node(node_id)
        if not current:
            return None
        metadata = dict(current.get("metadata") or {})
        metadata.update(dict(metadata_patch or {}))
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE presence_nodes
            SET last_seen_at = ?, metadata_json = ?, updated_at = ?
            WHERE node_id = ?
            """,
            (now, json.dumps(metadata, sort_keys=True), now, str(node_id).strip()),
        )
        self.conn.commit()
        return self.get_node(node_id)

    def rotate_node_token_ref(self, *, node_id: str, node_token_ref: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE presence_nodes
            SET node_token_ref = ?, rotated_at = ?, updated_at = ?
            WHERE node_id = ?
            """,
            (str(node_token_ref).strip(), now, now, str(node_id).strip()),
        )
        self.conn.commit()
        return self.get_node(node_id)

    def revoke_node(self, *, node_id: str, reason: str = "") -> dict[str, Any] | None:
        current = self.get_node(node_id)
        if not current:
            return None
        metadata = dict(current.get("metadata") or {})
        if reason:
            metadata["revocation_reason"] = str(reason)
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE presence_nodes
            SET pairing_status = 'revoked',
                revoked_at = ?,
                metadata_json = ?,
                updated_at = ?
            WHERE node_id = ?
            """,
            (now, json.dumps(metadata, sort_keys=True), now, str(node_id).strip()),
        )
        self.conn.commit()
        return self.get_node(node_id)

    def update_pairing_status(
        self,
        *,
        node_id: str,
        pairing_status: str,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_node(node_id)
        if not current:
            return None
        metadata = dict(current.get("metadata") or {})
        metadata.update(dict(metadata_patch or {}))
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE presence_nodes
            SET pairing_status = ?, metadata_json = ?, updated_at = ?
            WHERE node_id = ?
            """,
            (
                str(pairing_status).strip().lower() or "unknown",
                json.dumps(metadata, sort_keys=True),
                now,
                str(node_id).strip(),
            ),
        )
        self.conn.commit()
        return self.get_node(node_id)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM presence_nodes WHERE node_id = ?",
            (str(node_id).strip(),),
        ).fetchone()
        if not row:
            return None
        return {
            "node_id": row["node_id"],
            "device_id": row["device_id"],
            "owner_id": row["owner_id"],
            "pairing_status": row["pairing_status"],
            "gateway_token_ref": row["gateway_token_ref"],
            "node_token_ref": row["node_token_ref"],
            "paired_at": row["paired_at"],
            "rotated_at": row["rotated_at"],
            "revoked_at": row["revoked_at"],
            "last_seen_at": row["last_seen_at"],
            "metadata": json.loads(row["metadata_json"]),
            "updated_at": row["updated_at"],
        }

    def list_nodes(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status and str(status).strip().lower() != "all":
            rows = self.conn.execute(
                """
                SELECT *
                FROM presence_nodes
                WHERE pairing_status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(status).strip().lower(), max(1, int(limit))),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM presence_nodes
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "node_id": row["node_id"],
                "device_id": row["device_id"],
                "owner_id": row["owner_id"],
                "pairing_status": row["pairing_status"],
                "gateway_token_ref": row["gateway_token_ref"],
                "node_token_ref": row["node_token_ref"],
                "paired_at": row["paired_at"],
                "rotated_at": row["rotated_at"],
                "revoked_at": row["revoked_at"],
                "last_seen_at": row["last_seen_at"],
                "metadata": json.loads(row["metadata_json"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()
