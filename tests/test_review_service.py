from __future__ import annotations

import unittest

from jarvis.models import utc_now_iso
from jarvis.providers.base import ProviderReviewArtifact, ReviewStatusSnapshot
from jarvis.review_service import ReviewService


class _FakeProvider:
    provider_name = "fake"

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.synced: list[dict] = []
        self.configured: list[dict] = []
        self.promoted: list[dict] = []

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
        self.created.append(
            {
                "repo_slug": repo_slug,
                "title": title,
                "head_branch": head_branch,
                "labels": tuple(labels),
                "reviewers": tuple(reviewers),
                "metadata": dict(metadata or {}),
            }
        )
        return ProviderReviewArtifact(
            provider="fake",
            repo_slug=repo_slug,
            external_id="ext-1",
            number="11",
            title=title,
            body_markdown=body_markdown,
            web_url="https://example.test/review/11",
            api_url="https://api.example.test/reviews/11",
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
                blocking_contexts=("ci",),
                head_sha=head_sha,
                web_url="https://example.test/review/11",
                synced_at=utc_now_iso(),
            ),
            metadata=dict(metadata or {}),
        )

    def sync_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact:
        self.synced.append(artifact.to_dict())
        return artifact.with_updates(
            status=ReviewStatusSnapshot(
                review_state="open",
                checks_state="success",
                blocking_contexts=(),
                head_sha=artifact.head_sha,
                web_url=artifact.web_url,
                synced_at=utc_now_iso(),
            ).to_dict(),
            updated_at=utc_now_iso(),
        )

    def configure_review(
        self,
        artifact: ProviderReviewArtifact,
        *,
        reviewers=None,
        labels=None,
    ) -> ProviderReviewArtifact:
        self.configured.append(
            {
                "reviewers": tuple(reviewers or ()),
                "labels": tuple(labels or ()),
            }
        )
        return artifact.with_updates(
            reviewers=tuple(reviewers or artifact.reviewers),
            labels=tuple(labels or artifact.labels),
            updated_at=utc_now_iso(),
        )

    def mark_ready_for_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact:
        self.promoted.append({"number": artifact.number})
        return artifact.with_updates(draft=False, updated_at=utc_now_iso())


class ReviewServiceTests(unittest.TestCase):
    def test_open_and_sync_delegates_to_provider(self) -> None:
        provider = _FakeProvider()
        service = ReviewService({"fake": provider})
        artifact = service.open_review(
            provider_name="fake",
            repo_slug="acme/zenith",
            title="[JARVIS] Zenith",
            body_markdown="Body",
            head_branch="feature/jarvis",
            base_branch="main",
            head_sha="abc123",
            draft=True,
            labels=("jarvis",),
            reviewers=("alice",),
            metadata={"repo_id": "zenith"},
        )
        self.assertEqual(provider.created[0]["repo_slug"], "acme/zenith")
        self.assertEqual(provider.created[0]["reviewers"], ("alice",))
        synced = service.sync_review(artifact)
        assert synced.status is not None
        self.assertEqual(synced.status.checks_state, "success")
        self.assertEqual(provider.synced[0]["external_id"], "ext-1")
        configured = service.configure_review(
            synced,
            reviewers=("alice", "bob"),
            labels=("jarvis", "needs-review"),
        )
        self.assertEqual(provider.configured[0]["reviewers"], ("alice", "bob"))
        self.assertEqual(tuple(configured.labels), ("jarvis", "needs-review"))
        promoted = service.mark_ready_for_review(configured)
        self.assertFalse(promoted.draft)
        self.assertEqual(provider.promoted[0]["number"], "11")


if __name__ == "__main__":
    unittest.main()
