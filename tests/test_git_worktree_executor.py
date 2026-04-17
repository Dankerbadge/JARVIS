from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.executors.git_worktree import GitWorktreeExecutor


def _git(cwd: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _init_repo(repo_path: str) -> None:
    _git(repo_path, "init")
    _git(repo_path, "config", "user.email", "jarvis@example.com")
    _git(repo_path, "config", "user.name", "JARVIS")
    Path(repo_path, "service.py").write_text("value = 1\n", encoding="utf-8")
    Path(repo_path, "ui").mkdir()
    Path(repo_path, "ui", "zenith_ui.txt").write_text("screen=baseline\n", encoding="utf-8")
    _git(repo_path, "add", ".")
    _git(repo_path, "commit", "-m", "initial")


class GitWorktreeExecutorTests(unittest.TestCase):
    def test_executor_applies_patch_and_lists_changed_files(self) -> None:
        repo_dir = tempfile.mkdtemp(prefix="jarvis-repo-")
        worktrees_dir = tempfile.mkdtemp(prefix="jarvis-worktrees-")
        try:
            _init_repo(repo_dir)

            Path(repo_dir, "service.py").write_text("value = 2\n", encoding="utf-8")
            patch = _git(repo_dir, "diff", "--", "service.py")
            _git(repo_dir, "checkout", "--", "service.py")

            executor = GitWorktreeExecutor(repo_path=repo_dir, worktrees_root=worktrees_dir)
            sandbox = executor.create_sandbox(plan_id="plan-123")
            executor.apply_unified_diff(sandbox_path=sandbox.sandbox_path, patch_text=patch)
            changed_files = executor.list_changed_files(sandbox_path=sandbox.sandbox_path)

            self.assertEqual(changed_files, ["service.py"])
            self.assertEqual(
                Path(sandbox.sandbox_path, "service.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )

            executor.cleanup(sandbox_path=sandbox.sandbox_path)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)
            shutil.rmtree(worktrees_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

