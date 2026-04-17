from __future__ import annotations

import hashlib
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


def _stable_hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class MarketsPositionsConnector(BaseConnector):
    """Polls positions/exposure snapshot and emits position + regime changes."""

    def __init__(
        self,
        snapshot_path: str | Path,
        *,
        name: str = "markets_positions",
        default_account_id: str = "default",
    ) -> None:
        self.snapshot_path = Path(snapshot_path).resolve()
        self.name = name
        self.default_account_id = default_account_id
        if not self.snapshot_path.exists():
            raise FileNotFoundError(f"Markets positions snapshot not found: {self.snapshot_path}")

    def _load_snapshot(self) -> dict[str, Any]:
        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_fingerprint = str((cursor or {}).get("fingerprint") or "")
        current_fingerprint = _fingerprint(self.snapshot_path)
        if previous_fingerprint == current_fingerprint:
            return ConnectorPollResult(events=[], cursor={"fingerprint": current_fingerprint})

        snapshot = self._load_snapshot()
        account_id = str(snapshot.get("account_id") or self.default_account_id)
        positions = snapshot.get("positions") if isinstance(snapshot.get("positions"), list) else []
        risk_regime = str(snapshot.get("risk_regime") or snapshot.get("regime") or "").strip().lower()
        exposure = snapshot.get("gross_exposure_pct")
        net_exposure = snapshot.get("net_exposure_pct")
        occurred_at = str(snapshot.get("as_of") or snapshot.get("updated_at") or _utc_now_iso())

        base_payload = {
            "project": "markets",
            "domain": "markets",
            "account_id": account_id,
            "positions": positions,
            "gross_exposure_pct": exposure,
            "net_exposure_pct": net_exposure,
            "risk_regime": risk_regime or None,
            "ingested_from": str(self.snapshot_path),
            "ingestion_source_kind": "file_import",
            "ingestion_provider": "local_markets_positions",
            "source_item_id": f"{self.snapshot_path}:{current_fingerprint}",
        }

        events: list[EventEnvelope] = [
            EventEnvelope(
                source="markets",
                source_type="market.position_snapshot",
                payload=dict(base_payload),
                auth_context="connector_markets_positions_read",
                occurred_at=occurred_at,
            )
        ]

        previous_regime = str((cursor or {}).get("risk_regime") or "").strip().lower()
        previous_positions_hash = str((cursor or {}).get("positions_hash") or "")
        positions_hash = _stable_hash(positions)
        if risk_regime and (risk_regime != previous_regime or positions_hash != previous_positions_hash):
            events.append(
                EventEnvelope(
                    source="markets",
                    source_type="market.risk_regime_changed",
                    payload={
                        **base_payload,
                        "previous_risk_regime": previous_regime or None,
                    },
                    auth_context="connector_markets_positions_read",
                    occurred_at=occurred_at,
                )
            )

        next_cursor = {
            "fingerprint": current_fingerprint,
            "risk_regime": risk_regime,
            "positions_hash": positions_hash,
        }
        return ConnectorPollResult(events=events, cursor=next_cursor)

