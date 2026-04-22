from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class HypothesisLabRuntimeTests(unittest.TestCase):
    def _make_runtime(self, root: Path) -> JarvisRuntime:
        repo = root / "repo"
        db = root / "jarvis.db"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text(
            "def x():\n    return 'TODO_ZENITH'\n",
            encoding="utf-8",
        )
        return JarvisRuntime(db_path=db, repo_path=repo)

    def test_friction_clustering_and_hypothesis_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Too many paywalls before trying workouts",
                    severity=4,
                    symptom_tags=["paywall", "onboarding"],
                )
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="Too many paywalls before trying workout plans",
                    severity=5,
                    symptom_tags=["paywall", "pricing"],
                )
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="session_replays",
                    summary="Too many paywalls before trying a workout",
                    severity=4,
                    symptom_tags=["paywall", "dropoff"],
                )

                summary = runtime.summarize_domain_displeasures(domain="fitness_apps", min_count=2, limit=5)
                self.assertGreaterEqual(int(summary.get("cluster_count") or 0), 1)
                clusters = list(summary.get("clusters") or [])
                self.assertTrue(bool(clusters))
                self.assertIn("paywall", str(clusters[0].get("canonical_key") or ""))

                proposals = runtime.propose_friction_hypotheses(domain="fitness_apps", min_count=2, limit=3)
                self.assertTrue(bool(proposals))
                top = proposals[0]
                criteria = top.get("success_criteria") if isinstance(top.get("success_criteria"), dict) else {}
                self.assertEqual(str(criteria.get("metric")), "retention_d30")
                self.assertEqual(str(criteria.get("direction")), "increase")
            finally:
                runtime.close()

    def test_controlled_experiments_and_debug_replay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                ingest = runtime.ingest_event(
                    source="github",
                    source_type="ci",
                    payload={"project": "alpha", "status": "failed", "deadline_hours": 12},
                )
                plan_id = runtime.plan(ingest["triggers"])[0]
                runtime.run(plan_id, dry_run=True, approvals={})
                traces = runtime.list_decision_traces(plan_id=plan_id, limit=10)
                self.assertTrue(bool(traces))
                source_trace_id = str(traces[0].get("trace_id") or "")
                self.assertTrue(bool(source_trace_id))

                hypothesis = runtime.register_hypothesis(
                    domain="kalshi_weather",
                    title="Improve weather market calibration",
                    statement="Using tighter probability calibration should improve weather market edge.",
                    proposed_change="Add forecast-ensemble calibration before order generation.",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))

                promoted = runtime.run_hypothesis_experiment(
                    hypothesis_id=hypothesis_id,
                    environment="paper_trade",
                    baseline_metrics={
                        "brier_skill": 0.01,
                        "max_daily_loss": 0.02,
                        "slippage_bps": 12.0,
                    },
                    candidate_metrics={
                        "brier_skill": 0.05,
                        "max_daily_loss": 0.028,
                        "slippage_bps": 14.0,
                    },
                    sample_size=220,
                    source_trace_id=source_trace_id,
                    notes="paper trade cohort A",
                )
                promoted_eval = promoted.get("evaluation") if isinstance(promoted.get("evaluation"), dict) else {}
                self.assertEqual(str(promoted_eval.get("verdict")), "promote")
                self.assertEqual(str(promoted.get("hypothesis_status")), "validated")

                debug = runtime.debug_hypothesis_experiment(
                    run_id=str(promoted.get("run_id") or ""),
                    include_decision_timeline=True,
                )
                self.assertTrue(bool(debug.get("found")))
                reasoning_trace = debug.get("reasoning_trace") if isinstance(debug.get("reasoning_trace"), dict) else {}
                self.assertEqual(str(reasoning_trace.get("trace_id") or ""), source_trace_id)
                self.assertTrue(bool(debug.get("step_timeline")))

                blocked = runtime.run_hypothesis_experiment(
                    hypothesis_id=hypothesis_id,
                    environment="paper_trade",
                    baseline_metrics={
                        "brier_skill": 0.01,
                        "max_daily_loss": 0.02,
                        "slippage_bps": 12.0,
                    },
                    candidate_metrics={
                        "brier_skill": 0.06,
                        "max_daily_loss": 0.08,
                        "slippage_bps": 18.0,
                    },
                    sample_size=220,
                    source_trace_id=source_trace_id,
                    notes="paper trade cohort B",
                )
                blocked_eval = blocked.get("evaluation") if isinstance(blocked.get("evaluation"), dict) else {}
                self.assertEqual(str(blocked_eval.get("verdict")), "blocked_guardrail")
                self.assertEqual(str(blocked.get("hypothesis_status")), "rejected")

                history = runtime.list_hypothesis_experiments(hypothesis_id=hypothesis_id, limit=20)
                self.assertGreaterEqual(len(history), 2)
            finally:
                runtime.close()

    def test_feedback_batch_ingestion_normalizes_mixed_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                result = runtime.ingest_domain_feedback_batch(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    records=[
                        {
                            "id": "r1",
                            "title": "Crashes after sync",
                            "review": "The app crashes every time after workout sync.",
                            "rating": 1,
                            "tags": "crash,sync",
                            "user_segment": "beginner",
                        },
                        {
                            "id": "r2",
                            "content": "Paywall appears before trying core workout features.",
                            "rating": 2,
                            "labels": ["paywall", "pricing"],
                        },
                        {
                            "id": "r3",
                            "message": "",
                            "rating": 4,
                        },
                    ],
                    metadata={"dataset": "fitness_feedback_v1"},
                )
                self.assertEqual(int(result.get("requested_count") or 0), 3)
                self.assertEqual(int(result.get("ingested_count") or 0), 2)
                self.assertEqual(int(result.get("skipped_count") or 0), 1)

                rows = runtime.list_domain_frictions(domain="fitness_apps", source="app_store_reviews", limit=10)
                self.assertGreaterEqual(len(rows), 2)
                by_id = {str(row.get("evidence", {}).get("id") or ""): row for row in rows}
                self.assertIn("r1", by_id)
                self.assertIn("r2", by_id)
                self.assertEqual(int((by_id["r1"]).get("severity") or 0), 5)
                self.assertEqual(int((by_id["r2"]).get("severity") or 0), 4)
                self.assertIn("dataset", (by_id["r1"]).get("metadata") or {})
            finally:
                runtime.close()

    def test_friction_hypothesis_cycle_auto_registers_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = self._make_runtime(Path(td))
            try:
                runtime.ingest_domain_feedback_batch(
                    domain="fitness_apps",
                    source="support_tickets",
                    records=[
                        {"ticket_id": "t1", "summary": "Paywall blocks trying workouts early", "severity": 4},
                        {"ticket_id": "t2", "summary": "Paywall blocks trying workout plans early", "severity": 5},
                        {"ticket_id": "t3", "summary": "Paywall before trying features", "severity": 4},
                    ],
                )

                first = runtime.run_friction_hypothesis_cycle(
                    domain="fitness_apps",
                    min_cluster_count=1,
                    proposal_limit=5,
                    auto_register=True,
                    owner="test",
                )
                self.assertGreaterEqual(int(first.get("proposal_count") or 0), 1)
                self.assertGreaterEqual(int(first.get("created_count") or 0), 1)

                second = runtime.run_friction_hypothesis_cycle(
                    domain="fitness_apps",
                    min_cluster_count=1,
                    proposal_limit=5,
                    auto_register=True,
                    owner="test",
                )
                self.assertEqual(int(second.get("created_count") or 0), 0)
                self.assertGreaterEqual(int(second.get("skipped_existing_count") or 0), 1)
            finally:
                runtime.close()

    def test_feedback_file_ingestion_and_cycle_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runtime = self._make_runtime(root)
            try:
                feedback_path = root / "fitness_feedback.jsonl"
                feedback_path.write_text(
                    "\n".join(
                        [
                            json.dumps(
                                {
                                    "id": "f1",
                                    "title": "Paywall too early",
                                    "review": "Paywall appears before I can try any workout.",
                                    "rating": 2,
                                    "user_segment": "beginner",
                                }
                            ),
                            json.dumps(
                                {
                                    "id": "f2",
                                    "summary": "Paywall shows up before core features",
                                    "rating": 2,
                                    "tags": ["paywall", "pricing"],
                                }
                            ),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )

                run = runtime.run_friction_hypothesis_cycle_from_file(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    input_path=str(feedback_path),
                    input_format="jsonl",
                    min_cluster_count=1,
                    proposal_limit=3,
                    auto_register=True,
                    owner="test",
                )
                ingest = run.get("ingest") if isinstance(run.get("ingest"), dict) else {}
                cycle = run.get("cycle") if isinstance(run.get("cycle"), dict) else {}
                self.assertEqual(int(ingest.get("loaded_record_count") or 0), 2)
                self.assertEqual(int(ingest.get("ingested_count") or 0), 2)
                self.assertGreaterEqual(int(cycle.get("proposal_count") or 0), 1)
                self.assertGreaterEqual(int(cycle.get("created_count") or 0), 1)
            finally:
                runtime.close()

    def test_hypothesis_experiment_from_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runtime = self._make_runtime(root)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Reduce ML false positives while lifting precision",
                    statement="Feature calibration should improve precision without violating risk guardrails.",
                    proposed_change="Add calibrator + threshold tuning.",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))

                artifact_path = root / "market_ml_eval.json"
                artifact_path.write_text(
                    json.dumps(
                        {
                            "environment": "offline_backtest",
                            "baseline": {
                                "metrics": {
                                    "precision_at_k": 0.31,
                                    "false_positive_rate": 0.19,
                                    "inference_latency_ms_p95": 220,
                                }
                            },
                            "candidate": {
                                "metrics": {
                                    "precision_at_k": 0.36,
                                    "false_positive_rate": 0.17,
                                    "inference_latency_ms_p95": 200,
                                },
                                "sample_size": 450,
                            },
                            "notes": "walk-forward validation window",
                        },
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )

                result = runtime.run_hypothesis_experiment_from_artifact(
                    hypothesis_id=hypothesis_id,
                    artifact_path=str(artifact_path),
                )
                evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
                metric = evaluation.get("metric_result") if isinstance(evaluation.get("metric_result"), dict) else {}
                self.assertEqual(str(result.get("hypothesis_status")), "validated")
                self.assertEqual(str(evaluation.get("verdict")), "promote")
                self.assertAlmostEqual(float(metric.get("baseline") or 0.0), 0.31, places=6)
                self.assertAlmostEqual(float(metric.get("candidate") or 0.0), 0.36, places=6)
                artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
                self.assertEqual(str(artifact.get("path") or ""), str(artifact_path.resolve()))
            finally:
                runtime.close()

    def test_retest_lane_queue_and_side_by_side_compare(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runtime = self._make_runtime(root)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Retest lane candidate",
                    statement="Guardrail failures should requeue with safer retest settings.",
                    proposed_change="Tune classifier thresholds.",
                    friction_key="false_positive_drift",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))

                first = runtime.run_hypothesis_experiment(
                    hypothesis_id=hypothesis_id,
                    environment="offline_backtest",
                    baseline_metrics={
                        "precision_at_k": 0.30,
                        "false_positive_rate": 0.18,
                        "inference_latency_ms_p95": 210,
                    },
                    candidate_metrics={
                        "precision_at_k": 0.34,
                        "false_positive_rate": 0.17,
                        "inference_latency_ms_p95": 205,
                    },
                    sample_size=260,
                )
                self.assertEqual(str(((first.get("evaluation") or {}).get("verdict") or "")), "promote")

                second = runtime.run_hypothesis_experiment(
                    hypothesis_id=hypothesis_id,
                    environment="offline_backtest",
                    baseline_metrics={
                        "precision_at_k": 0.30,
                        "false_positive_rate": 0.18,
                        "inference_latency_ms_p95": 210,
                    },
                    candidate_metrics={
                        "precision_at_k": 0.38,
                        "false_positive_rate": 0.32,
                        "inference_latency_ms_p95": 215,
                    },
                    sample_size=500,
                )
                self.assertEqual(str(((second.get("evaluation") or {}).get("verdict") or "")), "blocked_guardrail")
                second_run_id = str(second.get("run_id") or "")
                self.assertTrue(bool(second_run_id))

                compare = runtime.compare_hypothesis_runs(
                    hypothesis_id=hypothesis_id,
                    current_run_id=second_run_id,
                )
                self.assertTrue(bool(compare.get("found")))
                self.assertTrue(bool(compare.get("has_previous")))
                transition = dict(compare.get("verdict_transition") or {})
                self.assertEqual(str(transition.get("previous") or ""), "promote")
                self.assertEqual(str(transition.get("current") or ""), "blocked_guardrail")
                sample_transition = dict(compare.get("sample_transition") or {})
                self.assertEqual(int(sample_transition.get("previous") or 0), 260)
                self.assertEqual(int(sample_transition.get("current") or 0), 500)

                retest = runtime.queue_hypothesis_retest_from_run(
                    run_id=second_run_id,
                    guardrail_sample_multiplier=1.1,
                    min_sample_increment=50,
                    guardrail_safety_factor=0.9,
                )
                self.assertTrue(bool(retest.get("queued")))
                self.assertEqual(str(retest.get("hypothesis_status") or ""), "queued")
                self.assertGreaterEqual(int(retest.get("recommended_sample_size") or 0), 550)
                safety_targets = list(retest.get("safety_targets") or [])
                self.assertTrue(bool(safety_targets))
                self.assertEqual(str((safety_targets[0] or {}).get("metric") or ""), "false_positive_rate")

                executed = runtime.run_hypothesis_retest(
                    hypothesis_id=hypothesis_id,
                    trigger_run_id=second_run_id,
                    notes="runtime retest execution",
                )
                self.assertTrue(bool(str(executed.get("run_id") or "")))
                executed_result = dict(executed.get("result") or {})
                executed_eval = dict(executed_result.get("evaluation") or {})
                self.assertIn(
                    str(executed_eval.get("verdict") or ""),
                    {"promote", "needs_iteration", "insufficient_data", "blocked_guardrail"},
                )
                side_by_side = dict(executed.get("side_by_side") or {})
                transition = dict(side_by_side.get("verdict_transition") or {})
                self.assertEqual(str(transition.get("previous") or ""), "blocked_guardrail")
                artifact_payload = dict(executed.get("artifact_payload") or {})
                candidate_block = dict(artifact_payload.get("candidate") or {})
                candidate_metrics = dict(candidate_block.get("metrics") or {})
                self.assertAlmostEqual(float(candidate_metrics.get("false_positive_rate") or 0.0), 0.18, places=6)
            finally:
                runtime.close()

    def test_hypothesis_inbox_report_contains_ranked_queue(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runtime = self._make_runtime(root)
            try:
                runtime.ingest_domain_feedback_batch(
                    domain="fitness_apps",
                    source="support_tickets",
                    records=[
                        {"ticket_id": "rpt-1", "summary": "Paywall appears before trying plans", "severity": 5},
                        {"ticket_id": "rpt-2", "summary": "Paywall before trying workouts", "severity": 4},
                    ],
                )
                cycle = runtime.run_friction_hypothesis_cycle(
                    domain="fitness_apps",
                    min_cluster_count=1,
                    proposal_limit=3,
                    auto_register=True,
                    owner="test",
                )
                self.assertGreaterEqual(int(cycle.get("created_count") or 0), 1)

                report = runtime.build_hypothesis_inbox_report(
                    domain="fitness_apps",
                    cluster_min_count=1,
                    queue_limit=10,
                )
                self.assertEqual(str(report.get("domain") or ""), "fitness_apps")
                self.assertGreaterEqual(int(report.get("ranked_queue_count") or 0), 1)
                ranked = list(report.get("ranked_queue") or [])
                self.assertTrue(bool(ranked))
                self.assertIn(str(ranked[0].get("status") or ""), {"queued", "testing", "validated", "rejected"})
                self.assertTrue(bool(list(report.get("suggested_actions") or [])))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
