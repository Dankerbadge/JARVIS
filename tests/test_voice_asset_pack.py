from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class VoiceAssetPackTests(unittest.TestCase):
    def test_runtime_resolves_and_surfaces_active_voice_pack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            pack_root = repo / ".jarvis" / "voice" / "training_assets" / "test_pack"
            clips_dir = pack_root / "clips"
            clips_dir.mkdir(parents=True, exist_ok=True)
            (clips_dir / "line_001.wav").write_bytes(b"RIFFTEST")
            (pack_root / "playlist.m3u").write_text("clips/line_001.wav\n", encoding="utf-8")
            (pack_root / "preview_reel.wav").write_bytes(b"RIFFPREVIEW")
            (pack_root / "metadata.json").write_text(
                json.dumps(
                    {
                        "clip_count": 1,
                        "total_duration_sec": 3.25,
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
                        "export_zip": str(repo / "exports" / "voice_pack.zip"),
                        "profile": "master_v2",
                        "updated_at": "2026-04-12T21:39:34.139292",
                    }
                ),
                encoding="utf-8",
            )

            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                resolved_pack = runtime.get_active_voice_pack()
                self.assertTrue(bool(resolved_pack.get("active")))
                self.assertEqual(resolved_pack.get("pack_name"), "test_pack")
                self.assertEqual(resolved_pack.get("profile"), "master_v2")
                self.assertEqual(resolved_pack.get("clip_count"), 1)
                self.assertEqual(resolved_pack.get("clip_file_count"), 1)
                self.assertEqual(resolved_pack.get("total_duration_sec"), 3.25)
                self.assertTrue(bool(resolved_pack.get("pack_id")))
                self.assertEqual(resolved_pack.get("quality_tier"), "seed")
                self.assertFalse(bool(resolved_pack.get("continuity_ready")))
                self.assertIn("continuity_coverage_low", resolved_pack.get("issues") or [])
                self.assertIn("clip_quality", resolved_pack)
                readiness = runtime.get_voice_readiness_report()
                self.assertFalse(bool(readiness.get("ready_for_production_talk")))
                self.assertIn("quality_tier", readiness)
                self.assertIn("recommendations", readiness)
                self.assertIn("checklist", readiness)
                self.assertIn("clarity_quality", readiness)
                self.assertIn("cadence_quality", readiness)
                self.assertIn("annunciation_quality", readiness)
                diagnostics = runtime.get_voice_continuity_diagnostics()
                self.assertFalse(bool(diagnostics.get("ready_for_strict_continuity")))
                self.assertIn("gaps", diagnostics)
                self.assertIn("metrics", diagnostics)
                tuning = runtime.get_voice_tuning_profile()
                self.assertIn("profile", tuning)
                self.assertIn("profile_id", tuning)
                self.assertIn("confidence", tuning)
                self.assertIn("override_revision", tuning)
                self.assertIn("continuity_smoothing", tuning)
                self.assertIn("clip_quality_score", tuning)
                self.assertIn("actor_profile_active", tuning)
                self.assertIn("movie_match_score", tuning)
                self.assertIn("cadence_score", tuning)
                self.assertIn("annunciation_score", tuning)
                smoothing = tuning.get("continuity_smoothing") if isinstance(tuning.get("continuity_smoothing"), dict) else {}
                self.assertIn("jitter_deadband_speed", smoothing)
                self.assertIn("jitter_deadband_stability", smoothing)
                self.assertIn("history_anchor_weight", smoothing)
                self.assertIn("smooth_alpha_speed", smoothing)
                self.assertIn("smooth_alpha_stability", smoothing)
                self.assertIn("speed_upward_step_ratio", smoothing)
                self.assertIn("stability_upward_step_ratio", smoothing)
                self.assertIn("flow_inertia", smoothing)
                self.assertIn("flow_oscillation_guard", smoothing)
                self.assertIn("flow_release_speed_ratio", smoothing)
                self.assertIn("flow_release_stability_ratio", smoothing)
                self.assertIn("flow_follow_through", smoothing)
                self.assertIn("flow_plateau_release_speed", smoothing)
                self.assertIn("flow_plateau_release_stability", smoothing)

                prepared = runtime.prepare_openclaw_voice_reply(
                    {
                        "text": "Status check.",
                        "surface_id": "voice:owner",
                        "session_id": "voice-1",
                    }
                )
                self.assertIn("voice_asset_pack", prepared)
                self.assertIn("voice_readiness", prepared)
                self.assertIn("voice_diagnostics", prepared)
                self.assertIn("voice_tuning_profile", prepared)
                pack_from_reply = prepared.get("voice_asset_pack") or {}
                self.assertTrue(bool(pack_from_reply.get("active")))
                self.assertEqual(pack_from_reply.get("pack_id"), resolved_pack.get("pack_id"))
                self.assertEqual(
                    ((prepared.get("voice") or {}).get("asset_pack") or {}).get("pack_id"),
                    resolved_pack.get("pack_id"),
                )
                self.assertEqual(
                    ((prepared.get("voice") or {}).get("asset_pack") or {}).get("quality_tier"),
                    resolved_pack.get("quality_tier"),
                )
                self.assertEqual(
                    (prepared.get("voice_directive") or {}).get("asset_pack_id"),
                    resolved_pack.get("pack_id"),
                )
                self.assertEqual(
                    (prepared.get("voice_directive") or {}).get("asset_pack_quality_tier"),
                    resolved_pack.get("quality_tier"),
                )
                self.assertEqual(
                    (prepared.get("voice_directive") or {}).get("asset_pack_continuity_ready"),
                    False,
                )
                self.assertEqual(
                    (prepared.get("voice_directive") or {}).get("asset_pack_ready_for_strict_continuity"),
                    False,
                )
                self.assertTrue(bool((prepared.get("voice_directive") or {}).get("tuning_profile_id")))
                self.assertIn("continuity_smoothed", prepared.get("voice_directive") or {})
                self.assertIn("continuity_prev_speed_anchor", prepared.get("voice_directive") or {})
                self.assertIn("continuity_prev_speed_trend", prepared.get("voice_directive") or {})
                self.assertIn("continuity_flow_inertia", prepared.get("voice_directive") or {})
                self.assertIn("continuity_prev_speed_direction_sign", prepared.get("voice_directive") or {})
                self.assertIn("continuity_flow_follow_through", prepared.get("voice_directive") or {})
                self.assertIn("continuity_prev_speed_plateau_streak", prepared.get("voice_directive") or {})
                self.assertIn("continuity_flow_plateau_release_speed", prepared.get("voice_directive") or {})
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
