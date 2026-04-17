from __future__ import annotations

import unittest

from jarvis.approval_packet import DiffSummary, PreflightCheckSummary, RankedCandidate, build_approval_packet


class PreflightPacketTests(unittest.TestCase):
    def test_failed_preflight_recommends_deny(self) -> None:
        packet = build_approval_packet(
            approval_id="appr-fail",
            plan_id="plan-fail",
            step_id="step-fail",
            permission_class="P2",
            reason="A patch failed preflight validation.",
            repo_id="zenith",
            branch="feature/bad",
            confidence=0.92,
            ranked_candidates=[RankedCandidate(path="service.py", score=0.92, reasons=("ci failure",))],
            diff_summary=DiffSummary(touched_files=("service.py",), protected_files=(), patch_bytes=42),
            preflight=[
                PreflightCheckSummary(
                    name="unit-tests",
                    passed=False,
                    return_code=1,
                    stderr_excerpt="1 failed",
                )
            ],
            rollback_plan=("remove worktree",),
        )
        self.assertEqual(packet.recommended_decision, "deny")


if __name__ == "__main__":
    unittest.main()

