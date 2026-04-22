from __future__ import annotations

import statistics
from typing import Any


class LearningEvaluator:
    def evaluate_examples(self, examples: list[dict[str, Any]]) -> dict[str, Any]:
        rows = list(examples or [])
        if not rows:
            return {
                "total_examples": 0,
                "avg_utility": 0.0,
                "median_utility": 0.0,
                "min_utility": 0.0,
                "max_utility": 0.0,
                "outcome_distribution": {},
                "accepted_feedback_rate": None,
            }

        utilities = [float(item.get("utility_score") or 0.0) for item in rows]
        outcome_distribution: dict[str, int] = {}
        accepted_feedback_values: list[bool] = []
        for item in rows:
            outcome = str(item.get("observed_outcome") or "").strip().lower() or "unknown"
            outcome_distribution[outcome] = int(outcome_distribution.get(outcome, 0)) + 1

            features = item.get("feature_vector")
            if isinstance(features, dict) and "suggestion_accepted" in features:
                accepted_feedback_values.append(bool(features.get("suggestion_accepted")))

        accepted_feedback_rate: float | None = None
        if accepted_feedback_values:
            accepted_feedback_rate = float(sum(1 for value in accepted_feedback_values if value)) / float(
                len(accepted_feedback_values)
            )

        return {
            "total_examples": len(rows),
            "avg_utility": round(sum(utilities) / float(len(utilities)), 6),
            "median_utility": round(float(statistics.median(utilities)), 6),
            "min_utility": round(min(utilities), 6),
            "max_utility": round(max(utilities), 6),
            "outcome_distribution": outcome_distribution,
            "accepted_feedback_rate": round(accepted_feedback_rate, 6)
            if accepted_feedback_rate is not None
            else None,
        }
