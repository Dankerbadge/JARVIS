from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


def _git(cwd: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


class RuntimePublicationTests(unittest.TestCase):
    def _init_repo_with_remote(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        remote = root / "origin.git"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text("def render():\n    return 'TODO_ZENITH'\n", encoding="utf-8")
        _git(str(root), "init", str(repo))
        _git(str(repo), "config", "user.email", "jarvis@example.com")
        _git(str(repo), "config", "user.name", "JARVIS")
        _git(str(repo), "add", ".")
        _git(str(repo), "commit", "-m", "initial")
        _git(str(repo), "branch", "-M", "main")
        _git(str(root), "init", "--bare", str(remote))
        _git(str(repo), "remote", "add", "origin", str(remote))
        _git(str(repo), "push", "-u", "origin", "main")
        return repo, remote

    def test_publishes_approved_step_and_stores_pr_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, remote = self._init_repo_with_remote(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                plan = runtime.zenith.propose_git_delta_plan(
                    {
                        "project": "zenith",
                        "repo_id": "zenith",
                        "branch": "feature/ci-fix",
                        "base_branch": "main",
                        "head_sha": "deadbeef",
                        "changed_files": ["service.py", "ui/zenith_ui.txt"],
                        "dirty_files": [],
                        "protected_ui_changed": True,
                        "pr_candidate": True,
                    }
                )
                assert plan is not None
                runtime.plan_repo.save_plan(plan)
                step = next(step for step in plan.steps if step.action_class == "P2")

                prepared = runtime.preflight_plan(plan.plan_id)
                self.assertTrue(prepared)
                approval_id = prepared[0]["approval_id"]
                runtime.security.approve(approval_id, approved_by="tester")

                receipt = runtime.publish_approved_step(
                    plan.plan_id,
                    step.step_id,
                    remote_name="origin",
                    base_branch="main",
                    draft=True,
                )

                self.assertEqual(receipt["base_branch"], "main")
                self.assertTrue(receipt["push"]["pushed"])
                self.assertIn("origin.git", receipt["remote_url"])
                self.assertTrue(receipt["pr_payload"]["draft"])
                self.assertIn("Summary", receipt["pr_payload"]["body_markdown"])

                stored = runtime.security.get_publication_receipt(approval_id)
                self.assertIsNotNone(stored)
                self.assertEqual(stored["pr_payload"]["base_branch"], "main")
                payload = runtime.get_pr_payload(plan.plan_id, step.step_id)
                self.assertIsNotNone(payload)
                self.assertEqual(payload["head_branch"], receipt["head_branch"])

                refs = _git(str(repo), "ls-remote", "--heads", "origin", receipt["head_branch"])
                self.assertIn(receipt["head_branch"], refs)
            finally:
                runtime.close()
                shutil.rmtree(remote, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
