from __future__ import annotations

import json
import unittest
from pathlib import Path


class ImprovementOperatorKnowledgeStackAssetsTests(unittest.TestCase):
    def test_pack_config_paths_and_defaults(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config_path = repo_root / "configs" / "improvement_operator_knowledge_stack.json"
        self.assertTrue(config_path.exists())

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)

        defaults = dict(payload.get("defaults") or {})
        self.assertTrue(bool(defaults.get("auto_retest_lane")))
        self.assertTrue(bool(defaults.get("collect_experiment_debug")))

        feed_jobs = list(payload.get("feed_jobs") or [])
        feedback_jobs = list(payload.get("feedback_jobs") or [])
        experiment_jobs = list(payload.get("experiment_jobs") or [])
        self.assertGreaterEqual(len(feed_jobs), 4)
        self.assertGreaterEqual(len(feedback_jobs), 4)
        self.assertGreaterEqual(len(experiment_jobs), 4)

        for feed in feed_jobs:
            self.assertIsInstance(feed, dict)
            source_path = (config_path.parent / str(feed.get("url") or "")).resolve()
            self.assertTrue(source_path.exists(), msg=f"missing feed source: {source_path}")
            self.assertTrue(bool(str(feed.get("output_path") or "").strip()))

        for job in experiment_jobs:
            self.assertIsInstance(job, dict)
            artifact_path = (config_path.parent / str(job.get("artifact_path") or "")).resolve()
            self.assertTrue(artifact_path.exists(), msg=f"missing artifact: {artifact_path}")
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertIsInstance(artifact, dict)
            baseline = dict((artifact.get("baseline") or {}).get("metrics") or {})
            candidate = dict((artifact.get("candidate") or {}).get("metrics") or {})
            self.assertTrue(bool(baseline))
            self.assertTrue(bool(candidate))

    def test_controlled_matrix_and_hypothesis_template_shape(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        matrix_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "matrices"
            / "controlled_experiment_matrix.json"
        )
        template_path = repo_root / "configs" / "improvement_hypothesis_templates_knowledge_stack.json"

        self.assertTrue(matrix_path.exists())
        self.assertTrue(template_path.exists())

        matrix_payload = json.loads(matrix_path.read_text(encoding="utf-8"))
        scenarios = list(matrix_payload.get("scenarios") or [])
        self.assertGreaterEqual(len(scenarios), 4)
        for scenario in scenarios:
            self.assertIsInstance(scenario, dict)
            self.assertTrue(bool(str(scenario.get("domain") or "").strip()))
            self.assertTrue(bool(str(scenario.get("friction_key") or "").strip()))
            self.assertTrue(bool(str(scenario.get("expected_verdict") or "").strip()))
            artifact_path = (matrix_path.parent / str(scenario.get("artifact_path") or "")).resolve()
            self.assertTrue(artifact_path.exists(), msg=f"missing matrix artifact: {artifact_path}")

        template_payload = json.loads(template_path.read_text(encoding="utf-8"))
        hypotheses = list(template_payload.get("hypotheses") or [])
        self.assertGreaterEqual(len(hypotheses), 8)
        domains = {str((row or {}).get("domain") or "") for row in hypotheses if isinstance(row, dict)}
        self.assertIn("quant_finance", domains)
        self.assertIn("kalshi_weather", domains)
        self.assertIn("fitness_apps", domains)
        self.assertIn("market_ml", domains)

    def test_compact_gate_workflow_snippet_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-gate-status-compact.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("--emit-ci-json-path", content)
        self.assertIn("steps.gate.outputs.blocked_step_count", content)
        self.assertIn("steps.gate.outputs.exit_reason", content)

    def test_active_compact_gate_workflow_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / ".github"
            / "workflows"
            / "improvement-gate-status-compact.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("schedule:", content)
        self.assertIn("--required-label jarvis", content)
        self.assertIn("--required-label needs-review", content)
        self.assertIn("--required-label protected-change", content)
        self.assertIn("--emit-ci-json-path", content)

    def test_codeowner_review_gate_reconciler_script_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "reconcile_codeowner_review_gate.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("--min-collaborators", content)
        self.assertIn("--apply", content)
        self.assertIn("protection/required_pull_request_reviews", content)
        self.assertIn("require_code_owner_reviews", content)

    def test_active_reconcile_codeowner_workflow_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / ".github"
            / "workflows"
            / "reconcile-codeowner-review-gate.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("JARVIS_ADMIN_GH_TOKEN", content)
        self.assertIn("reconcile_codeowner_review_gate.sh", content)
        self.assertIn("codeowner_review_reconcile.json", content)


if __name__ == "__main__":
    unittest.main()
