from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jarvis.cli import cmd_plans_gate_status, cmd_plans_gate_status_all
from jarvis.models import utc_now_iso
from jarvis.interrupts import InterruptDecision
from jarvis.providers.base import ProviderReviewArtifact, ReviewFeedbackSnapshot, ReviewStatusSnapshot
from jarvis.review_service import ReviewService
from jarvis.runtime import JarvisRuntime


def _git(cwd: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


class _FakeProviderNoChecks:
    provider_name = "fake"

    def create_review(
        self,
        *,
        repo_slug: str,
        title: str,
        body_markdown: str,
        head_branch: str,
        base_branch: str,
        head_sha: str,
        draft: bool,
        labels=(),
        reviewers=(),
        metadata=None,
    ) -> ProviderReviewArtifact:
        return ProviderReviewArtifact(
            provider="fake",
            repo_slug=repo_slug,
            external_id="provider-1",
            number="1",
            title=title,
            body_markdown=body_markdown,
            web_url="https://example.test/acme/zenith/pulls/1",
            api_url="https://api.example.test/pulls/1",
            base_branch=base_branch,
            head_branch=head_branch,
            head_sha=head_sha,
            state="open",
            draft=draft,
            labels=tuple(labels),
            reviewers=tuple(reviewers),
            status=ReviewStatusSnapshot(
                review_state="open",
                checks_state=None,
                merged=False,
                draft=draft,
                mergeable=True,
                blocking_contexts=(),
                head_sha=head_sha,
                web_url="https://example.test/acme/zenith/pulls/1",
                synced_at=utc_now_iso(),
            ),
            feedback=ReviewFeedbackSnapshot(
                requested_reviewers=tuple(reviewers),
                reviews=(),
                issue_comments=(),
                review_comments=(),
                timeline_events=(),
                timeline_cursor="1",
                review_summary={"decision": "none", "total_reviews": 0},
                merge_outcome=None,
                required_checks=(),
                required_checks_configured=False,
                synced_at=utc_now_iso(),
            ),
            metadata=dict(metadata or {}),
        )

    def sync_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact:
        # Simulate "no checks configured and no checks reported".
        feedback = artifact.feedback or ReviewFeedbackSnapshot()
        return artifact.with_updates(
            status=ReviewStatusSnapshot(
                review_state="open",
                checks_state=None,
                merged=False,
                draft=artifact.draft,
                mergeable=True,
                blocking_contexts=(),
                head_sha=artifact.head_sha,
                web_url=artifact.web_url,
                synced_at=utc_now_iso(),
            ).to_dict(),
            feedback=feedback.with_updates(
                required_checks=(),
                required_checks_configured=False,
                synced_at=utc_now_iso(),
            )
            if hasattr(feedback, "with_updates")
            else ReviewFeedbackSnapshot(
                requested_reviewers=feedback.requested_reviewers,
                reviews=feedback.reviews,
                issue_comments=feedback.issue_comments,
                review_comments=feedback.review_comments,
                timeline_events=feedback.timeline_events,
                timeline_cursor=feedback.timeline_cursor,
                review_summary=dict(feedback.review_summary),
                merge_outcome=feedback.merge_outcome,
                required_checks=(),
                required_checks_configured=False,
                synced_at=utc_now_iso(),
            ),
            updated_at=utc_now_iso(),
        )

    def configure_review(
        self,
        artifact: ProviderReviewArtifact,
        *,
        reviewers=None,
        labels=None,
    ) -> ProviderReviewArtifact:
        reviewers_tuple = tuple(reviewers or artifact.reviewers)
        labels_tuple = tuple(labels or artifact.labels)
        feedback = artifact.feedback or ReviewFeedbackSnapshot()
        return artifact.with_updates(
            reviewers=reviewers_tuple,
            labels=labels_tuple,
            feedback=ReviewFeedbackSnapshot(
                requested_reviewers=reviewers_tuple,
                reviews=feedback.reviews,
                issue_comments=feedback.issue_comments,
                review_comments=feedback.review_comments,
                timeline_events=feedback.timeline_events,
                timeline_cursor=feedback.timeline_cursor,
                review_summary=dict(feedback.review_summary),
                merge_outcome=feedback.merge_outcome,
                required_checks=feedback.required_checks,
                required_checks_configured=feedback.required_checks_configured,
                synced_at=utc_now_iso(),
            ),
            updated_at=utc_now_iso(),
        )

    def mark_ready_for_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact:
        return artifact.with_updates(
            draft=False,
            status=ReviewStatusSnapshot(
                review_state="open",
                checks_state=None,
                merged=False,
                draft=False,
                mergeable=True,
                blocking_contexts=(),
                head_sha=artifact.head_sha,
                web_url=artifact.web_url,
                synced_at=utc_now_iso(),
            ).to_dict(),
            updated_at=utc_now_iso(),
        )


class RuntimePromotionPolicyTests(unittest.TestCase):
    def _init_repo_with_remote(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        remote = root / "origin.git"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text("def render():\n    return 'TODO_ZENITH'\n", encoding="utf-8")
        _git(str(root), "init", str(repo))
        _git(str(repo), "config", "user.email", "jarvis@example.com")
        _git(str(repo), "config", "user.name", "JARVIS")
        _git(str(repo), "add", ".")
        _git(str(repo), "commit", "-m", "initial")
        _git(str(repo), "branch", "-M", "main")
        _git(str(root), "init", "--bare", str(remote))
        _git(str(repo), "remote", "add", "origin", str(remote))
        _git(str(repo), "push", "-u", "origin", "main")
        return repo, remote

    def test_promotion_policy_blocks_without_required_checks_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, remote = self._init_repo_with_remote(root)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                review_service=ReviewService({"fake": _FakeProviderNoChecks()}),
            )
            try:
                plan = runtime.zenith.propose_git_delta_plan(
                    {
                        "project": "zenith",
                        "repo_id": "zenith",
                        "branch": "feature/ci-fix",
                        "base_branch": "main",
                        "head_sha": "deadbeef",
                        "changed_files": ["service.py", "ui/zenith_ui.txt"],
                        "dirty_files": [],
                        "protected_ui_changed": True,
                        "pr_candidate": True,
                    }
                )
                assert plan is not None
                runtime.plan_repo.save_plan(plan)
                step = next(step for step in plan.steps if step.action_class == "P2")
                prepared = runtime.preflight_plan(plan.plan_id)
                runtime.security.approve(prepared[0]["approval_id"], approved_by="tester")

                combined = runtime.publish_approved_step(
                    plan.plan_id,
                    step.step_id,
                    remote_name="origin",
                    base_branch="main",
                    draft=True,
                    open_review=True,
                    provider="fake",
                    provider_repo="acme/zenith",
                    reviewers=[],
                )
                review = combined["review"]
                required_labels = list(review["labels"])

                runtime.configure_provider_review(
                    plan.plan_id,
                    step.step_id,
                    reviewers=[],
                    labels=required_labels,
                )
                runtime.sync_provider_review(plan.plan_id, step.step_id)

                blocked = runtime.evaluate_review_promotion_policy(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                )
                self.assertFalse(blocked["eligible"])
                self.assertIn("no_required_checks_configured", blocked["reasons"])
                self.assertIn("requested_reviewers_missing", blocked["reasons"])

                allowed = runtime.evaluate_review_promotion_policy(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                )
                self.assertFalse(allowed["eligible"])
                self.assertIn("requested_reviewers_missing", allowed["reasons"])

                solo_allowed = runtime.evaluate_review_promotion_policy(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                )
                self.assertTrue(solo_allowed["eligible"])
                self.assertTrue(solo_allowed["policy"]["single_maintainer_override"])
                self.assertEqual(
                    solo_allowed["policy"]["single_maintainer_override_policy"]["actor"],
                    "tester",
                )
                self.assertEqual(
                    solo_allowed["policy"]["single_maintainer_override_policy"]["pr_number"],
                    "1",
                )

                denied_promote = runtime.promote_provider_review_ready(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                )
                self.assertFalse(denied_promote["promoted"])

                promoted = runtime.promote_provider_review_ready(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                )
                self.assertTrue(promoted["promoted"])
                self.assertFalse(promoted["review"]["draft"])
                override = promoted["review"]["metadata"]["promotion_override"]
                self.assertTrue(override["single_maintainer_override"])
                self.assertTrue(override["allow_no_required_checks"])
                self.assertEqual(override["policy"]["actor"], "tester")
                self.assertEqual(override["policy"]["reason"], "single maintainer")
            finally:
                runtime.close()
                shutil.rmtree(remote, ignore_errors=True)

    def test_critical_matrix_drift_gate_blocks_until_interrupt_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, remote = self._init_repo_with_remote(root)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                review_service=ReviewService({"fake": _FakeProviderNoChecks()}),
            )
            try:
                plan = runtime.zenith.propose_git_delta_plan(
                    {
                        "project": "zenith",
                        "repo_id": "zenith",
                        "branch": "feature/critical-gate",
                        "base_branch": "main",
                        "head_sha": "cafefeed",
                        "changed_files": ["service.py", "ui/zenith_ui.txt"],
                        "dirty_files": [],
                        "protected_ui_changed": True,
                        "pr_candidate": True,
                    }
                )
                assert plan is not None
                runtime.plan_repo.save_plan(plan)
                step = next(step for step in plan.steps if step.action_class == "P2")
                prepared = runtime.preflight_plan(plan.plan_id)
                runtime.security.approve(prepared[0]["approval_id"], approved_by="tester")

                combined = runtime.publish_approved_step(
                    plan.plan_id,
                    step.step_id,
                    remote_name="origin",
                    base_branch="main",
                    draft=True,
                    open_review=True,
                    provider="fake",
                    provider_repo="acme/zenith",
                    reviewers=[],
                )
                review = combined["review"]
                required_labels = list(review["labels"])

                runtime.configure_provider_review(
                    plan.plan_id,
                    step.step_id,
                    reviewers=[],
                    labels=required_labels,
                )
                runtime.sync_provider_review(plan.plan_id, step.step_id)

                baseline_allowed = runtime.evaluate_review_promotion_policy(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=True,
                )
                self.assertTrue(baseline_allowed["eligible"])

                critical_alert = InterruptDecision(
                    interrupt_id="int_critical_drift_gate_1",
                    candidate_id="cand_critical_drift_gate_1",
                    domain="markets",
                    reason=(
                        "matrix_drift_detected severity=critical mismatches=1 "
                        "missing=0 invalid=0 guardrail_mismatches=1 top=scenario_a"
                    ),
                    urgency_score=0.98,
                    confidence=0.95,
                    suppression_window_hit=False,
                    delivered=True,
                    why_now="critical matrix drift detected",
                    why_not_later="promotion should remain blocked until acknowledged",
                    status="delivered",
                )
                runtime.interrupt_store.store(critical_alert)

                blocked = runtime.evaluate_review_promotion_policy(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=True,
                )
                self.assertFalse(blocked["eligible"])
                self.assertIn("critical_matrix_drift_unacknowledged", blocked["reasons"])
                gate = blocked["policy"]
                self.assertTrue(bool(gate.get("critical_drift_gate_enabled")))
                self.assertFalse(bool(gate.get("critical_drift_gate_passed")))
                self.assertEqual(int(gate.get("critical_drift_alert_count") or 0), 1)
                self.assertEqual(len(list(gate.get("critical_drift_alerts") or [])), 1)
                alert_entry = list(gate.get("critical_drift_alerts") or [])[0]
                self.assertEqual(alert_entry.get("interrupt_id"), "int_critical_drift_gate_1")
                self.assertEqual(alert_entry.get("drift_severity"), "critical")
                self.assertEqual(
                    alert_entry.get("acknowledge_command"),
                    "python3 -m jarvis.cli interrupts acknowledge int_critical_drift_gate_1 --actor operator",
                )
                gate_status = dict(gate.get("critical_drift_gate_status") or {})
                self.assertEqual(gate_status.get("mode"), "enabled")
                self.assertTrue(bool(gate_status.get("blocked")))
                self.assertEqual(
                    list(gate_status.get("blocking_interrupt_ids") or []),
                    ["int_critical_drift_gate_1"],
                )
                self.assertEqual(
                    list(gate_status.get("acknowledge_commands") or []),
                    [
                        "python3 -m jarvis.cli interrupts acknowledge "
                        "int_critical_drift_gate_1 --actor operator"
                    ],
                )
                self.assertEqual(
                    list(gate_status.get("blocking_acknowledge_commands") or []),
                    list(gate_status.get("acknowledge_commands") or []),
                )

                gate_disabled = runtime.evaluate_review_promotion_policy(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=False,
                )
                self.assertTrue(gate_disabled["eligible"])
                disabled_status = dict(gate_disabled["policy"].get("critical_drift_gate_status") or {})
                self.assertEqual(disabled_status.get("mode"), "disabled")
                self.assertFalse(bool(disabled_status.get("blocked")))
                self.assertEqual(list(disabled_status.get("blocking_interrupt_ids") or []), [])
                self.assertEqual(list(disabled_status.get("acknowledge_commands") or []), [])

                denied_promote = runtime.promote_provider_review_ready(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=True,
                )
                self.assertFalse(bool(denied_promote.get("promoted")))

                runtime.acknowledge_interrupt("int_critical_drift_gate_1", actor="tester")

                unblocked = runtime.evaluate_review_promotion_policy(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=True,
                )
                self.assertTrue(unblocked["eligible"])
                self.assertNotIn("critical_matrix_drift_unacknowledged", list(unblocked.get("reasons") or []))
                self.assertTrue(bool(unblocked["policy"].get("critical_drift_gate_passed")))
                unblocked_status = dict(unblocked["policy"].get("critical_drift_gate_status") or {})
                self.assertEqual(unblocked_status.get("mode"), "enabled")
                self.assertFalse(bool(unblocked_status.get("blocked")))
                self.assertEqual(list(unblocked_status.get("blocking_interrupt_ids") or []), [])
                self.assertEqual(list(unblocked_status.get("acknowledge_commands") or []), [])

                promoted = runtime.promote_provider_review_ready(
                    plan.plan_id,
                    step.step_id,
                    required_labels=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=True,
                )
                self.assertTrue(bool(promoted.get("promoted")))
            finally:
                runtime.close()
                shutil.rmtree(remote, ignore_errors=True)

    def test_cmd_plans_gate_status_outputs_blocking_interrupt_commands(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, remote = self._init_repo_with_remote(root)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                review_service=ReviewService({"fake": _FakeProviderNoChecks()}),
            )
            try:
                plan = runtime.zenith.propose_git_delta_plan(
                    {
                        "project": "zenith",
                        "repo_id": "zenith",
                        "branch": "feature/gate-status",
                        "base_branch": "main",
                        "head_sha": "abcddcba",
                        "changed_files": ["service.py", "ui/zenith_ui.txt"],
                        "dirty_files": [],
                        "protected_ui_changed": True,
                        "pr_candidate": True,
                    }
                )
                assert plan is not None
                runtime.plan_repo.save_plan(plan)
                step = next(step for step in plan.steps if step.action_class == "P2")
                prepared = runtime.preflight_plan(plan.plan_id)
                runtime.security.approve(prepared[0]["approval_id"], approved_by="tester")

                combined = runtime.publish_approved_step(
                    plan.plan_id,
                    step.step_id,
                    remote_name="origin",
                    base_branch="main",
                    draft=True,
                    open_review=True,
                    provider="fake",
                    provider_repo="acme/zenith",
                    reviewers=[],
                )
                review = combined["review"]
                required_labels = list(review["labels"])

                runtime.configure_provider_review(
                    plan.plan_id,
                    step.step_id,
                    reviewers=[],
                    labels=required_labels,
                )
                runtime.sync_provider_review(plan.plan_id, step.step_id)

                critical_alert = InterruptDecision(
                    interrupt_id="int_gate_status_critical_1",
                    candidate_id="cand_gate_status_critical_1",
                    domain="markets",
                    reason=(
                        "matrix_drift_detected severity=critical mismatches=1 "
                        "missing=0 invalid=0 guardrail_mismatches=1 top=scenario_a"
                    ),
                    urgency_score=0.98,
                    confidence=0.95,
                    suppression_window_hit=False,
                    delivered=True,
                    why_now="critical matrix drift detected",
                    why_not_later="promotion should remain blocked until acknowledged",
                    status="delivered",
                )
                runtime.interrupt_store.store(critical_alert)

                args = argparse.Namespace(
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    required_label=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=True,
                    critical_drift_gate_limit=100,
                    output="json",
                    repo_path=repo,
                    db_path=root / "jarvis.db",
                )
                captured = io.StringIO()
                with redirect_stdout(captured):
                    cmd_plans_gate_status(args)
                payload = json.loads(captured.getvalue())
                self.assertTrue(bool(payload.get("blocked")))
                self.assertEqual(
                    list(payload.get("blocking_interrupt_ids") or []),
                    ["int_gate_status_critical_1"],
                )
                self.assertEqual(
                    list(payload.get("acknowledge_commands") or []),
                    [
                        "python3 -m jarvis.cli interrupts acknowledge "
                        "int_gate_status_critical_1 --actor operator"
                    ],
                )
                blocking_alerts = list(payload.get("blocking_alerts") or [])
                self.assertEqual(len(blocking_alerts), 1)
                self.assertEqual(
                    blocking_alerts[0].get("acknowledge_command"),
                    "python3 -m jarvis.cli interrupts acknowledge int_gate_status_critical_1 --actor operator",
                )

                args.output = "text"
                captured_text = io.StringIO()
                with redirect_stdout(captured_text):
                    cmd_plans_gate_status(args)
                text_payload = captured_text.getvalue()
                self.assertIn("blocked: yes", text_payload)
                self.assertIn("blocking_interrupt_ids: int_gate_status_critical_1", text_payload)
                self.assertIn(
                    "python3 -m jarvis.cli interrupts acknowledge int_gate_status_critical_1 --actor operator",
                    text_payload,
                )

                args_all = argparse.Namespace(
                    limit=25,
                    provider=None,
                    repo_slug=None,
                    required_label=required_labels,
                    allow_no_required_checks=True,
                    single_maintainer_override=True,
                    override_actor="tester",
                    override_reason="single maintainer",
                    override_sunset_condition="disable when checks exist",
                    enforce_critical_drift_gate=True,
                    critical_drift_gate_limit=100,
                    output="json",
                    only_blocked=False,
                    fail_on_blocked=False,
                    fail_on_errors=False,
                    fail_on_zero_scanned=False,
                    fail_on_zero_evaluated=False,
                    fail_on_empty_ack_commands=False,
                    blocked_exit_code=2,
                    error_exit_code=3,
                    zero_scanned_exit_code=5,
                    zero_evaluated_exit_code=4,
                    empty_ack_commands_exit_code=6,
                    emit_ci_summary_path=None,
                    emit_ci_json_path=None,
                    repo_path=repo,
                    db_path=root / "jarvis.db",
                )
                captured_all = io.StringIO()
                with redirect_stdout(captured_all):
                    cmd_plans_gate_status_all(args_all)
                payload_all = json.loads(captured_all.getvalue())
                self.assertFalse(bool(payload_all.get("only_blocked")))
                self.assertFalse(bool(payload_all.get("fail_on_blocked")))
                self.assertFalse(bool(payload_all.get("fail_on_errors")))
                self.assertFalse(bool(payload_all.get("fail_on_zero_scanned")))
                self.assertFalse(bool(payload_all.get("fail_on_zero_evaluated")))
                self.assertFalse(bool(payload_all.get("fail_on_empty_ack_commands")))
                self.assertGreaterEqual(int(payload_all.get("scanned_review_count") or 0), 1)
                self.assertGreaterEqual(int(payload_all.get("evaluated_step_count") or 0), 1)
                self.assertGreaterEqual(int(payload_all.get("visible_step_count") or 0), 1)
                self.assertGreaterEqual(int(payload_all.get("blocked_step_count") or 0), 1)
                self.assertEqual(int(payload_all.get("error_count") or 0), 0)
                blocked_steps = list(payload_all.get("blocked_steps") or [])
                self.assertGreaterEqual(len(blocked_steps), 1)
                self.assertGreaterEqual(len(list(payload_all.get("gate_rows") or [])), 1)
                self.assertEqual(
                    blocked_steps[0].get("acknowledge_commands"),
                    [
                        "python3 -m jarvis.cli interrupts acknowledge "
                        "int_gate_status_critical_1 --actor operator"
                    ],
                )
                self.assertEqual(
                    list(payload_all.get("acknowledge_commands") or []),
                    [
                        "python3 -m jarvis.cli interrupts acknowledge "
                        "int_gate_status_critical_1 --actor operator"
                    ],
                )
                self.assertEqual(int(payload_all.get("exit_code") or 0), 0)
                self.assertFalse(bool(payload_all.get("exit_triggered")))
                self.assertEqual(str(payload_all.get("exit_reason") or ""), "none")
                self.assertEqual(int(payload_all.get("blocked_exit_code") or 0), 2)
                self.assertEqual(int(payload_all.get("error_exit_code") or 0), 3)
                self.assertEqual(int(payload_all.get("zero_scanned_exit_code") or 0), 5)
                self.assertEqual(int(payload_all.get("zero_evaluated_exit_code") or 0), 4)
                self.assertEqual(int(payload_all.get("empty_ack_commands_exit_code") or 0), 6)
                self.assertFalse(bool(payload_all.get("blocked_exit_triggered")))
                self.assertFalse(bool(payload_all.get("error_exit_triggered")))
                self.assertFalse(bool(payload_all.get("zero_scanned_exit_triggered")))
                self.assertFalse(bool(payload_all.get("zero_evaluated_exit_triggered")))
                self.assertFalse(bool(payload_all.get("empty_ack_commands_exit_triggered")))
                self.assertFalse(bool(payload_all.get("ci_summary_path")))
                self.assertFalse(bool(payload_all.get("ci_json_path")))

                ci_summary_path = root / "gate_status_all_summary.md"
                args_all_ci_summary = argparse.Namespace(**vars(args_all))
                args_all_ci_summary.emit_ci_summary_path = ci_summary_path
                captured_all_ci_summary = io.StringIO()
                with redirect_stdout(captured_all_ci_summary):
                    cmd_plans_gate_status_all(args_all_ci_summary)
                payload_all_ci_summary = json.loads(captured_all_ci_summary.getvalue())
                self.assertEqual(
                    str(payload_all_ci_summary.get("ci_summary_path") or ""),
                    str(ci_summary_path.resolve()),
                )
                self.assertEqual(str(payload_all_ci_summary.get("ci_summary_path_source") or ""), "cli")
                self.assertTrue(ci_summary_path.exists())
                ci_summary_text = ci_summary_path.read_text(encoding="utf-8")
                self.assertIn("# plans gate-status-all summary", ci_summary_text)
                self.assertIn("- blocked_step_count: 1", ci_summary_text)
                self.assertIn("int_gate_status_critical_1", ci_summary_text)
                self.assertIn(
                    "`python3 -m jarvis.cli interrupts acknowledge int_gate_status_critical_1 --actor operator`",
                    ci_summary_text,
                )
                self.assertIn("## Next Action", ci_summary_text)

                ci_json_path = root / "gate_status_all_compact.json"
                args_all_ci_json = argparse.Namespace(**vars(args_all))
                args_all_ci_json.emit_ci_json_path = ci_json_path
                captured_all_ci_json = io.StringIO()
                with redirect_stdout(captured_all_ci_json):
                    cmd_plans_gate_status_all(args_all_ci_json)
                payload_all_ci_json = json.loads(captured_all_ci_json.getvalue())
                self.assertEqual(
                    str(payload_all_ci_json.get("ci_json_path") or ""),
                    str(ci_json_path.resolve()),
                )
                self.assertTrue(ci_json_path.exists())
                ci_json_payload = json.loads(ci_json_path.read_text(encoding="utf-8"))
                self.assertEqual(int(ci_json_payload.get("blocked_step_count") or 0), 1)
                self.assertEqual(int(ci_json_payload.get("error_count") or 0), 0)
                self.assertEqual(str(ci_json_payload.get("exit_reason") or ""), "none")
                self.assertEqual(int(ci_json_payload.get("exit_code") or 0), 0)
                self.assertIn(
                    "python3 -m jarvis.cli interrupts acknowledge int_gate_status_critical_1 --actor operator",
                    list(ci_json_payload.get("acknowledge_commands") or []),
                )
                blocked_steps_ci_json = list(ci_json_payload.get("blocked_steps") or [])
                self.assertGreaterEqual(len(blocked_steps_ci_json), 1)
                self.assertEqual(
                    list(blocked_steps_ci_json[0].get("blocking_interrupt_ids") or []),
                    ["int_gate_status_critical_1"],
                )
                self.assertNotIn("gate_rows", ci_json_payload)

                env_ci_summary_path = root / "gate_status_all_env_summary.md"
                args_all_env_ci_summary = argparse.Namespace(**vars(args_all))
                captured_all_env_ci_summary = io.StringIO()
                with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(env_ci_summary_path)}, clear=False):
                    with redirect_stdout(captured_all_env_ci_summary):
                        cmd_plans_gate_status_all(args_all_env_ci_summary)
                payload_all_env_ci_summary = json.loads(captured_all_env_ci_summary.getvalue())
                self.assertEqual(
                    str(payload_all_env_ci_summary.get("ci_summary_path") or ""),
                    str(env_ci_summary_path.resolve()),
                )
                self.assertEqual(str(payload_all_env_ci_summary.get("ci_summary_path_source") or ""), "env")
                self.assertTrue(env_ci_summary_path.exists())
                env_ci_summary_text = env_ci_summary_path.read_text(encoding="utf-8")
                self.assertIn("# plans gate-status-all summary", env_ci_summary_text)
                self.assertIn("## Acknowledge Commands", env_ci_summary_text)

                env_ci_summary_override_path = root / "gate_status_all_env_summary_override.md"
                cli_ci_summary_override_path = root / "gate_status_all_cli_summary_override.md"
                args_all_ci_summary_override = argparse.Namespace(**vars(args_all))
                args_all_ci_summary_override.emit_ci_summary_path = cli_ci_summary_override_path
                captured_all_ci_summary_override = io.StringIO()
                with patch.dict(
                    os.environ,
                    {"GITHUB_STEP_SUMMARY": str(env_ci_summary_override_path)},
                    clear=False,
                ):
                    with redirect_stdout(captured_all_ci_summary_override):
                        cmd_plans_gate_status_all(args_all_ci_summary_override)
                payload_all_ci_summary_override = json.loads(captured_all_ci_summary_override.getvalue())
                self.assertEqual(
                    str(payload_all_ci_summary_override.get("ci_summary_path") or ""),
                    str(cli_ci_summary_override_path.resolve()),
                )
                self.assertEqual(
                    str(payload_all_ci_summary_override.get("ci_summary_path_source") or ""),
                    "cli",
                )
                self.assertTrue(cli_ci_summary_override_path.exists())
                self.assertFalse(env_ci_summary_override_path.exists())

                args_all_fail = argparse.Namespace(**vars(args_all))
                args_all_fail.fail_on_blocked = True
                args_all_fail.output = "json"
                captured_all_fail = io.StringIO()
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stdout(captured_all_fail):
                        cmd_plans_gate_status_all(args_all_fail)
                self.assertEqual(int(cm.exception.code or 0), 2)
                payload_all_fail = json.loads(captured_all_fail.getvalue())
                self.assertTrue(bool(payload_all_fail.get("fail_on_blocked")))
                self.assertGreaterEqual(int(payload_all_fail.get("blocked_step_count") or 0), 1)
                self.assertEqual(int(payload_all_fail.get("exit_code") or 0), 2)
                self.assertTrue(bool(payload_all_fail.get("exit_triggered")))
                self.assertEqual(str(payload_all_fail.get("exit_reason") or ""), "blocked_steps_present")
                self.assertEqual(int(payload_all_fail.get("blocked_exit_code") or 0), 2)
                self.assertTrue(bool(payload_all_fail.get("blocked_exit_triggered")))
                self.assertFalse(bool(payload_all_fail.get("error_exit_triggered")))
                self.assertFalse(bool(payload_all_fail.get("zero_scanned_exit_triggered")))
                self.assertFalse(bool(payload_all_fail.get("zero_evaluated_exit_triggered")))
                self.assertFalse(bool(payload_all_fail.get("empty_ack_commands_exit_triggered")))

                args_all_fail_custom = argparse.Namespace(**vars(args_all))
                args_all_fail_custom.fail_on_blocked = True
                args_all_fail_custom.blocked_exit_code = 7
                captured_all_fail_custom = io.StringIO()
                with self.assertRaises(SystemExit) as cm_custom:
                    with redirect_stdout(captured_all_fail_custom):
                        cmd_plans_gate_status_all(args_all_fail_custom)
                self.assertEqual(int(cm_custom.exception.code or 0), 7)
                payload_all_fail_custom = json.loads(captured_all_fail_custom.getvalue())
                self.assertEqual(int(payload_all_fail_custom.get("blocked_exit_code") or 0), 7)
                self.assertEqual(int(payload_all_fail_custom.get("exit_code") or 0), 7)
                self.assertTrue(bool(payload_all_fail_custom.get("exit_triggered")))

                args_all_empty_ack_nonfatal = argparse.Namespace(**vars(args_all))
                captured_all_empty_ack_nonfatal = io.StringIO()
                with patch(
                    "jarvis.cli._build_gate_status_payload",
                    side_effect=lambda *_a, **kw: {
                        "plan_id": str(kw.get("plan_id") or ""),
                        "step_id": str(kw.get("step_id") or ""),
                        "gate_mode": "enabled",
                        "blocked": True,
                        "blocking_interrupt_count": 1,
                        "blocking_interrupt_ids": ["int_missing_ack_1"],
                        "acknowledge_commands": [],
                        "blocking_alerts": [],
                        "next_action": "Acknowledge each blocking interrupt and rerun plans promote-ready.",
                        "critical_drift_gate_status": {"mode": "enabled", "blocked": True},
                    },
                ):
                    with redirect_stdout(captured_all_empty_ack_nonfatal):
                        cmd_plans_gate_status_all(args_all_empty_ack_nonfatal)
                payload_all_empty_ack_nonfatal = json.loads(captured_all_empty_ack_nonfatal.getvalue())
                self.assertGreaterEqual(int(payload_all_empty_ack_nonfatal.get("blocked_step_count") or 0), 1)
                self.assertEqual(list(payload_all_empty_ack_nonfatal.get("acknowledge_commands") or []), [])
                self.assertEqual(int(payload_all_empty_ack_nonfatal.get("exit_code") or 0), 0)
                self.assertFalse(bool(payload_all_empty_ack_nonfatal.get("exit_triggered")))
                self.assertEqual(str(payload_all_empty_ack_nonfatal.get("exit_reason") or ""), "none")
                self.assertFalse(bool(payload_all_empty_ack_nonfatal.get("empty_ack_commands_exit_triggered")))

                args_all_empty_ack_fatal = argparse.Namespace(**vars(args_all))
                args_all_empty_ack_fatal.fail_on_empty_ack_commands = True
                captured_all_empty_ack_fatal = io.StringIO()
                with patch(
                    "jarvis.cli._build_gate_status_payload",
                    side_effect=lambda *_a, **kw: {
                        "plan_id": str(kw.get("plan_id") or ""),
                        "step_id": str(kw.get("step_id") or ""),
                        "gate_mode": "enabled",
                        "blocked": True,
                        "blocking_interrupt_count": 1,
                        "blocking_interrupt_ids": ["int_missing_ack_1"],
                        "acknowledge_commands": [],
                        "blocking_alerts": [],
                        "next_action": "Acknowledge each blocking interrupt and rerun plans promote-ready.",
                        "critical_drift_gate_status": {"mode": "enabled", "blocked": True},
                    },
                ):
                    with self.assertRaises(SystemExit) as cm_empty_ack:
                        with redirect_stdout(captured_all_empty_ack_fatal):
                            cmd_plans_gate_status_all(args_all_empty_ack_fatal)
                self.assertEqual(int(cm_empty_ack.exception.code or 0), 6)
                payload_all_empty_ack_fatal = json.loads(captured_all_empty_ack_fatal.getvalue())
                self.assertTrue(bool(payload_all_empty_ack_fatal.get("fail_on_empty_ack_commands")))
                self.assertEqual(int(payload_all_empty_ack_fatal.get("empty_ack_commands_exit_code") or 0), 6)
                self.assertEqual(int(payload_all_empty_ack_fatal.get("exit_code") or 0), 6)
                self.assertEqual(str(payload_all_empty_ack_fatal.get("exit_reason") or ""), "empty_ack_commands_missing")
                self.assertTrue(bool(payload_all_empty_ack_fatal.get("empty_ack_commands_exit_triggered")))

                args_all_empty_ack_fatal_custom = argparse.Namespace(**vars(args_all))
                args_all_empty_ack_fatal_custom.fail_on_empty_ack_commands = True
                args_all_empty_ack_fatal_custom.empty_ack_commands_exit_code = 19
                captured_all_empty_ack_fatal_custom = io.StringIO()
                with patch(
                    "jarvis.cli._build_gate_status_payload",
                    side_effect=lambda *_a, **kw: {
                        "plan_id": str(kw.get("plan_id") or ""),
                        "step_id": str(kw.get("step_id") or ""),
                        "gate_mode": "enabled",
                        "blocked": True,
                        "blocking_interrupt_count": 1,
                        "blocking_interrupt_ids": ["int_missing_ack_1"],
                        "acknowledge_commands": [],
                        "blocking_alerts": [],
                        "next_action": "Acknowledge each blocking interrupt and rerun plans promote-ready.",
                        "critical_drift_gate_status": {"mode": "enabled", "blocked": True},
                    },
                ):
                    with self.assertRaises(SystemExit) as cm_empty_ack_custom:
                        with redirect_stdout(captured_all_empty_ack_fatal_custom):
                            cmd_plans_gate_status_all(args_all_empty_ack_fatal_custom)
                self.assertEqual(int(cm_empty_ack_custom.exception.code or 0), 19)
                payload_all_empty_ack_fatal_custom = json.loads(captured_all_empty_ack_fatal_custom.getvalue())
                self.assertEqual(int(payload_all_empty_ack_fatal_custom.get("empty_ack_commands_exit_code") or 0), 19)
                self.assertEqual(int(payload_all_empty_ack_fatal_custom.get("exit_code") or 0), 19)
                self.assertEqual(str(payload_all_empty_ack_fatal_custom.get("exit_reason") or ""), "empty_ack_commands_missing")

                args_all_empty_ack_precedence = argparse.Namespace(**vars(args_all))
                args_all_empty_ack_precedence.fail_on_empty_ack_commands = True
                args_all_empty_ack_precedence.empty_ack_commands_exit_code = 19
                args_all_empty_ack_precedence.fail_on_blocked = True
                args_all_empty_ack_precedence.blocked_exit_code = 7
                captured_all_empty_ack_precedence = io.StringIO()
                with patch(
                    "jarvis.cli._build_gate_status_payload",
                    side_effect=lambda *_a, **kw: {
                        "plan_id": str(kw.get("plan_id") or ""),
                        "step_id": str(kw.get("step_id") or ""),
                        "gate_mode": "enabled",
                        "blocked": True,
                        "blocking_interrupt_count": 1,
                        "blocking_interrupt_ids": ["int_missing_ack_1"],
                        "acknowledge_commands": [],
                        "blocking_alerts": [],
                        "next_action": "Acknowledge each blocking interrupt and rerun plans promote-ready.",
                        "critical_drift_gate_status": {"mode": "enabled", "blocked": True},
                    },
                ):
                    with self.assertRaises(SystemExit) as cm_empty_ack_precedence:
                        with redirect_stdout(captured_all_empty_ack_precedence):
                            cmd_plans_gate_status_all(args_all_empty_ack_precedence)
                self.assertEqual(int(cm_empty_ack_precedence.exception.code or 0), 19)
                payload_all_empty_ack_precedence = json.loads(captured_all_empty_ack_precedence.getvalue())
                self.assertTrue(bool(payload_all_empty_ack_precedence.get("blocked_exit_triggered")))
                self.assertTrue(bool(payload_all_empty_ack_precedence.get("empty_ack_commands_exit_triggered")))
                self.assertEqual(str(payload_all_empty_ack_precedence.get("exit_reason") or ""), "empty_ack_commands_missing")

                args_all_zero_nonfatal = argparse.Namespace(**vars(args_all))
                args_all_zero_nonfatal.provider = "provider-does-not-exist"
                captured_all_zero_nonfatal = io.StringIO()
                with redirect_stdout(captured_all_zero_nonfatal):
                    cmd_plans_gate_status_all(args_all_zero_nonfatal)
                payload_all_zero_nonfatal = json.loads(captured_all_zero_nonfatal.getvalue())
                self.assertEqual(int(payload_all_zero_nonfatal.get("scanned_review_count", -1)), 0)
                self.assertEqual(int(payload_all_zero_nonfatal.get("evaluated_step_count", -1)), 0)
                self.assertEqual(int(payload_all_zero_nonfatal.get("error_count", -1)), 0)
                self.assertEqual(int(payload_all_zero_nonfatal.get("exit_code", -1)), 0)
                self.assertEqual(str(payload_all_zero_nonfatal.get("exit_reason") or ""), "none")
                self.assertFalse(bool(payload_all_zero_nonfatal.get("zero_scanned_exit_triggered")))
                self.assertFalse(bool(payload_all_zero_nonfatal.get("zero_evaluated_exit_triggered")))

                args_all_zero_scanned_fatal = argparse.Namespace(**vars(args_all))
                args_all_zero_scanned_fatal.provider = "provider-does-not-exist"
                args_all_zero_scanned_fatal.fail_on_zero_scanned = True
                captured_all_zero_scanned_fatal = io.StringIO()
                with self.assertRaises(SystemExit) as cm_zero_scanned:
                    with redirect_stdout(captured_all_zero_scanned_fatal):
                        cmd_plans_gate_status_all(args_all_zero_scanned_fatal)
                self.assertEqual(int(cm_zero_scanned.exception.code or 0), 5)
                payload_all_zero_scanned_fatal = json.loads(captured_all_zero_scanned_fatal.getvalue())
                self.assertTrue(bool(payload_all_zero_scanned_fatal.get("fail_on_zero_scanned")))
                self.assertEqual(int(payload_all_zero_scanned_fatal.get("scanned_review_count", -1)), 0)
                self.assertEqual(int(payload_all_zero_scanned_fatal.get("exit_code", -1)), 5)
                self.assertEqual(str(payload_all_zero_scanned_fatal.get("exit_reason") or ""), "zero_scanned_reviews")
                self.assertTrue(bool(payload_all_zero_scanned_fatal.get("zero_scanned_exit_triggered")))
                self.assertFalse(bool(payload_all_zero_scanned_fatal.get("zero_evaluated_exit_triggered")))

                args_all_zero_scanned_fatal_custom = argparse.Namespace(**vars(args_all))
                args_all_zero_scanned_fatal_custom.provider = "provider-does-not-exist"
                args_all_zero_scanned_fatal_custom.fail_on_zero_scanned = True
                args_all_zero_scanned_fatal_custom.zero_scanned_exit_code = 17
                captured_all_zero_scanned_fatal_custom = io.StringIO()
                with self.assertRaises(SystemExit) as cm_zero_scanned_custom:
                    with redirect_stdout(captured_all_zero_scanned_fatal_custom):
                        cmd_plans_gate_status_all(args_all_zero_scanned_fatal_custom)
                self.assertEqual(int(cm_zero_scanned_custom.exception.code or 0), 17)
                payload_all_zero_scanned_fatal_custom = json.loads(captured_all_zero_scanned_fatal_custom.getvalue())
                self.assertEqual(int(payload_all_zero_scanned_fatal_custom.get("zero_scanned_exit_code") or 0), 17)
                self.assertEqual(int(payload_all_zero_scanned_fatal_custom.get("exit_code") or 0), 17)
                self.assertEqual(str(payload_all_zero_scanned_fatal_custom.get("exit_reason") or ""), "zero_scanned_reviews")

                args_all_zero_scanned_precedence = argparse.Namespace(**vars(args_all))
                args_all_zero_scanned_precedence.provider = "provider-does-not-exist"
                args_all_zero_scanned_precedence.fail_on_zero_scanned = True
                args_all_zero_scanned_precedence.zero_scanned_exit_code = 17
                args_all_zero_scanned_precedence.fail_on_zero_evaluated = True
                args_all_zero_scanned_precedence.zero_evaluated_exit_code = 13
                captured_all_zero_scanned_precedence = io.StringIO()
                with self.assertRaises(SystemExit) as cm_zero_scanned_precedence:
                    with redirect_stdout(captured_all_zero_scanned_precedence):
                        cmd_plans_gate_status_all(args_all_zero_scanned_precedence)
                self.assertEqual(int(cm_zero_scanned_precedence.exception.code or 0), 17)
                payload_all_zero_scanned_precedence = json.loads(captured_all_zero_scanned_precedence.getvalue())
                self.assertTrue(bool(payload_all_zero_scanned_precedence.get("zero_scanned_exit_triggered")))
                self.assertFalse(bool(payload_all_zero_scanned_precedence.get("zero_evaluated_exit_triggered")))
                self.assertEqual(str(payload_all_zero_scanned_precedence.get("exit_reason") or ""), "zero_scanned_reviews")

                args_all_zero_evaluated_fatal = argparse.Namespace(**vars(args_all))
                args_all_zero_evaluated_fatal.fail_on_zero_evaluated = True
                captured_all_zero_evaluated_fatal = io.StringIO()
                with patch("jarvis.cli._build_gate_status_payload", side_effect=RuntimeError("gate_eval_failed")):
                    with self.assertRaises(SystemExit) as cm_zero_evaluated:
                        with redirect_stdout(captured_all_zero_evaluated_fatal):
                            cmd_plans_gate_status_all(args_all_zero_evaluated_fatal)
                self.assertEqual(int(cm_zero_evaluated.exception.code or 0), 4)
                payload_all_zero_evaluated_fatal = json.loads(captured_all_zero_evaluated_fatal.getvalue())
                self.assertTrue(bool(payload_all_zero_evaluated_fatal.get("fail_on_zero_evaluated")))
                self.assertGreaterEqual(int(payload_all_zero_evaluated_fatal.get("scanned_review_count") or 0), 1)
                self.assertEqual(int(payload_all_zero_evaluated_fatal.get("evaluated_step_count", -1)), 0)
                self.assertGreaterEqual(int(payload_all_zero_evaluated_fatal.get("error_count") or 0), 1)
                self.assertEqual(int(payload_all_zero_evaluated_fatal.get("exit_code", -1)), 4)
                self.assertEqual(str(payload_all_zero_evaluated_fatal.get("exit_reason") or ""), "zero_evaluated_steps")
                self.assertFalse(bool(payload_all_zero_evaluated_fatal.get("zero_scanned_exit_triggered")))
                self.assertTrue(bool(payload_all_zero_evaluated_fatal.get("zero_evaluated_exit_triggered")))

                args_all_zero_evaluated_fatal_custom = argparse.Namespace(**vars(args_all))
                args_all_zero_evaluated_fatal_custom.fail_on_zero_evaluated = True
                args_all_zero_evaluated_fatal_custom.zero_evaluated_exit_code = 13
                captured_all_zero_evaluated_fatal_custom = io.StringIO()
                with patch("jarvis.cli._build_gate_status_payload", side_effect=RuntimeError("gate_eval_failed")):
                    with self.assertRaises(SystemExit) as cm_zero_evaluated_custom:
                        with redirect_stdout(captured_all_zero_evaluated_fatal_custom):
                            cmd_plans_gate_status_all(args_all_zero_evaluated_fatal_custom)
                self.assertEqual(int(cm_zero_evaluated_custom.exception.code or 0), 13)
                payload_all_zero_evaluated_fatal_custom = json.loads(captured_all_zero_evaluated_fatal_custom.getvalue())
                self.assertEqual(int(payload_all_zero_evaluated_fatal_custom.get("zero_evaluated_exit_code") or 0), 13)
                self.assertEqual(int(payload_all_zero_evaluated_fatal_custom.get("exit_code") or 0), 13)
                self.assertEqual(str(payload_all_zero_evaluated_fatal_custom.get("exit_reason") or ""), "zero_evaluated_steps")

                args_all_error_nonfatal = argparse.Namespace(**vars(args_all))
                args_all_error_nonfatal.output = "json"
                captured_all_error_nonfatal = io.StringIO()
                with patch("jarvis.cli._build_gate_status_payload", side_effect=RuntimeError("gate_eval_failed")):
                    with redirect_stdout(captured_all_error_nonfatal):
                        cmd_plans_gate_status_all(args_all_error_nonfatal)
                payload_all_error_nonfatal = json.loads(captured_all_error_nonfatal.getvalue())
                self.assertEqual(int(payload_all_error_nonfatal.get("evaluated_step_count") or 0), 0)
                self.assertGreaterEqual(int(payload_all_error_nonfatal.get("error_count") or 0), 1)
                self.assertEqual(int(payload_all_error_nonfatal.get("exit_code") or 0), 0)
                self.assertFalse(bool(payload_all_error_nonfatal.get("exit_triggered")))
                self.assertEqual(str(payload_all_error_nonfatal.get("exit_reason") or ""), "none")

                args_all_error_fatal = argparse.Namespace(**vars(args_all))
                args_all_error_fatal.fail_on_errors = True
                args_all_error_fatal.output = "json"
                captured_all_error_fatal = io.StringIO()
                with patch("jarvis.cli._build_gate_status_payload", side_effect=RuntimeError("gate_eval_failed")):
                    with self.assertRaises(SystemExit) as cm_error:
                        with redirect_stdout(captured_all_error_fatal):
                            cmd_plans_gate_status_all(args_all_error_fatal)
                self.assertEqual(int(cm_error.exception.code or 0), 3)
                payload_all_error_fatal = json.loads(captured_all_error_fatal.getvalue())
                self.assertTrue(bool(payload_all_error_fatal.get("fail_on_errors")))
                self.assertGreaterEqual(int(payload_all_error_fatal.get("error_count") or 0), 1)
                self.assertEqual(int(payload_all_error_fatal.get("exit_code") or 0), 3)
                self.assertTrue(bool(payload_all_error_fatal.get("exit_triggered")))
                self.assertEqual(str(payload_all_error_fatal.get("exit_reason") or ""), "errors_present")
                self.assertFalse(bool(payload_all_error_fatal.get("blocked_exit_triggered")))
                self.assertTrue(bool(payload_all_error_fatal.get("error_exit_triggered")))
                self.assertFalse(bool(payload_all_error_fatal.get("zero_evaluated_exit_triggered")))

                args_all_error_fatal_custom = argparse.Namespace(**vars(args_all))
                args_all_error_fatal_custom.fail_on_errors = True
                args_all_error_fatal_custom.error_exit_code = 11
                captured_all_error_fatal_custom = io.StringIO()
                with patch("jarvis.cli._build_gate_status_payload", side_effect=RuntimeError("gate_eval_failed")):
                    with self.assertRaises(SystemExit) as cm_error_custom:
                        with redirect_stdout(captured_all_error_fatal_custom):
                            cmd_plans_gate_status_all(args_all_error_fatal_custom)
                self.assertEqual(int(cm_error_custom.exception.code or 0), 11)
                payload_all_error_fatal_custom = json.loads(captured_all_error_fatal_custom.getvalue())
                self.assertEqual(int(payload_all_error_fatal_custom.get("error_exit_code") or 0), 11)
                self.assertEqual(int(payload_all_error_fatal_custom.get("exit_code") or 0), 11)
                self.assertEqual(str(payload_all_error_fatal_custom.get("exit_reason") or ""), "errors_present")

                args_all_error_zero_precedence = argparse.Namespace(**vars(args_all))
                args_all_error_zero_precedence.fail_on_errors = True
                args_all_error_zero_precedence.error_exit_code = 11
                args_all_error_zero_precedence.fail_on_zero_evaluated = True
                args_all_error_zero_precedence.zero_evaluated_exit_code = 13
                captured_all_error_zero_precedence = io.StringIO()
                with patch("jarvis.cli._build_gate_status_payload", side_effect=RuntimeError("gate_eval_failed")):
                    with self.assertRaises(SystemExit) as cm_error_zero_precedence:
                        with redirect_stdout(captured_all_error_zero_precedence):
                            cmd_plans_gate_status_all(args_all_error_zero_precedence)
                self.assertEqual(int(cm_error_zero_precedence.exception.code or 0), 11)
                payload_all_error_zero_precedence = json.loads(captured_all_error_zero_precedence.getvalue())
                self.assertTrue(bool(payload_all_error_zero_precedence.get("error_exit_triggered")))
                self.assertTrue(bool(payload_all_error_zero_precedence.get("zero_evaluated_exit_triggered")))
                self.assertEqual(str(payload_all_error_zero_precedence.get("exit_reason") or ""), "errors_present")

                args_all.output = "text"
                captured_all_text = io.StringIO()
                with redirect_stdout(captured_all_text):
                    cmd_plans_gate_status_all(args_all)
                payload_all_text = captured_all_text.getvalue()
                self.assertIn("only_blocked: no", payload_all_text)
                self.assertIn("fail_on_blocked: no", payload_all_text)
                self.assertIn("fail_on_errors: no", payload_all_text)
                self.assertIn("fail_on_zero_scanned: no", payload_all_text)
                self.assertIn("fail_on_zero_evaluated: no", payload_all_text)
                self.assertIn("fail_on_empty_ack_commands: no", payload_all_text)
                self.assertIn("blocked_exit_code: 2", payload_all_text)
                self.assertIn("error_exit_code: 3", payload_all_text)
                self.assertIn("zero_scanned_exit_code: 5", payload_all_text)
                self.assertIn("zero_evaluated_exit_code: 4", payload_all_text)
                self.assertIn("empty_ack_commands_exit_code: 6", payload_all_text)
                self.assertIn("blocked_step_count: 1", payload_all_text)
                self.assertIn("error_count: 0", payload_all_text)
                self.assertIn("int_gate_status_critical_1", payload_all_text)
                self.assertIn(
                    "python3 -m jarvis.cli interrupts acknowledge int_gate_status_critical_1 --actor operator",
                    payload_all_text,
                )

                args.enforce_critical_drift_gate = False
                args.output = "json"
                captured_disabled = io.StringIO()
                with redirect_stdout(captured_disabled):
                    cmd_plans_gate_status(args)
                disabled_payload = json.loads(captured_disabled.getvalue())
                self.assertFalse(bool(disabled_payload.get("blocked")))
                self.assertEqual(str(disabled_payload.get("gate_mode") or ""), "disabled")
                self.assertEqual(list(disabled_payload.get("blocking_interrupt_ids") or []), [])

                args_all.enforce_critical_drift_gate = False
                args_all.only_blocked = True
                args_all.fail_on_blocked = True
                args_all.fail_on_errors = True
                args_all.fail_on_zero_scanned = True
                args_all.fail_on_zero_evaluated = True
                args_all.fail_on_empty_ack_commands = True
                args_all.output = "json"
                captured_all_disabled = io.StringIO()
                with redirect_stdout(captured_all_disabled):
                    cmd_plans_gate_status_all(args_all)
                payload_all_disabled = json.loads(captured_all_disabled.getvalue())
                self.assertTrue(bool(payload_all_disabled.get("only_blocked")))
                self.assertTrue(bool(payload_all_disabled.get("fail_on_blocked")))
                self.assertTrue(bool(payload_all_disabled.get("fail_on_errors")))
                self.assertTrue(bool(payload_all_disabled.get("fail_on_zero_scanned")))
                self.assertTrue(bool(payload_all_disabled.get("fail_on_zero_evaluated")))
                self.assertTrue(bool(payload_all_disabled.get("fail_on_empty_ack_commands")))
                self.assertEqual(int(payload_all_disabled.get("blocked_step_count") or 0), 0)
                self.assertEqual(list(payload_all_disabled.get("acknowledge_commands") or []), [])
                self.assertEqual(int(payload_all_disabled.get("visible_step_count", -1)), 0)
                self.assertEqual(list(payload_all_disabled.get("gate_rows") or []), [])
                self.assertEqual(int(payload_all_disabled.get("blocked_exit_code") or 0), 2)
                self.assertEqual(int(payload_all_disabled.get("error_exit_code") or 0), 3)
                self.assertEqual(int(payload_all_disabled.get("zero_scanned_exit_code") or 0), 5)
                self.assertEqual(int(payload_all_disabled.get("zero_evaluated_exit_code") or 0), 4)
                self.assertEqual(int(payload_all_disabled.get("empty_ack_commands_exit_code") or 0), 6)
                self.assertEqual(int(payload_all_disabled.get("error_count") or 0), 0)
                self.assertEqual(int(payload_all_disabled.get("exit_code", -1)), 0)
                self.assertFalse(bool(payload_all_disabled.get("exit_triggered")))
                self.assertFalse(bool(payload_all_disabled.get("zero_scanned_exit_triggered")))
                self.assertFalse(bool(payload_all_disabled.get("zero_evaluated_exit_triggered")))
                self.assertFalse(bool(payload_all_disabled.get("empty_ack_commands_exit_triggered")))
                self.assertEqual(str(payload_all_disabled.get("exit_reason") or ""), "none")
            finally:
                runtime.close()
                shutil.rmtree(remote, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
