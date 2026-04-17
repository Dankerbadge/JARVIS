from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.base import BaseConnector, ConnectorPollResult
from jarvis.daemon import EventDaemon
from jarvis.models import EventEnvelope
from jarvis.reactors import ZenithRiskReactor
from jarvis.runtime import JarvisRuntime


class _StaticConnector(BaseConnector):
    def __init__(self, name: str, events: list[EventEnvelope]) -> None:
        self.name = name
        self._events = events

    def poll(self, cursor: dict | None) -> ConnectorPollResult:
        if (cursor or {}).get("done"):
            return ConnectorPollResult(events=[], cursor=cursor)
        return ConnectorPollResult(events=self._events, cursor={"done": True})


class M9RuntimeTests(unittest.TestCase):
    def _git(self, cwd: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def _create_repo(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text("def render():\n    return 'TODO_ZENITH'\n", encoding="utf-8")
        self._git(root, "init", str(repo))
        self._git(repo, "config", "user.email", "jarvis@example.com")
        self._git(repo, "config", "user.name", "JARVIS")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "initial")
        return repo

    def test_cognition_cycle_persists_thoughts_and_interrupts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._create_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
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
                        "reason": "academic.risk_signal",
                        "due_at": "2026-04-12T12:00:00+00:00",
                    },
                )
                cycle = runtime.run_cognition_cycle()
                self.assertEqual(cycle["status"], "ok")
                self.assertGreaterEqual(cycle["hypothesis_count"], 1)
                thoughts = runtime.list_recent_thoughts(limit=5)
                self.assertTrue(thoughts)
                interrupts = runtime.list_interrupts(status="all", limit=10)
                self.assertTrue(interrupts)
            finally:
                runtime.close()

    def test_academics_event_flows_through_shared_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._create_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            daemon = None
            try:
                event = EventEnvelope(
                    source="academics",
                    source_type="academic.assignment_due",
                    payload={
                        "project": "academics",
                        "domain": "academics",
                        "course_id": "CS101",
                        "term_id": "2026-spring",
                        "title": "Midterm Prep",
                        "due_at": "2026-04-11T12:00:00+00:00",
                    },
                )
                daemon = EventDaemon(
                    runtime=runtime,
                    connectors=[_StaticConnector("academic_static", [event])],
                    reactors=[ZenithRiskReactor()],
                )
                summary = daemon.run_once(dry_run=False)
                self.assertEqual(summary["events_processed"], 1)
                self.assertGreaterEqual(summary["plans_proposed"], 1)
                self.assertTrue(summary["pending_approvals"])
                self.assertTrue(summary["cognition"])
                academic_risks = runtime.list_academic_risks()
                self.assertTrue(academic_risks)
            finally:
                if daemon is not None:
                    daemon.close()
                runtime.close()

    def test_manual_synthesis_generation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._create_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.ingest_event(
                    source="academics",
                    source_type="academic.risk_signal",
                    payload={
                        "project": "academics",
                        "domain": "academics",
                        "course_id": "HIST200",
                        "term_id": "2026-spring",
                        "severity": "high",
                        "reason": "academic.risk_signal",
                    },
                )
                morning = runtime.generate_morning_synthesis()
                evening = runtime.generate_evening_synthesis()
                self.assertEqual(morning["kind"], "morning")
                self.assertEqual(evening["kind"], "evening")
                self.assertTrue(runtime.get_latest_synthesis("morning"))
                self.assertTrue(runtime.get_latest_synthesis("evening"))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

