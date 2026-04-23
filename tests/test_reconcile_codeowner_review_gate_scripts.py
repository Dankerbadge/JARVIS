from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReconcileCodeownerReviewGateScriptTests(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _write_mock_gh(self, path: Path) -> None:
        script = """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

args = sys.argv[1:]
if len(args) < 2 or args[0] != "api":
    print(f"unsupported gh invocation: {args}", file=sys.stderr)
    raise SystemExit(2)

target = str(args[1] or "")
collaborator_count = int(os.environ.get("MOCK_GH_COLLABORATOR_COUNT") or "0")
workflow_run_count = int(os.environ.get("MOCK_GH_WORKFLOW_RUN_COUNT") or "0")

if "/collaborators?" in target:
    payload = [
        {"login": f"user{index:05d}_" + ("x" * 64)}
        for index in range(collaborator_count)
    ]
    json.dump(payload, sys.stdout)
    raise SystemExit(0)

if "/branches/" in target and target.endswith("/protection"):
    payload = {
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "dismiss_stale_reviews": False,
            "require_last_push_approval": False,
            "require_code_owner_reviews": False,
        },
        "required_status_checks": {
            "strict": False,
            "contexts": [],
        },
    }
    json.dump(payload, sys.stdout)
    raise SystemExit(0)

if "/actions/workflows/" in target and "/runs?" in target:
    runs = []
    for index in range(workflow_run_count):
        run_id = 9000000 + index
        runs.append(
            {
                "id": run_id,
                "run_number": run_id,
                "event": "workflow_run",
                "status": "completed",
                "conclusion": "success",
                "html_url": f"https://example.test/runs/{run_id}",
                "created_at": "2026-04-23T12:00:00Z",
                "updated_at": "2026-04-23T12:01:00Z",
                "head_branch": "main",
                "name": "reconcile-codeowner-review-gate",
            }
        )
    json.dump({"workflow_runs": runs}, sys.stdout)
    raise SystemExit(0)

print(f"unexpected gh api target: {target}", file=sys.stderr)
raise SystemExit(3)
"""
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)

    def _test_env(self, bin_dir: Path, *, collaborator_count: int, workflow_run_count: int) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["MOCK_GH_COLLABORATOR_COUNT"] = str(collaborator_count)
        env["MOCK_GH_WORKFLOW_RUN_COUNT"] = str(workflow_run_count)
        return env

    def test_reconcile_script_handles_large_collaborator_payload(self) -> None:
        repo_root = self._repo_root()
        script = repo_root / "scripts" / "reconcile_codeowner_review_gate.sh"
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            self._write_mock_gh(bin_dir / "gh")
            env = self._test_env(bin_dir, collaborator_count=6500, workflow_run_count=0)

            proc = subprocess.run(
                [
                    "bash",
                    str(script),
                    "--repo-slug",
                    "acme/jarvis",
                    "--branch",
                    "main",
                    "--required-status-check",
                    "gate-status",
                ],
                cwd=str(repo_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(int(payload.get("collaborator_count") or 0), 6500)
            desired_contexts = list(((payload.get("required_status_checks") or {}).get("desired_contexts")) or [])
            self.assertIn("gate-status", desired_contexts)
            self.assertNotIn("Argument list too long", proc.stderr)

    def test_audit_script_handles_large_reconcile_and_workflow_payloads(self) -> None:
        repo_root = self._repo_root()
        script = repo_root / "scripts" / "audit_reconcile_codeowner_review_gate.sh"
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            self._write_mock_gh(bin_dir / "gh")
            env = self._test_env(bin_dir, collaborator_count=6500, workflow_run_count=2200)

            proc = subprocess.run(
                [
                    "bash",
                    str(script),
                    "--repo-slug",
                    "acme/jarvis",
                    "--branch",
                    "main",
                    "--recent-runs-limit",
                    "1500",
                    "--expected-trigger-event",
                    "workflow_run",
                    "--event-audit-since",
                    "2026-04-23T00:00:00Z",
                ],
                cwd=str(repo_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(int(payload.get("collaborator_count") or 0), 6500)
            self.assertEqual(int(payload.get("reconcile_trigger_recent_run_count") or 0), 1500)
            self.assertEqual(int(payload.get("reconcile_trigger_non_workflow_run_count") or 0), 0)
            self.assertNotIn("Argument list too long", proc.stderr)


if __name__ == "__main__":
    unittest.main()
