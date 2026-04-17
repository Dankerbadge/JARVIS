from __future__ import annotations

from typing import Any, Mapping, Sequence


def latest_repo_delta_key(repo_id: str, branch: str) -> str:
    return f"latest_repo_delta:{repo_id}:{branch}"


def latest_ci_failure_key(repo_id: str, branch: str) -> str:
    return f"latest_ci_failure:{repo_id}:{branch}"


def latest_root_cause_report_key(repo_id: str, branch: str) -> str:
    return f"latest_root_cause_report:{repo_id}:{branch}"


def recent_plan_outcomes_key(repo_id: str, branch: str) -> str:
    return f"recent_plan_outcomes:{repo_id}:{branch}"


def latest_review_artifact_key(repo_id: str, branch: str) -> str:
    return f"latest_review_artifact:{repo_id}:{branch}"


def latest_review_status_key(repo_id: str, branch: str) -> str:
    return f"latest_review_status:{repo_id}:{branch}"


def latest_requested_reviewers_key(repo_id: str, branch: str) -> str:
    return f"latest_requested_reviewers:{repo_id}:{branch}"


def latest_review_summary_key(repo_id: str, branch: str) -> str:
    return f"latest_review_summary:{repo_id}:{branch}"


def latest_review_comments_key(repo_id: str, branch: str) -> str:
    return f"latest_review_comments:{repo_id}:{branch}"


def latest_timeline_cursor_key(repo_id: str, branch: str) -> str:
    return f"latest_timeline_cursor:{repo_id}:{branch}"


def latest_merge_outcome_key(repo_id: str, branch: str) -> str:
    return f"latest_merge_outcome:{repo_id}:{branch}"


def latest_academic_overview_key(term_id: str) -> str:
    return f"latest_academic_overview:{term_id}"


def latest_course_risk_key(course_id: str) -> str:
    return f"latest_course_risk:{course_id}"


def latest_deadline_cluster_key(term_id: str) -> str:
    return f"latest_deadline_cluster:{term_id}"


def latest_study_recommendation_key(course_id: str) -> str:
    return f"latest_study_recommendation:{course_id}"


def latest_academic_schedule_context_key(term_id: str) -> str:
    return f"latest_academic_schedule_context:{term_id}"


def latest_academic_suppression_windows_key(term_id: str) -> str:
    return f"latest_academic_suppression_windows:{term_id}"


def latest_user_model_key(profile_id: str = "default") -> str:
    return f"latest_user_model:{profile_id}"


def latest_personal_context_key(profile_id: str = "default") -> str:
    return f"latest_personal_context:{profile_id}"


def latest_market_risk_posture_key(account_id: str = "default") -> str:
    return f"latest_market_risk_posture:{account_id}"


def latest_market_opportunity_key(signal_id: str) -> str:
    return f"latest_market_opportunity:{signal_id}"


def latest_market_abstention_key(signal_id: str) -> str:
    return f"latest_market_abstention:{signal_id}"


def latest_market_event_key(event_id: str) -> str:
    return f"latest_market_event:{event_id}"


def latest_market_handoff_key(handoff_id: str) -> str:
    return f"latest_market_handoff:{handoff_id}"


def latest_market_outcome_key(handoff_id: str) -> str:
    return f"latest_market_outcome:{handoff_id}"


class CorrelationStateView:
    """Adapter over nested dictionaries used by standalone correlation tests."""

    def __init__(self, world_state: Mapping[str, Any] | None) -> None:
        self.world_state = world_state or {}

    def latest_repo_delta(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        return ((self.world_state.get("latest_repo_delta") or {}).get(repo_id) or {}).get(branch)

    def latest_ci_failure(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        return ((self.world_state.get("latest_ci_failure") or {}).get(repo_id) or {}).get(branch)

    def recent_plan_outcomes(self, repo_id: str, branch: str) -> Sequence[Mapping[str, Any]]:
        return (
            ((self.world_state.get("recent_plan_outcomes") or {}).get(repo_id) or {}).get(branch)
            or []
        )


class RuntimeCorrelationStateView:
    """Adapter for the SQLite-backed runtime state graph + plan outcome store."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def latest_repo_delta(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_repo_delta_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_ci_failure(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_ci_failure_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_root_cause_report(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_root_cause_report_key(repo_id, branch),
        )
        return row["value"] if row else None

    def recent_plan_outcomes(self, repo_id: str, branch: str) -> Sequence[Mapping[str, Any]]:
        return self.runtime.plan_repo.list_recent_outcomes(repo_id, branch, limit=100)

    def latest_review_artifact(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_review_artifact_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_review_status(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_review_status_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_requested_reviewers(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_requested_reviewers_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_review_summary(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_review_summary_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_review_comments(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_review_comments_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_timeline_cursor(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_timeline_cursor_key(repo_id, branch),
        )
        return row["value"] if row else None

    def latest_merge_outcome(self, repo_id: str, branch: str) -> Mapping[str, Any] | None:
        row = self.runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_merge_outcome_key(repo_id, branch),
        )
        return row["value"] if row else None
