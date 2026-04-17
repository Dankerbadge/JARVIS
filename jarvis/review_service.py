from __future__ import annotations

from typing import Any
from typing import Mapping, Sequence

from .providers.base import ProviderReviewArtifact, ProviderReviewClient


class ReviewService:
    def __init__(self, providers: Mapping[str, ProviderReviewClient] | None = None) -> None:
        self.providers = dict(providers or {})

    def register(self, client: ProviderReviewClient) -> None:
        self.providers[client.provider_name] = client

    def _provider(self, provider_name: str) -> ProviderReviewClient:
        key = str(provider_name or "").strip().lower()
        client = self.providers.get(key)
        if not client:
            known = ", ".join(sorted(self.providers)) or "none configured"
            raise KeyError(f"Review provider not configured: {provider_name!r} (available: {known})")
        return client

    def open_review(
        self,
        *,
        provider_name: str,
        repo_slug: str,
        title: str,
        body_markdown: str,
        head_branch: str,
        base_branch: str,
        head_sha: str,
        draft: bool,
        labels: Sequence[str] = (),
        reviewers: Sequence[str] = (),
        metadata: dict | None = None,
    ) -> ProviderReviewArtifact:
        provider = self._provider(provider_name)
        return provider.create_review(
            repo_slug=repo_slug,
            title=title,
            body_markdown=body_markdown,
            head_branch=head_branch,
            base_branch=base_branch,
            head_sha=head_sha,
            draft=draft,
            labels=labels,
            reviewers=reviewers,
            metadata=metadata,
        )

    def sync_review(self, review: ProviderReviewArtifact | dict) -> ProviderReviewArtifact:
        artifact = review if isinstance(review, ProviderReviewArtifact) else ProviderReviewArtifact.from_dict(review)
        provider = self._provider(artifact.provider)
        return provider.sync_review(artifact)

    def sync_review_feedback(
        self,
        *,
        repo_id: str,
        pr_number: str,
        branch: str,
        provider_name: str,
        repo_slug: str,
        existing_review: ProviderReviewArtifact | dict | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderReviewArtifact:
        provider = self._provider(provider_name)
        if existing_review is not None:
            artifact = (
                existing_review
                if isinstance(existing_review, ProviderReviewArtifact)
                else ProviderReviewArtifact.from_dict(existing_review)
            )
            return provider.sync_review(artifact)

        pr = provider.get_pull_request(repo_slug, str(pr_number))
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        state = "merged" if bool(pr.get("merged")) else str(pr.get("state", "open"))
        artifact = ProviderReviewArtifact(
            provider=str(provider_name).strip().lower(),
            repo_slug=repo_slug,
            external_id=str(pr.get("id", pr_number)),
            number=str(pr.get("number", pr_number)),
            title=str(pr.get("title") or f"PR #{pr_number}"),
            body_markdown=str(pr.get("body") or ""),
            web_url=str(pr.get("html_url") or ""),
            api_url=str(pr.get("url") or ""),
            base_branch=str(base.get("ref") or branch),
            head_branch=str(head.get("ref") or branch),
            head_sha=str(head.get("sha") or ""),
            state=state,
            draft=bool(pr.get("draft", False)),
            labels=tuple(
                str(item.get("name", "")).strip()
                for item in (pr.get("labels") or [])
                if str(item.get("name", "")).strip()
            ),
            reviewers=provider.get_requested_reviewers(repo_slug, str(pr_number)),
            assignees=tuple(
                str(item.get("login", "")).strip()
                for item in (pr.get("assignees") or [])
                if str(item.get("login", "")).strip()
            ),
            metadata={
                "repo_id": repo_id,
                **dict(metadata or {}),
            },
        )
        return provider.sync_review(artifact)

    def configure_review(
        self,
        review: ProviderReviewArtifact | dict,
        *,
        reviewers: Sequence[str] | None = None,
        labels: Sequence[str] | None = None,
        assignees: Sequence[str] | None = None,
    ) -> ProviderReviewArtifact:
        artifact = review if isinstance(review, ProviderReviewArtifact) else ProviderReviewArtifact.from_dict(review)
        provider = self._provider(artifact.provider)
        if hasattr(provider, "configure_review"):
            configured = provider.configure_review(artifact, reviewers=reviewers, labels=labels)
            if assignees is not None and hasattr(provider, "set_assignees"):
                normalized = provider.set_assignees(configured.repo_slug, configured.number, assignees)
                configured = configured.with_updates(assignees=tuple(normalized))
            return configured

        merged_reviewers = artifact.reviewers if reviewers is None else tuple(
            dict.fromkeys(str(item) for item in reviewers if str(item).strip())
        )
        merged_labels = artifact.labels if labels is None else tuple(
            dict.fromkeys(str(item) for item in labels if str(item).strip())
        )
        merged_assignees = artifact.assignees if assignees is None else tuple(
            dict.fromkeys(str(item) for item in assignees if str(item).strip())
        )
        return artifact.with_updates(
            reviewers=merged_reviewers,
            labels=merged_labels,
            assignees=merged_assignees,
        )

    def mark_ready_for_review(self, review: ProviderReviewArtifact | dict) -> ProviderReviewArtifact:
        artifact = review if isinstance(review, ProviderReviewArtifact) else ProviderReviewArtifact.from_dict(review)
        provider = self._provider(artifact.provider)
        if hasattr(provider, "mark_ready_for_review"):
            return provider.mark_ready_for_review(artifact)
        return artifact.with_updates(draft=False)
