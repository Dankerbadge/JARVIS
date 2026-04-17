from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


_COURSE_ID_RE = re.compile(r"\b([A-Z]{2,5})[\s_-]?(\d{2,4}[A-Z]?)\b")


@dataclass(frozen=True)
class _CalendarItem:
    item_id: str
    title: str
    starts_at: str | None
    ends_at: str | None
    course_id: str | None
    source_path: str
    source_type: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_like(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _parse_ics_datetime(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    raw = raw.strip()
    if raw.endswith("Z"):
        try:
            parsed = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            return None
    if "T" in raw:
        try:
            parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            return None
    if len(raw) == 8:
        try:
            parsed = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            return None
    return _parse_iso_like(raw)


def _classify_calendar_title(title: str) -> str:
    text = title.lower()
    if any(token in text for token in ("exam", "midterm", "final", "quiz")):
        return "academic.exam_scheduled"
    if any(token in text for token in ("assignment", "homework", "project", "due", "paper", "lab")):
        return "academic.assignment_due"
    if any(token in text for token in ("study", "review session", "prep")):
        return "academic.study_window"
    return "academic.class_scheduled"


def _infer_course_id(text: str) -> str | None:
    match = _COURSE_ID_RE.search(text.upper())
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


class AcademicCalendarConnector(BaseConnector):
    """Poll local calendar exports (ICS/JSON) and emit academics events."""

    def __init__(
        self,
        calendar_path: str | Path,
        *,
        name: str = "academics_calendar",
        default_term_id: str = "current_term",
    ) -> None:
        self.calendar_path = Path(calendar_path).resolve()
        self.name = name
        self.default_term_id = default_term_id
        if not self.calendar_path.exists():
            raise FileNotFoundError(f"Calendar path not found: {self.calendar_path}")

    def _parse_ics(self) -> list[_CalendarItem]:
        text = self.calendar_path.read_text(encoding="utf-8")
        lines = [line.rstrip("\n") for line in text.splitlines()]
        events: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        for line in lines:
            if line == "BEGIN:VEVENT":
                current = {}
                continue
            if line == "END:VEVENT":
                if current is not None:
                    events.append(current)
                current = None
                continue
            if current is None or ":" not in line:
                continue
            key, value = line.split(":", 1)
            current[key.strip()] = value.strip()

        items: list[_CalendarItem] = []
        for idx, event in enumerate(events):
            uid = (
                event.get("UID")
                or event.get("UID;VALUE=TEXT")
                or f"{self.calendar_path.name}:{idx}"
            )
            title = event.get("SUMMARY") or event.get("SUMMARY;VALUE=TEXT") or "Course event"
            starts_at = _parse_ics_datetime(event.get("DTSTART") or event.get("DTSTART;VALUE=DATE"))
            ends_at = _parse_ics_datetime(event.get("DTEND") or event.get("DTEND;VALUE=DATE"))
            source_type = _classify_calendar_title(title)
            course_id = _infer_course_id(f"{title} {event.get('DESCRIPTION', '')}")
            item_id = f"{uid}|{starts_at or ''}|{source_type}"
            items.append(
                _CalendarItem(
                    item_id=item_id,
                    title=title,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    course_id=course_id,
                    source_path=str(self.calendar_path),
                    source_type=source_type,
                )
            )
        return items

    def _parse_json(self) -> list[_CalendarItem]:
        payload = json.loads(self.calendar_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("events") or payload.get("items") or []
        else:
            rows = []
        items: list[_CalendarItem] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("summary") or f"event-{idx}")
            starts_at = _parse_iso_like(str(row.get("start") or row.get("starts_at") or ""))
            ends_at = _parse_iso_like(str(row.get("end") or row.get("ends_at") or ""))
            course_id = str(row.get("course_id") or "").strip() or _infer_course_id(title)
            source_type = str(row.get("type") or row.get("source_type") or "").strip().lower()
            if not source_type.startswith("academic."):
                source_type = _classify_calendar_title(title)
            item_id = str(row.get("id") or f"{self.calendar_path.name}:{idx}:{starts_at or ''}")
            items.append(
                _CalendarItem(
                    item_id=item_id,
                    title=title,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    course_id=course_id,
                    source_path=str(self.calendar_path),
                    source_type=source_type,
                )
            )
        return items

    def _read_items(self) -> list[_CalendarItem]:
        if self.calendar_path.suffix.lower() == ".json":
            return self._parse_json()
        return self._parse_ics()

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_seen = set((cursor or {}).get("seen_ids", []))
        seen = set(previous_seen)
        events: list[EventEnvelope] = []
        for item in self._read_items():
            if item.item_id in previous_seen:
                continue
            seen.add(item.item_id)
            payload: dict[str, Any] = {
                "project": "academics",
                "domain": "academics",
                "term_id": self.default_term_id,
                "course_id": item.course_id,
                "title": item.title,
                "starts_at": item.starts_at,
                "ends_at": item.ends_at,
                "ingested_from": item.source_path,
                "ingestion_source_kind": "file_import",
                "ingestion_provider": "local_calendar",
                "source_item_id": item.item_id,
            }
            if item.source_type == "academic.assignment_due":
                payload["due_at"] = item.starts_at
            if item.source_type == "academic.exam_scheduled":
                payload["exam_at"] = item.starts_at
            if item.source_type == "academic.class_scheduled":
                payload["window_type"] = "class_session"
                payload["window_start_at"] = item.starts_at
                payload["window_end_at"] = item.ends_at or item.starts_at
            if item.source_type == "academic.study_window":
                payload["window_type"] = "study_window"
                payload["window_start_at"] = item.starts_at
                payload["window_end_at"] = item.ends_at or item.starts_at

            events.append(
                EventEnvelope(
                    source="academics",
                    source_type=item.source_type,
                    payload=payload,
                    auth_context="connector_academics_calendar_read",
                    occurred_at=item.starts_at or _utc_now_iso(),
                )
            )

        if len(seen) > 5000:
            seen = set(sorted(seen)[-5000:])
        return ConnectorPollResult(events=events, cursor={"seen_ids": sorted(seen)})
