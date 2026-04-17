from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.markets_outcomes import MarketsOutcomesConnector


class MarketsOutcomesConnectorTests(unittest.TestCase):
    def test_poll_emits_incremental_handoff_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "markets_outcomes.json"
            feed.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "handoff_id": "h1",
                                "signal_id": "sig_1",
                                "symbol": "NVDA",
                                "status": "accepted",
                                "occurred_at": "2026-04-11T18:00:00+00:00",
                            },
                            {
                                "handoff_id": "h2",
                                "signal_id": "sig_2",
                                "symbol": "AAPL",
                                "status": "filled",
                                "occurred_at": "2026-04-11T18:05:00+00:00",
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            connector = MarketsOutcomesConnector(feed)
            first = connector.poll(None)
            self.assertEqual(len(first.events), 2)
            self.assertEqual(first.events[0].source_type, "market.handoff_outcome")
            self.assertEqual(first.events[0].payload.get("handoff_id"), "h1")
            self.assertEqual(first.events[1].payload.get("status"), "filled")

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)


if __name__ == "__main__":
    unittest.main()
