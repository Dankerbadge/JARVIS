from __future__ import annotations

from typing import Any

from .security import SecurityManager


class ApprovalInbox:
    """Convenience wrapper for pending protected actions."""

    def __init__(self, security: SecurityManager) -> None:
        self.security = security

    def list(self, status: str = "pending") -> list[dict[str, Any]]:
        return self.security.list_approvals(status=status)

    def approve(self, approval_id: str, actor: str = "user") -> None:
        self.security.approve(approval_id, approved_by=actor)

    def deny(self, approval_id: str, actor: str = "user") -> None:
        self.security.deny(approval_id, denied_by=actor)

    def show(self, approval_id: str) -> dict[str, Any] | None:
        approval = self.security.get_approval(approval_id)
        if not approval:
            return None
        packet = self.security.get_approval_packet(approval_id)
        return {
            "approval": approval,
            "packet": packet,
        }
