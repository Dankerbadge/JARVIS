from __future__ import annotations

from typing import Any


class NextActionRanker:
    @staticmethod
    def _score(action: dict[str, Any]) -> float:
        expected = float(action.get("expected_value") or 0.0)
        confidence = float(action.get("confidence") or 0.0)
        authority = str(action.get("required_authority") or "none").strip().lower()
        authority_penalty = {"none": 0.0, "soft": 0.08, "hard": 0.15}.get(authority, 0.1)
        return (expected * confidence) - authority_penalty

    def rank(self, actions: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
        ranked = sorted(list(actions or []), key=self._score, reverse=True)
        return ranked[: max(1, int(limit))]
