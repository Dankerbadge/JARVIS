from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.codex_delegation import CodexDelegationService


class CodexDelegationTests(unittest.TestCase):
    def test_routes_to_gpt_for_non_code_prompt(self) -> None:
        intent = CodexDelegationService.classify_intent(
            text="Help me reflect on this decision and explain tradeoffs.",
            explicit_directive=False,
            context={},
        )
        self.assertEqual(intent.get("engine_route"), "gpt")
        self.assertFalse(bool(intent.get("should_delegate")))

    def test_does_not_false_match_ui_inside_quick(self) -> None:
        intent = CodexDelegationService.classify_intent(
            text="quick status update",
            explicit_directive=False,
            context={},
        )
        self.assertEqual(intent.get("engine_route"), "gpt")
        self.assertFalse(bool(intent.get("has_app_scope")))

    def test_routing_query_stays_on_gpt(self) -> None:
        intent = CodexDelegationService.classify_intent(
            text="Should this use codex or gpt and what tier is it?",
            explicit_directive=False,
            context={},
        )
        self.assertEqual(intent.get("engine_route"), "gpt")
        self.assertEqual(intent.get("route_reason"), "routing_query")
        self.assertFalse(bool(intent.get("should_delegate")))
        self.assertEqual(intent.get("effort_tier"), "thinking")

    def test_routes_to_codex_for_code_prompt(self) -> None:
        intent = CodexDelegationService.classify_intent(
            text="Figure out how to change this in the app and implement it.",
            explicit_directive=False,
            context={},
        )
        self.assertEqual(intent.get("engine_route"), "codex")
        self.assertTrue(bool(intent.get("should_delegate")))

    def test_classifies_effort_tier_from_prompt(self) -> None:
        classified = CodexDelegationService.classify_work_item(
            text="Do a deep research pass with citations and verify latest docs before implementation.",
            context={},
        )
        self.assertEqual(classified.get("effort_tier"), "deep_research")
        self.assertEqual(classified.get("reasoning_effort"), "xhigh")

    def test_submit_and_execute_task_with_stub_runner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "jarvis.db"
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)

            def _runner(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if "--output-last-message" in cmd:
                    idx = cmd.index("--output-last-message")
                    out_path = Path(cmd[idx + 1])
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text("Codex finished the requested change.", encoding="utf-8")
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

            service = CodexDelegationService(
                db_path=db_path,
                repo_path=repo,
                runner=_runner,
            )
            try:
                submission = service.submit_task(
                    user_text="Figure out how to change this in the app.",
                    source_surface="voice:owner",
                    session_id="sess-1",
                    actor="owner",
                    write_enabled=True,
                    auto_execute=False,
                    context={"codex_auto_execute": False},
                )
                self.assertTrue(submission.get("ok"))
                task = submission.get("task") if isinstance(submission.get("task"), dict) else {}
                task_id = str(task.get("task_id") or "")
                self.assertTrue(task_id)
                self.assertEqual(task.get("status"), "queued")
                self.assertIn(task.get("effort_tier"), {"instant", "thinking", "pro", "extended_thinking", "deep_research"})
                self.assertIn(task.get("reasoning_effort"), {"low", "medium", "high", "xhigh"})

                executed = service.execute_task(task_id, background=False)
                self.assertTrue(executed.get("ok"))
                self.assertEqual(executed.get("status"), "completed")

                fetched = service.get_task(task_id)
                self.assertIsNotNone(fetched)
                self.assertEqual((fetched or {}).get("status"), "completed")
                self.assertIn("Codex finished", str((fetched or {}).get("last_message") or ""))
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
