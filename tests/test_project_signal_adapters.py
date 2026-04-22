from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.providers.base import ProviderReviewArtifact, ReviewFeedbackSnapshot, ReviewStatusSnapshot
from jarvis.runtime import JarvisRuntime


class ProjectSignalAdapterTests(unittest.TestCase):
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

    def test_ingest_project_signals_from_plan_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-ci-1",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="ci failed",
                )
                bridged = runtime.ingest_project_signals_from_plan_outcomes(
                    project_id="alpha",
                    plan_id="plan-ci-1",
                    limit=20,
                )
                self.assertEqual(int(bridged.get("signals_count") or 0), 1)
                signal = bridged["signals"][0]
                self.assertEqual(str(signal.get("type")), "ci_failed")

                actions = runtime.list_project_actions(project_id="alpha", limit=20)
                self.assertTrue(any(str(item.get("action_type")) == "fix_ci" for item in actions))
            finally:
                runtime.close()

    def test_ingest_project_signals_from_review_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                plan_id = "plan-review-1"
                step_id = "step-review-1"
                approval_id = "apr-review-1"
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-77",
                    number="77",
                    title="Fix CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/77",
                    api_url="https://api.example.test/pr/77",
                    base_branch="main",
                    head_branch="feature/fix-ci",
                    head_sha="deadbeef",
                    state="open",
                    draft=False,
                    reviewers=("alice",),
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef",
                        web_url="https://example.test/pr/77",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested", "total_reviews": 1},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": plan_id},
                )
                runtime.security.store_provider_review(
                    approval_id=approval_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    provider="fake",
                    repo_slug="acme/zenith",
                    review=artifact.to_dict(),
                )
                runtime.security.store_review_artifact(
                    approval_id=approval_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="77",
                    branch="feature/fix-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_review_feedback(
                    approval_id=approval_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="77",
                    branch="feature/fix-ci",
                    feedback=(artifact.feedback.to_dict() if artifact.feedback else {}),
                    review_summary={"decision": "changes_requested", "total_reviews": 1},
                    comments={"issue_comments": [], "review_comments": []},
                    requested_reviewers=["alice"],
                )
                bridged = runtime.ingest_project_signals_from_review_artifacts(
                    project_id="alpha",
                    plan_id=plan_id,
                    step_id=step_id,
                )
                self.assertGreaterEqual(int(bridged.get("signals_count") or 0), 3)
                signal_types = {str(item.get("type")) for item in list(bridged.get("signals") or [])}
                self.assertIn("pull_request_updated", signal_types)
                self.assertIn("pull_request_review_changes", signal_types)
                self.assertIn("ci_failed", signal_types)

                actions = runtime.list_project_actions(project_id="alpha", limit=50)
                action_types = {str(item.get("action_type")) for item in actions}
                self.assertIn("address_review_feedback", action_types)
                self.assertIn("fix_ci", action_types)
            finally:
                runtime.close()

    def test_bulk_backfill_project_signals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-ci-2",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="ci failed again",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-88",
                    number="88",
                    title="Fix CI 2",
                    body_markdown="Body",
                    web_url="https://example.test/pr/88",
                    api_url="https://api.example.test/pr/88",
                    base_branch="main",
                    head_branch="feature/fix-ci-2",
                    head_sha="deadbeef88",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef88",
                        web_url="https://example.test/pr/88",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-review-2"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-backfill-1",
                    plan_id="plan-review-2",
                    step_id="step-review-2",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="88",
                    branch="feature/fix-ci-2",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-backfill-2",
                    plan_id="plan-review-2",
                    step_id="step-review-2",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="88",
                    branch="feature/fix-ci-2",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )

                backfill = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=True,
                    include_merge_outcomes=True,
                )
                self.assertGreaterEqual(int(backfill.get("signals_count") or 0), 3)
                signals = list(backfill.get("signals") or [])
                signal_types = {str((item.get("signal") or {}).get("type") or "") for item in signals}
                self.assertIn("ci_failed", signal_types)
                self.assertIn("pull_request_updated", signal_types)
                self.assertIn("pull_request_review_changes", signal_types)

                second = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=True,
                    include_merge_outcomes=True,
                )
                self.assertEqual(int(second.get("signals_count") or 0), 0)
                self.assertGreaterEqual(int(second.get("skipped_existing_count") or 0), 1)

                rerun_all = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=True,
                    include_merge_outcomes=True,
                    skip_seen=False,
                )
                self.assertGreaterEqual(int(rerun_all.get("signals_count") or 0), 3)
            finally:
                runtime.close()

    def test_bulk_backfill_project_signals_since_cursor_filters_sources(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                old_outcome_at = "2026-04-17T00:00:00+00:00"
                new_outcome_at = "2026-04-17T00:10:00+00:00"
                old_review_at = "2026-04-17T00:03:00+00:00"
                new_review_at = "2026-04-17T00:11:00+00:00"
                old_merge_at = "2026-04-17T00:04:00+00:00"
                new_merge_at = "2026-04-17T00:12:00+00:00"
                since = "2026-04-17T00:05:00+00:00"

                runtime.plan_repo.record_outcome(
                    plan_id="plan-outcome-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="old ci failure",
                    recorded_at=old_outcome_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-outcome-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="new ci failure",
                    recorded_at=new_outcome_at,
                )

                runtime.security.store_review_artifact(
                    approval_id="apr-review-old",
                    plan_id="plan-review-old",
                    step_id="step-review-old",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="91",
                    branch="feature/old",
                    artifact={"status": {"checks_state": "failure"}},
                )
                runtime.security.conn.execute(
                    "UPDATE review_artifacts SET created_at = ?, updated_at = ? WHERE approval_id = ?",
                    (old_review_at, old_review_at, "apr-review-old"),
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-review-new",
                    plan_id="plan-review-new",
                    step_id="step-review-new",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="92",
                    branch="feature/new",
                    artifact={"status": {"checks_state": "failure"}},
                )
                runtime.security.conn.execute(
                    "UPDATE review_artifacts SET created_at = ?, updated_at = ? WHERE approval_id = ?",
                    (new_review_at, new_review_at, "apr-review-new"),
                )

                runtime.security.store_merge_outcome(
                    approval_id="apr-merge-old",
                    plan_id="plan-merge-old",
                    step_id="step-merge-old",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="91",
                    branch="feature/old",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
                runtime.security.conn.execute(
                    "UPDATE merge_outcomes SET created_at = ?, updated_at = ? WHERE approval_id = ?",
                    (old_merge_at, old_merge_at, "apr-merge-old"),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-merge-new",
                    plan_id="plan-merge-new",
                    step_id="step-merge-new",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="92",
                    branch="feature/new",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
                runtime.security.conn.execute(
                    "UPDATE merge_outcomes SET created_at = ?, updated_at = ? WHERE approval_id = ?",
                    (new_merge_at, new_merge_at, "apr-merge-new"),
                )
                runtime.security.conn.commit()

                backfill = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=100,
                    include_outcomes=True,
                    include_review_artifacts=True,
                    include_merge_outcomes=True,
                    since_updated_at=since,
                )
                self.assertEqual(int(backfill.get("signals_count") or 0), 4)

                plan_ids = {
                    str((item.get("signal") or {}).get("plan_id") or "")
                    for item in list(backfill.get("signals") or [])
                }
                self.assertNotIn("plan-outcome-old", plan_ids)
                self.assertNotIn("plan-review-old", plan_ids)
                self.assertNotIn("plan-merge-old", plan_ids)
                self.assertIn("plan-outcome-new", plan_ids)
                self.assertIn("plan-review-new", plan_ids)
                self.assertIn("plan-merge-new", plan_ids)

                source_cursors = dict(backfill.get("source_cursors") or {})
                self.assertEqual(
                    str((source_cursors.get("plan_outcomes") or {}).get("since") or ""),
                    since,
                )
                self.assertEqual(
                    str((source_cursors.get("plan_outcomes") or {}).get("next_since") or ""),
                    new_outcome_at,
                )
                self.assertEqual(
                    str((source_cursors.get("review_artifacts") or {}).get("next_since") or ""),
                    new_review_at,
                )
                self.assertEqual(
                    str((source_cursors.get("merge_outcomes") or {}).get("next_since") or ""),
                    new_merge_at,
                )
                self.assertEqual(str(backfill.get("next_since_updated_at") or ""), new_merge_at)

                second = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=100,
                    include_outcomes=True,
                    include_review_artifacts=True,
                    include_merge_outcomes=True,
                    since_updated_at=since,
                )
                self.assertEqual(int(second.get("signals_count") or 0), 0)
                self.assertGreaterEqual(int(second.get("skipped_existing_count") or 0), 1)
            finally:
                runtime.close()

    def test_backfill_cursor_profile_persistence_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                outcome_at = "2026-04-18T01:02:03+00:00"
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cursor-1",
                    repo_id="alpha",
                    branch="main",
                    status="success",
                    touched_paths=["service.py"],
                    summary="cursor seed",
                    recorded_at=outcome_at,
                )
                backfill = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                )
                self.assertEqual(str(backfill.get("next_since_updated_at") or ""), outcome_at)

                saved = runtime.save_project_backfill_cursors_from_result(
                    project_id="alpha",
                    profile_key="nightly",
                    backfill_result=backfill,
                    actor="test",
                )
                self.assertEqual(str(saved.get("profile_key") or ""), "nightly")
                self.assertEqual(str(saved.get("next_since_updated_at") or ""), outcome_at)
                saved_sources = dict(saved.get("source_cursors") or {})
                self.assertEqual(
                    str((saved_sources.get("plan_outcomes") or {}).get("next_since") or ""),
                    outcome_at,
                )
                self.assertEqual(
                    str((((saved_sources.get("plan_outcomes") or {}).get("metadata") or {}).get("actor")) or ""),
                    "test",
                )

                fetched = runtime.get_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                )
                self.assertEqual(str(fetched.get("next_since_updated_at") or ""), outcome_at)

                merge_cursor = "2026-04-18T02:30:00+00:00"
                updated = runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"merge_outcomes": {"next_since": merge_cursor, "fetched_count": 3}},
                    actor="test",
                )
                updated_sources = dict(updated.get("source_cursors") or {})
                self.assertEqual(
                    str((updated_sources.get("merge_outcomes") or {}).get("next_since") or ""),
                    merge_cursor,
                )
                self.assertEqual(
                    int((((updated_sources.get("merge_outcomes") or {}).get("metadata") or {}).get("fetched_count")) or 0),
                    3,
                )
                self.assertEqual(
                    str((((updated_sources.get("merge_outcomes") or {}).get("metadata") or {}).get("actor")) or ""),
                    "test",
                )
            finally:
                runtime.close()

    def test_backfill_project_signals_can_load_since_defaults_from_cursor_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                old_at = "2026-04-18T03:00:00+00:00"
                new_at = "2026-04-18T03:10:00+00:00"
                profile_since = "2026-04-18T03:05:00+00:00"

                runtime.plan_repo.record_outcome(
                    plan_id="plan-profile-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="old outcome",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-profile-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="new outcome",
                    recorded_at=new_at,
                )

                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": profile_since}},
                    actor="test",
                )

                preview = runtime.preview_project_backfill_cursor_inputs(
                    project_id="alpha",
                    load_since_from_cursor_profile=True,
                    cursor_profile_key="nightly",
                )
                self.assertEqual(
                    str(((preview.get("effective_since") or {}).get("plan_outcomes")) or ""),
                    profile_since,
                )
                self.assertTrue(bool(((preview.get("cursor_profile") or {}).get("loaded"))))

                backfill = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=100,
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    load_since_from_cursor_profile=True,
                    cursor_profile_key="nightly",
                )
                self.assertEqual(int(backfill.get("signals_count") or 0), 1)
                plan_ids = {
                    str((item.get("signal") or {}).get("plan_id") or "")
                    for item in list(backfill.get("signals") or [])
                }
                self.assertNotIn("plan-profile-old", plan_ids)
                self.assertIn("plan-profile-new", plan_ids)
                self.assertEqual(
                    str(((backfill.get("effective_since") or {}).get("plan_outcomes")) or ""),
                    profile_since,
                )
                cursor_profile = dict(backfill.get("cursor_profile") or {})
                self.assertTrue(bool(cursor_profile.get("loaded")))
                self.assertEqual(str(cursor_profile.get("profile_key") or ""), "nightly")
                defaults_applied = dict(cursor_profile.get("defaults_applied") or {})
                self.assertTrue(bool(defaults_applied.get("plan_outcomes")))
                resolution_source = dict(cursor_profile.get("resolution_source") or {})
                self.assertEqual(str(resolution_source.get("plan_outcomes") or ""), "profile.source")
                self.assertEqual(str(resolution_source.get("global") or ""), "none")
            finally:
                runtime.close()

    def test_run_project_backfill_with_cursor_profile_persists_and_reuses_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                old_at = "2026-04-18T04:00:00+00:00"
                new_at = "2026-04-18T04:15:00+00:00"
                seed_since = "2026-04-18T04:05:00+00:00"

                runtime.plan_repo.record_outcome(
                    plan_id="plan-atomic-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="atomic old",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-atomic-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="atomic new",
                    recorded_at=new_at,
                )

                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": seed_since}},
                    actor="test",
                )

                first = runtime.run_project_backfill_with_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    load_since_from_cursor_profile=True,
                )
                backfill_first = dict(first.get("backfill") or {})
                self.assertEqual(int(backfill_first.get("signals_count") or 0), 1)
                self.assertEqual(
                    str(((backfill_first.get("effective_since") or {}).get("plan_outcomes")) or ""),
                    seed_since,
                )
                first_resolution = dict(((backfill_first.get("cursor_profile") or {}).get("resolution_source")) or {})
                self.assertEqual(str(first_resolution.get("plan_outcomes") or ""), "profile.source")
                first_plan_ids = {
                    str((item.get("signal") or {}).get("plan_id") or "")
                    for item in list(backfill_first.get("signals") or [])
                }
                self.assertNotIn("plan-atomic-old", first_plan_ids)
                self.assertIn("plan-atomic-new", first_plan_ids)

                persisted_first = dict(first.get("cursor_profile") or {})
                persisted_sources = dict(persisted_first.get("source_cursors") or {})
                self.assertEqual(
                    str((persisted_sources.get("plan_outcomes") or {}).get("next_since") or ""),
                    new_at,
                )
                self.assertEqual(
                    str((((persisted_sources.get("plan_outcomes") or {}).get("metadata") or {}).get("actor")) or ""),
                    "test",
                )

                second = runtime.run_project_backfill_with_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    load_since_from_cursor_profile=True,
                )
                backfill_second = dict(second.get("backfill") or {})
                self.assertEqual(int(backfill_second.get("signals_count") or 0), 0)
                self.assertEqual(
                    str(((backfill_second.get("effective_since") or {}).get("plan_outcomes")) or ""),
                    new_at,
                )
                second_resolution = dict(((backfill_second.get("cursor_profile") or {}).get("resolution_source")) or {})
                self.assertEqual(str(second_resolution.get("plan_outcomes") or ""), "profile.source")

                persisted_second = dict(second.get("cursor_profile") or {})
                persisted_second_sources = dict(persisted_second.get("source_cursors") or {})
                self.assertEqual(
                    str((persisted_second_sources.get("plan_outcomes") or {}).get("next_since") or ""),
                    new_at,
                )
            finally:
                runtime.close()

    def test_run_project_backfill_with_cursor_profile_dry_run_does_not_ingest_or_persist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                old_at = "2026-04-18T04:40:00+00:00"
                new_at = "2026-04-18T04:55:00+00:00"
                seed_since = "2026-04-18T04:50:00+00:00"

                runtime.plan_repo.record_outcome(
                    plan_id="plan-dry-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="dry old",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-dry-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="dry new",
                    recorded_at=new_at,
                )

                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": seed_since}},
                    actor="test",
                )

                dry = runtime.run_project_backfill_with_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    load_since_from_cursor_profile=True,
                    dry_run=True,
                )
                self.assertTrue(bool(dry.get("dry_run")))
                self.assertFalse(bool(dry.get("cursor_persisted")))
                dry_backfill = dict(dry.get("backfill") or {})
                self.assertTrue(bool(dry_backfill.get("dry_run")))
                self.assertEqual(int(dry_backfill.get("signals_count") or 0), 1)
                self.assertEqual(int(dry_backfill.get("would_ingest_count") or 0), 1)
                self.assertEqual(int(dry_backfill.get("persisted_marker_count") or 0), 0)
                self.assertEqual(len(list(dry_backfill.get("ingestions") or [])), 0)

                dry_profile = dict(dry.get("cursor_profile") or {})
                dry_sources = dict(dry_profile.get("source_cursors") or {})
                self.assertEqual(
                    str((dry_sources.get("plan_outcomes") or {}).get("next_since") or ""),
                    seed_since,
                )

                real = runtime.run_project_backfill_with_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    limit=50,
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    load_since_from_cursor_profile=True,
                )
                self.assertFalse(bool(real.get("dry_run")))
                self.assertTrue(bool(real.get("cursor_persisted")))
                real_backfill = dict(real.get("backfill") or {})
                self.assertEqual(int(real_backfill.get("signals_count") or 0), 1)
                self.assertEqual(int(real_backfill.get("persisted_marker_count") or 0), 1)
                real_profile = dict(real.get("cursor_profile") or {})
                real_sources = dict(real_profile.get("source_cursors") or {})
                self.assertEqual(
                    str((real_sources.get("plan_outcomes") or {}).get("next_since") or ""),
                    new_at,
                )
            finally:
                runtime.close()

    def test_backfill_project_signals_dry_run_sampling_reports_pre_cap_pool(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-sampling-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="sampling outcome",
                    recorded_at="2026-04-18T05:00:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-99",
                    number="99",
                    title="Sampling CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/99",
                    api_url="https://api.example.test/pr/99",
                    base_branch="main",
                    head_branch="feature/sampling-ci",
                    head_sha="deadbeef99",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef99",
                        web_url="https://example.test/pr/99",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-sampling-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-sampling-1",
                    plan_id="plan-sampling-review",
                    step_id="step-sampling-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="99",
                    branch="feature/sampling-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-sampling-2",
                    plan_id="plan-sampling-review",
                    step_id="step-sampling-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="99",
                    branch="feature/sampling-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )

                backfill = runtime.backfill_project_signals(
                    project_id="alpha",
                    limit=1,
                    include_outcomes=True,
                    include_review_artifacts=True,
                    include_merge_outcomes=True,
                    dry_run=True,
                )
                self.assertTrue(bool(backfill.get("dry_run")))
                self.assertEqual(int(backfill.get("would_ingest_count") or 0), 3)
                self.assertEqual(int(backfill.get("signals_count") or 0), 3)
                self.assertEqual(int(backfill.get("persisted_marker_count") or 0), 0)

                sampling = dict(backfill.get("sampling") or {})
                self.assertEqual(int(sampling.get("candidate_pool_count") or 0), 4)
                self.assertEqual(int(sampling.get("candidate_scan_limit") or 0), 3)
                self.assertEqual(int(sampling.get("candidate_scanned_count") or 0), 3)
                self.assertEqual(int(sampling.get("candidate_unscanned_count") or 0), 1)
                pool_by_source = dict(sampling.get("candidate_pool_by_source") or {})
                scanned_by_source = dict(sampling.get("candidate_scanned_by_source") or {})
                unscanned_by_source = dict(sampling.get("candidate_unscanned_by_source") or {})
                self.assertEqual(int(pool_by_source.get("plan_outcomes") or 0), 1)
                self.assertEqual(int(pool_by_source.get("review_artifacts") or 0), 2)
                self.assertEqual(int(pool_by_source.get("merge_outcomes") or 0), 1)
                self.assertEqual(int(scanned_by_source.get("plan_outcomes") or 0), 1)
                self.assertEqual(int(scanned_by_source.get("review_artifacts") or 0), 1)
                self.assertEqual(int(scanned_by_source.get("merge_outcomes") or 0), 1)
                self.assertEqual(int(unscanned_by_source.get("review_artifacts") or 0), 1)
            finally:
                runtime.close()

    def test_run_project_backfill_with_cursor_profile_summary_compact_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                old_at = "2026-04-18T06:00:00+00:00"
                new_at = "2026-04-18T06:20:00+00:00"
                seed_since = "2026-04-18T06:10:00+00:00"
                runtime.plan_repo.record_outcome(
                    plan_id="plan-summary-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="summary old",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-summary-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="summary new",
                    recorded_at=new_at,
                )
                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": seed_since}},
                    actor="test",
                )

                run = runtime.run_project_backfill_with_cursor_profile_summary(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    limit=5,
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    top_signal_types=3,
                )
                backfill = dict(run.get("backfill") or {})
                self.assertEqual(len(list(backfill.get("signals") or [])), 0)
                self.assertTrue(bool(backfill.get("signals_omitted")))
                self.assertEqual(int(backfill.get("signals_omitted_count") or 0), 1)
                self.assertEqual(len(list(backfill.get("ingestions") or [])), 0)
                self.assertTrue(bool(backfill.get("ingestions_omitted")))
                self.assertEqual(int(backfill.get("ingestions_omitted_count") or 0), 1)

                summary = dict(run.get("summary") or {})
                self.assertFalse(bool(summary.get("dry_run")))
                self.assertTrue(bool(summary.get("cursor_persisted")))
                self.assertEqual(int(summary.get("signals_count") or 0), 1)
                self.assertEqual(int(summary.get("persisted_marker_count") or 0), 1)
                self.assertEqual(int(summary.get("candidate_pool_count") or 0), 1)
                self.assertEqual(int(summary.get("candidate_scan_limit") or 0), 15)
                self.assertEqual(int(summary.get("candidate_scanned_count") or 0), 1)
                self.assertEqual(int(summary.get("candidate_unscanned_count") or 0), 0)
                pool_by_source = dict(summary.get("candidate_pool_by_source") or {})
                scanned_by_source = dict(summary.get("candidate_scanned_by_source") or {})
                unscanned_by_source = dict(summary.get("candidate_unscanned_by_source") or {})
                self.assertEqual(int(pool_by_source.get("plan_outcomes") or 0), 1)
                self.assertEqual(int(scanned_by_source.get("plan_outcomes") or 0), 1)
                self.assertEqual(len(unscanned_by_source), 0)
                source_counts = dict(summary.get("source_counts") or {})
                self.assertEqual(int(source_counts.get("plan_outcomes") or 0), 1)
                signal_type_counts = dict(summary.get("signal_type_counts") or {})
                self.assertEqual(int(signal_type_counts.get("ci_failed") or 0), 1)
                top_signal_types = list(summary.get("top_signal_types") or [])
                self.assertTrue(bool(top_signal_types))
                self.assertEqual(str((top_signal_types[0] or {}).get("type") or ""), "ci_failed")
                self.assertEqual(int((top_signal_types[0] or {}).get("count") or 0), 1)
                movement = dict(summary.get("cursor_movement") or {})
                plan_cursor = dict(movement.get("plan_outcomes") or {})
                self.assertEqual(str(plan_cursor.get("from") or ""), seed_since)
                self.assertEqual(str(plan_cursor.get("to") or ""), new_at)
                self.assertEqual(str(plan_cursor.get("after") or ""), new_at)
                self.assertTrue(bool(plan_cursor.get("changed")))
                self.assertTrue(bool(plan_cursor.get("persisted")))
            finally:
                runtime.close()

    def test_run_project_backfill_with_cursor_profile_summary_dry_run_cursor_movement(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                old_at = "2026-04-18T06:40:00+00:00"
                new_at = "2026-04-18T06:55:00+00:00"
                seed_since = "2026-04-18T06:45:00+00:00"
                runtime.plan_repo.record_outcome(
                    plan_id="plan-summary-dry-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="summary dry old",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-summary-dry-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="summary dry new",
                    recorded_at=new_at,
                )
                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": seed_since}},
                    actor="test",
                )

                run = runtime.run_project_backfill_with_cursor_profile_summary(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    dry_run=True,
                    top_signal_types=1,
                    include_raw_signals=True,
                )
                backfill = dict(run.get("backfill") or {})
                self.assertEqual(len(list(backfill.get("signals") or [])), 1)
                self.assertFalse(bool(backfill.get("signals_omitted")))
                self.assertEqual(int(backfill.get("signals_omitted_count") or 0), 0)
                self.assertEqual(len(list(backfill.get("ingestions") or [])), 0)
                self.assertTrue(bool(backfill.get("ingestions_omitted")))
                self.assertEqual(int(backfill.get("ingestions_omitted_count") or 0), 0)

                summary = dict(run.get("summary") or {})
                self.assertTrue(bool(summary.get("dry_run")))
                self.assertFalse(bool(summary.get("cursor_persisted")))
                self.assertEqual(int(summary.get("signals_count") or 0), 1)
                self.assertEqual(int(summary.get("persisted_marker_count") or 0), 0)
                self.assertEqual(int(summary.get("candidate_pool_count") or 0), 1)
                self.assertEqual(int(summary.get("candidate_scan_limit") or 0), 300)
                self.assertEqual(int(summary.get("candidate_scanned_count") or 0), 1)
                self.assertEqual(int(summary.get("candidate_unscanned_count") or 0), 0)
                pool_by_source = dict(summary.get("candidate_pool_by_source") or {})
                scanned_by_source = dict(summary.get("candidate_scanned_by_source") or {})
                unscanned_by_source = dict(summary.get("candidate_unscanned_by_source") or {})
                self.assertEqual(int(pool_by_source.get("plan_outcomes") or 0), 1)
                self.assertEqual(int(scanned_by_source.get("plan_outcomes") or 0), 1)
                self.assertEqual(len(unscanned_by_source), 0)
                top_signal_types = list(summary.get("top_signal_types") or [])
                self.assertEqual(len(top_signal_types), 1)
                self.assertEqual(str((top_signal_types[0] or {}).get("type") or ""), "ci_failed")
                movement = dict(summary.get("cursor_movement") or {})
                plan_cursor = dict(movement.get("plan_outcomes") or {})
                self.assertEqual(str(plan_cursor.get("from") or ""), seed_since)
                self.assertEqual(str(plan_cursor.get("to") or ""), new_at)
                self.assertEqual(str(plan_cursor.get("after") or ""), seed_since)
                self.assertTrue(bool(plan_cursor.get("changed")))
                self.assertFalse(bool(plan_cursor.get("persisted")))
            finally:
                runtime.close()

    def test_run_project_backfill_with_cursor_profile_summary_include_raw_ingestions_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                old_at = "2026-04-18T07:00:00+00:00"
                new_at = "2026-04-18T07:20:00+00:00"
                seed_since = "2026-04-18T07:10:00+00:00"
                runtime.plan_repo.record_outcome(
                    plan_id="plan-summary-ing-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="summary ing old",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-summary-ing-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="summary ing new",
                    recorded_at=new_at,
                )
                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": seed_since}},
                    actor="test",
                )

                run = runtime.run_project_backfill_with_cursor_profile_summary(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    include_outcomes=True,
                    include_review_artifacts=False,
                    include_merge_outcomes=False,
                    include_raw_ingestions=True,
                )
                backfill = dict(run.get("backfill") or {})
                self.assertEqual(len(list(backfill.get("signals") or [])), 0)
                self.assertTrue(bool(backfill.get("signals_omitted")))
                self.assertEqual(int(backfill.get("signals_omitted_count") or 0), 1)
                self.assertEqual(len(list(backfill.get("ingestions") or [])), 1)
                self.assertFalse(bool(backfill.get("ingestions_omitted")))
                self.assertEqual(int(backfill.get("ingestions_omitted_count") or 0), 0)
            finally:
                runtime.close()

    def test_run_project_backfill_with_cursor_profile_summary_applies_count_caps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cap-1",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cap ci",
                    recorded_at="2026-04-18T09:00:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-199",
                    number="199",
                    title="Cap CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/199",
                    api_url="https://api.example.test/pr/199",
                    base_branch="main",
                    head_branch="feature/cap-ci",
                    head_sha="deadbeef199",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef199",
                        web_url="https://example.test/pr/199",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cap-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cap-1",
                    plan_id="plan-cap-review",
                    step_id="step-cap-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="199",
                    branch="feature/cap-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cap-2",
                    plan_id="plan-cap-review",
                    step_id="step-cap-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="199",
                    branch="feature/cap-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )

                run = runtime.run_project_backfill_with_cursor_profile_summary(
                    project_id="alpha",
                    profile_key="nightly",
                    actor="test",
                    limit=2,
                    include_outcomes=True,
                    include_review_artifacts=True,
                    include_merge_outcomes=True,
                    dry_run=True,
                    include_raw_signals=True,
                    max_source_counts=2,
                    max_signal_type_counts=2,
                )
                summary = dict(run.get("summary") or {})
                source_counts = dict(summary.get("source_counts") or {})
                signal_type_counts = dict(summary.get("signal_type_counts") or {})
                source_meta = dict(summary.get("source_counts_metadata") or {})
                signal_type_meta = dict(summary.get("signal_type_counts_metadata") or {})

                self.assertEqual(len(source_counts), 2)
                self.assertEqual(int(source_meta.get("cap") or 0), 2)
                self.assertEqual(int(source_meta.get("total_keys") or 0), 3)
                self.assertEqual(int(source_meta.get("returned_keys") or 0), 2)
                self.assertEqual(int(source_meta.get("omitted_keys") or 0), 1)

                self.assertEqual(len(signal_type_counts), 2)
                self.assertEqual(int(signal_type_meta.get("cap") or 0), 2)
                self.assertEqual(int(signal_type_meta.get("total_keys") or 0), 3)
                self.assertEqual(int(signal_type_meta.get("returned_keys") or 0), 2)
                self.assertEqual(int(signal_type_meta.get("omitted_keys") or 0), 1)
            finally:
                runtime.close()

    def test_preview_backfill_cursor_inputs_resolution_source_labels(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    next_since_updated_at="2026-04-18T05:00:00+00:00",
                    source_cursors={"plan_outcomes": {"next_since": "2026-04-18T05:10:00+00:00"}},
                    actor="test",
                )
                preview = runtime.preview_project_backfill_cursor_inputs(
                    project_id="alpha",
                    load_since_from_cursor_profile=True,
                    cursor_profile_key="nightly",
                    since_review_artifacts_at="2026-04-18T05:20:00+00:00",
                )
                cursor_profile = dict(preview.get("cursor_profile") or {})
                resolution_source = dict(cursor_profile.get("resolution_source") or {})
                self.assertEqual(str(resolution_source.get("global") or ""), "profile.global")
                self.assertEqual(str(resolution_source.get("plan_outcomes") or ""), "profile.source")
                self.assertEqual(str(resolution_source.get("review_artifacts") or ""), "explicit")
                self.assertEqual(str(resolution_source.get("merge_outcomes") or ""), "global.profile")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
