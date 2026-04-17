from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceTuningStateStore:
    """Persisted manual override layer for voice tuning profile refinement."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_tuning_overrides (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                overrides_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                actor TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_tuning_override_events (
                event_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                overrides_json TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO voice_tuning_overrides(id, overrides_json, updated_at, actor)
            VALUES (1, '{}', ?, 'system')
            """,
            (_utc_now_iso(),),
        )
        self.conn.commit()

    @staticmethod
    def _clamp_float(value: Any, *, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    def _normalize_overrides(self, incoming: dict[str, Any]) -> dict[str, Any]:
        raw = dict(incoming or {})
        normalized: dict[str, Any] = {}
        if raw.get("latency_tier") is not None:
            tier = str(raw.get("latency_tier") or "").strip().lower()
            if tier in {"low", "balanced", "quality"}:
                normalized["latency_tier"] = tier
        if raw.get("speed_min") is not None:
            normalized["speed_min"] = round(self._clamp_float(raw.get("speed_min"), low=0.82, high=1.18), 4)
        if raw.get("speed_max") is not None:
            normalized["speed_max"] = round(self._clamp_float(raw.get("speed_max"), low=0.82, high=1.18), 4)
        if (
            normalized.get("speed_min") is not None
            and normalized.get("speed_max") is not None
            and float(normalized["speed_min"]) > float(normalized["speed_max"])
        ):
            normalized["speed_min"], normalized["speed_max"] = normalized["speed_max"], normalized["speed_min"]
        if raw.get("speed_bias") is not None:
            normalized["speed_bias"] = round(self._clamp_float(raw.get("speed_bias"), low=-0.08, high=0.08), 4)
        if raw.get("stability_floor") is not None:
            normalized["stability_floor"] = round(
                self._clamp_float(raw.get("stability_floor"), low=0.35, high=0.98),
                4,
            )
        if raw.get("stability_bias") is not None:
            normalized["stability_bias"] = round(
                self._clamp_float(raw.get("stability_bias"), low=-0.08, high=0.12),
                4,
            )
        if raw.get("cadence_bias") is not None:
            normalized["cadence_bias"] = round(
                self._clamp_float(raw.get("cadence_bias"), low=-0.06, high=0.2),
                4,
            )
        if raw.get("annunciation_bias") is not None:
            normalized["annunciation_bias"] = round(
                self._clamp_float(raw.get("annunciation_bias"), low=-0.06, high=0.24),
                4,
            )
        if raw.get("strict_mode_required") is not None:
            normalized["strict_mode_required"] = bool(raw.get("strict_mode_required"))
        if raw.get("confidence_floor") is not None:
            normalized["confidence_floor"] = round(
                self._clamp_float(raw.get("confidence_floor"), low=0.0, high=0.99),
                4,
            )
        if raw.get("prefer_stability") is not None:
            normalized["prefer_stability"] = bool(raw.get("prefer_stability"))
        return normalized

    def _revision(self, overrides: dict[str, Any]) -> str:
        payload = json.dumps(dict(overrides or {}), sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]

    def _row_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        overrides = json.loads(row["overrides_json"]) if row and row["overrides_json"] else {}
        if not isinstance(overrides, dict):
            overrides = {}
        return {
            "overrides": overrides,
            "revision": self._revision(overrides),
            "updated_at": row["updated_at"],
            "actor": row["actor"],
        }

    def get_overrides(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM voice_tuning_overrides WHERE id = 1",
        ).fetchone()
        if not row:
            return {
                "overrides": {},
                "revision": self._revision({}),
                "updated_at": _utc_now_iso(),
                "actor": "system",
            }
        return self._row_to_payload(row)

    def _log_event(self, *, action: str, actor: str, overrides: dict[str, Any], details: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO voice_tuning_override_events(event_id, action, actor, overrides_json, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"vto_{uuid4().hex}",
                action,
                actor,
                json.dumps(overrides, sort_keys=True),
                json.dumps(details, sort_keys=True),
                _utc_now_iso(),
            ),
        )
        self.conn.commit()

    def update_overrides(
        self,
        *,
        patch: dict[str, Any],
        replace: bool = False,
        actor: str = "operator",
    ) -> dict[str, Any]:
        current = self.get_overrides().get("overrides") if not replace else {}
        merged = dict(current or {})
        for key, value in dict(patch or {}).items():
            if value is None:
                merged.pop(str(key), None)
            else:
                merged[str(key)] = value
        normalized = self._normalize_overrides(merged)
        now = _utc_now_iso()
        self.conn.execute(
            """
            UPDATE voice_tuning_overrides
            SET overrides_json = ?, updated_at = ?, actor = ?
            WHERE id = 1
            """,
            (json.dumps(normalized, sort_keys=True), now, str(actor or "operator")),
        )
        self.conn.commit()
        payload = self.get_overrides()
        self._log_event(
            action=("replace" if replace else "patch"),
            actor=str(actor or "operator"),
            overrides=payload.get("overrides") or {},
            details={"patch": patch, "replace": bool(replace), "revision": payload.get("revision")},
        )
        return payload

    def clear_overrides(self, *, actor: str = "operator") -> dict[str, Any]:
        return self.update_overrides(patch={}, replace=True, actor=actor)

    def list_events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM voice_tuning_override_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "event_id": row["event_id"],
                    "action": row["action"],
                    "actor": row["actor"],
                    "overrides": json.loads(row["overrides_json"]),
                    "details": json.loads(row["details_json"]),
                    "created_at": row["created_at"],
                }
            )
        return result

    def close(self) -> None:
        self.conn.close()
