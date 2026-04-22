from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import utc_now_iso


class ProjectGraphStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_nodes (
                node_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                external_ref TEXT,
                status TEXT NOT NULL,
                score REAL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_nodes_project_kind_updated
            ON project_nodes(project_id, kind, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_edges (
                edge_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                relation TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, from_node, to_node, relation)
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_edges_project_updated
            ON project_edges(project_id, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_ranked_actions (
                action_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                trace_id TEXT,
                action_type TEXT NOT NULL,
                expected_value REAL NOT NULL,
                confidence REAL NOT NULL,
                required_authority TEXT NOT NULL,
                reason TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_actions_project_created
            ON project_ranked_actions(project_id, created_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_backfill_markers (
                marker_key TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_backfill_markers_project_updated
            ON project_backfill_markers(project_id, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_backfill_cursors (
                project_id TEXT NOT NULL,
                profile_key TEXT NOT NULL,
                source TEXT NOT NULL,
                cursor_value TEXT,
                metadata_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(project_id, profile_key, source)
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_backfill_cursors_project_profile_updated
            ON project_backfill_cursors(project_id, profile_key, updated_at DESC)
            """
        )
        self.conn.commit()

    def upsert_node(
        self,
        *,
        project_id: str,
        node_id: str,
        kind: str,
        status: str,
        external_ref: str | None = None,
        score: float | None = None,
        payload: dict[str, Any] | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        resolved = {
            "node_id": str(node_id),
            "project_id": str(project_id),
            "kind": str(kind),
            "external_ref": str(external_ref) if external_ref is not None else None,
            "status": str(status),
            "score": float(score) if score is not None else None,
            "payload": dict(payload or {}),
            "updated_at": str(updated_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO project_nodes (
                node_id, project_id, kind, external_ref, status, score, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                project_id = excluded.project_id,
                kind = excluded.kind,
                external_ref = excluded.external_ref,
                status = excluded.status,
                score = excluded.score,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                resolved["node_id"],
                resolved["project_id"],
                resolved["kind"],
                resolved["external_ref"],
                resolved["status"],
                resolved["score"],
                json.dumps(resolved["payload"], sort_keys=True),
                resolved["updated_at"],
            ),
        )
        self.conn.commit()
        return resolved

    def upsert_edge(
        self,
        *,
        project_id: str,
        from_node: str,
        to_node: str,
        relation: str,
        payload: dict[str, Any] | None = None,
        edge_id: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        resolved = {
            "edge_id": str(edge_id or f"pedg_{uuid4().hex}"),
            "project_id": str(project_id),
            "from_node": str(from_node),
            "to_node": str(to_node),
            "relation": str(relation),
            "payload": dict(payload or {}),
            "updated_at": str(updated_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO project_edges (
                edge_id, project_id, from_node, to_node, relation, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, from_node, to_node, relation) DO UPDATE SET
                edge_id = excluded.edge_id,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                resolved["edge_id"],
                resolved["project_id"],
                resolved["from_node"],
                resolved["to_node"],
                resolved["relation"],
                json.dumps(resolved["payload"], sort_keys=True),
                resolved["updated_at"],
            ),
        )
        self.conn.commit()
        return resolved

    def record_action(
        self,
        *,
        project_id: str,
        action_type: str,
        reason: str,
        expected_value: float,
        confidence: float,
        required_authority: str = "none",
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        action_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        resolved = {
            "action_id": str(action_id or f"pact_{uuid4().hex}"),
            "project_id": str(project_id),
            "trace_id": str(trace_id) if trace_id is not None else None,
            "action_type": str(action_type),
            "expected_value": float(expected_value),
            "confidence": float(confidence),
            "required_authority": str(required_authority),
            "reason": str(reason),
            "metadata": dict(metadata or {}),
            "created_at": str(created_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO project_ranked_actions (
                action_id, project_id, trace_id, action_type, expected_value,
                confidence, required_authority, reason, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved["action_id"],
                resolved["project_id"],
                resolved["trace_id"],
                resolved["action_type"],
                resolved["expected_value"],
                resolved["confidence"],
                resolved["required_authority"],
                resolved["reason"],
                json.dumps(resolved["metadata"], sort_keys=True),
                resolved["created_at"],
            ),
        )
        self.conn.commit()
        return resolved

    def list_nodes(
        self,
        *,
        project_id: str,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        if kind is None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM project_nodes
                WHERE project_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(project_id), resolved_limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM project_nodes
                WHERE project_id = ? AND kind = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(project_id), str(kind), resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "node_id": row["node_id"],
                    "project_id": row["project_id"],
                    "kind": row["kind"],
                    "external_ref": row["external_ref"],
                    "status": row["status"],
                    "score": row["score"],
                    "payload": json.loads(str(row["payload_json"] or "{}")),
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def list_edges(self, *, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM project_edges
            WHERE project_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (str(project_id), max(1, int(limit))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "edge_id": row["edge_id"],
                    "project_id": row["project_id"],
                    "from_node": row["from_node"],
                    "to_node": row["to_node"],
                    "relation": row["relation"],
                    "payload": json.loads(str(row["payload_json"] or "{}")),
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def list_actions(self, *, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM project_ranked_actions
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(project_id), max(1, int(limit))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "action_id": row["action_id"],
                    "project_id": row["project_id"],
                    "trace_id": row["trace_id"],
                    "action_type": row["action_type"],
                    "expected_value": row["expected_value"],
                    "confidence": row["confidence"],
                    "required_authority": row["required_authority"],
                    "reason": row["reason"],
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "created_at": row["created_at"],
                }
            )
        return out

    def has_backfill_marker(self, *, project_id: str, marker_key: str) -> bool:
        row = self.conn.execute(
            """
            SELECT marker_key
            FROM project_backfill_markers
            WHERE project_id = ? AND marker_key = ?
            LIMIT 1
            """,
            (str(project_id), str(marker_key)),
        ).fetchone()
        return row is not None

    def record_backfill_marker(
        self,
        *,
        project_id: str,
        source: str,
        marker_key: str,
        payload: dict[str, Any] | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        resolved = {
            "marker_key": str(marker_key),
            "project_id": str(project_id),
            "source": str(source),
            "payload": dict(payload or {}),
            "updated_at": str(updated_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO project_backfill_markers (
                marker_key, project_id, source, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(marker_key) DO UPDATE SET
                project_id = excluded.project_id,
                source = excluded.source,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                resolved["marker_key"],
                resolved["project_id"],
                resolved["source"],
                json.dumps(resolved["payload"], sort_keys=True),
                resolved["updated_at"],
            ),
        )
        self.conn.commit()
        return resolved

    def upsert_backfill_cursor(
        self,
        *,
        project_id: str,
        profile_key: str,
        source: str,
        cursor_value: str | None,
        metadata: dict[str, Any] | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        resolved = {
            "project_id": str(project_id),
            "profile_key": str(profile_key),
            "source": str(source),
            "cursor_value": str(cursor_value) if cursor_value is not None else None,
            "metadata": dict(metadata or {}),
            "updated_at": str(updated_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO project_backfill_cursors (
                project_id, profile_key, source, cursor_value, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, profile_key, source) DO UPDATE SET
                cursor_value = excluded.cursor_value,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                resolved["project_id"],
                resolved["profile_key"],
                resolved["source"],
                resolved["cursor_value"],
                json.dumps(resolved["metadata"], sort_keys=True),
                resolved["updated_at"],
            ),
        )
        self.conn.commit()
        return resolved

    def list_backfill_cursors(
        self,
        *,
        project_id: str,
        profile_key: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM project_backfill_cursors
            WHERE project_id = ? AND profile_key = ?
            ORDER BY source ASC
            LIMIT ?
            """,
            (str(project_id), str(profile_key), max(1, int(limit))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "project_id": row["project_id"],
                    "profile_key": row["profile_key"],
                    "source": row["source"],
                    "cursor_value": row["cursor_value"],
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def close(self) -> None:
        self.conn.close()
