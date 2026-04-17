from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.models import utc_now_iso
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


if __name__ == "__main__":
    unittest.main()
