from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .interrupts import InterruptCandidate, InterruptPolicy
from .model_backends import HeuristicCognitionBackend
from .model_backends.base import BackendHypothesis, CognitionBackend
from .models import new_id, utc_now_iso
from .state_index import latest_academic_suppression_windows_key


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class Hypothesis:
    id: str
    claim: str
    domain_tags: tuple[str, ...]
    support_refs: tuple[str, ...]
    counter_refs: tuple[str, ...]
    confidence: float
    expected_value: float
    novelty: float
    dead_end_risk: float
    skepticism_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["domain_tags"] = list(self.domain_tags)
        data["support_refs"] = list(self.support_refs)
        data["counter_refs"] = list(self.counter_refs)
        data["skepticism_flags"] = list(self.skepticism_flags)
        return data


@dataclass(frozen=True)
class ThoughtArtifact:
    thought_id: str
    created_at: str
    state_refs: tuple[str, ...]
    open_threads: tuple[str, ...]
    hypotheses: tuple[Hypothesis, ...]
    interrupt_candidates: tuple[dict[str, Any], ...]
    proposed_plan_ids: tuple[str, ...]
    suppressed_reasons: tuple[str, ...]
    next_wake_at: str
    backend_name: str = "heuristic"
    backend_model: str | None = None
    backend_mode: str = "heuristic"
    backend_metrics: dict[str, Any] = field(default_factory=dict)
    model_assisted: bool = False
    user_model_snapshot: dict[str, Any] = field(default_factory=dict)
    personal_context_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought_id": self.thought_id,
            "created_at": self.created_at,
            "state_refs": list(self.state_refs),
            "open_threads": list(self.open_threads),
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "interrupt_candidates": [dict(item) for item in self.interrupt_candidates],
            "proposed_plan_ids": list(self.proposed_plan_ids),
            "suppressed_reasons": list(self.suppressed_reasons),
            "next_wake_at": self.next_wake_at,
            "backend_name": self.backend_name,
            "backend_model": self.backend_model,
            "backend_mode": self.backend_mode,
            "backend_metrics": dict(self.backend_metrics),
            "model_assisted": self.model_assisted,
            "user_model_snapshot": dict(self.user_model_snapshot),
            "personal_context_snapshot": dict(self.personal_context_snapshot),
        }


class ThoughtStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS thought_artifacts (
                thought_id TEXT PRIMARY KEY,
                artifact_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def store(self, artifact: ThoughtArtifact) -> str:
        data = artifact.to_dict()
        self.conn.execute(
            """
            INSERT INTO thought_artifacts (thought_id, artifact_json, created_at)
            VALUES (?, ?, ?)
            """,
            (artifact.thought_id, json.dumps(data, sort_keys=True), artifact.created_at),
        )
        self.conn.commit()
        return artifact.thought_id

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT artifact_json
            FROM thought_artifacts
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [json.loads(row["artifact_json"]) for row in rows]

    def get(self, thought_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT artifact_json FROM thought_artifacts WHERE thought_id = ?",
            (thought_id,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["artifact_json"])

    def latest(self) -> dict[str, Any] | None:
        rows = self.recent(limit=1)
        return rows[0] if rows else None

    def close(self) -> None:
        self.conn.close()


class CognitionEngine:
    def __init__(
        self,
        db_path: str | Path,
        *,
        backend: CognitionBackend | None = None,
        enabled: bool = True,
        max_hypotheses_per_cycle: int = 10,
        min_cycle_interval_seconds: int = 120,
        wake_interval_seconds: int = 300,
    ) -> None:
        self.store = ThoughtStore(db_path)
        self.backend = backend or HeuristicCognitionBackend(local_only=True)
        self.enabled = bool(enabled)
        self.max_hypotheses_per_cycle = max_hypotheses_per_cycle
        self.min_cycle_interval_seconds = min_cycle_interval_seconds
        self.wake_interval_seconds = wake_interval_seconds
        self.interrupt_policy = InterruptPolicy()

    def _build_hypotheses(
        self,
        *,
        risks: list[dict[str, Any]],
        recent_outcomes: list[dict[str, Any]],
        user_model: dict[str, Any],
        personal_context: dict[str, Any],
        active_focus_domain: str | None,
    ) -> list[Hypothesis]:
        context = {
            "risks": risks,
            "recent_outcomes": recent_outcomes,
            "max_hypotheses": self.max_hypotheses_per_cycle,
            "user_model": user_model,
            "personal_context": personal_context,
        }
        generated = self.backend.generate_hypotheses(
            risks=risks,
            recent_outcomes=recent_outcomes,
            max_hypotheses=self.max_hypotheses_per_cycle,
        )
        skeptical = self.backend.skepticism_pass(hypotheses=generated, context=context)
        diagnosed: list[BackendHypothesis] = []
        for item in skeptical:
            diagnosed_item = self.backend.diagnose_dead_end(hypothesis=item, context=context)
            diagnosed.append(diagnosed_item.normalized())
        diagnosed.sort(
            key=lambda item: (float(item.expected_value), float(item.confidence)),
            reverse=True,
        )
        trimmed = diagnosed[: self.max_hypotheses_per_cycle]
        hypotheses = [
            Hypothesis(
                id=new_id("hyp"),
                claim=item.claim,
                domain_tags=item.domain_tags,
                support_refs=item.support_refs,
                counter_refs=item.counter_refs,
                confidence=float(item.confidence),
                expected_value=float(item.expected_value),
                novelty=float(item.novelty),
                dead_end_risk=float(item.dead_end_risk),
                skepticism_flags=item.skepticism_flags,
            )
            for item in trimmed
        ]
        return self._apply_goal_weighting(
            hypotheses=hypotheses,
            user_model=user_model,
            personal_context=personal_context,
            active_focus_domain=active_focus_domain,
        )

    def _domain_weights(self, user_model: dict[str, Any]) -> dict[str, float]:
        weights = {
            str(key).strip().lower(): _clamp(float(value), 0.2, 2.5)
            for key, value in dict(user_model.get("domain_weights") or {}).items()
            if str(key).strip()
        }
        goals = list(user_model.get("goals") or [])
        for goal in goals:
            if not isinstance(goal, dict):
                continue
            goal_weight = _clamp(float(goal.get("weight") or 1.0), 0.2, 2.5)
            domains = [
                str(item).strip().lower()
                for item in list(goal.get("domains") or [])
                if str(item).strip()
            ]
            for domain in domains:
                baseline = float(weights.get(domain, 1.0))
                weights[domain] = max(baseline, goal_weight)
        return weights

    def _apply_goal_weighting(
        self,
        *,
        hypotheses: list[Hypothesis],
        user_model: dict[str, Any],
        personal_context: dict[str, Any],
        active_focus_domain: str | None,
    ) -> list[Hypothesis]:
        domain_weights = self._domain_weights(user_model)
        stress = _clamp(float(personal_context.get("stress_level", 0.5)))
        energy = _clamp(float(personal_context.get("energy_level", 0.6)))
        sleep_hours_raw = personal_context.get("sleep_hours")
        sleep_hours = float(sleep_hours_raw) if isinstance(sleep_hours_raw, (int, float)) else None
        focus_minutes_raw = personal_context.get("available_focus_minutes")
        focus_minutes = int(focus_minutes_raw) if isinstance(focus_minutes_raw, (int, float)) else None
        adjusted: list[Hypothesis] = []

        for item in hypotheses:
            domain = item.domain_tags[0] if item.domain_tags else "zenith"
            domain_weight = _clamp(float(domain_weights.get(domain, 1.0)), 0.2, 2.5)
            weight = domain_weight
            skepticism_flags = set(item.skepticism_flags)

            if active_focus_domain and domain != active_focus_domain:
                weight *= 0.94
                skepticism_flags.add("focus_context_penalty")

            if stress >= 0.75 and domain == "academics":
                weight *= 1.08
            if stress >= 0.75 and domain == "zenith" and item.expected_value < 0.9:
                weight *= 0.9
                skepticism_flags.add("stress_suppression_guard")

            if energy <= 0.35 and item.expected_value < 0.85:
                weight *= 0.9
                skepticism_flags.add("low_energy_guard")
            if sleep_hours is not None and sleep_hours < 6 and item.expected_value < 0.85:
                weight *= 0.92
                skepticism_flags.add("fatigue_guard")

            if focus_minutes is not None and focus_minutes < 45 and item.expected_value < 0.8:
                weight *= 0.88
                skepticism_flags.add("limited_focus_budget")

            if domain_weight < 0.95:
                skepticism_flags.add("lower_priority_domain")
            if domain_weight > 1.05:
                skepticism_flags.add("goal_priority_boost")

            adjusted_expected = _clamp(item.expected_value * weight)
            confidence_delta = 0.02 if domain_weight > 1.05 else (-0.015 if domain_weight < 0.95 else 0.0)
            adjusted_confidence = _clamp(item.confidence + confidence_delta)
            adjusted.append(
                replace(
                    item,
                    expected_value=adjusted_expected,
                    confidence=adjusted_confidence,
                    skepticism_flags=tuple(sorted(skepticism_flags)),
                )
            )
        adjusted.sort(key=lambda hyp: (hyp.expected_value, hyp.confidence), reverse=True)
        return adjusted[: self.max_hypotheses_per_cycle]

    def _suppression_windows(self, now: datetime, runtime: Any) -> tuple[list[str], str | None]:
        windows: list[str] = []
        hour = now.hour
        if hour < 7 or hour >= 23:
            windows.append("sleep_window")
        active_focus_domain: str | None = None

        operator_prefs = runtime.get_operator_preferences()
        quiet_hours = operator_prefs.get("quiet_hours") if isinstance(operator_prefs, dict) else None
        if isinstance(quiet_hours, dict):
            start_hour = quiet_hours.get("start_hour")
            end_hour = quiet_hours.get("end_hour")
            if isinstance(start_hour, int) and isinstance(end_hour, int):
                in_quiet = False
                if start_hour == end_hour:
                    in_quiet = True
                elif start_hour < end_hour:
                    in_quiet = start_hour <= hour < end_hour
                else:
                    in_quiet = hour >= start_hour or hour < end_hour
                if in_quiet:
                    windows.append("user_quiet_hours")

        suppress_until = _parse_iso(operator_prefs.get("suppress_until") if isinstance(operator_prefs, dict) else None)
        if suppress_until and now <= suppress_until:
            windows.append("manual_suppress_until")

        focus_mode = (
            str(operator_prefs.get("focus_mode_domain") or "").strip().lower()
            if isinstance(operator_prefs, dict)
            else ""
        )
        if focus_mode:
            active_focus_domain = focus_mode

        suppression_entity = runtime.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_academic_suppression_windows_key("current_term"),
        )
        if suppression_entity:
            for item in (suppression_entity.get("value") or {}).get("windows", []):
                if not isinstance(item, dict):
                    continue
                start_at = _parse_iso(item.get("start_at"))
                end_at = _parse_iso(item.get("end_at"))
                if not start_at or not end_at:
                    continue
                if start_at <= now <= end_at:
                    kind = str(item.get("kind") or "academic_window").strip().lower()
                    windows.append(f"academic:{kind}")
                    active_focus_domain = "academics"

        academic_risks = runtime.list_academic_risks()
        for risk in academic_risks:
            value = risk.get("value") or {}
            hours_until_due = value.get("hours_until_due")
            due_at = _parse_iso(value.get("due_at"))
            hours = None
            if isinstance(hours_until_due, (int, float)):
                hours = float(hours_until_due)
            elif due_at:
                hours = (due_at - now).total_seconds() / 3600.0
            if hours is not None and hours <= 72:
                windows.append("academic_deadline_focus")
                active_focus_domain = "academics"
                break
        return (sorted(set(windows)), active_focus_domain)

    def run_cycle(self, runtime: Any) -> dict[str, Any]:
        now = _utc_now()
        if not self.enabled:
            return {
                "status": "disabled",
                "backend": self.backend.name,
                "model": self.backend.model,
                "next_wake_at": (now + timedelta(seconds=self.wake_interval_seconds)).isoformat(),
            }
        latest = self.store.latest()
        if latest:
            created_at = datetime.fromisoformat(str(latest["created_at"]))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            elapsed = (now - created_at).total_seconds()
            if elapsed < self.min_cycle_interval_seconds:
                return {
                    "status": "skipped_interval_guard",
                    "backend": self.backend.name,
                    "model": self.backend.model,
                    "next_wake_at": (created_at + timedelta(seconds=self.min_cycle_interval_seconds)).isoformat(),
                }

        self.backend.reset_cycle_metrics()
        risks = runtime.state_graph.get_active_entities("Risk")
        recent_outcomes = runtime.plan_repo.list_recent_outcomes_global(limit=120)
        pending_approvals = runtime.security.list_approvals(status="pending")
        open_threads = [str(item.get("approval_id")) for item in pending_approvals[:10]]
        user_model = runtime.get_user_model()
        personal_context = runtime.get_personal_context()
        suppression_windows, active_focus_domain = self._suppression_windows(now, runtime)
        hypotheses = self._build_hypotheses(
            risks=risks,
            recent_outcomes=recent_outcomes,
            user_model=user_model,
            personal_context=personal_context,
            active_focus_domain=active_focus_domain,
        )
        interrupt_candidates: list[dict[str, Any]] = []
        suppressed_reasons: list[str] = []
        domain_weights = self._domain_weights(user_model)

        rationale_context = {
            "suppression_windows": suppression_windows,
            "open_threads": open_threads,
            "top_hypotheses": [item.to_dict() for item in hypotheses[:3]],
            "user_model": user_model,
            "personal_context": personal_context,
        }

        for hypothesis in hypotheses[:5]:
            domain = hypothesis.domain_tags[0] if hypothesis.domain_tags else "zenith"
            candidate = InterruptCandidate(
                candidate_id=new_id("cand"),
                domain=domain,
                reason=hypothesis.claim,
                urgency_score=hypothesis.expected_value,
                confidence=hypothesis.confidence,
                state_refs=hypothesis.support_refs,
            )
            decision = self.interrupt_policy.evaluate(
                candidate,
                suppression_windows=suppression_windows,
                active_focus_domain=active_focus_domain,
                goal_domain_weight=float(domain_weights.get(domain, 1.0)),
                personal_context=personal_context,
            )
            rationale = self.backend.draft_interrupt_rationale(
                candidate=candidate.to_dict(),
                decision=decision.to_dict(),
                context=rationale_context,
            )
            if rationale:
                why_now, why_not_later = rationale
                if str(why_now).strip() and str(why_not_later).strip():
                    decision = replace(
                        decision,
                        why_now=str(why_now).strip(),
                        why_not_later=str(why_not_later).strip(),
                        updated_at=utc_now_iso(),
                    )
            runtime.interrupt_store.store(decision)
            interrupt_candidates.append(
                {
                    "candidate": candidate.to_dict(),
                    "decision": decision.to_dict(),
                }
            )
            if not decision.delivered:
                suppressed_reasons.append(decision.why_not_later)

        backend_metrics = self.backend.get_cycle_metrics()
        assist_used = bool(backend_metrics.get("assist_used"))
        if self.backend.name == "heuristic":
            backend_mode = "heuristic"
        elif assist_used:
            backend_mode = f"{self.backend.name}_assisted"
        else:
            backend_mode = "heuristic_fallback"

        thought = ThoughtArtifact(
            thought_id=new_id("tht"),
            created_at=utc_now_iso(),
            state_refs=tuple(str(item.get("id")) for item in risks[:20]),
            open_threads=tuple(open_threads),
            hypotheses=tuple(hypotheses),
            interrupt_candidates=tuple(interrupt_candidates),
            proposed_plan_ids=(),
            suppressed_reasons=tuple(sorted(set(suppressed_reasons))),
            next_wake_at=(now + timedelta(seconds=self.wake_interval_seconds)).isoformat(),
            backend_name=self.backend.name,
            backend_model=self.backend.model,
            backend_mode=backend_mode,
            backend_metrics=backend_metrics,
            model_assisted=assist_used,
            user_model_snapshot=user_model,
            personal_context_snapshot=personal_context,
        )
        self.store.store(thought)
        provenance_state_ids = list(thought.state_refs)
        if not provenance_state_ids:
            provenance_state_ids = [f"cognition:{thought.thought_id}"]
        runtime.memory.add_episode(
            memory_id=new_id("epi"),
            category="cognition_cycle",
            data={
                "backend": {
                    "name": self.backend.name,
                    "model": self.backend.model,
                    "mode": backend_mode,
                    "model_assisted": assist_used,
                    "metrics": backend_metrics,
                },
                "thought": thought.to_dict(),
            },
            provenance_event_ids=[],
            provenance_state_ids=provenance_state_ids,
        )
        generated = runtime.synthesis_engine.maybe_generate_daily(
            runtime,
            source_thought_id=thought.thought_id,
            suppressed_reasons=list(thought.suppressed_reasons),
            now=now,
        )
        return {
            "status": "ok",
            "backend": self.backend.name,
            "model": self.backend.model,
            "backend_mode": backend_mode,
            "model_assisted": assist_used,
            "fallback_triggered": bool(backend_metrics.get("fallback_triggered")),
            "backend_metrics": backend_metrics,
            "thought_id": thought.thought_id,
            "hypothesis_count": len(thought.hypotheses),
            "interrupt_candidate_count": len(thought.interrupt_candidates),
            "suppressed_reason_count": len(thought.suppressed_reasons),
            "next_wake_at": thought.next_wake_at,
            "generated_synthesis": generated,
        }

    def close(self) -> None:
        self.store.close()
