from __future__ import annotations

import json
from typing import Any


_EVENT_KIND_MAP = {
    "gateway.connected": "context.update",
    "gateway.connect.accepted": "context.update",
    "gateway.connect.rejected": "operator.command",
    "gateway.auth.failed": "operator.command",
    "surface.message.received": "message.inbound",
    "surface.session.started": "context.update",
    "surface.session.ended": "context.update",
    "node.connected": "context.update",
    "node.disconnected": "context.update",
    "node.pair.requested": "operator.command",
    "node.pair.pending": "operator.command",
    "node.pair.approved": "operator.command",
    "node.pair.rotated": "operator.command",
    "node.pair.revoked": "operator.command",
    "node.pair.expired": "operator.command",
    "node.status.changed": "context.update",
    "node.notification.ack": "context.update",
}

_CHAT_EVENTS = {"surface.message.received"}


class OpenClawWsBridge:
    """Dumb transport adapter: OpenClaw event -> canonical JARVIS signal envelope."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def _event_type(self, event: dict[str, Any]) -> str:
        return (
            str(event.get("type") or event.get("event_type") or "")
            .strip()
            .lower()
        )

    def _source_kind(self, event_type: str) -> str:
        if event_type in _CHAT_EVENTS:
            return "chat"
        if event_type.startswith("node.") or event_type.startswith("surface."):
            return "system"
        return "system"

    def to_signal_envelope(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = self._event_type(event)
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = event.get("data") if isinstance(event.get("data"), dict) else {}
        source_id = str(
            event.get("id")
            or event.get("event_id")
            or payload.get("id")
            or payload.get("message_id")
            or event_type
            or "openclaw_event"
        )
        signal_kind = _EVENT_KIND_MAP.get(event_type, "context.update")
        normalized_payload = {
            "source_type": f"openclaw.{event_type.replace('.', '_')}" if event_type else "openclaw.unknown_event",
            "event_type": event_type,
            "event_payload": dict(payload),
            "node_id": str(payload.get("node_id") or event.get("node_id") or "").strip() or None,
            "session_id": str(payload.get("session_id") or event.get("session_id") or "").strip() or None,
            "channel_id": str(payload.get("channel_id") or event.get("channel_id") or "").strip() or None,
            "text": str(payload.get("text") or payload.get("body") or "").strip() or None,
        }
        return {
            "schema_version": "jarvis.signal.v1",
            "kind": signal_kind,
            "payload": normalized_payload,
            "provenance": {
                "source_kind": self._source_kind(event_type),
                "provider": "openclaw",
                "source_id": source_id,
                "trust": "untrusted",
                "redaction_level": "redacted",
            },
            "priority_hint": "normal",
        }

    def ingest_gateway_event(self, event: dict[str, Any]) -> dict[str, Any]:
        envelope = self.to_signal_envelope(dict(event or {}))
        event_type = self._event_type(event)
        payload = envelope.get("payload") or {}
        node_id = str(payload.get("node_id") or "").strip()
        if event_type == "node.connected" and node_id:
            self.runtime.mark_presence_node_seen(node_id=node_id, metadata_patch={"last_event_type": event_type})
            self.runtime.set_presence_bridge_status(
                connection_status="connected",
                connected=True,
                details={"last_node_id": node_id},
            )
        elif event_type == "node.disconnected":
            self.runtime.set_presence_bridge_status(
                connection_status="degraded",
                connected=False,
                details={"last_node_id": node_id, "last_event_type": event_type},
            )
        elif event_type == "surface.session.ended":
            self.runtime.set_presence_bridge_status(
                connection_status="idle",
                connected=False,
                details={"last_event_type": event_type},
            )
        result = self.runtime.ingest_signal(envelope, auth_context="openclaw_gateway_ws")
        self.runtime.record_presence_gateway_event(event_type=event_type or "unknown", details={"source_id": envelope["provenance"]["source_id"]})
        return {
            "ok": True,
            "event_type": event_type,
            "signal": envelope,
            "ingest": result,
        }

    def serialize_event(self, event: dict[str, Any]) -> str:
        return json.dumps(dict(event or {}), sort_keys=True)
