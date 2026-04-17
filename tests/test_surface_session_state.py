from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.surface_session_state import SurfaceSessionStateStore


class SurfaceSessionStateStoreTests(unittest.TestCase):
    def test_touch_and_transition_session_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = SurfaceSessionStateStore(Path(td) / "jarvis.db")
            try:
                started = store.touch_event(
                    surface_id="dm:owner",
                    channel_type="dm",
                    session_id="sess-1",
                    operator_identity="owner",
                    paired_node_id="node-1",
                    relationship_mode="equal",
                    contract_hash="abc123",
                    status="active",
                    metadata={"event_type": "surface.session.started"},
                )
                self.assertEqual(started.get("status"), "active")
                self.assertEqual(started.get("channel_type"), "dm")

                ended = store.touch_event(
                    surface_id="dm:owner",
                    channel_type="dm",
                    session_id="sess-1",
                    status="ended",
                    metadata={"event_type": "surface.session.ended"},
                )
                self.assertEqual(ended.get("status"), "ended")
                self.assertEqual(ended.get("operator_identity"), "owner")

                active = store.list_sessions(status="active", limit=5)
                self.assertEqual(len(active), 0)

                all_sessions = store.list_sessions(status="all", limit=5)
                self.assertEqual(len(all_sessions), 1)
                self.assertEqual(all_sessions[0].get("session_key"), "dm:owner:sess-1")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
