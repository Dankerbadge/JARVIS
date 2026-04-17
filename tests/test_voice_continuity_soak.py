from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class VoiceContinuitySoakTests(unittest.TestCase):
    def test_start_record_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                run = runtime.start_voice_continuity_soak(label="m19-soak")
                self.assertTrue(str(run.get("run_id") or "").strip())

                turn_payload = runtime.record_voice_continuity_soak_turn(
                    run_id=str(run.get("run_id")),
                    draft={
                        "text": "Give me the short version first.",
                        "surface_id": "voice:owner",
                        "session_id": "voice-soak-1",
                        "high_stakes": True,
                        "uncertainty": 0.62,
                        "hypothesis_notice": "possible focus drift",
                    },
                    observed_latencies_ms={
                        "phase_a_presence": 850,
                        "phase_b_first_useful": 2500,
                        "phase_c_deep_followup": 4700,
                    },
                    interrupted=True,
                    interruption_recovered=True,
                    expected_mode="strategist",
                    pushback_outcome="accepted",
                )
                self.assertIn("turn", turn_payload)
                self.assertIn("prepared_reply", turn_payload)
                turn = turn_payload.get("turn") or {}
                self.assertEqual(turn.get("modality"), "voice")
                self.assertTrue(bool(turn.get("interrupted")))

                report = runtime.get_voice_continuity_soak_report(run_id=str(run.get("run_id")), limit=20)
                self.assertEqual(report.get("turn_count"), 1)
                axes = report.get("axes") if isinstance(report.get("axes"), dict) else {}
                self.assertIn("continuity", axes)
                self.assertIn("latency_ladder", axes)
                self.assertIn("interruption_recovery", axes)
                self.assertIn("tone_balance", axes)
                self.assertIn("pushback", axes)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
