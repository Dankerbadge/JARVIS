from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.academics import AcademicsFeedConnector


class AcademicsConnectorTests(unittest.TestCase):
    def test_poll_emits_new_events_and_persists_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "academics_feed.json"
            feed.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "a1",
                                "type": "assignment_due",
                                "course_id": "CS101",
                                "term_id": "2026-spring",
                                "title": "Project Milestone",
                                "due_at": "2026-04-12T15:00:00+00:00",
                            },
                            {
                                "id": "a2",
                                "type": "grade_update",
                                "course_id": "MATH201",
                                "term_id": "2026-spring",
                                "grade": 76,
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            connector = AcademicsFeedConnector(feed)
            first = connector.poll(None)
            self.assertEqual(len(first.events), 2)
            self.assertEqual(first.events[0].source_type, "academic.assignment_due")
            self.assertEqual(first.events[1].source_type, "academic.grade_update")
            self.assertTrue(first.cursor and first.cursor.get("seen_ids"))

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)


if __name__ == "__main__":
    unittest.main()

