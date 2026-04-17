from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.approval_packet import RankedCandidate
from jarvis.execution_service import ApprovalExecutionService
from jarvis.preflight import PreflightRunner


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


class ExecutionServiceTests(unittest.TestCase):
    def test_builds_manual_review_packet_for_protected_change(self) -> None:
        repo_dir = tempfile.mkdtemp(prefix="jarvis-repo-")
        worktrees_dir = tempfile.mkdtemp(prefix="jarvis-worktrees-")
        try:
            _init_repo(repo_dir)

            Path(repo_dir, "service.py").write_text("value = 2\n", encoding="utf-8")
            Path(repo_dir, "ui", "zenith_ui.txt").write_text("screen=updated\n", encoding="utf-8")
            patch = _git(repo_dir, "diff")
            _git(repo_dir, "checkout", "--", "service.py", "ui/zenith_ui.txt")

            service = ApprovalExecutionService(
                repo_path=repo_dir,
                worktrees_root=worktrees_dir,
                protected_prefixes=("ui/",),
                preflight_runner=PreflightRunner(timeout_seconds=5.0),
            )
            prepared = service.prepare_protected_step(
                approval_id="appr-5",
                plan_id="plan-5",
                step_id="step-ui",
                permission_class="P2",
                reason="CI failure plus delta indicates service and protected UI changes.",
                repo_id="zenith",
                branch="feature/fix-ui",
                confidence=0.88,
                patch_text=patch,
                ranked_candidates=[
                    RankedCandidate(path="service.py", score=0.91, reasons=("latest delta",)),
                    RankedCandidate(path="ui/zenith_ui.txt", score=0.87, reasons=("snapshot mismatch",)),
                ],
                preflight_checks=[
                    ("compile", ["python3", "-m", "py_compile", "service.py"]),
                ],
            )

            self.assertTrue(prepared.preflight_report.passed)
            self.assertEqual(prepared.packet.recommended_decision, "manual-review")
            self.assertIn("ui/zenith_ui.txt", prepared.touched_files)
            self.assertEqual(
                Path(prepared.sandbox.sandbox_path, "ui", "zenith_ui.txt").read_text(encoding="utf-8"),
                "screen=updated\n",
            )

            rendered = prepared.packet.to_markdown()
            self.assertIn("Prepared in isolated git worktree.", rendered)
            self.assertIn("compile", rendered)

            service.cleanup(prepared)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)
            shutil.rmtree(worktrees_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

