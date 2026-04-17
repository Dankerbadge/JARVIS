from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class CodexRuntimeIntegrationTests(unittest.TestCase):
    def test_presence_reply_respects_forced_gpt_engine(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                prepared = runtime.prepare_openclaw_reply(
                    {
                        "text": "Figure out how to change this in the app and implement it.",
                        "surface_id": "voice:owner",
                        "session_id": "live-1",
                        "execution_engine": "gpt",
                        "context": {"codex_auto_execute": False},
                    }
                )
                self.assertEqual(((prepared.get("work_item") or {}).get("engine_route")), "gpt")
                self.assertNotIn("codex_task", prepared)
            finally:
                runtime.close()

    def test_presence_reply_non_code_routes_to_gpt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                prepared = runtime.prepare_openclaw_reply(
                    {
                        "text": "Explain the tradeoffs between two strategies for tomorrow.",
                        "surface_id": "dm:owner",
                        "session_id": "live-1",
                    }
                )
                self.assertEqual(((prepared.get("work_item") or {}).get("engine_route")), "gpt")
                self.assertNotIn("codex_task", prepared)
            finally:
                runtime.close()

    def test_presence_reply_can_explain_route_when_asked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                prepared = runtime.prepare_openclaw_reply(
                    {
                        "text": "Should this use codex or gpt and what tier is it?",
                        "surface_id": "dm:owner",
                        "session_id": "live-1",
                    }
                )
                self.assertEqual(((prepared.get("work_item") or {}).get("engine_route")), "gpt")
                self.assertEqual(((prepared.get("work_item") or {}).get("effort_tier")), "thinking")
                self.assertTrue(bool(prepared.get("work_item_explained")))
                self.assertIn("Routing:", str(prepared.get("reply_text") or ""))
                self.assertNotIn("codex_task", prepared)
            finally:
                runtime.close()

    def test_presence_reply_can_queue_codex_task(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                prepared = runtime.prepare_openclaw_reply(
                    {
                        "text": "Hey, figure out how to change this in the app and implement it.",
                        "surface_id": "voice:owner",
                        "session_id": "live-1",
                        "context": {"codex_auto_execute": False},
                    }
                )
                self.assertIn("work_item", prepared)
                self.assertIn(
                    ((prepared.get("work_item") or {}).get("effort_tier")),
                    {"instant", "thinking", "pro", "extended_thinking", "deep_research"},
                )
                self.assertEqual(((prepared.get("work_item") or {}).get("engine_route")), "codex")
                self.assertIn("codex_task", prepared)
                self.assertIn("Codex:", str(prepared.get("reply_text") or ""))
                task = (
                    ((prepared.get("codex_task") or {}).get("submission") or {}).get("task")
                    if isinstance(prepared.get("codex_task"), dict)
                    else {}
                )
                self.assertEqual((task or {}).get("status"), "queued")

                tasks = runtime.list_codex_tasks(limit=10)
                self.assertGreaterEqual(len(tasks), 1)
                self.assertEqual(tasks[0].get("status"), "queued")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
