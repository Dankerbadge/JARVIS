from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketsOutcomesConnector(BaseConnector):
    """Polls investing-bot handoff outcomes and emits closed-loop market outcome events."""

    def __init__(
        self,
        feed_path: str | Path,
        *,
        name: str = "markets_outcomes",
        default_account_id: str = "default",
    ) -> None:
        self.feed_path = Path(feed_path).resolve()
        self.name = name
        self.default_account_id = default_account_id
        if not self.feed_path.exists():
            raise FileNotFoundError(f"Markets outcomes path not found: {self.feed_path}")

    def _read_items(self) -> list[dict[str, Any]]:
        payload = json.loads(self.feed_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("items") or payload.get("outcomes") or []
        else:
            items = []
        return [item for item in items if isinstance(item, dict)]

    def _item_id(self, item: dict[str, Any], index: int) -> str:
        explicit = item.get("handoff_id") or item.get("id") or item.get("uid")
        if explicit:
            return str(explicit)
        parts = [
            str(item.get("signal_id") or ""),
            str(item.get("symbol") or ""),
            str(item.get("status") or item.get("outcome") or ""),
            str(index),
        ]
        return "|".join(parts)

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_seen = set((cursor or {}).get("seen_ids", []))
        seen = set(previous_seen)
        events: list[EventEnvelope] = []
        for index, item in enumerate(self._read_items()):
            item_id = self._item_id(item, index)
            if item_id in previous_seen:
                continue
            seen.add(item_id)
            payload = dict(item)
            payload.setdefault("project", "markets")
            payload.setdefault("domain", "markets")
            payload.setdefault("account_id", self.default_account_id)
            payload.setdefault("handoff_id", item_id)
            payload.setdefault("ingested_from", str(self.feed_path))
            payload.setdefault("ingestion_source_kind", "file_import")
            payload.setdefault("ingestion_provider", "local_markets_outcomes")
            payload.setdefault("source_item_id", item_id)
            events.append(
                EventEnvelope(
                    source="markets",
                    source_type="market.handoff_outcome",
                    payload=payload,
                    auth_context="connector_markets_outcomes_read",
                    occurred_at=str(item.get("occurred_at") or item.get("updated_at") or _utc_now_iso()),
                )
            )

        if len(seen) > 5000:
            seen = set(sorted(seen)[-5000:])
        return ConnectorPollResult(events=events, cursor={"seen_ids": sorted(seen)})

