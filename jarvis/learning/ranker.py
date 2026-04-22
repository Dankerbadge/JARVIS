from __future__ import annotations

from collections import defaultdict
from typing import Any


class LearningActionRanker:
    @staticmethod
    def _score(
        *,
        avg_utility: float,
        success_rate: float,
        sample_size: int,
        compensation_rate: float,
        failure_rate: float,
        blocked_rate: float,
    ) -> float:
        bounded_size = min(max(int(sample_size), 0), 20)
        base = float(avg_utility) + (float(success_rate) * 0.15) + (bounded_size * 0.05)
        penalty = (
            (float(compensation_rate) * 0.45)
            + (float(failure_rate) * 0.35)
            + (float(blocked_rate) * 0.15)
        )
        return base - penalty

    def rank_actions(
        self,
        examples: list[dict[str, Any]],
        *,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in list(examples or []):
            action = str(item.get("chosen_action") or "").strip()
            if not action:
                continue
            grouped[action].append(item)

        ranked: list[dict[str, Any]] = []
        for action, rows in grouped.items():
            if not rows:
                continue
            utilities = [float(item.get("utility_score") or 0.0) for item in rows]
            outcomes = [str(item.get("observed_outcome") or "").strip().lower() for item in rows]
            successes = sum(1 for value in outcomes if value == "succeeded")
            failures = sum(1 for value in outcomes if value == "failed")
            blocked = sum(1 for value in outcomes if value == "blocked")
            compensated = sum(1 for value in outcomes if value == "compensated")
            compensation_feature_signals = 0
            for item in rows:
                features = item.get("feature_vector") if isinstance(item.get("feature_vector"), dict) else {}
                if int(features.get("compensated_attempts") or 0) > 0:
                    compensation_feature_signals += 1
                elif str(features.get("trace_status") or "").strip().lower() == "compensated":
                    compensation_feature_signals += 1
            compensation_signal_count = max(compensated, compensation_feature_signals)

            compensation_rate = float(compensation_signal_count) / float(len(rows))
            failure_rate = float(failures) / float(len(rows))
            blocked_rate = float(blocked) / float(len(rows))
            success_rate = float(successes) / float(len(rows))
            avg_utility = sum(utilities) / float(len(utilities))
            score = self._score(
                avg_utility=avg_utility,
                success_rate=success_rate,
                sample_size=len(rows),
                compensation_rate=compensation_rate,
                failure_rate=failure_rate,
                blocked_rate=blocked_rate,
            )
            bounded_size = min(max(int(len(rows)), 0), 20)
            base_score = float(avg_utility) + (float(success_rate) * 0.15) + (bounded_size * 0.05)
            penalty = base_score - score
            ranked.append(
                {
                    "chosen_action": action,
                    "sample_size": len(rows),
                    "avg_utility": round(avg_utility, 6),
                    "success_rate": round(success_rate, 6),
                    "compensation_rate": round(compensation_rate, 6),
                    "failure_rate": round(failure_rate, 6),
                    "blocked_rate": round(blocked_rate, 6),
                    "penalty": round(penalty, 6),
                    "score": round(score, 6),
                }
            )

        ranked.sort(
            key=lambda item: (
                float(item.get("score") or 0.0),
                float(item.get("avg_utility") or 0.0),
                int(item.get("sample_size") or 0),
            ),
            reverse=True,
        )
        return ranked[: max(1, int(top_k))]
