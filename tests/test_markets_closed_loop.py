from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.markets_outcomes import MarketsOutcomesConnector
from jarvis.daemon import EventDaemon
from jarvis.reactors import ZenithRiskReactor
from jarvis.runtime import JarvisRuntime


class MarketsClosedLoopTests(unittest.TestCase):
    def test_handoff_outcomes_feed_learning_loop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "ui").mkdir(parents=True, exist_ok=True)
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            (repo / "service.py").write_text("def run():\n    return 'TODO_ZENITH'\n", encoding="utf-8")

            outcomes_path = root / "markets_outcomes.json"
            outcomes_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "handoff_id": "h_success",
                                "plan_id": "plan_market_h1",
                                "signal_id": "sig_h1",
                                "symbol": "NVDA",
                                "status": "filled",
                                "account_id": "default",
                                "occurred_at": "2026-04-11T19:00:00+00:00",
                            },
                            {
                                "handoff_id": "h_fail",
                                "plan_id": "plan_market_h2",
                                "signal_id": "sig_h2",
                                "symbol": "AAPL",
                                "status": "stopped",
                                "account_id": "default",
                                "occurred_at": "2026-04-11T19:10:00+00:00",
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            daemon = EventDaemon(
                runtime=runtime,
                connectors=[MarketsOutcomesConnector(outcomes_path)],
                reactors=[ZenithRiskReactor()],
            )
            try:
                summary = daemon.run_once(dry_run=True)
                learned = summary.get("market_outcome_learning") or []
                self.assertEqual(len(learned), 2)

                outcomes = runtime.list_market_outcomes(limit=10)
                self.assertEqual(len(outcomes), 2)
                statuses = {str(item.get("status")) for item in outcomes}
                self.assertIn("filled", statuses)
                self.assertIn("stopped", statuses)

                aggregate = runtime.summarize_market_outcomes(limit=20)
                self.assertEqual((aggregate.get("by_status") or {}).get("filled"), 1)
                self.assertEqual((aggregate.get("by_status") or {}).get("stopped"), 1)

                recent = runtime.plan_repo.list_recent_outcomes("markets:default", "markets", limit=10)
                self.assertEqual(len(recent), 2)
                plan_statuses = {item.get("status") for item in recent}
                self.assertIn("success", plan_statuses)
                self.assertIn("regression", plan_statuses)
            finally:
                daemon.close()
                runtime.close()


if __name__ == "__main__":
    unittest.main()
