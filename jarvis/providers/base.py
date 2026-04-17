from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, Sequence

from ..models import new_id, utc_now_iso


@dataclass(frozen=True)
class ReviewStatusSnapshot:
    review_state: str
    checks_state: str | None = None
    merged: bool = False
    draft: bool = True
    mergeable: bool | None = None
    blocking_contexts: tuple[str, ...] = field(default_factory=tuple)
    head_sha: str = ""
    web_url: str = ""
    synced_at: str = field(default_factory=utc_now_iso)
    provider_updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blocking_contexts"] = list(self.blocking_contexts)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | "ReviewStatusSnapshot") -> "ReviewStatusSnapshot":
        if isinstance(data, ReviewStatusSnapshot):
            return data
        return cls(
            review_state=str(data.get("review_state", "open")),
            checks_state=(str(data["checks_state"]) if data.get("checks_state") is not None else None),
            merged=bool(data.get("merged", False)),
            draft=bool(data.get("draft", True)),
            mergeable=data.get("mergeable"),
            blocking_contexts=tuple(str(item) for item in data.get("blocking_contexts", [])),
            head_sha=str(data.get("head_sha", "")),
            web_url=str(data.get("web_url", "")),
            synced_at=str(data.get("synced_at") or utc_now_iso()),
            provider_updated_at=(
                str(data["provider_updated_at"]) if data.get("provider_updated_at") is not None else None
            ),
        )


@dataclass(frozen=True)
class ReviewFeedbackSnapshot:
    requested_reviewers: tuple[str, ...] = field(default_factory=tuple)
    reviews: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    issue_comments: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    review_comments: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    timeline_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    timeline_cursor: str | None = None
    review_summary: dict[str, Any] = field(default_factory=dict)
    merge_outcome: str | None = None
    required_checks: tuple[str, ...] = field(default_factory=tuple)
    required_checks_configured: bool = False
    synced_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_reviewers": list(self.requested_reviewers),
            "reviews": [dict(item) for item in self.reviews],
            "issue_comments": [dict(item) for item in self.issue_comments],
            "review_comments": [dict(item) for item in self.review_comments],
            "timeline_events": [dict(item) for item in self.timeline_events],
            "timeline_cursor": self.timeline_cursor,
            "review_summary": dict(self.review_summary),
            "merge_outcome": self.merge_outcome,
            "required_checks": list(self.required_checks),
            "required_checks_configured": self.required_checks_configured,
            "synced_at": self.synced_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | "ReviewFeedbackSnapshot") -> "ReviewFeedbackSnapshot":
        if isinstance(data, ReviewFeedbackSnapshot):
            return data
        return cls(
            requested_reviewers=tuple(str(item) for item in data.get("requested_reviewers", [])),
            reviews=tuple(dict(item) for item in data.get("reviews", [])),
            issue_comments=tuple(dict(item) for item in data.get("issue_comments", [])),
            review_comments=tuple(dict(item) for item in data.get("review_comments", [])),
            timeline_events=tuple(dict(item) for item in data.get("timeline_events", [])),
            timeline_cursor=(
                str(data["timeline_cursor"]) if data.get("timeline_cursor") is not None else None
            ),
            review_summary=dict(data.get("review_summary", {})),
            merge_outcome=(str(data["merge_outcome"]) if data.get("merge_outcome") is not None else None),
            required_checks=tuple(str(item) for item in data.get("required_checks", [])),
            required_checks_configured=bool(data.get("required_checks_configured", False)),
            synced_at=str(data.get("synced_at") or utc_now_iso()),
        )

    def with_updates(self, **changes: Any) -> "ReviewFeedbackSnapshot":
        data = self.to_dict()
        data.update(changes)
        return ReviewFeedbackSnapshot.from_dict(data)


@dataclass(frozen=True)
class ProviderReviewArtifact:
    provider: str
    repo_slug: str
    external_id: str
    number: str
    title: str
    body_markdown: str
    web_url: str
    api_url: str
    base_branch: str
    head_branch: str
    head_sha: str
    state: str
    draft: bool = True
    labels: tuple[str, ...] = field(default_factory=tuple)
    reviewers: tuple[str, ...] = field(default_factory=tuple)
    assignees: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    review_local_id: str = field(default_factory=lambda: new_id("rev"))
    status: ReviewStatusSnapshot | None = None
    feedback: ReviewFeedbackSnapshot | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["labels"] = list(self.labels)
        data["reviewers"] = list(self.reviewers)
        data["assignees"] = list(self.assignees)
        data["status"] = self.status.to_dict() if self.status else None
        data["feedback"] = self.feedback.to_dict() if self.feedback else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderReviewArtifact":
        return cls(
            provider=str(data.get("provider", "")),
            repo_slug=str(data.get("repo_slug", "")),
            external_id=str(data.get("external_id", "")),
            number=str(data.get("number", "")),
            title=str(data.get("title", "")),
            body_markdown=str(data.get("body_markdown", "")),
            web_url=str(data.get("web_url", "")),
            api_url=str(data.get("api_url", "")),
            base_branch=str(data.get("base_branch", "")),
            head_branch=str(data.get("head_branch", "")),
            head_sha=str(data.get("head_sha", "")),
            state=str(data.get("state", "open")),
            draft=bool(data.get("draft", True)),
            labels=tuple(str(item) for item in data.get("labels", [])),
            reviewers=tuple(str(item) for item in data.get("reviewers", [])),
            assignees=tuple(str(item) for item in data.get("assignees", [])),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            review_local_id=str(data.get("review_local_id") or new_id("rev")),
            status=ReviewStatusSnapshot.from_dict(data["status"]) if data.get("status") else None,
            feedback=ReviewFeedbackSnapshot.from_dict(data["feedback"]) if data.get("feedback") else None,
            metadata=dict(data.get("metadata", {})),
        )

    def with_updates(self, **changes: Any) -> "ProviderReviewArtifact":
        data = self.to_dict()
        data.update(changes)
        return ProviderReviewArtifact.from_dict(data)


class ProviderReviewClient(Protocol):
    provider_name: str

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
        labels: Sequence[str] = (),
        reviewers: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> ProviderReviewArtifact: ...

    def sync_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact: ...

    def get_pull_request(self, repo_slug: str, pr_number: str) -> dict[str, Any]: ...

    def get_requested_reviewers(self, repo_slug: str, pr_number: str) -> tuple[str, ...]: ...

    def list_reviews(self, repo_slug: str, pr_number: str) -> tuple[dict[str, Any], ...]: ...

    def list_issue_comments(self, repo_slug: str, pr_number: str) -> tuple[dict[str, Any], ...]: ...

    def list_review_comments(self, repo_slug: str, pr_number: str) -> tuple[dict[str, Any], ...]: ...

    def list_timeline_events(
        self,
        repo_slug: str,
        pr_number: str,
        *,
        since_cursor: str | None = None,
    ) -> tuple[tuple[dict[str, Any], ...], str | None]: ...

    def set_labels(
        self,
        repo_slug: str,
        pr_number: str,
        labels: Sequence[str],
    ) -> tuple[str, ...]: ...

    def set_assignees(
        self,
        repo_slug: str,
        pr_number: str,
        assignees: Sequence[str],
    ) -> tuple[str, ...]: ...

    def configure_review(
        self,
        artifact: ProviderReviewArtifact,
        *,
        reviewers: Sequence[str] | None = None,
        labels: Sequence[str] | None = None,
    ) -> ProviderReviewArtifact: ...

    def mark_ready_for_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact: ...
