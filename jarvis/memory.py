from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProvenanceError(ValueError):
    pass


class MemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.events_root = self.db_path.parent / "memory" / ".dreams"
        self.events_root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.events_root / "events.jsonl"
        self._init_schema()

    def _new_event_id(self) -> str:
        return f"memevt_{uuid4().hex}"

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                data_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_memory (
                id TEXT PRIMARY KEY,
                memory_key TEXT NOT NULL,
                text_value TEXT NOT NULL,
                confidence REAL NOT NULL,
                provenance_json TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS procedural_memory (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                safety_json TEXT NOT NULL,
                preconditions_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def _validate_provenance(
        self,
        provenance_event_ids: list[str],
        provenance_state_ids: list[str] | None = None,
    ) -> dict[str, list[str]]:
        if not provenance_event_ids and not provenance_state_ids:
            raise ProvenanceError("At least one provenance reference is required.")
        return {
            "event_ids": provenance_event_ids,
            "state_ids": provenance_state_ids or [],
        }

    def append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        resolved_event_id = event_id or self._new_event_id()
        row = {
            "event_id": resolved_event_id,
            "event_type": str(event_type or "memory.event"),
            "payload": dict(payload or {}),
            "created_at": str(created_at or _utc_now_iso()),
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return resolved_event_id

    def list_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.events_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                if event_type and str(parsed.get("event_type")) != str(event_type):
                    continue
                rows.append(parsed)
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[: max(1, int(limit))]

    def add_episode(
        self,
        *,
        memory_id: str,
        category: str,
        data: dict[str, Any],
        provenance_event_ids: list[str],
        provenance_state_ids: list[str] | None = None,
        occurred_at: str | None = None,
    ) -> str:
        provenance = self._validate_provenance(provenance_event_ids, provenance_state_ids)
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO episodic_memory (
                id, category, data_json, provenance_json, occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                category,
                json.dumps(data, sort_keys=True),
                json.dumps(provenance, sort_keys=True),
                occurred_at or now,
                now,
            ),
        )
        self.conn.commit()
        self.append_event(
            "memory.episode_added",
            {
                "memory_id": memory_id,
                "category": category,
                "provenance_event_ids": list(provenance.get("event_ids") or []),
                "provenance_state_ids": list(provenance.get("state_ids") or []),
            },
        )
        return memory_id

    def add_semantic(
        self,
        *,
        memory_id: str,
        memory_key: str,
        text_value: str,
        confidence: float,
        provenance_event_ids: list[str],
        provenance_state_ids: list[str] | None = None,
        last_verified_at: str | None = None,
    ) -> str:
        provenance = self._validate_provenance(provenance_event_ids, provenance_state_ids)
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO semantic_memory (
                id, memory_key, text_value, confidence, provenance_json,
                last_verified_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                memory_key,
                text_value,
                confidence,
                json.dumps(provenance, sort_keys=True),
                last_verified_at or now,
                now,
            ),
        )
        self.conn.commit()
        self.append_event(
            "memory.semantic_added",
            {
                "memory_id": memory_id,
                "memory_key": memory_key,
                "confidence": float(confidence),
                "provenance_event_ids": list(provenance.get("event_ids") or []),
                "provenance_state_ids": list(provenance.get("state_ids") or []),
            },
        )
        return memory_id

    def upsert_procedure(
        self,
        *,
        procedure_id: str,
        name: str,
        version: str,
        steps: list[dict[str, Any]],
        safety: dict[str, Any],
        preconditions: dict[str, Any],
    ) -> str:
        now = _utc_now_iso()
        existing = self.conn.execute(
            "SELECT id FROM procedural_memory WHERE id = ?",
            (procedure_id,),
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE procedural_memory
                SET name = ?, version = ?, steps_json = ?, safety_json = ?,
                    preconditions_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    version,
                    json.dumps(steps, sort_keys=True),
                    json.dumps(safety, sort_keys=True),
                    json.dumps(preconditions, sort_keys=True),
                    now,
                    procedure_id,
                ),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO procedural_memory (
                    id, name, version, steps_json, safety_json, preconditions_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    procedure_id,
                    name,
                    version,
                    json.dumps(steps, sort_keys=True),
                    json.dumps(safety, sort_keys=True),
                    json.dumps(preconditions, sort_keys=True),
                    now,
                    now,
                ),
            )
        self.conn.commit()
        self.append_event(
            "memory.procedure_upserted",
            {
                "procedure_id": procedure_id,
                "name": name,
                "version": version,
            },
        )
        return procedure_id

    def retrieve_semantic(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM semantic_memory
            WHERE memory_key LIKE ? OR text_value LIKE ?
            ORDER BY confidence DESC, created_at DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "answer_payload": {"memory_key": row["memory_key"], "text": row["text_value"]},
                    "confidence": row["confidence"],
                    "provenance": json.loads(row["provenance_json"]),
                    "freshness": row["last_verified_at"],
                    "conflict_flags": [],
                }
            )
        self.append_event(
            "memory.recall",
            {
                "query": str(query),
                "limit": int(limit),
                "result_count": len(results),
            },
        )
        return results

    def close(self) -> None:
        self.conn.close()
