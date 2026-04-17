from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.personal_context import PersonalContextConnector


class PersonalContextConnectorTests(unittest.TestCase):
    def test_poll_emits_only_on_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "personal_context.json"
            path.write_text(
                json.dumps(
                    {
                        "stress_level": 0.7,
                        "energy_level": 0.45,
                        "sleep_hours": 6.1,
                        "available_focus_minutes": 75,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            connector = PersonalContextConnector(path)

            first = connector.poll(None)
            self.assertEqual(len(first.events), 1)
            self.assertEqual(first.events[0].source_type, "personal.context_snapshot")
            self.assertEqual(first.events[0].payload.get("ingestion_provider"), "local_personal_context")

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)

            path.write_text(
                json.dumps(
                    {
                        "stress_level": 0.82,
                        "energy_level": 0.32,
                        "sleep_hours": 5.2,
                        "available_focus_minutes": 30,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            third = connector.poll(second.cursor)
            self.assertEqual(len(third.events), 1)
            self.assertGreater(float(third.events[0].payload.get("stress_level")), 0.8)


if __name__ == "__main__":
    unittest.main()

