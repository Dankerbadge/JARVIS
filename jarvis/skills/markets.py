from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import EventEnvelope, PlanArtifact, PlanStep, new_id, utc_now_iso
from ..state_index import (
    latest_market_abstention_key,
    latest_market_event_key,
    latest_market_handoff_key,
    latest_market_opportunity_key,
    latest_market_outcome_key,
    latest_market_risk_posture_key,
)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


class MarketsSkill:
    """Suggestion-first market skill pack (no direct trade execution)."""

    def __init__(self, workspace_path: str | Path) -> None:
        self.workspace_path = Path(workspace_path).resolve()

    def _opportunity_confidence(self, payload: dict[str, Any]) -> float:
        return max(
            _as_float(payload.get("confidence"), 0.0),
            _as_float(payload.get("model_confidence"), 0.0),
        )

    def _low_downside(self, payload: dict[str, Any]) -> bool:
        downside_bps = abs(_as_float(payload.get("downside_bps"), 9999.0))
        downside_pct = abs(_as_float(payload.get("downside_pct"), 9999.0))
        if downside_pct <= 2.0:
            return True
        return downside_bps <= 80

    def _opportunity_reason(self, payload: dict[str, Any]) -> str:
        return str(payload.get("thesis") or payload.get("reason") or "market_signal").strip()

    def extract_candidates(self, event: EventEnvelope) -> list[dict[str, Any]]:
        if not event.source_type.startswith("market."):
            return []
        payload = dict(event.payload or {})
        account_id = str(payload.get("account_id") or "default")
        symbol = str(payload.get("symbol") or "unknown")
        signal_id = str(payload.get("signal_id") or payload.get("source_item_id") or event.event_id)
        handoff_id = str(payload.get("handoff_id") or payload.get("source_item_id") or signal_id)
        source_refs = [event.event_id]
        candidates: list[dict[str, Any]] = []

        if event.source_type == "market.signal_detected":
            confidence = self._opportunity_confidence(payload)
            upside_bps = _as_float(payload.get("upside_bps"), 0.0)
            downside_bps = abs(_as_float(payload.get("downside_bps"), 9999.0))
            time_to_expiry_hours = _as_float(
                payload.get("expiry_horizon_hours") or payload.get("horizon_hours"),
                0.0,
            )
            support_signals = payload.get("support_signals") if isinstance(payload.get("support_signals"), list) else []
            counter_signals = payload.get("counter_signals") if isinstance(payload.get("counter_signals"), list) else []
            low_downside = self._low_downside(payload)
            reason = self._opportunity_reason(payload)

            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_market_opportunity_key(signal_id),
                    "entity_type": "Artifact",
                    "value": {
                        "project": "markets",
                        "domain": "markets",
                        "account_id": account_id,
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "thesis": reason,
                        "support_signals": support_signals,
                        "counter_signals": counter_signals,
                        "upside_bps": upside_bps,
                        "downside_bps": downside_bps,
                        "confidence": confidence,
                        "expiry_horizon_hours": time_to_expiry_hours,
                        "why_now": str(payload.get("why_now") or "Signal has current edge and finite horizon."),
                        "why_not": str(payload.get("why_not") or "Abstain if confidence degrades or downside expands."),
                        "signal_source_kind": str(payload.get("ingestion_source_kind") or ""),
                        "signal_provider": str(payload.get("ingestion_provider") or ""),
                        "updated_from": event.event_id,
                    },
                    "confidence": max(0.55, min(0.99, confidence)),
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )

            high_conf = confidence >= 0.82
            if high_conf and low_downside:
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": f"risk:markets:opportunity:{signal_id}",
                        "entity_type": "Risk",
                        "value": {
                            "project": "markets",
                            "domain": "markets",
                            "account_id": account_id,
                            "symbol": symbol,
                            "signal_id": signal_id,
                            "severity": "high" if confidence < 0.9 else "critical",
                            "reason": "high_confidence_low_downside_opportunity",
                            "confidence": confidence,
                            "upside_bps": upside_bps,
                            "downside_bps": downside_bps,
                            "expiry_horizon_hours": time_to_expiry_hours,
                        },
                        "confidence": max(0.7, min(0.99, confidence)),
                        "source_refs": source_refs,
                        "last_verified_at": utc_now_iso(),
                    }
                )
            else:
                abstain_reason = "insufficient_confidence"
                if high_conf and not low_downside:
                    abstain_reason = "downside_too_wide"
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": latest_market_abstention_key(signal_id),
                        "entity_type": "Artifact",
                        "value": {
                            "project": "markets",
                            "domain": "markets",
                            "account_id": account_id,
                            "signal_id": signal_id,
                            "symbol": symbol,
                            "confidence": confidence,
                            "upside_bps": upside_bps,
                            "downside_bps": downside_bps,
                            "abstain_reason": abstain_reason,
                            "counter_signals": counter_signals,
                            "updated_from": event.event_id,
                        },
                        "confidence": 0.86,
                        "source_refs": source_refs,
                        "last_verified_at": utc_now_iso(),
                    }
                )

        if event.source_type == "market.position_snapshot":
            positions = payload.get("positions") if isinstance(payload.get("positions"), list) else []
            gross = _as_float(payload.get("gross_exposure_pct"), 0.0)
            net = _as_float(payload.get("net_exposure_pct"), 0.0)
            regime = str(payload.get("risk_regime") or "").strip().lower()
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_market_risk_posture_key(account_id),
                    "entity_type": "Artifact",
                    "value": {
                        "project": "markets",
                        "domain": "markets",
                        "account_id": account_id,
                        "positions": positions,
                        "gross_exposure_pct": gross,
                        "net_exposure_pct": net,
                        "risk_regime": regime or None,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.93,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )
            if gross >= 75:
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": f"risk:markets:exposure:{account_id}",
                        "entity_type": "Risk",
                        "value": {
                            "project": "markets",
                            "domain": "markets",
                            "account_id": account_id,
                            "severity": "high",
                            "reason": "market_exposure_high",
                            "gross_exposure_pct": gross,
                            "net_exposure_pct": net,
                            "risk_regime": regime or None,
                        },
                        "confidence": 0.88,
                        "source_refs": source_refs,
                        "last_verified_at": utc_now_iso(),
                    }
                )

        if event.source_type == "market.risk_regime_changed":
            regime = str(payload.get("risk_regime") or "").strip().lower()
            if regime:
                severity = "medium"
                if regime in {"risk_off", "high_volatility", "stressed"}:
                    severity = "high"
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": f"risk:markets:regime:{account_id}",
                        "entity_type": "Risk",
                        "value": {
                            "project": "markets",
                            "domain": "markets",
                            "account_id": account_id,
                            "severity": severity,
                            "reason": "market_risk_regime_changed",
                            "risk_regime": regime,
                            "previous_risk_regime": payload.get("previous_risk_regime"),
                        },
                        "confidence": 0.82 if severity == "high" else 0.68,
                        "source_refs": source_refs,
                        "last_verified_at": utc_now_iso(),
                    }
                )

        if event.source_type in {"market.event_upcoming", "market.opportunity_expired"}:
            event_id = str(payload.get("event_id") or payload.get("source_item_id") or event.event_id)
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_market_event_key(event_id),
                    "entity_type": "Artifact",
                    "value": {
                        "project": "markets",
                        "domain": "markets",
                        "account_id": account_id,
                        "event_id": event_id,
                        "symbol": symbol,
                        "event_kind": event.source_type,
                        "event_at": payload.get("event_at"),
                        "importance": payload.get("importance"),
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.78,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )

        if event.source_type == "market.handoff_outcome":
            status = str(payload.get("status") or payload.get("outcome") or "unknown").strip().lower()
            reason = str(payload.get("reason") or payload.get("note") or "").strip()
            decision_confidence = self._opportunity_confidence(payload)
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_market_handoff_key(handoff_id),
                    "entity_type": "Artifact",
                    "value": {
                        "project": "markets",
                        "domain": "markets",
                        "account_id": account_id,
                        "handoff_id": handoff_id,
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "status": status,
                        "reason": reason,
                        "decision_confidence": decision_confidence,
                        "updated_from": event.event_id,
                    },
                    "confidence": max(0.55, min(0.99, decision_confidence or 0.8)),
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_market_outcome_key(handoff_id),
                    "entity_type": "Artifact",
                    "value": {
                        "project": "markets",
                        "domain": "markets",
                        "account_id": account_id,
                        "handoff_id": handoff_id,
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "status": status,
                        "filled_qty": payload.get("filled_qty"),
                        "avg_fill_price": payload.get("avg_fill_price"),
                        "pnl_bps": payload.get("pnl_bps"),
                        "reason": reason,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.92,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )
            if status in {"stopped", "rejected", "expired"}:
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": f"risk:markets:handoff:{handoff_id}",
                        "entity_type": "Risk",
                        "value": {
                            "project": "markets",
                            "domain": "markets",
                            "account_id": account_id,
                            "signal_id": signal_id,
                            "symbol": symbol,
                            "severity": "high" if status == "stopped" else "medium",
                            "reason": f"market_handoff_{status}",
                            "handoff_id": handoff_id,
                            "outcome_status": status,
                        },
                        "confidence": 0.84 if status == "stopped" else 0.72,
                        "source_refs": source_refs,
                        "last_verified_at": utc_now_iso(),
                    }
                )
        return candidates

    def propose_plan(self, active_risks: list[dict[str, Any]]) -> PlanArtifact | None:
        market_risks = []
        for risk in active_risks:
            value = risk.get("value", {})
            domain = str(value.get("domain") or value.get("project") or "").lower()
            if domain == "markets":
                market_risks.append(risk)
        if not market_risks:
            return None
        top = market_risks[0].get("value", {})
        reason = str(top.get("reason") or "market_signal")
        severity = str(top.get("severity") or "medium").lower()
        account_id = str(top.get("account_id") or "default")
        signal_id = str(top.get("signal_id") or "")
        symbol = str(top.get("symbol") or "unknown")
        confidence = _as_float(top.get("confidence"), 0.0)
        downside_bps = abs(_as_float(top.get("downside_bps"), 9999.0))

        steps: list[PlanStep] = [
            PlanStep(
                action_class="P0",
                proposed_action="markets_collect_context",
                expected_effect="Collected market context for ranking and abstention analysis.",
                rollback="none",
                payload={
                    "domain": "markets",
                    "account_id": account_id,
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "reason": reason,
                    "severity": severity,
                    "confidence": confidence,
                },
            ),
            PlanStep(
                action_class="P1",
                proposed_action="markets_generate_suggestion_brief",
                expected_effect="Generated suggestion-first opportunity/abstention brief with skepticism.",
                rollback="discard_brief",
                payload={
                    "domain": "markets",
                    "account_id": account_id,
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "reason": reason,
                    "confidence": confidence,
                    "downside_bps": downside_bps,
                },
            ),
        ]
        approval_requirements: list[str] = []
        if confidence >= 0.88 and downside_bps <= 80:
            handoff_id = new_id("mkt_handoff")
            steps.append(
                PlanStep(
                    action_class="P2",
                    proposed_action="markets_prepare_handoff_packet",
                    expected_effect="Prepared handoff artifact for external investing bot/operator review.",
                    rollback="discard_handoff_packet",
                    payload={
                        "domain": "markets",
                        "account_id": account_id,
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "handoff_id": handoff_id,
                        "confidence": confidence,
                        "downside_bps": downside_bps,
                        "reason": reason,
                    },
                    requires_approval=True,
                )
            )
            approval_requirements.append("P2 approval for markets handoff packet")

        return PlanArtifact(
            intent="evaluate_market_signal",
            priority="high" if severity in {"high", "critical"} else "medium",
            reasoning_summary=(
                f"Markets risk/opportunity detected for {symbol} ({reason}); generated suggestion-first review plan."
            ),
            steps=steps,
            approval_requirements=approval_requirements,
            expires_at=utc_now_iso(),
        )

    def tool_collect_context(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        return {
            "domain": "markets",
            "account_id": payload.get("account_id"),
            "signal_id": payload.get("signal_id"),
            "symbol": payload.get("symbol"),
            "reason": payload.get("reason"),
            "severity": payload.get("severity"),
            "dry_run": dry_run,
        }

    def tool_generate_suggestion_brief(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        confidence = _as_float(payload.get("confidence"), 0.0)
        downside_bps = abs(_as_float(payload.get("downside_bps"), 9999.0))
        recommendation = "abstain"
        if confidence >= 0.82 and downside_bps <= 120:
            recommendation = "review_candidate"
        skepticism = []
        if confidence < 0.85:
            skepticism.append("confidence_not_top_decile")
        if downside_bps > 80:
            skepticism.append("downside_wider_than_preferred")
        return {
            "domain": "markets",
            "account_id": payload.get("account_id"),
            "signal_id": payload.get("signal_id"),
            "symbol": payload.get("symbol"),
            "recommendation": recommendation,
            "confidence": confidence,
            "downside_bps": downside_bps,
            "skepticism_flags": skepticism,
            "dry_run": dry_run,
        }

    def tool_prepare_handoff_packet(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        handoff_id = str(payload.get("handoff_id") or new_id("mkt_handoff"))
        return {
            "domain": "markets",
            "account_id": payload.get("account_id"),
            "signal_id": payload.get("signal_id"),
            "symbol": payload.get("symbol"),
            "handoff_id": handoff_id,
            "packet_type": "suggestion_first_handoff",
            "confidence": _as_float(payload.get("confidence"), 0.0),
            "downside_bps": abs(_as_float(payload.get("downside_bps"), 0.0)),
            "reason": payload.get("reason"),
            "status": "prepared_for_external_bot_review",
            "dry_run": dry_run,
        }

    def register_tools(self) -> dict[str, Any]:
        return {
            "markets_collect_context": self.tool_collect_context,
            "markets_generate_suggestion_brief": self.tool_generate_suggestion_brief,
            "markets_prepare_handoff_packet": self.tool_prepare_handoff_packet,
        }
