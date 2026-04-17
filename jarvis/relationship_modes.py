from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"rmd_{uuid4().hex}"


@dataclass(frozen=True)
class ModeDecision:
    decision_id: str
    mode: str
    reason: str
    confidence: float
    context: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "mode": self.mode,
            "reason": self.reason,
            "confidence": self.confidence,
            "context": dict(self.context),
            "created_at": self.created_at,
        }


class RelationshipModeStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship_mode_decisions (
                decision_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                reason TEXT NOT NULL,
                confidence REAL NOT NULL,
                context_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relationship_mode_created
            ON relationship_mode_decisions(created_at DESC)
            """
        )
        self.conn.commit()

    def record(self, decision: ModeDecision) -> dict[str, Any]:
        self.conn.execute(
            """
            INSERT INTO relationship_mode_decisions(
                decision_id, mode, reason, confidence, context_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.mode,
                decision.reason,
                float(decision.confidence),
                json.dumps(decision.context, sort_keys=True),
                decision.created_at,
            ),
        )
        self.conn.commit()
        return decision.to_dict()

    def latest(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM relationship_mode_decisions
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return {
            "decision_id": row["decision_id"],
            "mode": row["mode"],
            "reason": row["reason"],
            "confidence": float(row["confidence"]),
            "context": json.loads(row["context_json"]),
            "created_at": row["created_at"],
        }

    def list_recent(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM relationship_mode_decisions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [
            {
                "decision_id": row["decision_id"],
                "mode": row["mode"],
                "reason": row["reason"],
                "confidence": float(row["confidence"]),
                "context": json.loads(row["context_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()


class RelationshipModeEngine:
    """One-consciousness mode selector: equal (default), strategist, butler."""

    def __init__(self, db_path: str | Path) -> None:
        self.store = RelationshipModeStore(db_path)

    def decide(
        self,
        *,
        explicit_directive: bool = False,
        disputed: bool = False,
        high_stakes: bool = False,
        uncertainty: float = 0.0,
        force_mode: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_map = dict(context or {})
        uncertainty_threshold = float(context_map.get("uncertainty_strategist_threshold") or 0.6)
        uncertainty_threshold = max(0.2, min(0.95, uncertainty_threshold))
        high_stakes_prefers_strategist = bool(context_map.get("high_stakes_prefers_strategist", True))
        disputed_prefers_strategist = bool(context_map.get("disputed_prefers_strategist", True))
        explicit_directive_to_butler = bool(context_map.get("explicit_directive_to_butler", True))

        mode = "equal"
        reason = "default_peer_mode"
        confidence = 0.82
        if force_mode in {"equal", "strategist", "butler"}:
            mode = str(force_mode)
            reason = "forced_mode"
            confidence = 0.95
        elif explicit_directive_to_butler and explicit_directive and not disputed:
            mode = "butler"
            reason = "explicit_non_disputed_directive"
            confidence = 0.9
        elif (
            (high_stakes_prefers_strategist and high_stakes)
            or (disputed_prefers_strategist and disputed)
            or float(uncertainty) >= uncertainty_threshold
        ):
            mode = "strategist"
            reason = "high_stakes_or_disputed_or_uncertain"
            confidence = 0.84
        decision = ModeDecision(
            decision_id=_new_id(),
            mode=mode,
            reason=reason,
            confidence=round(max(0.0, min(1.0, float(confidence))), 4),
            context=context_map,
            created_at=_utc_now_iso(),
        )
        return self.store.record(decision)

    def latest(self) -> dict[str, Any] | None:
        return self.store.latest()

    def list_recent(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return self.store.list_recent(limit=limit)

    def close(self) -> None:
        self.store.close()
