from __future__ import annotations

import unittest

from jarvis.correlation import RootCauseScorer


class CorrelationEngineTests(unittest.TestCase):
    def test_failed_path_with_positive_history_ranks_first(self) -> None:
        scorer = RootCauseScorer()
        report = scorer.rank(
            repo_id="Zenith",
            branch="feature/fix-health",
            repo_delta={
                "head_sha": "abc123",
                "changed_files": ["service.py", "ui/zenith_ui.txt"],
                "dirty_files": [],
            },
            ci_failure={
                "report_id": "ci-101",
                "repo_id": "Zenith",
                "branch": "feature/fix-health",
                "head_sha": "abc123",
                "failed_tests": ["tests/test_service_health.py::test_health_endpoint"],
                "failed_paths": ["service.py"],
                "summary": "health endpoint regression in service.py",
                "stacktrace": "Traceback ... in service.py line 88",
            },
            recent_outcomes=[
                {
                    "plan_id": "p1",
                    "repo_id": "Zenith",
                    "branch": "feature/fix-health",
                    "status": "success",
                    "failure_family": "health-endpoint-regression-in-service.py",
                    "touched_paths": ["service.py"],
                    "recorded_at": "2026-04-10T10:00:00+00:00",
                }
            ],
        )

        self.assertGreaterEqual(report.confidence, 0.7)
        self.assertEqual(report.candidates[0].path, "service.py")
        self.assertIn("ci:ci-101", report.signals)
        self.assertIn("git:abc123", report.signals)
        self.assertGreater(report.candidates[0].score, report.candidates[1].score)

    def test_negative_history_penalizes_protected_ui_candidate(self) -> None:
        scorer = RootCauseScorer()
        report = scorer.rank(
            repo_id="Zenith",
            branch="feature/ui-fix",
            repo_delta={
                "head_sha": "def456",
                "changed_files": ["service.py", "ui/zenith_ui.txt"],
                "dirty_files": ["ui/zenith_ui.txt"],
            },
            ci_failure={
                "report_id": "ci-202",
                "repo_id": "Zenith",
                "branch": "feature/ui-fix",
                "head_sha": "def456",
                "failed_tests": ["tests/test_service_health.py::test_health_endpoint"],
                "failed_paths": ["service.py", "ui/zenith_ui.txt"],
                "summary": "health endpoint regression with stale snapshot",
            },
            recent_outcomes=[
                {
                    "plan_id": "p2",
                    "repo_id": "Zenith",
                    "branch": "feature/ui-fix",
                    "status": "regression",
                    "failure_family": "health-endpoint-regression-with-stale-snapshot",
                    "touched_paths": ["ui/zenith_ui.txt"],
                    "recorded_at": "2026-04-10T11:00:00+00:00",
                },
                {
                    "plan_id": "p3",
                    "repo_id": "Zenith",
                    "branch": "feature/ui-fix",
                    "status": "success",
                    "failure_family": "health-endpoint-regression-with-stale-snapshot",
                    "touched_paths": ["service.py"],
                    "recorded_at": "2026-04-10T12:00:00+00:00",
                },
            ],
        )

        top = report.candidates[0]
        ui_candidate = next(
            candidate for candidate in report.candidates if candidate.path == "ui/zenith_ui.txt"
        )
        self.assertEqual(top.path, "service.py")
        self.assertTrue(ui_candidate.protected)
        self.assertLess(ui_candidate.score, top.score)
        self.assertTrue(any("approval-gated" in reason for reason in ui_candidate.reasons))


if __name__ == "__main__":
    unittest.main()

