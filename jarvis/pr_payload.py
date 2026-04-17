from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Sequence

from .approval_packet import ApprovalPacket
from .models import PlanArtifact, PlanStep


@dataclass(frozen=True)
class PullRequestPayload:
    repo_id: str
    base_branch: str
    head_branch: str
    title: str
    body_markdown: str
    draft: bool = True
    labels: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["labels"] = list(self.labels)
        return data


def _path_hint(packet: ApprovalPacket, step: PlanStep) -> str:
    if packet.ranked_candidates:
        return packet.ranked_candidates[0].path
    relative = str(step.payload.get("relative_path", "")).strip()
    if relative:
        return relative
    return step.proposed_action


def _title(plan: PlanArtifact, step: PlanStep, packet: ApprovalPacket) -> str:
    hint = _path_hint(packet, step)
    name = PurePosixPath(hint).name or hint
    title = f"[JARVIS] {plan.intent}: {name}"
    if len(title) <= 72:
        return title
    return title[:69] + "..."


def _labels(plan: PlanArtifact, packet: ApprovalPacket) -> tuple[str, ...]:
    labels = ["jarvis", "needs-review"]
    intent = plan.intent.replace("_", "-")
    labels.append(intent[:50])
    if packet.diff_summary.touches_protected_paths:
        labels.append("protected-change")
    if any(not check.passed for check in packet.preflight):
        labels.append("preflight-failed")
    return tuple(dict.fromkeys(labels))


def build_pull_request_payload(
    *,
    plan: PlanArtifact,
    step: PlanStep,
    packet: ApprovalPacket,
    base_branch: str,
    head_branch: str,
    commit_sha: str,
    approved_by: str | None,
    remote_name: str,
    draft: bool = True,
) -> PullRequestPayload:
    candidate_lines = [
        f"- `{candidate.path}` (score={candidate.score:.2f}) — {', '.join(candidate.reasons) or 'n/a'}"
        for candidate in packet.ranked_candidates[:5]
    ] or ["- none"]

    preflight_lines = [
        f"- **{check.name}**: {'PASS' if check.passed else 'FAIL'} (code={check.return_code})"
        for check in packet.preflight
    ] or ["- none"]

    outcome_lines = [
        f"- `{outcome.path}` — {outcome.status} (weight={outcome.weight:.2f}) {outcome.note}".rstrip()
        for outcome in packet.recent_outcomes[:8]
    ] or ["- none"]

    touched = ", ".join(f"`{path}`" for path in packet.diff_summary.touched_files) or "none"
    protected = ", ".join(f"`{path}`" for path in packet.diff_summary.protected_files) or "none"
    rollback = "\n".join(f"- {item}" for item in packet.rollback_plan) or "- none"
    notes = "\n".join(f"- {note}" for note in packet.notes) or "- none"

    body = "\n".join(
        [
            "## Summary",
            plan.reasoning_summary,
            "",
            "## Why this branch exists",
            packet.reason,
            "",
            "## Change scope",
            f"- Base branch: `{base_branch}`",
            f"- Head branch: `{head_branch}`",
            f"- Remote: `{remote_name}`",
            f"- Commit: `{commit_sha}`",
            f"- Approval: `{packet.approval_id}`",
            f"- Approved by: `{approved_by or 'unknown'}`",
            "",
            "## Ranked root-cause candidates",
            *candidate_lines,
            "",
            "## Files touched",
            f"- Touched: {touched}",
            f"- Protected: {protected}",
            "",
            "## Preflight",
            *preflight_lines,
            "",
            "## Recent related outcomes",
            *outcome_lines,
            "",
            "## Rollback plan",
            rollback,
            "",
            "## Notes",
            notes,
            "",
            "## Audit refs",
            f"- Plan: `{plan.plan_id}`",
            f"- Step: `{step.step_id}`",
            f"- Permission class: `{packet.permission_class}`",
        ]
    )

    return PullRequestPayload(
        repo_id=packet.repo_id,
        base_branch=base_branch,
        head_branch=head_branch,
        title=_title(plan, step, packet),
        body_markdown=body,
        draft=draft,
        labels=_labels(plan, packet),
    )
