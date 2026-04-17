from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.academics_materials import AcademicMaterialsConnector


class AcademicMaterialsConnectorTests(unittest.TestCase):
    def test_material_files_emit_reading_due_and_message_signals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            syllabus = root / "CS101_syllabus.txt"
            syllabus.write_text(
                "CS101 syllabus\n"
                "Reading Chapter 3 before class\n"
                "Project 1 due 2026-04-15T23:59:00Z\n",
                encoding="utf-8",
            )
            email_file = root / "prof_update.eml"
            email_file.write_text(
                "From: professor@university.edu\n"
                "Subject: Midterm exam reminder\n\n"
                "Midterm exam on 2026-04-14T13:00:00Z.\n",
                encoding="utf-8",
            )

            connector = AcademicMaterialsConnector(root)
            first = connector.poll(None)
            source_types = sorted({event.source_type for event in first.events})
            self.assertIn("academic.syllabus_item", source_types)
            self.assertIn("academic.reading_assigned", source_types)
            self.assertIn("academic.assignment_due", source_types)
            self.assertIn("academic.professor_message", source_types)
            self.assertIn("academic.exam_scheduled", source_types)

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)


if __name__ == "__main__":
    unittest.main()
