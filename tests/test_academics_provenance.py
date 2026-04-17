from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class AcademicsProvenanceTests(unittest.TestCase):
    def test_provider_source_metadata_flows_into_academic_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.ingest_event(
                    source="academics_google_calendar",
                    source_type="academic.exam_scheduled",
                    payload={
                        "project": "academics",
                        "domain": "academics",
                        "course_id": "CS101",
                        "term_id": "current_term",
                        "title": "CS101 Midterm",
                        "exam_at": "2026-04-13T14:00:00+00:00",
                        "ingestion_source_kind": "provider",
                        "ingestion_provider": "google_calendar",
                    },
                )
                risks = runtime.list_academic_risks()
                self.assertTrue(risks)
                self.assertEqual(risks[0]["value"].get("signal_source_kind"), "provider")
                self.assertEqual(risks[0]["value"].get("signal_provider"), "google_calendar")

                overview = runtime.get_academics_overview("current_term") or {}
                self.assertEqual(overview.get("signal_source_kind"), "provider")
                self.assertEqual(overview.get("signal_provider"), "google_calendar")

                home = runtime.get_operator_home()
                sources = ((home.get("academics") or {}).get("signal_sources") or [])
                self.assertTrue(
                    any(
                        item.get("source_kind") == "provider"
                        and item.get("provider") == "google_calendar"
                        for item in sources
                    )
                )
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

