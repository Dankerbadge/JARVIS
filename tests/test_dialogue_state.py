from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.dialogue_state import DialogueStateStore


class DialogueStateStoreTests(unittest.TestCase):
    def test_thread_and_turn_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "jarvis.db"
            store = DialogueStateStore(db_path)
            try:
                thread = store.upsert_thread(
                    surface_id="dm:owner",
                    session_id="sess-1",
                    session_key="dm:owner:sess-1",
                    mode="equal",
                    objective_hint="stabilize dialogue quality",
                )
                self.assertTrue(str(thread.get("thread_id") or "").startswith("dthread_"))

                turn = store.record_turn(
                    thread_id=str(thread.get("thread_id")),
                    user_text="What is the next step?",
                    intent={"is_question": True},
                    context_refs=["identity", "dialogue.recent_turns"],
                    candidate_reply="Next step is define objective and constraints.",
                    final_reply="Next step is define objective and constraints.",
                    critique={"accepted": True, "issues": []},
                    mode="strategist",
                    pushback_triggered=False,
                    continuity={"continuity_ok": True},
                )
                self.assertEqual(turn.get("turn_index"), 1)
                self.assertEqual(turn.get("mode"), "strategist")

                turns = store.list_recent_turns(thread_id=str(thread.get("thread_id")), limit=10)
                self.assertEqual(len(turns), 1)
                self.assertEqual(str(turns[0].get("user_text")), "What is the next step?")

                updated = store.update_thread_state(
                    thread_id=str(thread.get("thread_id")),
                    summary_text="Objective and constraints locked.",
                    unresolved_questions=["What is the expected timeline?"],
                    active_hypotheses=["A constrained rollout will reduce drift."],
                )
                self.assertIn("expected timeline", str((updated or {}).get("unresolved_questions")))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()

