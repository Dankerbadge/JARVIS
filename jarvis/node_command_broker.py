from __future__ import annotations

from typing import Any

from .security import ActionClass


_READONLY_PREFIXES = ("camera.capture", "camera.snapshot", "device.status", "notifications.list")
_NOTIFY_PREFIXES = ("notifications.send", "canvas.draw", "canvas.show")
_CONTROL_PREFIXES = ("device.lock", "device.unlock", "device.focus", "system.volume")
_EXEC_PREFIXES = ("system.run", "system.exec", "shell.", "exec.", "host.exec")


class NodeCommandBroker:
    """Classifies node commands and enforces JARVIS approval boundaries."""

    def classify(self, command: str) -> str:
        normalized = str(command or "").strip().lower()
        if normalized.startswith(_READONLY_PREFIXES):
            return "readonly"
        if normalized.startswith(_NOTIFY_PREFIXES):
            return "notification_ui"
        if normalized.startswith(_CONTROL_PREFIXES):
            return "control_plane"
        if normalized.startswith(_EXEC_PREFIXES):
            return "exec_like"
        return "unknown"

    def broker(
        self,
        *,
        command: str,
        payload: dict[str, Any] | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        capability = self.classify(command)
        if capability == "exec_like":
            return {
                "allowed": False,
                "capability": capability,
                "action_class": ActionClass.P3.value,
                "requires_approval": True,
                "reason": "exec_like_commands_must_route_through_plan_and_approval",
                "actor": actor,
                "command": command,
                "payload": dict(payload or {}),
            }
        if capability == "unknown":
            return {
                "allowed": False,
                "capability": capability,
                "action_class": ActionClass.P2.value,
                "requires_approval": True,
                "reason": "unknown_node_capability_requires_manual_review",
                "actor": actor,
                "command": command,
                "payload": dict(payload or {}),
            }
        action_class = ActionClass.P0.value if capability in {"readonly", "notification_ui"} else ActionClass.P1.value
        return {
            "allowed": True,
            "capability": capability,
            "action_class": action_class,
            "requires_approval": False,
            "reason": "capability_allowed_within_presence_boundary",
            "actor": actor,
            "command": command,
            "payload": dict(payload or {}),
        }
