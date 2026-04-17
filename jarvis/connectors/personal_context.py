from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


class PersonalContextConnector(BaseConnector):
    """Poll a local JSON personal-context snapshot and emit one context signal when changed."""

    def __init__(self, context_path: str | Path, *, name: str = "personal_context") -> None:
        self.context_path = Path(context_path).resolve()
        self.name = name
        if not self.context_path.exists():
            raise FileNotFoundError(f"Personal context path not found: {self.context_path}")

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        current_fp = _fingerprint(self.context_path)
        previous_fp = str((cursor or {}).get("fingerprint") or "")
        if previous_fp == current_fp:
            return ConnectorPollResult(events=[], cursor={"fingerprint": current_fp})

        payload_raw = json.loads(self.context_path.read_text(encoding="utf-8"))
        if not isinstance(payload_raw, dict):
            payload_raw = {}
        payload = dict(payload_raw)
        payload.setdefault("project", "personal")
        payload.setdefault("domain", "personal")
        payload.setdefault("ingested_from", str(self.context_path))
        payload.setdefault("ingestion_source_kind", "file_import")
        payload.setdefault("ingestion_provider", "local_personal_context")
        payload.setdefault("source_item_id", f"{self.context_path}:{current_fp}")
        event = EventEnvelope(
            source="personal_context",
            source_type="personal.context_snapshot",
            payload=payload,
            auth_context="connector_personal_context_read",
            occurred_at=str(payload.get("occurred_at") or payload.get("updated_at") or _utc_now_iso()),
        )
        return ConnectorPollResult(events=[event], cursor={"fingerprint": current_fp})

