from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.models import EventEnvelope, new_id, utc_now_iso
from jarvis.state_graph import StateGraph


class StateGraphTests(unittest.TestCase):
    def test_event_pipeline_and_truth_object(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "jarvis.db"
            sg = StateGraph(db_path)
            try:
                event = EventEnvelope(
                    source="github",
                    source_type="ci",
                    payload={"project": "alpha", "status": "failed"},
                )

                def extractor(_: EventEnvelope) -> list[dict]:
                    return [
                        {
                            "kind": "entity",
                            "id": new_id("ent"),
                            "entity_key": "risk:alpha:ci_failed",
                            "entity_type": "Risk",
                            "value": {"project": "alpha", "severity": "high", "reason": "ci_failed"},
                            "confidence": 0.9,
                            "source_refs": [event.event_id],
                            "last_verified_at": utc_now_iso(),
                        }
                    ]

                out = sg.process_event(event, extractor)
                self.assertEqual(out["event_id"], event.event_id)
                self.assertTrue(out["touched_ids"])
                self.assertEqual(out["triggers"][0]["type"], "high_risk_detected")

                active = sg.get_active_entities("Risk")
                self.assertEqual(len(active), 1)
                risk = active[0]
                self.assertIsNotNone(risk["valid_from"])
                self.assertIsNone(risk["valid_to"])
                self.assertIn(event.event_id, risk["source_refs"])
            finally:
                sg.close()


if __name__ == "__main__":
    unittest.main()

