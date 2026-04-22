from __future__ import annotations

import unittest

from jarvis.learning.ranker import LearningActionRanker


class LearningRankerPenaltyTests(unittest.TestCase):
    def test_compensation_signals_reduce_action_rank(self) -> None:
        ranker = LearningActionRanker()
        stable_examples = [
            {
                "chosen_action": "stable_action",
                "utility_score": 0.8,
                "observed_outcome": "succeeded",
                "feature_vector": {"compensated_attempts": 0, "trace_status": "succeeded"},
            }
            for _ in range(3)
        ]
        compensated_examples = [
            {
                "chosen_action": "compensated_action",
                "utility_score": 0.8,
                "observed_outcome": "succeeded",
                "feature_vector": {"compensated_attempts": 2, "trace_status": "succeeded"},
            }
            for _ in range(3)
        ]
        ranked = ranker.rank_actions(stable_examples + compensated_examples, top_k=5)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(str(ranked[0].get("chosen_action")), "stable_action")

        stable_row = next(item for item in ranked if str(item.get("chosen_action")) == "stable_action")
        compensated_row = next(item for item in ranked if str(item.get("chosen_action")) == "compensated_action")
        self.assertGreater(float(compensated_row.get("compensation_rate") or 0.0), 0.0)
        self.assertGreater(float(compensated_row.get("penalty") or 0.0), float(stable_row.get("penalty") or 0.0))
        self.assertGreater(float(stable_row.get("score") or 0.0), float(compensated_row.get("score") or 0.0))


if __name__ == "__main__":
    unittest.main()
