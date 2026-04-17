from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.skills.zenith import RepoDiffEngine


class RepoDiffEngineTests(unittest.TestCase):
    def test_preview_patch_uses_real_repo_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "ui").mkdir()
            file_path = repo / "ui" / "zenith_ui.txt"
            file_path.write_text("hello TODO_UI\n", encoding="utf-8")

            engine = RepoDiffEngine(repo)
            proposal = engine.preview_file_replacement(
                "ui/zenith_ui.txt",
                "TODO_UI",
                "READY_UI",
            )

            self.assertTrue(proposal.changed)
            self.assertIn("a/ui/zenith_ui.txt", proposal.patch)
            self.assertIn("b/ui/zenith_ui.txt", proposal.patch)
            self.assertIn("-hello TODO_UI", proposal.patch)
            self.assertIn("+hello READY_UI", proposal.patch)


if __name__ == "__main__":
    unittest.main()

