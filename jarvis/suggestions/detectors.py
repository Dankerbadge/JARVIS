from __future__ import annotations

from typing import Any

from .models import SuggestionCandidate


class SuggestionDetectors:
    def from_reasoning_traces(self, traces: list[dict[str, Any]]) -> list[SuggestionCandidate]:
        out: list[SuggestionCandidate] = []
        for trace in traces:
            status = str(trace.get("status") or "").strip().lower()
            trace_id = str(trace.get("trace_id") or "")
            proposed_action = str(trace.get("proposed_action") or "action").strip() or "action"
            action_class = str(trace.get("action_class") or "P1").strip() or "P1"
            metadata = trace.get("metadata") if isinstance(trace.get("metadata"), dict) else {}
            is_review_related = "review" in proposed_action.lower()
            is_continuity_related = "continuity" in proposed_action.lower() or bool(
                metadata.get("continuity_regression")
            )

            if status == "blocked":
                out.append(
                    SuggestionCandidate(
                        kind="approval_followup",
                        domain="workflow",
                        trigger="trace_blocked",
                        why_now=f"Step '{proposed_action}' is blocked and requires intervention.",
                        why_not_later="Delaying increases execution latency and plan staleness.",
                        cost=0.2,
                        confidence=0.88,
                        expected_value=0.72,
                        required_context={
                            "trace_id": trace_id,
                            "step_id": trace.get("step_id"),
                            "plan_id": trace.get("plan_id"),
                        },
                        approval_class="soft" if action_class == "P2" else "hard",
                        source_trace_id=trace_id or None,
                    )
                )
                if is_review_related:
                    out.append(
                        SuggestionCandidate(
                            kind="review_sync_unblock",
                            domain="review",
                            trigger="review_trace_blocked",
                            why_now=f"Review step '{proposed_action}' is blocked and may stall merge readiness.",
                            why_not_later="Review context and thread state drift quickly after delays.",
                            cost=0.35,
                            confidence=0.84,
                            expected_value=0.77,
                            required_context={
                                "trace_id": trace_id,
                                "step_id": trace.get("step_id"),
                                "plan_id": trace.get("plan_id"),
                            },
                            approval_class="soft",
                            source_trace_id=trace_id or None,
                        )
                    )
            elif status == "failed":
                out.append(
                    SuggestionCandidate(
                        kind="failure_investigation",
                        domain="workflow",
                        trigger="trace_failed",
                        why_now=f"Step '{proposed_action}' failed and needs root-cause follow-up.",
                        why_not_later="Failure patterns get harder to diagnose as context decays.",
                        cost=0.5,
                        confidence=0.82,
                        expected_value=0.79,
                        required_context={
                            "trace_id": trace_id,
                            "step_id": trace.get("step_id"),
                            "plan_id": trace.get("plan_id"),
                        },
                        approval_class="none",
                        source_trace_id=trace_id or None,
                    )
                )
                if is_review_related:
                    out.append(
                        SuggestionCandidate(
                            kind="review_sync_repair",
                            domain="review",
                            trigger="review_trace_failed",
                            why_now=f"Review-related step '{proposed_action}' failed and needs synchronization repair.",
                            why_not_later="Delays can desync approvals, labels, and requested reviewers.",
                            cost=0.4,
                            confidence=0.8,
                            expected_value=0.78,
                            required_context={
                                "trace_id": trace_id,
                                "step_id": trace.get("step_id"),
                                "plan_id": trace.get("plan_id"),
                            },
                            approval_class="none",
                            source_trace_id=trace_id or None,
                        )
                    )
            elif status == "compensated":
                out.append(
                    SuggestionCandidate(
                        kind="compensation_review",
                        domain="workflow",
                        trigger="trace_compensated",
                        why_now=f"Step '{proposed_action}' required compensation and should be reviewed for recurrence.",
                        why_not_later="Compensation without follow-up usually repeats failure patterns.",
                        cost=0.45,
                        confidence=0.79,
                        expected_value=0.74,
                        required_context={
                            "trace_id": trace_id,
                            "step_id": trace.get("step_id"),
                            "plan_id": trace.get("plan_id"),
                        },
                        approval_class="none",
                        source_trace_id=trace_id or None,
                    )
                )
            if is_continuity_related and status in {"failed", "blocked", "compensated"}:
                out.append(
                    SuggestionCandidate(
                        kind="continuity_regression_followup",
                        domain="continuity",
                        trigger=f"continuity_trace_{status}",
                        why_now=f"Continuity-sensitive step '{proposed_action}' regressed and needs stabilization.",
                        why_not_later="Voice and dialogue continuity degrade quickly when regressions linger.",
                        cost=0.4,
                        confidence=0.86,
                        expected_value=0.81,
                        required_context={
                            "trace_id": trace_id,
                            "step_id": trace.get("step_id"),
                            "plan_id": trace.get("plan_id"),
                        },
                        approval_class="none",
                        source_trace_id=trace_id or None,
                    )
                )
        return out
