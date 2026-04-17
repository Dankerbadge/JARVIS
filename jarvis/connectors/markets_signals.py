from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketsSignalsConnector(BaseConnector):
    """Polls market signal feed snapshots and emits suggestion-first market events."""

    def __init__(
        self,
        feed_path: str | Path,
        *,
        name: str = "markets_signals",
        default_account_id: str = "default",
    ) -> None:
        self.feed_path = Path(feed_path).resolve()
        self.name = name
        self.default_account_id = default_account_id
        if not self.feed_path.exists():
            raise FileNotFoundError(f"Markets feed path not found: {self.feed_path}")

    def _read_items(self) -> list[dict[str, Any]]:
        payload = json.loads(self.feed_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("items") or payload.get("signals") or []
        else:
            items = []
        return [item for item in items if isinstance(item, dict)]

    def _item_id(self, item: dict[str, Any], index: int) -> str:
        explicit = item.get("signal_id") or item.get("id") or item.get("uid")
        if explicit:
            return str(explicit)
        parts = [
            str(item.get("symbol") or ""),
            str(item.get("timeframe") or ""),
            str(item.get("thesis") or item.get("reason") or ""),
            str(index),
        ]
        return "|".join(parts)

    def _map_source_type(self, item: dict[str, Any]) -> str:
        raw = str(item.get("type") or item.get("kind") or "signal_detected").strip().lower()
        mapping = {
            "signal_detected": "market.signal_detected",
            "risk_regime_changed": "market.risk_regime_changed",
            "event_upcoming": "market.event_upcoming",
            "opportunity_expired": "market.opportunity_expired",
        }
        if raw in mapping:
            return mapping[raw]
        if raw.startswith("market."):
            return raw
        return "market.signal_detected"

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_seen = set((cursor or {}).get("seen_ids", []))
        seen = set(previous_seen)
        events: list[EventEnvelope] = []
        for index, item in enumerate(self._read_items()):
            item_id = self._item_id(item, index)
            if item_id in previous_seen:
                continue
            seen.add(item_id)
            source_type = self._map_source_type(item)
            payload = dict(item)
            payload.setdefault("project", "markets")
            payload.setdefault("domain", "markets")
            payload.setdefault("account_id", self.default_account_id)
            payload.setdefault("ingested_from", str(self.feed_path))
            payload.setdefault("ingestion_source_kind", "file_import")
            payload.setdefault("ingestion_provider", "local_markets_signals")
            payload.setdefault("source_item_id", item_id)
            events.append(
                EventEnvelope(
                    source="markets",
                    source_type=source_type,
                    payload=payload,
                    auth_context="connector_markets_signals_read",
                    occurred_at=str(item.get("occurred_at") or item.get("updated_at") or _utc_now_iso()),
                )
            )

        if len(seen) > 5000:
            seen = set(sorted(seen)[-5000:])
        return ConnectorPollResult(events=events, cursor={"seen_ids": sorted(seen)})

