from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from ..models import utc_now_iso


@dataclass(frozen=True)
class SuggestionCandidate:
    kind: str
    domain: str
    trigger: str
    why_now: str
    why_not_later: str
    cost: float
    confidence: float
    expected_value: float
    required_context: dict[str, Any] = field(default_factory=dict)
    approval_class: str = "none"
    source_trace_id: str | None = None
    suggestion_id: str = field(default_factory=lambda: f"sgn_{uuid4().hex}")
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

