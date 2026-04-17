from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class PushbackCalibrationLoopTests(unittest.TestCase):
    def test_pushback_override_outcome_calibration_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                pushback = runtime.record_pushback(
                    domain="personal",
                    recommendation="Take a 15-minute break before continuing.",
                    severity="high",
                    rationale={"signal": "emotional_flooding"},
                )
                self.assertEqual(pushback.get("status"), "open")

                override = runtime.record_override(
                    pushback_id=str(pushback.get("pushback_id") or ""),
                    operator_action="continue_for_30_minutes",
                    rationale={"reason": "hard deadline"},
                )
                self.assertEqual(override.get("pushback_id"), pushback.get("pushback_id"))

                review = runtime.record_pushback_outcome_review(
                    pushback_id=str(pushback.get("pushback_id") or ""),
                    override_id=str(override.get("override_id") or ""),
                    outcome="mixed",
                    impact_score=0.4,
                    notes={"result": "completed task but elevated fatigue"},
                )
                self.assertEqual(review.get("pushback_id"), pushback.get("pushback_id"))

                delta = runtime.record_pushback_calibration_delta(
                    domain="personal",
                    direction="increase",
                    magnitude=0.2,
                    reason="high fatigue observed after override",
                    source_review_id=str(review.get("review_id") or ""),
                )
                self.assertEqual(delta.get("domain"), "personal")

                updated = runtime.pushback_calibration.get_pushback(str(pushback.get("pushback_id") or "")) or {}
                self.assertEqual(updated.get("status"), "reviewed")

                recent = runtime.list_pushback_calibration(limit=10)
                self.assertTrue(recent.get("pushbacks"))
                self.assertTrue(recent.get("overrides"))
                self.assertTrue(recent.get("reviews"))
                self.assertTrue(recent.get("calibration_deltas"))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
