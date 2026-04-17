from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class OpenClawEventRouterTests(unittest.TestCase):
    def test_router_updates_surface_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.pair_presence_node(
                    node_id="node-1",
                    device_id="phone-1",
                    owner_id="owner",
                    gateway_token_ref="env:OPENCLAW_GATEWAY_TOKEN",
                    node_token_ref="env:OPENCLAW_NODE_TOKEN_1",
                    actor="test",
                )
                started = runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-1",
                        "type": "surface.session.started",
                        "payload": {
                            "channel_id": "dm:owner",
                            "session_id": "sess-1",
                            "user_id": "owner",
                        },
                    }
                )
                self.assertTrue(started.get("ok"))
                route = started.get("route") or {}
                self.assertEqual(route.get("channel_type"), "dm")
                self.assertFalse(bool(route.get("continuity_reset_required")))

                active = runtime.list_surface_sessions(status="active", limit=10)
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0].get("session_id"), "sess-1")
                self.assertEqual(active[0].get("operator_identity"), "owner")

                pairing = runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-pair-1",
                        "type": "node.pair.pending",
                        "payload": {"node_id": "node-1"},
                    }
                )
                self.assertEqual((pairing.get("route") or {}).get("pairing_transition"), "pending")
                nodes = runtime.list_presence_nodes(status="all", limit=10)
                self.assertEqual((nodes[0].get("pairing_status") if nodes else None), "pending")

                runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-2",
                        "type": "surface.session.ended",
                        "payload": {
                            "channel_id": "dm:owner",
                            "session_id": "sess-1",
                        },
                    }
                )
                active = runtime.list_surface_sessions(status="active", limit=10)
                self.assertGreaterEqual(len(active), 1)
                all_sessions = runtime.list_surface_sessions(status="all", limit=10)
                dm_sessions = [item for item in all_sessions if item.get("surface_id") == "dm:owner"]
                self.assertEqual(len(dm_sessions), 1)
                self.assertEqual(dm_sessions[0].get("status"), "ended")

                voice_started = runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-voice-1",
                        "type": "talk.session.started",
                        "payload": {
                            "channel_id": "voice:owner",
                            "session_id": "voice-1",
                            "modality": "voice",
                            "user_id": "owner",
                        },
                    }
                )
                self.assertTrue(voice_started.get("ok"))
                self.assertEqual((voice_started.get("route") or {}).get("channel_type"), "voice")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
