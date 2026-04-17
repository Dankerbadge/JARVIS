from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_snapshot_id() -> str:
    return f"tone_{uuid4().hex}"


class ToneBalanceStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tone_balance_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                modality TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                imbalances_json TEXT NOT NULL,
                calibration_hint TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tone_balance_created
            ON tone_balance_snapshots(created_at DESC)
            """
        )
        self.conn.commit()

    def record(
        self,
        *,
        mode: str,
        modality: str,
        profile: dict[str, Any] | None = None,
        imbalances: list[str] | None = None,
        calibration_hint: str | None = None,
    ) -> dict[str, Any]:
        snapshot_id = _new_snapshot_id()
        created_at = _utc_now_iso()
        normalized_mode = str(mode or "equal").strip().lower() or "equal"
        normalized_modality = str(modality or "text").strip().lower() or "text"
        normalized_profile = dict(profile or {})
        normalized_imbalances = [str(item).strip() for item in (imbalances or []) if str(item).strip()]
        normalized_hint = str(calibration_hint or "").strip() or None
        self.conn.execute(
            """
            INSERT INTO tone_balance_snapshots(
                snapshot_id, mode, modality, profile_json, imbalances_json, calibration_hint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                normalized_mode,
                normalized_modality,
                json.dumps(normalized_profile, sort_keys=True),
                json.dumps(normalized_imbalances, sort_keys=True),
                normalized_hint,
                created_at,
            ),
        )
        self.conn.commit()
        return {
            "snapshot_id": snapshot_id,
            "mode": normalized_mode,
            "modality": normalized_modality,
            "profile": normalized_profile,
            "imbalances": normalized_imbalances,
            "calibration_hint": normalized_hint,
            "created_at": created_at,
        }

    def latest(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM tone_balance_snapshots
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_recent(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM tone_balance_snapshots
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def summary(self, *, limit: int = 30) -> dict[str, Any]:
        recent = self.list_recent(limit=limit)
        by_mode: dict[str, int] = {}
        by_modality: dict[str, int] = {}
        by_hint: dict[str, int] = {}
        for item in recent:
            mode = str(item.get("mode") or "unknown")
            modality = str(item.get("modality") or "unknown")
            hint = str(item.get("calibration_hint") or "none")
            by_mode[mode] = by_mode.get(mode, 0) + 1
            by_modality[modality] = by_modality.get(modality, 0) + 1
            by_hint[hint] = by_hint.get(hint, 0) + 1
        return {
            "latest": recent[0] if recent else None,
            "count": len(recent),
            "by_mode": by_mode,
            "by_modality": by_modality,
            "by_calibration_hint": by_hint,
            "items": recent,
        }

    def close(self) -> None:
        self.conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "snapshot_id": row["snapshot_id"],
            "mode": row["mode"],
            "modality": row["modality"],
            "profile": json.loads(row["profile_json"]),
            "imbalances": json.loads(row["imbalances_json"]),
            "calibration_hint": row["calibration_hint"],
            "created_at": row["created_at"],
        }
