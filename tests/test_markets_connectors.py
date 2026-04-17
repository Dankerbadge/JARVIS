from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.connectors.markets_calendar import MarketsCalendarConnector
from jarvis.connectors.markets_positions import MarketsPositionsConnector
from jarvis.connectors.markets_signals import MarketsSignalsConnector


class MarketsConnectorsTests(unittest.TestCase):
    def test_markets_signals_connector_emits_new_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "markets_signals.json"
            feed.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "sig_1",
                                "type": "signal_detected",
                                "symbol": "NVDA",
                                "confidence": 0.91,
                            },
                            {
                                "id": "sig_2",
                                "type": "opportunity_expired",
                                "symbol": "MSFT",
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            connector = MarketsSignalsConnector(feed)
            first = connector.poll(None)
            self.assertEqual(len(first.events), 2)
            self.assertEqual(first.events[0].source_type, "market.signal_detected")
            self.assertEqual(first.events[1].source_type, "market.opportunity_expired")
            self.assertEqual(first.events[0].payload.get("domain"), "markets")

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)

    def test_markets_positions_connector_emits_snapshot_and_regime_change(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            snapshot = Path(td) / "positions.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "account_id": "paper",
                        "positions": [{"symbol": "NVDA", "size": 20}],
                        "gross_exposure_pct": 64,
                        "net_exposure_pct": 28,
                        "risk_regime": "risk_on",
                        "as_of": "2026-04-11T14:00:00+00:00",
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            connector = MarketsPositionsConnector(snapshot)
            first = connector.poll(None)
            self.assertEqual(len(first.events), 2)
            self.assertEqual(first.events[0].source_type, "market.position_snapshot")
            self.assertEqual(first.events[1].source_type, "market.risk_regime_changed")
            self.assertEqual(first.events[0].payload.get("account_id"), "paper")

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)

    def test_markets_calendar_connector_emits_event_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            calendar = Path(td) / "markets_calendar.json"
            calendar.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "evt_1",
                                "type": "event_upcoming",
                                "symbol": "AAPL",
                                "event_at": "2026-04-12T13:00:00+00:00",
                            },
                            {
                                "id": "evt_2",
                                "type": "opportunity_expired",
                                "symbol": "TSLA",
                                "event_at": "2026-04-12T15:00:00+00:00",
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            connector = MarketsCalendarConnector(calendar)
            first = connector.poll(None)
            self.assertEqual(len(first.events), 2)
            self.assertEqual(first.events[0].source_type, "market.event_upcoming")
            self.assertEqual(first.events[1].source_type, "market.opportunity_expired")

            second = connector.poll(first.cursor)
            self.assertEqual(len(second.events), 0)


if __name__ == "__main__":
    unittest.main()
