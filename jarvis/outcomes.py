from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


TERMINAL_STATUSES = {"success", "failure", "regression", "partial"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class PlanOutcome:
    plan_id: str
    repo_id: str
    branch: str
    status: str
    touched_paths: tuple[str, ...]
    failure_family: str | None = None
    summary: str | None = None
    recorded_at: str = field(default_factory=utc_now_iso)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True)
class PathFeedback:
    success_count: int = 0
    partial_count: int = 0
    failure_count: int = 0
    regression_count: int = 0
    last_touched_at: str | None = None

    @property
    def net_signal(self) -> float:
        return (
            (self.success_count * 1.0)
            + (self.partial_count * 0.45)
            - (self.failure_count * 0.55)
            - (self.regression_count * 0.9)
        )


def map_review_feedback_to_outcome(
    *,
    decision: str | None,
    merge_outcome: str | None,
) -> tuple[str | None, str]:
    merge = str(merge_outcome or "").strip().lower()
    review_decision = str(decision or "").strip().lower()
    if merge == "merged":
        return "success", "MERGED"
    if merge == "closed_unmerged":
        return "failure", "CLOSED_UNMERGED"
    if merge == "changes_requested" or review_decision == "changes_requested":
        return "regression", "CHANGES_REQUESTED"
    if merge == "approved" or review_decision == "approved":
        return "success", "APPROVED"
    if review_decision == "commented":
        return "commented", "COMMENTED"
    return None, "NONE"


def coerce_plan_outcome(raw: PlanOutcome | Mapping[str, Any]) -> PlanOutcome:
    if isinstance(raw, PlanOutcome):
        return raw
    return PlanOutcome(
        plan_id=str(raw["plan_id"]),
        repo_id=str(raw.get("repo_id") or raw.get("repo") or "unknown"),
        branch=str(raw.get("branch") or "unknown"),
        status=str(raw["status"]),
        touched_paths=tuple(str(path) for path in raw.get("touched_paths", [])),
        failure_family=raw.get("failure_family"),
        summary=raw.get("summary"),
        recorded_at=str(raw.get("recorded_at", utc_now_iso())),
    )


def build_path_feedback(
    outcomes: Iterable[PlanOutcome | Mapping[str, Any]],
    *,
    failure_family: str | None = None,
    branch: str | None = None,
) -> dict[str, PathFeedback]:
    stats: dict[str, dict[str, Any]] = {}

    for raw in outcomes:
        outcome = coerce_plan_outcome(raw)
        if failure_family and outcome.failure_family and outcome.failure_family != failure_family:
            continue
        if branch and outcome.branch != branch:
            continue
        if not outcome.is_terminal():
            continue

        for path in outcome.touched_paths:
            entry = stats.setdefault(
                path,
                {
                    "success_count": 0,
                    "partial_count": 0,
                    "failure_count": 0,
                    "regression_count": 0,
                    "last_touched_at": outcome.recorded_at,
                },
            )
            if outcome.recorded_at >= str(entry.get("last_touched_at", "")):
                entry["last_touched_at"] = outcome.recorded_at

            if outcome.status == "success":
                entry["success_count"] += 1
            elif outcome.status == "partial":
                entry["partial_count"] += 1
            elif outcome.status == "failure":
                entry["failure_count"] += 1
            elif outcome.status == "regression":
                entry["regression_count"] += 1

    return {path: PathFeedback(**values) for path, values in stats.items()}
