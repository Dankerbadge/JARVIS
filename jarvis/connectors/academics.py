from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AcademicsFeedConnector(BaseConnector):
    """Polls a JSON feed of academic signals and emits bounded event envelopes."""

    def __init__(
        self,
        feed_path: str | Path,
        *,
        name: str = "academics_feed",
        default_term_id: str = "current_term",
    ) -> None:
        self.feed_path = Path(feed_path).resolve()
        self.name = name
        self.default_term_id = default_term_id
        if not self.feed_path.exists():
            raise FileNotFoundError(f"Academics feed path not found: {self.feed_path}")

    def _read_items(self) -> list[dict[str, Any]]:
        text = self.feed_path.read_text(encoding="utf-8")
        payload = json.loads(text)
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("items") or payload.get("events") or []
        else:
            items = []
        out: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                out.append(item)
        return out

    def _item_id(self, item: dict[str, Any], index: int) -> str:
        explicit = item.get("id") or item.get("event_id") or item.get("uid")
        if explicit:
            return str(explicit)
        parts = [
            str(item.get("type") or item.get("kind") or "academic.event"),
            str(item.get("course_id") or ""),
            str(item.get("title") or item.get("name") or ""),
            str(item.get("due_at") or item.get("exam_at") or ""),
            str(index),
        ]
        return "|".join(parts)

    def _map_source_type(self, item: dict[str, Any]) -> str:
        raw = str(item.get("type") or item.get("kind") or "").strip().lower()
        mapping = {
            "assignment_due": "academic.assignment_due",
            "exam_scheduled": "academic.exam_scheduled",
            "class_scheduled": "academic.class_scheduled",
            "reading_assigned": "academic.reading_assigned",
            "grade_update": "academic.grade_update",
            "risk_signal": "academic.risk_signal",
            "study_window": "academic.study_window",
            "suppression_window": "academic.suppression_window",
            "announcement": "academic.announcement",
            "professor_message": "academic.professor_message",
            "syllabus_item": "academic.syllabus_item",
        }
        if raw in mapping:
            return mapping[raw]
        if raw.startswith("academic."):
            return raw
        return "academic.risk_signal"

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_seen = set((cursor or {}).get("seen_ids", []))
        seen = set(previous_seen)
        events: list[EventEnvelope] = []
        items = self._read_items()
        for idx, item in enumerate(items):
            item_id = self._item_id(item, idx)
            if item_id in previous_seen:
                continue
            seen.add(item_id)
            source_type = self._map_source_type(item)
            payload = dict(item)
            payload.setdefault("term_id", self.default_term_id)
            payload.setdefault("ingested_from", str(self.feed_path))
            payload.setdefault("source_item_id", item_id)
            payload.setdefault("ingestion_source_kind", "file_import")
            payload.setdefault("ingestion_provider", "local_feed")
            event = EventEnvelope(
                source="academics",
                source_type=source_type,
                payload=payload,
                auth_context="connector_academics_read",
                occurred_at=str(item.get("occurred_at") or item.get("updated_at") or _utc_now_iso()),
            )
            events.append(event)

        if len(seen) > 2000:
            # Keep cursor bounded in memory.
            seen = set(sorted(seen)[-2000:])
        return ConnectorPollResult(events=events, cursor={"seen_ids": sorted(seen)})
