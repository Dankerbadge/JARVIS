from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class EventEnvelope:
    source: str
    source_type: str
    payload: dict[str, Any]
    auth_context: str = "local"
    occurred_at: str = field(default_factory=utc_now_iso)
    ingested_at: str = field(default_factory=utc_now_iso)
    event_id: str = field(default_factory=lambda: new_id("evt"))
    trace_id: str = field(default_factory=lambda: new_id("trc"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanStep:
    action_class: str
    proposed_action: str
    expected_effect: str
    rollback: str
    payload: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    step_id: str = field(default_factory=lambda: new_id("stp"))
    idempotency_key: str = field(default_factory=lambda: new_id("idem"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanArtifact:
    intent: str
    priority: str
    reasoning_summary: str
    steps: list[PlanStep]
    approval_requirements: list[str]
    expires_at: str
    plan_id: str = field(default_factory=lambda: new_id("pln"))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [step.to_dict() for step in self.steps]
        return data

