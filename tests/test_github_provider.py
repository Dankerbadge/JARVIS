from __future__ import annotations

import json
import unittest
from urllib.request import Request

from jarvis.providers.github import GitHubReviewClient


class _FakeResponse:
    def __init__(self, payload: dict | list) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.labels = ["jarvis", "needs-review"]
        self.requested_reviewers = ["octocat"]
        self.assignees: list[str] = []
        self.is_draft = True

    def _users_payload(self) -> dict:
        return {
            "users": [{"login": login} for login in self.requested_reviewers],
            "teams": [],
        }

    def __call__(self, request: Request) -> _FakeResponse:
        method = request.get_method()
        path = request.full_url.replace("https://api.github.test", "")
        payload = json.loads(request.data.decode("utf-8")) if request.data else None
        headers = {k: v for k, v in request.header_items()}
        self.calls.append({"method": method, "path": path, "payload": payload, "headers": headers})

        if (method, path) == ("POST", "/repos/acme/zenith/pulls"):
            self.is_draft = bool(payload.get("draft", True))
            return _FakeResponse(
                {
                    "id": 101,
                    "node_id": "PR_node_7",
                    "number": 7,
                    "title": payload.get("title"),
                    "url": "https://api.github.test/repos/acme/zenith/pulls/7",
                    "html_url": "https://github.test/acme/zenith/pull/7",
                    "state": "open",
                    "draft": self.is_draft,
                    "created_at": "2026-04-10T00:00:00Z",
                    "updated_at": "2026-04-10T00:00:01Z",
                    "head": {"sha": "abc123"},
                    "base": {"ref": payload.get("base", "main")},
                    "assignees": [{"login": login} for login in self.assignees],
                    "mergeable": None,
                    "merged": False,
                }
            )
        if (method, path) == ("PUT", "/repos/acme/zenith/issues/7/labels"):
            self.labels = list(payload.get("labels", []))
            return _FakeResponse([{"name": item} for item in self.labels])
        if (method, path) == ("GET", "/repos/acme/zenith/issues/7/labels"):
            return _FakeResponse([{"name": item} for item in self.labels])
        if (method, path) == ("POST", "/repos/acme/zenith/pulls/7/requested_reviewers"):
            for item in payload.get("reviewers", []):
                if item not in self.requested_reviewers:
                    self.requested_reviewers.append(item)
            return _FakeResponse(self._users_payload())
        if (method, path) == ("DELETE", "/repos/acme/zenith/pulls/7/requested_reviewers"):
            removals = set(payload.get("reviewers", []))
            self.requested_reviewers = [item for item in self.requested_reviewers if item not in removals]
            return _FakeResponse(self._users_payload())
        if (method, path) == ("GET", "/repos/acme/zenith/pulls/7/requested_reviewers"):
            return _FakeResponse(self._users_payload())
        if (method, path) == ("GET", "/repos/acme/zenith/pulls/7"):
            return _FakeResponse(
                {
                    "id": 101,
                    "node_id": "PR_node_7",
                    "number": 7,
                    "title": "[JARVIS] Zenith",
                    "url": "https://api.github.test/repos/acme/zenith/pulls/7",
                    "html_url": "https://github.test/acme/zenith/pull/7",
                    "state": "open",
                    "draft": self.is_draft,
                    "merged": False,
                    "mergeable": True,
                    "updated_at": "2026-04-10T00:10:00Z",
                    "head": {"sha": "abc123"},
                    "base": {"ref": "main"},
                    "assignees": [{"login": login} for login in self.assignees],
                }
            )
        if (method, path) == ("PATCH", "/repos/acme/zenith/issues/7"):
            self.assignees = list(payload.get("assignees", []))
            return _FakeResponse(
                {
                    "number": 7,
                    "assignees": [{"login": login} for login in self.assignees],
                }
            )
        if (method, path) == ("GET", "/repos/acme/zenith/branches/main/protection/required_status_checks"):
            return _FakeResponse({"strict": True, "contexts": ["unit-tests"]})
        if (method, path) == ("GET", "/repos/acme/zenith/commits/abc123/status"):
            return _FakeResponse(
                {
                    "state": "failure",
                    "statuses": [
                        {"context": "unit-tests", "state": "failure"},
                        {"context": "lint", "state": "success"},
                    ],
                }
            )
        if (method, path) == ("GET", "/repos/acme/zenith/pulls/7/reviews"):
            return _FakeResponse(
                [
                    {
                        "id": 501,
                        "state": "APPROVED",
                        "user": {"login": "alice"},
                        "submitted_at": "2026-04-10T00:03:00Z",
                        "commit_id": "abc123",
                        "body": "Looks good",
                    }
                ]
            )
        if (method, path) == ("GET", "/repos/acme/zenith/issues/7/comments"):
            return _FakeResponse(
                [
                    {
                        "id": 601,
                        "user": {"login": "bob"},
                        "created_at": "2026-04-10T00:04:00Z",
                        "updated_at": "2026-04-10T00:04:00Z",
                        "body": "Please double check",
                    }
                ]
            )
        if (method, path) == ("GET", "/repos/acme/zenith/pulls/7/comments"):
            return _FakeResponse(
                [
                    {
                        "id": 701,
                        "user": {"login": "carol"},
                        "path": "ui/zenith_ui.txt",
                        "line": 1,
                        "side": "RIGHT",
                        "created_at": "2026-04-10T00:05:00Z",
                        "updated_at": "2026-04-10T00:05:00Z",
                        "body": "Style nit",
                    }
                ]
            )
        if (method, path) == ("GET", "/repos/acme/zenith/issues/7/timeline"):
            return _FakeResponse(
                [
                    {
                        "id": 801,
                        "event": "ready_for_review",
                        "created_at": "2026-04-10T00:06:00Z",
                        "actor": {"login": "octocat"},
                    },
                    {
                        "id": 802,
                        "event": "reviewed",
                        "created_at": "2026-04-10T00:07:00Z",
                        "actor": {"login": "alice"},
                    },
                ]
            )
        if (method, path) == ("POST", "/graphql"):
            self.is_draft = False
            return _FakeResponse(
                {
                    "data": {
                        "markPullRequestReadyForReview": {
                            "pullRequest": {
                                "number": 7,
                                "isDraft": False,
                                "state": "OPEN",
                                "url": "https://github.test/acme/zenith/pull/7",
                            }
                        }
                    }
                }
            )
        if (method, path) == ("POST", "/repos/acme/zenith/pulls/7/ready_for_review"):
            self.is_draft = False
            return _FakeResponse({})

        raise AssertionError(f"Unexpected request: {method} {path}")


class GitHubReviewClientTests(unittest.TestCase):
    def test_create_review_builds_expected_requests(self) -> None:
        recorder = _Recorder()
        client = GitHubReviewClient(
            token="test-token",
            api_base="https://api.github.test",
            requester=recorder,
        )
        artifact = client.create_review(
            repo_slug="acme/zenith",
            title="[JARVIS] Zenith",
            body_markdown="Body",
            head_branch="feature/jarvis",
            base_branch="main",
            head_sha="abc123",
            draft=True,
            labels=("jarvis", "needs-review"),
            reviewers=("octocat",),
        )
        self.assertEqual(artifact.provider, "github")
        self.assertEqual(artifact.number, "7")
        self.assertEqual(artifact.head_sha, "abc123")
        self.assertTrue(artifact.draft)
        self.assertEqual(len(recorder.calls), 3)
        self.assertEqual(recorder.calls[0]["payload"]["head"], "feature/jarvis")
        self.assertEqual(recorder.calls[1]["payload"]["labels"], ["jarvis", "needs-review"])
        self.assertEqual(recorder.calls[2]["payload"]["reviewers"], ["octocat"])
        self.assertEqual(recorder.calls[0]["headers"]["Authorization"], "Bearer test-token")

    def test_sync_review_rolls_up_checks_and_feedback(self) -> None:
        recorder = _Recorder()
        client = GitHubReviewClient(
            token="test-token",
            api_base="https://api.github.test",
            requester=recorder,
        )
        artifact = client.create_review(
            repo_slug="acme/zenith",
            title="[JARVIS] Zenith",
            body_markdown="Body",
            head_branch="feature/jarvis",
            base_branch="main",
            head_sha="abc123",
            draft=True,
        )
        synced = client.sync_review(artifact)
        assert synced.status is not None
        assert synced.feedback is not None
        self.assertEqual(synced.status.review_state, "open")
        self.assertEqual(synced.status.checks_state, "failure")
        self.assertEqual(synced.status.blocking_contexts, ("unit-tests",))
        self.assertTrue(synced.status.mergeable)
        self.assertEqual(tuple(synced.reviewers), ("octocat",))
        self.assertEqual(synced.feedback.review_summary["decision"], "approved")
        self.assertEqual(synced.feedback.timeline_cursor, "802")
        self.assertEqual(synced.feedback.required_checks, ("unit-tests",))
        self.assertTrue(synced.feedback.required_checks_configured)

    def test_configure_review_normalizes_reviewers_and_labels(self) -> None:
        recorder = _Recorder()
        client = GitHubReviewClient(
            token="test-token",
            api_base="https://api.github.test",
            requester=recorder,
        )
        artifact = client.create_review(
            repo_slug="acme/zenith",
            title="[JARVIS] Zenith",
            body_markdown="Body",
            head_branch="feature/jarvis",
            base_branch="main",
            head_sha="abc123",
            draft=True,
            reviewers=("octocat",),
            labels=("jarvis", "needs-review"),
        )
        updated = client.configure_review(
            artifact,
            reviewers=("alice",),
            labels=("jarvis", "protected-change"),
        )
        self.assertEqual(tuple(updated.reviewers), ("alice",))
        self.assertEqual(tuple(updated.labels), ("jarvis", "protected-change"))
        self.assertIn(
            ("DELETE", "/repos/acme/zenith/pulls/7/requested_reviewers"),
            {(call["method"], call["path"]) for call in recorder.calls},
        )

    def test_mark_ready_for_review_uses_graphql(self) -> None:
        recorder = _Recorder()
        client = GitHubReviewClient(
            token="test-token",
            api_base="https://api.github.test",
            requester=recorder,
        )
        artifact = client.create_review(
            repo_slug="acme/zenith",
            title="[JARVIS] Zenith",
            body_markdown="Body",
            head_branch="feature/jarvis",
            base_branch="main",
            head_sha="abc123",
            draft=True,
        )
        updated = client.mark_ready_for_review(artifact)
        self.assertFalse(updated.draft)
        self.assertIn(
            ("POST", "/graphql"),
            {(call["method"], call["path"]) for call in recorder.calls},
        )

    def test_set_labels_and_assignees_methods(self) -> None:
        recorder = _Recorder()
        client = GitHubReviewClient(
            token="test-token",
            api_base="https://api.github.test",
            requester=recorder,
        )
        labels = client.set_labels("acme/zenith", "7", ("jarvis", "needs-review"))
        assignees = client.set_assignees("acme/zenith", "7", ("alice",))
        self.assertEqual(labels, ("jarvis", "needs-review"))
        self.assertEqual(assignees, ("alice",))


if __name__ == "__main__":
    unittest.main()
