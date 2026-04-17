from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.git_native import GitNativeRepoConnector


def _run(cwd: Path, *args: str) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(args)} :: {proc.stderr}")
    return proc.stdout.strip()


class GitNativeConnectorTests(unittest.TestCase):
    def _init_repo(self, root: Path) -> None:
        _run(root, "git", "init")
        _run(root, "git", "config", "user.email", "test@example.com")
        _run(root, "git", "config", "user.name", "Test User")
        (root / "service.py").write_text("print('hello')\n", encoding="utf-8")
        _run(root, "git", "add", ".")
        _run(root, "git", "commit", "-m", "initial")

    def test_emits_git_delta_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self._init_repo(repo)
            connector = GitNativeRepoConnector(repo, emit_on_initial_scan=False, project="zenith")

            first = connector.poll(None)
            self.assertEqual(first.events, [])

            (repo / "service.py").write_text("print('hello world')\n", encoding="utf-8")
            _run(repo, "git", "add", "service.py")
            _run(repo, "git", "commit", "-m", "change")

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 1)
            payload = second.events[0].payload
            self.assertEqual(second.events[0].source_type, "repo.git_delta")
            self.assertIn("branch", payload)
            self.assertIn("base_branch", payload)
            self.assertIn("head_sha", payload)
            self.assertIn("merge_base", payload)
            self.assertIn("commit_range", payload)
            self.assertIn("commits", payload)
            self.assertIn("changed_files", payload)
            self.assertIn("dirty_files", payload)
            self.assertIn("pr_candidate", payload)

    def test_detects_dirty_working_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self._init_repo(repo)
            connector = GitNativeRepoConnector(repo, emit_on_initial_scan=False, project="zenith")
            baseline = connector.poll(None)

            (repo / "ui").mkdir()
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            dirty = connector.poll(baseline.cursor)
            self.assertEqual(len(dirty.events), 1)
            payload = dirty.events[0].payload
            self.assertIn("ui/zenith_ui.txt", payload["dirty_files"])
            self.assertTrue(payload["protected_ui_changed"])


if __name__ == "__main__":
    unittest.main()

