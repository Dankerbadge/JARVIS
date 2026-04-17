from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.models import EventEnvelope
from jarvis.reactors import ZenithCiFailureReactor
from jarvis.runtime import JarvisRuntime


class CiReactorTests(unittest.TestCase):
    def _setup_repo(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text("print('TODO_ZENITH')\n", encoding="utf-8")
        return repo

    def test_ci_failure_with_zenith_paths_creates_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._setup_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            reactor = ZenithCiFailureReactor()
            try:
                delta_event = EventEnvelope(
                    source="git",
                    source_type="repo.git_delta",
                    payload={
                        "project": "zenith",
                        "repo_id": str(repo),
                        "repo_path": str(repo),
                        "branch": "feature/a",
                        "base_branch": "main",
                        "head_sha": "abc123",
                        "merge_base": "def456",
                        "commit_range": "def456..abc123",
                        "commits": [{"sha": "abc123", "subject": "test", "epoch": 1}],
                        "changed_files": ["service.py"],
                        "dirty_files": ["ui/zenith_ui.txt"],
                        "pr_candidate": True,
                        "protected_ui_changed": True,
                    },
                )
                runtime.ingest_envelope(delta_event)

                ci_event = EventEnvelope(
                    source="ci",
                    source_type="ci.failure",
                    payload={
                        "project": "zenith",
                        "repo_id": str(repo),
                        "branch": "feature/a",
                        "head_sha": "abc123",
                        "status": "failed",
                        "job_name": "unit-tests",
                        "error_summary": "Zenith test failure",
                        "implicated_paths": ["jarvis/skills/zenith.py", "ui/zenith_ui.txt"],
                        "zenith_owned": True,
                        "protected_ui_changed": True,
                    },
                )
                ingestion_outcome = runtime.ingest_envelope(ci_event)
                plans = reactor.propose_plans(runtime, ci_event, ingestion_outcome)
                self.assertEqual(len(plans), 1)
                plan = plans[0]
                self.assertEqual(plan.intent, "triage_ci_failure_with_repo_context")
                self.assertTrue(any(step.action_class == "P2" for step in plan.steps))
            finally:
                runtime.close()

    def test_ci_failure_without_zenith_paths_skips_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._setup_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            reactor = ZenithCiFailureReactor()
            try:
                ci_event = EventEnvelope(
                    source="ci",
                    source_type="ci.failure",
                    payload={
                        "project": "zenith",
                        "repo_id": str(repo),
                        "branch": "main",
                        "head_sha": "abc123",
                        "status": "failed",
                        "implicated_paths": ["docs/readme.md"],
                        "zenith_owned": False,
                        "protected_ui_changed": False,
                    },
                )
                ingestion_outcome = runtime.ingest_envelope(ci_event)
                plans = reactor.propose_plans(runtime, ci_event, ingestion_outcome)
                self.assertEqual(plans, [])
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

