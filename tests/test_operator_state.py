from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.operator_state import OperatorStateStore


class OperatorStateStoreTests(unittest.TestCase):
    def test_preferences_round_trip_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "jarvis.db"
            store = OperatorStateStore(db_path)
            try:
                defaults = store.get_preferences()
                self.assertIsNone(defaults.get("focus_mode_domain"))
                pondering_defaults = defaults.get("pondering_mode") or {}
                self.assertFalse(bool(pondering_defaults.get("enabled")))
                self.assertEqual(pondering_defaults.get("style"), "open_discussion")

                updated = store.set_focus_mode(domain="academics", actor="tester")
                self.assertEqual(updated.get("focus_mode_domain"), "academics")

                updated = store.set_quiet_hours(start_hour=22, end_hour=7, actor="tester")
                quiet = updated.get("quiet_hours") or {}
                self.assertEqual(quiet.get("start_hour"), 22)
                self.assertEqual(quiet.get("end_hour"), 7)

                updated = store.set_suppress_until(
                    until_iso="2026-04-12T12:00:00+00:00",
                    reason="deep work",
                    actor="tester",
                )
                self.assertEqual(updated.get("suppress_until"), "2026-04-12T12:00:00+00:00")
                self.assertEqual(updated.get("suppression_reason"), "deep work")

                updated = store.set_pondering_mode(
                    enabled=True,
                    style="socratic",
                    min_confidence_for_understood=0.84,
                    actor="tester",
                )
                pondering = updated.get("pondering_mode") or {}
                self.assertTrue(bool(pondering.get("enabled")))
                self.assertEqual(pondering.get("style"), "socratic")
                self.assertEqual(pondering.get("min_confidence_for_understood"), 0.84)

                events = store.list_events(limit=10)
                self.assertGreaterEqual(len(events), 4)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
