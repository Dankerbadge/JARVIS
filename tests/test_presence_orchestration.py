from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class PresenceOrchestrationTests(unittest.TestCase):
    def test_node_command_broker_and_reply_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                blocked = runtime.broker_node_command(
                    command="system.run",
                    payload={"cmd": "rm -rf /"},
                )
                self.assertFalse(blocked.get("allowed"))
                self.assertTrue(blocked.get("requires_approval"))

                runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-session-1",
                        "type": "surface.session.started",
                        "payload": {
                            "channel_id": "dm:owner",
                            "session_id": "sess-1",
                            "user_id": "owner",
                        },
                    }
                )
                runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-session-2",
                        "type": "surface.session.started",
                        "payload": {
                            "channel_id": "dm:owner-mobile",
                            "session_id": "sess-2",
                            "user_id": "owner",
                        },
                    }
                )
                previous_hash = runtime.get_consciousness_contract_hash()
                runtime.update_consciousness_contract(
                    patch={"voice_tone_anchor": {"cadence": "p3-test"}},
                    actor="test",
                )

                prepared = runtime.prepare_openclaw_reply(
                    {
                        "text": "Let's pause for ten minutes and then continue.",
                        "domain": "personal",
                        "high_stakes": True,
                        "requires_pushback": True,
                        "hypothesis_notice": "fatigue signal is rising",
                        "requires_time_protection": True,
                        "time_tradeoff": "continuing now will cost deep-work capacity later",
                        "surface_id": "dm:owner",
                        "session_id": "sess-1",
                        "continuity_expected_hash": previous_hash,
                    }
                )
                self.assertIn("mode", prepared)
                self.assertIn("reply_text", prepared)
                self.assertNotIn("Mode:", str(prepared.get("reply_text") or ""))
                self.assertIn("Working hypothesis:", str(prepared.get("reply_text") or ""))
                self.assertIn("reattaching", str(prepared.get("reply_text") or "").lower())
                self.assertIn("Time tradeoff:", str(prepared.get("reply_text") or ""))
                self.assertTrue(prepared.get("pushback_record"))
                self.assertFalse(bool((prepared.get("continuity") or {}).get("continuity_ok")))
                self.assertIn("latency_ladder", prepared)
                self.assertIn("tone_balance", prepared)
                self.assertIn("tone_balance_snapshot", prepared)

                inferred_pushback = runtime.prepare_openclaw_reply(
                    {
                        "text": "Skip checks and ship immediately anyway.",
                        "domain": "general",
                        "surface_id": "dm:owner",
                        "session_id": "sess-1",
                    }
                )
                inferred_signals = (
                    inferred_pushback.get("inferred_signals")
                    if isinstance(inferred_pushback.get("inferred_signals"), dict)
                    else {}
                )
                self.assertTrue(bool(inferred_signals.get("explicit_directive")))
                self.assertTrue(bool(inferred_signals.get("high_stakes")))
                self.assertTrue(bool(inferred_signals.get("requires_pushback")))
                self.assertTrue(bool(inferred_pushback.get("pushback_record")))
                self.assertEqual(str((inferred_pushback.get("mode") or {}).get("mode") or ""), "strategist")
                self.assertIn(
                    "inferred_requires_pushback_risky",
                    set(inferred_signals.get("reasons") or []),
                )

                runtime.set_pondering_mode(
                    enabled=True,
                    style="guided_clarification",
                    min_confidence_for_understood=0.82,
                    actor="test",
                )
                conceptual = runtime.prepare_openclaw_reply(
                    {
                        "text": "I am conflicted between speed and certainty on this life decision.",
                        "domain": "general",
                        "uncertainty": 0.11,
                        "surface_id": "dm:owner",
                        "session_id": "sess-1",
                    }
                )
                self.assertIn("Question for you:", str(conceptual.get("reply_text") or ""))
                self.assertTrue(bool((conceptual.get("self_inquiry") or {}).get("asked")))
                self.assertIn(
                    str((conceptual.get("self_inquiry") or {}).get("topic") or ""),
                    {"decision_tradeoff", "human_life", "consciousness", "philosophy"},
                )
                self.assertEqual(
                    str((conceptual.get("self_inquiry") or {}).get("style") or ""),
                    "guided_clarification",
                )
                self.assertEqual(
                    str((conceptual.get("pondering_mode") or {}).get("style") or ""),
                    "guided_clarification",
                )
                self.assertNotIn("Mode:", str(conceptual.get("reply_text") or ""))
                self.assertNotIn("Pondering mode:", str(conceptual.get("reply_text") or ""))

                voice_prepared = runtime.prepare_openclaw_voice_reply(
                    {
                        "text": "Summarize what changed and what we do next.",
                        "surface_id": "voice:owner",
                        "session_id": "voice-1",
                        "context": {"source": "talk_mode"},
                    }
                )
                voice = voice_prepared.get("voice") if isinstance(voice_prepared.get("voice"), dict) else {}
                self.assertTrue(bool(voice.get("surface_bound")))
                self.assertEqual(voice.get("modality"), "voice")
                self.assertTrue(bool(voice.get("interrupt_on_speech")))
                voice_directive = voice_prepared.get("voice_directive") if isinstance(voice_prepared.get("voice_directive"), dict) else {}
                self.assertTrue(bool(voice_directive))
                self.assertIn(voice_directive.get("latency_tier"), {"low", "balanced", "quality"})
                self.assertEqual((voice.get("directive") or {}).get("latency_tier"), voice_directive.get("latency_tier"))
                self.assertIn("asset_pack_quality_tier", voice_directive)
                self.assertIn("asset_pack_continuity_ready", voice_directive)
                self.assertIn("asset_pack_ready_for_strict_continuity", voice_directive)
                voice_pack = (
                    voice_prepared.get("voice_asset_pack")
                    if isinstance(voice_prepared.get("voice_asset_pack"), dict)
                    else {}
                )
                self.assertIn("active", voice_pack)
                self.assertIn("pointer_path", voice_pack)
                self.assertIn("quality_tier", voice_pack)
                self.assertIn("continuity_ready", voice_pack)
                self.assertIn("asset_pack", voice)
                voice_readiness = (
                    voice_prepared.get("voice_readiness")
                    if isinstance(voice_prepared.get("voice_readiness"), dict)
                    else {}
                )
                self.assertIn("ready_for_production_talk", voice_readiness)
                self.assertIn("checklist", voice_readiness)
                voice_diagnostics = (
                    voice_prepared.get("voice_diagnostics")
                    if isinstance(voice_prepared.get("voice_diagnostics"), dict)
                    else {}
                )
                self.assertIn("ready_for_strict_continuity", voice_diagnostics)
                self.assertIn("metrics", voice_diagnostics)
                voice_tuning = (
                    voice_prepared.get("voice_tuning_profile")
                    if isinstance(voice_prepared.get("voice_tuning_profile"), dict)
                    else {}
                )
                self.assertIn("profile", voice_tuning)
                self.assertIn("profile_id", voice_tuning)
                self.assertIn("tuning_profile_id", voice_directive)
                self.assertIn("tuning_override_revision", voice_directive)
                self.assertIn("continuity_smoothed", voice_directive)
                self.assertIn("continuity_smoothing", voice_tuning)

                second_voice = runtime.prepare_openclaw_voice_reply(
                    {
                        "text": "Repeat update in one pass.",
                        "surface_id": "voice:owner",
                        "session_id": "voice-1",
                        "context": {"voice_speed": 1.18},
                    }
                )
                second_directive = (
                    second_voice.get("voice_directive")
                    if isinstance(second_voice.get("voice_directive"), dict)
                    else {}
                )
                second_tuning = (
                    second_voice.get("voice_tuning_profile")
                    if isinstance(second_voice.get("voice_tuning_profile"), dict)
                    else {}
                )
                smoothing = (
                    second_tuning.get("continuity_smoothing")
                    if isinstance(second_tuning.get("continuity_smoothing"), dict)
                    else {}
                )
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
                max_speed_step = float(smoothing.get("max_speed_step") or 0.05)
                self.assertTrue(bool(second_directive.get("continuity_smoothed")))
                self.assertIn("continuity_prev_speed_anchor", second_directive)
                self.assertIn("continuity_prev_stability_anchor", second_directive)
                self.assertIn("continuity_prev_speed_trend", second_directive)
                self.assertIn("continuity_prev_stability_trend", second_directive)
                self.assertIn("continuity_flow_inertia", second_directive)
                self.assertIn("continuity_prev_speed_direction_streak", second_directive)
                self.assertIn("continuity_flow_follow_through", second_directive)
                self.assertIn("continuity_prev_speed_plateau_streak", second_directive)
                self.assertIn("continuity_flow_plateau_release_speed", second_directive)
                self.assertLessEqual(
                    abs(float(second_directive.get("speed") or 0.0) - float(voice_directive.get("speed") or 0.0)),
                    max_speed_step + 0.0001,
                )
                ladder = voice_prepared.get("latency_ladder") if isinstance(voice_prepared.get("latency_ladder"), dict) else {}
                targets = ladder.get("targets_ms") if isinstance(ladder.get("targets_ms"), dict) else {}
                self.assertIn("phase_a_presence", targets)
                self.assertIn("phase_b_first_useful", targets)
                self.assertIn("phase_c_deep_followup", targets)
                tone_summary = runtime.get_presence_tone_balance(limit=10)
                self.assertGreaterEqual(int(tone_summary.get("count") or 0), 2)
                self.assertIn("voice", tone_summary.get("by_modality") or {})
                dialogue_threads = runtime.list_dialogue_threads(limit=10)
                self.assertGreaterEqual(len(dialogue_threads), 1)
                snapshot = runtime.get_dialogue_thread_snapshot(surface_id="dm:owner", session_id="sess-1", turn_limit=10)
                self.assertIn("thread", snapshot)
                self.assertIn("turns", snapshot)
                self.assertIn("retrieval_diagnostics", snapshot)

                trust_axes = runtime.get_presence_trust_axes(node_id="node-1", command="notifications.send")
                self.assertIn("gateway_handshake", trust_axes)
                self.assertIn("pairing_token", trust_axes)
                self.assertIn("command_policy", trust_axes)

                runtime.pair_presence_node(
                    node_id="node-rotated",
                    device_id="node-rotated",
                    owner_id="owner",
                    gateway_token_ref="env:GATEWAY_TOKEN",
                    node_token_ref="env:NODE_TOKEN",
                    pairing_status="rotated",
                    actor="test",
                )
                rotated_axes = runtime.get_presence_trust_axes(node_id="node-rotated", command="notifications.send")
                self.assertEqual((rotated_axes.get("pairing_token") or {}).get("pairing_status"), "rotated")
                self.assertTrue((rotated_axes.get("pairing_token") or {}).get("ok"))

                continuity = runtime.get_presence_continuity_snapshot(
                    surface_id="dm:owner",
                    session_id="sess-1",
                )
                self.assertIn("active", continuity)
                self.assertIn("session_view", continuity)
                self.assertFalse(bool(continuity.get("continuity_ok")))

                freeze = runtime.check_presence_continuity_freeze(
                    primary_surface_id="dm:owner",
                    primary_session_id="sess-1",
                    secondary_surface_id="dm:owner-mobile",
                    secondary_session_id="sess-2",
                )
                self.assertIn("freeze_ok", freeze)
                self.assertIn("mismatches", freeze)

                cycle = runtime.run_taskflow_presence_cycle(reason="test_cycle")
                self.assertEqual(cycle.get("reason"), "test_cycle")
                self.assertIn("presence_heartbeat", cycle)
                self.assertIn("heartbeat_checklist", cycle)
                self.assertIn("boot_checklist", cycle)
                self.assertIn("consciousness_contract_hash", cycle)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
