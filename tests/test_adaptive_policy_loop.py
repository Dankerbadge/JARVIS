from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class AdaptivePolicyLoopTests(unittest.TestCase):
    def test_relationship_mode_uses_adaptive_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.update_adaptive_policy(
                    patch={"relationship_mode": {"uncertainty_strategist_threshold": 0.9}},
                    reason="test_threshold",
                )
                decision = runtime.decide_relationship_mode(
                    uncertainty=0.7,
                    high_stakes=False,
                    disputed=False,
                    context={"source": "test"},
                )
                self.assertEqual(decision.get("mode"), "equal")
            finally:
                runtime.close()

    def test_manual_calibration_updates_policy_revision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                before = runtime.get_adaptive_policy_revision()
                runtime.tone_balance.record(
                    mode="equal",
                    modality="voice",
                    profile={"warmth": 0.4, "challenge": 0.8},
                    imbalances=["challenge_over_warmth"],
                    calibration_hint="increase_warmth_before_pushback",
                )
                result = runtime.run_adaptive_calibration(reason="test_manual", apply=True)
                after = runtime.get_adaptive_policy_revision()
                self.assertTrue(result.get("ok"))
                self.assertTrue(result.get("applied"))
                self.assertNotEqual(before, after)
                self.assertIn("metrics", result)
                self.assertIn("policy_patch", result)
            finally:
                runtime.close()

    def test_auto_calibration_runs_on_turn_interval(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.update_adaptive_policy(
                    patch={"runtime": {"auto_calibration_enabled": True, "auto_calibration_every_turns": 5}},
                    reason="test_auto_config",
                )
                before_runs = int(((runtime.get_adaptive_policy().get("metadata") or {}).get("calibration_runs") or 0))
                runtime._adaptive_turn_counter = 4  # force next prepared reply to trigger auto calibration
                runtime.prepare_openclaw_reply(
                    {
                        "text": "status update",
                        "surface_id": "dm:owner",
                        "session_id": "auto-1",
                    }
                )
                after_runs = int(((runtime.get_adaptive_policy().get("metadata") or {}).get("calibration_runs") or 0))
                self.assertGreaterEqual(after_runs, before_runs + 1)
            finally:
                runtime.close()

    def test_routing_bias_can_shift_preview_route(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.update_adaptive_policy(
                    patch={
                        "routing": {
                            "codex_bias": 2.0,
                            "delegate_score_threshold": 1.0,
                        }
                    },
                    reason="test_routing_bias",
                )
                preview = runtime.preview_work_item_route(
                    text="status update",
                    context={},
                )
                work_item = preview.get("work_item") if isinstance(preview.get("work_item"), dict) else {}
                self.assertEqual(work_item.get("engine_route"), "codex")
            finally:
                runtime.close()

    def test_self_patch_trigger_from_calibration_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.update_adaptive_policy(
                    patch={
                        "self_patch": {
                            "enabled": True,
                            "auto_execute": False,
                            "cooldown_minutes": 1,
                            "max_open_tasks": 3,
                            "min_voice_turns": 1,
                            "min_continuity_failure_rate": 0.1,
                        }
                    },
                    reason="test_self_patch_policy",
                )
                result = runtime._maybe_trigger_self_patch_from_calibration(
                    metrics={
                        "voice_turn_count": 5,
                        "continuity_failure_rate": 0.4,
                        "mode_scored_turns": 0,
                        "mode_accuracy": None,
                        "codex_total": 0,
                        "codex_fail_rate": 0.0,
                        "interrupted_turns": 0,
                        "interruption_recovery_rate": None,
                        "review_total": 0,
                        "negative_review_rate": 0.0,
                    },
                    reason="test_metric_trigger",
                )
                self.assertTrue(result.get("triggered"))
                submission = (result.get("submission") or {}).get("submission") if isinstance(result.get("submission"), dict) else {}
                task = submission.get("task") if isinstance(submission.get("task"), dict) else {}
                self.assertEqual(task.get("status"), "queued")
                context = task.get("context") if isinstance(task.get("context"), dict) else {}
                self.assertTrue(bool(context.get("self_patch")))
                self.assertEqual(str(context.get("self_patch_project_scope") or ""), "jarvis")
            finally:
                runtime.close()

    def test_self_patch_blocks_when_quota_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.update_adaptive_policy(
                    patch={
                        "self_patch": {
                            "enabled": True,
                            "weekly_remaining_percent": 35.0,
                            "min_weekly_remaining_percent": 40.0,
                        }
                    },
                    reason="test_quota_block",
                )
                result = runtime.trigger_self_patch_task(
                    issue="Improve routing observability for voice mode.",
                    reason="quota_block_test",
                    project_scope="jarvis",
                    approval_source="codex",
                    change_impact="minor",
                )
                self.assertFalse(bool(result.get("ok")))
                self.assertEqual(result.get("error"), "quota_below_threshold")
            finally:
                runtime.close()

    def test_self_patch_requires_owner_for_major_external(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                result = runtime.trigger_self_patch_task(
                    issue="Install and configure a new external broker integration.",
                    reason="owner_gate_test",
                    project_scope="market_ml",
                    approval_source="codex",
                    change_impact="major",
                    requested_capabilities=["install_app", "service_access", "obtain_key"],
                )
                self.assertFalse(bool(result.get("ok")))
                self.assertEqual(result.get("error"), "owner_approval_required")
                self.assertTrue(bool(result.get("approval_required")))
                self.assertEqual(result.get("required_approval_source"), "owner")
            finally:
                runtime.close()

    def test_self_patch_owner_approval_allows_major_external(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                result = runtime.trigger_self_patch_task(
                    issue="Integrate approved data provider for betting feature pipeline.",
                    reason="owner_gate_allow_test",
                    project_scope="betting_bot",
                    approval_source="owner",
                    change_impact="major",
                    requested_capabilities=["service_access"],
                    auto_execute=False,
                )
                self.assertTrue(bool(result.get("ok")))
                submission = result.get("submission") if isinstance(result.get("submission"), dict) else {}
                task = submission.get("task") if isinstance(submission.get("task"), dict) else {}
                self.assertEqual(task.get("status"), "queued")
                governance = result.get("governance") if isinstance(result.get("governance"), dict) else {}
                self.assertEqual(governance.get("project_scope"), "betting_bot")
                self.assertEqual(governance.get("approval_source"), "owner")
                self.assertTrue(bool(governance.get("requires_external_access")))
            finally:
                runtime.close()

    def test_self_patch_allows_minor_external_with_codex_approval(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                result = runtime.trigger_self_patch_task(
                    issue="Add trusted market data access to improve forecast speed.",
                    reason="minor_external_access_test",
                    project_scope="market_ml",
                    approval_source="codex",
                    change_impact="minor",
                    requested_capabilities=["service_access", "trusted_data_source"],
                    auto_execute=False,
                )
                self.assertTrue(bool(result.get("ok")))
                governance = result.get("governance") if isinstance(result.get("governance"), dict) else {}
                self.assertEqual(governance.get("change_impact"), "minor")
                self.assertTrue(bool(governance.get("requires_external_access")))
                self.assertTrue(bool(governance.get("minor_external_access_allowed")))
            finally:
                runtime.close()

    def test_self_patch_blocks_minor_external_when_policy_disallows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.update_adaptive_policy(
                    patch={
                        "self_patch": {
                            "external_access_requires_owner": True,
                            "minor_external_access_allowed": False,
                        }
                    },
                    reason="strict_external_policy",
                )
                result = runtime.trigger_self_patch_task(
                    issue="Add provider access for market data feeds.",
                    reason="strict_external_gate_test",
                    project_scope="market_ml",
                    approval_source="codex",
                    change_impact="minor",
                    requested_capabilities=["service_access"],
                )
                self.assertFalse(bool(result.get("ok")))
                self.assertEqual(result.get("error"), "owner_approval_required")
            finally:
                runtime.close()

    def test_update_self_patch_quota_updates_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                updated = runtime.update_self_patch_quota(
                    weekly_remaining_percent=72.5,
                    min_weekly_remaining_percent=40.0,
                    actor="test",
                    reason="quota_update_test",
                )
                self.assertTrue(bool(updated.get("ok")))
                quota = updated.get("quota") if isinstance(updated.get("quota"), dict) else {}
                self.assertEqual(quota.get("weekly_remaining_percent"), 72.5)
                policy = runtime.get_adaptive_policy()
                self_patch = policy.get("self_patch") if isinstance(policy.get("self_patch"), dict) else {}
                self.assertEqual(self_patch.get("weekly_remaining_percent"), 72.5)
                events = runtime.list_self_patch_events(limit=5)
                event_types = {str(item.get("event_type") or "") for item in events}
                self.assertIn("codex.self_patch_quota_updated", event_types)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
