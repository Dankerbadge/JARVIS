from __future__ import annotations

from typing import Any

from .schema import TraceStatus
from .store import ReasoningStore


class ReasoningTracer:
    def __init__(self, store: ReasoningStore) -> None:
        self.store = store

    def open_step_trace(
        self,
        *,
        plan_id: str,
        step_id: str,
        action_class: str,
        proposed_action: str,
        payload: dict[str, Any] | None = None,
        dry_run: bool = True,
    ) -> str:
        trace_id = self.store.create_trace(
            plan_id=plan_id,
            step_id=step_id,
            action_class=action_class,
            proposed_action=proposed_action,
            status=TraceStatus.RUNNING.value,
            metadata={
                "dry_run": bool(dry_run),
                "payload": dict(payload or {}),
            },
        )
        self.store.append_event(
            trace_id=trace_id,
            event_type="trace.opened",
            payload={
                "plan_id": plan_id,
                "step_id": step_id,
                "action_class": action_class,
                "proposed_action": proposed_action,
            },
        )
        return trace_id

    def record_event(
        self,
        *,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self.store.append_event(
            trace_id=trace_id,
            event_type=event_type,
            payload=payload,
        )

    def add_candidate(
        self,
        *,
        trace_id: str,
        candidate_kind: str,
        candidate_ref: str,
        rationale: str | None = None,
        expected_value: float | None = None,
        confidence: float | None = None,
        cost: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        candidate_id = self.store.add_candidate(
            trace_id=trace_id,
            candidate_kind=candidate_kind,
            candidate_ref=candidate_ref,
            rationale=rationale,
            expected_value=expected_value,
            confidence=confidence,
            cost=cost,
            metadata=metadata,
        )
        self.store.append_event(
            trace_id=trace_id,
            event_type="candidate.added",
            payload={
                "candidate_id": candidate_id,
                "candidate_kind": candidate_kind,
                "candidate_ref": candidate_ref,
            },
        )
        return candidate_id

    def select_candidate(
        self,
        *,
        trace_id: str,
        candidate_id: str,
        selected_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        selected_id = self.store.select_candidate(
            trace_id=trace_id,
            candidate_id=candidate_id,
            selected_reason=selected_reason,
            metadata=metadata,
        )
        self.store.append_event(
            trace_id=trace_id,
            event_type="candidate.selected",
            payload={
                "selected_id": selected_id,
                "candidate_id": candidate_id,
                "selected_reason": selected_reason,
            },
        )
        return selected_id

    def finalize_trace(
        self,
        *,
        trace_id: str,
        status: TraceStatus | str,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        resolved_status = (
            status.value if isinstance(status, TraceStatus) else str(status or "").strip().lower()
        )
        self.store.append_event(
            trace_id=trace_id,
            event_type=f"trace.finalized:{resolved_status}",
            payload=dict(payload or {}),
        )
        self.store.update_trace(
            trace_id=trace_id,
            status=resolved_status,
            summary=summary,
            metadata_patch={"finalized": True, "final_status": resolved_status},
        )
