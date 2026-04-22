from __future__ import annotations

from enum import Enum
from typing import Mapping


class StepState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    APPROVED = "approved"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATED = "compensated"


_ALLOWED_TRANSITIONS: Mapping[str, set[str]] = {
    StepState.QUEUED.value: {
        StepState.RUNNING.value,
        StepState.BLOCKED.value,
        StepState.FAILED.value,
    },
    StepState.RUNNING.value: {
        StepState.APPROVED.value,
        StepState.BLOCKED.value,
        StepState.SUCCEEDED.value,
        StepState.FAILED.value,
        StepState.COMPENSATED.value,
    },
    StepState.BLOCKED.value: {
        StepState.RUNNING.value,
        StepState.APPROVED.value,
        StepState.FAILED.value,
        StepState.COMPENSATED.value,
    },
    StepState.APPROVED.value: {
        StepState.RUNNING.value,
        StepState.BLOCKED.value,
        StepState.SUCCEEDED.value,
        StepState.FAILED.value,
        StepState.COMPENSATED.value,
    },
    # Re-runs are allowed for terminal states so operators can retry intentionally.
    StepState.SUCCEEDED.value: {StepState.RUNNING.value},
    StepState.FAILED.value: {StepState.RUNNING.value, StepState.COMPENSATED.value},
    StepState.COMPENSATED.value: {StepState.RUNNING.value},
}


def normalize_step_state(step_state: StepState | str) -> str:
    normalized = (
        step_state.value
        if isinstance(step_state, StepState)
        else str(step_state or "").strip().lower()
    )
    allowed = {item.value for item in StepState}
    if normalized not in allowed:
        raise ValueError(f"Unsupported step state: {step_state}")
    return normalized


def transition_allowed(previous_state: str | None, next_state: StepState | str) -> bool:
    normalized_next = normalize_step_state(next_state)
    if previous_state is None:
        # Backward compatible with historical plans that predate queued-at-save instrumentation.
        return normalized_next in {
            StepState.QUEUED.value,
            StepState.RUNNING.value,
            StepState.BLOCKED.value,
            StepState.APPROVED.value,
            StepState.FAILED.value,
            StepState.SUCCEEDED.value,
            StepState.COMPENSATED.value,
        }
    normalized_previous = normalize_step_state(previous_state)
    allowed = _ALLOWED_TRANSITIONS.get(normalized_previous, set())
    return normalized_next in allowed
