from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


class GitNativeRepoConnector(BaseConnector):
    """Git-aware connector that emits branch and commit topology deltas."""

    def __init__(
        self,
        repo_path: str | Path,
        *,
        base_branch: str | None = None,
        name: str = "git_native_repo",
        emit_on_initial_scan: bool = False,
        project: str = "zenith",
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.base_branch = base_branch
        self.name = name
        self.emit_on_initial_scan = emit_on_initial_scan
        self.project = project
        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise FileNotFoundError(f"Repo path not found: {self.repo_path}")
        if not self._is_git_repo():
            raise ValueError(f"Not a git repository: {self.repo_path}")

    def _git(self, *args: str, check: bool = True) -> str:
        cmd = ["git", "-C", str(self.repo_path), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)} :: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _is_git_repo(self) -> bool:
        try:
            out = self._git("rev-parse", "--is-inside-work-tree")
            return out.lower() == "true"
        except RuntimeError:
            return False

    def _resolve_base_branch(self) -> str:
        if self.base_branch:
            return self.base_branch
        for candidate in ("main", "master"):
            try:
                self._git("show-ref", "--verify", f"refs/heads/{candidate}")
                return candidate
            except RuntimeError:
                continue
        current = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return current if current != "HEAD" else "main"

    def _dirty_files(self) -> list[str]:
        raw = self._git("status", "--porcelain", "--untracked-files=all", check=False)
        if not raw:
            return []
        files: list[str] = []
        for line in raw.splitlines():
            entry = line[3:].strip()
            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1]
            if entry:
                files.append(entry)
        return sorted(set(files))

    def _dirty_hash(self, dirty_files: list[str]) -> str:
        digest = hashlib.sha1()
        digest.update("\n".join(sorted(dirty_files)).encode("utf-8"))
        return digest.hexdigest()

    def _commit_list(self, commit_range: str) -> list[dict[str, Any]]:
        if not commit_range:
            return []
        raw = self._git(
            "log",
            "--max-count",
            "50",
            "--pretty=format:%H%x1f%s%x1f%ct",
            commit_range,
            check=False,
        )
        if not raw:
            return []
        commits: list[dict[str, Any]] = []
        for line in raw.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 3:
                continue
            sha, subject, epoch = parts
            commits.append(
                {
                    "sha": sha,
                    "subject": subject,
                    "epoch": int(epoch),
                }
            )
        return commits

    def _changed_files(self, commit_range: str) -> list[str]:
        if not commit_range:
            return []
        raw = self._git("diff", "--name-only", commit_range, check=False)
        if not raw:
            return []
        return sorted({line.strip() for line in raw.splitlines() if line.strip()})

    def _snapshot(self) -> dict[str, Any]:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        head_sha = self._git("rev-parse", "HEAD")
        base_branch = self._resolve_base_branch()
        merge_base = ""
        if branch != "HEAD":
            merge_base = self._git("merge-base", base_branch, "HEAD", check=False)
        if not merge_base:
            commit_range = ""
        else:
            commit_range = f"{merge_base}..{head_sha}"
        commits = self._commit_list(commit_range)
        changed_files = self._changed_files(commit_range)
        dirty_files = self._dirty_files()
        pr_candidate = branch not in {"HEAD", base_branch}
        protected_ui_changed = any(path.startswith("ui/") for path in (changed_files + dirty_files))
        return {
            "project": self.project,
            "repo_path": str(self.repo_path),
            "repo_id": str(self.repo_path),
            "branch": branch,
            "base_branch": base_branch,
            "head_sha": head_sha,
            "merge_base": merge_base,
            "commit_range": commit_range,
            "commits": commits,
            "changed_files": changed_files,
            "dirty_files": dirty_files,
            "pr_candidate": pr_candidate,
            "protected_ui_changed": protected_ui_changed,
        }

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        snapshot = self._snapshot()
        previous_head_sha = (cursor or {}).get("head_sha")
        if previous_head_sha and previous_head_sha != snapshot["head_sha"]:
            incremental_range = f"{previous_head_sha}..{snapshot['head_sha']}"
            snapshot["commit_range"] = incremental_range
            snapshot["commits"] = self._commit_list(incremental_range)
            snapshot["changed_files"] = self._changed_files(incremental_range)
        dirty_hash = self._dirty_hash(snapshot["dirty_files"])
        fingerprint = "|".join(
            [
                snapshot["branch"],
                snapshot["base_branch"],
                snapshot["head_sha"],
                snapshot["merge_base"],
                dirty_hash,
            ]
        )
        next_cursor = {"fingerprint": fingerprint, "head_sha": snapshot["head_sha"]}

        previous_fingerprint = (cursor or {}).get("fingerprint")
        if previous_fingerprint == fingerprint:
            return ConnectorPollResult(events=[], cursor=next_cursor)

        if previous_fingerprint is None and not self.emit_on_initial_scan:
            return ConnectorPollResult(events=[], cursor=next_cursor)

        event = EventEnvelope(
            source="git",
            source_type="repo.git_delta",
            payload=snapshot,
            auth_context="connector_git_read",
        )
        return ConnectorPollResult(events=[event], cursor=next_cursor)
