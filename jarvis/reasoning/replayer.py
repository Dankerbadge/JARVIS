from __future__ import annotations

from typing import Any

from ..workflows.plan_repository import PlanRepository
from .store import ReasoningStore


class ReasoningReplayer:
    def __init__(self, store: ReasoningStore, plan_repository: PlanRepository) -> None:
        self.store = store
        self.plan_repository = plan_repository

    @staticmethod
    def _safe_time(value: Any) -> str:
        return str(value or "")

    def replay_step_timeline(self, *, plan_id: str, step_id: str) -> dict[str, Any]:
        attempts = list(
            self.plan_repository.export_step_transition_timeline(
                plan_id=plan_id,
                step_id=step_id,
                limit=1000,
            )
        )

        traces = list(
            self.store.list_traces(
                plan_id=plan_id,
                step_id=step_id,
                limit=1000,
            )
        )
        traces.sort(key=lambda item: self._safe_time(item.get("created_at")))
        compensations = list(
            self.plan_repository.list_step_compensations(
                plan_id=plan_id,
                step_id=step_id,
                limit=1000,
            )
        )
        compensations.sort(key=lambda item: self._safe_time(item.get("created_at")))

        merged_events: list[dict[str, Any]] = []
        for attempt in attempts:
            merged_events.append(
                {
                    "source": "attempt",
                    "timestamp": attempt.get("created_at"),
                    "step_state": attempt.get("step_state"),
                    "attempt_number": attempt.get("attempt_number"),
                    "payload": dict(attempt.get("details") or {}),
                }
            )
        for compensation in compensations:
            merged_events.append(
                {
                    "source": "compensation",
                    "timestamp": compensation.get("created_at"),
                    "compensation_id": compensation.get("compensation_id"),
                    "reason": compensation.get("reason"),
                    "strategy": compensation.get("strategy"),
                    "payload": dict(compensation.get("details") or {}),
                }
            )
        for trace in traces:
            trace_id = str(trace.get("trace_id") or "")
            trace_detail = self.store.get_trace(trace_id) if trace_id else None
            for candidate in list((trace_detail or {}).get("candidates") or []):
                merged_events.append(
                    {
                        "source": "candidate",
                        "timestamp": candidate.get("created_at"),
                        "trace_id": trace_id,
                        "candidate_id": candidate.get("candidate_id"),
                        "candidate_kind": candidate.get("candidate_kind"),
                        "candidate_ref": candidate.get("candidate_ref"),
                        "payload": dict(candidate.get("metadata") or {}),
                    }
                )
            selected_action = (trace_detail or {}).get("selected_action")
            if isinstance(selected_action, dict):
                merged_events.append(
                    {
                        "source": "selected_action",
                        "timestamp": selected_action.get("created_at"),
                        "trace_id": trace_id,
                        "selected_id": selected_action.get("selected_id"),
                        "candidate_id": selected_action.get("candidate_id"),
                        "selected_reason": selected_action.get("selected_reason"),
                        "payload": dict(selected_action.get("metadata") or {}),
                    }
                )
            for event in list((trace_detail or {}).get("events") or []):
                merged_events.append(
                    {
                        "source": "trace_event",
                        "timestamp": event.get("created_at"),
                        "trace_id": trace_id,
                        "event_type": event.get("event_type"),
                        "payload": dict(event.get("payload") or {}),
                    }
                )
            merged_events.append(
                {
                    "source": "trace_final",
                    "timestamp": trace.get("updated_at"),
                    "trace_id": trace_id,
                    "trace_status": trace.get("status"),
                    "summary": trace.get("summary"),
                    "payload": dict(trace.get("metadata") or {}),
                }
            )

        merged_events.sort(
            key=lambda item: (
                self._safe_time(item.get("timestamp")),
                str(item.get("source") or ""),
                int(item.get("attempt_number") or 0),
            )
        )
        latest_attempt = self.plan_repository.get_latest_step_attempt(plan_id=plan_id, step_id=step_id)
        final_step_state = (latest_attempt or {}).get("step_state")
        final_trace_status = traces[-1].get("status") if traces else None
        return {
            "plan_id": str(plan_id),
            "step_id": str(step_id),
            "attempt_count": len(attempts),
            "trace_count": len(traces),
            "compensation_count": len(compensations),
            "final_step_state": final_step_state,
            "final_trace_status": final_trace_status,
            "events": merged_events,
        }

    def replay_plan_timeline(self, *, plan_id: str, limit_per_step: int = 500) -> dict[str, Any]:
        attempts = self.plan_repository.list_step_attempts(plan_id=plan_id, limit=max(1, int(limit_per_step)))
        step_ids = sorted({str(item.get("step_id") or "") for item in attempts if str(item.get("step_id") or "")})
        step_timelines = [self.replay_step_timeline(plan_id=plan_id, step_id=step_id) for step_id in step_ids]
        return {
            "plan_id": str(plan_id),
            "step_count": len(step_timelines),
            "steps": step_timelines,
        }
