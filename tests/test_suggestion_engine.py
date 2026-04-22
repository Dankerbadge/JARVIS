from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class SuggestionEngineTests(unittest.TestCase):
    def _make_runtime(self, root: Path) -> JarvisRuntime:
        repo = root / "repo"
        db = root / "jarvis.db"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text(
            "def x():\n    return 'TODO_ZENITH'\n",
            encoding="utf-8",
        )
        return JarvisRuntime(db_path=db, repo_path=repo)

    def test_suggestions_include_approval_followup_for_blocked_traces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                ingest = runtime.ingest_event(
                    source="github",
                    source_type="ci",
                    payload={"project": "alpha", "status": "failed", "deadline_hours": 12},
                )
                plan_id = runtime.plan(ingest["triggers"])[0]
                runtime.run(plan_id, dry_run=True, approvals={})

                suggestions = runtime.list_suggestion_candidates(plan_id=plan_id, limit=20)
                self.assertTrue(any(str(item.get("kind")) == "approval_followup" for item in suggestions))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

