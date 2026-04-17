from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.interrupts import InterruptCandidate, InterruptPolicy
from jarvis.runtime import JarvisRuntime


class CognitionIdentityWeightingTests(unittest.TestCase):
    def test_goal_hierarchy_weights_shift_top_hypothesis_domain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.set_domain_weight(domain="academics", weight=1.45, actor="test")
                runtime.set_domain_weight(domain="zenith", weight=0.72, actor="test")
                runtime.update_personal_context(stress_level=0.8, energy_level=0.5, actor="test")

                runtime.ingest_event(
                    source="academics",
                    source_type="academic.risk_signal",
                    payload={
                        "project": "academics",
                        "domain": "academics",
                        "course_id": "CS101",
                        "term_id": "2026-spring",
                        "severity": "high",
                        "reason": "exam_in_36h",
                    },
                )
                runtime.ingest_event(
                    source="zenith",
                    source_type="ci",
                    payload={
                        "project": "zenith",
                        "domain": "zenith",
                        "severity": "high",
                        "reason": "minor_release_polish",
                    },
                )
                runtime.cognition.min_cycle_interval_seconds = 0
                cycle = runtime.run_cognition_cycle()
                self.assertEqual(cycle["status"], "ok")

                thought = runtime.list_recent_thoughts(limit=1)[0]
                top = thought["hypotheses"][0]
                self.assertEqual(top["domain_tags"][0], "academics")
                self.assertIn("goal_priority_boost", top.get("skepticism_flags", []))
                self.assertIn("user_model_snapshot", thought)
                self.assertIn("personal_context_snapshot", thought)
            finally:
                runtime.close()

    def test_interrupt_policy_respects_priority_and_stress(self) -> None:
        policy = InterruptPolicy(base_threshold=0.72)
        candidate = InterruptCandidate(
            candidate_id="cand_z",
            domain="zenith",
            reason="minor_cleanup",
            urgency_score=0.74,
            confidence=0.75,
        )
        decision = policy.evaluate(
            candidate,
            suppression_windows=[],
            active_focus_domain=None,
            goal_domain_weight=0.72,
            personal_context={"stress_level": 0.82, "energy_level": 0.55},
        )
        self.assertFalse(decision.delivered)
        self.assertEqual(decision.status, "suppressed")


if __name__ == "__main__":
    unittest.main()

