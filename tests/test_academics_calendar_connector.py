from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.academics_calendar import AcademicCalendarConnector


class AcademicCalendarConnectorTests(unittest.TestCase):
    def test_ics_calendar_emits_exam_assignment_and_class_events(self) -> None:
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-1
SUMMARY:CS101 Midterm Exam
DTSTART:20260415T130000Z
DTEND:20260415T150000Z
END:VEVENT
BEGIN:VEVENT
UID:event-2
SUMMARY:CS101 Project Due
DTSTART:20260414T220000Z
DTEND:20260414T230000Z
END:VEVENT
BEGIN:VEVENT
UID:event-3
SUMMARY:CS101 Lecture
DTSTART:20260412T140000Z
DTEND:20260412T152000Z
END:VEVENT
END:VCALENDAR
"""
        with tempfile.TemporaryDirectory() as td:
            calendar = Path(td) / "courses.ics"
            calendar.write_text(ics, encoding="utf-8")
            connector = AcademicCalendarConnector(calendar)

            first = connector.poll(None)
            source_types = sorted(event.source_type for event in first.events)
            self.assertIn("academic.exam_scheduled", source_types)
            self.assertIn("academic.assignment_due", source_types)
            self.assertIn("academic.class_scheduled", source_types)
            self.assertTrue(first.cursor and first.cursor.get("seen_ids"))

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)


if __name__ == "__main__":
    unittest.main()
