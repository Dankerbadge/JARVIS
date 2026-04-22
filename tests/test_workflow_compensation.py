from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime
from jarvis.security import ActionClass
from jarvis.workflows import StepState


class WorkflowCompensationTests(unittest.TestCase):
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

    def test_prepared_step_failure_records_compensation_and_trace(self) -> None:
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
                target_step_id: str | None = None
                target_action_name: str | None = None
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
                        if target_step_id is None:
                            target_step_id = step.step_id
                            target_action_name = step.proposed_action

                self.assertIsNotNone(target_step_id)
                self.assertIsNotNone(target_action_name)

                def _boom(payload: dict, dry_run: bool) -> dict:
                    raise RuntimeError("simulated protected step failure")

                runtime.tools[str(target_action_name)] = _boom

                with self.assertRaises(RuntimeError):
                    runtime.run(plan_id, dry_run=True, approvals=approvals)

                compensations = runtime.list_plan_step_compensations(
                    plan_id,
                    step_id=str(target_step_id),
                    limit=20,
                )
                self.assertGreaterEqual(len(compensations), 1)
                first_comp = compensations[0]
                self.assertEqual(str(first_comp.get("strategy")), "rollback_marker")

                attempts = runtime.list_plan_step_attempts(
                    plan_id,
                    step_id=str(target_step_id),
                    limit=50,
                )
                states = {str(item.get("step_state")) for item in attempts}
                self.assertIn(StepState.FAILED.value, states)
                self.assertIn(StepState.COMPENSATED.value, states)

                traces = runtime.list_decision_traces(
                    plan_id=plan_id,
                    step_id=str(target_step_id),
                    limit=20,
                )
                self.assertTrue(bool(traces))
                trace = traces[0]
                self.assertEqual(str(trace.get("status")), "compensated")
                detail = runtime.get_decision_trace(str(trace.get("trace_id")))
                self.assertIsNotNone(detail)
                events = list((detail or {}).get("events") or [])
                self.assertTrue(any(str(item.get("event_type")) == "compensation.applied" for item in events))

                timeline = runtime.replay_step_decision_timeline(
                    plan_id=plan_id,
                    step_id=str(target_step_id),
                )
                self.assertGreaterEqual(int(timeline.get("compensation_count") or 0), 1)
                timeline_events = list(timeline.get("events") or [])
                self.assertTrue(any(str(item.get("source")) == "compensation" for item in timeline_events))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
