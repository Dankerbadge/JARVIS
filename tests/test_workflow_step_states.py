from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime
from jarvis.security import ActionClass
from jarvis.workflows import StepState


class WorkflowStepStateTests(unittest.TestCase):
    def test_step_attempts_record_queue_run_and_success(self) -> None:
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
                attempts = runtime.list_plan_step_attempts(plan_id=plan_id, limit=500)
                states = {str(item.get("step_state")) for item in attempts}

                self.assertIn(StepState.QUEUED.value, states)
                self.assertIn(StepState.RUNNING.value, states)
                self.assertIn(StepState.SUCCEEDED.value, states)
                first_step = plan.steps[0]
                latest = runtime.plan_repo.get_latest_step_attempt(
                    plan_id=plan_id,
                    step_id=first_step.step_id,
                )
                self.assertIsNotNone(latest)
                self.assertEqual(str((latest or {}).get("step_state")), StepState.SUCCEEDED.value)
                timeline = runtime.plan_repo.export_step_transition_timeline(
                    plan_id=plan_id,
                    step_id=first_step.step_id,
                )
                self.assertGreaterEqual(len(timeline), 2)
                first_timeline_state = str(timeline[0].get("step_state"))
                self.assertEqual(first_timeline_state, StepState.QUEUED.value)
            finally:
                runtime.close()

    def test_step_attempts_record_blocked_when_missing_approval(self) -> None:
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
                ingest = runtime.ingest_event(
                    source="github",
                    source_type="ci",
                    payload={"project": "alpha", "status": "failed", "deadline_hours": 12},
                )
                plan_id = runtime.plan(ingest["triggers"])[0]
                execution = runtime.run(plan_id, dry_run=True, approvals={})
                self.assertTrue(any(item.get("status") == "awaiting_approval" for item in execution))

                attempts = runtime.list_plan_step_attempts(plan_id=plan_id, limit=200)
                states = {str(item.get("step_state")) for item in attempts}
                self.assertIn(StepState.BLOCKED.value, states)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
