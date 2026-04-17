from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path

from jarvis.connectors.base import BaseConnector, ConnectorPollResult
from jarvis.daemon import EventDaemon
from jarvis.models import EventEnvelope
from jarvis.reactors import ZenithRiskReactor
from jarvis.runtime import JarvisRuntime


class StaticConnector(BaseConnector):
    def __init__(self, name: str, events: list[EventEnvelope]) -> None:
        self.name = name
        self._events = events

    def poll(self, cursor: dict | None) -> ConnectorPollResult:
        if (cursor or {}).get("done"):
            return ConnectorPollResult(events=[], cursor=cursor)
        return ConnectorPollResult(events=self._events, cursor={"done": True})


class EventRuntimeTests(unittest.TestCase):
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
        (repo / "service.py").write_text(
            "def render():\n    return 'TODO_ZENITH'\n",
            encoding="utf-8",
        )
        self._git(root, "init", str(repo))
        self._git(repo, "config", "user.email", "jarvis@example.com")
        self._git(repo, "config", "user.name", "JARVIS")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "initial")
        return repo

    def test_benign_event_produces_no_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._create_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            daemon = None
            try:
                benign = EventEnvelope(
                    source="github",
                    source_type="ci",
                    payload={"project": "zenith", "status": "passed", "deadline_hours": 120},
                )
                daemon = EventDaemon(
                    runtime=runtime,
                    connectors=[StaticConnector("static_benign", [benign])],
                    reactors=[ZenithRiskReactor()],
                )
                summary = daemon.run_once(dry_run=True)
                self.assertEqual(summary["events_processed"], 1)
                self.assertEqual(summary["plans_proposed"], 0)
                self.assertEqual(summary["plans_executed"], 0)

                summary2 = daemon.run_once(dry_run=True)
                self.assertEqual(summary2["events_processed"], 0)
            finally:
                if daemon is not None:
                    daemon.close()
                runtime.close()

    def test_high_risk_event_creates_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._create_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            daemon = None
            try:
                risky = EventEnvelope(
                    source="github",
                    source_type="ci",
                    payload={"project": "zenith", "status": "failed", "deadline_hours": 12},
                )
                daemon = EventDaemon(
                    runtime=runtime,
                    connectors=[StaticConnector("static_risk", [risky])],
                    reactors=[ZenithRiskReactor()],
                )
                summary = daemon.run_once(dry_run=False)
                self.assertEqual(summary["plans_proposed"], 1)
                self.assertEqual(summary["plans_executed"], 1)
                self.assertTrue(summary["pending_approvals"])
                approval_id = summary["pending_approvals"][0]["approval_id"]
                packet = runtime.security.get_approval_packet(approval_id)
                self.assertIsNotNone(packet)
                self.assertIn("packet", packet or {})
                self.assertIn("markdown", packet or {})
                statuses = [
                    result["status"]
                    for execution in summary["executions"]
                    for result in execution["results"]
                ]
                self.assertIn("awaiting_approval", statuses)
            finally:
                if daemon is not None:
                    daemon.close()
                runtime.close()


if __name__ == "__main__":
    unittest.main()
