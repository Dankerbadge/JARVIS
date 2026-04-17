from __future__ import annotations

import json
import io
import unittest
import urllib.error
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

from jarvis.connectors.academics_google_calendar import GoogleCalendarConnector


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _CalendarAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.items: list[dict] = [
            {
                "id": "evt-exam",
                "summary": "CS101 Midterm Exam",
                "description": "Room 103",
                "status": "confirmed",
                "updated": "2026-04-11T09:00:00Z",
                "start": {"dateTime": "2026-04-15T13:00:00Z"},
                "end": {"dateTime": "2026-04-15T15:00:00Z"},
            },
            {
                "id": "evt-assignment",
                "summary": "CS101 Project Due",
                "description": "Assignment submission deadline",
                "status": "confirmed",
                "updated": "2026-04-11T09:00:00Z",
                "start": {"dateTime": "2026-04-14T23:00:00Z"},
                "end": {"dateTime": "2026-04-15T00:00:00Z"},
            },
        ]

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
        return _FakeResponse({"items": list(self.items)})


class _CalendarAPIWithRefresh:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.refresh_calls = 0
        self.items: list[dict] = [
            {
                "id": "evt-refresh",
                "summary": "CS101 Midterm Exam",
                "description": "Room 103",
                "status": "confirmed",
                "updated": "2026-04-11T09:00:00Z",
                "start": {"dateTime": "2026-04-15T13:00:00Z"},
                "end": {"dateTime": "2026-04-15T15:00:00Z"},
            }
        ]

    def __call__(self, request: Request) -> _FakeResponse:
        parsed = urlparse(request.full_url)
        path = parsed.path
        headers = dict(request.header_items())
        auth = headers.get("Authorization")
        self.calls.append({"path": path, "auth": auth})
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
        return _FakeResponse({"items": list(self.items)})


class GoogleCalendarConnectorTests(unittest.TestCase):
    def test_poll_uses_cursor_and_emits_provider_sourced_events(self) -> None:
        recorder = _CalendarAPI()
        connector = GoogleCalendarConnector(
            calendar_id="primary",
            token="test-google-token",
            requester=recorder,
            api_base="https://www.googleapis.test/calendar/v3",
        )

        first = connector.poll(None)
        self.assertEqual(len(first.events), 2)
        self.assertTrue(first.cursor)
        self.assertEqual(first.events[0].payload.get("ingestion_source_kind"), "provider")
        self.assertEqual(first.events[0].payload.get("ingestion_provider"), "google_calendar")
        source_types = sorted(event.source_type for event in first.events)
        self.assertIn("academic.assignment_due", source_types)
        self.assertIn("academic.exam_scheduled", source_types)
        self.assertEqual(recorder.calls[0]["auth"], "Bearer test-google-token")
        self.assertNotIn("updatedMin", recorder.calls[0]["query"])

        second = connector.poll(first.cursor)
        self.assertEqual(len(second.events), 0)
        self.assertIn("updatedMin", recorder.calls[-1]["query"])

        recorder.items.append(
            {
                "id": "evt-class",
                "summary": "CS101 Lecture",
                "description": "Weekly class session",
                "status": "confirmed",
                "updated": "2026-04-11T10:30:00Z",
                "start": {"dateTime": "2026-04-12T14:00:00Z"},
                "end": {"dateTime": "2026-04-12T15:20:00Z"},
            }
        )
        third = connector.poll(second.cursor)
        self.assertEqual(len(third.events), 1)
        self.assertEqual(third.events[0].source_type, "academic.class_scheduled")
        self.assertEqual(third.events[0].payload.get("window_type"), "class_session")

    def test_poll_refreshes_token_after_401(self) -> None:
        recorder = _CalendarAPIWithRefresh()
        connector = GoogleCalendarConnector(
            calendar_id="primary",
            token="expired-token",
            refresh_token="refresh-token",
            client_id="client-id",
            client_secret="client-secret",
            token_endpoint="https://oauth2.googleapis.test/token",
            requester=recorder,
            auth_requester=recorder,
            api_base="https://www.googleapis.test/calendar/v3",
        )
        first = connector.poll(None)
        self.assertEqual(len(first.events), 1)
        self.assertEqual(recorder.refresh_calls, 1)
        auth_headers = [item["auth"] for item in recorder.calls if item["path"] != "/token"]
        self.assertIn("Bearer expired-token", auth_headers)
        self.assertIn("Bearer fresh-token", auth_headers)


if __name__ == "__main__":
    unittest.main()
