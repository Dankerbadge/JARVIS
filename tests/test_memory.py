from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.memory import MemoryStore, ProvenanceError
from jarvis.models import new_id


class MemoryTests(unittest.TestCase):
    def test_provenance_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mem = MemoryStore(Path(td) / "jarvis.db")
            try:
                with self.assertRaises(ProvenanceError):
                    mem.add_episode(
                        memory_id=new_id("epi"),
                        category="test",
                        data={"x": 1},
                        provenance_event_ids=[],
                    )
            finally:
                mem.close()

    def test_semantic_retrieval_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mem = MemoryStore(Path(td) / "jarvis.db")
            try:
                mem.add_semantic(
                    memory_id=new_id("sem"),
                    memory_key="project_alpha",
                    text_value="Alpha release risk due to CI failures.",
                    confidence=0.81,
                    provenance_event_ids=["evt_1"],
                )
                out = mem.retrieve_semantic("alpha")
                self.assertEqual(len(out), 1)
                item = out[0]
                self.assertIn("answer_payload", item)
                self.assertIn("confidence", item)
                self.assertIn("provenance", item)
                self.assertIn("freshness", item)
                self.assertIn("conflict_flags", item)
                self.assertTrue(mem.events_path.exists())
                events = mem.list_events(limit=10)
                event_types = {str(event.get("event_type")) for event in events}
                self.assertIn("memory.semantic_added", event_types)
                self.assertIn("memory.recall", event_types)
            finally:
                mem.close()


if __name__ == "__main__":
    unittest.main()
