from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class VoiceDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_reports_gaps_until_soak_has_enough_turns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            pack_root = repo / ".jarvis" / "voice" / "training_assets" / "prod_pack"
            clips_dir = pack_root / "clips"
            clips_dir.mkdir(parents=True, exist_ok=True)
            for idx in range(12):
                (clips_dir / f"line_{idx:03d}.wav").write_bytes(b"RIFFTEST")
            (pack_root / "metadata.json").write_text(
                json.dumps(
                    {
                        "clip_count": 12,
                        "total_duration_sec": 72.0,
                        "version": 2,
                        "created_at": "2026-04-12T21:39:33.570915",
                    }
                ),
                encoding="utf-8",
            )
            pointer = repo / ".jarvis" / "voice" / "ACTIVE_VOICE_PACK.json"
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(
                json.dumps(
                    {
                        "active_pack": str(pack_root),
                        "profile": "master_v2",
                        "updated_at": "2026-04-12T21:39:34.139292",
                    }
                ),
                encoding="utf-8",
            )

            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                run = runtime.start_voice_continuity_soak(label="diag-test")
                run_id = str(run.get("run_id") or "")
                self.assertTrue(run_id)
                runtime.record_voice_continuity_soak_turn(
                    run_id=run_id,
                    draft={
                        "text": "Short update first.",
                        "surface_id": "voice:owner",
                        "session_id": "voice-1",
                    },
                    observed_latencies_ms={
                        "phase_a_presence": 760,
                        "phase_b_first_useful": 2200,
                        "phase_c_deep_followup": 4400,
                    },
                    interrupted=True,
                    interruption_recovered=True,
                    expected_mode="equal",
                )
                diagnostics = runtime.get_voice_continuity_diagnostics(run_id=run_id, limit=50)
                self.assertEqual(diagnostics.get("run_id"), run_id)
                self.assertEqual(int(diagnostics.get("turn_count") or 0), 1)
                self.assertIn("metrics", diagnostics)
                self.assertIn("readiness", diagnostics)
                self.assertIn("insufficient_soak_turns", diagnostics.get("gaps") or [])
                self.assertFalse(bool(diagnostics.get("ready_for_strict_continuity")))
                tuning = runtime.get_voice_tuning_profile(run_id=run_id, limit=50)
                self.assertIn("profile", tuning)
                self.assertIn("profile_id", tuning)
                self.assertIn("rationale", tuning)
                self.assertEqual(str(tuning.get("run_id") or ""), run_id)
                profile = tuning.get("profile") if isinstance(tuning.get("profile"), dict) else {}
                self.assertIn("cadence_bias", profile)
                self.assertIn("annunciation_bias", profile)
                baseline_revision = str((tuning.get("overrides") or {}).get("revision") or "")
                self.assertTrue(baseline_revision)

                updated_overrides = runtime.update_voice_tuning_overrides(
                    patch={
                        "strict_mode_required": True,
                        "latency_tier": "low",
                        "speed_max": 0.96,
                    },
                    actor="test",
                )
                self.assertIn("revision", updated_overrides)
                self.assertNotEqual(str(updated_overrides.get("revision") or ""), baseline_revision)

                tuned = runtime.get_voice_tuning_profile(run_id=run_id, limit=50)
                profile = tuned.get("profile") if isinstance(tuned.get("profile"), dict) else {}
                # strict mode required + not enough turns forces balanced even if override asks for low.
                self.assertEqual(profile.get("latency_tier"), "balanced")
                self.assertLessEqual(float(profile.get("speed_max") or 99.0), 0.96)
                self.assertIn("strict_mode_required", tuned.get("override_applied") or [])
                events = runtime.list_voice_tuning_override_events(limit=10)
                self.assertGreaterEqual(len(events), 1)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
