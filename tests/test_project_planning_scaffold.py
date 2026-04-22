from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class ProjectPlanningScaffoldTests(unittest.TestCase):
    def _make_runtime(self, root: Path) -> JarvisRuntime:
        repo = root / "repo"
        db = root / "jarvis.db"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text(
            "def x():\n    return 'TODO_ZENITH'\n",
            encoding="utf-8",
        )
        return JarvisRuntime(db_path=db, repo_path=repo)

    def test_project_signal_ingest_and_next_action_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                project_id = "alpha"
                ci = runtime.ingest_project_signal(
                    project_id=project_id,
                    signal={"type": "ci_failed", "run_id": "run-123", "branch": "main"},
                )
                self.assertTrue(any(str(item.get("action_type")) == "fix_ci" for item in ci.get("created_actions", [])))

                pr = runtime.ingest_project_signal(
                    project_id=project_id,
                    signal={"type": "pull_request_review_changes", "pr_number": "42"},
                )
                self.assertTrue(
                    any(
                        str(item.get("action_type")) == "address_review_feedback"
                        for item in pr.get("created_actions", [])
                    )
                )

                graph = runtime.list_project_graph(project_id=project_id)
                self.assertGreaterEqual(int(graph.get("node_count") or 0), 3)
                self.assertGreaterEqual(int(graph.get("edge_count") or 0), 2)

                actions = runtime.list_project_actions(project_id=project_id, limit=20)
                self.assertGreaterEqual(len(actions), 2)

                ranked = runtime.propose_project_next_actions(project_id=project_id, limit=3)
                self.assertTrue(bool(ranked))
                self.assertIn(str(ranked[0].get("action_type")), {"fix_ci", "address_review_feedback"})
            finally:
                runtime.close()

    def test_project_milestone_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                project_id = "alpha"
                runtime.ingest_project_signal(
                    project_id=project_id,
                    signal={
                        "type": "milestone_created",
                        "milestone_id": "m1",
                        "status": "open",
                        "title": "Ship durable workflow runtime",
                    },
                )
                summary = runtime.summarize_project_milestones(project_id=project_id)
                self.assertGreaterEqual(int(summary.get("node_count") or 0), 2)
                by_kind = summary.get("by_kind") if isinstance(summary.get("by_kind"), dict) else {}
                self.assertGreaterEqual(int(by_kind.get("milestone") or 0), 1)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
