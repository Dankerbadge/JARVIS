from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime
from jarvis.security import ActionClass


class LearningMaterializationTests(unittest.TestCase):
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

    def test_materialize_learning_examples_from_traces_and_feedback(self) -> None:
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
                blocked = next(
                    (item for item in traces if str(item.get("status")) == "blocked"),
                    None,
                )
                self.assertIsNotNone(blocked)
                blocked_trace_id = str((blocked or {}).get("trace_id") or "")
                self.assertTrue(bool(blocked_trace_id))

                feedback = runtime.record_suggestion_feedback(
                    suggestion_id="sgn_manual_blocked_trace",
                    source_trace_id=blocked_trace_id,
                    accepted=False,
                    action_taken="manual_triage",
                    utility_score=-0.75,
                )
                self.assertEqual(str(feedback.get("source_trace_id")), blocked_trace_id)

                materialized = runtime.materialize_learning_examples(plan_id=plan_id, limit=200)
                self.assertGreaterEqual(int(materialized.get("materialized_count") or 0), 1)

                examples = runtime.list_learning_examples(plan_id=plan_id, limit=200)
                self.assertTrue(bool(examples))
                blocked_example = next(
                    (item for item in examples if str(item.get("trace_id")) == blocked_trace_id),
                    None,
                )
                self.assertIsNotNone(blocked_example)
                self.assertEqual(str((blocked_example or {}).get("label_source")), "trace+feedback.explicit")
                self.assertAlmostEqual(float((blocked_example or {}).get("utility_score") or 0.0), -0.75, places=6)
                features = (blocked_example or {}).get("feature_vector") or {}
                self.assertTrue(bool(features.get("feedback_present")))
            finally:
                runtime.close()

    def test_learning_eval_ranking_and_policy_registry(self) -> None:
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
                runtime.materialize_learning_examples(plan_id=plan_id, limit=200)

                report = runtime.evaluate_learning_policy(plan_id=plan_id, limit=200, top_actions=5)
                self.assertGreaterEqual(int(report.get("example_count") or 0), 1)
                metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
                self.assertGreaterEqual(int(metrics.get("total_examples") or 0), 1)
                ranked_actions = list(report.get("ranked_actions") or [])
                self.assertTrue(bool(ranked_actions))

                promoted = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="baseline-ranker",
                    metrics=metrics,
                    promoted_by="test",
                )
                self.assertTrue(str(promoted.get("policy_id")).startswith("lpol_"))
                promoted_metadata = promoted.get("metadata") if isinstance(promoted.get("metadata"), dict) else {}
                self.assertEqual(
                    str(promoted_metadata.get("promotion_audit_id") or ""),
                    str((promoted.get("audit") or {}).get("audit_id") or ""),
                )
                policies = runtime.list_learning_policies(task_family="workflow_step_decision", limit=5)
                linked = next(
                    (item for item in policies if str(item.get("policy_id")) == str(promoted.get("policy_id"))),
                    None,
                )
                self.assertIsNotNone(linked)
                linked_metadata = (linked or {}).get("metadata") if isinstance((linked or {}).get("metadata"), dict) else {}
                self.assertEqual(
                    str(linked_metadata.get("promotion_audit_id") or ""),
                    str((promoted.get("audit") or {}).get("audit_id") or ""),
                )
            finally:
                runtime.close()

    def test_materialization_infers_feedback_from_blocked_to_success_transition(self) -> None:
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

                # First run intentionally blocks on approval to create blocked transition history.
                runtime.run(plan_id, dry_run=True, approvals={})

                approvals: dict[str, str] = {}
                tracked_step_id: str | None = None
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
                        if tracked_step_id is None:
                            tracked_step_id = step.step_id

                self.assertIsNotNone(tracked_step_id)
                runtime.run(plan_id, dry_run=True, approvals=approvals)

                materialized = runtime.materialize_learning_examples(plan_id=plan_id, limit=300)
                self.assertGreaterEqual(int(materialized.get("materialized_count") or 0), 1)

                traces = runtime.list_decision_traces(
                    plan_id=plan_id,
                    step_id=str(tracked_step_id),
                    limit=50,
                )
                succeeded_trace = next(
                    (item for item in traces if str(item.get("status")) == "succeeded"),
                    None,
                )
                self.assertIsNotNone(succeeded_trace)
                succeeded_trace_id = str((succeeded_trace or {}).get("trace_id") or "")
                self.assertTrue(bool(succeeded_trace_id))

                feedback_rows = runtime.list_suggestion_feedback(source_trace_id=succeeded_trace_id, limit=20)
                inferred = next(
                    (item for item in feedback_rows if bool((item.get("metadata") or {}).get("inferred"))),
                    None,
                )
                self.assertIsNotNone(inferred)
                self.assertTrue(bool((inferred or {}).get("accepted")))
            finally:
                runtime.close()

    def test_promotion_gate_blocks_without_examples(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                promoted = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="gate-blocked-empty",
                )
                self.assertFalse(bool(promoted.get("promoted")))
                gate = promoted.get("gate") if isinstance(promoted.get("gate"), dict) else {}
                reasons = list(gate.get("reasons") or [])
                self.assertIn("insufficient_examples", reasons)

                bypass = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="gate-bypass-empty",
                    enforce_gates=False,
                )
                self.assertTrue(bool(bypass.get("promoted")))
                self.assertTrue(str(bypass.get("policy_id")).startswith("lpol_"))
            finally:
                runtime.close()

    def test_promotion_presets_adaptive_overrides_and_audits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                strict_block = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="strict-block",
                    gate_preset="strict",
                )
                self.assertFalse(bool(strict_block.get("promoted")))
                strict_gate = strict_block.get("gate") if isinstance(strict_block.get("gate"), dict) else {}
                strict_thresholds = strict_gate.get("thresholds") if isinstance(strict_gate.get("thresholds"), dict) else {}
                self.assertEqual(str(strict_thresholds.get("preset")), "strict")
                strict_audit = strict_block.get("audit") if isinstance(strict_block.get("audit"), dict) else {}
                self.assertEqual(str(strict_audit.get("decision")), "blocked")

                runtime.update_adaptive_policy(
                    patch={
                        "learning": {
                            "promotion_gates": {
                                "defaults": {
                                    "min_examples": 50,
                                }
                            }
                        }
                    },
                    reason="test_learning_gate_defaults",
                )
                adaptive_block = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="adaptive-block",
                    gate_preset="aggressive",
                )
                self.assertFalse(bool(adaptive_block.get("promoted")))
                adaptive_gate = adaptive_block.get("gate") if isinstance(adaptive_block.get("gate"), dict) else {}
                adaptive_thresholds = (
                    adaptive_gate.get("thresholds")
                    if isinstance(adaptive_gate.get("thresholds"), dict)
                    else {}
                )
                self.assertEqual(int(adaptive_thresholds.get("min_examples") or -1), 50)
                self.assertEqual(str(adaptive_thresholds.get("preset")), "aggressive")

                explicit_override = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="adaptive-explicit-override",
                    gate_preset="aggressive",
                    min_examples=0,
                    require_ranked_actions=False,
                )
                self.assertTrue(bool(explicit_override.get("promoted")))
                explicit_audit = explicit_override.get("audit") if isinstance(explicit_override.get("audit"), dict) else {}
                self.assertEqual(str(explicit_audit.get("decision")), "promoted")

                audits = runtime.list_learning_promotion_audits(
                    task_family="workflow_step_decision",
                    limit=20,
                )
                decisions = {str(item.get("decision")) for item in audits}
                self.assertIn("blocked", decisions)
                self.assertIn("promoted", decisions)
            finally:
                runtime.close()

    def test_learning_gate_profile_and_policy_lifecycle_controls(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                strict = runtime.get_learning_gate_profile(gate_preset="strict")
                strict_thresholds = strict.get("thresholds") if isinstance(strict.get("thresholds"), dict) else {}
                self.assertEqual(str(strict.get("preset")), "strict")
                self.assertGreaterEqual(int(strict_thresholds.get("min_examples") or 0), 1)

                updated_profile = runtime.set_learning_gate_profile(
                    gate_preset="strict",
                    min_examples=21,
                    actor="test",
                    reason="tighten_profile",
                )
                updated_thresholds = (
                    updated_profile.get("thresholds")
                    if isinstance(updated_profile.get("thresholds"), dict)
                    else {}
                )
                self.assertEqual(int(updated_thresholds.get("min_examples") or -1), 21)

                p1 = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="lifecycle-p1",
                    enforce_gates=False,
                )
                self.assertTrue(bool(p1.get("promoted")))
                p1_id = str(p1.get("policy_id") or "")
                self.assertTrue(bool(p1_id))

                p2 = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="lifecycle-p2",
                    enforce_gates=False,
                )
                self.assertTrue(bool(p2.get("promoted")))
                p2_id = str(p2.get("policy_id") or "")
                self.assertTrue(bool(p2_id))

                all_policies = runtime.list_learning_policies(task_family="workflow_step_decision", limit=20)
                by_id = {str(item.get("policy_id")): item for item in all_policies}
                self.assertEqual(str((by_id.get(p2_id) or {}).get("policy_status")), "active")
                self.assertEqual(str((by_id.get(p1_id) or {}).get("policy_status")), "superseded")
                active_initial = runtime.get_active_learning_policy(task_family="workflow_step_decision")
                self.assertEqual(str((active_initial or {}).get("policy_id") or ""), p2_id)

                disabled = runtime.disable_learning_policy(
                    policy_id=p2_id,
                    actor="test",
                    reason="safety_pause",
                )
                self.assertEqual(
                    str(((disabled.get("policy") or {}).get("policy_status"))),
                    "disabled",
                )
                self.assertEqual(str((disabled.get("audit") or {}).get("decision")), "disabled")
                disabled_audit_metadata = (
                    (disabled.get("audit") or {}).get("metadata")
                    if isinstance((disabled.get("audit") or {}).get("metadata"), dict)
                    else {}
                )
                self.assertEqual(str(disabled_audit_metadata.get("from_status") or ""), "active")
                self.assertEqual(str(disabled_audit_metadata.get("to_status") or ""), "disabled")
                active_after_disable = runtime.get_active_learning_policy(task_family="workflow_step_decision")
                self.assertIsNone(active_after_disable)

                rollback = runtime.rollback_learning_policy(
                    task_family="workflow_step_decision",
                    target_policy_id=p1_id,
                    actor="test",
                    reason="restore_previous_policy",
                )
                self.assertEqual(str(((rollback.get("policy") or {}).get("policy_status"))), "active")
                self.assertEqual(str((rollback.get("audit") or {}).get("decision")), "rolled_back")
                rollback_audit_metadata = (
                    (rollback.get("audit") or {}).get("metadata")
                    if isinstance((rollback.get("audit") or {}).get("metadata"), dict)
                    else {}
                )
                self.assertEqual(str(rollback_audit_metadata.get("from_status") or ""), "superseded")
                self.assertEqual(str(rollback_audit_metadata.get("to_status") or ""), "active")
                active_after_rollback = runtime.get_active_learning_policy(task_family="workflow_step_decision")
                self.assertEqual(str((active_after_rollback or {}).get("policy_id") or ""), p1_id)

                active = runtime.list_learning_policies(
                    task_family="workflow_step_decision",
                    policy_status="active",
                    limit=10,
                )
                self.assertTrue(any(str(item.get("policy_id")) == p1_id for item in active))

                audits = runtime.list_learning_promotion_audits(
                    task_family="workflow_step_decision",
                    limit=100,
                )
                decisions = {str(item.get("decision")) for item in audits}
                self.assertIn("disabled", decisions)
                self.assertIn("rolled_back", decisions)
                self.assertIn("activated", decisions)
                self.assertIn("superseded", decisions)
            finally:
                runtime.close()

    def test_policy_status_guardrail_requires_explicit_superseded_disable_intent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                p1 = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="guardrail-p1",
                    enforce_gates=False,
                )
                self.assertTrue(bool(p1.get("promoted")))
                p1_id = str(p1.get("policy_id") or "")
                self.assertTrue(bool(p1_id))

                p2 = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="guardrail-p2",
                    enforce_gates=False,
                )
                self.assertTrue(bool(p2.get("promoted")))
                p2_id = str(p2.get("policy_id") or "")
                self.assertTrue(bool(p2_id))

                with self.assertRaises(ValueError):
                    runtime.disable_learning_policy(
                        policy_id=p1_id,
                        actor="test",
                        reason="cleanup_superseded_without_flag",
                    )

                active_still = runtime.get_active_learning_policy(task_family="workflow_step_decision")
                self.assertEqual(str((active_still or {}).get("policy_id") or ""), p2_id)

                disabled = runtime.disable_learning_policy(
                    policy_id=p1_id,
                    actor="test",
                    reason="cleanup_superseded_with_flag",
                    allow_superseded_disable=True,
                )
                policy = disabled.get("policy") if isinstance(disabled.get("policy"), dict) else {}
                self.assertEqual(str(policy.get("policy_status") or ""), "disabled")
                metadata = policy.get("metadata") if isinstance(policy.get("metadata"), dict) else {}
                self.assertTrue(bool(metadata.get("allow_superseded_disable")))
                audit_metadata = (
                    (disabled.get("audit") or {}).get("metadata")
                    if isinstance((disabled.get("audit") or {}).get("metadata"), dict)
                    else {}
                )
                self.assertEqual(str(audit_metadata.get("from_status") or ""), "superseded")
                self.assertEqual(str(audit_metadata.get("to_status") or ""), "disabled")
            finally:
                runtime.close()

    def test_get_active_learning_policy_fallback_to_latest_when_no_active(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                p1 = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="fallback-p1",
                    enforce_gates=False,
                )
                self.assertTrue(bool(p1.get("promoted")))
                p1_id = str(p1.get("policy_id") or "")
                self.assertTrue(bool(p1_id))

                p2 = runtime.promote_learning_policy(
                    task_family="workflow_step_decision",
                    policy_name="fallback-p2",
                    enforce_gates=False,
                )
                self.assertTrue(bool(p2.get("promoted")))
                p2_id = str(p2.get("policy_id") or "")
                self.assertTrue(bool(p2_id))

                runtime.disable_learning_policy(
                    policy_id=p2_id,
                    actor="test",
                    reason="pause_current_champion",
                )

                no_active = runtime.get_active_learning_policy(task_family="workflow_step_decision")
                self.assertIsNone(no_active)

                fallback = runtime.get_active_learning_policy(
                    task_family="workflow_step_decision",
                    fallback_to_latest=True,
                )
                self.assertEqual(str((fallback or {}).get("policy_id") or ""), p2_id)
                self.assertEqual(str((fallback or {}).get("policy_status") or ""), "disabled")
            finally:
                runtime.close()

    def test_policy_status_transition_matrix_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                active = runtime.learning_registry.register_policy(
                    task_family="matrix_family",
                    policy_name="matrix-active",
                    promoted_by="test",
                )
                disabled = runtime.learning_registry.register_policy(
                    task_family="matrix_family",
                    policy_name="matrix-disabled",
                    promoted_by="test",
                )
                superseded = runtime.learning_registry.register_policy(
                    task_family="matrix_family",
                    policy_name="matrix-superseded",
                    promoted_by="test",
                )

                active_id = str(active.get("policy_id") or "")
                disabled_id = str(disabled.get("policy_id") or "")
                superseded_id = str(superseded.get("policy_id") or "")
                self.assertTrue(bool(active_id and disabled_id and superseded_id))

                runtime.learning_registry.set_policy_status(
                    policy_id=disabled_id,
                    policy_status="disabled",
                    actor="test",
                    reason="seed_disabled",
                )
                runtime.learning_registry.set_policy_status(
                    policy_id=superseded_id,
                    policy_status="superseded",
                    actor="test",
                    reason=f"superseded_by:{active_id}",
                    superseded_by_policy_id=active_id,
                )

                # No-op transitions are allowed.
                no_op_active = runtime.learning_registry.set_policy_status(
                    policy_id=active_id,
                    policy_status="active",
                    actor="test",
                    reason="noop_active",
                )
                self.assertEqual(str(no_op_active.get("policy_status") or ""), "active")
                no_op_disabled = runtime.learning_registry.set_policy_status(
                    policy_id=disabled_id,
                    policy_status="disabled",
                    actor="test",
                    reason="noop_disabled",
                )
                self.assertEqual(str(no_op_disabled.get("policy_status") or ""), "disabled")
                no_op_superseded = runtime.learning_registry.set_policy_status(
                    policy_id=superseded_id,
                    policy_status="superseded",
                    actor="test",
                    reason="noop_superseded",
                    superseded_by_policy_id=active_id,
                )
                self.assertEqual(str(no_op_superseded.get("policy_status") or ""), "superseded")

                # Allowed directional transitions.
                active_to_disabled = runtime.learning_registry.set_policy_status(
                    policy_id=active_id,
                    policy_status="disabled",
                    actor="test",
                    reason="active_to_disabled",
                )
                self.assertEqual(str(active_to_disabled.get("policy_status") or ""), "disabled")
                disabled_to_active = runtime.learning_registry.set_policy_status(
                    policy_id=disabled_id,
                    policy_status="active",
                    actor="test",
                    reason="disabled_to_active",
                )
                self.assertEqual(str(disabled_to_active.get("policy_status") or ""), "active")
                disabled_to_superseded = runtime.learning_registry.set_policy_status(
                    policy_id=active_id,
                    policy_status="superseded",
                    actor="test",
                    reason=f"superseded_by:{disabled_id}",
                    superseded_by_policy_id=disabled_id,
                )
                self.assertEqual(str(disabled_to_superseded.get("policy_status") or ""), "superseded")
                superseded_to_active = runtime.learning_registry.set_policy_status(
                    policy_id=superseded_id,
                    policy_status="active",
                    actor="test",
                    reason="superseded_to_active",
                )
                self.assertEqual(str(superseded_to_active.get("policy_status") or ""), "active")

                # Guardrail: superseded -> disabled requires explicit intent.
                runtime.learning_registry.set_policy_status(
                    policy_id=superseded_id,
                    policy_status="superseded",
                    actor="test",
                    reason=f"superseded_by:{disabled_id}",
                    superseded_by_policy_id=disabled_id,
                )
                with self.assertRaises(ValueError):
                    runtime.learning_registry.set_policy_status(
                        policy_id=superseded_id,
                        policy_status="disabled",
                        actor="test",
                        reason="blocked_without_flag",
                    )
                allowed = runtime.learning_registry.set_policy_status(
                    policy_id=superseded_id,
                    policy_status="disabled",
                    actor="test",
                    reason="allowed_with_flag",
                    allow_superseded_disable=True,
                )
                self.assertEqual(str(allowed.get("policy_status") or ""), "disabled")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
