from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Sequence

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


_COURSE_ID_RE = re.compile(r"\b([A-Z]{2,5})[\s_-]?(\d{2,4}[A-Z]?)\b")


class GoogleCalendarConnectorError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_iso(value: str | None) -> str | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def _infer_course_id(text: str) -> str | None:
    match = _COURSE_ID_RE.search(text.upper())
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


def _classify(title: str, description: str) -> str:
    text = f"{title}\n{description}".lower()
    if any(token in text for token in ("exam", "midterm", "final", "quiz")):
        return "academic.exam_scheduled"
    if any(token in text for token in ("assignment", "homework", "project", "paper", "lab", "due")):
        return "academic.assignment_due"
    if any(token in text for token in ("study", "office hour", "review session", "prep")):
        return "academic.study_window"
    return "academic.class_scheduled"


class GoogleCalendarConnector(BaseConnector):
    """Read-only Google Calendar connector for Academics domain ingestion."""

    def __init__(
        self,
        *,
        calendar_id: str,
        token: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_endpoint: str = "https://oauth2.googleapis.com/token",
        name: str | None = None,
        default_term_id: str = "current_term",
        api_base: str = "https://www.googleapis.com/calendar/v3",
        lookback_days: int = 30,
        max_results: int = 250,
        requester: Callable[[urllib.request.Request], Any] | None = None,
        auth_requester: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        calendar_id = str(calendar_id or "").strip()
        token = str(token or "").strip()
        refresh_token = str(refresh_token or "").strip()
        client_id = str(client_id or "").strip()
        client_secret = str(client_secret or "").strip()
        if not calendar_id:
            raise ValueError("calendar_id is required.")
        if not token and not (refresh_token and client_id and client_secret):
            raise ValueError("Google access token or refresh-token credentials are required.")
        self.calendar_id = calendar_id
        self.token = token
        self.refresh_token = refresh_token or None
        self.client_id = client_id or None
        self.client_secret = client_secret or None
        self.token_endpoint = str(token_endpoint or "https://oauth2.googleapis.com/token").strip()
        self.last_token_refresh_at: str | None = None
        self.last_token_expires_in: int | None = None
        self.name = str(name or f"academics_google_calendar:{calendar_id}")
        self.default_term_id = default_term_id
        self.api_base = api_base.rstrip("/")
        self.lookback_days = max(1, int(lookback_days))
        self.max_results = max(1, min(int(max_results), 2500))
        self.requester = requester or urllib.request.urlopen
        self.auth_requester = auth_requester or self.requester

    def _can_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id and self.client_secret)

    def _refresh_access_token(self) -> str:
        if not self._can_refresh():
            raise GoogleCalendarConnectorError("Refresh requested but refresh-token credentials are not configured.")
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.token_endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "JARVIS/0.1",
            },
            method="POST",
        )
        try:
            with self.auth_requester(request) as response:
                raw = response.read().decode("utf-8")
                payload = json.loads(raw) if raw else {}
                access_token = str((payload or {}).get("access_token") or "").strip()
                if not access_token:
                    raise GoogleCalendarConnectorError("Refresh response did not include access_token.")
                self.token = access_token
                expires_raw = (payload or {}).get("expires_in")
                try:
                    self.last_token_expires_in = int(expires_raw) if expires_raw is not None else None
                except (TypeError, ValueError):
                    self.last_token_expires_in = None
                self.last_token_refresh_at = _utc_now_iso()
                return self.token
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
            raise GoogleCalendarConnectorError(
                f"Google token refresh failed: [{exc.code}] {detail}"
            ) from exc

    def _ensure_access_token(self) -> None:
        if self.token:
            return
        self._refresh_access_token()

    def _headers(self) -> dict[str, str]:
        self._ensure_access_token()
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "JARVIS/0.1",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        ignore_codes: Sequence[int] = (),
        _retried_after_refresh: bool = False,
    ) -> dict[str, Any]:
        query_string = urllib.parse.urlencode(
            {k: v for k, v in (query or {}).items() if v is not None},
            doseq=True,
        )
        url = f"{self.api_base}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        request = urllib.request.Request(url, headers=self._headers(), method=method)
        try:
            with self.requester(request) as response:
                raw = response.read().decode("utf-8")
                payload = json.loads(raw) if raw else {}
                if not isinstance(payload, dict):
                    raise GoogleCalendarConnectorError("Calendar API returned non-object payload.")
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code in set(ignore_codes):
                return {}
            if exc.code == 401 and (not _retried_after_refresh) and self._can_refresh():
                try:
                    if hasattr(exc, "read"):
                        exc.read()
                except Exception:
                    pass
                try:
                    if hasattr(exc, "close"):
                        exc.close()
                except Exception:
                    pass
                self._refresh_access_token()
                return self._request(
                    method,
                    path,
                    query=query,
                    ignore_codes=ignore_codes,
                    _retried_after_refresh=True,
                )
            detail = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
            raise GoogleCalendarConnectorError(
                f"Google Calendar API request failed: {method} {path} [{exc.code}] {detail}"
            ) from exc

    def _iter_events(self, *, updated_min: str | None) -> list[dict[str, Any]]:
        calendar_escaped = urllib.parse.quote(self.calendar_id, safe="")
        page_token: str | None = None
        pages = 0
        events: list[dict[str, Any]] = []
        while True:
            pages += 1
            if pages > 20:
                break
            query: dict[str, Any] = {
                "singleEvents": "true",
                "orderBy": "updated",
                "showDeleted": "false",
                "maxResults": str(self.max_results),
                "timeMin": (_utc_now() - timedelta(days=self.lookback_days)).isoformat(),
                "pageToken": page_token,
            }
            if updated_min:
                query["updatedMin"] = updated_min
            payload = self._request("GET", f"/calendars/{calendar_escaped}/events", query=query)
            page_items = payload.get("items") or []
            if isinstance(page_items, list):
                events.extend(item for item in page_items if isinstance(item, dict))
            page_token = str(payload.get("nextPageToken") or "").strip() or None
            if not page_token:
                break
        return events

    def _event_envelope(self, raw_event: dict[str, Any]) -> EventEnvelope | None:
        status = str(raw_event.get("status") or "").strip().lower()
        if status == "cancelled":
            return None
        provider_event_id = str(raw_event.get("id") or "").strip()
        if not provider_event_id:
            return None
        title = str(raw_event.get("summary") or "Calendar event")
        description = str(raw_event.get("description") or "")
        starts_at = _normalize_iso(
            str((raw_event.get("start") or {}).get("dateTime") or (raw_event.get("start") or {}).get("date") or "")
        )
        ends_at = _normalize_iso(
            str((raw_event.get("end") or {}).get("dateTime") or (raw_event.get("end") or {}).get("date") or "")
        )
        updated_at = _normalize_iso(str(raw_event.get("updated") or "")) or _utc_now_iso()
        source_type = _classify(title, description)
        course_id = _infer_course_id(f"{title}\n{description}")

        payload: dict[str, Any] = {
            "project": "academics",
            "domain": "academics",
            "term_id": self.default_term_id,
            "course_id": course_id,
            "title": title,
            "description_excerpt": " ".join(description.split())[:280],
            "starts_at": starts_at,
            "ends_at": ends_at,
            "ingested_from": f"google_calendar:{self.calendar_id}",
            "ingestion_source_kind": "provider",
            "ingestion_provider": "google_calendar",
            "source_item_id": f"{self.calendar_id}:{provider_event_id}",
            "provider_event_id": provider_event_id,
            "provider_calendar_id": self.calendar_id,
            "provider_updated_at": updated_at,
        }
        if source_type == "academic.assignment_due":
            payload["due_at"] = starts_at
        if source_type == "academic.exam_scheduled":
            payload["exam_at"] = starts_at
        if source_type == "academic.class_scheduled":
            payload["window_type"] = "class_session"
            payload["window_start_at"] = starts_at
            payload["window_end_at"] = ends_at or starts_at
        if source_type == "academic.study_window":
            payload["window_type"] = "study_window"
            payload["window_start_at"] = starts_at
            payload["window_end_at"] = ends_at or starts_at
        return EventEnvelope(
            source="academics_google_calendar",
            source_type=source_type,
            payload=payload,
            auth_context="connector_google_calendar_read",
            occurred_at=starts_at or updated_at,
        )

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_updated_iso = str((cursor or {}).get("updated_cursor") or "").strip() or None
        previous_updated = _parse_iso(previous_updated_iso)
        previous_seen = set((cursor or {}).get("seen_at_cursor") or [])

        events: list[EventEnvelope] = []
        max_updated = previous_updated
        max_updated_iso = previous_updated_iso
        seen_at_max = set(previous_seen if previous_updated_iso else [])

        for raw in self._iter_events(updated_min=previous_updated_iso):
            provider_event_id = str(raw.get("id") or "").strip()
            updated_iso = _normalize_iso(str(raw.get("updated") or "")) or _utc_now_iso()
            updated_dt = _parse_iso(updated_iso)
            if updated_dt is None:
                continue
            if previous_updated is not None:
                if updated_dt < previous_updated:
                    continue
                if updated_dt == previous_updated and provider_event_id in previous_seen:
                    continue
            envelope = self._event_envelope(raw)
            if envelope is None:
                continue
            events.append(envelope)

            if max_updated is None or updated_dt > max_updated:
                max_updated = updated_dt
                max_updated_iso = updated_iso
                seen_at_max = {provider_event_id}
            elif updated_dt == max_updated:
                seen_at_max.add(provider_event_id)

        if max_updated_iso is None:
            max_updated_iso = previous_updated_iso or _utc_now_iso()
            seen_at_max = previous_seen
        if len(seen_at_max) > 5000:
            seen_at_max = set(sorted(seen_at_max)[-5000:])
        next_cursor = {
            "updated_cursor": max_updated_iso,
            "seen_at_cursor": sorted(seen_at_max),
        }
        return ConnectorPollResult(events=events, cursor=next_cursor)
