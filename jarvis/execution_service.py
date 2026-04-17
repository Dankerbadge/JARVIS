from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .approval_packet import (
    ApprovalPacket,
    DiffSummary,
    OutcomeSummary,
    PreflightCheckSummary,
    RankedCandidate,
    build_approval_packet,
)
from .executors.git_worktree import GitWorktreeExecutor, SandboxSession
from .preflight import PreflightReport, PreflightRunner
from .skills.zenith import RepoDiffEngine


@dataclass(frozen=True)
class PreparedExecution:
    packet: ApprovalPacket
    sandbox: SandboxSession
    preflight_report: PreflightReport
    touched_files: Sequence[str]
    patch_text: str


def _protected_subset(paths: Sequence[str], protected_prefixes: Sequence[str]) -> list[str]:
    results: list[str] = []
    for path in paths:
        if any(path.startswith(prefix) for prefix in protected_prefixes):
            results.append(path)
    return results


class ApprovalExecutionService:
    def __init__(
        self,
        *,
        repo_path: str,
        worktrees_root: str,
        protected_prefixes: Sequence[str],
        preflight_runner: PreflightRunner | None = None,
    ) -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self.worktrees_root = str(Path(worktrees_root).resolve())
        self.protected_prefixes = tuple(protected_prefixes)
        self.preflight_runner = preflight_runner or PreflightRunner()
        self.executor = GitWorktreeExecutor(repo_path=self.repo_path, worktrees_root=self.worktrees_root)
        self.diff_engine = RepoDiffEngine(self.repo_path)

    def build_patch_for_step(self, *, proposed_action: str, payload: dict) -> str:
        if proposed_action != "zenith_apply_protected_ui_patch":
            return ""
        relative_path = str(payload.get("relative_path", "ui/zenith_ui.txt"))
        search = str(payload.get("search", "TODO_UI"))
        replacement = str(payload.get("replacement", "READY_UI"))
        proposal = self.diff_engine.preview_file_replacement(relative_path, search, replacement)
        return proposal.patch if proposal.changed else ""

    def _default_preflight_checks(self, touched_files: Sequence[str]) -> list[tuple[str, Sequence[str]]]:
        checks: list[tuple[str, Sequence[str]]] = [("git-status", ("git", "status", "--short"))]
        py_files = [path for path in touched_files if path.endswith(".py")]
        for file_path in py_files[:5]:
            checks.append((f"py-compile:{file_path}", ("python3", "-m", "py_compile", file_path)))
        return checks

    def prepare_protected_step(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        permission_class: str,
        reason: str,
        repo_id: str,
        branch: str,
        confidence: float,
        patch_text: str,
        ranked_candidates: Iterable[RankedCandidate],
        recent_outcomes: Iterable[OutcomeSummary] = (),
        preflight_checks: Iterable[tuple[str, Sequence[str]]] = (),
        action_desc: str = "",
    ) -> PreparedExecution:
        sandbox = self.executor.create_sandbox(plan_id=plan_id)
        self.executor.apply_unified_diff(sandbox_path=sandbox.sandbox_path, patch_text=patch_text)
        touched_files = tuple(self.executor.list_changed_files(sandbox_path=sandbox.sandbox_path))
        checks = tuple(preflight_checks) or tuple(self._default_preflight_checks(touched_files))
        preflight_report = self.preflight_runner.run(
            working_dir=sandbox.sandbox_path,
            checks=checks,
        )
        packet = build_approval_packet(
            approval_id=approval_id,
            plan_id=plan_id,
            step_id=step_id,
            permission_class=permission_class,
            reason=reason,
            repo_id=repo_id,
            branch=branch,
            confidence=confidence,
            ranked_candidates=ranked_candidates,
            diff_summary=DiffSummary(
                touched_files=touched_files,
                protected_files=_protected_subset(touched_files, self.protected_prefixes),
                patch_bytes=len(patch_text.encode("utf-8")),
            ),
            preflight=[
                PreflightCheckSummary(
                    name=check.name,
                    passed=check.passed,
                    return_code=check.return_code,
                    stdout_excerpt=check.stdout_excerpt,
                    stderr_excerpt=check.stderr_excerpt,
                )
                for check in preflight_report.checks
            ],
            rollback_plan=(
                f"Remove sandbox worktree at {sandbox.sandbox_path}",
                f"Delete sandbox branch {sandbox.branch_name} if it is no longer needed",
                "Discard unmerged changes from the sandbox",
            ),
            recent_outcomes=recent_outcomes,
            notes=tuple(
                note
                for note in (
                    "Prepared in isolated git worktree.",
                    f"Sandbox branch: {sandbox.branch_name}",
                    f"Action: {action_desc}" if action_desc else None,
                )
                if note
            ),
        )
        return PreparedExecution(
            packet=packet,
            sandbox=sandbox,
            preflight_report=preflight_report,
            touched_files=touched_files,
            patch_text=patch_text,
        )

    def cleanup(self, prepared: PreparedExecution) -> None:
        self.executor.cleanup(sandbox_path=prepared.sandbox.sandbox_path)
