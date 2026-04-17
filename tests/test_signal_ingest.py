from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class SignalIngestTests(unittest.TestCase):
    def test_ingest_signal_is_replay_safe_and_routes_to_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                signal = {
                    "schema_version": "jarvis.signal.v1",
                    "kind": "email.thread",
                    "payload": {
                        "source_type": "academic.assignment_due",
                        "course_id": "CS101",
                        "term_id": "current_term",
                        "title": "Project 1 due",
                        "due_at": "2026-04-15T16:00:00+00:00",
                    },
                    "provenance": {
                        "source_kind": "provider",
                        "provider": "gmail",
                        "source_id": "gmail:msg:123",
                        "trust": "untrusted",
                    },
                }
                first = runtime.ingest_signal(signal)
                self.assertTrue(first.get("ok"))
                self.assertTrue(first.get("accepted"))
                self.assertFalse(first.get("duplicate"))

                second = runtime.ingest_signal(signal)
                self.assertTrue(second.get("ok"))
                self.assertTrue(second.get("duplicate"))
                self.assertFalse(second.get("accepted"))

                stored = runtime.list_ingested_signals(limit=10)
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0].get("kind"), "email.thread")
                self.assertEqual((stored[0].get("provenance") or {}).get("provider"), "gmail")

                risks = runtime.list_academic_risks()
                self.assertTrue(risks)
                self.assertEqual(risks[0]["value"].get("signal_source_kind"), "provider")
                self.assertEqual(risks[0]["value"].get("signal_provider"), "gmail")

                events = runtime.list_consciousness_events(limit=20, event_type="ingest.signal_received")
                self.assertGreaterEqual(len(events), 1)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
