from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True)
class RankedCandidate:
    path: str
    score: float
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class DiffSummary:
    touched_files: Sequence[str]
    protected_files: Sequence[str]
    patch_bytes: int = 0

    @property
    def touches_protected_paths(self) -> bool:
        return bool(self.protected_files)


@dataclass(frozen=True)
class PreflightCheckSummary:
    name: str
    passed: bool
    return_code: int
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""


@dataclass(frozen=True)
class OutcomeSummary:
    path: str
    status: str
    weight: float
    note: str = ""


@dataclass(frozen=True)
class ApprovalPacket:
    approval_id: str
    plan_id: str
    step_id: str
    permission_class: str
    reason: str
    repo_id: str
    branch: str
    confidence: float
    recommended_decision: str
    ranked_candidates: Sequence[RankedCandidate]
    diff_summary: DiffSummary
    preflight: Sequence[PreflightCheckSummary]
    rollback_plan: Sequence[str]
    recent_outcomes: Sequence[OutcomeSummary] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalPacket":
        return ApprovalPacket(
            approval_id=data["approval_id"],
            plan_id=data["plan_id"],
            step_id=data["step_id"],
            permission_class=data["permission_class"],
            reason=data["reason"],
            repo_id=data["repo_id"],
            branch=data["branch"],
            confidence=float(data["confidence"]),
            recommended_decision=data["recommended_decision"],
            ranked_candidates=tuple(
                RankedCandidate(
                    path=item["path"],
                    score=float(item["score"]),
                    reasons=tuple(item.get("reasons", [])),
                )
                for item in data.get("ranked_candidates", [])
            ),
            diff_summary=DiffSummary(
                touched_files=tuple(data.get("diff_summary", {}).get("touched_files", [])),
                protected_files=tuple(data.get("diff_summary", {}).get("protected_files", [])),
                patch_bytes=int(data.get("diff_summary", {}).get("patch_bytes", 0)),
            ),
            preflight=tuple(
                PreflightCheckSummary(
                    name=item["name"],
                    passed=bool(item["passed"]),
                    return_code=int(item["return_code"]),
                    stdout_excerpt=item.get("stdout_excerpt", ""),
                    stderr_excerpt=item.get("stderr_excerpt", ""),
                )
                for item in data.get("preflight", [])
            ),
            rollback_plan=tuple(data.get("rollback_plan", [])),
            recent_outcomes=tuple(
                OutcomeSummary(
                    path=item["path"],
                    status=item["status"],
                    weight=float(item["weight"]),
                    note=item.get("note", ""),
                )
                for item in data.get("recent_outcomes", [])
            ),
            notes=tuple(data.get("notes", [])),
        )

    def to_markdown(self) -> str:
        candidate_lines = [
            f"- `{candidate.path}` - score={candidate.score:.2f}; reasons: {', '.join(candidate.reasons) or 'n/a'}"
            for candidate in self.ranked_candidates
        ] or ["- none"]

        preflight_lines = [
            f"- **{check.name}**: {'PASS' if check.passed else 'FAIL'} (code={check.return_code})"
            for check in self.preflight
        ] or ["- none"]

        outcome_lines = [
            f"- `{outcome.path}` - {outcome.status} (weight={outcome.weight:.2f}) {outcome.note}".rstrip()
            for outcome in self.recent_outcomes
        ] or ["- none"]

        note_lines = [f"- {note}" for note in self.notes] or ["- none"]

        touched = ", ".join(f"`{path}`" for path in self.diff_summary.touched_files) or "none"
        protected = ", ".join(f"`{path}`" for path in self.diff_summary.protected_files) or "none"
        rollback = "\n".join(f"- {step}" for step in self.rollback_plan) or "- none"

        return "\n".join(
            [
                f"# Approval Packet `{self.approval_id}`",
                "",
                f"- Plan: `{self.plan_id}`",
                f"- Step: `{self.step_id}`",
                f"- Permission: `{self.permission_class}`",
                f"- Repo/Branch: `{self.repo_id}` / `{self.branch}`",
                f"- Confidence: {self.confidence:.2f}",
                f"- Recommended decision: **{self.recommended_decision}**",
                "",
                "## Reason",
                self.reason,
                "",
                "## Ranked root-cause candidates",
                *candidate_lines,
                "",
                "## Diff summary",
                f"- Touched files: {touched}",
                f"- Protected files: {protected}",
                f"- Patch bytes: {self.diff_summary.patch_bytes}",
                "",
                "## Preflight",
                *preflight_lines,
                "",
                "## Rollback plan",
                rollback,
                "",
                "## Recent outcomes",
                *outcome_lines,
                "",
                "## Notes",
                *note_lines,
            ]
        )


def _decide_recommendation(
    permission_class: str,
    confidence: float,
    preflight: Sequence[PreflightCheckSummary],
    touches_protected_paths: bool,
) -> str:
    failed = any(not check.passed for check in preflight)
    if failed:
        return "deny"
    if permission_class in {"P0", "P1"} and not touches_protected_paths:
        return "approve"
    if confidence >= 0.85 and not touches_protected_paths:
        return "approve"
    return "manual-review"


def build_approval_packet(
    *,
    approval_id: str,
    plan_id: str,
    step_id: str,
    permission_class: str,
    reason: str,
    repo_id: str,
    branch: str,
    confidence: float,
    ranked_candidates: Iterable[RankedCandidate],
    diff_summary: DiffSummary,
    preflight: Iterable[PreflightCheckSummary],
    rollback_plan: Iterable[str],
    recent_outcomes: Iterable[OutcomeSummary] = (),
    notes: Iterable[str] = (),
) -> ApprovalPacket:
    candidates = tuple(ranked_candidates)
    preflight_checks = tuple(preflight)
    outcomes = tuple(recent_outcomes)
    notes_tuple = tuple(notes)

    recommendation = _decide_recommendation(
        permission_class=permission_class,
        confidence=confidence,
        preflight=preflight_checks,
        touches_protected_paths=diff_summary.touches_protected_paths,
    )

    if diff_summary.touches_protected_paths and "Protected paths touched." not in notes_tuple:
        notes_tuple = notes_tuple + ("Protected paths touched.",)

    return ApprovalPacket(
        approval_id=approval_id,
        plan_id=plan_id,
        step_id=step_id,
        permission_class=permission_class,
        reason=reason,
        repo_id=repo_id,
        branch=branch,
        confidence=confidence,
        recommended_decision=recommendation,
        ranked_candidates=candidates,
        diff_summary=diff_summary,
        preflight=preflight_checks,
        rollback_plan=tuple(rollback_plan),
        recent_outcomes=outcomes,
        notes=notes_tuple,
    )

