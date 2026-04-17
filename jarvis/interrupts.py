from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import new_id, utc_now_iso


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class InterruptCandidate:
    candidate_id: str
    domain: str
    reason: str
    urgency_score: float
    confidence: float
    state_refs: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state_refs"] = list(self.state_refs)
        return data


@dataclass(frozen=True)
class InterruptDecision:
    interrupt_id: str
    candidate_id: str
    domain: str
    reason: str
    urgency_score: float
    confidence: float
    suppression_window_hit: bool
    delivered: bool
    why_now: str
    why_not_later: str
    status: str = "pending"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    snoozed_until: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InterruptStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interrupt_decisions (
                interrupt_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                reason TEXT NOT NULL,
                urgency_score REAL NOT NULL,
                confidence REAL NOT NULL,
                suppression_window_hit INTEGER NOT NULL,
                delivered INTEGER NOT NULL,
                why_now TEXT NOT NULL,
                why_not_later TEXT NOT NULL,
                status TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                acknowledged_by TEXT,
                acknowledged_at TEXT,
                snoozed_until TEXT
            )
            """
        )
        self.conn.commit()

    def store(self, decision: InterruptDecision) -> str:
        data = decision.to_dict()
        self.conn.execute(
            """
            INSERT INTO interrupt_decisions (
                interrupt_id, candidate_id, domain, reason, urgency_score, confidence,
                suppression_window_hit, delivered, why_now, why_not_later, status,
                decision_json, created_at, updated_at, acknowledged_by, acknowledged_at, snoozed_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(interrupt_id) DO UPDATE SET
                status = excluded.status,
                decision_json = excluded.decision_json,
                updated_at = excluded.updated_at,
                acknowledged_by = excluded.acknowledged_by,
                acknowledged_at = excluded.acknowledged_at,
                snoozed_until = excluded.snoozed_until
            """,
            (
                decision.interrupt_id,
                decision.candidate_id,
                decision.domain,
                decision.reason,
                decision.urgency_score,
                decision.confidence,
                1 if decision.suppression_window_hit else 0,
                1 if decision.delivered else 0,
                decision.why_now,
                decision.why_not_later,
                decision.status,
                json.dumps(data, sort_keys=True),
                decision.created_at,
                decision.updated_at,
                decision.acknowledged_by,
                decision.acknowledged_at,
                decision.snoozed_until,
            ),
        )
        self.conn.commit()
        return decision.interrupt_id

    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: list[Any] = []
        query = "SELECT * FROM interrupt_decisions"
        if status and status != "all":
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, tuple(params)).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            value = json.loads(row["decision_json"])
            value["status"] = row["status"]
            value["acknowledged_by"] = row["acknowledged_by"]
            value["acknowledged_at"] = row["acknowledged_at"]
            value["snoozed_until"] = row["snoozed_until"]
            out.append(value)
        return out

    def get(self, interrupt_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM interrupt_decisions WHERE interrupt_id = ?",
            (interrupt_id,),
        ).fetchone()
        if not row:
            return None
        value = json.loads(row["decision_json"])
        value["status"] = row["status"]
        value["acknowledged_by"] = row["acknowledged_by"]
        value["acknowledged_at"] = row["acknowledged_at"]
        value["snoozed_until"] = row["snoozed_until"]
        return value

    def acknowledge(self, interrupt_id: str, *, actor: str = "user") -> dict[str, Any]:
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE interrupt_decisions
            SET status = 'acknowledged',
                acknowledged_by = ?,
                acknowledged_at = ?,
                updated_at = ?
            WHERE interrupt_id = ?
            """,
            (actor, now, now, interrupt_id),
        )
        self.conn.commit()
        updated = self.get(interrupt_id)
        if not updated:
            raise KeyError(f"Interrupt not found: {interrupt_id}")
        return updated

    def snooze(
        self,
        interrupt_id: str,
        *,
        minutes: int = 60,
        actor: str = "user",
    ) -> dict[str, Any]:
        now = _utc_now()
        until = (now + timedelta(minutes=max(1, minutes))).isoformat()
        self.conn.execute(
            """
            UPDATE interrupt_decisions
            SET status = 'snoozed',
                acknowledged_by = ?,
                acknowledged_at = ?,
                snoozed_until = ?,
                updated_at = ?
            WHERE interrupt_id = ?
            """,
            (actor, now.isoformat(), until, now.isoformat(), interrupt_id),
        )
        self.conn.commit()
        updated = self.get(interrupt_id)
        if not updated:
            raise KeyError(f"Interrupt not found: {interrupt_id}")
        return updated

    def close(self) -> None:
        self.conn.close()


class InterruptPolicy:
    def __init__(self, *, base_threshold: float = 0.72) -> None:
        self.base_threshold = base_threshold

    def evaluate(
        self,
        candidate: InterruptCandidate,
        *,
        suppression_windows: list[str],
        active_focus_domain: str | None,
        goal_domain_weight: float | None = None,
        personal_context: dict[str, Any] | None = None,
    ) -> InterruptDecision:
        suppression_hit = bool(suppression_windows)
        threshold = self.base_threshold
        domain_weight = float(goal_domain_weight) if goal_domain_weight is not None else 1.0
        threshold -= max(0.0, domain_weight - 1.0) * 0.08
        threshold += max(0.0, 1.0 - domain_weight) * 0.12
        if active_focus_domain and active_focus_domain != candidate.domain:
            threshold += 0.08
        if candidate.domain == "zenith" and candidate.urgency_score >= 0.9:
            threshold = min(threshold, 0.62)
        if candidate.domain == "academics" and "deadline_window" in suppression_windows:
            threshold = min(threshold, 0.65)
        if candidate.domain == "academics" and "academic_deadline_focus" in suppression_windows:
            threshold = min(threshold, 0.64)
        if candidate.domain == "markets":
            threshold += 0.06
            if candidate.confidence < 0.82:
                threshold += 0.08
            if "academic_deadline_focus" in suppression_windows:
                threshold += 0.12
            if active_focus_domain in {"academics", "zenith"}:
                threshold += 0.08

        context = dict(personal_context or {})
        stress = float(context.get("stress_level", 0.5))
        energy = float(context.get("energy_level", 0.6))
        if stress >= 0.75 and candidate.domain == "zenith" and candidate.urgency_score < 0.9:
            threshold += 0.07
        if stress >= 0.75 and candidate.domain == "academics":
            threshold = min(threshold, 0.66)
        if stress >= 0.75 and candidate.domain == "markets":
            threshold += 0.08
        if energy <= 0.35 and candidate.urgency_score < 0.85:
            threshold += 0.05
        threshold = max(0.45, min(0.97, threshold))

        score = candidate.urgency_score * 0.65 + candidate.confidence * 0.35
        zenith_reason = candidate.reason.lower()
        high_impact_zenith = candidate.domain == "zenith" and (
            candidate.urgency_score >= 0.9
            or any(token in zenith_reason for token in ("ci_failed", "regression", "outage", "security"))
        )
        academic_focus_lock = "academic_deadline_focus" in suppression_windows and active_focus_domain == "academics"

        delivered = score >= threshold
        if suppression_hit and candidate.domain != "zenith" and candidate.urgency_score < 0.95:
            delivered = False
        if academic_focus_lock and candidate.domain == "zenith" and not high_impact_zenith:
            delivered = False
        if candidate.domain == "markets" and candidate.confidence < 0.82:
            delivered = False

        if delivered:
            why_now = "high-urgency signal exceeded interruption threshold."
            why_not_later = "deferral risks lower expected outcome quality."
            status = "delivered"
        else:
            if suppression_hit:
                why_now = "suppression window active."
                why_not_later = "re-evaluate after suppression window or confidence increase."
            else:
                why_now = "signal below interruption threshold."
                why_not_later = "wait for stronger corroboration."
            status = "suppressed"

        return InterruptDecision(
            interrupt_id=new_id("int"),
            candidate_id=candidate.candidate_id,
            domain=candidate.domain,
            reason=candidate.reason,
            urgency_score=candidate.urgency_score,
            confidence=candidate.confidence,
            suppression_window_hit=suppression_hit,
            delivered=delivered,
            why_now=why_now,
            why_not_later=why_not_later,
            status=status,
        )
