from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from ..models import utc_now_iso
from .base import ProviderReviewArtifact, ReviewFeedbackSnapshot, ReviewStatusSnapshot


class GitHubProviderError(RuntimeError):
    pass


_TIMELINE_EVENTS_OF_INTEREST = {
    "ready_for_review",
    "reviewed",
    "labeled",
    "unlabeled",
    "closed",
    "merged",
    "review_request_removed",
    "head_ref_force_pushed",
}


class GitHubReviewClient:
    provider_name = "github"

    def __init__(
        self,
        *,
        token: str,
        api_base: str = "https://api.github.com",
        requester: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        token = str(token or "").strip()
        if not token:
            raise ValueError("GitHub token is required.")
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.requester = requester or urllib.request.urlopen

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "JARVIS/0.1",
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        ignore_codes: Sequence[int] = (),
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        body = None
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)
        if payload is not None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.requester(request) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in set(ignore_codes):
                return None
            detail = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
            raise GitHubProviderError(
                f"GitHub API request failed: {method} {path} [{exc.code}] {detail}"
            ) from exc

    def _graphql_request(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {"query": query, "variables": dict(variables or {})}
        result = self._request("POST", "/graphql", payload=payload)
        if not isinstance(result, dict):
            raise GitHubProviderError("Invalid GraphQL response payload.")
        errors = result.get("errors") or []
        if errors:
            raise GitHubProviderError(f"GraphQL request failed: {errors}")
        return dict(result.get("data") or {})

    def _parse_repo_slug(self, repo_slug: str) -> tuple[str, str]:
        parts = [part for part in str(repo_slug).strip().split("/") if part]
        if len(parts) != 2:
            raise ValueError(f"GitHub repo slug must be owner/repo, got: {repo_slug!r}")
        return parts[0], parts[1]

    def _unique_ordered(self, values: Sequence[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(item) for item in values if str(item).strip()))

    def _parse_requested_reviewers(self, payload: dict[str, Any] | None) -> tuple[str, ...]:
        data = payload or {}
        users = [
            str(item.get("login", "")).strip()
            for item in data.get("users", []) or []
            if str(item.get("login", "")).strip()
        ]
        teams = [
            f"team:{str(item.get('slug', '')).strip()}"
            for item in data.get("teams", []) or []
            if str(item.get("slug", "")).strip()
        ]
        return self._unique_ordered([*users, *teams])

    def _parse_labels(self, payload: Sequence[dict[str, Any]] | None) -> tuple[str, ...]:
        labels = [
            str(item.get("name", "")).strip()
            for item in (payload or [])
            if str(item.get("name", "")).strip()
        ]
        return self._unique_ordered(labels)

    def _parse_assignees_from_pr(self, payload: dict[str, Any] | None) -> tuple[str, ...]:
        raw = payload or {}
        values = [
            str(item.get("login", "")).strip()
            for item in raw.get("assignees", []) or []
            if str(item.get("login", "")).strip()
        ]
        return self._unique_ordered(values)

    def _normalize_reviews(self, payload: Sequence[dict[str, Any]] | None) -> tuple[dict[str, Any], ...]:
        items = payload if isinstance(payload, list) else []
        return tuple(
            {
                "id": item.get("id"),
                "state": str(item.get("state", "")).strip().lower(),
                "user": str((item.get("user") or {}).get("login", "")),
                "submitted_at": item.get("submitted_at"),
                "commit_id": item.get("commit_id"),
                "body": str(item.get("body", ""))[:400],
            }
            for item in items
        )

    def _normalize_issue_comments(
        self,
        payload: Sequence[dict[str, Any]] | None,
    ) -> tuple[dict[str, Any], ...]:
        items = payload if isinstance(payload, list) else []
        return tuple(
            {
                "id": item.get("id"),
                "user": str((item.get("user") or {}).get("login", "")),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "body": str(item.get("body", ""))[:400],
            }
            for item in items
        )

    def _normalize_review_comments(
        self,
        payload: Sequence[dict[str, Any]] | None,
    ) -> tuple[dict[str, Any], ...]:
        items = payload if isinstance(payload, list) else []
        return tuple(
            {
                "id": item.get("id"),
                "user": str((item.get("user") or {}).get("login", "")),
                "path": item.get("path"),
                "line": item.get("line"),
                "side": item.get("side"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "body": str(item.get("body", ""))[:400],
            }
            for item in items
        )

    def _review_summary(self, reviews: Sequence[dict[str, Any]]) -> dict[str, Any]:
        counts = {
            "approved": 0,
            "changes_requested": 0,
            "commented": 0,
            "dismissed": 0,
            "pending": 0,
        }
        latest_state = "none"
        for review in reviews:
            state = str(review.get("state", "")).strip().lower()
            if state == "approved":
                counts["approved"] += 1
            elif state == "changes_requested":
                counts["changes_requested"] += 1
            elif state == "commented":
                counts["commented"] += 1
            elif state == "dismissed":
                counts["dismissed"] += 1
            elif state:
                counts["pending"] += 1
            if state:
                latest_state = state
        decision = "none"
        if counts["changes_requested"] > 0:
            decision = "changes_requested"
        elif counts["approved"] > 0:
            decision = "approved"
        elif counts["commented"] > 0:
            decision = "commented"
        return {
            "decision": decision,
            "latest_state": latest_state,
            "approved_count": counts["approved"],
            "changes_requested_count": counts["changes_requested"],
            "commented_count": counts["commented"],
            "dismissed_count": counts["dismissed"],
            "pending_count": counts["pending"],
            "total_reviews": len(reviews),
        }

    def _filter_timeline_events(
        self,
        events: Sequence[dict[str, Any]],
        *,
        since_cursor: str | None,
    ) -> tuple[tuple[dict[str, Any], ...], str | None]:
        cursor_num = None
        if since_cursor is not None:
            raw = str(since_cursor).strip()
            if raw.isdigit():
                cursor_num = int(raw)
        filtered: list[dict[str, Any]] = []
        latest_cursor = since_cursor
        for event in events:
            event_type = str(event.get("event", "")).strip()
            if event_type not in _TIMELINE_EVENTS_OF_INTEREST:
                continue
            event_id = event.get("id")
            if cursor_num is not None and isinstance(event_id, int) and event_id <= cursor_num:
                continue
            filtered.append(
                {
                    "id": event_id,
                    "event": event_type,
                    "created_at": event.get("created_at"),
                    "actor": ((event.get("actor") or {}).get("login")),
                }
            )
            if isinstance(event_id, int):
                latest_cursor = str(event_id)
        return tuple(filtered), (str(latest_cursor) if latest_cursor is not None else None)

    def _required_status_checks(
        self,
        *,
        owner: str,
        repo: str,
        base_branch: str,
    ) -> tuple[bool, tuple[str, ...]]:
        payload = self._request(
            "GET",
            f"/repos/{owner}/{repo}/branches/{base_branch}/protection/required_status_checks",
            ignore_codes=(403, 404),
        )
        if not isinstance(payload, dict):
            return False, ()
        contexts = payload.get("contexts")
        if isinstance(contexts, list):
            return True, self._unique_ordered([str(item) for item in contexts])
        checks = payload.get("checks")
        if isinstance(checks, list):
            names = [
                str(item.get("context", "")).strip()
                for item in checks
                if str(item.get("context", "")).strip()
            ]
            return True, self._unique_ordered(names)
        return True, ()

    def _rollup_checks(
        self,
        *,
        status_payload: dict[str, Any] | None,
        required_checks_configured: bool,
        required_checks: Sequence[str],
    ) -> tuple[str | None, tuple[str, ...]]:
        payload = status_payload or {}
        statuses = payload.get("statuses", []) or []
        context_state: dict[str, str] = {}
        for status in statuses:
            context = str(status.get("context", "")).strip()
            if not context:
                continue
            context_state[context] = str(status.get("state", "pending")).strip().lower()

        if required_checks_configured and required_checks:
            blocking: list[str] = []
            has_failure = False
            has_pending = False
            for context in required_checks:
                state = context_state.get(context, "pending")
                if state in {"failure", "error"}:
                    has_failure = True
                    blocking.append(context)
                elif state != "success":
                    has_pending = True
                    blocking.append(context)
            if has_failure:
                return "failure", tuple(blocking)
            if has_pending:
                return "pending", tuple(blocking)
            return "success", ()

        blocking_contexts = tuple(
            str(status.get("context") or "unknown")
            for status in statuses
            if str(status.get("state") or "pending").lower() not in {"success"}
        )
        if not statuses and not required_checks_configured:
            return None, ()
        state = payload.get("state")
        return (str(state) if state is not None else None), blocking_contexts

    def get_pull_request(self, repo_slug: str, pr_number: str) -> dict[str, Any]:
        owner, repo = self._parse_repo_slug(repo_slug)
        payload = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")
        if not isinstance(payload, dict):
            raise GitHubProviderError("Invalid pull request payload returned by provider.")
        return payload

    def get_requested_reviewers(self, repo_slug: str, pr_number: str) -> tuple[str, ...]:
        owner, repo = self._parse_repo_slug(repo_slug)
        payload = self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
        )
        return self._parse_requested_reviewers(payload if isinstance(payload, dict) else {})

    def list_reviews(self, repo_slug: str, pr_number: str) -> tuple[dict[str, Any], ...]:
        owner, repo = self._parse_repo_slug(repo_slug)
        payload = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews")
        return self._normalize_reviews(payload if isinstance(payload, list) else [])

    def list_issue_comments(self, repo_slug: str, pr_number: str) -> tuple[dict[str, Any], ...]:
        owner, repo = self._parse_repo_slug(repo_slug)
        payload = self._request("GET", f"/repos/{owner}/{repo}/issues/{pr_number}/comments")
        return self._normalize_issue_comments(payload if isinstance(payload, list) else [])

    def list_review_comments(self, repo_slug: str, pr_number: str) -> tuple[dict[str, Any], ...]:
        owner, repo = self._parse_repo_slug(repo_slug)
        payload = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/comments")
        return self._normalize_review_comments(payload if isinstance(payload, list) else [])

    def list_timeline_events(
        self,
        repo_slug: str,
        pr_number: str,
        *,
        since_cursor: str | None = None,
    ) -> tuple[tuple[dict[str, Any], ...], str | None]:
        owner, repo = self._parse_repo_slug(repo_slug)
        payload = self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{pr_number}/timeline",
            extra_headers={"Accept": "application/vnd.github+json"},
        )
        timeline_raw = payload if isinstance(payload, list) else []
        return self._filter_timeline_events(timeline_raw, since_cursor=since_cursor)

    def set_labels(
        self,
        repo_slug: str,
        pr_number: str,
        labels: Sequence[str],
    ) -> tuple[str, ...]:
        owner, repo = self._parse_repo_slug(repo_slug)
        normalized = self._unique_ordered(labels)
        payload = self._request(
            "PUT",
            f"/repos/{owner}/{repo}/issues/{pr_number}/labels",
            {"labels": list(normalized)},
        )
        return self._parse_labels(payload if isinstance(payload, list) else [])

    def set_assignees(
        self,
        repo_slug: str,
        pr_number: str,
        assignees: Sequence[str],
    ) -> tuple[str, ...]:
        owner, repo = self._parse_repo_slug(repo_slug)
        normalized = self._unique_ordered(assignees)
        payload = self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{pr_number}",
            {"assignees": list(normalized)},
        )
        return self._parse_assignees_from_pr(payload if isinstance(payload, dict) else {})

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
    ) -> ProviderReviewArtifact:
        owner, repo = self._parse_repo_slug(repo_slug)
        normalized_labels = self._unique_ordered(labels)
        normalized_reviewers = self._unique_ordered(reviewers)

        pr = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            {
                "title": title,
                "head": head_branch,
                "base": base_branch,
                "body": body_markdown,
                "draft": bool(draft),
            },
        )
        number = int(pr["number"])
        if normalized_labels:
            self.set_labels(f"{owner}/{repo}", str(number), normalized_labels)
        if normalized_reviewers:
            self._request(
                "POST",
                f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
                {"reviewers": [name for name in normalized_reviewers if not name.startswith("team:")]},
            )

        returned_head_sha = str(((pr.get("head") or {}).get("sha") or head_sha))
        review_state = "merged" if bool(pr.get("merged")) else str(pr.get("state", "open"))
        snapshot = ReviewStatusSnapshot(
            review_state=review_state,
            checks_state=None,
            merged=bool(pr.get("merged", False)),
            draft=bool(pr.get("draft", draft)),
            mergeable=pr.get("mergeable"),
            blocking_contexts=(),
            head_sha=returned_head_sha,
            web_url=str(pr.get("html_url", "")),
            synced_at=utc_now_iso(),
            provider_updated_at=(str(pr["updated_at"]) if pr.get("updated_at") else None),
        )
        feedback = ReviewFeedbackSnapshot(
            requested_reviewers=normalized_reviewers,
            reviews=(),
            issue_comments=(),
            review_comments=(),
            timeline_events=(),
            timeline_cursor=None,
            review_summary={"decision": "none", "total_reviews": 0},
            merge_outcome=None,
            required_checks=(),
            required_checks_configured=False,
            synced_at=utc_now_iso(),
        )
        return ProviderReviewArtifact(
            provider=self.provider_name,
            repo_slug=f"{owner}/{repo}",
            external_id=str(pr.get("id", number)),
            number=str(number),
            title=str(pr.get("title") or title),
            body_markdown=body_markdown,
            web_url=str(pr.get("html_url", "")),
            api_url=str(pr.get("url", "")),
            base_branch=base_branch,
            head_branch=head_branch,
            head_sha=returned_head_sha,
            state=review_state,
            draft=bool(pr.get("draft", draft)),
            labels=normalized_labels,
            reviewers=normalized_reviewers,
            assignees=self._parse_assignees_from_pr(pr),
            created_at=str(pr.get("created_at") or utc_now_iso()),
            updated_at=str(pr.get("updated_at") or utc_now_iso()),
            status=snapshot,
            feedback=feedback,
            metadata=dict(metadata or {}),
        )

    def configure_review(
        self,
        artifact: ProviderReviewArtifact,
        *,
        reviewers: Sequence[str] | None = None,
        labels: Sequence[str] | None = None,
    ) -> ProviderReviewArtifact:
        owner, repo = self._parse_repo_slug(artifact.repo_slug)
        number = artifact.number
        if labels is not None:
            self.set_labels(f"{owner}/{repo}", str(number), labels)
        if reviewers is not None:
            desired = set(self._unique_ordered(reviewers))
            current = set(self.get_requested_reviewers(f"{owner}/{repo}", str(number)))
            to_add = sorted(item for item in desired - current if not item.startswith("team:"))
            to_remove = sorted(item for item in current - desired if not item.startswith("team:"))
            if to_add:
                self._request(
                    "POST",
                    f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
                    {"reviewers": to_add},
                )
            if to_remove:
                self._request(
                    "DELETE",
                    f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
                    {"reviewers": to_remove},
                )
        return self.sync_review(artifact)

    def mark_ready_for_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact:
        if not artifact.draft:
            return self.sync_review(artifact)
        owner, repo = self._parse_repo_slug(artifact.repo_slug)
        pr = self._request("GET", f"/repos/{owner}/{repo}/pulls/{artifact.number}")
        node_id = str(pr.get("node_id", "")).strip()
        if node_id:
            mutation = """
            mutation MarkReady($pullRequestId: ID!) {
              markPullRequestReadyForReview(input: {pullRequestId: $pullRequestId}) {
                pullRequest {
                  number
                  isDraft
                  state
                  url
                }
              }
            }
            """
            try:
                self._graphql_request(mutation, {"pullRequestId": node_id})
            except GitHubProviderError:
                # REST fallback in case GraphQL permissions are restricted.
                self._request(
                    "POST",
                    f"/repos/{owner}/{repo}/pulls/{artifact.number}/ready_for_review",
                )
        else:
            self._request(
                "POST",
                f"/repos/{owner}/{repo}/pulls/{artifact.number}/ready_for_review",
            )
        return self.sync_review(artifact.with_updates(draft=False))

    def sync_review(self, artifact: ProviderReviewArtifact) -> ProviderReviewArtifact:
        pr = self.get_pull_request(artifact.repo_slug, artifact.number)
        owner, repo = self._parse_repo_slug(artifact.repo_slug)
        head_sha = str(((pr.get("head") or {}).get("sha") or artifact.head_sha))
        base_branch = str(((pr.get("base") or {}).get("ref") or artifact.base_branch))
        required_configured, required_checks = self._required_status_checks(
            owner=owner,
            repo=repo,
            base_branch=base_branch,
        )
        status_payload = self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits/{head_sha}/status",
            ignore_codes=(404,),
        )
        checks_state, blocking_contexts = self._rollup_checks(
            status_payload=status_payload if isinstance(status_payload, dict) else None,
            required_checks_configured=required_configured,
            required_checks=required_checks,
        )

        requested_reviewers = self.get_requested_reviewers(artifact.repo_slug, artifact.number)
        labels_payload = self._request("GET", f"/repos/{owner}/{repo}/issues/{artifact.number}/labels")
        labels = self._parse_labels(labels_payload if isinstance(labels_payload, list) else [])
        reviews = self.list_reviews(artifact.repo_slug, artifact.number)
        review_summary = self._review_summary(reviews)
        issue_comments = self.list_issue_comments(artifact.repo_slug, artifact.number)
        review_comments = self.list_review_comments(artifact.repo_slug, artifact.number)
        since_cursor = artifact.feedback.timeline_cursor if artifact.feedback else None
        timeline_events, timeline_cursor = self.list_timeline_events(
            artifact.repo_slug,
            artifact.number,
            since_cursor=since_cursor,
        )

        merge_outcome = None
        if bool(pr.get("merged", False)):
            merge_outcome = "merged"
        elif str(pr.get("state", "")).lower() == "closed":
            merge_outcome = "closed_unmerged"
        elif review_summary["decision"] == "changes_requested":
            merge_outcome = "changes_requested"
        elif review_summary["decision"] == "approved":
            merge_outcome = "approved"

        review_state = "merged" if bool(pr.get("merged")) else str(pr.get("state", artifact.state))
        snapshot = ReviewStatusSnapshot(
            review_state=review_state,
            checks_state=checks_state,
            merged=bool(pr.get("merged", False)),
            draft=bool(pr.get("draft", artifact.draft)),
            mergeable=pr.get("mergeable"),
            blocking_contexts=blocking_contexts,
            head_sha=head_sha,
            web_url=str(pr.get("html_url") or artifact.web_url),
            synced_at=utc_now_iso(),
            provider_updated_at=(str(pr["updated_at"]) if pr.get("updated_at") else None),
        )
        feedback = ReviewFeedbackSnapshot(
            requested_reviewers=requested_reviewers,
            reviews=reviews,
            issue_comments=issue_comments,
            review_comments=review_comments,
            timeline_events=timeline_events,
            timeline_cursor=timeline_cursor,
            review_summary=review_summary,
            merge_outcome=merge_outcome,
            required_checks=required_checks,
            required_checks_configured=required_configured,
            synced_at=utc_now_iso(),
        )
        return artifact.with_updates(
            title=str(pr.get("title") or artifact.title),
            web_url=str(pr.get("html_url") or artifact.web_url),
            api_url=str(pr.get("url") or artifact.api_url),
            base_branch=base_branch,
            head_sha=head_sha,
            state=review_state,
            draft=bool(pr.get("draft", artifact.draft)),
            labels=labels,
            reviewers=requested_reviewers,
            assignees=self._parse_assignees_from_pr(pr),
            updated_at=str(pr.get("updated_at") or utc_now_iso()),
            status=snapshot,
            feedback=feedback,
        )
