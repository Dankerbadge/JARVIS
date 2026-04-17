from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.runtime import JarvisRuntime


class RuntimeCognitionBackendConfigTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "ui").mkdir(parents=True, exist_ok=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text("def x():\n    return 'TODO_ZENITH'\n", encoding="utf-8")
        return repo

    def test_cognition_can_be_disabled_by_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._repo(root)
            with patch.dict("os.environ", {"JARVIS_COGNITION_ENABLED": "false"}, clear=False):
                runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                result = runtime.run_cognition_cycle()
                self.assertEqual(result["status"], "disabled")
            finally:
                runtime.close()

    def test_runtime_uses_backend_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._repo(root)
            with patch.dict(
                "os.environ",
                {
                    "JARVIS_COGNITION_BACKEND": "ollama",
                    "JARVIS_COGNITION_MODEL": "llama3.2:3b-instruct",
                    "JARVIS_COGNITION_ENABLED": "true",
                },
                clear=False,
            ):
                runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                config = runtime.get_cognition_config()
                self.assertEqual(config["backend"], "ollama")
                self.assertEqual(config["model"], "llama3.2:3b-instruct")
                self.assertTrue(config["enabled"])
                self.assertTrue(config["local_only"])
                self.assertIn("fallback_backend", config)
                self.assertIn("max_hypotheses_per_cycle", config)
                self.assertIn("model_assisted_synthesis", config)
                self.assertIn("model_assisted_skepticism", config)
            finally:
                runtime.close()

    def test_thought_artifact_includes_backend_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo, cognition_enabled=True)
            try:
                runtime.ingest_event(
                    source="academics",
                    source_type="academic.risk_signal",
                    payload={
                        "project": "academics",
                        "domain": "academics",
                        "course_id": "CS101",
                        "term_id": "2026-spring",
                        "severity": "high",
                        "reason": "exam_in_36h",
                    },
                )
                result = runtime.run_cognition_cycle()
                self.assertEqual(result["status"], "ok")
                thought = runtime.list_recent_thoughts(limit=1)[0]
                self.assertIn("backend_mode", thought)
                self.assertIn("backend_metrics", thought)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
