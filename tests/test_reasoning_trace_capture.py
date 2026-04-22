from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime
from jarvis.security import ActionClass


class ReasoningTraceCaptureTests(unittest.TestCase):
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

    def test_reasoning_traces_record_successful_step_execution(self) -> None:
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
                traces = runtime.list_decision_traces(plan_id=plan_id, limit=200)
                self.assertGreaterEqual(len(traces), len(plan.steps))
                self.assertTrue(any(str(item.get("status")) == "succeeded" for item in traces))
                first = traces[0]
                detail = runtime.get_decision_trace(str(first.get("trace_id")))
                self.assertIsNotNone(detail)
                self.assertTrue(bool((detail or {}).get("events")))
                self.assertTrue(bool((detail or {}).get("candidates")))
                self.assertIsNotNone((detail or {}).get("selected_action"))
            finally:
                runtime.close()

    def test_reasoning_traces_record_blocked_approval(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                ingest = runtime.ingest_event(
                    source="github",
                    source_type="ci",
                    payload={"project": "alpha", "status": "failed", "deadline_hours": 12},
                )
                plan_id = runtime.plan(ingest["triggers"])[0]
                runtime.run(plan_id, dry_run=True, approvals={})
                traces = runtime.list_decision_traces(plan_id=plan_id, limit=200)
                self.assertTrue(any(str(item.get("status")) == "blocked" for item in traces))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
