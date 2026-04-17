from __future__ import annotations

import unittest

from jarvis.openclaw_protocol_profile import load_openclaw_protocol_profile


class OpenClawProtocolProfileTests(unittest.TestCase):
    def test_load_builtin_profile_and_render_frames(self) -> None:
        profile = load_openclaw_protocol_profile(profile_id="openclaw_gateway_v2026_04_2")
        self.assertEqual(profile.profile_id, "openclaw_gateway_v2026_04_2")
        self.assertTrue(profile.gateway_version)
        self.assertTrue(profile.require_connect_ack)

        connect = profile.render_connect(owner_id="owner", client="jarvis", timestamp_iso="2026-04-12T00:00:00+00:00")
        self.assertEqual((connect or {}).get("type"), "req")
        self.assertEqual((connect or {}).get("method"), "connect")
        params = (connect or {}).get("params") if isinstance((connect or {}).get("params"), dict) else {}
        self.assertEqual(str(params.get("role") or ""), "operator")
        self.assertEqual(int(params.get("minProtocol") or 0), 3)

        attach = profile.render_attach(owner_id="owner", client="jarvis", timestamp_iso="2026-04-12T00:00:00+00:00")
        self.assertEqual(attach, {})

        subs = profile.render_subscribe(
            owner_id="owner",
            client="jarvis",
            timestamp_iso="2026-04-12T00:00:00+00:00",
        )
        self.assertEqual(subs, ())

        hb = profile.render_heartbeat(owner_id="owner", client="jarvis", timestamp_iso="2026-04-12T00:00:05+00:00")
        self.assertEqual(hb, {})

    def test_pairing_transition_and_token_ref_extraction(self) -> None:
        profile = load_openclaw_protocol_profile(profile_id="openclaw_gateway_v2026_04_2")
        self.assertEqual(profile.pairing_transition("node.pair.pending"), "pending")
        self.assertEqual(profile.pairing_transition("node.pair.approved"), "approved")
        self.assertEqual(profile.pairing_transition("node.pair.revoked"), "revoked")
        self.assertEqual(profile.pairing_transition("node.pair.rotated"), "rotated")
        self.assertEqual(profile.connect_transition("gateway.connected"), "ack")
        self.assertEqual(profile.connect_transition("gateway.connect.rejected"), "reject")
        self.assertEqual(profile.connect_transition("surface.message.received"), "none")

        hint = profile.extract_token_ref_hint(
            {
                "type": "node.pair.approved",
                "payload": {
                    "node_id": "node-1",
                    "node_token_ref": "env:OPENCLAW_NODE_TOKEN_1",
                },
            }
        )
        self.assertEqual(hint, "env:OPENCLAW_NODE_TOKEN_1")


if __name__ == "__main__":
    unittest.main()
