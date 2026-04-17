from __future__ import annotations

import os
import unittest
from collections import deque
from typing import Any

from jarvis.openclaw_gateway_client import OpenClawGatewayClient, OpenClawGatewayConfig


class _FakeSession:
    def __init__(self, messages: list[dict[str, Any]], *, error_on_empty: Exception | None = None) -> None:
        self.messages = deque(messages)
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.error_on_empty = error_on_empty

    def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(dict(payload))

    def recv_json(self, *, timeout_seconds: float = 0.0) -> dict[str, Any] | None:
        if not self.messages:
            if self.error_on_empty is None:
                raise TimeoutError("no messages")
            raise self.error_on_empty
        return self.messages.popleft()

    def close(self) -> None:
        self.closed = True


class _FakeTransport:
    def __init__(self, sessions: list[_FakeSession]) -> None:
        self.sessions = deque(sessions)
        self.calls = 0

    def connect(self, *, ws_url: str, timeout_seconds: float, headers: dict[str, str] | None = None) -> _FakeSession:
        self.calls += 1
        if not self.sessions:
            raise ConnectionError("no fake sessions available")
        return self.sessions.popleft()


class OpenClawGatewayClientTests(unittest.TestCase):
    def test_connect_tick_pairing_lifecycle_and_stop(self) -> None:
        env_name = "OPENCLAW_TEST_GATEWAY_TOKEN"
        os.environ[env_name] = "token-123"
        routed: list[dict[str, Any]] = []
        states: list[str] = []
        fake_session = _FakeSession(
            [
                {"type": "gateway.connected", "payload": {"owner_id": "owner"}},
                {"type": "node.pair.pending", "payload": {"node_id": "node-1"}},
                {"type": "node.pair.approved", "payload": {"node_id": "node-1"}},
                {"type": "surface.message.received", "payload": {"text": "hello"}},
                {"type": "node.pair.revoked", "payload": {"node_id": "node-1"}},
            ]
        )
        transport = _FakeTransport([fake_session])
        client = OpenClawGatewayClient(
            config=OpenClawGatewayConfig(
                ws_url="ws://127.0.0.1:3333/gateway",
                token_ref=f"env:{env_name}",
                owner_id="owner",
                heartbeat_interval_seconds=60.0,
            ),
            route_event=lambda event: routed.append(dict(event)) or {"ok": True},
            on_state=lambda state, snapshot: states.append(str(state)),
            transport=transport,
        )
        status = client.start()
        self.assertTrue(status.get("running"))

        after_tick = client.tick(max_messages=10)
        self.assertTrue(after_tick.get("connected"))
        self.assertEqual(int(after_tick.get("events_routed") or 0), 5)
        self.assertEqual(len(routed), 5)
        self.assertEqual(str(after_tick.get("connect_handshake_state")), "acked")
        self.assertEqual(str(after_tick.get("pairing_state")), "revoked")
        self.assertFalse(bool(after_tick.get("commands_enabled")))
        self.assertEqual(str(after_tick.get("protocol_profile_id")), "openclaw_gateway_v2026_04_2")
        self.assertGreaterEqual(len(fake_session.sent), 1)
        self.assertEqual(str(fake_session.sent[0].get("type")), "req")
        self.assertEqual(str(fake_session.sent[0].get("method")), "connect")
        self.assertTrue(str(fake_session.sent[0].get("id") or "").strip())

        stopped = client.stop()
        self.assertFalse(stopped.get("running"))
        self.assertFalse(stopped.get("connected"))
        self.assertTrue(fake_session.closed)
        self.assertIn("connected", states)
        self.assertIn("pairing_approved", states)

    def test_reconnect_after_disconnect_uses_backoff_and_new_session(self) -> None:
        env_name = "OPENCLAW_TEST_GATEWAY_TOKEN_2"
        os.environ[env_name] = "token-abc"
        routed: list[dict[str, Any]] = []
        clock = {"t": 0.0}

        def now_fn() -> float:
            return float(clock["t"])

        first_session = _FakeSession(
            [{"type": "node.connected", "payload": {"node_id": "node-1"}}],
            error_on_empty=ConnectionError("socket dropped"),
        )
        second_session = _FakeSession(
            [{"type": "surface.message.received", "payload": {"text": "after reconnect"}}],
            error_on_empty=None,
        )
        transport = _FakeTransport([first_session, second_session])
        client = OpenClawGatewayClient(
            config=OpenClawGatewayConfig(
                ws_url="ws://127.0.0.1:4444/gateway",
                token_ref=f"env:{env_name}",
                owner_id="owner",
                heartbeat_interval_seconds=60.0,
                min_backoff_seconds=0.5,
                max_backoff_seconds=0.5,
            ),
            route_event=lambda event: routed.append(dict(event)) or {"ok": True},
            transport=transport,
            now_fn=now_fn,
        )
        client.start()
        first = client.tick(max_messages=10)
        self.assertFalse(first.get("connected"))
        self.assertGreaterEqual(int(first.get("reconnect_attempts") or 0), 1)

        # Advance beyond backoff window to trigger reconnect.
        clock["t"] = clock["t"] + 2.0
        second = client.tick(max_messages=10)
        self.assertTrue(second.get("connected"))
        self.assertGreaterEqual(int(second.get("reconnect_attempts") or 0), 1)
        self.assertGreaterEqual(transport.calls, 2)
        self.assertGreaterEqual(len(routed), 2)
        self.assertEqual(str(second.get("connect_handshake_state")), "pending")
        self.assertFalse(bool(second.get("commands_enabled")))

    def test_connect_ack_from_response_frame(self) -> None:
        env_name = "OPENCLAW_TEST_GATEWAY_TOKEN_3"
        os.environ[env_name] = "token-live"
        fake_session = _FakeSession(
            [
                {"type": "res", "id": "connect-ack", "ok": True, "payload": {"type": "hello-ok"}},
                {"type": "surface.message.received", "payload": {"text": "hello"}},
            ]
        )
        transport = _FakeTransport([fake_session])
        client = OpenClawGatewayClient(
            config=OpenClawGatewayConfig(
                ws_url="ws://127.0.0.1:5555/gateway",
                token_ref=f"env:{env_name}",
                owner_id="owner",
                heartbeat_interval_seconds=60.0,
                protocol_profile_path=None,
            ),
            route_event=lambda event: {"ok": bool(event)},
            transport=transport,
        )
        client.start()
        # Force deterministic request-id in this test so fake response can match.
        fake_session.sent.clear()
        client.protocol_profile.connect_template["id"] = "connect-ack"  # type: ignore[index]
        after_tick = client.tick(max_messages=10)
        self.assertTrue(after_tick.get("connected"))
        self.assertEqual(str(after_tick.get("connect_handshake_state")), "acked")
        self.assertEqual(str(after_tick.get("connect_handshake_ack_event_type")), "connect.response.ok")


if __name__ == "__main__":
    unittest.main()
