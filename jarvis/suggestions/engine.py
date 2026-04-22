from __future__ import annotations

from typing import Any

from .detectors import SuggestionDetectors
from .ranker import SuggestionRanker


class SuggestionEngine:
    def __init__(self) -> None:
        self.detectors = SuggestionDetectors()
        self.ranker = SuggestionRanker()

    def propose_from_reasoning(
        self,
        traces: list[dict[str, Any]],
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        candidates = self.detectors.from_reasoning_traces(list(traces or []))
        ranked = self.ranker.rank(candidates, limit=limit)
        return [item.to_dict() for item in ranked]

