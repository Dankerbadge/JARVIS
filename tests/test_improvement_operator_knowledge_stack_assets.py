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
        self.assertTrue(bool(defaults.get("verify_matrix_alert_enable")))
        self.assertTrue(bool(defaults.get("knowledge_brief_enable")))
        self.assertTrue(bool(defaults.get("knowledge_delta_alert_enable")))
        self.assertEqual(int(defaults.get("benchmark_stale_runtime_history_window") or 0), 7)
        self.assertEqual(int(defaults.get("benchmark_stale_runtime_repeat_threshold") or 0), 2)
        self.assertEqual(float(defaults.get("benchmark_stale_runtime_rate_ceiling") or 0.0), 0.6)
        self.assertEqual(int(defaults.get("benchmark_stale_runtime_consecutive_runs") or 0), 2)
        self.assertEqual(
            str(defaults.get("seed_domains") or ""),
            "quant_finance,kalshi_weather,fitness_apps,market_ml",
        )
        self.assertEqual(
            str(defaults.get("knowledge_delta_domains") or ""),
            "quant_finance,kalshi_weather,fitness_apps,market_ml",
        )

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

    def test_controlled_contract_alignment_and_environment_consistency(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config_path = repo_root / "configs" / "improvement_operator_knowledge_stack.json"
        matrix_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "matrices"
            / "controlled_experiment_matrix.json"
        )
        template_path = repo_root / "configs" / "improvement_hypothesis_templates_knowledge_stack.json"

        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        matrix_payload = json.loads(matrix_path.read_text(encoding="utf-8"))
        template_payload = json.loads(template_path.read_text(encoding="utf-8"))

        experiment_jobs = [row for row in list(config_payload.get("experiment_jobs") or []) if isinstance(row, dict)]
        canonical_jobs = [row for row in experiment_jobs if not str(row.get("hypothesis_id") or "").strip()]
        scenarios = [row for row in list(matrix_payload.get("scenarios") or []) if isinstance(row, dict)]
        hypotheses = [row for row in list(template_payload.get("hypotheses") or []) if isinstance(row, dict)]

        self.assertGreaterEqual(len(canonical_jobs), 4)
        self.assertGreaterEqual(len(scenarios), 4)

        canonical_pairs = {
            (str(row.get("domain") or "").strip(), str(row.get("friction_key") or "").strip())
            for row in canonical_jobs
            if str(row.get("domain") or "").strip() and str(row.get("friction_key") or "").strip()
        }
        scenario_pairs = {
            (str(row.get("domain") or "").strip(), str(row.get("friction_key") or "").strip())
            for row in scenarios
            if str(row.get("domain") or "").strip() and str(row.get("friction_key") or "").strip()
        }
        hypothesis_pairs = {
            (str(row.get("domain") or "").strip(), str(row.get("friction_key") or "").strip())
            for row in hypotheses
            if str(row.get("domain") or "").strip() and str(row.get("friction_key") or "").strip()
        }

        self.assertEqual(
            canonical_pairs,
            scenario_pairs,
            msg="controlled canonical experiment jobs must match matrix scenarios exactly",
        )
        self.assertTrue(
            scenario_pairs.issubset(hypothesis_pairs),
            msg="each controlled matrix scenario must map to a template hypothesis",
        )

        scenario_by_pair: dict[tuple[str, str], dict] = {}
        for row in scenarios:
            domain = str(row.get("domain") or "").strip()
            friction_key = str(row.get("friction_key") or "").strip()
            if not domain or not friction_key:
                continue
            pair = (domain, friction_key)
            self.assertNotIn(pair, scenario_by_pair, msg=f"duplicate matrix scenario pair: {pair}")
            scenario_by_pair[pair] = row

        for row in canonical_jobs:
            domain = str(row.get("domain") or "").strip()
            friction_key = str(row.get("friction_key") or "").strip()
            if not domain or not friction_key:
                continue
            pair = (domain, friction_key)
            scenario = scenario_by_pair[pair]

            job_artifact_path = (config_path.parent / str(row.get("artifact_path") or "")).resolve()
            matrix_artifact_path = (matrix_path.parent / str(scenario.get("artifact_path") or "")).resolve()
            self.assertEqual(
                job_artifact_path,
                matrix_artifact_path,
                msg=f"matrix/job artifact mismatch for {pair}",
            )
            self.assertTrue(job_artifact_path.exists(), msg=f"missing artifact for canonical job: {job_artifact_path}")

            artifact = json.loads(job_artifact_path.read_text(encoding="utf-8"))
            self.assertIsInstance(artifact, dict)
            artifact_environment = str(artifact.get("environment") or "").strip()
            job_environment = str(row.get("environment") or "").strip()
            self.assertTrue(job_environment, msg=f"missing job environment for {pair}")
            self.assertTrue(artifact_environment, msg=f"missing artifact environment for {pair}")
            self.assertEqual(
                job_environment,
                artifact_environment,
                msg=f"artifact/job environment mismatch for {pair}",
            )

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
        self.assertIn("--fail-on-zero-unlock-ready", content)
        self.assertIn("--zero-unlock-ready-exit-code", content)
        self.assertIn("--emit-ci-json-path", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn('--summary-heading "Gate Status Compact"', content)
        self.assertIn("id: gate", content)
        self.assertIn("unlock_ready_step_count", content)
        self.assertIn("first_unlock_ready_command", content)
        self.assertIn("first_acknowledge_command", content)
        self.assertIn("Zero unlock-ready branch", content)
        self.assertIn("zero_unlock_ready_steps", content)
        self.assertIn("steps.gate.outputs.exit_reason != 'zero_unlock_ready_steps'", content)
        self.assertIn("steps.gate.outputs.blocked_step_count", content)
        self.assertIn("steps.gate.outputs.exit_reason", content)
        self.assertNotIn("python3 - <<'PY'", content)

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
        self.assertIn("--fail-on-zero-unlock-ready", content)
        self.assertIn("--zero-unlock-ready-exit-code", content)
        self.assertIn("--emit-ci-json-path", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn('--summary-heading "Gate Status Compact"', content)
        self.assertIn("id: gate", content)
        self.assertIn("unlock_ready_step_count", content)
        self.assertIn("first_unlock_ready_command", content)
        self.assertIn("first_acknowledge_command", content)
        self.assertIn("Zero unlock-ready branch", content)
        self.assertIn("zero_unlock_ready_steps", content)
        self.assertIn("steps.gate.outputs.exit_reason != 'zero_unlock_ready_steps'", content)
        self.assertNotIn("compact_artifact_missing", content)
        self.assertNotIn("python3 - <<'PY'", content)
        self.assertIn("output/ci/gate_status_all_summary.md", content)

    def test_unlock_ready_contract_consistency_across_compact_gate_and_bootstrap_workflows(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_paths = [
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-gate-status-compact.yml",
            repo_root / ".github" / "workflows" / "improvement-gate-status-compact.yml",
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-knowledge-bootstrap-route.yml",
            repo_root / ".github" / "workflows" / "improvement-knowledge-bootstrap-route.yml",
        ]
        compact_gate_contract_keys = [
            "first_unlock_ready_command",
            "first_acknowledge_command",
            "unlock_ready_step_count",
            "blocked_step_count",
            "exit_reason",
        ]
        bootstrap_route_contract_keys = [
            "verify_matrix_first_unlock_ready_command",
            "verify_matrix_recheck_command",
            "missing_domain_count",
            "verify_matrix_first_missing_domain",
        ]
        for workflow_path in workflow_paths:
            self.assertTrue(workflow_path.exists(), msg=f"missing workflow: {workflow_path}")
            content = workflow_path.read_text(encoding="utf-8")
            contract_keys = (
                bootstrap_route_contract_keys
                if "knowledge-bootstrap-route" in workflow_path.name
                else compact_gate_contract_keys
            )
            for key in contract_keys:
                self.assertIn(
                    key,
                    content,
                    msg=f"{workflow_path} missing unlock-ready contract key: {key}",
                )

    def test_knowledge_bootstrap_route_workflow_template_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-knowledge-bootstrap-route.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("schedule:", content)
        self.assertIn("run_improvement_operator_cycle.sh", content)
        self.assertIn("actions: read", content)
        self.assertIn("Prepare benchmark restore paths", content)
        self.assertIn("Resolve prior successful route run", content)
        self.assertIn("id: prior_route_run", content)
        self.assertIn("actions/workflows/improvement-knowledge-bootstrap-route.yml/runs", content)
        self.assertIn("actions/download-artifact@v5", content)
        self.assertIn("run-id: ${{ steps.prior_route_run.outputs.run_id }}", content)
        self.assertIn("pattern: knowledge-bootstrap-route", content)
        self.assertIn("path: output/ci/prior_route_artifact", content)
        self.assertIn("merge-multiple: true", content)
        self.assertIn("Restore prior benchmark report artifact", content)
        self.assertIn("operator_cycle/benchmark_frustrations_report.json", content)
        self.assertIn("benchmark_frustrations_report.json", content)
        self.assertIn("Restore prior benchmark stale fallback history artifact", content)
        self.assertIn("benchmark_stale_fallback_history.jsonl", content)
        self.assertNotIn("actions/cache/restore@v4", content)
        self.assertNotIn("actions/cache/save@v4", content)
        self.assertIn("knowledge_bootstrap_route.json", content)
        self.assertIn("steps.route.outputs.route", content)
        self.assertIn("route_blocking", content)
        self.assertIn("steps.route.outputs.route_blocking", content)
        self.assertIn("steps.route.outputs.route_blocking == '1'", content)
        self.assertIn("benchmark_stale_fallback", content)
        self.assertIn("benchmark_stale_reason", content)
        self.assertIn("benchmark_stale_next_action", content)
        self.assertIn("Benchmark stale fallback branch", content)
        self.assertIn("steps.route.outputs.benchmark_stale_fallback == '1'", content)
        self.assertIn("id: route_initial", content)
        self.assertIn("improvement knowledge-bootstrap-route-outputs", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn("--summary-heading", content)
        self.assertIn("--summary-include-artifact-source", content)
        self.assertIn("Benchmark stale fallback runtime alert", content)
        self.assertIn("id: benchmark_stale", content)
        self.assertIn("run_improvement_benchmark_stale_fallback_runtime_alert.sh", content)
        self.assertIn('--summary-heading "Benchmark Stale Fallback Runtime Alert"', content)
        self.assertIn("benchmark_stale_runtime_should_alert", content)
        self.assertIn("benchmark_stale_runtime_alert_created", content)
        self.assertIn("benchmark_stale_runtime_error", content)
        self.assertIn("steps.route.outputs.benchmark_stale_history_window", content)
        self.assertIn("steps.route.outputs.benchmark_stale_repeat_threshold", content)
        self.assertIn("steps.route.outputs.benchmark_stale_rate_ceiling", content)
        self.assertIn("steps.route.outputs.benchmark_stale_rate_consecutive_runs", content)
        self.assertIn("--rate-ceiling", content)
        self.assertIn("--consecutive-runs", content)
        self.assertIn("Fail on benchmark stale fallback alert creation error", content)
        self.assertIn("Benchmark stale fallback no-op branch", content)
        self.assertIn("benchmark_stale_recent_count", content)
        self.assertIn("benchmark_stale_repeat_threshold", content)
        self.assertIn("Fail on benchmark stale fallback rate gate", content)
        self.assertIn("benchmark_stale_runtime_rate_gate_blocking", content)
        self.assertIn("benchmark_stale_runtime_rate_gate_reason", content)
        self.assertIn("benchmark_stale_runtime_latest_rolling_rate", content)
        self.assertIn("knowledge_bootstrap_route_effective_outputs.json", content)
        self.assertIn("Bootstrap follow-up rerun", content)
        self.assertIn("improvement knowledge-bootstrap-followup-rerun", content)
        self.assertIn("knowledge_bootstrap_followup_rerun.json", content)
        self.assertIn('--summary-heading "Bootstrap Follow-Up"', content)
        self.assertNotIn("missing_knowledge_bootstrap_followup_rerun_output", content)
        self.assertIn("knowledge_bootstrap_route_post_bootstrap.json", content)
        self.assertIn("--artifact-source", content)
        self.assertIn("--knowledge-brief-enable", content)
        self.assertIn("--knowledge-delta-alert-enable", content)
        self.assertIn("Extract evidence lookup batch outputs", content)
        self.assertIn("id: evidence_lookup_batch", content)
        self.assertIn("run_improvement_evidence_lookup_batch_outputs.sh", content)
        self.assertIn("evidence_lookup_batch_outputs.json", content)
        self.assertIn("evidence_lookup_ready", content)
        self.assertIn("evidence_lookup_command", content)
        self.assertIn("Run evidence lookup batch", content)
        self.assertIn("Open evidence lookup runtime alert", content)
        self.assertIn("run_improvement_evidence_lookup_runtime_alert.sh", content)
        self.assertIn('--summary-heading "Evidence Lookup Runtime Alert"', content)
        self.assertIn("evidence_lookup_runtime_alert.json", content)
        self.assertIn("evidence_lookup_missing_count", content)
        self.assertIn("evidence_lookup_runtime_interrupt_id", content)
        self.assertIn("evidence_lookup_runtime_first_repair_command", content)
        self.assertIn("Fail on unresolved evidence lookup IDs", content)
        self.assertIn("Evidence lookup batch no-op branch", content)
        self.assertIn("Guardrail gate", content)
        self.assertIn("improvement verify-matrix-guardrail-gate", content)
        self.assertIn('--summary-heading "Operator Guardrail Gate"', content)
        self.assertIn("--emit-github-output", content)
        self.assertIn("--strict", content)
        self.assertNotIn("invalid_verify_matrix_guardrail_gate_payload:expected_json_object", content)
        self.assertIn("Verify matrix coverage compact gate", content)
        self.assertIn("id: verify_matrix_compact", content)
        self.assertIn("improvement verify-matrix-compact", content)
        self.assertIn('--summary-heading "Verify Matrix Compact Coverage"', content)
        self.assertIn("--summary-include-markdown", content)
        self.assertIn("--emit-github-output", content)
        self.assertNotIn("invalid_verify_matrix_compact_payload:expected_json_object", content)
        self.assertIn("verify_matrix_compact.json", content)
        self.assertIn("verify_matrix_compact.md", content)
        self.assertIn("verify_matrix_compact_path", content)
        self.assertIn("verify_matrix_compact_status", content)
        self.assertIn("verify_matrix_first_missing_domain", content)
        self.assertIn("missing_domain_count", content)
        self.assertIn("missing_domains_csv", content)
        self.assertIn("verify_matrix_recheck_command", content)
        self.assertIn("verify_matrix_first_unlock_ready_command", content)
        self.assertIn("first_repair_command", content)
        self.assertIn("Open verify matrix coverage interrupt alert", content)
        self.assertIn("improvement verify-matrix-coverage-alert", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn('--summary-heading "Verify Matrix Coverage Interrupt Alert"', content)
        self.assertNotIn(
            "invalid_verify_matrix_coverage_alert_payload:expected_json_object",
            content,
        )
        self.assertIn("verify_matrix_coverage_alert.json", content)
        self.assertIn("improvement.verify_matrix_coverage_alert_created", content)
        self.assertIn("verify_matrix_coverage_interrupt_id", content)
        self.assertIn(
            "steps.verify_matrix_coverage_alert.outputs.verify_matrix_coverage_interrupt_id",
            content,
        )
        self.assertIn("verify_matrix_coverage_first_repair_command", content)
        self.assertIn("coverage_interrupt_id", content)
        self.assertIn("Fail on verify matrix coverage gaps", content)
        self.assertIn("Collect debug trace artifacts", content)
        self.assertIn("output/ci/debug_runs", content)
        self.assertIn("output/ci/knowledge_snapshots", content)

    def test_codeowner_review_gate_reconciler_script_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "reconcile_codeowner_review_gate.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("--min-collaborators", content)
        self.assertIn("--apply", content)
        self.assertIn("--required-status-check", content)
        self.assertIn("--required-status-check-strict", content)
        self.assertIn("--source-workflow-run-id", content)
        self.assertIn("--source-workflow-run-conclusion", content)
        self.assertIn("--source-workflow-name", content)
        self.assertIn("--source-workflow-event", content)
        self.assertIn("--source-workflow-run-url", content)
        self.assertIn("protection/required_pull_request_reviews", content)
        self.assertIn("protection/required_status_checks", content)
        self.assertIn("require_code_owner_reviews", content)
        self.assertIn("desired_required_approving_review_count", content)
        self.assertIn("current_required_approving_review_count", content)
        self.assertIn("desired_require_last_push_approval", content)
        self.assertIn("current_require_last_push_approval", content)
        self.assertIn("required_status_checks", content)
        self.assertIn("desired_contexts", content)
        self.assertIn("strict_mode", content)
        self.assertIn("reconcile_provenance", content)

    def test_knowledge_bootstrap_route_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_knowledge_bootstrap_route.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement knowledge-bootstrap-route", content)
        self.assertIn("--report-path", content)
        self.assertIn("--output-path", content)

    def test_benchmark_stale_runtime_alert_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_benchmark_stale_fallback_runtime_alert.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement benchmark-stale-fallback-runtime-alert", content)
        self.assertIn("--route-output-path", content)
        self.assertIn("--history-window", content)
        self.assertIn("--repeat-threshold", content)
        self.assertIn("--rate-ceiling", content)
        self.assertIn("--consecutive-runs", content)
        self.assertIn("--rerun-command", content)

    def test_evidence_lookup_runtime_alert_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_evidence_lookup_runtime_alert.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement evidence-lookup-runtime-alert", content)
        self.assertIn("--report-path", content)
        self.assertIn("--output-path", content)
        self.assertIn("--rerun-command", content)
        self.assertIn("--history-path", content)
        self.assertIn("--history-window", content)

    def test_evidence_lookup_batch_outputs_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_evidence_lookup_batch_outputs.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement evidence-lookup-batch-outputs", content)
        self.assertIn("--report-path", content)
        self.assertIn("--report-source", content)
        self.assertIn("--output-path", content)
        self.assertIn("--strict", content)

    def test_evidence_lane_smoke_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_evidence_lane_smoke.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("run_improvement_evidence_lookup_batch_outputs.sh", content)
        self.assertIn("run_improvement_evidence_lookup_runtime_alert.sh", content)
        self.assertIn("evidence_lane_smoke_summary.json", content)
        self.assertIn("--workspace-dir", content)
        self.assertIn("--strict", content)
        self.assertIn("--no-strict", content)

    def test_reconcile_codeowner_review_gate_runtime_alert_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_reconcile_codeowner_review_gate_runtime_alert.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement reconcile-codeowner-review-gate-runtime-alert", content)
        self.assertIn("--report-path", content)
        self.assertIn("--output-path", content)
        self.assertIn("--rerun-command", content)
        self.assertIn("--strict", content)

    def test_kalshi_leaderboard_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_kalshi_leaderboard.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement fitness-leaderboard", content)
        self.assertIn("JARVIS_IMPROVEMENT_KALSHI_LEADERBOARD_OUTPUT_PATH", content)
        self.assertIn("JARVIS_IMPROVEMENT_KALSHI_LEADERBOARD_LOOKBACK_DAYS", content)
        self.assertIn("JARVIS_IMPROVEMENT_KALSHI_LEADERBOARD_DOMAIN", content)
        self.assertIn("JARVIS_IMPROVEMENT_KALSHI_LEADERBOARD_SOURCE", content)
        self.assertIn("--domain)", content)
        self.assertIn("--source)", content)
        self.assertIn("--min-cross-app-count)", content)
        self.assertIn("kalshi_weather", content)
        self.assertIn("kalshi_trade_journal", content)
        self.assertIn("--min-cross-app-count", content)

    def test_quant_leaderboard_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_quant_leaderboard.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement fitness-leaderboard", content)
        self.assertIn("JARVIS_IMPROVEMENT_QUANT_LEADERBOARD_OUTPUT_PATH", content)
        self.assertIn("JARVIS_IMPROVEMENT_QUANT_LEADERBOARD_LOOKBACK_DAYS", content)
        self.assertIn("JARVIS_IMPROVEMENT_QUANT_LEADERBOARD_DOMAIN", content)
        self.assertIn("JARVIS_IMPROVEMENT_QUANT_LEADERBOARD_SOURCE", content)
        self.assertIn("--domain)", content)
        self.assertIn("--source)", content)
        self.assertIn("--min-cross-app-count)", content)
        self.assertIn("quant_finance", content)
        self.assertIn("research_notes", content)
        self.assertIn("--min-cross-app-count", content)

    def test_market_ml_leaderboard_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_market_ml_leaderboard.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("improvement fitness-leaderboard", content)
        self.assertIn("JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_OUTPUT_PATH", content)
        self.assertIn("JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_LOOKBACK_DAYS", content)
        self.assertIn("JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_DOMAIN", content)
        self.assertIn("JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_SOURCE", content)
        self.assertIn("--domain)", content)
        self.assertIn("--source)", content)
        self.assertIn("--min-cross-app-count)", content)
        self.assertIn("market_ml", content)
        self.assertIn("ml_incident_log", content)
        self.assertIn("--min-cross-app-count", content)

    def test_domain_smoke_wrapper_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "run_improvement_domain_smoke.sh"
        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("run_improvement_pull_feeds.sh", content)
        self.assertIn("run_improvement_seed_from_leaderboard.sh", content)
        self.assertIn("run_improvement_fitness_leaderboard.sh", content)
        self.assertIn("run_improvement_kalshi_leaderboard.sh", content)
        self.assertIn("run_improvement_quant_leaderboard.sh", content)
        self.assertIn("run_improvement_market_ml_leaderboard.sh", content)
        self.assertIn("missing_feedback_job_for_domain", content)
        self.assertIn("domain_smoke_", content)
        self.assertIn("_smoke_summary.json", content)

    def test_domain_smoke_nightly_workflow_template_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-domain-smoke-nightly.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("matrix:", content)
        self.assertIn("quant_finance", content)
        self.assertIn("kalshi_weather", content)
        self.assertIn("fitness_apps", content)
        self.assertIn("market_ml", content)
        self.assertIn("run_improvement_domain_smoke.sh", content)
        self.assertIn("--allow-missing", content)
        self.assertIn("improvement domain-smoke-outputs", content)
        self.assertIn('--summary-heading "Domain Smoke"', content)
        self.assertIn("Open domain smoke interrupt alert", content)
        self.assertIn("improvement domain-smoke-runtime-alert", content)
        self.assertIn('--summary-heading "Domain Smoke Interrupt Alert"', content)
        self.assertIn("SMOKE_REASON", content)
        self.assertIn("steps.smoke_alert.outputs.interrupt_id", content)
        self.assertIn("domain-smoke-aggregate", content)
        self.assertIn("actions/download-artifact@v5", content)
        self.assertIn("pattern: domain-smoke-*", content)
        self.assertIn("Build cross-domain smoke summary", content)
        self.assertIn("improvement domain-smoke-cross-domain-compact", content)
        self.assertIn('--summary-heading "Domain Smoke Cross-Domain Summary"', content)
        self.assertIn("domain_smoke_cross_domain_summary.json", content)
        self.assertIn("domain-smoke-cross-domain-summary", content)
        self.assertIn("Open cross-domain smoke interrupt alert", content)
        self.assertIn("domain_smoke_cross_domain_alert.json", content)
        self.assertIn("improvement domain-smoke-cross-domain-runtime-alert", content)
        self.assertIn('--summary-heading "Domain Smoke Cross-Domain Interrupt Alert"', content)
        self.assertIn("steps.aggregate.outputs.cross_domain_status == 'warning'", content)
        self.assertIn("WARNING_COUNT", content)
        self.assertIn("BLOCKING_COUNT", content)
        self.assertIn("TOP_DOMAIN", content)
        self.assertIn("TOP_RISK_SCORE", content)
        self.assertIn("cross_domain_status", content)
        self.assertIn("Collect debug trace artifacts", content)
        self.assertIn("actions/upload-artifact@v7", content)
        self.assertIn("steps.smoke.outputs.smoke_blocking == '1'", content)

    def test_evidence_lane_smoke_workflow_template_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-evidence-lane-smoke.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("pull_request:", content)
        self.assertIn("run_improvement_evidence_lane_smoke.sh", content)
        self.assertIn("--workspace-dir ./output/ci/evidence_lane_smoke", content)
        self.assertIn("--strict", content)
        self.assertIn("Upload evidence lane smoke artifacts", content)
        self.assertIn("actions/upload-artifact@v7", content)
        self.assertIn("evidence-lane-smoke", content)
        self.assertIn("output/ci/evidence_lane_smoke/", content)

    def test_reconcile_codeowner_review_gate_drift_check_workflow_template_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-reconcile-codeowner-review-gate-drift-check.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("Dry-run code-owner review gate reconciliation", content)
        self.assertIn("reconcile_codeowner_review_gate.sh", content)
        self.assertIn("--required-status-check gate-status", content)
        self.assertIn("--required-status-check evidence-lane-smoke", content)
        self.assertIn("--required-status-check release-hygiene", content)
        self.assertIn("--required-status-check-strict true", content)
        self.assertIn("--source-workflow-run-id", content)
        self.assertIn("--source-workflow-run-conclusion", content)
        self.assertIn("--source-workflow-name", content)
        self.assertIn("--source-workflow-event", content)
        self.assertIn("--source-workflow-run-url", content)
        self.assertIn("${{ github.run_id }}", content)
        self.assertIn("${{ github.workflow }}", content)
        self.assertIn("${{ github.event_name }}", content)
        self.assertIn("https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }}", content)
        self.assertNotIn("--apply \\", content)
        self.assertIn("improvement reconcile-codeowner-review-gate-outputs", content)
        self.assertIn('--summary-heading "Codeowner Review Gate Drift Check"', content)
        self.assertIn("Open required-status-check drift alert", content)
        self.assertIn("run_improvement_reconcile_codeowner_review_gate_runtime_alert.sh", content)
        self.assertIn('--summary-heading "Codeowner Review Gate Drift Alert"', content)
        self.assertIn("codeowner_review_reconcile_drift_alert.json", content)
        self.assertIn("codeowner-review-reconcile-drift-check", content)
        self.assertIn("codeowner_review_drift_source_workflow_run_id", content)
        self.assertIn("codeowner_review_drift_source_workflow_run_conclusion", content)
        self.assertIn("codeowner_review_drift_source_workflow_name", content)
        self.assertIn("codeowner_review_drift_source_workflow_event", content)
        self.assertIn("codeowner_review_drift_source_workflow_run_url", content)
        self.assertIn("Fail on drift alert creation error", content)
        self.assertIn("Fail on required-status-check drift", content)

    def test_controlled_matrix_nightly_workflow_template_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / "configs"
            / "improvement_operator_knowledge_stack"
            / "github-actions-controlled-matrix-nightly.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("run_improvement_daily_pipeline.sh", content)
        self.assertIn("run_improvement_verify_matrix_alert.sh", content)
        self.assertIn("controlled_experiment_matrix.json", content)
        self.assertIn("verify_matrix_alert_report.json", content)
        self.assertIn("id: matrix", content)
        self.assertIn("improvement controlled-matrix-compact", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn('--summary-heading "Controlled Matrix Drift Summary"', content)
        self.assertIn("matrix_status", content)
        self.assertIn("DRIFT_SEVERITY", content)
        self.assertIn("first_repair_command", content)
        self.assertIn("first_suggested_action", content)
        self.assertIn("Open controlled matrix runtime interrupt alert", content)
        self.assertIn("improvement controlled-matrix-runtime-alert", content)
        self.assertIn('--summary-heading "Controlled Matrix Runtime Interrupt Alert"', content)
        self.assertIn("MATRIX_SUMMARY_PATH", content)
        self.assertIn("steps.matrix.outputs.matrix_interrupt_id == 'none'", content)
        self.assertIn("matrix_runtime_interrupt_id", content)
        self.assertIn("matrix_runtime_first_repair_command", content)
        self.assertIn("Collect debug trace artifacts", content)
        self.assertIn("controlled-matrix-validation", content)
        self.assertIn("Fail on controlled matrix drift", content)
        self.assertNotIn("python3 - <<'PY'", content)

    def test_active_knowledge_bootstrap_route_workflow_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / ".github"
            / "workflows"
            / "improvement-knowledge-bootstrap-route.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("run_improvement_operator_cycle.sh", content)
        self.assertIn("actions: read", content)
        self.assertIn("Prepare benchmark restore paths", content)
        self.assertIn("Resolve prior successful route run", content)
        self.assertIn("id: prior_route_run", content)
        self.assertIn("actions/workflows/improvement-knowledge-bootstrap-route.yml/runs", content)
        self.assertIn("actions/download-artifact@v5", content)
        self.assertIn("run-id: ${{ steps.prior_route_run.outputs.run_id }}", content)
        self.assertIn("pattern: knowledge-bootstrap-route", content)
        self.assertIn("path: output/ci/prior_route_artifact", content)
        self.assertIn("merge-multiple: true", content)
        self.assertIn("Restore prior benchmark report artifact", content)
        self.assertIn("operator_cycle/benchmark_frustrations_report.json", content)
        self.assertIn("benchmark_frustrations_report.json", content)
        self.assertIn("Restore prior benchmark stale fallback history artifact", content)
        self.assertIn("benchmark_stale_fallback_history.jsonl", content)
        self.assertNotIn("actions/cache/restore@v4", content)
        self.assertNotIn("actions/cache/save@v4", content)
        self.assertIn("knowledge_bootstrap_route.json", content)
        self.assertIn("steps.route.outputs.route", content)
        self.assertIn("route_blocking", content)
        self.assertIn("steps.route.outputs.route_blocking", content)
        self.assertIn("steps.route.outputs.route_blocking == '1'", content)
        self.assertIn("benchmark_stale_fallback", content)
        self.assertIn("benchmark_stale_reason", content)
        self.assertIn("benchmark_stale_next_action", content)
        self.assertIn("Benchmark stale fallback branch", content)
        self.assertIn("steps.route.outputs.benchmark_stale_fallback == '1'", content)
        self.assertIn("id: route_initial", content)
        self.assertIn("improvement knowledge-bootstrap-route-outputs", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn("--summary-heading", content)
        self.assertIn("--summary-include-artifact-source", content)
        self.assertIn("Benchmark stale fallback runtime alert", content)
        self.assertIn("id: benchmark_stale", content)
        self.assertIn("run_improvement_benchmark_stale_fallback_runtime_alert.sh", content)
        self.assertIn('--summary-heading "Benchmark Stale Fallback Runtime Alert"', content)
        self.assertIn("benchmark_stale_runtime_should_alert", content)
        self.assertIn("benchmark_stale_runtime_alert_created", content)
        self.assertIn("benchmark_stale_runtime_error", content)
        self.assertIn("steps.route.outputs.benchmark_stale_history_window", content)
        self.assertIn("steps.route.outputs.benchmark_stale_repeat_threshold", content)
        self.assertIn("steps.route.outputs.benchmark_stale_rate_ceiling", content)
        self.assertIn("steps.route.outputs.benchmark_stale_rate_consecutive_runs", content)
        self.assertIn("--rate-ceiling", content)
        self.assertIn("--consecutive-runs", content)
        self.assertIn("Fail on benchmark stale fallback alert creation error", content)
        self.assertIn("Benchmark stale fallback no-op branch", content)
        self.assertIn("benchmark_stale_recent_count", content)
        self.assertIn("benchmark_stale_repeat_threshold", content)
        self.assertIn("Fail on benchmark stale fallback rate gate", content)
        self.assertIn("benchmark_stale_runtime_rate_gate_blocking", content)
        self.assertIn("benchmark_stale_runtime_rate_gate_reason", content)
        self.assertIn("benchmark_stale_runtime_latest_rolling_rate", content)
        self.assertIn("knowledge_bootstrap_route_effective_outputs.json", content)
        self.assertIn("Bootstrap follow-up rerun", content)
        self.assertIn("improvement knowledge-bootstrap-followup-rerun", content)
        self.assertIn("knowledge_bootstrap_followup_rerun.json", content)
        self.assertIn('--summary-heading "Bootstrap Follow-Up"', content)
        self.assertNotIn("missing_knowledge_bootstrap_followup_rerun_output", content)
        self.assertIn("knowledge_bootstrap_route_post_bootstrap.json", content)
        self.assertIn("--artifact-source", content)
        self.assertIn("--knowledge-brief-enable", content)
        self.assertIn("--knowledge-delta-alert-enable", content)
        self.assertIn("Extract evidence lookup batch outputs", content)
        self.assertIn("id: evidence_lookup_batch", content)
        self.assertIn("run_improvement_evidence_lookup_batch_outputs.sh", content)
        self.assertIn("evidence_lookup_batch_outputs.json", content)
        self.assertIn("evidence_lookup_ready", content)
        self.assertIn("evidence_lookup_command", content)
        self.assertIn("Run evidence lookup batch", content)
        self.assertIn("Open evidence lookup runtime alert", content)
        self.assertIn("run_improvement_evidence_lookup_runtime_alert.sh", content)
        self.assertIn('--summary-heading "Evidence Lookup Runtime Alert"', content)
        self.assertIn("evidence_lookup_runtime_alert.json", content)
        self.assertIn("evidence_lookup_missing_count", content)
        self.assertIn("evidence_lookup_runtime_interrupt_id", content)
        self.assertIn("evidence_lookup_runtime_first_repair_command", content)
        self.assertIn("Fail on unresolved evidence lookup IDs", content)
        self.assertIn("Evidence lookup batch no-op branch", content)
        self.assertIn("Guardrail gate", content)
        self.assertIn("improvement verify-matrix-guardrail-gate", content)
        self.assertIn('--summary-heading "Operator Guardrail Gate"', content)
        self.assertIn("--emit-github-output", content)
        self.assertIn("--strict", content)
        self.assertNotIn("invalid_verify_matrix_guardrail_gate_payload:expected_json_object", content)
        self.assertIn("Verify matrix coverage compact gate", content)
        self.assertIn("id: verify_matrix_compact", content)
        self.assertIn("improvement verify-matrix-compact", content)
        self.assertIn('--summary-heading "Verify Matrix Compact Coverage"', content)
        self.assertIn("--summary-include-markdown", content)
        self.assertIn("--emit-github-output", content)
        self.assertNotIn("invalid_verify_matrix_compact_payload:expected_json_object", content)
        self.assertIn("verify_matrix_compact.json", content)
        self.assertIn("verify_matrix_compact.md", content)
        self.assertIn("verify_matrix_compact_path", content)
        self.assertIn("verify_matrix_compact_status", content)
        self.assertIn("verify_matrix_first_missing_domain", content)
        self.assertIn("missing_domain_count", content)
        self.assertIn("missing_domains_csv", content)
        self.assertIn("verify_matrix_recheck_command", content)
        self.assertIn("verify_matrix_first_unlock_ready_command", content)
        self.assertIn("first_repair_command", content)
        self.assertIn("Open verify matrix coverage interrupt alert", content)
        self.assertIn("improvement verify-matrix-coverage-alert", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn('--summary-heading "Verify Matrix Coverage Interrupt Alert"', content)
        self.assertNotIn(
            "invalid_verify_matrix_coverage_alert_payload:expected_json_object",
            content,
        )
        self.assertIn("verify_matrix_coverage_alert.json", content)
        self.assertIn("improvement.verify_matrix_coverage_alert_created", content)
        self.assertIn("verify_matrix_coverage_interrupt_id", content)
        self.assertIn(
            "steps.verify_matrix_coverage_alert.outputs.verify_matrix_coverage_interrupt_id",
            content,
        )
        self.assertIn("verify_matrix_coverage_first_repair_command", content)
        self.assertIn("coverage_interrupt_id", content)
        self.assertIn("Fail on verify matrix coverage gaps", content)
        self.assertIn("Collect debug trace artifacts", content)
        self.assertIn("output/ci/debug_runs", content)
        self.assertIn("output/ci/knowledge_snapshots", content)

    def test_active_domain_smoke_nightly_workflow_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / ".github"
            / "workflows"
            / "improvement-domain-smoke-nightly.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("matrix:", content)
        self.assertIn("quant_finance", content)
        self.assertIn("kalshi_weather", content)
        self.assertIn("fitness_apps", content)
        self.assertIn("market_ml", content)
        self.assertIn("run_improvement_domain_smoke.sh", content)
        self.assertIn("--allow-missing", content)
        self.assertIn("improvement domain-smoke-outputs", content)
        self.assertIn('--summary-heading "Domain Smoke"', content)
        self.assertIn("Open domain smoke interrupt alert", content)
        self.assertIn("improvement domain-smoke-runtime-alert", content)
        self.assertIn('--summary-heading "Domain Smoke Interrupt Alert"', content)
        self.assertIn("SMOKE_REASON", content)
        self.assertIn("steps.smoke_alert.outputs.interrupt_id", content)
        self.assertIn("domain-smoke-aggregate", content)
        self.assertIn("actions/download-artifact@v5", content)
        self.assertIn("pattern: domain-smoke-*", content)
        self.assertIn("Build cross-domain smoke summary", content)
        self.assertIn("improvement domain-smoke-cross-domain-compact", content)
        self.assertIn('--summary-heading "Domain Smoke Cross-Domain Summary"', content)
        self.assertIn("domain_smoke_cross_domain_summary.json", content)
        self.assertIn("domain-smoke-cross-domain-summary", content)
        self.assertIn("Open cross-domain smoke interrupt alert", content)
        self.assertIn("domain_smoke_cross_domain_alert.json", content)
        self.assertIn("improvement domain-smoke-cross-domain-runtime-alert", content)
        self.assertIn('--summary-heading "Domain Smoke Cross-Domain Interrupt Alert"', content)
        self.assertIn("steps.aggregate.outputs.cross_domain_status == 'warning'", content)
        self.assertIn("WARNING_COUNT", content)
        self.assertIn("BLOCKING_COUNT", content)
        self.assertIn("TOP_DOMAIN", content)
        self.assertIn("TOP_RISK_SCORE", content)
        self.assertIn("cross_domain_status", content)
        self.assertIn("Collect debug trace artifacts", content)
        self.assertIn("actions/upload-artifact@v7", content)
        self.assertIn("steps.smoke.outputs.smoke_blocking == '1'", content)

    def test_active_evidence_lane_smoke_workflow_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / ".github"
            / "workflows"
            / "improvement-evidence-lane-smoke.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("pull_request:", content)
        self.assertIn("run_improvement_evidence_lane_smoke.sh", content)
        self.assertIn("--workspace-dir ./output/ci/evidence_lane_smoke", content)
        self.assertIn("--strict", content)
        self.assertIn("Upload evidence lane smoke artifacts", content)
        self.assertIn("actions/upload-artifact@v7", content)
        self.assertIn("evidence-lane-smoke", content)
        self.assertIn("output/ci/evidence_lane_smoke/", content)

    def test_active_controlled_matrix_nightly_workflow_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / ".github"
            / "workflows"
            / "improvement-controlled-matrix-nightly.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("run_improvement_daily_pipeline.sh", content)
        self.assertIn("run_improvement_verify_matrix_alert.sh", content)
        self.assertIn("controlled_experiment_matrix.json", content)
        self.assertIn("verify_matrix_alert_report.json", content)
        self.assertIn("id: matrix", content)
        self.assertIn("improvement controlled-matrix-compact", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn('--summary-heading "Controlled Matrix Drift Summary"', content)
        self.assertIn("matrix_status", content)
        self.assertIn("DRIFT_SEVERITY", content)
        self.assertIn("first_repair_command", content)
        self.assertIn("first_suggested_action", content)
        self.assertIn("Open controlled matrix runtime interrupt alert", content)
        self.assertIn("improvement controlled-matrix-runtime-alert", content)
        self.assertIn('--summary-heading "Controlled Matrix Runtime Interrupt Alert"', content)
        self.assertIn("MATRIX_SUMMARY_PATH", content)
        self.assertIn("steps.matrix.outputs.matrix_interrupt_id == 'none'", content)
        self.assertIn("matrix_runtime_interrupt_id", content)
        self.assertIn("matrix_runtime_first_repair_command", content)
        self.assertIn("Collect debug trace artifacts", content)
        self.assertIn("controlled-matrix-validation", content)
        self.assertIn("Fail on controlled matrix drift", content)
        self.assertNotIn("python3 - <<'PY'", content)

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
        self.assertNotIn("workflow_dispatch:", content)
        self.assertIn("workflow_run:", content)
        self.assertIn("reconcile-codeowner-review-gate-drift-check", content)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", content)
        self.assertIn("Guard reconcile trigger event", content)
        self.assertIn('if [[ "${EVENT_NAME}" != "workflow_run" ]]; then', content)
        self.assertIn("Unexpected reconcile trigger event", content)
        self.assertIn("JARVIS_ADMIN_GH_TOKEN", content)
        self.assertIn("reconcile_codeowner_review_gate.sh", content)
        self.assertIn("--required-status-check gate-status", content)
        self.assertIn("--required-status-check evidence-lane-smoke", content)
        self.assertIn("--required-status-check release-hygiene", content)
        self.assertIn("--required-status-check-strict true", content)
        self.assertIn("--source-workflow-run-id", content)
        self.assertIn("--source-workflow-run-conclusion", content)
        self.assertIn("--source-workflow-name", content)
        self.assertIn("--source-workflow-event", content)
        self.assertIn("--source-workflow-run-url", content)
        self.assertIn("codeowner_review_reconcile.json", content)
        self.assertIn("improvement reconcile-codeowner-review-gate-outputs", content)
        self.assertIn("--emit-github-output", content)
        self.assertIn("current_required_approving_review_count", content)
        self.assertIn("desired_required_approving_review_count", content)
        self.assertIn("current_require_last_push_approval", content)
        self.assertIn("desired_require_last_push_approval", content)
        self.assertIn("current_required_status_checks_csv", content)
        self.assertIn("desired_required_status_checks_csv", content)
        self.assertIn("required_status_checks_change_needed", content)
        self.assertIn("source_workflow_run_id", content)
        self.assertIn("source_workflow_run_conclusion", content)
        self.assertIn("source_workflow_name", content)
        self.assertIn("source_workflow_event", content)
        self.assertIn("source_workflow_run_url", content)
        self.assertNotIn("python3 - <<'PY'", content)

    def test_active_reconcile_codeowner_drift_check_workflow_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_path = (
            repo_root
            / ".github"
            / "workflows"
            / "reconcile-codeowner-review-gate-drift-check.yml"
        )
        self.assertTrue(workflow_path.exists())
        content = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("schedule:", content)
        self.assertIn("JARVIS_ADMIN_GH_TOKEN", content)
        self.assertIn("Dry-run code-owner review gate reconciliation", content)
        self.assertIn("reconcile_codeowner_review_gate.sh", content)
        self.assertIn("--required-status-check gate-status", content)
        self.assertIn("--required-status-check evidence-lane-smoke", content)
        self.assertIn("--required-status-check release-hygiene", content)
        self.assertIn("--required-status-check-strict true", content)
        self.assertNotIn("--apply \\", content)
        self.assertIn("improvement reconcile-codeowner-review-gate-outputs", content)
        self.assertIn('--summary-heading "Codeowner Review Gate Drift Check"', content)
        self.assertIn("Open required-status-check drift alert", content)
        self.assertIn("run_improvement_reconcile_codeowner_review_gate_runtime_alert.sh", content)
        self.assertIn('--summary-heading "Codeowner Review Gate Drift Alert"', content)
        self.assertIn("codeowner_review_reconcile_drift_alert.json", content)
        self.assertIn("codeowner-review-reconcile-drift-check", content)
        self.assertIn("codeowner_review_drift_alert_created", content)
        self.assertIn("codeowner_review_drift_error", content)
        self.assertIn("codeowner_review_drift_source_workflow_run_id", content)
        self.assertIn("codeowner_review_drift_source_workflow_run_conclusion", content)
        self.assertIn("codeowner_review_drift_source_workflow_name", content)
        self.assertIn("codeowner_review_drift_source_workflow_event", content)
        self.assertIn("codeowner_review_drift_source_workflow_run_url", content)
        self.assertIn("Fail on drift alert creation error", content)
        self.assertIn("Fail on required-status-check drift", content)
        self.assertNotIn("python3 - <<'PY'", content)

    def test_no_inline_python_workflow_adapters_remaining(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_roots = [
            repo_root / ".github" / "workflows",
            repo_root / "configs" / "improvement_operator_knowledge_stack",
        ]
        inline_hits: list[str] = []
        for root in workflow_roots:
            for path in sorted(root.rglob("*.yml")):
                content = path.read_text(encoding="utf-8")
                if "python3 - <<'PY'" in content:
                    inline_hits.append(str(path.relative_to(repo_root)))
        self.assertEqual(
            inline_hits,
            [],
            msg=f"inline python workflow adapters detected: {inline_hits}",
        )


if __name__ == "__main__":
    unittest.main()
