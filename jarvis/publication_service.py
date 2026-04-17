from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .approval_packet import ApprovalPacket
from .executors.git_remote import CommitReceipt, GitRemoteExecutor, PushReceipt
from .models import PlanArtifact, PlanStep, utc_now_iso
from .pr_payload import PullRequestPayload, build_pull_request_payload


@dataclass(frozen=True)
class PublicationReceipt:
    approval_id: str
    plan_id: str
    step_id: str
    repo_id: str
    remote_name: str
    remote_url: str
    base_branch: str
    head_branch: str
    commit: CommitReceipt
    push: PushReceipt
    pr_payload: PullRequestPayload
    published_at: str
    sandbox_path: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["commit"] = self.commit.to_dict()
        data["push"] = self.push.to_dict()
        data["pr_payload"] = self.pr_payload.to_dict()
        return data


class RemotePublicationService:
    def __init__(self, *, repo_path: str | Path, remote_executor: GitRemoteExecutor | None = None) -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self.remote_executor = remote_executor or GitRemoteExecutor(self.repo_path)

    def _infer_base_branch(
        self,
        *,
        plan: PlanArtifact,
        approval_packet: ApprovalPacket,
        explicit_base_branch: str | None,
    ) -> str:
        if explicit_base_branch:
            return explicit_base_branch
        root_payload = plan.steps[0].payload if plan.steps else {}
        latest_git_delta = root_payload.get("latest_git_delta", {}) or {}
        for candidate in (
            latest_git_delta.get("base_branch"),
            root_payload.get("base_branch"),
            approval_packet.branch,
            "main",
        ):
            value = str(candidate or "").strip()
            if value and value.upper() != "HEAD":
                return value
        return "main"

    def _commit_message(self, *, plan: PlanArtifact, step: PlanStep, approval_packet: ApprovalPacket) -> str:
        hint = step.payload.get("relative_path") or (
            approval_packet.ranked_candidates[0].path if approval_packet.ranked_candidates else step.proposed_action
        )
        return f"jarvis: {plan.intent} [{hint}]"

    def publish_prepared_step(
        self,
        *,
        plan: PlanArtifact,
        step: PlanStep,
        approval: dict[str, Any],
        approval_packet_data: dict[str, Any],
        remote_name: str = "origin",
        base_branch: str | None = None,
        draft: bool = True,
        force_with_lease: bool = False,
    ) -> PublicationReceipt:
        packet = ApprovalPacket.from_dict(approval_packet_data["packet"])
        sandbox = approval_packet_data.get("sandbox", {})
        sandbox_path = str(sandbox.get("sandbox_path", ""))
        if not sandbox_path or not Path(sandbox_path).exists():
            raise RuntimeError("Prepared sandbox path is missing or no longer exists.")

        commit_message = self._commit_message(plan=plan, step=step, approval_packet=packet)
        commit = self.remote_executor.commit_all(sandbox_path=sandbox_path, message=commit_message)
        push = self.remote_executor.push_branch(
            sandbox_path=sandbox_path,
            remote_name=remote_name,
            branch_name=str(sandbox.get("branch_name") or None),
            force_with_lease=force_with_lease,
        )
        inferred_base_branch = self._infer_base_branch(
            plan=plan,
            approval_packet=packet,
            explicit_base_branch=base_branch,
        )
        pr_payload = build_pull_request_payload(
            plan=plan,
            step=step,
            packet=packet,
            base_branch=inferred_base_branch,
            head_branch=push.branch_name,
            commit_sha=push.head_sha,
            approved_by=approval.get("approved_by"),
            remote_name=remote_name,
            draft=draft,
        )
        return PublicationReceipt(
            approval_id=str(approval["approval_id"]),
            plan_id=plan.plan_id,
            step_id=step.step_id,
            repo_id=packet.repo_id,
            remote_name=remote_name,
            remote_url=push.remote_url,
            base_branch=inferred_base_branch,
            head_branch=push.branch_name,
            commit=commit,
            push=push,
            pr_payload=pr_payload,
            published_at=utc_now_iso(),
            sandbox_path=sandbox_path,
        )
