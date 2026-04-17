from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import socket
import ssl
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .openclaw_bridge import _is_private_or_loopback_host
from .openclaw_protocol_profile import (
    OpenClawProtocolProfile,
    OpenClawProtocolProfileError,
    load_openclaw_protocol_profile,
)
from .secref_nodes import SecretRefError, parse_secret_ref, resolve_secret_ref


_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class OpenClawGatewayClientError(RuntimeError):
    pass


class GatewaySession(Protocol):
    def send_json(self, payload: dict[str, Any]) -> None: ...
    def recv_json(self, *, timeout_seconds: float = 0.0) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


class GatewayTransport(Protocol):
    def connect(
        self,
        *,
        ws_url: str,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
    ) -> GatewaySession: ...


def _safe_now() -> float:
    return time.time()


def _is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout))


@dataclass(frozen=True)
class OpenClawGatewayConfig:
    ws_url: str
    token_ref: str
    owner_id: str = "primary_operator"
    client_name: str = "jarvis"
    protocol_profile_id: str = "openclaw_gateway_v2026_04_2"
    protocol_profile_path: str | None = None
    allow_remote: bool = False
    connect_timeout_seconds: float = 8.0
    heartbeat_interval_seconds: float = 20.0
    min_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    subscribe_payloads: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class StdlibWebSocketSession:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.sock.settimeout(1.0)

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(0x1, json.dumps(dict(payload or {}), separators=(",", ":")).encode("utf-8"))

    def recv_json(self, *, timeout_seconds: float = 0.0) -> dict[str, Any] | None:
        if timeout_seconds <= 0:
            self.sock.settimeout(0.001)
        else:
            self.sock.settimeout(float(timeout_seconds))
        opcode, payload = self._recv_frame()
        if opcode == 0x8:
            raise ConnectionError("websocket closed by remote peer")
        if opcode == 0x9:
            # Ping -> pong.
            self._send_frame(0xA, payload)
            return None
        if opcode in {0xA, 0x0}:
            return None
        if opcode != 0x1:
            return None
        text = payload.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"type": "openclaw.raw", "payload": {"value": parsed}}

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        length = len(payload)
        first = bytes([0x80 | (opcode & 0x0F)])
        if length <= 125:
            header = bytes([0x80 | length])
        elif length <= 0xFFFF:
            header = bytes([0x80 | 126]) + length.to_bytes(2, "big")
        else:
            header = bytes([0x80 | 127]) + length.to_bytes(8, "big")
        mask = os.urandom(4)
        masked = bytes(payload[idx] ^ mask[idx % 4] for idx in range(length))
        self.sock.sendall(first + header + mask + masked)

    def _recv_exact(self, n_bytes: int) -> bytes:
        data = bytearray()
        while len(data) < n_bytes:
            chunk = self.sock.recv(n_bytes - len(data))
            if not chunk:
                raise ConnectionError("socket closed while reading websocket frame")
            data.extend(chunk)
        return bytes(data)

    def _recv_frame(self) -> tuple[int, bytes]:
        header = self._recv_exact(2)
        first = header[0]
        second = header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(self._recv_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._recv_exact(8), "big")
        mask_key = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(payload[idx] ^ mask_key[idx % 4] for idx in range(len(payload)))
        if not fin:
            # Fragmented frames are not expected for gateway event payloads.
            raise OpenClawGatewayClientError("fragmented websocket frames are not supported.")
        return opcode, payload


class StdlibWebSocketTransport:
    def connect(
        self,
        *,
        ws_url: str,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
    ) -> GatewaySession:
        parsed = urllib.parse.urlparse(str(ws_url or "").strip())
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise OpenClawGatewayClientError("ws_url must be a valid ws(s) URL.")
        host = str(parsed.hostname)
        port = int(parsed.port or (443 if parsed.scheme == "wss" else 80))
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        sock = socket.create_connection((host, port), timeout=float(timeout_seconds))
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req_headers: dict[str, str] = {
            "Host": f"{host}:{port}",
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": key,
            "Sec-WebSocket-Version": "13",
            "User-Agent": "JARVIS/0.1",
        }
        for header_key, header_value in (headers or {}).items():
            if str(header_key).strip() and str(header_value).strip():
                req_headers[str(header_key).strip()] = str(header_value).strip()

        request_lines = [f"GET {path} HTTP/1.1"]
        request_lines.extend(f"{key}: {value}" for key, value in req_headers.items())
        request_lines.append("")
        request_lines.append("")
        sock.sendall("\r\n".join(request_lines).encode("utf-8"))
        raw = self._read_http_headers(sock, timeout=float(timeout_seconds))
        status_line, response_headers = self._parse_http_headers(raw)
        if " 101 " not in status_line:
            raise OpenClawGatewayClientError(f"websocket handshake failed: {status_line}")
        accept = response_headers.get("sec-websocket-accept")
        expected = base64.b64encode(hashlib.sha1(f"{key}{_WS_GUID}".encode("utf-8")).digest()).decode("ascii")
        if accept != expected:
            raise OpenClawGatewayClientError("websocket handshake accept mismatch.")
        return StdlibWebSocketSession(sock)

    def _read_http_headers(self, sock: socket.socket, *, timeout: float) -> bytes:
        sock.settimeout(max(0.1, timeout))
        data = bytearray()
        marker = b"\r\n\r\n"
        while marker not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 65536:
                raise OpenClawGatewayClientError("handshake response too large.")
        if marker not in data:
            raise OpenClawGatewayClientError("incomplete websocket handshake response.")
        return bytes(data[: data.index(marker) + len(marker)])

    def _parse_http_headers(self, raw: bytes) -> tuple[str, dict[str, str]]:
        lines = raw.decode("utf-8", errors="replace").split("\r\n")
        status = lines[0] if lines else "HTTP/1.1 000"
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return status, headers


class OpenClawGatewayClient:
    """Persistent stateful gateway loop with reconnect/backoff and event routing hooks."""

    def __init__(
        self,
        *,
        config: OpenClawGatewayConfig,
        route_event: Callable[[dict[str, Any]], dict[str, Any]],
        on_state: Callable[[str, dict[str, Any]], None] | None = None,
        transport: GatewayTransport | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        parsed = urllib.parse.urlparse(str(config.ws_url or "").strip())
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise OpenClawGatewayClientError("ws_url must be an absolute ws(s) URL.")
        host = str(parsed.hostname or "").strip()
        if not config.allow_remote and not _is_private_or_loopback_host(host):
            raise OpenClawGatewayClientError("gateway ws host must be private/loopback unless allow_remote=True.")
        try:
            ref = parse_secret_ref(config.token_ref)
        except SecretRefError as exc:
            raise OpenClawGatewayClientError(str(exc)) from exc
        if ref.kind not in {"env", "file"}:
            raise OpenClawGatewayClientError("token_ref must be a SecretRef (env: or file:).")
        try:
            profile = load_openclaw_protocol_profile(
                profile_id=config.protocol_profile_id,
                profile_path=config.protocol_profile_path,
            )
        except OpenClawProtocolProfileError as exc:
            raise OpenClawGatewayClientError(str(exc)) from exc

        self.config = config
        self.protocol_profile: OpenClawProtocolProfile = profile
        self.route_event = route_event
        self.on_state = on_state
        self.transport = transport or StdlibWebSocketTransport()
        self.now_fn = now_fn or _safe_now
        self._session: GatewaySession | None = None
        self._running = False
        self._connect_request_id: str | None = None
        self._next_connect_at = 0.0
        self._backoff_seconds = max(0.5, float(config.min_backoff_seconds))
        self._last_heartbeat_at = 0.0
        self._stats: dict[str, Any] = {
            "running": False,
            "connected": False,
            "ws_url": config.ws_url,
            "owner_id": config.owner_id,
            "client_name": config.client_name,
            "protocol_profile_id": self.protocol_profile.profile_id,
            "protocol_gateway_version": self.protocol_profile.gateway_version,
            "protocol_source": self.protocol_profile.source,
            "last_connect_at": None,
            "last_disconnect_at": None,
            "last_message_at": None,
            "last_error": None,
            "reconnect_attempts": 0,
            "frames_received": 0,
            "events_routed": 0,
            "connect_handshake_required": bool(self.protocol_profile.require_connect_ack),
            "connect_handshake_state": "not_required"
            if not self.protocol_profile.require_connect_ack
            else "pending",
            "connect_handshake_sent_at": None,
            "connect_handshake_acked_at": None,
            "connect_handshake_ack_event_type": None,
            "connect_request_id": None,
            "pairing_state": "unknown",
            "commands_enabled": False,
            "last_pairing_event_type": None,
            "last_pairing_event_at": None,
            "paired_node_id": None,
            "last_token_ref_hint": None,
        }

    def start(self) -> dict[str, Any]:
        self._running = True
        self._stats["running"] = True
        self._next_connect_at = 0.0
        self._emit_state("starting")
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        self._running = False
        self._stats["running"] = False
        self._disconnect(reason="stopped")
        self._emit_state("stopped")
        return self.snapshot()

    def tick(self, *, max_messages: int = 50) -> dict[str, Any]:
        now = self.now_fn()
        if not self._running:
            return self.snapshot()

        if self._session is None:
            if now < self._next_connect_at:
                return self.snapshot()
            self._attempt_connect(now=now)
            if self._session is None:
                return self.snapshot()

        messages_processed = 0
        while messages_processed < max(1, int(max_messages)):
            try:
                incoming = self._session.recv_json(timeout_seconds=0.0)
            except Exception as exc:
                if _is_timeout_error(exc):
                    break
                self._stats["last_error"] = str(exc)
                self._disconnect(reason="recv_error")
                self._schedule_backoff(now=self.now_fn())
                break
            if incoming is None:
                break
            self._stats["frames_received"] = int(self._stats["frames_received"] or 0) + 1
            self._stats["last_message_at"] = self._iso_now()
            try:
                raw_incoming = dict(incoming or {})
                normalized_incoming = self._normalize_incoming_event(raw_incoming)
                if self._apply_connect_handshake(raw_incoming, normalized_incoming):
                    self._disconnect(reason="connect_rejected")
                    self._schedule_backoff(now=self.now_fn())
                    break
                if normalized_incoming is None:
                    messages_processed += 1
                    continue
                self._apply_pairing_state(normalized_incoming)
                routed = self.route_event(normalized_incoming)
                if isinstance(routed, dict):
                    self._stats["events_routed"] = int(self._stats["events_routed"] or 0) + 1
            except Exception as exc:
                self._stats["last_error"] = f"route_error: {exc}"
            messages_processed += 1

        if self._session is not None:
            elapsed = now - self._last_heartbeat_at
            if elapsed >= max(1.0, float(self.config.heartbeat_interval_seconds)):
                try:
                    heartbeat_payload = self.protocol_profile.render_heartbeat(
                        owner_id=self.config.owner_id,
                        client=self.config.client_name,
                        timestamp_iso=self._iso_now(),
                    )
                    if heartbeat_payload:
                        self._assign_request_id_if_needed(heartbeat_payload, prefix="heartbeat")
                        self._session.send_json(heartbeat_payload)
                    self._last_heartbeat_at = now
                except Exception as exc:
                    self._stats["last_error"] = f"heartbeat_error: {exc}"
                    self._disconnect(reason="heartbeat_error")
                    self._schedule_backoff(now=self.now_fn())

        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return dict(self._stats)

    def _attempt_connect(self, *, now: float) -> None:
        token = self._resolve_token()
        try:
            session = self.transport.connect(
                ws_url=self.config.ws_url,
                timeout_seconds=float(self.config.connect_timeout_seconds),
                headers={"Authorization": f"Bearer {token}"},
            )
            self._session = session
            self._stats["connected"] = True
            self._stats["last_connect_at"] = self._iso_now()
            self._stats["last_error"] = None
            self._stats["connect_handshake_required"] = bool(self.protocol_profile.require_connect_ack)
            self._stats["connect_handshake_state"] = (
                "pending" if self.protocol_profile.require_connect_ack else "not_required"
            )
            self._stats["connect_handshake_sent_at"] = None
            self._stats["connect_handshake_acked_at"] = None
            self._stats["connect_handshake_ack_event_type"] = None
            self._stats["connect_request_id"] = None
            self._connect_request_id = None
            self._stats["commands_enabled"] = self._commands_ready()
            self._backoff_seconds = max(0.5, float(self.config.min_backoff_seconds))
            self._last_heartbeat_at = now
            connect_frame = self.protocol_profile.render_connect(
                owner_id=self.config.owner_id,
                client=self.config.client_name,
                timestamp_iso=self._iso_now(),
            )
            if connect_frame:
                connect_frame = dict(connect_frame)
                self._inject_connect_auth_token(connect_frame, token=token)
                self._assign_request_id_if_needed(connect_frame, prefix="connect")
                session.send_json(connect_frame)
                self._stats["connect_handshake_sent_at"] = self._iso_now()
                if (
                    str(connect_frame.get("type") or "").strip().lower() == "req"
                    and str(connect_frame.get("method") or "").strip().lower() == "connect"
                ):
                    self._connect_request_id = str(connect_frame.get("id") or "").strip() or None
                    self._stats["connect_request_id"] = self._connect_request_id
            elif self.protocol_profile.require_connect_ack:
                raise OpenClawGatewayClientError(
                    "protocol profile requires connect ack but connect template is missing."
                )
            attach_frame = self.protocol_profile.render_attach(
                owner_id=self.config.owner_id,
                client=self.config.client_name,
                timestamp_iso=self._iso_now(),
            )
            if attach_frame:
                attach_frame = dict(attach_frame)
                self._assign_request_id_if_needed(attach_frame, prefix="attach")
                session.send_json(attach_frame)
            for payload in self.protocol_profile.render_subscribe(
                owner_id=self.config.owner_id,
                client=self.config.client_name,
                timestamp_iso=self._iso_now(),
                extra_payloads=self.config.subscribe_payloads,
            ):
                if not payload:
                    continue
                outbound = dict(payload)
                self._assign_request_id_if_needed(outbound, prefix="subscribe")
                session.send_json(outbound)
            self._emit_state("connected")
        except Exception as exc:
            self._stats["last_error"] = str(exc)
            self._stats["connected"] = False
            self._emit_state("connect_error")
            self._schedule_backoff(now=now)

    def _schedule_backoff(self, *, now: float) -> None:
        self._stats["reconnect_attempts"] = int(self._stats["reconnect_attempts"] or 0) + 1
        jitter = random.uniform(0.0, max(0.2, self._backoff_seconds * 0.3))
        self._next_connect_at = now + self._backoff_seconds + jitter
        self._backoff_seconds = min(
            max(float(self.config.min_backoff_seconds), self._backoff_seconds * 2.0),
            float(self.config.max_backoff_seconds),
        )

    def _disconnect(self, *, reason: str) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
        self._session = None
        self._connect_request_id = None
        self._stats["connected"] = False
        self._stats["commands_enabled"] = False
        self._stats["last_disconnect_at"] = self._iso_now()
        self._emit_state(reason)

    def _apply_connect_handshake(self, frame: dict[str, Any], event: dict[str, Any] | None) -> bool:
        frame_type = str(frame.get("type") or "").strip().lower()
        if frame_type == "res":
            response_id = str(frame.get("id") or "").strip()
            if response_id and response_id == str(self._connect_request_id or ""):
                if bool(frame.get("ok")):
                    self._stats["connect_handshake_state"] = "acked"
                    self._stats["connect_handshake_acked_at"] = self._iso_now()
                    self._stats["connect_handshake_ack_event_type"] = "connect.response.ok"
                    self._stats["commands_enabled"] = self._commands_ready()
                    self._emit_state("connect_handshake_acked")
                    return False
                error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
                code = str(error.get("code") or "").strip() or "invalid_request"
                message = str(error.get("message") or "").strip() or "connect rejected"
                self._stats["connect_handshake_state"] = "rejected"
                self._stats["connect_handshake_ack_event_type"] = "connect.response.error"
                self._stats["last_error"] = f"connect_handshake_rejected:{code}:{message}"
                self._stats["commands_enabled"] = False
                self._emit_state("connect_handshake_rejected")
                return True
            return False

        if event is None:
            return False
        event_type = self.protocol_profile.canonical_event_type(
            str(event.get("type") or event.get("event_type") or "")
        )
        transition = self.protocol_profile.connect_transition(event_type)
        if transition == "none":
            return False
        if transition == "ack":
            self._stats["connect_handshake_state"] = "acked"
            self._stats["connect_handshake_acked_at"] = self._iso_now()
            self._stats["connect_handshake_ack_event_type"] = event_type
            self._stats["commands_enabled"] = self._commands_ready()
            self._emit_state("connect_handshake_acked")
            return False
        self._stats["connect_handshake_state"] = "rejected"
        self._stats["connect_handshake_ack_event_type"] = event_type
        self._stats["last_error"] = f"connect_handshake_rejected:{event_type}"
        self._stats["commands_enabled"] = False
        self._emit_state("connect_handshake_rejected")
        return True

    def _apply_pairing_state(self, event: dict[str, Any]) -> None:
        event_type = self.protocol_profile.canonical_event_type(
            str(event.get("type") or event.get("event_type") or "")
        )
        transition = self.protocol_profile.pairing_transition(event_type)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if transition == "none" and event_type == "node.pair.resolved":
            decision = str(payload.get("decision") or payload.get("status") or "").strip().lower()
            if decision in {"approved", "allow", "allowed"}:
                transition = "approved"
            elif decision in {"rotated", "reissued"}:
                transition = "rotated"
            elif decision in {"rejected", "revoked", "denied", "expired"}:
                transition = "revoked"
        if transition == "none":
            return
        self._stats["last_pairing_event_type"] = event_type
        self._stats["last_pairing_event_at"] = self._iso_now()
        node_id = str(payload.get("node_id") or event.get("node_id") or "").strip() or None
        if node_id:
            self._stats["paired_node_id"] = node_id
        token_ref_hint = self.protocol_profile.extract_token_ref_hint(event)
        if token_ref_hint:
            self._stats["last_token_ref_hint"] = token_ref_hint
        if transition == "pending":
            self._stats["pairing_state"] = "pending"
            self._stats["commands_enabled"] = False
            self._emit_state("pairing_pending")
            return
        if transition == "approved":
            self._stats["pairing_state"] = "approved"
            self._stats["commands_enabled"] = self._commands_ready()
            self._emit_state("pairing_approved")
            return
        if transition == "revoked":
            self._stats["pairing_state"] = "revoked"
            self._stats["commands_enabled"] = False
            self._emit_state("pairing_revoked")
            return
        if transition == "rotated":
            if str(self._stats.get("pairing_state") or "") != "approved":
                self._stats["pairing_state"] = "approved"
            self._stats["commands_enabled"] = self._commands_ready()
            self._emit_state("pairing_rotated")

    def _commands_ready(self) -> bool:
        if not bool(self._stats.get("connected")):
            return False
        if self._requires_pairing() and str(self._stats.get("pairing_state") or "") != "approved":
            return False
        handshake_state = str(self._stats.get("connect_handshake_state") or "")
        if bool(self.protocol_profile.require_connect_ack):
            return handshake_state == "acked"
        return handshake_state in {"not_required", "acked", ""}

    def _resolve_token(self) -> str:
        ref = parse_secret_ref(self.config.token_ref)
        try:
            token = resolve_secret_ref(ref)
        except SecretRefError as exc:
            raise OpenClawGatewayClientError(str(exc)) from exc
        if not token:
            raise OpenClawGatewayClientError("resolved gateway token is empty.")
        return token

    def _emit_state(self, state: str) -> None:
        if self.on_state is None:
            return
        try:
            self.on_state(str(state), self.snapshot())
        except Exception:
            return

    def _iso_now(self) -> str:
        return datetime.fromtimestamp(self.now_fn(), tz=timezone.utc).isoformat()

    def _assign_request_id_if_needed(self, payload: dict[str, Any], *, prefix: str) -> None:
        if str(payload.get("type") or "").strip().lower() != "req":
            return
        if str(payload.get("id") or "").strip():
            return
        payload["id"] = f"jarvis-{prefix}-{uuid.uuid4().hex[:12]}"

    def _inject_connect_auth_token(self, payload: dict[str, Any], *, token: str) -> None:
        if str(payload.get("type") or "").strip().lower() != "req":
            return
        if str(payload.get("method") or "").strip().lower() != "connect":
            return
        params = payload.get("params") if isinstance(payload.get("params"), dict) else None
        if params is None:
            return
        auth = params.get("auth") if isinstance(params.get("auth"), dict) else {}
        if not str(auth.get("token") or "").strip():
            auth["token"] = str(token)
        params["auth"] = auth

    def _normalize_incoming_event(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        frame_type = str(frame.get("type") or "").strip().lower()
        if frame_type == "event":
            event_type = str(frame.get("event") or "").strip().lower()
            if not event_type:
                return None
            payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
            event: dict[str, Any] = {
                "type": event_type,
                "event_type": event_type,
                "payload": dict(payload),
            }
            if frame.get("seq") is not None:
                event["seq"] = frame.get("seq")
            state_version = frame.get("stateVersion")
            if isinstance(state_version, dict):
                event["state_version"] = dict(state_version)
            return self.protocol_profile.normalize_incoming_event(event)
        if frame_type in {"req", "res"}:
            return None
        return self.protocol_profile.normalize_incoming_event(dict(frame))

    def _requires_pairing(self) -> bool:
        connect_template = self.protocol_profile.connect_template or {}
        frame_type = str(connect_template.get("type") or "").strip().lower()
        if frame_type == "req":
            params = connect_template.get("params") if isinstance(connect_template.get("params"), dict) else {}
            role = str(params.get("role") or "").strip().lower()
            if role:
                return role == "node"
            return False
        role = str(connect_template.get("role") or "").strip().lower()
        if role:
            return role == "node"
        return True
