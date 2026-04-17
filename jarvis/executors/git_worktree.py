from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


class GitWorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxSession:
    repo_path: str
    sandbox_path: str
    branch_name: str
    base_ref: str


@dataclass(frozen=True)
class CommandReceipt:
    command: Sequence[str]
    return_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ExecutionReceipt:
    sandbox_path: str
    branch_name: str
    changed_files: Sequence[str]
    command_receipts: Sequence[CommandReceipt]
    patch_applied: bool


class GitWorktreeExecutor:
    def __init__(self, repo_path: str, worktrees_root: str) -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self.worktrees_root = str(Path(worktrees_root).resolve())
        Path(self.worktrees_root).mkdir(parents=True, exist_ok=True)

    def _run(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            input=input_text,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise GitWorktreeError(
                f"Command failed ({completed.returncode}): {' '.join(command)}\n"
                f"stdout={completed.stdout}\n"
                f"stderr={completed.stderr}"
            )
        return completed

    def create_sandbox(
        self,
        *,
        plan_id: str,
        base_ref: str = "HEAD",
        branch_name: str | None = None,
    ) -> SandboxSession:
        branch = branch_name or f"jarvis/{plan_id}"
        sandbox_dir = Path(self.worktrees_root) / f"{plan_id}-{uuid.uuid4().hex[:8]}"
        self._run(["git", "-C", self.repo_path, "worktree", "add", "--detach", str(sandbox_dir), base_ref])
        self._run(["git", "-C", str(sandbox_dir), "checkout", "-b", branch])
        return SandboxSession(
            repo_path=self.repo_path,
            sandbox_path=str(sandbox_dir),
            branch_name=branch,
            base_ref=base_ref,
        )

    def apply_unified_diff(self, *, sandbox_path: str, patch_text: str) -> None:
        if not patch_text.strip():
            return
        self._run(
            ["git", "-C", sandbox_path, "apply", "--whitespace=nowarn", "-"],
            input_text=patch_text,
        )

    def list_changed_files(self, *, sandbox_path: str) -> list[str]:
        completed = self._run(["git", "-C", sandbox_path, "status", "--porcelain", "--untracked-files=all"])
        files: list[str] = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
        return sorted(set(files))

    def run_commands(self, *, sandbox_path: str, commands: Iterable[Sequence[str]]) -> list[CommandReceipt]:
        receipts: list[CommandReceipt] = []
        for command in commands:
            completed = subprocess.run(
                list(command),
                cwd=sandbox_path,
                text=True,
                capture_output=True,
                check=False,
            )
            receipts.append(
                CommandReceipt(
                    command=tuple(command),
                    return_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            )
        return receipts

    def cleanup(self, *, sandbox_path: str) -> None:
        subprocess.run(
            ["git", "-C", self.repo_path, "worktree", "remove", "--force", sandbox_path],
            text=True,
            capture_output=True,
            check=False,
        )
        if Path(sandbox_path).exists():
            shutil.rmtree(sandbox_path, ignore_errors=True)

