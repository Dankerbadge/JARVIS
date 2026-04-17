from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class BackendHypothesis:
    claim: str
    domain_tags: tuple[str, ...]
    support_refs: tuple[str, ...] = ()
    counter_refs: tuple[str, ...] = ()
    confidence: float = 0.5
    expected_value: float = 0.5
    novelty: float = 0.5
    dead_end_risk: float = 0.1
    skepticism_flags: tuple[str, ...] = ()

    def normalized(self) -> "BackendHypothesis":
        clean_domains = tuple(sorted({str(tag).strip().lower() for tag in self.domain_tags if str(tag).strip()}))
        if not clean_domains:
            clean_domains = ("zenith",)
        clean_flags = tuple(sorted({str(flag).strip().lower() for flag in self.skepticism_flags if str(flag).strip()}))
        return replace(
            self,
            claim=str(self.claim).strip() or "Unspecified hypothesis.",
            domain_tags=clean_domains,
            support_refs=tuple(str(item).strip() for item in self.support_refs if str(item).strip()),
            counter_refs=tuple(str(item).strip() for item in self.counter_refs if str(item).strip()),
            confidence=_clamp(self.confidence),
            expected_value=_clamp(self.expected_value),
            novelty=_clamp(self.novelty),
            dead_end_risk=_clamp(self.dead_end_risk),
            skepticism_flags=clean_flags,
        )


class CognitionBackend(ABC):
    name = "base"
    model: str | None = None
    model_assisted = False
    supports_model_assisted_skepticism = False
    supports_model_assisted_synthesis = False
    fallback_backend = "heuristic"
    timeout_seconds: float | None = None
    retry_attempts = 0

    def __init__(self, *, local_only: bool = True) -> None:
        self.local_only = bool(local_only)
        self._cycle_metrics = {
            "assist_used": False,
            "fallback_triggered": False,
            "query_count": 0,
            "successful_query_count": 0,
            "average_latency_ms": None,
            "errors": [],
        }

    @abstractmethod
    def generate_hypotheses(
        self,
        *,
        risks: list[dict[str, Any]],
        recent_outcomes: list[dict[str, Any]],
        max_hypotheses: int,
    ) -> list[BackendHypothesis]:
        raise NotImplementedError

    def skepticism_pass(
        self,
        *,
        hypotheses: list[BackendHypothesis],
        context: dict[str, Any],
    ) -> list[BackendHypothesis]:
        return [item.normalized() for item in hypotheses]

    def diagnose_dead_end(
        self,
        *,
        hypothesis: BackendHypothesis,
        context: dict[str, Any],
    ) -> BackendHypothesis:
        return hypothesis.normalized()

    def draft_synthesis(
        self,
        *,
        kind: str,
        structured: dict[str, Any],
        context: dict[str, Any],
    ) -> str | None:
        return None

    def draft_interrupt_rationale(
        self,
        *,
        candidate: dict[str, Any],
        decision: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str] | None:
        return None

    def reset_cycle_metrics(self) -> None:
        self._cycle_metrics = {
            "assist_used": False,
            "fallback_triggered": False,
            "query_count": 0,
            "successful_query_count": 0,
            "average_latency_ms": None,
            "errors": [],
        }

    def get_cycle_metrics(self) -> dict[str, Any]:
        return dict(self._cycle_metrics)

    def get_config(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "model": self.model,
            "enabled": True,
            "local_only": self.local_only,
            "timeout_seconds": self.timeout_seconds,
            "retry_attempts": int(self.retry_attempts),
            "fallback_backend": self.fallback_backend,
            "model_assisted_synthesis": bool(self.supports_model_assisted_synthesis),
            "model_assisted_skepticism": bool(self.supports_model_assisted_skepticism),
        }
