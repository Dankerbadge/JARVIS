from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OpenClawProtocolProfileError(ValueError):
    pass


def _deep_render(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {str(k): _deep_render(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_render(item, replacements) for item in value]
    if isinstance(value, str):
        rendered = value
        for key, replacement in replacements.items():
            rendered = rendered.replace("{" + key + "}", replacement)
        return rendered
    return value


def _dig(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in [segment for segment in str(path or "").split(".") if segment]:
        if not isinstance(node, dict):
            return None
        if part not in node:
            return None
        node = node.get(part)
    return node


@dataclass(frozen=True)
class OpenClawProtocolProfile:
    profile_id: str
    gateway_version: str
    source: str
    connect_template: dict[str, Any] | None
    attach_template: dict[str, Any]
    subscribe_templates: tuple[dict[str, Any], ...]
    heartbeat_template: dict[str, Any]
    require_connect_ack: bool
    connect_ack_events: tuple[str, ...]
    connect_reject_events: tuple[str, ...]
    known_events: tuple[str, ...]
    event_aliases: dict[str, str]
    pairing_pending_events: tuple[str, ...]
    pairing_approved_events: tuple[str, ...]
    pairing_revoked_events: tuple[str, ...]
    pairing_rotated_events: tuple[str, ...]
    token_ref_paths: tuple[str, ...]

    def canonical_event_type(self, raw_event_type: str) -> str:
        normalized = str(raw_event_type or "").strip().lower()
        return str(self.event_aliases.get(normalized, normalized))

    def normalize_incoming_event(self, event: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(event or {})
        raw_event_type = (
            str(normalized.get("type") or normalized.get("event_type") or "")
            .strip()
            .lower()
        )
        canonical = self.canonical_event_type(raw_event_type)
        if canonical:
            normalized["type"] = canonical
            normalized["event_type"] = canonical
        if raw_event_type and canonical and raw_event_type != canonical:
            metadata = normalized.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["original_event_type"] = raw_event_type
            normalized["metadata"] = metadata
        return normalized

    def render_connect(
        self,
        *,
        owner_id: str,
        client: str,
        timestamp_iso: str,
    ) -> dict[str, Any] | None:
        if not self.connect_template:
            return None
        rendered = _deep_render(
            self.connect_template,
            {
                "owner_id": str(owner_id or "").strip(),
                "client": str(client or "").strip(),
                "timestamp": str(timestamp_iso or "").strip(),
                "profile_id": self.profile_id,
                "gateway_version": self.gateway_version,
            },
        )
        return dict(rendered) if isinstance(rendered, dict) else None

    def render_attach(
        self,
        *,
        owner_id: str,
        client: str,
        timestamp_iso: str,
    ) -> dict[str, Any]:
        rendered = _deep_render(
            self.attach_template,
            {
                "owner_id": str(owner_id or "").strip(),
                "client": str(client or "").strip(),
                "timestamp": str(timestamp_iso or "").strip(),
                "profile_id": self.profile_id,
                "gateway_version": self.gateway_version,
            },
        )
        return dict(rendered) if isinstance(rendered, dict) else {}

    def render_subscribe(
        self,
        *,
        owner_id: str,
        client: str,
        timestamp_iso: str,
        extra_payloads: tuple[dict[str, Any], ...] = (),
    ) -> tuple[dict[str, Any], ...]:
        frames: list[dict[str, Any]] = []
        replacements = {
            "owner_id": str(owner_id or "").strip(),
            "client": str(client or "").strip(),
            "timestamp": str(timestamp_iso or "").strip(),
            "profile_id": self.profile_id,
            "gateway_version": self.gateway_version,
        }
        for template in self.subscribe_templates:
            rendered = _deep_render(template, replacements)
            if isinstance(rendered, dict):
                frames.append(rendered)
        for payload in extra_payloads:
            if isinstance(payload, dict):
                rendered = _deep_render(dict(payload), replacements)
                if isinstance(rendered, dict):
                    frames.append(rendered)
        return tuple(frames)

    def render_heartbeat(
        self,
        *,
        owner_id: str,
        client: str,
        timestamp_iso: str,
    ) -> dict[str, Any]:
        rendered = _deep_render(
            self.heartbeat_template,
            {
                "owner_id": str(owner_id or "").strip(),
                "client": str(client or "").strip(),
                "timestamp": str(timestamp_iso or "").strip(),
                "profile_id": self.profile_id,
                "gateway_version": self.gateway_version,
            },
        )
        return dict(rendered) if isinstance(rendered, dict) else {}

    def pairing_transition(self, event_type: str) -> str:
        normalized = self.canonical_event_type(event_type)
        if normalized in self.pairing_pending_events:
            return "pending"
        if normalized in self.pairing_approved_events:
            return "approved"
        if normalized in self.pairing_revoked_events:
            return "revoked"
        if normalized in self.pairing_rotated_events:
            return "rotated"
        return "none"

    def connect_transition(self, event_type: str) -> str:
        normalized = self.canonical_event_type(event_type)
        if normalized in self.connect_ack_events:
            return "ack"
        if normalized in self.connect_reject_events:
            return "reject"
        return "none"

    def extract_token_ref_hint(self, event: dict[str, Any]) -> str | None:
        normalized = self.normalize_incoming_event(event)
        for path in self.token_ref_paths:
            candidate = _dig(normalized, path)
            text = str(candidate or "").strip()
            if text.startswith("env:") or text.startswith("file:"):
                return text
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "gateway_version": self.gateway_version,
            "source": self.source,
            "require_connect_ack": self.require_connect_ack,
            "connect_ack_events": list(self.connect_ack_events),
            "connect_reject_events": list(self.connect_reject_events),
            "known_events": list(self.known_events),
            "pairing_pending_events": list(self.pairing_pending_events),
            "pairing_approved_events": list(self.pairing_approved_events),
            "pairing_revoked_events": list(self.pairing_revoked_events),
            "pairing_rotated_events": list(self.pairing_rotated_events),
            "token_ref_paths": list(self.token_ref_paths),
        }


def _builtin_profile_path(profile_id: str) -> Path:
    base = Path(__file__).resolve().parent / "protocol"
    return base / f"{profile_id}.json"


def load_openclaw_protocol_profile(
    *,
    profile_id: str | None = None,
    profile_path: str | Path | None = None,
) -> OpenClawProtocolProfile:
    chosen_id = str(profile_id or "openclaw_gateway_v2026_04_2").strip()
    path = Path(profile_path).expanduser().resolve() if profile_path else _builtin_profile_path(chosen_id)
    if not path.exists() or not path.is_file():
        raise OpenClawProtocolProfileError(f"protocol profile not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise OpenClawProtocolProfileError("protocol profile must be a JSON object.")

    wire = raw.get("wire")
    pairing = raw.get("pairing")
    events = raw.get("events")
    handshake = raw.get("handshake")
    if (
        not isinstance(wire, dict)
        or not isinstance(pairing, dict)
        or not isinstance(events, dict)
    ):
        raise OpenClawProtocolProfileError(
            "protocol profile missing required wire/pairing/events sections."
        )
    if handshake is not None and not isinstance(handshake, dict):
        raise OpenClawProtocolProfileError("protocol profile handshake section must be an object when present.")

    def _as_tuple(values: Any) -> tuple[str, ...]:
        if not isinstance(values, list):
            return ()
        out: list[str] = []
        for item in values:
            text = str(item or "").strip().lower()
            if text:
                out.append(text)
        return tuple(out)

    aliases_raw = events.get("aliases")
    aliases: dict[str, str] = {}
    if isinstance(aliases_raw, dict):
        for key, value in aliases_raw.items():
            k = str(key or "").strip().lower()
            v = str(value or "").strip().lower()
            if k and v:
                aliases[k] = v

    connect_template = wire.get("connect_template")
    attach_template = wire.get("attach_template")
    subscribe_templates = wire.get("subscribe_templates")
    heartbeat_template = wire.get("heartbeat_template")
    if connect_template is not None and not isinstance(connect_template, dict):
        raise OpenClawProtocolProfileError("wire.connect_template must be an object when present.")
    if attach_template is None:
        attach_template = {}
    if not isinstance(attach_template, dict) or not isinstance(heartbeat_template, dict):
        raise OpenClawProtocolProfileError(
            "wire.attach_template and wire.heartbeat_template must be objects."
        )
    if not isinstance(subscribe_templates, list):
        subscribe_templates = []
    require_connect_ack = bool((handshake or {}).get("require_connect_ack"))

    return OpenClawProtocolProfile(
        profile_id=str(raw.get("profile_id") or chosen_id),
        gateway_version=str(raw.get("gateway_version") or "unknown"),
        source=str(raw.get("source") or ""),
        connect_template=dict(connect_template) if isinstance(connect_template, dict) else None,
        attach_template=dict(attach_template),
        subscribe_templates=tuple(
            dict(item) for item in subscribe_templates if isinstance(item, dict)
        ),
        heartbeat_template=dict(heartbeat_template),
        require_connect_ack=require_connect_ack,
        connect_ack_events=_as_tuple((handshake or {}).get("ack_events")),
        connect_reject_events=_as_tuple((handshake or {}).get("reject_events")),
        known_events=_as_tuple(events.get("known")),
        event_aliases=aliases,
        pairing_pending_events=_as_tuple(pairing.get("pending_events")),
        pairing_approved_events=_as_tuple(pairing.get("approved_events")),
        pairing_revoked_events=_as_tuple(pairing.get("revoked_events")),
        pairing_rotated_events=_as_tuple(pairing.get("rotated_events")),
        token_ref_paths=tuple(
            str(item).strip()
            for item in (pairing.get("token_ref_paths") or [])
            if str(item).strip()
        ),
    )
