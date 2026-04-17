from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class OpenClawWsBridgeTests(unittest.TestCase):
    def test_gateway_event_normalization_and_presence_updates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.pair_presence_node(
                    node_id="node-1",
                    device_id="phone-1",
                    owner_id="owner-1",
                    gateway_token_ref="env:OPENCLAW_GATEWAY_TOKEN",
                    node_token_ref="env:OPENCLAW_NODE_TOKEN_1",
                    actor="test",
                )

                result = runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-1",
                        "type": "node.connected",
                        "payload": {"node_id": "node-1", "session_id": "sess-1"},
                    }
                )
                self.assertTrue(result.get("ok"))
                signal = result.get("signal") or {}
                self.assertEqual(signal.get("schema_version"), "jarvis.signal.v1")
                self.assertEqual(signal.get("kind"), "context.update")
                self.assertEqual((signal.get("provenance") or {}).get("provider"), "openclaw")

                ingest = result.get("ingest") or {}
                self.assertTrue(ingest.get("accepted"))
                self.assertFalse(ingest.get("duplicate"))

                health = runtime.get_presence_health()
                self.assertTrue((health.get("bridge") or {}).get("connected"))
                self.assertEqual((health.get("bridge") or {}).get("last_event_type"), "node.connected")

                nodes = runtime.list_presence_nodes(status="paired", limit=5)
                self.assertEqual(len(nodes), 1)
                self.assertEqual(nodes[0].get("node_id"), "node-1")
                self.assertIsNotNone(nodes[0].get("last_seen_at"))

                runtime.ingest_openclaw_gateway_event(
                    {
                        "event_id": "evt-2",
                        "type": "surface.session.ended",
                        "payload": {"session_id": "sess-1"},
                    }
                )
                health = runtime.get_presence_health()
                self.assertFalse((health.get("bridge") or {}).get("connected"))
                self.assertEqual((health.get("bridge") or {}).get("last_event_type"), "surface.session.ended")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
