from __future__ import annotations

from enum import Enum


class TraceStatus(str, Enum):
    OPEN = "open"
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATED = "compensated"

