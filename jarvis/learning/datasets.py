from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..models import utc_now_iso
from ..reasoning.store import ReasoningStore
from ..suggestions.feedback import SuggestionFeedbackStore
from ..workflows.plan_repository import PlanRepository
from .features import apply_feedback_to_utility, build_trace_feature_vector, utility_from_trace_status


class LearningDatasetStore:
    def __init__(
        self,
        *,
        db_path: str | Path,
        reasoning_store: ReasoningStore,
        plan_repo: PlanRepository,
        feedback_store: SuggestionFeedbackStore | None = None,
    ) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.reasoning_store = reasoning_store
        self.plan_repo = plan_repo
        self.feedback_store = feedback_store
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_examples (
                example_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL UNIQUE,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                domain TEXT NOT NULL,
                chosen_action TEXT NOT NULL,
                observed_outcome TEXT NOT NULL,
                utility_score REAL NOT NULL,
                label_source TEXT NOT NULL,
                feature_vector_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_examples_plan_step_updated
            ON learning_examples(plan_id, step_id, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_examples_task_domain_updated
            ON learning_examples(task_type, domain, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_materializations (
                materialization_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL UNIQUE,
                example_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                materialized_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_materializations_at
            ON learning_materializations(materialized_at DESC)
            """
        )
        self.conn.commit()

    @staticmethod
    def _canonical_hash(payload: dict[str, Any]) -> str:
        raw = json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _final_trace_status(status: str) -> bool:
        return str(status or "").strip().lower() in {"blocked", "succeeded", "failed", "compensated"}

    def _resolve_chosen_action(self, detail: dict[str, Any]) -> str:
        selected = detail.get("selected_action") if isinstance(detail.get("selected_action"), dict) else {}
        candidate_id = str(selected.get("candidate_id") or "")
        if candidate_id:
            for candidate in list(detail.get("candidates") or []):
                if str(candidate.get("candidate_id") or "") == candidate_id:
                    value = str(candidate.get("candidate_ref") or "").strip()
                    if value:
                        return value
        fallback = str(detail.get("proposed_action") or "").strip()
        return fallback or "unknown_action"

    def _upsert_example(
        self,
        *,
        trace: dict[str, Any],
        trace_detail: dict[str, Any],
        step_attempts: list[dict[str, Any]],
        feedback: dict[str, Any] | None,
    ) -> dict[str, Any]:
        trace_id = str(trace_detail.get("trace_id") or trace.get("trace_id") or "")
        if not trace_id:
            raise ValueError("trace_id is required to materialize learning example")
        now = utc_now_iso()
        status = str(trace_detail.get("status") or trace.get("status") or "")
        feature_vector = build_trace_feature_vector(
            trace=trace,
            trace_detail=trace_detail,
            step_attempts=step_attempts,
            suggestion_feedback=feedback,
        )
        base_utility = utility_from_trace_status(status)
        utility_score, label_source = apply_feedback_to_utility(base_utility, feedback)
        chosen_action = self._resolve_chosen_action(trace_detail)
        metadata = {
            "trace_created_at": trace_detail.get("created_at"),
            "trace_updated_at": trace_detail.get("updated_at"),
            "feedback_id": (feedback or {}).get("feedback_id") if isinstance(feedback, dict) else None,
            "feedback_suggestion_id": (feedback or {}).get("suggestion_id") if isinstance(feedback, dict) else None,
            "materialized_at": now,
        }
        example_id = f"lex_{trace_id}"

        self.conn.execute(
            """
            INSERT INTO learning_examples (
                example_id, trace_id, plan_id, step_id, task_type, domain, chosen_action,
                observed_outcome, utility_score, label_source, feature_vector_json,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                task_type = excluded.task_type,
                domain = excluded.domain,
                chosen_action = excluded.chosen_action,
                observed_outcome = excluded.observed_outcome,
                utility_score = excluded.utility_score,
                label_source = excluded.label_source,
                feature_vector_json = excluded.feature_vector_json,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                example_id,
                trace_id,
                str(trace_detail.get("plan_id") or trace.get("plan_id") or ""),
                str(trace_detail.get("step_id") or trace.get("step_id") or ""),
                str(feature_vector.get("task_type") or "workflow_step_decision"),
                str(feature_vector.get("domain") or "workflow"),
                chosen_action,
                status,
                float(utility_score),
                str(label_source),
                json.dumps(feature_vector, sort_keys=True),
                json.dumps(metadata, sort_keys=True),
                now,
                now,
            ),
        )
        source_hash = self._canonical_hash(
            {
                "trace_status": status,
                "trace_updated_at": trace_detail.get("updated_at"),
                "event_count": len(list(trace_detail.get("events") or [])),
                "candidate_count": len(list(trace_detail.get("candidates") or [])),
                "attempt_count": len(step_attempts),
                "feedback": feedback or {},
            }
        )
        self.conn.execute(
            """
            INSERT INTO learning_materializations (
                materialization_id, trace_id, example_id, source_hash, status, materialized_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                materialization_id = excluded.materialization_id,
                example_id = excluded.example_id,
                source_hash = excluded.source_hash,
                status = excluded.status,
                materialized_at = excluded.materialized_at
            """,
            (
                f"lmat_{trace_id}",
                trace_id,
                example_id,
                source_hash,
                "materialized",
                now,
            ),
        )
        self.conn.commit()
        return {
            "example_id": example_id,
            "trace_id": trace_id,
            "plan_id": str(trace_detail.get("plan_id") or trace.get("plan_id") or ""),
            "step_id": str(trace_detail.get("step_id") or trace.get("step_id") or ""),
            "task_type": str(feature_vector.get("task_type") or "workflow_step_decision"),
            "domain": str(feature_vector.get("domain") or "workflow"),
            "chosen_action": chosen_action,
            "observed_outcome": status,
            "utility_score": float(utility_score),
            "label_source": str(label_source),
            "feature_vector": feature_vector,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _infer_feedback_from_attempts(
        *,
        trace_status: str,
        step_attempts: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        attempts = list(step_attempts or [])
        if not attempts:
            return None
        states = [str(item.get("step_state") or "").strip().lower() for item in attempts]
        if not any(state == "blocked" for state in states):
            return None

        status = str(trace_status or "").strip().lower()
        has_approval_progress = any(state in {"approved", "succeeded"} for state in states)
        has_terminal_failure = any(state in {"failed", "compensated"} for state in states)

        if has_approval_progress and status in {"succeeded", "compensated"}:
            return {
                "accepted": True,
                "action_taken": "approval_progression_observed",
                "utility_score": None,
                "metadata": {
                    "inferred": True,
                    "source": "step_attempts",
                    "states": states,
                    "inference_reason": "blocked_then_approved_or_succeeded",
                },
            }
        if status in {"blocked", "failed", "compensated"} or has_terminal_failure:
            return {
                "accepted": False,
                "action_taken": "blocked_without_success_resolution",
                "utility_score": None,
                "metadata": {
                    "inferred": True,
                    "source": "step_attempts",
                    "states": states,
                    "inference_reason": "blocked_then_terminal_without_success",
                },
            }
        return None

    def materialize_trace(self, *, trace_id: str) -> dict[str, Any] | None:
        trace_detail = self.reasoning_store.get_trace(trace_id)
        if not isinstance(trace_detail, dict):
            return None
        status = str(trace_detail.get("status") or "")
        if not self._final_trace_status(status):
            return None
        step_attempts = self.plan_repo.export_step_transition_timeline(
            plan_id=str(trace_detail.get("plan_id") or ""),
            step_id=str(trace_detail.get("step_id") or ""),
            limit=500,
        )
        feedback = (
            self.feedback_store.latest_feedback_for_trace(trace_id)
            if self.feedback_store is not None
            else None
        )
        if feedback is None and self.feedback_store is not None:
            inferred = self._infer_feedback_from_attempts(
                trace_status=status,
                step_attempts=step_attempts,
            )
            if isinstance(inferred, dict):
                feedback = self.feedback_store.record_feedback(
                    suggestion_id=f"implicit_trace_resolution:{trace_id}",
                    source_trace_id=trace_id,
                    accepted=bool(inferred.get("accepted")),
                    action_taken=str(inferred.get("action_taken") or ""),
                    utility_score=inferred.get("utility_score"),
                    metadata=dict(inferred.get("metadata") or {}),
                )
        return self._upsert_example(
            trace=trace_detail,
            trace_detail=trace_detail,
            step_attempts=step_attempts,
            feedback=feedback,
        )

    def materialize_traces(
        self,
        *,
        plan_id: str | None = None,
        step_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        traces = self.reasoning_store.list_traces(plan_id=plan_id, step_id=step_id, limit=limit)
        materialized: list[dict[str, Any]] = []
        skipped = 0
        for trace in traces:
            trace_id = str(trace.get("trace_id") or "")
            if not trace_id:
                skipped += 1
                continue
            item = self.materialize_trace(trace_id=trace_id)
            if item is None:
                skipped += 1
                continue
            materialized.append(item)
        return {
            "requested_count": len(traces),
            "materialized_count": len(materialized),
            "skipped_count": int(skipped),
            "examples": materialized,
        }

    def list_examples(
        self,
        *,
        plan_id: str | None = None,
        step_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        clauses: list[str] = []
        params: list[Any] = []
        if plan_id is not None:
            clauses.append("plan_id = ?")
            params.append(str(plan_id))
        if step_id is not None:
            clauses.append("step_id = ?")
            params.append(str(step_id))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM learning_examples
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*params, resolved_limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "example_id": row["example_id"],
                    "trace_id": row["trace_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "task_type": row["task_type"],
                    "domain": row["domain"],
                    "chosen_action": row["chosen_action"],
                    "observed_outcome": row["observed_outcome"],
                    "utility_score": row["utility_score"],
                    "label_source": row["label_source"],
                    "feature_vector": json.loads(str(row["feature_vector_json"] or "{}")),
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def close(self) -> None:
        self.conn.close()
