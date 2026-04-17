from __future__ import annotations

import unittest

from jarvis.outcomes import build_path_feedback, map_review_feedback_to_outcome


class OutcomeFeedbackTests(unittest.TestCase):
    def test_build_path_feedback_aggregates_status_counts(self) -> None:
        outcomes = [
            {
                "plan_id": "p1",
                "repo_id": "Zenith",
                "branch": "feature/fix-health",
                "status": "success",
                "failure_family": "health-endpoint-regression",
                "touched_paths": ["service.py"],
                "recorded_at": "2026-04-10T10:00:00+00:00",
            },
            {
                "plan_id": "p2",
                "repo_id": "Zenith",
                "branch": "feature/fix-health",
                "status": "partial",
                "failure_family": "health-endpoint-regression",
                "touched_paths": ["service.py", "api/health.py"],
                "recorded_at": "2026-04-10T11:00:00+00:00",
            },
            {
                "plan_id": "p3",
                "repo_id": "Zenith",
                "branch": "feature/fix-health",
                "status": "regression",
                "failure_family": "health-endpoint-regression",
                "touched_paths": ["ui/zenith_ui.txt"],
                "recorded_at": "2026-04-10T12:00:00+00:00",
            },
        ]

        stats = build_path_feedback(
            outcomes,
            failure_family="health-endpoint-regression",
            branch="feature/fix-health",
        )

        self.assertEqual(stats["service.py"].success_count, 1)
        self.assertEqual(stats["service.py"].partial_count, 1)
        self.assertEqual(stats["ui/zenith_ui.txt"].regression_count, 1)
        self.assertGreater(stats["service.py"].net_signal, 0)
        self.assertLess(stats["ui/zenith_ui.txt"].net_signal, 0)

    def test_map_review_feedback_to_outcome_contract(self) -> None:
        self.assertEqual(
            map_review_feedback_to_outcome(decision="approved", merge_outcome=None),
            ("success", "APPROVED"),
        )
        self.assertEqual(
            map_review_feedback_to_outcome(decision="changes_requested", merge_outcome=None),
            ("regression", "CHANGES_REQUESTED"),
        )
        self.assertEqual(
            map_review_feedback_to_outcome(decision="commented", merge_outcome=None),
            ("commented", "COMMENTED"),
        )
        self.assertEqual(
            map_review_feedback_to_outcome(decision="none", merge_outcome="merged"),
            ("success", "MERGED"),
        )
        self.assertEqual(
            map_review_feedback_to_outcome(decision="none", merge_outcome="closed_unmerged"),
            ("failure", "CLOSED_UNMERGED"),
        )


if __name__ == "__main__":
    unittest.main()
