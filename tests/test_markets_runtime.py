from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.markets_calendar import MarketsCalendarConnector
from jarvis.connectors.markets_positions import MarketsPositionsConnector
from jarvis.connectors.markets_signals import MarketsSignalsConnector
from jarvis.daemon import EventDaemon
from jarvis.reactors import ZenithRiskReactor
from jarvis.runtime import JarvisRuntime


class MarketsRuntimeTests(unittest.TestCase):
    def test_markets_events_flow_into_shared_runtime_and_bounded_plans(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "ui").mkdir(parents=True, exist_ok=True)
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            (repo / "service.py").write_text("def run():\n    return 'TODO_ZENITH'\n", encoding="utf-8")

            signals_path = root / "markets_signals.json"
            signals_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "sig_strong_1",
                                "type": "signal_detected",
                                "symbol": "NVDA",
                                "confidence": 0.92,
                                "upside_bps": 210,
                                "downside_bps": 55,
                                "expiry_horizon_hours": 8,
                                "support_signals": ["breakout", "volume_expansion"],
                                "counter_signals": ["macro_event_risk"],
                                "why_now": "Trend continuation with strong breadth.",
                                "why_not": "Skip if risk regime flips before open.",
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            positions_path = root / "markets_positions.json"
            positions_path.write_text(
                json.dumps(
                    {
                        "account_id": "paper",
                        "positions": [{"symbol": "NVDA", "size": 10}],
                        "gross_exposure_pct": 52,
                        "net_exposure_pct": 23,
                        "risk_regime": "risk_on",
                        "as_of": "2026-04-11T14:00:00+00:00",
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            calendar_path = root / "markets_calendar.json"
            calendar_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "evt_earnings_1",
                                "type": "event_upcoming",
                                "symbol": "NVDA",
                                "event_at": "2026-04-12T20:00:00+00:00",
                                "importance": "high",
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            daemon = EventDaemon(
                runtime=runtime,
                connectors=[
                    MarketsSignalsConnector(signals_path),
                    MarketsPositionsConnector(positions_path),
                    MarketsCalendarConnector(calendar_path),
                ],
                reactors=[ZenithRiskReactor()],
            )
            try:
                summary = daemon.run_once(dry_run=True)
                self.assertGreaterEqual(summary.get("events_processed", 0), 3)
                self.assertGreaterEqual(summary.get("plans_proposed", 0), 1)

                opportunities = runtime.list_market_opportunities(limit=10)
                self.assertTrue(opportunities)
                self.assertEqual(opportunities[0].get("domain"), "markets")

                posture = runtime.get_market_risk_posture(account_id="paper")
                self.assertIsNotNone(posture)
                self.assertEqual((posture or {}).get("account_id"), "paper")

                home = runtime.get_operator_home()
                self.assertIn("markets", home)
                self.assertTrue((home.get("markets") or {}).get("opportunities"))

                executed_plan_ids = [item.get("plan_id") for item in summary.get("executions", []) if item.get("plan_id")]
                self.assertTrue(executed_plan_ids)
                for plan_id in executed_plan_ids:
                    plan = runtime.plan_repo.get_plan(plan_id)
                    action_classes = {step.action_class for step in plan.steps}
                    self.assertTrue(action_classes.issubset({"P0", "P1", "P2"}))
            finally:
                daemon.close()
                runtime.close()


if __name__ == "__main__":
    unittest.main()
