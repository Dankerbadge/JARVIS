from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.device_tokens import DeviceTokenStore


class DeviceTokenStoreTests(unittest.TestCase):
    def test_pair_rotate_seen_revoke_flow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "jarvis.db"
            store = DeviceTokenStore(db_path)
            try:
                paired = store.upsert_pairing(
                    node_id="node-1",
                    device_id="phone-1",
                    owner_id="owner-1",
                    gateway_token_ref="env:OPENCLAW_GATEWAY_TOKEN",
                    node_token_ref="env:OPENCLAW_NODE_TOKEN_1",
                    metadata={"label": "Phone"},
                )
                self.assertEqual(paired.get("pairing_status"), "paired")

                seen = store.mark_seen(node_id="node-1", metadata_patch={"battery": "85%"})
                self.assertIsNotNone(seen)
                self.assertEqual((seen or {}).get("metadata", {}).get("battery"), "85%")
                self.assertIsNotNone((seen or {}).get("last_seen_at"))

                rotated = store.rotate_node_token_ref(
                    node_id="node-1",
                    node_token_ref="env:OPENCLAW_NODE_TOKEN_1_ROTATED",
                )
                self.assertEqual((rotated or {}).get("node_token_ref"), "env:OPENCLAW_NODE_TOKEN_1_ROTATED")
                self.assertIsNotNone((rotated or {}).get("rotated_at"))

                revoked = store.revoke_node(node_id="node-1", reason="test")
                self.assertEqual((revoked or {}).get("pairing_status"), "revoked")
                self.assertEqual((revoked or {}).get("metadata", {}).get("revocation_reason"), "test")

                paired_nodes = store.list_nodes(status="paired", limit=10)
                self.assertEqual(len(paired_nodes), 0)
                revoked_nodes = store.list_nodes(status="revoked", limit=10)
                self.assertEqual(len(revoked_nodes), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
