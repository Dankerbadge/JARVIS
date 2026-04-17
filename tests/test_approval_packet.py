from __future__ import annotations

import unittest

from jarvis.approval_packet import (
    DiffSummary,
    OutcomeSummary,
    PreflightCheckSummary,
    RankedCandidate,
    build_approval_packet,
)


class ApprovalPacketTests(unittest.TestCase):
    def test_build_approval_packet_flags_protected_paths(self) -> None:
        packet = build_approval_packet(
            approval_id="appr-1",
            plan_id="plan-1",
            step_id="step-1",
            permission_class="P2",
            reason="Protected UI patch proposed due to ranked candidates.",
            repo_id="zenith",
            branch="feature/fix-ci",
            confidence=0.91,
            ranked_candidates=[
                RankedCandidate(
                    path="ui/zenith_ui.txt",
                    score=0.93,
                    reasons=("ci stack trace", "recent delta"),
                ),
            ],
            diff_summary=DiffSummary(
                touched_files=("service.py", "ui/zenith_ui.txt"),
                protected_files=("ui/zenith_ui.txt",),
                patch_bytes=120,
            ),
            preflight=[
                PreflightCheckSummary(name="syntax", passed=True, return_code=0),
            ],
            rollback_plan=("remove worktree", "delete branch"),
            recent_outcomes=(
                OutcomeSummary(
                    path="ui/zenith_ui.txt",
                    status="success",
                    weight=0.4,
                    note="Similar fix merged last week",
                ),
            ),
        )

        self.assertEqual(packet.recommended_decision, "manual-review")
        self.assertIn("Protected paths touched.", packet.notes)
        rendered = packet.to_markdown()
        self.assertIn("ui/zenith_ui.txt", rendered)
        self.assertIn("manual-review", rendered)


if __name__ == "__main__":
    unittest.main()

