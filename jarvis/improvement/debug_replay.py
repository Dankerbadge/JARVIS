from __future__ import annotations

from typing import Any

from ..reasoning.replayer import ReasoningReplayer
from ..reasoning.store import ReasoningStore
from .hypothesis_lab import HypothesisLabStore


class ExperimentDebugService:
    def __init__(
        self,
        *,
        lab_store: HypothesisLabStore,
        reasoning_store: ReasoningStore,
        reasoning_replayer: ReasoningReplayer,
    ) -> None:
        self.lab_store = lab_store
        self.reasoning_store = reasoning_store
        self.reasoning_replayer = reasoning_replayer

    @staticmethod
    def _root_cause_hints(evaluation: dict[str, Any]) -> list[str]:
        hints: list[str] = []
        metric = evaluation.get("metric_result") if isinstance(evaluation.get("metric_result"), dict) else {}
        sample = evaluation.get("sample_result") if isinstance(evaluation.get("sample_result"), dict) else {}
        guardrails = list(evaluation.get("guardrail_results") or [])

        if bool(metric.get("missing")):
            hints.append("Primary metric data is missing; instrumentation or metric mapping should be fixed first.")
        elif not bool(metric.get("pass")):
            hints.append("Primary success metric did not clear the minimum effect threshold.")

        if not bool(sample.get("pass", True)):
            hints.append("Sample size is below the configured floor; rerun with a larger controlled cohort.")

        for item in guardrails:
            if not isinstance(item, dict):
                continue
            if bool(item.get("pass")):
                continue
            metric_name = str(item.get("metric") or "guardrail")
            hints.append(f"Guardrail violation on '{metric_name}' blocked promotion.")

        if not hints:
            hints.append("No blocking checks detected; candidate is eligible for staged promotion.")
        return hints

    def debug_run(
        self,
        *,
        run_id: str,
        include_decision_timeline: bool = True,
    ) -> dict[str, Any]:
        run = self.lab_store.get_experiment_run(str(run_id))
        if not isinstance(run, dict):
            return {
                "run_id": str(run_id),
                "found": False,
            }

        hypothesis = self.lab_store.get_hypothesis(str(run.get("hypothesis_id") or ""))
        evaluation = dict(run.get("evaluation") or {})

        failed_checks: list[dict[str, Any]] = []
        metric = evaluation.get("metric_result") if isinstance(evaluation.get("metric_result"), dict) else {}
        if metric and not bool(metric.get("pass")):
            failed_checks.append(
                {
                    "type": "metric",
                    "metric": metric.get("metric"),
                    "baseline": metric.get("baseline"),
                    "candidate": metric.get("candidate"),
                    "signed_effect": metric.get("signed_effect"),
                    "min_effect": metric.get("min_effect"),
                }
            )
        sample = evaluation.get("sample_result") if isinstance(evaluation.get("sample_result"), dict) else {}
        if sample and not bool(sample.get("pass", True)):
            failed_checks.append(
                {
                    "type": "sample_size",
                    "sample_size": sample.get("sample_size"),
                    "min_sample_size": sample.get("min_sample_size"),
                }
            )
        for row in list(evaluation.get("guardrail_results") or []):
            if not isinstance(row, dict):
                continue
            if bool(row.get("pass")):
                continue
            failed_checks.append(
                {
                    "type": "guardrail",
                    "metric": row.get("metric"),
                    "value": row.get("value"),
                    "op": row.get("op"),
                    "threshold": row.get("threshold"),
                    "reason": row.get("reason"),
                }
            )

        source_trace_id = str(run.get("source_trace_id") or "").strip()
        reasoning_trace = self.reasoning_store.get_trace(source_trace_id) if source_trace_id else None

        step_timeline: dict[str, Any] | None = None
        if include_decision_timeline and isinstance(reasoning_trace, dict):
            plan_id = str(reasoning_trace.get("plan_id") or "").strip()
            step_id = str(reasoning_trace.get("step_id") or "").strip()
            if plan_id and step_id:
                step_timeline = self.reasoning_replayer.replay_step_timeline(plan_id=plan_id, step_id=step_id)

        return {
            "run_id": str(run.get("run_id") or ""),
            "found": True,
            "run": run,
            "hypothesis": hypothesis,
            "evaluation": evaluation,
            "failed_checks": failed_checks,
            "root_cause_hints": self._root_cause_hints(evaluation),
            "reasoning_trace": reasoning_trace,
            "step_timeline": step_timeline,
        }
