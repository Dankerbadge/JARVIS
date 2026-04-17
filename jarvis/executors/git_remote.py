from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


class GitRemoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommitReceipt:
    sandbox_path: str
    branch_name: str
    commit_sha: str
    message: str
    created: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PushReceipt:
    sandbox_path: str
    remote_name: str
    remote_url: str
    branch_name: str
    head_sha: str
    upstream_ref: str
    pushed: bool
    force_with_lease: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class GitRemoteExecutor:
    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = str(Path(repo_path).resolve())

    def _run(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            raise GitRemoteError(
                f"Command failed ({completed.returncode}): {' '.join(command)}\n"
                f"stdout={completed.stdout}\n"
                f"stderr={completed.stderr}"
            )
        return completed

    def current_branch(self, *, sandbox_path: str) -> str:
        completed = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=sandbox_path)
        return completed.stdout.strip()

    def head_sha(self, *, sandbox_path: str) -> str:
        completed = self._run(["git", "rev-parse", "HEAD"], cwd=sandbox_path)
        return completed.stdout.strip()

    def remote_url(self, *, sandbox_path: str, remote_name: str = "origin") -> str:
        completed = self._run(["git", "remote", "get-url", remote_name], cwd=sandbox_path)
        return completed.stdout.strip()

    def _has_uncommitted_changes(self, *, sandbox_path: str) -> bool:
        completed = self._run(["git", "status", "--porcelain"], cwd=sandbox_path)
        return bool(completed.stdout.strip())

    def commit_all(
        self,
        *,
        sandbox_path: str,
        message: str,
        author_name: str = "JARVIS",
        author_email: str = "jarvis@example.com",
    ) -> CommitReceipt:
        branch_name = self.current_branch(sandbox_path=sandbox_path)
        if not self._has_uncommitted_changes(sandbox_path=sandbox_path):
            return CommitReceipt(
                sandbox_path=sandbox_path,
                branch_name=branch_name,
                commit_sha=self.head_sha(sandbox_path=sandbox_path),
                message=message,
                created=False,
            )

        self._run(["git", "add", "-A"], cwd=sandbox_path)
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
            }
        )
        self._run(["git", "commit", "-m", message], cwd=sandbox_path, env=env)
        return CommitReceipt(
            sandbox_path=sandbox_path,
            branch_name=branch_name,
            commit_sha=self.head_sha(sandbox_path=sandbox_path),
            message=message,
            created=True,
        )

    def push_branch(
        self,
        *,
        sandbox_path: str,
        remote_name: str = "origin",
        branch_name: str | None = None,
        force_with_lease: bool = False,
    ) -> PushReceipt:
        effective_branch = branch_name or self.current_branch(sandbox_path=sandbox_path)
        upstream_ref = f"refs/heads/{effective_branch}"
        command = ["git", "push", "--set-upstream"]
        if force_with_lease:
            command.append("--force-with-lease")
        command.extend([remote_name, f"HEAD:{upstream_ref}"])
        self._run(command, cwd=sandbox_path)
        return PushReceipt(
            sandbox_path=sandbox_path,
            remote_name=remote_name,
            remote_url=self.remote_url(sandbox_path=sandbox_path, remote_name=remote_name),
            branch_name=effective_branch,
            head_sha=self.head_sha(sandbox_path=sandbox_path),
            upstream_ref=f"{remote_name}/{effective_branch}",
            pushed=True,
            force_with_lease=force_with_lease,
        )
