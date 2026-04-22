from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jarvis.models import PlanArtifact, PlanStep, utc_now_iso
from jarvis.workflows import PlanRepository, StepState


class WorkflowTransitionValidationTests(unittest.TestCase):
    def _basic_plan(self) -> PlanArtifact:
        step = PlanStep(
            action_class="P1",
            proposed_action="noop",
            expected_effect="none",
            rollback="none",
            payload={},
            requires_approval=False,
        )
        return PlanArtifact(
            intent="test",
            priority="medium",
            reasoning_summary="test plan",
            steps=[step],
            approval_requirements=[],
            expires_at=utc_now_iso(),
        )

    def test_attempt_numbers_are_monotonic_per_step(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "jarvis.db"
            repo = PlanRepository(db)
            try:
                plan = self._basic_plan()
                plan_id = repo.save_plan(plan)
                step_id = plan.steps[0].step_id

                repo.record_step_attempt(
                    plan_id=plan_id,
                    step_id=step_id,
                    step_state=StepState.RUNNING,
                    details={},
                )
                repo.record_step_attempt(
                    plan_id=plan_id,
                    step_id=step_id,
                    step_state=StepState.SUCCEEDED,
                    details={},
                )

                attempts = repo.list_step_attempts(plan_id=plan_id, step_id=step_id, limit=20)
                numbers = [int(item["attempt_number"]) for item in attempts]
                self.assertEqual(numbers, sorted(numbers, reverse=True))
                self.assertEqual(set(numbers), {1, 2, 3})
            finally:
                repo.close()

    def test_invalid_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "jarvis.db"
            repo = PlanRepository(db)
            try:
                plan = self._basic_plan()
                plan_id = repo.save_plan(plan)
                step_id = plan.steps[0].step_id
                with self.assertRaises(ValueError):
                    repo.record_step_attempt(
                        plan_id=plan_id,
                        step_id=step_id,
                        step_state=StepState.SUCCEEDED,
                        details={"reason": "invalid jump"},
                    )
            finally:
                repo.close()

    def test_legacy_step_attempt_schema_is_backfilled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "jarvis.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                """
                CREATE TABLE plan_step_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_state TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO plan_step_attempts (
                    attempt_id, plan_id, step_id, step_state, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "stpat_old",
                    "plan_old",
                    "step_old",
                    "running",
                    "{}",
                    utc_now_iso(),
                ),
            )
            conn.commit()
            conn.close()

            repo = PlanRepository(db)
            try:
                attempts = repo.list_step_attempts(plan_id="plan_old", step_id="step_old", limit=5)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(int(attempts[0]["attempt_number"]), 1)
            finally:
                repo.close()

    def test_step_compensation_records_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "jarvis.db"
            repo = PlanRepository(db)
            try:
                plan = self._basic_plan()
                plan_id = repo.save_plan(plan)
                step_id = plan.steps[0].step_id

                compensation_id = repo.record_step_compensation(
                    plan_id=plan_id,
                    step_id=step_id,
                    reason="synthetic failure",
                    strategy="rollback_marker",
                    details={"rollback_hint": "none"},
                )
                self.assertTrue(str(compensation_id).startswith("cmp_"))

                rows = repo.list_step_compensations(plan_id=plan_id, step_id=step_id, limit=10)
                self.assertEqual(len(rows), 1)
                self.assertEqual(str(rows[0]["strategy"]), "rollback_marker")
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
