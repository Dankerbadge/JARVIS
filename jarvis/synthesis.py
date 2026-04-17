from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model_backends import HeuristicCognitionBackend
from .model_backends.base import CognitionBackend
from .models import new_id, utc_now_iso


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _day_key(now: datetime | None = None) -> str:
    value = now or _utc_now()
    return value.date().isoformat()


@dataclass(frozen=True)
class MorningSynthesis:
    synthesis_id: str
    created_at: str
    day_key: str
    top_priorities: tuple[dict[str, Any], ...]
    cross_domain_conflicts: tuple[dict[str, Any], ...]
    risks_today: tuple[dict[str, Any], ...]
    recommended_focus_windows: tuple[dict[str, Any], ...]
    interrupt_threshold: float
    state_refs: tuple[str, ...] = field(default_factory=tuple)
    source_thought_id: str | None = None
    suppressed_reasons: tuple[str, ...] = field(default_factory=tuple)
    narrative: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["top_priorities"] = [dict(item) for item in self.top_priorities]
        data["cross_domain_conflicts"] = [dict(item) for item in self.cross_domain_conflicts]
        data["risks_today"] = [dict(item) for item in self.risks_today]
        data["recommended_focus_windows"] = [dict(item) for item in self.recommended_focus_windows]
        data["state_refs"] = list(self.state_refs)
        data["suppressed_reasons"] = list(self.suppressed_reasons)
        data["kind"] = "morning"
        return data


@dataclass(frozen=True)
class EveningSynthesis:
    synthesis_id: str
    created_at: str
    day_key: str
    what_changed: tuple[dict[str, Any], ...]
    what_slipped: tuple[dict[str, Any], ...]
    unresolved: tuple[dict[str, Any], ...]
    rotation_recommendations: tuple[str, ...]
    learned_from_interrupts: tuple[str, ...]
    state_refs: tuple[str, ...] = field(default_factory=tuple)
    source_thought_id: str | None = None
    narrative: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["what_changed"] = [dict(item) for item in self.what_changed]
        data["what_slipped"] = [dict(item) for item in self.what_slipped]
        data["unresolved"] = [dict(item) for item in self.unresolved]
        data["rotation_recommendations"] = list(self.rotation_recommendations)
        data["learned_from_interrupts"] = list(self.learned_from_interrupts)
        data["state_refs"] = list(self.state_refs)
        data["kind"] = "evening"
        return data


class SynthesisStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS synthesis_artifacts (
                synthesis_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                day_key TEXT NOT NULL,
                artifact_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_synthesis_kind_day ON synthesis_artifacts(kind, day_key)"
        )
        self.conn.commit()

    def upsert(self, kind: str, day_key: str, artifact: dict[str, Any]) -> str:
        now = utc_now_iso()
        synthesis_id = str(artifact.get("synthesis_id") or new_id("syn"))
        payload = dict(artifact)
        payload["synthesis_id"] = synthesis_id
        self.conn.execute(
            """
            INSERT INTO synthesis_artifacts (synthesis_id, kind, day_key, artifact_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, day_key) DO UPDATE SET
                artifact_json = excluded.artifact_json,
                synthesis_id = excluded.synthesis_id,
                updated_at = excluded.updated_at
            """,
            (synthesis_id, kind, day_key, json.dumps(payload, sort_keys=True), now, now),
        )
        self.conn.commit()
        return synthesis_id

    def get(self, kind: str, day_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT artifact_json FROM synthesis_artifacts WHERE kind = ? AND day_key = ?",
            (kind, day_key),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["artifact_json"])

    def latest(self, kind: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT artifact_json
            FROM synthesis_artifacts
            WHERE kind = ?
            ORDER BY day_key DESC
            LIMIT 1
            """,
            (kind,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["artifact_json"])

    def close(self) -> None:
        self.conn.close()


class SynthesisEngine:
    def __init__(
        self,
        db_path: str | Path,
        *,
        default_interrupt_threshold: float = 0.72,
        backend: CognitionBackend | None = None,
    ) -> None:
        self.store = SynthesisStore(db_path)
        self.default_interrupt_threshold = default_interrupt_threshold
        self.backend = backend or HeuristicCognitionBackend(local_only=True)

    def _top_risks(self, runtime: Any, *, limit: int = 8) -> list[dict[str, Any]]:
        risks = runtime.state_graph.get_active_entities("Risk")
        ranked = sorted(risks, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        return ranked[:limit]

    def generate_morning(
        self,
        runtime: Any,
        *,
        source_thought_id: str | None = None,
        suppressed_reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        day = _day_key(now)
        risks = self._top_risks(runtime, limit=8)
        top_priorities = [
            {
                "domain": str((risk.get("value") or {}).get("domain") or (risk.get("value") or {}).get("project") or "unknown"),
                "risk_key": risk.get("entity_key"),
                "reason": (risk.get("value") or {}).get("reason"),
                "confidence": risk.get("confidence"),
            }
            for risk in risks[:4]
        ]
        has_zenith = any(item["domain"] == "zenith" for item in top_priorities)
        has_academics = any(item["domain"] == "academics" for item in top_priorities)
        has_markets = any(item["domain"] == "markets" for item in top_priorities)
        conflicts: list[dict[str, Any]] = []
        if has_zenith and has_academics:
            conflicts.append(
                {
                    "kind": "focus_budget_conflict",
                    "note": "Zenith and Academics both have active high-confidence risks.",
                }
            )
        if has_markets and has_academics:
            conflicts.append(
                {
                    "kind": "attention_timing_conflict",
                    "note": "Markets opportunities are competing with academic deadline pressure.",
                }
            )
        if has_markets and has_zenith:
            conflicts.append(
                {
                    "kind": "execution_conflict",
                    "note": "Markets and Zenith both request attention; prioritize by confidence and downside.",
                }
            )
        windows = [
            {"label": "deep-work-window-1", "minutes": 90},
            {"label": "deep-work-window-2", "minutes": 75},
        ]
        structured = {
            "top_priorities": top_priorities,
            "cross_domain_conflicts": conflicts,
            "risks_today": top_priorities,
            "recommended_focus_windows": windows,
            "interrupt_threshold": self.default_interrupt_threshold,
        }
        context = {
            "source_thought_id": source_thought_id,
            "suppressed_reasons": list(suppressed_reasons or []),
            "state_refs": [str(risk.get("id")) for risk in risks],
        }
        narrative = self.backend.draft_synthesis(kind="morning", structured=structured, context=context)

        morning = MorningSynthesis(
            synthesis_id=new_id("syn"),
            created_at=utc_now_iso(),
            day_key=day,
            top_priorities=tuple(top_priorities),
            cross_domain_conflicts=tuple(conflicts),
            risks_today=tuple(top_priorities),
            recommended_focus_windows=tuple(windows),
            interrupt_threshold=self.default_interrupt_threshold,
            state_refs=tuple(str(risk.get("id")) for risk in risks),
            source_thought_id=source_thought_id,
            suppressed_reasons=tuple(suppressed_reasons or []),
            narrative=narrative,
        )
        artifact = morning.to_dict()
        self.store.upsert("morning", day, artifact)
        return artifact

    def generate_evening(
        self,
        runtime: Any,
        *,
        source_thought_id: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        day = _day_key(now)
        outcomes = runtime.plan_repo.list_recent_outcomes_global(limit=30)
        interrupts = runtime.interrupt_store.list(status="all", limit=20)
        what_changed = [
            {"summary": outcome.get("summary"), "status": outcome.get("status")}
            for outcome in outcomes[:5]
        ]
        slipped = [item for item in what_changed if item.get("status") in {"failure", "regression"}]
        unresolved = runtime.security.list_approvals(status="pending")
        learned = []
        delivered = [item for item in interrupts if item.get("status") == "delivered"]
        suppressed = [item for item in interrupts if item.get("status") == "suppressed"]
        if delivered:
            learned.append("Delivered interrupts indicate active high-urgency threads.")
        if suppressed:
            learned.append("Suppressed interrupts suggest thresholds are avoiding low-signal noise.")
        rotations = []
        if slipped:
            rotations.append("Rotate priority toward unresolved failures first.")
        if not rotations:
            rotations.append("Maintain current priority mix with mild review of tomorrow risks.")

        unresolved_view = tuple(
            {
                "approval_id": item.get("approval_id"),
                "plan_id": item.get("plan_id"),
                "step_id": item.get("step_id"),
            }
            for item in unresolved[:8]
        )
        structured = {
            "what_changed": what_changed,
            "what_slipped": slipped,
            "unresolved": list(unresolved_view),
            "rotation_recommendations": rotations,
            "learned_from_interrupts": learned,
        }
        context = {
            "source_thought_id": source_thought_id,
            "interrupts": interrupts,
            "outcomes": outcomes,
        }
        narrative = self.backend.draft_synthesis(kind="evening", structured=structured, context=context)

        evening = EveningSynthesis(
            synthesis_id=new_id("syn"),
            created_at=utc_now_iso(),
            day_key=day,
            what_changed=tuple(what_changed),
            what_slipped=tuple(slipped),
            unresolved=unresolved_view,
            rotation_recommendations=tuple(rotations),
            learned_from_interrupts=tuple(learned),
            state_refs=tuple(str(item.get("approval_id")) for item in unresolved[:8]),
            source_thought_id=source_thought_id,
            narrative=narrative,
        )
        artifact = evening.to_dict()
        self.store.upsert("evening", day, artifact)
        return artifact

    def maybe_generate_daily(
        self,
        runtime: Any,
        *,
        source_thought_id: str | None = None,
        suppressed_reasons: list[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ts = now or _utc_now()
        day = _day_key(ts)
        generated: dict[str, Any] = {}
        # Morning window: before 15:00 UTC if not generated today.
        if ts.hour < 15 and not self.store.get("morning", day):
            generated["morning"] = self.generate_morning(
                runtime,
                source_thought_id=source_thought_id,
                suppressed_reasons=suppressed_reasons,
            )
        # Evening window: after 21:00 UTC if not generated today.
        if ts.hour >= 21 and not self.store.get("evening", day):
            generated["evening"] = self.generate_evening(
                runtime,
                source_thought_id=source_thought_id,
            )
        return generated

    def close(self) -> None:
        self.store.close()
