from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
import re
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


_ISO_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?)\b"
)
_COURSE_ID_RE = re.compile(r"\b([A-Z]{2,5})[\s_-]?(\d{2,4}[A-Z]?)\b")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _parse_iso(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _find_course_id(text: str) -> str | None:
    match = _COURSE_ID_RE.search(text.upper())
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


def _line_due_time(text: str) -> str | None:
    match = _ISO_RE.search(text)
    if not match:
        return None
    return _parse_iso(match.group(1))


class AcademicMaterialsConnector(BaseConnector):
    """Poll local syllabus/email/material files and emit read-only academics signals."""

    def __init__(
        self,
        materials_path: str | Path,
        *,
        name: str = "academics_materials",
        default_term_id: str = "current_term",
    ) -> None:
        self.materials_path = Path(materials_path).resolve()
        self.name = name
        self.default_term_id = default_term_id
        if not self.materials_path.exists():
            raise FileNotFoundError(f"Academics materials path not found: {self.materials_path}")

    def _iter_material_files(self) -> list[Path]:
        if self.materials_path.is_file():
            return [self.materials_path]
        allowed = {".txt", ".md", ".markdown", ".eml", ".json"}
        files = [
            path
            for path in sorted(self.materials_path.rglob("*"))
            if path.is_file() and path.suffix.lower() in allowed
        ]
        return files

    def _stable_id(self, path: Path, suffix: str) -> str:
        digest = hashlib.sha1(f"{path}:{suffix}".encode("utf-8")).hexdigest()[:16]
        return f"{path.name}:{digest}"

    def _announcement_event(self, *, path: Path, text: str, source_type: str) -> EventEnvelope:
        course_id = _find_course_id(f"{path.name}\n{text}")
        payload = {
            "project": "academics",
            "domain": "academics",
            "term_id": self.default_term_id,
            "course_id": course_id,
            "title": path.stem,
            "message_excerpt": " ".join(text.strip().split())[:220],
            "ingested_from": str(path),
            "ingestion_source_kind": "file_import",
            "ingestion_provider": "local_materials",
            "source_item_id": self._stable_id(path, source_type),
        }
        return EventEnvelope(
            source="academics",
            source_type=source_type,
            payload=payload,
            auth_context="connector_academics_materials_read",
            occurred_at=_utc_now_iso(),
        )

    def _events_from_json_file(self, path: Path) -> list[EventEnvelope]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("items") or payload.get("events") or []
        else:
            rows = []
        events: list[EventEnvelope] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            source_type = str(row.get("type") or row.get("source_type") or "academic.announcement").strip().lower()
            if not source_type.startswith("academic."):
                source_type = f"academic.{source_type}"
            title = str(row.get("title") or row.get("name") or f"material-{idx}")
            course_id = str(row.get("course_id") or "").strip() or _find_course_id(title)
            event_payload = {
                "project": "academics",
                "domain": "academics",
                "term_id": str(row.get("term_id") or self.default_term_id),
                "course_id": course_id,
                "title": title,
                "ingested_from": str(path),
                "ingestion_source_kind": str(row.get("ingestion_source_kind") or "file_import"),
                "ingestion_provider": str(row.get("ingestion_provider") or "local_materials"),
                "source_item_id": str(row.get("id") or self._stable_id(path, f"{source_type}:{idx}")),
            }
            for key in (
                "due_at",
                "exam_at",
                "starts_at",
                "ends_at",
                "window_start_at",
                "window_end_at",
                "window_type",
                "severity",
                "grade",
                "reason",
            ):
                if key in row:
                    event_payload[key] = row[key]
            events.append(
                EventEnvelope(
                    source="academics",
                    source_type=source_type,
                    payload=event_payload,
                    auth_context="connector_academics_materials_read",
                    occurred_at=str(row.get("occurred_at") or row.get("updated_at") or _utc_now_iso()),
                )
            )
        return events

    def _events_from_text(self, path: Path, text: str, *, source_type_hint: str) -> list[EventEnvelope]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        joined = "\n".join(lines)
        course_id = _find_course_id(f"{path.name}\n{joined}")
        events: list[EventEnvelope] = []

        events.append(self._announcement_event(path=path, text=joined, source_type=source_type_hint))

        for idx, line in enumerate(lines[:250]):
            lower = line.lower()
            when = _line_due_time(line)
            if any(token in lower for token in ("exam", "midterm", "final", "quiz")):
                events.append(
                    EventEnvelope(
                        source="academics",
                        source_type="academic.exam_scheduled",
                        payload={
                            "project": "academics",
                            "domain": "academics",
                            "term_id": self.default_term_id,
                            "course_id": course_id,
                            "title": line[:180],
                            "exam_at": when,
                            "ingested_from": str(path),
                            "ingestion_source_kind": "file_import",
                            "ingestion_provider": "local_materials",
                            "source_item_id": self._stable_id(path, f"exam:{idx}"),
                        },
                        auth_context="connector_academics_materials_read",
                        occurred_at=when or _utc_now_iso(),
                    )
                )
                continue
            if any(token in lower for token in ("assignment", "homework", "project", "paper", "lab")) and (
                "due" in lower or when
            ):
                events.append(
                    EventEnvelope(
                        source="academics",
                        source_type="academic.assignment_due",
                        payload={
                            "project": "academics",
                            "domain": "academics",
                            "term_id": self.default_term_id,
                            "course_id": course_id,
                            "title": line[:180],
                            "due_at": when,
                            "ingested_from": str(path),
                            "ingestion_source_kind": "file_import",
                            "ingestion_provider": "local_materials",
                            "source_item_id": self._stable_id(path, f"due:{idx}"),
                        },
                        auth_context="connector_academics_materials_read",
                        occurred_at=when or _utc_now_iso(),
                    )
                )
                continue
            if "reading" in lower or "chapter" in lower:
                events.append(
                    EventEnvelope(
                        source="academics",
                        source_type="academic.reading_assigned",
                        payload={
                            "project": "academics",
                            "domain": "academics",
                            "term_id": self.default_term_id,
                            "course_id": course_id,
                            "title": line[:180],
                            "topics": [line[:120]],
                            "ingested_from": str(path),
                            "ingestion_source_kind": "file_import",
                            "ingestion_provider": "local_materials",
                            "source_item_id": self._stable_id(path, f"reading:{idx}"),
                        },
                        auth_context="connector_academics_materials_read",
                        occurred_at=_utc_now_iso(),
                    )
                )
        return events

    def _events_from_eml(self, path: Path) -> list[EventEnvelope]:
        raw = path.read_bytes()
        message = BytesParser(policy=policy.default).parsebytes(raw)
        subject = str(message.get("subject") or "")
        sender = str(message.get("from") or "")
        body = ""
        if message.is_multipart():
            for part in message.walk():
                content_type = str(part.get_content_type() or "")
                if content_type == "text/plain":
                    body = str(part.get_content() or "")
                    break
        else:
            body = str(message.get_content() or "")
        source_type = "academic.professor_message"
        if "prof" not in sender.lower() and "instructor" not in sender.lower():
            source_type = "academic.announcement"
        text = f"Subject: {subject}\nFrom: {sender}\n{body}"
        return self._events_from_text(path, text, source_type_hint=source_type)

    def _events_for_file(self, path: Path) -> list[EventEnvelope]:
        suffix = path.suffix.lower()
        if suffix == ".json":
            return self._events_from_json_file(path)
        if suffix == ".eml":
            return self._events_from_eml(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        hint = "academic.syllabus_item" if "syllabus" in path.name.lower() else "academic.announcement"
        return self._events_from_text(path, text, source_type_hint=hint)

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous = dict((cursor or {}).get("file_fingerprints") or {})
        next_fp = dict(previous)
        events: list[EventEnvelope] = []
        for path in self._iter_material_files():
            current = _fingerprint(path)
            key = str(path)
            if previous.get(key) == current:
                continue
            next_fp[key] = current
            events.extend(self._events_for_file(path))

        # prune deleted files from cursor
        existing = {str(path) for path in self._iter_material_files()}
        next_fp = {k: v for k, v in next_fp.items() if k in existing}
        return ConnectorPollResult(events=events, cursor={"file_fingerprints": next_fp})
