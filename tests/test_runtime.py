from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime
from jarvis.security import ActionClass


class RuntimeTests(unittest.TestCase):
    def test_planner_executor_split_with_persisted_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            db = root / "jarvis.db"
            (repo / "ui").mkdir(parents=True)
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            (repo / "service.py").write_text(
                "def x():\n    return 'TODO_ZENITH'\n",
                encoding="utf-8",
            )

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                out = runtime.ingest_event(
                    source="github",
                    source_type="ci",
                    payload={"project": "alpha", "status": "failed", "deadline_hours": 12},
                )
                self.assertTrue(out["triggers"])

                plan_ids = runtime.plan(out["triggers"])
                self.assertEqual(len(plan_ids), 1)
                plan_id = plan_ids[0]
                plan = runtime.plan_repo.get_plan(plan_id)
                self.assertGreaterEqual(len(plan.steps), 3)

                approvals = {}
                for step in plan.steps:
                    if step.action_class == ActionClass.P2.value and step.requires_approval:
                        approval_id = runtime.security.request_approval(
                            plan_id=plan_id,
                            step_id=step.step_id,
                            action_class=ActionClass.P2,
                            action_desc=step.proposed_action,
                        )
                        runtime.security.approve(approval_id, approved_by="test")
                        approvals[step.step_id] = approval_id

                execution = runtime.run(plan_id, dry_run=True, approvals=approvals)
                self.assertTrue(any(step["status"] == "ok" for step in execution))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

