from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib import request


class OperatorServerTests(unittest.TestCase):
    def _get_json(self, url: str) -> dict:
        with request.urlopen(url, timeout=5) as resp:  # noqa: S310 - local server
            data = resp.read().decode("utf-8")
            return json.loads(data)

    def _post_json(self, url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=5) as resp:  # noqa: S310 - local server
            return json.loads(resp.read().decode("utf-8"))

    def test_operator_surface_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "ui").mkdir(parents=True, exist_ok=True)
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            (repo / "service.py").write_text("def run():\n    return 'TODO_ZENITH'\n", encoding="utf-8")
            db_path = root / "jarvis.db"
            project_root = Path(__file__).resolve().parents[1]

            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "jarvis.cli",
                    "serve",
                    "--repo-path",
                    str(repo),
                    "--db-path",
                    str(db_path),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8876",
                ],
                cwd=str(project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                base = "http://127.0.0.1:8876"
                ready = False
                for _ in range(40):
                    try:
                        health = self._get_json(base + "/api/health")
                    except Exception:
                        time.sleep(0.1)
                        continue
                    if health.get("status") == "ok":
                        ready = True
                        break
                    time.sleep(0.1)
                self.assertTrue(ready)

                cognition_config = self._get_json(base + "/api/cognition/config")
                self.assertIn("backend", cognition_config)
                self.assertIn("model", cognition_config)
                self.assertIn("model_assisted", cognition_config)

                updated = self._post_json(
                    base + "/api/preferences/focus-mode",
                    {"domain": "academics", "actor": "test"},
                )
                self.assertEqual(updated.get("focus_mode_domain"), "academics")

                pondering_updated = self._post_json(
                    base + "/api/preferences/pondering-mode",
                    {
                        "enabled": True,
                        "style": "guided_clarification",
                        "min_confidence_for_understood": 0.83,
                        "actor": "test",
                    },
                )
                pondering_mode = pondering_updated.get("pondering_mode") if isinstance(pondering_updated.get("pondering_mode"), dict) else {}
                self.assertTrue(bool(pondering_mode.get("enabled")))
                self.assertEqual(pondering_mode.get("style"), "guided_clarification")

                prefs = self._get_json(base + "/api/preferences")
                self.assertEqual((prefs.get("preferences") or {}).get("focus_mode_domain"), "academics")
                self.assertTrue(bool(((prefs.get("preferences") or {}).get("pondering_mode") or {}).get("enabled")))
                pondering_read = self._get_json(base + "/api/preferences/pondering-mode")
                self.assertTrue(bool(pondering_read.get("enabled")))
                self.assertEqual(pondering_read.get("style"), "guided_clarification")

                identity = self._get_json(base + "/api/identity")
                self.assertIn("user_model", identity)
                self.assertIn("personal_context", identity)

                codex_created = self._post_json(
                    base + "/api/codex/tasks",
                    {
                        "text": "Figure out how to change this in the app and implement it.",
                        "surface_id": "voice:owner",
                        "session_id": "srv-voice-1",
                        "auto_execute": False,
                    },
                )
                self.assertTrue(codex_created.get("ok"))
                codex_task = codex_created.get("task") if isinstance(codex_created.get("task"), dict) else {}
                codex_task_id = str(codex_task.get("task_id") or "")
                self.assertTrue(codex_task_id)
                self.assertEqual(codex_task.get("status"), "queued")
                self.assertIn(codex_task.get("effort_tier"), {"instant", "thinking", "pro", "extended_thinking", "deep_research"})
                self.assertIn(codex_task.get("reasoning_effort"), {"low", "medium", "high", "xhigh"})

                codex_tasks = self._get_json(base + "/api/codex/tasks?status=all&limit=10")
                self.assertGreaterEqual(int(codex_tasks.get("count") or 0), 1)
                self.assertIn("summary", codex_tasks)
                self.assertIn("by_effort_tier", codex_tasks.get("summary") or {})

                codex_task_read = self._get_json(base + f"/api/codex/tasks/{codex_task_id}")
                self.assertEqual(codex_task_read.get("task_id"), codex_task_id)

                updated_context = self._post_json(
                    base + "/api/identity/context",
                    {
                        "stress_level": 0.79,
                        "energy_level": 0.41,
                        "available_focus_minutes": 35,
                        "actor": "test",
                    },
                )
                self.assertEqual(updated_context.get("available_focus_minutes"), 35)

                home = self._get_json(base + "/api/home")
                self.assertIn("priorities", home)
                self.assertIn("operator_preferences", home)
                self.assertIn("identity", home)
                self.assertIn("markets", home)
                self.assertIn("presence", home)
                self.assertEqual(
                    ((home.get("identity") or {}).get("personal_context") or {}).get("available_focus_minutes"),
                    35,
                )
                self.assertIn("voice_pack", (home.get("presence") or {}))
                self.assertIn("voice_readiness", (home.get("presence") or {}))
                self.assertIn("voice_diagnostics", (home.get("presence") or {}))
                self.assertIn("voice_tuning_profile", (home.get("presence") or {}))
                self.assertIn("voice_tuning_overrides", (home.get("presence") or {}))

                constraints = self._get_json(base + "/api/presence/constraints")
                self.assertTrue(constraints.get("single_owner_boundary"))
                self.assertEqual(constraints.get("canonical_mind"), "jarvis_core")

                paired = self._post_json(
                    base + "/api/presence/nodes/pair",
                    {
                        "node_id": "node-1",
                        "device_id": "iphone-1",
                        "owner_id": "owner-primary",
                        "gateway_token_ref": "env:OPENCLAW_GATEWAY_TOKEN",
                        "node_token_ref": "env:OPENCLAW_NODE_TOKEN_1",
                        "metadata": {"device_label": "Phone"},
                        "actor": "test",
                    },
                )
                self.assertEqual(paired.get("node_id"), "node-1")
                self.assertEqual(paired.get("pairing_status"), "paired")

                nodes = self._get_json(base + "/api/presence/nodes?status=all&limit=10")
                self.assertEqual(nodes.get("count"), 1)
                self.assertEqual((nodes.get("items") or [])[0].get("node_id"), "node-1")

                mode = self._post_json(
                    base + "/api/presence/mode",
                    {
                        "high_stakes": True,
                        "uncertainty": 0.7,
                        "context": {"source": "test"},
                    },
                )
                self.assertEqual(mode.get("mode"), "strategist")

                mode_read = self._get_json(base + "/api/presence/mode")
                self.assertIn("mode", mode_read)
                self.assertIn("recent", mode_read)

                openclaw_ingest = self._post_json(
                    base + "/api/presence/openclaw-event",
                    {
                        "event": {
                            "event_id": "evt-1",
                            "type": "node.connected",
                            "payload": {"node_id": "node-1"},
                        }
                    },
                )
                self.assertTrue(openclaw_ingest.get("ok"))
                self.assertEqual((openclaw_ingest.get("signal") or {}).get("schema_version"), "jarvis.signal.v1")

                health = self._get_json(base + "/api/presence/health")
                self.assertIn("bridge", health)
                self.assertTrue((health.get("bridge") or {}).get("connected"))
                self.assertGreaterEqual(int(health.get("node_count") or 0), 1)
                self.assertIn("gateway_loop", health)

                sessions = self._get_json(base + "/api/presence/sessions?status=all&limit=10")
                self.assertIn("count", sessions)
                self.assertGreaterEqual(int(sessions.get("count") or 0), 1)
                dialogue_threads = self._get_json(base + "/api/presence/dialogue/threads?limit=10")
                self.assertIn("count", dialogue_threads)
                self.assertGreaterEqual(int(dialogue_threads.get("count") or 0), 0)
                dialogue_retrieval = self._get_json(base + "/api/presence/dialogue/retrieval")
                self.assertIn("weights", dialogue_retrieval)
                self.assertIn("embed_rerank", dialogue_retrieval)
                self.assertIn("flag_rerank", dialogue_retrieval)

                trust_axes = self._get_json(base + "/api/presence/trust-axes?node_id=node-1&command=notifications.send")
                self.assertIn("gateway_handshake", trust_axes)
                self.assertIn("pairing_token", trust_axes)
                self.assertIn("command_policy", trust_axes)

                continuity_snapshot = self._get_json(base + "/api/presence/continuity-snapshot?surface_id=dm%3Aowner&session_id=sess-1")
                self.assertIn("active", continuity_snapshot)
                self.assertIn("session_view", continuity_snapshot)
                dialogue_snapshot = self._get_json(base + "/api/presence/dialogue/snapshot?surface_id=dm%3Aowner&session_id=sess-1")
                self.assertIn("thread", dialogue_snapshot)
                self.assertIn("turns", dialogue_snapshot)
                self.assertIn("retrieval_diagnostics", dialogue_snapshot)

                freeze_check = self._post_json(
                    base + "/api/presence/continuity-freeze-check",
                    {
                        "primary_surface_id": "dm:owner",
                        "primary_session_id": "sess-1",
                        "secondary_surface_id": "dm:owner",
                        "secondary_session_id": "sess-1",
                    },
                )
                self.assertIn("freeze_ok", freeze_check)

                gateway_status = self._get_json(base + "/api/presence/gateway-loop")
                self.assertIn("enabled", gateway_status)
                self.assertIn("configured", gateway_status)
                self.assertIn("protocol_profile_id", gateway_status)

                gateway_profile = self._get_json(base + "/api/presence/gateway-profile")
                self.assertIn("configured", gateway_profile)

                soak = self._post_json(base + "/api/presence/gateway-loop/soak", {"loops": 1})
                self.assertIn("ok", soak)

                node_soak_invalid = self._post_json(
                    base + "/api/presence/gateway-loop/node-soak",
                    {"ws_url": "not-a-ws-url"},
                )
                self.assertFalse(node_soak_invalid.get("ok"))
                self.assertEqual(node_soak_invalid.get("error"), "invalid_gateway_ws_url")

                heartbeat = self._post_json(base + "/api/presence/heartbeat", {})
                self.assertIn("heartbeat", heartbeat)
                self.assertIn("mode", heartbeat)
                self.assertIn("health", heartbeat)

                brokered = self._post_json(
                    base + "/api/presence/node-command/broker",
                    {"command": "system.run", "payload": {"cmd": "echo hi"}, "actor": "test"},
                )
                self.assertFalse(brokered.get("allowed"))
                self.assertTrue(brokered.get("requires_approval"))

                prepared_reply = self._post_json(
                    base + "/api/presence/reply/prepare",
                    {
                        "text": "Pause ten minutes before continuing.",
                        "high_stakes": True,
                        "requires_pushback": True,
                        "hypothesis_notice": "fatigue rising",
                        "surface_id": "dm:owner",
                        "session_id": "sess-1",
                    },
                )
                self.assertIn("mode", prepared_reply)
                self.assertIn("reply_text", prepared_reply)
                self.assertIn("latency_ladder", prepared_reply)
                self.assertIn("tone_balance", prepared_reply)
                self.assertIn("work_item", prepared_reply)
                self.assertIn(
                    (prepared_reply.get("work_item") or {}).get("engine_route"),
                    {"gpt", "codex"},
                )
                dialogue_threads_after = self._get_json(base + "/api/presence/dialogue/threads?limit=10")
                self.assertGreaterEqual(int(dialogue_threads_after.get("count") or 0), 1)
                first_thread = (
                    (dialogue_threads_after.get("items") or [])[0]
                    if isinstance(dialogue_threads_after.get("items"), list) and dialogue_threads_after.get("items")
                    else {}
                )
                self.assertIn("thread_id", first_thread)

                router_preview = self._post_json(
                    base + "/api/presence/router/preview",
                    {
                        "text": "Should this use codex or gpt and what tier is it?",
                        "surface_id": "dm:owner",
                    },
                )
                self.assertIn("work_item", router_preview)
                self.assertIn("intent", router_preview)
                self.assertIn("explanation", router_preview)
                self.assertEqual(((router_preview.get("work_item") or {}).get("engine_route")), "gpt")
                self.assertEqual(((router_preview.get("work_item") or {}).get("effort_tier")), "thinking")

                prepared_voice_reply = self._post_json(
                    base + "/api/presence/voice/reply/prepare",
                    {
                        "text": "Give me the short update first.",
                        "surface_id": "voice:owner",
                        "session_id": "voice-1",
                    },
                )
                self.assertTrue(bool((prepared_voice_reply.get("voice") or {}).get("surface_bound")))
                self.assertEqual((prepared_voice_reply.get("voice") or {}).get("modality"), "voice")
                self.assertIn("latency_ladder", prepared_voice_reply)
                self.assertIn("voice_directive", prepared_voice_reply)
                self.assertTrue(bool(prepared_voice_reply.get("voice_directive")))
                self.assertEqual(
                    ((prepared_voice_reply.get("voice") or {}).get("directive") or {}).get("mode"),
                    (prepared_voice_reply.get("voice_directive") or {}).get("mode"),
                )
                self.assertIn("voice_asset_pack", prepared_voice_reply)
                prepared_pack = (
                    prepared_voice_reply.get("voice_asset_pack")
                    if isinstance(prepared_voice_reply.get("voice_asset_pack"), dict)
                    else {}
                )
                self.assertIn("active", prepared_pack)
                self.assertIn("pointer_path", prepared_pack)
                self.assertIn("quality_tier", prepared_pack)
                self.assertIn("continuity_ready", prepared_pack)

                voice_pack_status = self._get_json(base + "/api/presence/voice/pack")
                self.assertIn("active", voice_pack_status)
                self.assertIn("pointer_path", voice_pack_status)
                self.assertIn("quality_tier", voice_pack_status)
                self.assertIn("continuity_ready", voice_pack_status)
                self.assertEqual(voice_pack_status.get("active"), prepared_pack.get("active"))

                voice_readiness = self._get_json(base + "/api/presence/voice/readiness")
                self.assertIn("ready_for_production_talk", voice_readiness)
                self.assertIn("confidence", voice_readiness)
                self.assertIn("checklist", voice_readiness)
                self.assertIn("pack", voice_readiness)
                self.assertIn("cadence_quality", voice_readiness)
                self.assertIn("annunciation_quality", voice_readiness)

                voice_diagnostics = self._get_json(base + "/api/presence/voice/diagnostics")
                self.assertIn("ready_for_strict_continuity", voice_diagnostics)
                self.assertIn("continuity_confidence", voice_diagnostics)
                self.assertIn("metrics", voice_diagnostics)
                self.assertIn("readiness", voice_diagnostics)

                voice_tuning = self._get_json(base + "/api/presence/voice/tuning")
                self.assertIn("profile", voice_tuning)
                self.assertIn("profile_id", voice_tuning)
                self.assertIn("confidence", voice_tuning)
                self.assertIn("continuity_smoothing", voice_tuning)
                self.assertIn("overrides", voice_tuning)
                self.assertIn("cadence_score", voice_tuning)
                self.assertIn("annunciation_score", voice_tuning)
                smoothing = (
                    voice_tuning.get("continuity_smoothing")
                    if isinstance(voice_tuning.get("continuity_smoothing"), dict)
                    else {}
                )
                self.assertIn("flow_inertia", smoothing)
                self.assertIn("flow_oscillation_guard", smoothing)
                self.assertIn("flow_follow_through", smoothing)
                self.assertIn("flow_plateau_release_speed", smoothing)
                self.assertIn("flow_plateau_release_stability", smoothing)

                voice_tuning_overrides_initial = self._get_json(base + "/api/presence/voice/tuning/overrides?events_limit=5")
                self.assertIn("overrides", voice_tuning_overrides_initial)
                self.assertIn("events", voice_tuning_overrides_initial)

                updated_overrides = self._post_json(
                    base + "/api/presence/voice/tuning/overrides",
                    {
                        "patch": {
                            "speed_bias": -0.02,
                            "prefer_stability": True,
                        },
                        "actor": "server-test",
                    },
                )
                self.assertIn("revision", updated_overrides)
                self.assertTrue(bool((updated_overrides.get("overrides") or {}).get("prefer_stability")))

                voice_tuning_overrides_after = self._get_json(base + "/api/presence/voice/tuning/overrides?events_limit=5")
                self.assertGreaterEqual(len(voice_tuning_overrides_after.get("events") or []), 1)

                reset_overrides = self._post_json(
                    base + "/api/presence/voice/tuning/overrides/reset",
                    {"actor": "server-test"},
                )
                self.assertEqual(reset_overrides.get("overrides"), {})

                tone_balance = self._get_json(base + "/api/presence/tone-balance?limit=10")
                self.assertIn("latest", tone_balance)
                self.assertIn("items", tone_balance)
                self.assertIn("by_modality", tone_balance)

                adaptive_policy = self._get_json(base + "/api/presence/adaptive-policy")
                self.assertIn("policy", adaptive_policy)
                self.assertIn("revision", adaptive_policy)

                adaptive_preview = self._post_json(
                    base + "/api/presence/adaptive-policy/calibrate",
                    {"reason": "operator_server_test", "apply": False},
                )
                self.assertTrue(adaptive_preview.get("ok"))
                self.assertFalse(bool(adaptive_preview.get("applied")))
                self.assertIn("policy_patch", adaptive_preview)

                adaptive_update = self._post_json(
                    base + "/api/presence/adaptive-policy/update",
                    {
                        "reason": "operator_server_test_patch",
                        "patch": {"tone": {"warmth_bias": 0.05}},
                    },
                )
                self.assertIn("policy", adaptive_update)

                quota_update = self._post_json(
                    base + "/api/presence/self-patch/quota",
                    {
                        "weekly_remaining_percent": 88.0,
                        "min_weekly_remaining_percent": 40.0,
                        "actor": "operator_server_test",
                    },
                )
                self.assertTrue(quota_update.get("ok"))
                quota = quota_update.get("quota") if isinstance(quota_update.get("quota"), dict) else {}
                self.assertEqual(quota.get("weekly_remaining_percent"), 88.0)

                adaptive_history = self._get_json(base + "/api/presence/adaptive-policy/history?limit=5")
                self.assertGreaterEqual(int(adaptive_history.get("count") or 0), 1)

                self_patch = self._post_json(
                    base + "/api/presence/self-patch/trigger",
                    {
                        "issue": "Patch routing fallback behavior for edge prompts.",
                        "reason": "operator_server_test_self_patch",
                        "effort_tier": "pro",
                        "auto_execute": False,
                        "project_scope": "jarvis",
                        "approval_source": "codex",
                        "change_impact": "minor",
                    },
                )
                self.assertTrue(self_patch.get("ok"))
                self_patch_task = (
                    ((self_patch.get("submission") or {}).get("task") or {})
                    if isinstance(self_patch.get("submission"), dict)
                    else {}
                )
                self.assertEqual(self_patch_task.get("status"), "queued")

                self_patch_events = self._get_json(base + "/api/presence/self-patch/events?limit=5")
                self.assertGreaterEqual(int(self_patch_events.get("count") or 0), 1)

                soak_run = self._post_json(
                    base + "/api/presence/voice/soak/start",
                    {"label": "server-test-soak"},
                )
                run_id = str(soak_run.get("run_id") or "")
                self.assertTrue(run_id)

                soak_turn = self._post_json(
                    base + "/api/presence/voice/soak/turn",
                    {
                        "run_id": run_id,
                        "draft": {
                            "text": "Short strategic read, then deeper pass.",
                            "surface_id": "voice:owner",
                            "session_id": "voice-soak-operator-1",
                            "high_stakes": True,
                            "uncertainty": 0.66,
                        },
                        "observed_latencies_ms": {
                            "phase_a_presence": 900,
                            "phase_b_first_useful": 2400,
                            "phase_c_deep_followup": 4900,
                        },
                        "interrupted": True,
                        "interruption_recovered": True,
                        "expected_mode": "strategist",
                        "pushback_outcome": "accepted",
                    },
                )
                self.assertIn("turn", soak_turn)
                self.assertIn("prepared_reply", soak_turn)

                soak_report = self._get_json(base + f"/api/presence/voice/soak/report?run_id={run_id}&limit=20")
                self.assertEqual(int(soak_report.get("turn_count") or 0), 1)
                self.assertIn("axes", soak_report)

                markets = self._get_json(base + "/api/markets/overview?account_id=default")
                self.assertIn("risk_posture", markets)
                self.assertIn("opportunities", markets)
                self.assertIn("abstentions", markets)
                self.assertIn("events", markets)
                self.assertIn("handoffs", markets)
                self.assertIn("outcomes", markets)
                self.assertIn("evaluation", markets)
                self.assertIn("risks", markets)

                market_handoffs = self._get_json(base + "/api/markets/handoffs?limit=10")
                self.assertIn("count", market_handoffs)
                self.assertIn("items", market_handoffs)

                market_outcomes = self._get_json(base + "/api/markets/outcomes?limit=10")
                self.assertIn("count", market_outcomes)
                self.assertIn("items", market_outcomes)
                self.assertIn("summary", market_outcomes)

                ingest = self._post_json(
                    base + "/api/ingest",
                    {
                        "schema_version": "jarvis.signal.v1",
                        "kind": "email.thread",
                        "payload": {
                            "source_type": "academic.assignment_due",
                            "course_id": "CS101",
                            "term_id": "current_term",
                            "title": "Essay due",
                            "due_at": "2026-04-15T16:00:00+00:00",
                        },
                        "provenance": {
                            "source_kind": "provider",
                            "provider": "gmail",
                            "source_id": "gmail:msg:abc",
                        },
                    },
                )
                self.assertTrue(ingest.get("ok"))
                self.assertFalse(ingest.get("duplicate"))

                duplicate = self._post_json(
                    base + "/api/ingest/signal",
                    {
                        "schema_version": "jarvis.signal.v1",
                        "kind": "email.thread",
                        "payload": {
                            "source_type": "academic.assignment_due",
                            "course_id": "CS101",
                            "term_id": "current_term",
                            "title": "Essay due",
                            "due_at": "2026-04-15T16:00:00+00:00",
                        },
                        "provenance": {
                            "source_kind": "provider",
                            "provider": "gmail",
                            "source_id": "gmail:msg:abc",
                        },
                    },
                )
                self.assertTrue(duplicate.get("duplicate"))

                signals = self._get_json(base + "/api/ingest/signals?limit=5")
                self.assertIn("count", signals)
                self.assertIn("items", signals)

                surfaces = self._get_json(base + "/api/consciousness/surfaces")
                self.assertIn("files", surfaces)

                refreshed = self._post_json(base + "/api/consciousness/refresh", {"reason": "test"})
                self.assertEqual(refreshed.get("reason"), "test")

                contract = self._post_json(
                    base + "/api/identity/consciousness-contract",
                    {"patch": {"interaction_modes": {"equal_ratio": 0.8}}, "actor": "test"},
                )
                self.assertAlmostEqual(float(contract["interaction_modes"]["equal_ratio"]), 0.8, places=2)

                events = self._get_json(base + "/api/consciousness/events?limit=10")
                self.assertIn("count", events)
                self.assertIn("items", events)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
