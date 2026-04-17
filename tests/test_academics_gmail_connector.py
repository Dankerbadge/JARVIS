from __future__ import annotations

import base64
import json
import io
import unittest
import urllib.error
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

from jarvis.connectors.academics_gmail import GmailAcademicsConnector


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("utf-8").rstrip("=")


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _GmailAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.messages: dict[str, dict] = {
            "m1": self._message(
                message_id="m1",
                history_id=100,
                subject="CS101 Assignment 2 due 2026-04-18T23:59:00Z",
                sender="instructor@university.edu",
                body="Assignment details and rubric.",
            ),
            "m2": self._message(
                message_id="m2",
                history_id=101,
                subject="CS101 weekly update",
                sender="professor@university.edu",
                body="Reminder: lecture and reading chapter 7.",
            ),
        }

    def _message(
        self,
        *,
        message_id: str,
        history_id: int,
        subject: str,
        sender: str,
        body: str,
    ) -> dict:
        return {
            "id": message_id,
            "threadId": f"t-{message_id}",
            "historyId": str(history_id),
            "internalDate": str(1775800000000 + history_id),
            "snippet": body[:120],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": subject},
                    {"name": "From", "value": sender},
                    {"name": "Date", "value": "Fri, 11 Apr 2026 10:00:00 +0000"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64(body)},
                    }
                ],
            },
        }

    def __call__(self, request: Request) -> _FakeResponse:
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)
        self.calls.append(
            {
                "path": parsed.path,
                "query": query,
                "auth": dict(request.header_items()).get("Authorization"),
            }
        )
        if parsed.path.endswith("/messages"):
            message_ids = sorted(self.messages.keys())
            return _FakeResponse({"messages": [{"id": message_id} for message_id in message_ids]})
        message_id = parsed.path.rsplit("/", 1)[-1]
        return _FakeResponse(dict(self.messages[message_id]))


class _GmailAPIWithRefresh:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.refresh_calls = 0
        self.messages: dict[str, dict] = {
            "m1": {
                "id": "m1",
                "threadId": "t-m1",
                "historyId": "201",
                "internalDate": "1775800001000",
                "snippet": "exam reminder",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "CS101 Midterm exam announcement 2026-04-20T14:00:00Z"},
                        {"name": "From", "value": "instructor@university.edu"},
                        {"name": "Date", "value": "Fri, 11 Apr 2026 10:00:00 +0000"},
                    ],
                    "parts": [{"mimeType": "text/plain", "body": {"data": _b64("midterm details")}}],
                },
            }
        }

    def __call__(self, request: Request) -> _FakeResponse:
        parsed = urlparse(request.full_url)
        path = parsed.path
        query = parse_qs(parsed.query)
        headers = dict(request.header_items())
        auth = headers.get("Authorization")
        self.calls.append({"path": path, "query": query, "auth": auth})

        if path == "/token":
            self.refresh_calls += 1
            return _FakeResponse({"access_token": "fresh-token", "expires_in": 3599})

        if auth == "Bearer expired-token":
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"invalid_token"}'),
            )

        if path.endswith("/messages"):
            return _FakeResponse({"messages": [{"id": "m1"}]})
        return _FakeResponse(dict(self.messages["m1"]))


class GmailAcademicsConnectorTests(unittest.TestCase):
    def test_poll_emits_incremental_provider_events(self) -> None:
        recorder = _GmailAPI()
        connector = GmailAcademicsConnector(
            token="test-google-token",
            query="newer_than:30d subject:(assignment OR exam)",
            requester=recorder,
            api_base="https://gmail.googleapis.test/gmail/v1",
        )

        first = connector.poll(None)
        self.assertEqual(len(first.events), 2)
        source_types = sorted(event.source_type for event in first.events)
        self.assertIn("academic.assignment_due", source_types)
        self.assertIn("academic.reading_assigned", source_types)
        self.assertEqual(first.events[0].payload.get("ingestion_source_kind"), "provider")
        self.assertEqual(first.events[0].payload.get("ingestion_provider"), "gmail")
        self.assertEqual(recorder.calls[0]["auth"], "Bearer test-google-token")
        self.assertEqual(first.cursor.get("history_id_cursor"), 101)

        second = connector.poll(first.cursor)
        self.assertEqual(len(second.events), 0)

        recorder.messages["m3"] = recorder._message(
            message_id="m3",
            history_id=102,
            subject="CS101 Midterm exam announcement 2026-04-20T14:00:00Z",
            sender="instructor@university.edu",
            body="Midterm logistics and allowed materials.",
        )
        third = connector.poll(second.cursor)
        self.assertEqual(len(third.events), 1)
        self.assertEqual(third.events[0].source_type, "academic.exam_scheduled")
        self.assertEqual(third.events[0].payload.get("provider_message_id"), "m3")

    def test_poll_refreshes_token_after_401(self) -> None:
        recorder = _GmailAPIWithRefresh()
        connector = GmailAcademicsConnector(
            token="expired-token",
            refresh_token="refresh-token",
            client_id="client-id",
            client_secret="client-secret",
            token_endpoint="https://oauth2.googleapis.test/token",
            query="newer_than:30d subject:(assignment OR exam)",
            requester=recorder,
            auth_requester=recorder,
            api_base="https://gmail.googleapis.test/gmail/v1",
        )
        first = connector.poll(None)
        self.assertEqual(len(first.events), 1)
        self.assertEqual(first.events[0].source_type, "academic.exam_scheduled")
        self.assertEqual(recorder.refresh_calls, 1)
        auth_headers = [item["auth"] for item in recorder.calls if item["path"] != "/token"]
        self.assertIn("Bearer expired-token", auth_headers)
        self.assertIn("Bearer fresh-token", auth_headers)


if __name__ == "__main__":
    unittest.main()
