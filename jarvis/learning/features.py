from __future__ import annotations

from typing import Any


def utility_from_trace_status(status: str) -> float:
    mapping = {
        "succeeded": 1.0,
        "compensated": 0.3,
        "blocked": -0.2,
        "failed": -1.0,
        "running": 0.0,
        "open": 0.0,
    }
    return float(mapping.get(str(status or "").strip().lower(), 0.0))


def apply_feedback_to_utility(
    base_utility: float,
    feedback: dict[str, Any] | None,
) -> tuple[float, str]:
    if not isinstance(feedback, dict):
        return float(base_utility), "trace"
    explicit = feedback.get("utility_score")
    if explicit is not None:
        return float(explicit), "trace+feedback.explicit"
    accepted = bool(feedback.get("accepted"))
    adjusted = float(base_utility) + (0.2 if accepted else -0.2)
    return max(-1.0, min(1.0, adjusted)), "trace+feedback.implicit"


def build_trace_feature_vector(
    *,
    trace: dict[str, Any],
    trace_detail: dict[str, Any] | None = None,
    step_attempts: list[dict[str, Any]] | None = None,
    suggestion_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = dict(trace_detail or {})
    attempts = list(step_attempts or [])
    events = list(detail.get("events") or [])
    candidates = list(detail.get("candidates") or [])
    selected_action = detail.get("selected_action") if isinstance(detail.get("selected_action"), dict) else {}

    attempt_states = [str(item.get("step_state") or "").strip().lower() for item in attempts]
    approved_count = sum(1 for state in attempt_states if state == "approved")
    failed_count = sum(1 for state in attempt_states if state == "failed")
    compensated_count = sum(1 for state in attempt_states if state == "compensated")
    blocked_count = sum(1 for state in attempt_states if state == "blocked")

    trace_status = str(trace.get("status") or "").strip().lower()
    action_class = str(trace.get("action_class") or "P1").strip().upper()
    proposed_action = str(trace.get("proposed_action") or "unknown_action")

    feature_vector: dict[str, Any] = {
        "task_type": "workflow_step_decision",
        "domain": "workflow",
        "trace_status": trace_status,
        "action_class": action_class,
        "proposed_action": proposed_action,
        "candidate_count": int(len(candidates)),
        "event_count": int(len(events)),
        "attempt_count": int(len(attempts)),
        "recent_failures": int(failed_count + compensated_count),
        "failed_attempts": int(failed_count),
        "compensated_attempts": int(compensated_count),
        "recent_approvals": int(approved_count),
        "blocked_attempts": int(blocked_count),
        "requires_approval": bool(action_class in {"P2", "P3"}),
        "selected_candidate_id": str(selected_action.get("candidate_id") or ""),
        "feedback_present": bool(isinstance(suggestion_feedback, dict)),
    }

    if isinstance(suggestion_feedback, dict):
        feature_vector["suggestion_accepted"] = bool(suggestion_feedback.get("accepted"))
        feature_vector["feedback_action_taken"] = str(suggestion_feedback.get("action_taken") or "")
        feature_vector["feedback_has_explicit_utility"] = suggestion_feedback.get("utility_score") is not None

    return feature_vector
