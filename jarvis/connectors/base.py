from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..models import EventEnvelope


@dataclass
class ConnectorPollResult:
    events: list[EventEnvelope]
    cursor: dict[str, Any] | None


class BaseConnector(ABC):
    """Polling connector contract with explicit cursor round-tripping."""

    name: str

    @abstractmethod
    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        """Return new events and the next cursor state."""
        raise NotImplementedError

