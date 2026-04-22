from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import PlanArtifact, PlanStep, utc_now_iso
from .models import StepState, normalize_step_state, transition_allowed


class PlanRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_artifacts (
                plan_id TEXT PRIMARY KEY,
                intent TEXT NOT NULL,
                priority TEXT NOT NULL,
                reasoning_summary TEXT NOT NULL,
                approval_requirements_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_steps (
                step_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_idx INTEGER NOT NULL,
                action_class TEXT NOT NULL,
                proposed_action TEXT NOT NULL,
                expected_effect TEXT NOT NULL,
                rollback_text TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                requires_approval INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_outcomes (
                plan_id TEXT PRIMARY KEY,
                repo_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                status TEXT NOT NULL,
                touched_paths_json TEXT NOT NULL,
                failure_family TEXT,
                summary TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_step_attempts (
                attempt_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                step_state TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_step_compensations (
                compensation_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                strategy TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_step_attempts_plan_step_created
            ON plan_step_attempts(plan_id, step_id, created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_step_attempts_plan_created
            ON plan_step_attempts(plan_id, created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_step_compensations_plan_step_created
            ON plan_step_compensations(plan_id, step_id, created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_step_compensations_plan_created
            ON plan_step_compensations(plan_id, created_at DESC)
            """
        )
        self._ensure_plan_step_attempts_schema()
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_step_attempts_plan_step_attempt
            ON plan_step_attempts(plan_id, step_id, attempt_number DESC)
            """
        )
        self.conn.commit()

    def _ensure_plan_step_attempts_schema(self) -> None:
        columns = {
            str(row["name"]): row
            for row in self.conn.execute("PRAGMA table_info(plan_step_attempts)").fetchall()
        }
        if "attempt_number" in columns:
            return
        self.conn.execute("ALTER TABLE plan_step_attempts ADD COLUMN attempt_number INTEGER")
        rows = self.conn.execute(
            """
            SELECT rowid, plan_id, step_id
            FROM plan_step_attempts
            ORDER BY plan_id ASC, step_id ASC, created_at ASC, rowid ASC
            """
        ).fetchall()
        counter: dict[tuple[str, str], int] = {}
        for row in rows:
            key = (str(row["plan_id"]), str(row["step_id"]))
            next_number = int(counter.get(key, 0)) + 1
            counter[key] = next_number
            self.conn.execute(
                "UPDATE plan_step_attempts SET attempt_number = ? WHERE rowid = ?",
                (next_number, int(row["rowid"])),
            )

    def save_plan(self, plan: PlanArtifact, status: str = "proposed") -> str:
        self.conn.execute(
            """
            INSERT INTO plan_artifacts (
                plan_id, intent, priority, reasoning_summary, approval_requirements_json,
                expires_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.plan_id,
                plan.intent,
                plan.priority,
                plan.reasoning_summary,
                json.dumps(plan.approval_requirements, sort_keys=True),
                plan.expires_at,
                status,
                utc_now_iso(),
            ),
        )
        for idx, step in enumerate(plan.steps):
            self.conn.execute(
                """
                INSERT INTO plan_steps (
                    step_id, plan_id, step_idx, action_class, proposed_action, expected_effect,
                    rollback_text, payload_json, requires_approval, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.step_id,
                    plan.plan_id,
                    idx,
                    step.action_class,
                    step.proposed_action,
                    step.expected_effect,
                    step.rollback,
                    json.dumps(step.payload, sort_keys=True),
                    1 if step.requires_approval else 0,
                    step.idempotency_key,
                ),
            )
            self.record_step_attempt(
                plan_id=plan.plan_id,
                step_id=step.step_id,
                step_state=StepState.QUEUED,
                details={
                    "action_class": step.action_class,
                    "requires_approval": bool(step.requires_approval),
                    "idempotency_key": step.idempotency_key,
                },
                commit=False,
            )
        self.conn.commit()
        return plan.plan_id

    def set_status(self, plan_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE plan_artifacts SET status = ? WHERE plan_id = ?",
            (status, plan_id),
        )
        self.conn.commit()

    def get_plan(self, plan_id: str) -> PlanArtifact:
        row = self.conn.execute(
            "SELECT * FROM plan_artifacts WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Plan not found: {plan_id}")

        step_rows = self.conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_idx ASC",
            (plan_id,),
        ).fetchall()
        steps: list[PlanStep] = []
        for step_row in step_rows:
            steps.append(
                PlanStep(
                    action_class=step_row["action_class"],
                    proposed_action=step_row["proposed_action"],
                    expected_effect=step_row["expected_effect"],
                    rollback=step_row["rollback_text"],
                    payload=json.loads(step_row["payload_json"]),
                    requires_approval=bool(step_row["requires_approval"]),
                    step_id=step_row["step_id"],
                    idempotency_key=step_row["idempotency_key"],
                )
            )
        return PlanArtifact(
            intent=row["intent"],
            priority=row["priority"],
            reasoning_summary=row["reasoning_summary"],
            steps=steps,
            approval_requirements=json.loads(row["approval_requirements_json"]),
            expires_at=row["expires_at"],
            plan_id=row["plan_id"],
        )

    def close(self) -> None:
        self.conn.close()

    def _latest_step_attempt(self, *, plan_id: str, step_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT attempt_id, step_state, attempt_number, created_at
            FROM plan_step_attempts
            WHERE plan_id = ? AND step_id = ?
            ORDER BY attempt_number DESC, created_at DESC
            LIMIT 1
            """,
            (str(plan_id), str(step_id)),
        ).fetchone()
        if not row:
            return None
        return {
            "attempt_id": row["attempt_id"],
            "step_state": row["step_state"],
            "attempt_number": int(row["attempt_number"] or 0),
            "created_at": row["created_at"],
        }

    def get_latest_step_attempt(self, *, plan_id: str, step_id: str) -> dict[str, Any] | None:
        return self._latest_step_attempt(plan_id=plan_id, step_id=step_id)

    def _next_attempt_number(self, *, plan_id: str, step_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(attempt_number), 0) AS max_attempt
            FROM plan_step_attempts
            WHERE plan_id = ? AND step_id = ?
            """,
            (str(plan_id), str(step_id)),
        ).fetchone()
        max_attempt = int(row["max_attempt"] if row and row["max_attempt"] is not None else 0)
        return max_attempt + 1

    def record_step_attempt(
        self,
        *,
        plan_id: str,
        step_id: str,
        step_state: StepState | str,
        details: dict[str, Any] | None = None,
        attempt_id: str | None = None,
        attempt_number: int | None = None,
        created_at: str | None = None,
        validate_transition: bool = True,
        commit: bool = True,
    ) -> str:
        resolved_attempt_id = str(attempt_id or f"stpat_{uuid4().hex}")
        normalized_state = normalize_step_state(step_state)
        latest = self._latest_step_attempt(plan_id=plan_id, step_id=step_id)
        if validate_transition and not transition_allowed(
            latest.get("step_state") if isinstance(latest, dict) else None,
            normalized_state,
        ):
            raise ValueError(
                "Invalid step state transition for attempt journal: "
                f"{(latest or {}).get('step_state')} -> {normalized_state} "
                f"(plan_id={plan_id}, step_id={step_id})"
            )
        resolved_attempt_number = (
            int(attempt_number)
            if isinstance(attempt_number, int) and attempt_number > 0
            else self._next_attempt_number(plan_id=plan_id, step_id=step_id)
        )
        self.conn.execute(
            """
            INSERT INTO plan_step_attempts (
                attempt_id, plan_id, step_id, step_state, attempt_number, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_attempt_id,
                str(plan_id),
                str(step_id),
                normalized_state,
                resolved_attempt_number,
                json.dumps(dict(details or {}), sort_keys=True),
                str(created_at or utc_now_iso()),
            ),
        )
        if commit:
            self.conn.commit()
        return resolved_attempt_id

    def list_step_attempts(
        self,
        *,
        plan_id: str,
        step_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        if step_id:
            rows = self.conn.execute(
                """
                SELECT *
                FROM plan_step_attempts
                WHERE plan_id = ? AND step_id = ?
                ORDER BY attempt_number DESC, created_at DESC
                LIMIT ?
                """,
                (plan_id, step_id, resolved_limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM plan_step_attempts
                WHERE plan_id = ?
                ORDER BY created_at DESC, attempt_number DESC
                LIMIT ?
                """,
                (plan_id, resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "attempt_id": row["attempt_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "step_state": row["step_state"],
                    "attempt_number": int(row["attempt_number"] or 0),
                    "details": json.loads(row["details_json"]),
                    "created_at": row["created_at"],
                }
            )
        return out

    def export_step_transition_timeline(
        self,
        *,
        plan_id: str,
        step_id: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        attempts = self.list_step_attempts(plan_id=plan_id, step_id=step_id, limit=limit)
        attempts.sort(
            key=lambda item: (
                int(item.get("attempt_number") or 0),
                str(item.get("created_at") or ""),
            )
        )
        return attempts

    def record_step_compensation(
        self,
        *,
        plan_id: str,
        step_id: str,
        reason: str,
        strategy: str,
        details: dict[str, Any] | None = None,
        compensation_id: str | None = None,
        created_at: str | None = None,
        commit: bool = True,
    ) -> str:
        resolved_compensation_id = str(compensation_id or f"cmp_{uuid4().hex}")
        self.conn.execute(
            """
            INSERT INTO plan_step_compensations (
                compensation_id, plan_id, step_id, reason, strategy, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_compensation_id,
                str(plan_id),
                str(step_id),
                str(reason),
                str(strategy),
                json.dumps(dict(details or {}), sort_keys=True),
                str(created_at or utc_now_iso()),
            ),
        )
        if commit:
            self.conn.commit()
        return resolved_compensation_id

    def list_step_compensations(
        self,
        *,
        plan_id: str,
        step_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        if step_id:
            rows = self.conn.execute(
                """
                SELECT *
                FROM plan_step_compensations
                WHERE plan_id = ? AND step_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(plan_id), str(step_id), resolved_limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM plan_step_compensations
                WHERE plan_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(plan_id), resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "compensation_id": row["compensation_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "reason": row["reason"],
                    "strategy": row["strategy"],
                    "details": json.loads(str(row["details_json"] or "{}")),
                    "created_at": row["created_at"],
                }
            )
        return out

    def record_outcome(
        self,
        *,
        plan_id: str,
        repo_id: str,
        branch: str,
        status: str,
        touched_paths: list[str],
        failure_family: str | None = None,
        summary: str | None = None,
        recorded_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO plan_outcomes (
                plan_id, repo_id, branch, status, touched_paths_json, failure_family, summary, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                repo_id = excluded.repo_id,
                branch = excluded.branch,
                status = excluded.status,
                touched_paths_json = excluded.touched_paths_json,
                failure_family = excluded.failure_family,
                summary = excluded.summary,
                recorded_at = excluded.recorded_at
            """,
            (
                plan_id,
                repo_id,
                branch,
                status,
                json.dumps(sorted(set(touched_paths))),
                failure_family,
                summary,
                recorded_at or utc_now_iso(),
            ),
        )
        self.conn.commit()

    def list_recent_outcomes(
        self,
        repo_id: str,
        branch: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM plan_outcomes
            WHERE repo_id = ? AND branch = ?
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            (repo_id, branch, limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "plan_id": row["plan_id"],
                    "repo_id": row["repo_id"],
                    "branch": row["branch"],
                    "status": row["status"],
                    "touched_paths": json.loads(row["touched_paths_json"]),
                    "failure_family": row["failure_family"],
                    "summary": row["summary"],
                    "recorded_at": row["recorded_at"],
                }
            )
        return out

    def list_recent_outcomes_global(
        self,
        *,
        limit: int = 100,
        since_recorded_at: str | None = None,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        if since_recorded_at is None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM plan_outcomes
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (resolved_limit,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM plan_outcomes
                WHERE recorded_at > ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (str(since_recorded_at), resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "plan_id": row["plan_id"],
                    "repo_id": row["repo_id"],
                    "branch": row["branch"],
                    "status": row["status"],
                    "touched_paths": json.loads(row["touched_paths_json"]),
                    "failure_family": row["failure_family"],
                    "summary": row["summary"],
                    "recorded_at": row["recorded_at"],
                }
            )
        return out
