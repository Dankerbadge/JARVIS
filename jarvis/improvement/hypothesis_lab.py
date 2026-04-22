from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import utc_now_iso


class HypothesisLabStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hypothesis_registry (
                hypothesis_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                title TEXT NOT NULL,
                statement TEXT NOT NULL,
                friction_key TEXT,
                friction_ids_json TEXT NOT NULL,
                proposed_change TEXT NOT NULL,
                success_criteria_json TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                owner TEXT NOT NULL,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_hypothesis_registry_domain_status_updated
            ON hypothesis_registry(domain, status, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hypothesis_experiment_runs (
                run_id TEXT PRIMARY KEY,
                hypothesis_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                environment TEXT NOT NULL,
                baseline_metrics_json TEXT NOT NULL,
                candidate_metrics_json TEXT NOT NULL,
                evaluation_json TEXT NOT NULL,
                source_trace_id TEXT,
                sample_size INTEGER,
                notes TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_hypothesis_runs_hypothesis_created
            ON hypothesis_experiment_runs(hypothesis_id, created_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_hypothesis_runs_domain_created
            ON hypothesis_experiment_runs(domain, created_at DESC)
            """
        )
        self.conn.commit()

    @staticmethod
    def _row_to_hypothesis(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "hypothesis_id": row["hypothesis_id"],
            "domain": row["domain"],
            "title": row["title"],
            "statement": row["statement"],
            "friction_key": row["friction_key"],
            "friction_ids": json.loads(str(row["friction_ids_json"] or "[]")),
            "proposed_change": row["proposed_change"],
            "success_criteria": json.loads(str(row["success_criteria_json"] or "{}")),
            "risk_level": row["risk_level"],
            "owner": row["owner"],
            "status": row["status"],
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "hypothesis_id": row["hypothesis_id"],
            "domain": row["domain"],
            "environment": row["environment"],
            "baseline_metrics": json.loads(str(row["baseline_metrics_json"] or "{}")),
            "candidate_metrics": json.loads(str(row["candidate_metrics_json"] or "{}")),
            "evaluation": json.loads(str(row["evaluation_json"] or "{}")),
            "source_trace_id": row["source_trace_id"],
            "sample_size": int(row["sample_size"]) if row["sample_size"] is not None else None,
            "notes": row["notes"],
            "status": row["status"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    def create_hypothesis(
        self,
        *,
        domain: str,
        title: str,
        statement: str,
        proposed_change: str,
        success_criteria: dict[str, Any],
        friction_key: str | None = None,
        friction_ids: list[str] | None = None,
        risk_level: str = "medium",
        owner: str = "runtime",
        status: str = "queued",
        metadata: dict[str, Any] | None = None,
        hypothesis_id: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        payload = {
            "hypothesis_id": str(hypothesis_id or f"hyp_{uuid4().hex}"),
            "domain": str(domain or "unknown").strip().lower() or "unknown",
            "title": str(title or "Untitled hypothesis").strip() or "Untitled hypothesis",
            "statement": str(statement or "").strip(),
            "friction_key": str(friction_key).strip() if friction_key is not None else None,
            "friction_ids": [str(item).strip() for item in list(friction_ids or []) if str(item).strip()],
            "proposed_change": str(proposed_change or "").strip(),
            "success_criteria": dict(success_criteria or {}),
            "risk_level": str(risk_level or "medium").strip().lower() or "medium",
            "owner": str(owner or "runtime").strip() or "runtime",
            "status": str(status or "queued").strip().lower() or "queued",
            "metadata": dict(metadata or {}),
            "created_at": str(created_at or now),
            "updated_at": str(updated_at or now),
        }
        self.conn.execute(
            """
            INSERT INTO hypothesis_registry (
                hypothesis_id, domain, title, statement, friction_key, friction_ids_json,
                proposed_change, success_criteria_json, risk_level, owner, status,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["hypothesis_id"],
                payload["domain"],
                payload["title"],
                payload["statement"],
                payload["friction_key"],
                json.dumps(payload["friction_ids"], sort_keys=True),
                payload["proposed_change"],
                json.dumps(payload["success_criteria"], sort_keys=True),
                payload["risk_level"],
                payload["owner"],
                payload["status"],
                json.dumps(payload["metadata"], sort_keys=True),
                payload["created_at"],
                payload["updated_at"],
            ),
        )
        self.conn.commit()
        return payload

    def get_hypothesis(self, hypothesis_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM hypothesis_registry
            WHERE hypothesis_id = ?
            LIMIT 1
            """,
            (str(hypothesis_id),),
        ).fetchone()
        if not row:
            return None
        return self._row_to_hypothesis(row)

    def list_hypotheses(
        self,
        *,
        domain: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if domain is not None:
            clauses.append("domain = ?")
            params.append(str(domain).strip().lower())
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status).strip().lower())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM hypothesis_registry
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
        return [self._row_to_hypothesis(row) for row in rows]

    def set_hypothesis_status(
        self,
        *,
        hypothesis_id: str,
        status: str,
        metadata_patch: dict[str, Any] | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT *
            FROM hypothesis_registry
            WHERE hypothesis_id = ?
            LIMIT 1
            """,
            (str(hypothesis_id),),
        ).fetchone()
        if not row:
            raise KeyError(f"Hypothesis not found: {hypothesis_id}")

        metadata = json.loads(str(row["metadata_json"] or "{}"))
        metadata.update(dict(metadata_patch or {}))
        now = str(updated_at or utc_now_iso())
        resolved_status = str(status or "").strip().lower() or "queued"
        self.conn.execute(
            """
            UPDATE hypothesis_registry
            SET status = ?, metadata_json = ?, updated_at = ?
            WHERE hypothesis_id = ?
            """,
            (resolved_status, json.dumps(metadata, sort_keys=True), now, str(hypothesis_id)),
        )
        self.conn.commit()
        updated = self.get_hypothesis(str(hypothesis_id))
        if not isinstance(updated, dict):
            raise KeyError(f"Hypothesis not found after status update: {hypothesis_id}")
        return updated

    def record_experiment_run(
        self,
        *,
        hypothesis_id: str,
        domain: str,
        environment: str,
        baseline_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
        evaluation: dict[str, Any],
        source_trace_id: str | None = None,
        sample_size: int | None = None,
        notes: str | None = None,
        status: str = "completed",
        run_id: str | None = None,
        created_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        payload = {
            "run_id": str(run_id or f"exp_{uuid4().hex}"),
            "hypothesis_id": str(hypothesis_id),
            "domain": str(domain or "unknown").strip().lower() or "unknown",
            "environment": str(environment or "sandbox").strip().lower() or "sandbox",
            "baseline_metrics": dict(baseline_metrics or {}),
            "candidate_metrics": dict(candidate_metrics or {}),
            "evaluation": dict(evaluation or {}),
            "source_trace_id": str(source_trace_id).strip() if source_trace_id else None,
            "sample_size": int(sample_size) if sample_size is not None else None,
            "notes": str(notes).strip() if notes is not None else None,
            "status": str(status or "completed").strip().lower() or "completed",
            "created_at": str(created_at or now),
            "finished_at": str(finished_at or now),
        }
        self.conn.execute(
            """
            INSERT INTO hypothesis_experiment_runs (
                run_id, hypothesis_id, domain, environment, baseline_metrics_json,
                candidate_metrics_json, evaluation_json, source_trace_id,
                sample_size, notes, status, created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["run_id"],
                payload["hypothesis_id"],
                payload["domain"],
                payload["environment"],
                json.dumps(payload["baseline_metrics"], sort_keys=True),
                json.dumps(payload["candidate_metrics"], sort_keys=True),
                json.dumps(payload["evaluation"], sort_keys=True),
                payload["source_trace_id"],
                payload["sample_size"],
                payload["notes"],
                payload["status"],
                payload["created_at"],
                payload["finished_at"],
            ),
        )
        self.conn.commit()
        return payload

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM hypothesis_experiment_runs
            WHERE run_id = ?
            LIMIT 1
            """,
            (str(run_id),),
        ).fetchone()
        if not row:
            return None
        return self._row_to_run(row)

    def list_experiment_runs(
        self,
        *,
        hypothesis_id: str | None = None,
        domain: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if hypothesis_id is not None:
            clauses.append("hypothesis_id = ?")
            params.append(str(hypothesis_id))
        if domain is not None:
            clauses.append("domain = ?")
            params.append(str(domain).strip().lower())
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status).strip().lower())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM hypothesis_experiment_runs
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def close(self) -> None:
        self.conn.close()
