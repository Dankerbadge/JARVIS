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
from jarvis.state_index import (
    latest_merge_outcome_key,
    latest_requested_reviewers_key,
    latest_review_artifact_key,
    latest_review_comments_key,
    latest_review_status_key,
    latest_review_summary_key,
    latest_timeline_cursor_key,
)


def _git(cwd: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


class _FakeProvider:
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
            number="19",
            title=title,
            body_markdown=body_markdown,
            web_url="https://example.test/acme/zenith/reviews/19",
            api_url="https://api.example.test/reviews/19",
            base_branch=base_branch,
            head_branch=head_branch,
            head_sha=head_sha,
            state="open",
            draft=draft,
            labels=tuple(labels),
            reviewers=tuple(reviewers),
            status=ReviewStatusSnapshot(
                review_state="open",
                checks_state="pending",
                blocking_contexts=("unit-tests",),
                head_sha=head_sha,
                web_url="https://example.test/acme/zenith/reviews/19",
                synced_at=utc_now_iso(),
            ),
            feedback=ReviewFeedbackSnapshot(
                requested_reviewers=tuple(reviewers),
                reviews=(),
                issue_comments=(),
                review_comments=(),
                timeline_events=(),
                timeline_cursor="10",
                review_summary={"decision": "none", "total_reviews": 0},
                merge_outcome=None,
                required_checks=("unit-tests",),
                required_checks_configured=True,
                synced_at=utc_now_iso(),
            ),
            metadata=dict(metadata or {}),
        )

    def sync_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact:
        return artifact.with_updates(
            status=ReviewStatusSnapshot(
                review_state="open",
                checks_state="success",
                blocking_contexts=(),
                head_sha=artifact.head_sha,
                web_url=artifact.web_url,
                synced_at=utc_now_iso(),
            ).to_dict(),
            feedback=ReviewFeedbackSnapshot(
                requested_reviewers=tuple(artifact.reviewers),
                reviews=(
                    {
                        "id": 1,
                        "state": "approved",
                        "user": "alice",
                        "submitted_at": utc_now_iso(),
                    },
                ),
                issue_comments=(
                    {
                        "id": 2,
                        "user": "bob",
                        "body": "ship it",
                        "created_at": utc_now_iso(),
                    },
                ),
                review_comments=(
                    {
                        "id": 3,
                        "user": "carol",
                        "path": "ui/zenith_ui.txt",
                        "body": "looks good",
                        "created_at": utc_now_iso(),
                    },
                ),
                timeline_events=(
                    {
                        "id": 100,
                        "event": "reviewed",
                        "created_at": utc_now_iso(),
                        "actor": "alice",
                    },
                ),
                timeline_cursor="100",
                review_summary={
                    "decision": "approved",
                    "approved_count": 1,
                    "changes_requested_count": 0,
                    "total_reviews": 1,
                },
                merge_outcome="approved",
                required_checks=("unit-tests",),
                required_checks_configured=True,
                synced_at=utc_now_iso(),
            ),
            updated_at=utc_now_iso(),
        )


class RuntimeReviewSyncTests(unittest.TestCase):
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

    def test_publish_open_and_sync_review(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, remote = self._init_repo_with_remote(root)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                review_service=ReviewService({"fake": _FakeProvider()}),
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
                approval_id = prepared[0]["approval_id"]
                runtime.security.approve(approval_id, approved_by="tester")

                combined = runtime.publish_approved_step(
                    plan.plan_id,
                    step.step_id,
                    remote_name="origin",
                    base_branch="main",
                    draft=True,
                    open_review=True,
                    provider="fake",
                    provider_repo="acme/zenith",
                    reviewers=["alice"],
                )
                self.assertIn("publication", combined)
                self.assertIn("review", combined)
                review = combined["review"]
                self.assertEqual(review["provider"], "fake")

                stored = runtime.security.find_provider_review(plan_id=plan.plan_id, step_id=step.step_id)
                self.assertIsNotNone(stored)
                synced = runtime.sync_provider_review(plan.plan_id, step.step_id)
                self.assertEqual(synced["status"]["checks_state"], "success")

                artifact_state = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=latest_review_artifact_key("zenith", review["head_branch"]),
                )
                status_state = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=latest_review_status_key("zenith", review["head_branch"]),
                )
                reviewers_state = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=latest_requested_reviewers_key("zenith", review["head_branch"]),
                )
                summary_state = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=latest_review_summary_key("zenith", review["head_branch"]),
                )
                comments_state = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=latest_review_comments_key("zenith", review["head_branch"]),
                )
                timeline_state = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=latest_timeline_cursor_key("zenith", review["head_branch"]),
                )
                merge_state = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=latest_merge_outcome_key("zenith", review["head_branch"]),
                )
                self.assertIsNotNone(artifact_state)
                self.assertIsNotNone(status_state)
                self.assertIsNotNone(reviewers_state)
                self.assertIsNotNone(summary_state)
                self.assertIsNotNone(comments_state)
                self.assertIsNotNone(timeline_state)
                self.assertIsNotNone(merge_state)
                self.assertEqual(status_state["value"]["checks_state"], "success")
                self.assertEqual(summary_state["value"]["review_summary"]["decision"], "approved")
                self.assertEqual(merge_state["value"]["merge_outcome"], "approved")
                outcomes = runtime.plan_repo.list_recent_outcomes("zenith", review["head_branch"])
                self.assertEqual(outcomes[0]["status"], "success")

                feedback_row = runtime.security.find_review_feedback(
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                )
                self.assertIsNotNone(feedback_row)
                self.assertEqual(feedback_row["review_summary"]["decision"], "approved")

                timeline_row = runtime.security.find_review_timeline_cursor(
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                )
                self.assertIsNotNone(timeline_row)
                self.assertEqual(timeline_row["timeline_cursor"], "100")

                merge_row = runtime.security.find_merge_outcome(
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                )
                self.assertIsNotNone(merge_row)
                self.assertEqual(merge_row["merge_outcome"], "approved")

                review_summary = runtime.get_review_summary(plan.plan_id, step.step_id)
                self.assertEqual(
                    review_summary["hosted_feedback"]["review_summary"]["decision"],
                    "approved",
                )
                review_comments = runtime.get_review_comments(plan.plan_id, step.step_id)
                self.assertEqual(review_comments["issue_comment_count"], 1)
                self.assertEqual(review_comments["review_comment_count"], 1)
            finally:
                runtime.close()
                shutil.rmtree(remote, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
