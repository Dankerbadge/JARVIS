from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class RelationshipModeProjectionTests(unittest.TestCase):
    def test_one_consciousness_mode_selection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                default_mode = runtime.decide_relationship_mode(context={"case": "default"})
                self.assertEqual(default_mode.get("mode"), "equal")

                butler_mode = runtime.decide_relationship_mode(
                    explicit_directive=True,
                    disputed=False,
                    context={"case": "explicit"},
                )
                self.assertEqual(butler_mode.get("mode"), "butler")

                strategist_mode = runtime.decide_relationship_mode(
                    high_stakes=True,
                    uncertainty=0.7,
                    context={"case": "high_stakes"},
                )
                self.assertEqual(strategist_mode.get("mode"), "strategist")

                latest = runtime.get_presence_mode()
                self.assertEqual(latest.get("mode"), "strategist")

                recent = runtime.relationship_modes.list_recent(limit=10)
                self.assertGreaterEqual(len(recent), 3)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
