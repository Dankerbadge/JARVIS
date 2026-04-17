from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketsCalendarConnector(BaseConnector):
    """Polls market event calendar snapshots and emits upcoming/expiry events."""

    def __init__(
        self,
        calendar_path: str | Path,
        *,
        name: str = "markets_calendar",
        default_account_id: str = "default",
    ) -> None:
        self.calendar_path = Path(calendar_path).resolve()
        self.name = name
        self.default_account_id = default_account_id
        if not self.calendar_path.exists():
            raise FileNotFoundError(f"Markets calendar path not found: {self.calendar_path}")

    def _read_items(self) -> list[dict[str, Any]]:
        payload = json.loads(self.calendar_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("items") or payload.get("events") or []
        else:
            items = []
        return [item for item in items if isinstance(item, dict)]

    def _item_id(self, item: dict[str, Any], index: int) -> str:
        explicit = item.get("event_id") or item.get("id") or item.get("uid")
        if explicit:
            return str(explicit)
        parts = [
            str(item.get("symbol") or ""),
            str(item.get("event_kind") or item.get("kind") or ""),
            str(item.get("event_at") or item.get("occurred_at") or ""),
            str(index),
        ]
        return "|".join(parts)

    def _source_type(self, item: dict[str, Any]) -> str:
        raw = str(item.get("type") or item.get("kind") or "event_upcoming").strip().lower()
        if raw in {"event_upcoming", "market.event_upcoming"}:
            return "market.event_upcoming"
        if raw in {"opportunity_expired", "market.opportunity_expired"}:
            return "market.opportunity_expired"
        if raw.startswith("market."):
            return raw
        return "market.event_upcoming"

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_seen = set((cursor or {}).get("seen_ids", []))
        seen = set(previous_seen)
        events: list[EventEnvelope] = []
        for index, item in enumerate(self._read_items()):
            item_id = self._item_id(item, index)
            if item_id in previous_seen:
                continue
            seen.add(item_id)
            source_type = self._source_type(item)
            payload = dict(item)
            payload.setdefault("project", "markets")
            payload.setdefault("domain", "markets")
            payload.setdefault("account_id", self.default_account_id)
            payload.setdefault("ingested_from", str(self.calendar_path))
            payload.setdefault("ingestion_source_kind", "file_import")
            payload.setdefault("ingestion_provider", "local_markets_calendar")
            payload.setdefault("source_item_id", item_id)
            occurred_at = str(
                item.get("occurred_at")
                or item.get("event_at")
                or item.get("updated_at")
                or _utc_now_iso()
            )
            events.append(
                EventEnvelope(
                    source="markets",
                    source_type=source_type,
                    payload=payload,
                    auth_context="connector_markets_calendar_read",
                    occurred_at=occurred_at,
                )
            )

        if len(seen) > 4000:
            seen = set(sorted(seen)[-4000:])
        return ConnectorPollResult(events=events, cursor={"seen_ids": sorted(seen)})

