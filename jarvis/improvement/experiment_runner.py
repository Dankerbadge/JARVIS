from __future__ import annotations

from typing import Any


class ExperimentRunner:
    _VALID_OPS = {"<", "<=", ">", ">=", "==", "!="}

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _compare(cls, *, left: float, op: str, right: float) -> bool:
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        raise ValueError(f"Unsupported guardrail comparator: {op}")

    def evaluate(
        self,
        *,
        hypothesis: dict[str, Any],
        baseline_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
        sample_size: int | None = None,
        guardrail_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        criteria = dict(hypothesis.get("success_criteria") or {})
        metric_name = str(criteria.get("metric") or "").strip()
        direction = str(criteria.get("direction") or "increase").strip().lower()
        min_effect = self._to_float(criteria.get("min_effect"))
        if min_effect is None:
            min_effect = 0.0

        baseline_value = self._to_float((baseline_metrics or {}).get(metric_name)) if metric_name else None
        candidate_value = self._to_float((candidate_metrics or {}).get(metric_name)) if metric_name else None

        raw_effect: float | None = None
        signed_effect: float | None = None
        metric_pass = False
        metric_missing = not metric_name or baseline_value is None or candidate_value is None
        if not metric_missing:
            raw_effect = float(candidate_value - baseline_value)
            signed_effect = raw_effect if direction != "decrease" else -raw_effect
            metric_pass = bool(signed_effect >= float(min_effect))

        min_sample_size_raw = criteria.get("min_sample_size")
        try:
            min_sample_size = int(min_sample_size_raw) if min_sample_size_raw is not None else None
        except (TypeError, ValueError):
            min_sample_size = None
        sample_pass = True
        if min_sample_size is not None:
            sample_pass = sample_size is not None and int(sample_size) >= int(min_sample_size)

        guardrail_rows = list(criteria.get("guardrails") or [])
        guardrail_source = dict(candidate_metrics or {})
        guardrail_source.update(dict(guardrail_metrics or {}))
        guardrail_results: list[dict[str, Any]] = []
        for row in guardrail_rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("metric") or "").strip()
            op = str(row.get("op") or "<=").strip()
            threshold = self._to_float(row.get("value"))
            if op not in self._VALID_OPS:
                op = "<="
            value = self._to_float(guardrail_source.get(name)) if name else None
            passed = False
            reason = None
            if not name:
                reason = "missing_metric_name"
            elif value is None:
                reason = "missing_metric_value"
            elif threshold is None:
                reason = "missing_threshold"
            else:
                passed = self._compare(left=float(value), op=op, right=float(threshold))
                if not passed:
                    reason = "threshold_violation"
            guardrail_results.append(
                {
                    "metric": name,
                    "op": op,
                    "threshold": threshold,
                    "value": value,
                    "pass": bool(passed),
                    "reason": reason,
                }
            )

        guardrails_pass = all(bool(item.get("pass")) for item in guardrail_results)
        overall_pass = bool(metric_pass and sample_pass and guardrails_pass)
        if overall_pass:
            verdict = "promote"
        elif not guardrails_pass:
            verdict = "blocked_guardrail"
        elif not sample_pass:
            verdict = "insufficient_data"
        elif metric_missing:
            verdict = "invalid_measurement"
        else:
            verdict = "needs_iteration"

        return {
            "verdict": verdict,
            "overall_pass": overall_pass,
            "metric_result": {
                "metric": metric_name,
                "direction": direction,
                "min_effect": float(min_effect),
                "baseline": baseline_value,
                "candidate": candidate_value,
                "raw_effect": raw_effect,
                "signed_effect": signed_effect,
                "pass": bool(metric_pass),
                "missing": bool(metric_missing),
            },
            "sample_result": {
                "sample_size": int(sample_size) if sample_size is not None else None,
                "min_sample_size": int(min_sample_size) if min_sample_size is not None else None,
                "pass": bool(sample_pass),
            },
            "guardrails_pass": bool(guardrails_pass),
            "guardrail_results": guardrail_results,
        }
