from __future__ import annotations

import hashlib
import json
from typing import Any

from .surface_session_state import SurfaceSessionStateStore


class OpenClawEventRouter:
    """Routes OpenClaw gateway events into canonical signal ingest + session continuity."""

    def __init__(
        self,
        *,
        runtime: Any,
        bridge: Any,
        session_state: SurfaceSessionStateStore,
    ) -> None:
        self.runtime = runtime
        self.bridge = bridge
        self.session_state = session_state

    def _channel_type(self, event_type: str, payload: dict[str, Any]) -> str:
        channel_id = str(payload.get("channel_id") or "").strip()
        node_id = str(payload.get("node_id") or "").strip()
        modality = str(payload.get("modality") or payload.get("interaction_modality") or "").strip().lower()
        normalized_event_type = str(event_type or "").strip().lower()
        if (
            modality in {"voice", "speech", "talk"}
            or normalized_event_type.startswith("talk.")
            or normalized_event_type.startswith("voice.")
            or channel_id.startswith("talk:")
            or channel_id.startswith("voice:")
        ):
            return "voice"
        if event_type.startswith("node.") or node_id:
            return "node"
        if channel_id.startswith("dm:"):
            return "dm"
        if channel_id:
            return "channel"
        return "surface"

    def _surface_id(self, payload: dict[str, Any]) -> str:
        return (
            str(payload.get("channel_id") or "").strip()
            or str(payload.get("surface_id") or "").strip()
            or str(payload.get("node_id") or "").strip()
            or "openclaw"
        )

    def _session_id(self, payload: dict[str, Any], source_id: str) -> str:
        return (
            str(payload.get("session_id") or "").strip()
            or str(payload.get("thread_id") or "").strip()
            or str(payload.get("conversation_id") or "").strip()
            or str(source_id).strip()
            or "default"
        )

    def _operator_identity(self, raw_event: dict[str, Any], payload: dict[str, Any]) -> str | None:
        raw_payload = raw_event.get("payload") if isinstance(raw_event.get("payload"), dict) else {}
        raw_data = raw_event.get("data") if isinstance(raw_event.get("data"), dict) else {}
        for key in ("operator_id", "owner_id", "user_id", "author_id", "sender_id"):
            value = str(
                payload.get(key)
                or raw_payload.get(key)
                or raw_data.get(key)
                or raw_event.get(key)
                or ""
            ).strip()
            if value:
                return value
        actor = raw_event.get("actor")
        if isinstance(actor, dict):
            for key in ("id", "user_id", "operator_id"):
                value = str(actor.get(key) or "").strip()
                if value:
                    return value
        return None

    def _contract_hash(self) -> str:
        if hasattr(self.runtime, "get_consciousness_contract_hash"):
            value = str(self.runtime.get_consciousness_contract_hash() or "").strip()
            if value:
                return value
        contract = self.runtime.get_consciousness_contract()
        encoded = json.dumps(contract, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def _user_model_revision(self) -> str | None:
        if hasattr(self.runtime, "get_user_model_revision"):
            value = str(self.runtime.get_user_model_revision() or "").strip()
            if value:
                return value
        artifact = self.runtime.get_latest_user_model_artifact() or self.runtime.get_user_model()
        if not isinstance(artifact, dict):
            return None
        encoded = json.dumps(artifact, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def _pushback_calibration_revision(self) -> str | None:
        if hasattr(self.runtime, "get_pushback_calibration_revision"):
            value = str(self.runtime.get_pushback_calibration_revision() or "").strip()
            if value:
                return value
        recent = self.runtime.list_pushback_calibration(limit=1)
        if not isinstance(recent, dict):
            return None
        for key in ("calibration_deltas", "reviews", "overrides", "pushbacks"):
            items = recent.get(key)
            if isinstance(items, list) and items:
                head = items[0]
                if isinstance(head, dict):
                    for id_key in ("delta_id", "review_id", "override_id", "pushback_id"):
                        value = str(head.get(id_key) or "").strip()
                        if value:
                            return value
        return None

    def _pairing_transition(self, event_type: str) -> str:
        normalized = str(event_type or "").strip().lower()
        if normalized in {"node.pair.requested", "node.pair.pending"}:
            return "pending"
        if normalized in {"node.pair.approved", "node.pair.confirmed", "node.pair.rotated", "node.pair.reissued"}:
            return "approved"
        if normalized in {"node.pair.revoked", "node.pair.expired", "node.pair.denied"}:
            return "revoked"
        return "none"

    def route_gateway_event(self, event: dict[str, Any]) -> dict[str, Any]:
        routed = self.bridge.ingest_gateway_event(dict(event or {}))
        signal = routed.get("signal") if isinstance(routed.get("signal"), dict) else {}
        payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else {}
        event_type = str(payload.get("event_type") or "").strip().lower()
        source_id = str((signal.get("provenance") or {}).get("source_id") or "").strip()
        channel_type = self._channel_type(event_type, payload)
        status = "ended" if event_type == "surface.session.ended" else "active"
        surface_id = self._surface_id(payload)
        session_id = self._session_id(payload, source_id)
        session_key = f"{surface_id}:{session_id}"

        latest_mode = self.runtime.get_presence_mode()
        latest_surfaces = self.runtime.get_consciousness_surfaces(include_content=False)
        revision = str(latest_surfaces.get("updated_at") or "").strip() or None
        contract_hash = self._contract_hash()
        previous = self.session_state.get(session_key) or {}
        previous_contract_hash = str(previous.get("last_seen_contract_hash") or "").strip() or None
        continuity_reset_required = bool(
            previous_contract_hash and contract_hash and previous_contract_hash != contract_hash
        )
        pairing_transition = self._pairing_transition(event_type)
        node_id = str(payload.get("node_id") or "").strip() or None
        token_ref_hint = str(payload.get("node_token_ref") or payload.get("token_ref") or "").strip() or None
        session_record = self.session_state.touch_event(
            surface_id=surface_id,
            channel_type=channel_type,
            session_id=session_id,
            operator_identity=self._operator_identity(event, payload),
            paired_node_id=node_id,
            relationship_mode=str(latest_mode.get("mode") or "").strip() or None,
            consciousness_revision=revision,
            contract_hash=contract_hash,
            status=status,
            metadata={
                "event_type": event_type or "unknown",
                "source_id": source_id or "unknown",
                "signal_kind": str(signal.get("kind") or "unknown"),
                "pairing_transition": pairing_transition,
                "user_model_revision": self._user_model_revision(),
                "pushback_calibration_revision": self._pushback_calibration_revision(),
                "previous_contract_hash": previous_contract_hash,
                "continuity_reset_required": continuity_reset_required,
            },
        )
        if pairing_transition != "none" and node_id:
            self.runtime.apply_gateway_pairing_event(
                node_id=node_id,
                pairing_status=pairing_transition,
                event_type=event_type or "unknown",
                token_ref_hint=token_ref_hint,
            )
        return {
            **routed,
            "route": {
                "channel_type": channel_type,
                "session_key": session_record.get("session_key"),
                "relationship_mode": session_record.get("last_relationship_mode"),
                "contract_hash": session_record.get("last_seen_contract_hash"),
                "previous_contract_hash": previous_contract_hash,
                "continuity_reset_required": continuity_reset_required,
                "pairing_transition": pairing_transition,
            },
            "surface_session": session_record,
        }
