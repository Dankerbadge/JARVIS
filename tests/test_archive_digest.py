from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class ArchiveDigestTests(unittest.TestCase):
    def test_export_daily_digest_creates_indexed_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "ui").mkdir(parents=True, exist_ok=True)
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            (repo / "service.py").write_text("def run():\n    return 'TODO_ZENITH'\n", encoding="utf-8")

            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.ingest_event(
                    source="academics",
                    source_type="academic.risk_signal",
                    payload={
                        "project": "academics",
                        "domain": "academics",
                        "course_id": "CS101",
                        "term_id": "current_term",
                        "severity": "high",
                        "reason": "exam_in_36h",
                    },
                )
                runtime.run_cognition_cycle()
                exported = runtime.export_daily_digest()
                self.assertTrue(Path(exported["markdown_path"]).exists())
                self.assertTrue(Path(exported["html_path"]).exists())
                self.assertTrue(Path(exported["json_path"]).exists())

                listing = runtime.list_digest_exports(limit=5)
                self.assertEqual(len(listing), 1)
                day_key = listing[0]["day_key"]
                self.assertIsNotNone(runtime.get_digest_export(day_key))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
