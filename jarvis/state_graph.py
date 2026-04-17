from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import EventEnvelope


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateGraph:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                auth_context TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                entity_key TEXT NOT NULL,
                type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                confidence REAL NOT NULL,
                source_refs_json TEXT NOT NULL,
                last_verified_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                edge_key TEXT NOT NULL,
                src_entity_id TEXT NOT NULL,
                dst_entity_id TEXT NOT NULL,
                type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                confidence REAL NOT NULL,
                source_refs_json TEXT NOT NULL,
                last_verified_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_entities_active ON entities(type, entity_key, valid_to)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_active ON edges(type, edge_key, valid_to)"
        )
        self.conn.commit()

    def normalize_event(
        self,
        source: str,
        source_type: str,
        payload: dict[str, Any],
        auth_context: str = "local",
    ) -> EventEnvelope:
        return EventEnvelope(
            source=source,
            source_type=source_type,
            payload=payload,
            auth_context=auth_context,
        )

    def ingest_event(self, event: EventEnvelope) -> None:
        self.conn.execute(
            """
            INSERT INTO events (
                event_id, source, source_type, occurred_at, ingested_at,
                payload_json, trace_id, auth_context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.source,
                event.source_type,
                event.occurred_at,
                event.ingested_at,
                json.dumps(event.payload, sort_keys=True),
                event.trace_id,
                event.auth_context,
            ),
        )
        self.conn.commit()

    def upsert_entity(
        self,
        *,
        entity_id: str,
        entity_key: str,
        entity_type: str,
        value: dict[str, Any],
        confidence: float,
        source_refs: list[str],
        last_verified_at: str | None = None,
    ) -> str:
        now = _utc_now_iso()
        last_verified = last_verified_at or now
        self.conn.execute(
            """
            UPDATE entities
            SET valid_to = ?
            WHERE type = ? AND entity_key = ? AND valid_to IS NULL
            """,
            (now, entity_type, entity_key),
        )
        self.conn.execute(
            """
            INSERT INTO entities (
                id, entity_key, type, value_json, valid_from, valid_to,
                confidence, source_refs_json, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                entity_id,
                entity_key,
                entity_type,
                json.dumps(value, sort_keys=True),
                now,
                confidence,
                json.dumps(source_refs),
                last_verified,
            ),
        )
        self.conn.commit()
        return entity_id

    def add_edge(
        self,
        *,
        edge_id: str,
        edge_key: str,
        src_entity_id: str,
        dst_entity_id: str,
        edge_type: str,
        value: dict[str, Any],
        confidence: float,
        source_refs: list[str],
        last_verified_at: str | None = None,
    ) -> str:
        now = _utc_now_iso()
        last_verified = last_verified_at or now
        self.conn.execute(
            """
            UPDATE edges
            SET valid_to = ?
            WHERE type = ? AND edge_key = ? AND valid_to IS NULL
            """,
            (now, edge_type, edge_key),
        )
        self.conn.execute(
            """
            INSERT INTO edges (
                id, edge_key, src_entity_id, dst_entity_id, type,
                value_json, valid_from, valid_to, confidence,
                source_refs_json, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                edge_id,
                edge_key,
                src_entity_id,
                dst_entity_id,
                edge_type,
                json.dumps(value, sort_keys=True),
                now,
                confidence,
                json.dumps(source_refs),
                last_verified,
            ),
        )
        self.conn.commit()
        return edge_id

    def apply_candidates(self, candidates: list[dict[str, Any]], event_id: str) -> list[str]:
        touched: list[str] = []
        for candidate in candidates:
            kind = candidate["kind"]
            source_refs = candidate.get("source_refs", [event_id])
            confidence = float(candidate.get("confidence", 0.5))
            if kind == "entity":
                entity_id = candidate["id"]
                touched.append(
                    self.upsert_entity(
                        entity_id=entity_id,
                        entity_key=candidate["entity_key"],
                        entity_type=candidate["entity_type"],
                        value=candidate["value"],
                        confidence=confidence,
                        source_refs=source_refs,
                        last_verified_at=candidate.get("last_verified_at"),
                    )
                )
            elif kind == "edge":
                edge_id = candidate["id"]
                touched.append(
                    self.add_edge(
                        edge_id=edge_id,
                        edge_key=candidate["edge_key"],
                        src_entity_id=candidate["src_entity_id"],
                        dst_entity_id=candidate["dst_entity_id"],
                        edge_type=candidate["edge_type"],
                        value=candidate.get("value", {}),
                        confidence=confidence,
                        source_refs=source_refs,
                        last_verified_at=candidate.get("last_verified_at"),
                    )
                )
            else:
                raise ValueError(f"Unsupported candidate kind: {kind}")
        return touched

    def process_event(
        self,
        event: EventEnvelope,
        extractor: Callable[[EventEnvelope], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        self.ingest_event(event)
        candidates = extractor(event)
        touched_ids = self.apply_candidates(candidates, event.event_id)
        triggers = self.derive_triggers(candidates)
        return {"event_id": event.event_id, "touched_ids": touched_ids, "triggers": triggers}

    def derive_triggers(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        triggers: list[dict[str, Any]] = []
        for item in candidates:
            if item.get("kind") != "entity" or item.get("entity_type") != "Risk":
                continue
            value = item.get("value", {})
            severity = str(value.get("severity", "")).lower()
            if severity in {"high", "critical"}:
                triggers.append(
                    {
                        "type": "high_risk_detected",
                        "risk_key": item.get("entity_key"),
                        "project": value.get("project"),
                        "domain": value.get("domain") or value.get("project"),
                        "reason": value.get("reason"),
                    }
                )
        return triggers

    def get_active_entities(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        if entity_type:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE valid_to IS NULL AND type = ?",
                (entity_type,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM entities WHERE valid_to IS NULL").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "entity_key": row["entity_key"],
                    "type": row["type"],
                    "value": json.loads(row["value_json"]),
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "confidence": row["confidence"],
                    "source_refs": json.loads(row["source_refs_json"]),
                    "last_verified_at": row["last_verified_at"],
                }
            )
        return result

    def get_active_entity(self, *, entity_type: str, entity_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM entities
            WHERE valid_to IS NULL AND type = ? AND entity_key = ?
            ORDER BY valid_from DESC
            LIMIT 1
            """,
            (entity_type, entity_key),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "entity_key": row["entity_key"],
            "type": row["type"],
            "value": json.loads(row["value_json"]),
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "confidence": row["confidence"],
            "source_refs": json.loads(row["source_refs_json"]),
            "last_verified_at": row["last_verified_at"],
        }

    def close(self) -> None:
        self.conn.close()
