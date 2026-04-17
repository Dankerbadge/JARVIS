from __future__ import annotations

from dataclasses import replace
from typing import Any

from .base import BackendHypothesis, CognitionBackend


class HeuristicCognitionBackend(CognitionBackend):
    name = "heuristic"
    model = "heuristic-v1"
    model_assisted = False

    def _domain_for_risk(self, risk: dict[str, Any]) -> str:
        value = risk.get("value", {})
        domain = str(value.get("domain") or value.get("project") or "").strip().lower()
        if domain:
            return domain
        key = str(risk.get("entity_key") or "")
        if "academic" in key:
            return "academics"
        return "zenith"

    def _dead_end_risk(self, domain: str, recent_outcomes: list[dict[str, Any]]) -> float:
        considered = [
            item
            for item in recent_outcomes
            if str(item.get("repo_id", "")).lower().find(domain) >= 0
        ]
        if not considered:
            return 0.1
        failures = sum(
            1
            for item in considered
            if str(item.get("status", "")).lower() in {"failure", "regression"}
        )
        return min(0.95, failures / max(1, len(considered)))

    def _severity_weight(self, value: dict[str, Any]) -> float:
        severity = str(value.get("severity") or "medium").lower()
        if severity in {"critical"}:
            return 1.0
        if severity in {"high"}:
            return 0.82
        if severity in {"medium"}:
            return 0.58
        return 0.4

    def generate_hypotheses(
        self,
        *,
        risks: list[dict[str, Any]],
        recent_outcomes: list[dict[str, Any]],
        max_hypotheses: int,
    ) -> list[BackendHypothesis]:
        hypotheses: list[BackendHypothesis] = []
        for risk in risks[: max_hypotheses]:
            value = risk.get("value", {})
            domain = self._domain_for_risk(risk)
            confidence = float(risk.get("confidence", 0.5))
            severity_weight = self._severity_weight(value)
            dead_end = self._dead_end_risk(domain, recent_outcomes)
            expected_value = max(
                0.0,
                min(1.0, severity_weight * confidence * (1.0 - dead_end * 0.4)),
            )
            novelty = max(0.0, min(1.0, 1.0 - dead_end))
            claim = (
                f"{domain} risk `{value.get('reason') or risk.get('entity_key')}` warrants active "
                "monitoring and bounded response."
            )
            hypotheses.append(
                BackendHypothesis(
                    claim=claim,
                    domain_tags=(domain,),
                    support_refs=tuple(str(item) for item in risk.get("source_refs", [])),
                    confidence=confidence,
                    expected_value=expected_value,
                    novelty=novelty,
                    dead_end_risk=dead_end,
                ).normalized()
            )
        hypotheses.sort(key=lambda item: (item.expected_value, item.confidence), reverse=True)
        return hypotheses[:max_hypotheses]

    def skepticism_pass(
        self,
        *,
        hypotheses: list[BackendHypothesis],
        context: dict[str, Any],
    ) -> list[BackendHypothesis]:
        out: list[BackendHypothesis] = []
        for hypothesis in hypotheses:
            skepticism = set(hypothesis.skepticism_flags)
            counter_refs = list(hypothesis.counter_refs)
            if hypothesis.confidence < 0.65:
                skepticism.add("low_confidence")
            if hypothesis.dead_end_risk > 0.65:
                skepticism.add("possible_dead_end_loop")
                counter_refs.append("recent_failure_cluster")
            if hypothesis.expected_value < 0.5:
                skepticism.add("low_expected_value")
            out.append(
                replace(
                    hypothesis,
                    skepticism_flags=tuple(sorted(skepticism)),
                    counter_refs=tuple(dict.fromkeys(counter_refs)),
                ).normalized()
            )
        return out

    def diagnose_dead_end(
        self,
        *,
        hypothesis: BackendHypothesis,
        context: dict[str, Any],
    ) -> BackendHypothesis:
        skepticism = set(hypothesis.skepticism_flags)
        expected_value = hypothesis.expected_value
        if hypothesis.dead_end_risk >= 0.75:
            skepticism.add("repeat_failure_cycle")
            expected_value = max(0.0, expected_value - 0.1)
        if hypothesis.novelty < 0.2:
            skepticism.add("stale_path")
        return replace(
            hypothesis,
            expected_value=expected_value,
            skepticism_flags=tuple(sorted(skepticism)),
        ).normalized()

    def draft_synthesis(
        self,
        *,
        kind: str,
        structured: dict[str, Any],
        context: dict[str, Any],
    ) -> str | None:
        if kind == "morning":
            priorities = structured.get("top_priorities", [])
            if not priorities:
                return "No urgent priorities identified; continue baseline monitoring."
            first = priorities[0]
            return (
                f"Primary focus starts in {first.get('domain', 'unknown')} for {first.get('risk_key')}; "
                "use bounded focus windows and avoid low-confidence interruptions."
            )
        if kind == "evening":
            changed = structured.get("what_changed", [])
            unresolved = structured.get("unresolved", [])
            return (
                f"Closed loop on {len(changed)} recent outcomes with {len(unresolved)} unresolved approval threads; "
                "carry unresolved high-impact work into tomorrow planning."
            )
        return None

    def draft_interrupt_rationale(
        self,
        *,
        candidate: dict[str, Any],
        decision: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str] | None:
        if bool(decision.get("delivered")):
            return (
                "Interrupt delivered because urgency-confidence blend exceeded the active threshold.",
                "Deferral increases coordination risk and reduces expected payoff.",
            )
        if bool(decision.get("suppression_window_hit")):
            return (
                "Interrupt suppressed because a policy suppression window is active.",
                "Re-evaluate once suppression clears or corroborating evidence increases confidence.",
            )
        return (
            "Interrupt suppressed because signal quality is below threshold.",
            "Wait for additional corroboration before notifying.",
        )
