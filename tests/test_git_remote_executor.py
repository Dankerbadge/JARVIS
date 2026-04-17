from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.executors.git_remote import GitRemoteExecutor
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


class GitRemoteExecutorTests(unittest.TestCase):
    def test_commit_and_push_branch_to_origin(self) -> None:
        repo_dir = tempfile.mkdtemp(prefix="jarvis-remote-repo-")
        worktrees_dir = tempfile.mkdtemp(prefix="jarvis-remote-worktrees-")
        remote_dir = tempfile.mkdtemp(prefix="jarvis-remote-origin-")
        try:
            _git(repo_dir, "init")
            _git(repo_dir, "config", "user.email", "jarvis@example.com")
            _git(repo_dir, "config", "user.name", "JARVIS")
            Path(repo_dir, "service.py").write_text("value = 1\n", encoding="utf-8")
            _git(repo_dir, "add", ".")
            _git(repo_dir, "commit", "-m", "initial")
            _git(repo_dir, "branch", "-M", "main")

            bare_remote = Path(remote_dir, "origin.git")
            _git(remote_dir, "init", "--bare", str(bare_remote))
            _git(repo_dir, "remote", "add", "origin", str(bare_remote))
            _git(repo_dir, "push", "-u", "origin", "main")

            worktree = GitWorktreeExecutor(repo_path=repo_dir, worktrees_root=worktrees_dir)
            sandbox = worktree.create_sandbox(plan_id="plan_publish", base_ref="main")
            Path(sandbox.sandbox_path, "service.py").write_text("value = 2\n", encoding="utf-8")

            remote = GitRemoteExecutor(repo_path=repo_dir)
            commit = remote.commit_all(
                sandbox_path=sandbox.sandbox_path,
                message="jarvis: publish test [service.py]",
            )
            push = remote.push_branch(
                sandbox_path=sandbox.sandbox_path,
                remote_name="origin",
                branch_name=sandbox.branch_name,
            )

            self.assertTrue(commit.created)
            self.assertTrue(push.pushed)
            self.assertIn("origin.git", push.remote_url)
            refs = _git(repo_dir, "ls-remote", "--heads", "origin", sandbox.branch_name)
            self.assertIn(sandbox.branch_name, refs)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)
            shutil.rmtree(worktrees_dir, ignore_errors=True)
            shutil.rmtree(remote_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
