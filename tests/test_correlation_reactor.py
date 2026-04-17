from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.models import EventEnvelope
from jarvis.reactors import ZenithCorrelationReactor
from jarvis.runtime import JarvisRuntime
from jarvis.state_index import latest_root_cause_report_key


class CorrelationReactorTests(unittest.TestCase):
    def _setup_repo(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text("print('TODO_ZENITH')\n", encoding="utf-8")
        return repo

    def test_ci_failure_emits_correlation_plan_and_indexes_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._setup_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            reactor = ZenithCorrelationReactor()
            try:
                delta_event = EventEnvelope(
                    source="git",
                    source_type="repo.git_delta",
                    payload={
                        "project": "zenith",
                        "repo_id": str(repo),
                        "repo_path": str(repo),
                        "branch": "feature/fix-health",
                        "base_branch": "main",
                        "head_sha": "abc123",
                        "merge_base": "def456",
                        "commit_range": "def456..abc123",
                        "commits": [{"sha": "abc123", "subject": "test", "epoch": 1}],
                        "changed_files": ["service.py", "ui/zenith_ui.txt"],
                        "dirty_files": [],
                        "pr_candidate": True,
                        "protected_ui_changed": True,
                    },
                )
                runtime.ingest_envelope(delta_event)
                runtime.plan_repo.record_outcome(
                    plan_id="p1",
                    repo_id=str(repo),
                    branch="feature/fix-health",
                    status="success",
                    touched_paths=["service.py"],
                    failure_family="health-endpoint-regression-in-service.py",
                    summary="fixed service path",
                )

                ci_event = EventEnvelope(
                    source="ci",
                    source_type="ci.failure",
                    payload={
                        "project": "zenith",
                        "repo_id": str(repo),
                        "branch": "feature/fix-health",
                        "head_sha": "abc123",
                        "report_id": "ci-303",
                        "status": "failed",
                        "job_name": "unit-tests",
                        "summary": "health endpoint regression in service.py",
                        "error_summary": "health endpoint regression in service.py",
                        "failed_tests": ["tests/test_service_health.py::test_health_endpoint"],
                        "failed_paths": ["service.py"],
                        "implicated_paths": ["service.py"],
                        "zenith_owned": True,
                        "protected_ui_changed": False,
                    },
                )
                ingestion = runtime.ingest_envelope(ci_event)
                plans = reactor.propose_plans(runtime, ci_event, ingestion)
                self.assertEqual(len(plans), 1)
                plan = plans[0]
                self.assertEqual(plan.intent, "triage_ci_failure_with_root_cause_correlation")
                self.assertTrue(
                    "root_cause_report" in plan.steps[0].payload
                    and plan.steps[0].payload["root_cause_report"]["failure_family"]
                )

                key = latest_root_cause_report_key(str(repo), "feature/fix-health")
                report_entity = runtime.state_graph.get_active_entity(
                    entity_type="Artifact",
                    entity_key=key,
                )
                self.assertIsNotNone(report_entity)
                report_value = report_entity["value"]
                self.assertIn("candidates", report_value)
                self.assertGreaterEqual(report_value.get("confidence", 0), 0.6)
            finally:
                runtime.close()

    def test_repo_delta_refresh_uses_latest_ci_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._setup_repo(root)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            reactor = ZenithCorrelationReactor()
            try:
                ci_event = EventEnvelope(
                    source="ci",
                    source_type="ci.failure",
                    payload={
                        "project": "zenith",
                        "repo_id": str(repo),
                        "branch": "feature/ui-fix",
                        "head_sha": "def456",
                        "report_id": "ci-404",
                        "status": "failed",
                        "summary": "snapshot mismatch in ui/zenith_ui.txt",
                        "error_summary": "snapshot mismatch in ui/zenith_ui.txt",
                        "failed_tests": ["tests/test_ui_snapshot.py::test_snapshot"],
                        "failed_paths": ["ui/zenith_ui.txt"],
                        "implicated_paths": ["ui/zenith_ui.txt"],
                        "zenith_owned": True,
                        "protected_ui_changed": True,
                    },
                )
                runtime.ingest_envelope(ci_event)

                delta_event = EventEnvelope(
                    source="git",
                    source_type="repo.git_delta",
                    payload={
                        "project": "zenith",
                        "repo_id": str(repo),
                        "repo_path": str(repo),
                        "branch": "feature/ui-fix",
                        "head_sha": "def456",
                        "changed_files": ["ui/zenith_ui.txt", "service.py"],
                        "dirty_files": ["ui/zenith_ui.txt"],
                        "protected_ui_changed": True,
                    },
                )
                ingestion = runtime.ingest_envelope(delta_event)
                plans = reactor.propose_plans(runtime, delta_event, ingestion)
                self.assertEqual(len(plans), 1)
                plan = plans[0]
                self.assertEqual(plan.intent, "refresh_root_cause_after_git_delta")
                self.assertTrue(any(step.action_class == "P2" for step in plan.steps))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

