from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Sequence

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


_COURSE_ID_RE = re.compile(r"\b([A-Z]{2,5})[\s_-]?(\d{2,4}[A-Z]?)\b")
_ISO_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?)\b"
)
_GRADE_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%?\b")


class GmailAcademicsConnectorError(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _decode_b64url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    padding = "=" * ((4 - len(raw) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(raw + padding)
    except (ValueError, TypeError):
        return ""
    return decoded.decode("utf-8", errors="ignore")


def _iter_payload_parts(node: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [node]
    for part in node.get("parts", []) or []:
        if isinstance(part, dict):
            out.extend(_iter_payload_parts(part))
    return out


def _header(payload: dict[str, Any], name: str) -> str:
    headers = payload.get("headers") or []
    for item in headers:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip().lower() == name.lower():
            return str(item.get("value") or "")
    return ""


def _infer_course_id(text: str) -> str | None:
    match = _COURSE_ID_RE.search(text.upper())
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


def _extract_due_like(text: str) -> str | None:
    match = _ISO_RE.search(text)
    if not match:
        return None
    return _normalize_iso(match.group(1))


def _extract_grade(text: str) -> float | None:
    lower = text.lower()
    if "grade" not in lower and "score" not in lower:
        return None
    for match in _GRADE_RE.finditer(text):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if 0 <= value <= 100:
            return value
    return None


def _classify(subject: str, body: str, sender: str) -> str:
    text = f"{subject}\n{body}".lower()
    if any(token in text for token in ("exam", "midterm", "final", "quiz")):
        return "academic.exam_scheduled"
    if any(token in text for token in ("assignment", "homework", "project", "paper", "lab")) and (
        "due" in text or _extract_due_like(text)
    ):
        return "academic.assignment_due"
    if "reading" in text or "chapter" in text:
        return "academic.reading_assigned"
    if "syllabus" in text:
        return "academic.syllabus_item"
    if _extract_grade(text) is not None:
        return "academic.grade_update"
    sender_lower = sender.lower()
    if any(token in sender_lower for token in ("prof", "instructor", ".edu")):
        return "academic.professor_message"
    return "academic.announcement"


class GmailAcademicsConnector(BaseConnector):
    """Read-only Gmail connector for academics announcements and deadline signals."""

    def __init__(
        self,
        *,
        token: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_endpoint: str = "https://oauth2.googleapis.com/token",
        query: str,
        user_id: str = "me",
        name: str = "academics_gmail",
        default_term_id: str = "current_term",
        max_results: int = 50,
        api_base: str = "https://gmail.googleapis.com/gmail/v1",
        requester: Callable[[urllib.request.Request], Any] | None = None,
        auth_requester: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        token = str(token or "").strip()
        refresh_token = str(refresh_token or "").strip()
        client_id = str(client_id or "").strip()
        client_secret = str(client_secret or "").strip()
        query = str(query or "").strip()
        if not token and not (refresh_token and client_id and client_secret):
            raise ValueError("Google access token or refresh-token credentials are required.")
        if not query:
            raise ValueError("Gmail query is required.")
        self.token = token
        self.refresh_token = refresh_token or None
        self.client_id = client_id or None
        self.client_secret = client_secret or None
        self.token_endpoint = str(token_endpoint or "https://oauth2.googleapis.com/token").strip()
        self.last_token_refresh_at: str | None = None
        self.last_token_expires_in: int | None = None
        self.query = query
        self.user_id = str(user_id or "me")
        self.name = name
        self.default_term_id = default_term_id
        self.max_results = max(1, min(int(max_results), 500))
        self.api_base = api_base.rstrip("/")
        self.requester = requester or urllib.request.urlopen
        self.auth_requester = auth_requester or self.requester

    def _can_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id and self.client_secret)

    def _refresh_access_token(self) -> str:
        if not self._can_refresh():
            raise GmailAcademicsConnectorError("Refresh requested but refresh-token credentials are not configured.")
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
                    raise GmailAcademicsConnectorError("Refresh response did not include access_token.")
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
            raise GmailAcademicsConnectorError(
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
                    raise GmailAcademicsConnectorError("Gmail API returned non-object payload.")
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
            raise GmailAcademicsConnectorError(
                f"Gmail API request failed: {method} {path} [{exc.code}] {detail}"
            ) from exc

    def _list_message_ids(self) -> list[str]:
        page_token: str | None = None
        ids: list[str] = []
        pages = 0
        while len(ids) < self.max_results:
            pages += 1
            if pages > 10:
                break
            payload = self._request(
                "GET",
                f"/users/{urllib.parse.quote(self.user_id, safe='')}/messages",
                query={
                    "q": self.query,
                    "maxResults": str(min(100, self.max_results - len(ids))),
                    "includeSpamTrash": "false",
                    "pageToken": page_token,
                },
            )
            rows = payload.get("messages") or []
            if isinstance(rows, list):
                ids.extend(
                    str(item.get("id") or "").strip()
                    for item in rows
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                )
            page_token = str(payload.get("nextPageToken") or "").strip() or None
            if not page_token:
                break
        return ids[: self.max_results]

    def _fetch_message(self, message_id: str) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/users/{urllib.parse.quote(self.user_id, safe='')}/messages/{urllib.parse.quote(message_id, safe='')}",
            query={"format": "full"},
        )
        if not isinstance(payload, dict):
            raise GmailAcademicsConnectorError("Invalid message payload.")
        return payload

    def _extract_text_body(self, payload: dict[str, Any], *, fallback: str) -> str:
        for part in _iter_payload_parts(payload):
            mime_type = str(part.get("mimeType") or "").strip().lower()
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            decoded = _decode_b64url(body.get("data"))
            if decoded and mime_type == "text/plain":
                return decoded
        for part in _iter_payload_parts(payload):
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            decoded = _decode_b64url(body.get("data"))
            if decoded:
                return decoded
        return fallback

    def _occurred_at(self, *, internal_date_ms: str, date_header: str) -> str:
        raw_ms = str(internal_date_ms or "").strip()
        if raw_ms.isdigit():
            try:
                epoch_ms = int(raw_ms)
                return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                pass
        if date_header:
            try:
                parsed = parsedate_to_datetime(date_header)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                pass
        return _utc_now_iso()

    def _to_event(self, message: dict[str, Any]) -> EventEnvelope | None:
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            return None
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        subject = _header(payload, "Subject")
        sender = _header(payload, "From")
        date_header = _header(payload, "Date")
        snippet = str(message.get("snippet") or "")
        body = self._extract_text_body(payload, fallback=snippet)
        body_excerpt = " ".join(body.strip().split())[:420]
        source_type = _classify(subject, body, sender)
        due_like = _extract_due_like(f"{subject}\n{body}")
        course_id = _infer_course_id(f"{subject}\n{body}")
        grade = _extract_grade(f"{subject}\n{body}")

        occurred_at = self._occurred_at(
            internal_date_ms=str(message.get("internalDate") or ""),
            date_header=date_header,
        )
        event_payload: dict[str, Any] = {
            "project": "academics",
            "domain": "academics",
            "term_id": self.default_term_id,
            "course_id": course_id,
            "title": subject or "Course message",
            "message_excerpt": body_excerpt or snippet[:220],
            "sender": sender,
            "subject": subject,
            "ingested_from": f"gmail:{self.query}",
            "ingestion_source_kind": "provider",
            "ingestion_provider": "gmail",
            "source_item_id": message_id,
            "provider_message_id": message_id,
            "provider_thread_id": str(message.get("threadId") or ""),
            "provider_history_id": str(message.get("historyId") or ""),
        }
        if source_type == "academic.assignment_due":
            event_payload["due_at"] = due_like or occurred_at
        if source_type == "academic.exam_scheduled":
            event_payload["exam_at"] = due_like or occurred_at
        if source_type == "academic.grade_update" and grade is not None:
            event_payload["grade"] = grade
        if source_type == "academic.reading_assigned":
            event_payload["topics"] = [subject or body_excerpt[:120]]
        return EventEnvelope(
            source="academics_gmail",
            source_type=source_type,
            payload=event_payload,
            auth_context="connector_gmail_read",
            occurred_at=occurred_at,
        )

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_history = int((cursor or {}).get("history_id_cursor") or 0)
        previous_seen = set((cursor or {}).get("seen_at_history") or [])

        message_ids = self._list_message_ids()
        if not message_ids:
            return ConnectorPollResult(
                events=[],
                cursor={
                    "history_id_cursor": previous_history,
                    "seen_at_history": sorted(previous_seen),
                },
            )

        messages = [self._fetch_message(message_id) for message_id in message_ids]
        messages = sorted(
            messages,
            key=lambda row: int(str(row.get("historyId") or "0") or "0"),
        )

        events: list[EventEnvelope] = []
        max_history = previous_history
        seen_at_max = set(previous_seen if previous_history else [])

        for message in messages:
            message_id = str(message.get("id") or "").strip()
            history_id_raw = str(message.get("historyId") or "0")
            history_id = int(history_id_raw) if history_id_raw.isdigit() else 0
            if history_id < previous_history:
                continue
            if history_id == previous_history and message_id in previous_seen:
                continue

            envelope = self._to_event(message)
            if envelope is None:
                continue
            events.append(envelope)

            if history_id > max_history:
                max_history = history_id
                seen_at_max = {message_id}
            elif history_id == max_history:
                seen_at_max.add(message_id)

        if len(seen_at_max) > 5000:
            seen_at_max = set(sorted(seen_at_max)[-5000:])
        return ConnectorPollResult(
            events=events,
            cursor={
                "history_id_cursor": max_history,
                "seen_at_history": sorted(seen_at_max),
            },
        )
