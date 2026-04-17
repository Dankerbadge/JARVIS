from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class PushbackCalibrationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pushback_records (
                pushback_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                severity TEXT NOT NULL,
                rationale_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS override_records (
                override_id TEXT PRIMARY KEY,
                pushback_id TEXT NOT NULL,
                operator_action TEXT NOT NULL,
                rationale_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outcome_reviews (
                review_id TEXT PRIMARY KEY,
                pushback_id TEXT NOT NULL,
                override_id TEXT,
                outcome TEXT NOT NULL,
                impact_score REAL NOT NULL,
                notes_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calibration_deltas (
                delta_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                direction TEXT NOT NULL,
                magnitude REAL NOT NULL,
                reason TEXT NOT NULL,
                source_review_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def record_pushback(
        self,
        *,
        domain: str,
        recommendation: str,
        severity: str,
        rationale: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pushback_id = _new_id("pbk")
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO pushback_records(
                pushback_id, domain, recommendation, severity, rationale_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                pushback_id,
                str(domain).strip().lower() or "general",
                str(recommendation),
                str(severity).strip().lower() or "medium",
                json.dumps(dict(rationale or {}), sort_keys=True),
                now,
            ),
        )
        self.conn.commit()
        return self.get_pushback(pushback_id) or {}

    def record_override(
        self,
        *,
        pushback_id: str,
        operator_action: str,
        rationale: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        override_id = _new_id("ovr")
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO override_records(
                override_id, pushback_id, operator_action, rationale_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                override_id,
                str(pushback_id),
                str(operator_action),
                json.dumps(dict(rationale or {}), sort_keys=True),
                now,
            ),
        )
        self.conn.execute(
            "UPDATE pushback_records SET status = 'overridden' WHERE pushback_id = ?",
            (str(pushback_id),),
        )
        self.conn.commit()
        return self.get_override(override_id) or {}

    def record_outcome_review(
        self,
        *,
        pushback_id: str,
        outcome: str,
        impact_score: float,
        notes: dict[str, Any] | None = None,
        override_id: str | None = None,
    ) -> dict[str, Any]:
        review_id = _new_id("rvw")
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO outcome_reviews(
                review_id, pushback_id, override_id, outcome, impact_score, notes_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                str(pushback_id),
                str(override_id).strip() or None,
                str(outcome).strip().lower() or "unknown",
                float(impact_score),
                json.dumps(dict(notes or {}), sort_keys=True),
                now,
            ),
        )
        self.conn.execute(
            "UPDATE pushback_records SET status = 'reviewed' WHERE pushback_id = ?",
            (str(pushback_id),),
        )
        self.conn.commit()
        return self.get_outcome_review(review_id) or {}

    def record_calibration_delta(
        self,
        *,
        domain: str,
        direction: str,
        magnitude: float,
        reason: str,
        source_review_id: str | None = None,
    ) -> dict[str, Any]:
        delta_id = _new_id("cal")
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO calibration_deltas(
                delta_id, domain, direction, magnitude, reason, source_review_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delta_id,
                str(domain).strip().lower() or "general",
                str(direction).strip().lower() or "hold",
                float(magnitude),
                str(reason),
                str(source_review_id).strip() or None,
                now,
            ),
        )
        self.conn.commit()
        return self.get_calibration_delta(delta_id) or {}

    def get_pushback(self, pushback_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pushback_records WHERE pushback_id = ?",
            (str(pushback_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "pushback_id": row["pushback_id"],
            "domain": row["domain"],
            "recommendation": row["recommendation"],
            "severity": row["severity"],
            "rationale": json.loads(row["rationale_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def get_override(self, override_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM override_records WHERE override_id = ?",
            (str(override_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "override_id": row["override_id"],
            "pushback_id": row["pushback_id"],
            "operator_action": row["operator_action"],
            "rationale": json.loads(row["rationale_json"]),
            "created_at": row["created_at"],
        }

    def get_outcome_review(self, review_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM outcome_reviews WHERE review_id = ?",
            (str(review_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "review_id": row["review_id"],
            "pushback_id": row["pushback_id"],
            "override_id": row["override_id"],
            "outcome": row["outcome"],
            "impact_score": float(row["impact_score"]),
            "notes": json.loads(row["notes_json"]),
            "created_at": row["created_at"],
        }

    def get_calibration_delta(self, delta_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM calibration_deltas WHERE delta_id = ?",
            (str(delta_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "delta_id": row["delta_id"],
            "domain": row["domain"],
            "direction": row["direction"],
            "magnitude": float(row["magnitude"]),
            "reason": row["reason"],
            "source_review_id": row["source_review_id"],
            "created_at": row["created_at"],
        }

    def list_recent(self, *, limit: int = 30) -> dict[str, list[dict[str, Any]]]:
        lim = max(1, int(limit))
        pushbacks = [
            self.get_pushback(row["pushback_id"]) or {}
            for row in self.conn.execute(
                "SELECT pushback_id FROM pushback_records ORDER BY created_at DESC LIMIT ?",
                (lim,),
            ).fetchall()
        ]
        overrides = [
            self.get_override(row["override_id"]) or {}
            for row in self.conn.execute(
                "SELECT override_id FROM override_records ORDER BY created_at DESC LIMIT ?",
                (lim,),
            ).fetchall()
        ]
        reviews = [
            self.get_outcome_review(row["review_id"]) or {}
            for row in self.conn.execute(
                "SELECT review_id FROM outcome_reviews ORDER BY created_at DESC LIMIT ?",
                (lim,),
            ).fetchall()
        ]
        deltas = [
            self.get_calibration_delta(row["delta_id"]) or {}
            for row in self.conn.execute(
                "SELECT delta_id FROM calibration_deltas ORDER BY created_at DESC LIMIT ?",
                (lim,),
            ).fetchall()
        ]
        return {
            "pushbacks": pushbacks,
            "overrides": overrides,
            "reviews": reviews,
            "calibration_deltas": deltas,
        }

    def close(self) -> None:
        self.conn.close()
