from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .correlation import RootCauseScorer
from .models import EventEnvelope, PlanArtifact
from .models import new_id, utc_now_iso
from .state_index import (
    RuntimeCorrelationStateView,
    latest_ci_failure_key,
    latest_root_cause_report_key,
)


class BaseReactor(ABC):
    """Turns ingested events/state triggers into plan proposals."""

    name: str

    @abstractmethod
    def propose_plans(
        self,
        runtime: Any,
        event: EventEnvelope,
        ingestion_outcome: dict[str, Any],
    ) -> list[PlanArtifact]:
        raise NotImplementedError


class ZenithRiskReactor(BaseReactor):
    """Produces Zenith plans when high-risk triggers appear."""

    name = "zenith_risk"

    def propose_plans(
        self,
        runtime: Any,
        event: EventEnvelope,
        ingestion_outcome: dict[str, Any],
    ) -> list[PlanArtifact]:
        if event.source_type in {"repo.git_delta", "ci.failure"}:
            # Specialized reactors handle these to avoid duplicate plans.
            return []
        triggers = ingestion_outcome.get("triggers", [])
        if not triggers:
            return []
        return runtime.planner.build_plans(triggers)


class ZenithGitDeltaReactor(BaseReactor):
    """Builds branch-aware plans from git-native repo deltas."""

    name = "zenith_git_delta"

    def propose_plans(
        self,
        runtime: Any,
        event: EventEnvelope,
        ingestion_outcome: dict[str, Any],
    ) -> list[PlanArtifact]:
        if event.source_type != "repo.git_delta":
            return []
        payload = event.payload
        repo_id = str(payload.get("repo_id") or payload.get("repo_path") or "unknown")
        branch = str(payload.get("branch") or "unknown")
        latest_ci = runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_ci_failure_key(repo_id, branch),
        )
        if latest_ci:
            # Correlation reactors should handle refresh when CI context exists.
            return []
        plan = runtime.zenith.propose_git_delta_plan(event.payload)
        return [plan] if plan else []


class ZenithCiFailureReactor(BaseReactor):
    """Builds higher-confidence plans from CI failures implicating Zenith-owned paths."""

    name = "zenith_ci_failure"

    def propose_plans(
        self,
        runtime: Any,
        event: EventEnvelope,
        ingestion_outcome: dict[str, Any],
    ) -> list[PlanArtifact]:
        if event.source_type != "ci.failure":
            return []
        payload = event.payload
        repo_id = str(payload.get("repo_id") or payload.get("repo_path") or "unknown")
        branch = str(payload.get("branch") or "unknown")
        latest_delta_entity = runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=f"latest_repo_delta:{repo_id}:{branch}",
        )
        latest_delta = latest_delta_entity["value"] if latest_delta_entity else None
        plan = runtime.zenith.propose_ci_failure_plan(payload, latest_git_delta=latest_delta)
        return [plan] if plan else []


class ZenithCorrelationReactor(BaseReactor):
    """Stateful root-cause correlation over CI failure + latest git delta + outcomes."""

    name = "zenith_root_cause_correlation"

    def __init__(
        self,
        *,
        protected_paths: tuple[str, ...] = ("ui/",),
        owned_paths: tuple[str, ...] = ("service.py", "ui/", "api/", "zenith/", "jarvis/"),
    ) -> None:
        self.scorer = RootCauseScorer(
            protected_paths=protected_paths,
            owned_paths=owned_paths,
        )

    def propose_plans(
        self,
        runtime: Any,
        event: EventEnvelope,
        ingestion_outcome: dict[str, Any],
    ) -> list[PlanArtifact]:
        if event.source_type not in {"ci.failure", "repo.git_delta"}:
            return []

        state_view = RuntimeCorrelationStateView(runtime)
        payload = event.payload
        repo_id = str(payload.get("repo_id") or payload.get("repo_path") or "unknown")
        branch = str(payload.get("branch") or "unknown")
        if repo_id == "unknown" or branch == "unknown":
            return []

        if event.source_type == "ci.failure":
            ci_failure = payload
            repo_delta = state_view.latest_repo_delta(repo_id, branch)
        else:
            repo_delta = payload
            ci_failure = state_view.latest_ci_failure(repo_id, branch)
            if not ci_failure:
                return []

        recent_outcomes = state_view.recent_plan_outcomes(repo_id, branch)
        report = self.scorer.rank(
            repo_id=repo_id,
            branch=branch,
            repo_delta=repo_delta,
            ci_failure=ci_failure,
            recent_outcomes=recent_outcomes,
        )
        if not report.candidates:
            return []

        report_key = latest_root_cause_report_key(repo_id, branch)
        root_entity_id = runtime.state_graph.upsert_entity(
            entity_id=new_id("ent"),
            entity_key=report_key,
            entity_type="Artifact",
            value={
                **report.as_dict(),
                "repo_delta_head_sha": (repo_delta or {}).get("head_sha"),
                "source_signal_kind": event.source_type,
                "updated_from": event.event_id,
            },
            confidence=report.confidence,
            source_refs=[event.event_id],
            last_verified_at=utc_now_iso(),
        )
        runtime.memory.add_semantic(
            memory_id=new_id("sem"),
            memory_key=report_key,
            text_value=(
                f"Root-cause report for {repo_id}:{branch} top candidate "
                f"{report.candidates[0].path} ({report.candidates[0].score})."
            ),
            confidence=report.confidence,
            provenance_event_ids=[event.event_id],
            provenance_state_ids=[root_entity_id],
        )
        plan = runtime.zenith.propose_root_cause_plan(
            ci_failure_payload=dict(ci_failure),
            correlation_report=report,
            latest_git_delta=dict(repo_delta) if repo_delta else None,
            source_signal_kind=event.source_type,
        )
        return [plan] if plan else []
