from __future__ import annotations

from .models import SuggestionCandidate


class SuggestionRanker:
    @staticmethod
    def _score(candidate: SuggestionCandidate) -> float:
        return (float(candidate.expected_value) * float(candidate.confidence)) - (float(candidate.cost) * 0.3)

    def rank(self, candidates: list[SuggestionCandidate], *, limit: int = 20) -> list[SuggestionCandidate]:
        ranked = sorted(candidates, key=self._score, reverse=True)
        return ranked[: max(1, int(limit))]

