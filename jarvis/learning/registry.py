from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import utc_now_iso


class LearningPolicyRegistry:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_policy_registry (
                policy_id TEXT PRIMARY KEY,
                task_family TEXT NOT NULL,
                policy_name TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                policy_status TEXT NOT NULL DEFAULT 'active',
                superseded_by_policy_id TEXT,
                disabled_at TEXT,
                disabled_by TEXT,
                disable_reason TEXT,
                promoted_by TEXT NOT NULL,
                promoted_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_policy_audit (
                audit_id TEXT PRIMARY KEY,
                policy_id TEXT,
                task_family TEXT NOT NULL,
                policy_name TEXT NOT NULL,
                decision TEXT NOT NULL,
                actor TEXT NOT NULL,
                gate_json TEXT NOT NULL,
                report_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_policy_family_promoted
            ON learning_policy_registry(task_family, promoted_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_policy_audit_family_created
            ON learning_policy_audit(task_family, created_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_policy_audit_policy_created
            ON learning_policy_audit(policy_id, created_at DESC)
            """
        )
        self._ensure_column("learning_policy_registry", "policy_status", "TEXT NOT NULL DEFAULT 'active'")
        self._ensure_column("learning_policy_registry", "superseded_by_policy_id", "TEXT")
        self._ensure_column("learning_policy_registry", "disabled_at", "TEXT")
        self._ensure_column("learning_policy_registry", "disabled_by", "TEXT")
        self._ensure_column("learning_policy_registry", "disable_reason", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, column_sql: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {str(row["name"]) for row in rows}
        if column in names:
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")

    def register_policy(
        self,
        *,
        task_family: str,
        policy_name: str,
        metrics: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        promoted_by: str = "runtime",
        policy_id: str | None = None,
        promoted_at: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "policy_id": str(policy_id or f"lpol_{uuid4().hex}"),
            "task_family": str(task_family),
            "policy_name": str(policy_name),
            "metrics": dict(metrics or {}),
            "metadata": dict(metadata or {}),
            "policy_status": "active",
            "superseded_by_policy_id": None,
            "disabled_at": None,
            "disabled_by": None,
            "disable_reason": None,
            "promoted_by": str(promoted_by or "runtime"),
            "promoted_at": str(promoted_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO learning_policy_registry (
                policy_id, task_family, policy_name, metrics_json, metadata_json, policy_status,
                superseded_by_policy_id, disabled_at, disabled_by, disable_reason, promoted_by, promoted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["policy_id"],
                payload["task_family"],
                payload["policy_name"],
                json.dumps(payload["metrics"], sort_keys=True),
                json.dumps(payload["metadata"], sort_keys=True),
                payload["policy_status"],
                payload["superseded_by_policy_id"],
                payload["disabled_at"],
                payload["disabled_by"],
                payload["disable_reason"],
                payload["promoted_by"],
                payload["promoted_at"],
            ),
        )
        self.conn.commit()
        return payload

    def list_policies(
        self,
        *,
        task_family: str | None = None,
        policy_status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        clauses: list[str] = []
        params: list[Any] = []
        if task_family is not None:
            clauses.append("task_family = ?")
            params.append(str(task_family))
        if policy_status is not None:
            clauses.append("policy_status = ?")
            params.append(str(policy_status))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM learning_policy_registry
            {where_sql}
            ORDER BY promoted_at DESC
            LIMIT ?
            """,
            (*params, resolved_limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "policy_id": row["policy_id"],
                    "task_family": row["task_family"],
                    "policy_name": row["policy_name"],
                    "metrics": json.loads(str(row["metrics_json"] or "{}")),
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "policy_status": str(row["policy_status"] or "active"),
                    "superseded_by_policy_id": row["superseded_by_policy_id"],
                    "disabled_at": row["disabled_at"],
                    "disabled_by": row["disabled_by"],
                    "disable_reason": row["disable_reason"],
                    "promoted_by": row["promoted_by"],
                    "promoted_at": row["promoted_at"],
                }
            )
        return out

    def latest_policy(self, *, task_family: str) -> dict[str, Any] | None:
        rows = self.list_policies(task_family=task_family, policy_status="active", limit=1)
        return rows[0] if rows else None

    def get_policy(self, *, policy_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM learning_policy_registry
            WHERE policy_id = ?
            LIMIT 1
            """,
            (str(policy_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "policy_id": row["policy_id"],
            "task_family": row["task_family"],
            "policy_name": row["policy_name"],
            "metrics": json.loads(str(row["metrics_json"] or "{}")),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "policy_status": str(row["policy_status"] or "active"),
            "superseded_by_policy_id": row["superseded_by_policy_id"],
            "disabled_at": row["disabled_at"],
            "disabled_by": row["disabled_by"],
            "disable_reason": row["disable_reason"],
            "promoted_by": row["promoted_by"],
            "promoted_at": row["promoted_at"],
        }

    def update_policy_metadata(
        self,
        *,
        policy_id: str,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT *
            FROM learning_policy_registry
            WHERE policy_id = ?
            LIMIT 1
            """,
            (str(policy_id),),
        ).fetchone()
        if not row:
            raise KeyError(f"Policy not found: {policy_id}")
        existing_metadata = json.loads(str(row["metadata_json"] or "{}"))
        existing_metadata.update(dict(metadata_patch or {}))
        self.conn.execute(
            """
            UPDATE learning_policy_registry
            SET metadata_json = ?
            WHERE policy_id = ?
            """,
            (json.dumps(existing_metadata, sort_keys=True), str(policy_id)),
        )
        self.conn.commit()
        return {
            "policy_id": row["policy_id"],
            "task_family": row["task_family"],
            "policy_name": row["policy_name"],
            "metrics": json.loads(str(row["metrics_json"] or "{}")),
            "metadata": existing_metadata,
            "policy_status": str(row["policy_status"] or "active"),
            "superseded_by_policy_id": row["superseded_by_policy_id"],
            "disabled_at": row["disabled_at"],
            "disabled_by": row["disabled_by"],
            "disable_reason": row["disable_reason"],
            "promoted_by": row["promoted_by"],
            "promoted_at": row["promoted_at"],
        }

    def set_policy_status(
        self,
        *,
        policy_id: str,
        policy_status: str,
        actor: str = "runtime",
        reason: str | None = None,
        superseded_by_policy_id: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
        changed_at: str | None = None,
        allow_superseded_disable: bool = False,
    ) -> dict[str, Any]:
        resolved_status = str(policy_status or "").strip().lower()
        if resolved_status not in {"active", "disabled", "superseded"}:
            raise ValueError(f"Unsupported policy status: {policy_status}")
        row = self.conn.execute(
            """
            SELECT *
            FROM learning_policy_registry
            WHERE policy_id = ?
            LIMIT 1
            """,
            (str(policy_id),),
        ).fetchone()
        if not row:
            raise KeyError(f"Policy not found: {policy_id}")

        current_status = str(row["policy_status"] or "active").strip().lower() or "active"
        if current_status not in {"active", "disabled", "superseded"}:
            current_status = "active"
        if current_status != resolved_status:
            allowed_transitions: dict[str, set[str]] = {
                "active": {"disabled", "superseded"},
                "disabled": {"active", "superseded"},
                "superseded": {"active"},
            }
            transition_allowed = resolved_status in allowed_transitions.get(current_status, set())
            explicit_superseded_disable = bool(
                current_status == "superseded"
                and resolved_status == "disabled"
                and allow_superseded_disable
            )
            if not transition_allowed and not explicit_superseded_disable:
                hint = ""
                if current_status == "superseded" and resolved_status == "disabled":
                    hint = " Pass allow_superseded_disable=True for explicit operator intent."
                raise ValueError(
                    "Unsupported policy status transition: "
                    f"{current_status} -> {resolved_status}.{hint}"
                )

        now = str(changed_at or utc_now_iso())
        disabled_at = row["disabled_at"]
        disabled_by = row["disabled_by"]
        disable_reason = row["disable_reason"]
        superseded_ref = row["superseded_by_policy_id"]

        if resolved_status == "disabled":
            disabled_at = now
            disabled_by = str(actor or "runtime")
            disable_reason = str(reason or "disabled")
            superseded_ref = None
        elif resolved_status == "superseded":
            disabled_at = None
            disabled_by = None
            disable_reason = None
            superseded_ref = str(superseded_by_policy_id) if superseded_by_policy_id else superseded_ref
        else:
            disabled_at = None
            disabled_by = None
            disable_reason = None
            superseded_ref = None

        metadata = json.loads(str(row["metadata_json"] or "{}"))
        metadata.update(dict(metadata_patch or {}))
        metadata["policy_status"] = resolved_status
        metadata["status_updated_at"] = now
        metadata["status_updated_by"] = str(actor or "runtime")
        if reason:
            metadata["status_reason"] = str(reason)
        if current_status == "superseded" and resolved_status == "disabled":
            metadata["allow_superseded_disable"] = bool(allow_superseded_disable)
        if superseded_ref:
            metadata["superseded_by_policy_id"] = str(superseded_ref)
        elif "superseded_by_policy_id" in metadata:
            metadata.pop("superseded_by_policy_id", None)

        self.conn.execute(
            """
            UPDATE learning_policy_registry
            SET metadata_json = ?,
                policy_status = ?,
                superseded_by_policy_id = ?,
                disabled_at = ?,
                disabled_by = ?,
                disable_reason = ?
            WHERE policy_id = ?
            """,
            (
                json.dumps(metadata, sort_keys=True),
                resolved_status,
                superseded_ref,
                disabled_at,
                disabled_by,
                disable_reason,
                str(policy_id),
            ),
        )
        self.conn.commit()
        updated = self.get_policy(policy_id=str(policy_id))
        if not isinstance(updated, dict):
            raise KeyError(f"Policy not found after status update: {policy_id}")
        return updated

    def record_promotion_audit(
        self,
        *,
        task_family: str,
        policy_name: str,
        decision: str,
        actor: str = "runtime",
        gate: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        policy_id: str | None = None,
        audit_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "audit_id": str(audit_id or f"lpa_{uuid4().hex}"),
            "policy_id": str(policy_id) if policy_id is not None else None,
            "task_family": str(task_family),
            "policy_name": str(policy_name),
            "decision": str(decision),
            "actor": str(actor or "runtime"),
            "gate": dict(gate or {}),
            "report": dict(report or {}),
            "metadata": dict(metadata or {}),
            "created_at": str(created_at or utc_now_iso()),
        }
        self.conn.execute(
            """
            INSERT INTO learning_policy_audit (
                audit_id, policy_id, task_family, policy_name, decision, actor,
                gate_json, report_json, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["audit_id"],
                payload["policy_id"],
                payload["task_family"],
                payload["policy_name"],
                payload["decision"],
                payload["actor"],
                json.dumps(payload["gate"], sort_keys=True),
                json.dumps(payload["report"], sort_keys=True),
                json.dumps(payload["metadata"], sort_keys=True),
                payload["created_at"],
            ),
        )
        self.conn.commit()
        return payload

    def list_promotion_audits(
        self,
        *,
        task_family: str | None = None,
        decision: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        clauses: list[str] = []
        params: list[Any] = []
        if task_family is not None:
            clauses.append("task_family = ?")
            params.append(str(task_family))
        if decision is not None:
            clauses.append("decision = ?")
            params.append(str(decision))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM learning_policy_audit
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
                    "audit_id": row["audit_id"],
                    "policy_id": row["policy_id"],
                    "task_family": row["task_family"],
                    "policy_name": row["policy_name"],
                    "decision": row["decision"],
                    "actor": row["actor"],
                    "gate": json.loads(str(row["gate_json"] or "{}")),
                    "report": json.loads(str(row["report_json"] or "{}")),
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "created_at": row["created_at"],
                }
            )
        return out

    def close(self) -> None:
        self.conn.close()
