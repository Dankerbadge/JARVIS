from __future__ import annotations

import unittest

from jarvis.approval_packet import (
    ApprovalPacket,
    DiffSummary,
    OutcomeSummary,
    PreflightCheckSummary,
    RankedCandidate,
)
from jarvis.models import PlanArtifact, PlanStep
from jarvis.pr_payload import build_pull_request_payload


class PullRequestPayloadTests(unittest.TestCase):
    def test_builds_draft_payload_with_evidence_sections(self) -> None:
        step = PlanStep(
            action_class="P2",
            proposed_action="zenith_apply_protected_ui_patch",
            expected_effect="UI patch applied",
            rollback="git revert",
            payload={"relative_path": "ui/zenith_ui.txt"},
            requires_approval=True,
            step_id="step-1",
        )
        plan = PlanArtifact(
            intent="triage_ci_failure_with_root_cause_correlation",
            priority="high",
            reasoning_summary="Correlated CI failure on main; generated ranked root-cause plan.",
            steps=[step],
            approval_requirements=["P2 approval"],
            expires_at="2099-01-01T00:00:00+00:00",
            plan_id="plan-1",
        )
        packet = ApprovalPacket(
            approval_id="apr-1",
            plan_id="plan-1",
            step_id="step-1",
            permission_class="P2",
            reason="Protected UI change needs review.",
            repo_id="zenith",
            branch="feature/candidate",
            confidence=0.91,
            recommended_decision="manual-review",
            ranked_candidates=(
                RankedCandidate(path="service.py", score=0.95, reasons=("latest delta", "failed test")),
                RankedCandidate(path="ui/zenith_ui.txt", score=0.82, reasons=("protected path",)),
            ),
            diff_summary=DiffSummary(
                touched_files=("service.py", "ui/zenith_ui.txt"),
                protected_files=("ui/zenith_ui.txt",),
                patch_bytes=128,
            ),
            preflight=(
                PreflightCheckSummary(name="compile", passed=True, return_code=0),
            ),
            rollback_plan=("delete sandbox branch", "discard sandbox worktree"),
            recent_outcomes=(
                OutcomeSummary(path="service.py", status="success", weight=1.0, note="resolved similar failure"),
            ),
            notes=("Prepared in isolated git worktree.",),
        )

        payload = build_pull_request_payload(
            plan=plan,
            step=step,
            packet=packet,
            base_branch="main",
            head_branch="jarvis/plan-1",
            commit_sha="abc123",
            approved_by="tester",
            remote_name="origin",
            draft=True,
        )

        self.assertTrue(payload.draft)
        self.assertIn("[JARVIS]", payload.title)
        self.assertIn("service.py", payload.body_markdown)
        self.assertIn("Approved by: `tester`", payload.body_markdown)
        self.assertIn("protected-change", payload.labels)
        self.assertIn("needs-review", payload.labels)


if __name__ == "__main__":
    unittest.main()
