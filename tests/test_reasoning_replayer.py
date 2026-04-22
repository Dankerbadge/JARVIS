from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime
from jarvis.security import ActionClass


class ReasoningReplayerTests(unittest.TestCase):
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

    def test_replay_step_and_plan_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                ingest = runtime.ingest_event(
                    source="github",
                    source_type="ci",
                    payload={"project": "alpha", "status": "failed", "deadline_hours": 12},
                )
                plan_id = runtime.plan(ingest["triggers"])[0]
                plan = runtime.plan_repo.get_plan(plan_id)
                approvals: dict[str, str] = {}
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

                runtime.run(plan_id, dry_run=True, approvals=approvals)
                first_step_id = plan.steps[0].step_id
                step_timeline = runtime.replay_step_decision_timeline(plan_id=plan_id, step_id=first_step_id)
                self.assertEqual(str(step_timeline.get("plan_id")), plan_id)
                self.assertEqual(str(step_timeline.get("step_id")), first_step_id)
                self.assertGreaterEqual(int(step_timeline.get("attempt_count") or 0), 1)
                events = list(step_timeline.get("events") or [])
                self.assertTrue(any(str(item.get("source")) == "attempt" for item in events))
                self.assertTrue(any(str(item.get("source")) == "trace_event" for item in events))
                self.assertTrue(any(str(item.get("source")) == "candidate" for item in events))
                self.assertTrue(any(str(item.get("source")) == "selected_action" for item in events))

                plan_timeline = runtime.replay_plan_decision_timeline(plan_id=plan_id)
                self.assertEqual(str(plan_timeline.get("plan_id")), plan_id)
                self.assertGreaterEqual(int(plan_timeline.get("step_count") or 0), 1)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
