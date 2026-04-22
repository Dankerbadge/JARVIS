from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jarvis.cli import (
    cmd_improvement_daily_pipeline,
    cmd_improvement_execute_retests,
    cmd_improvement_operator_cycle,
    cmd_improvement_pull_feeds,
    cmd_improvement_run_experiment_artifact,
    cmd_improvement_seed_hypotheses,
    cmd_improvement_verify_matrix,
    cmd_improvement_verify_matrix_alert,
)
from jarvis.runtime import JarvisRuntime


class CliImprovementPipelineTests(unittest.TestCase):
    def _make_repo(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        db = root / "jarvis.db"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text(
            "def x():\n    return 'TODO_ZENITH'\n",
            encoding="utf-8",
        )
        return repo, db

    def test_pull_feeds_command_materializes_jsonl_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw_input = root / "inputs" / "fitness_reviews.json"
            raw_input.parent.mkdir(parents=True, exist_ok=True)
            raw_input.write_text(
                json.dumps(
                    {
                        "reviews": [
                            {
                                "id": "rv-1",
                                "title": "Paywall too early",
                                "text": "Paywall appears before trying workouts.",
                                "rating": 2,
                            },
                            {
                                "id": "rv-2",
                                "title": "Crash after sync",
                                "text": "App crashes after workout sync.",
                                "rating": 1,
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "feeds.json"
            config_path.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "name": "fitness_reviews",
                                "url": "inputs/fitness_reviews.json",
                                "format": "json",
                                "records_path": "reviews",
                                "mapping": {
                                    "id": "id",
                                    "title": "title",
                                    "summary": "text",
                                    "review": "text",
                                    "rating": "rating",
                                },
                                "static_fields": {
                                    "source_context": "unit_test",
                                },
                                "output_path": "analysis/fitness_feedback.jsonl",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            report_path = root / "reports" / "pull_feeds.json"
            args = argparse.Namespace(
                config_path=config_path,
                feed_names=None,
                allow_missing=False,
                strict=False,
                timeout_seconds=20.0,
                output_path=report_path,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_pull_feeds(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("error_count") or 0), 0)
            self.assertEqual(int(payload.get("run_count") or 0), 1)
            self.assertTrue(report_path.exists())

            runs = list(payload.get("runs") or [])
            self.assertEqual(len(runs), 1)
            output_path = Path(str((runs[0] or {}).get("output_path") or ""))
            self.assertTrue(output_path.exists())
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(str((rows[0] or {}).get("source_context") or ""), "unit_test")

    def test_run_experiment_artifact_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Lift precision while controlling fp",
                    statement="Calibrated thresholding should lift precision.",
                    proposed_change="Apply calibrator in inference path.",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
            finally:
                runtime.close()

            artifact_path = root / "ml_eval.json"
            artifact_path.write_text(
                json.dumps(
                    {
                        "environment": "offline_backtest",
                        "baseline": {
                            "metrics": {
                                "precision_at_k": 0.32,
                                "false_positive_rate": 0.18,
                                "inference_latency_ms_p95": 220,
                            }
                        },
                        "candidate": {
                            "metrics": {
                                "precision_at_k": 0.37,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 210,
                            },
                            "sample_size": 500,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                hypothesis_id=hypothesis_id,
                artifact_path=artifact_path,
                environment=None,
                source_trace_id=None,
                notes=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_run_experiment_artifact(args)
            payload = json.loads(out.getvalue())

            result = dict(payload.get("result") or {})
            evaluation = dict(result.get("evaluation") or {})
            self.assertEqual(str(payload.get("hypothesis_id") or ""), hypothesis_id)
            self.assertEqual(str(evaluation.get("verdict") or ""), "promote")
            self.assertEqual(str(result.get("hypothesis_status") or ""), "validated")

    def test_seed_hypotheses_command_creates_and_dedupes_templates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            template_path = root / "hypothesis_templates.json"
            template_path.write_text(
                json.dumps(
                    {
                        "hypotheses": [
                            {
                                "domain": "fitness_apps",
                                "title": "Reduce paywall-before-trial dropoff",
                                "statement": "Early paywall prompts reduce trial completion.",
                                "proposed_change": "Delay paywall until after one completed workout.",
                                "friction_key": "paywall_before_core_workout_trial",
                                "risk_level": "medium",
                            },
                            {
                                "domain": "market_ml",
                                "title": "Reduce false-positive drift in volatile windows",
                                "statement": "Classifier false positives spike during volatility.",
                                "proposed_change": "Apply volatility-aware calibration layer.",
                                "friction_key": "false_positive_drift_in_high_volatility_windows",
                                "risk_level": "high",
                            },
                            {
                                "domain": "fitness_apps",
                                "title": "Duplicate paywall template",
                                "statement": "Duplicate should dedupe on friction key.",
                                "proposed_change": "No-op",
                                "friction_key": "paywall_before_core_workout_trial",
                                "risk_level": "low",
                            },
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                template_path=template_path,
                owner="operator",
                lookup_limit=200,
                allow_invalid_rows=False,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_seed_hypotheses(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("error_count") or 0), 0)
            self.assertEqual(int(payload.get("created_count") or 0), 2)
            self.assertEqual(int(payload.get("existing_count") or 0), 1)

            created = list(payload.get("created") or [])
            self.assertEqual(len(created), 2)
            created_keys = {str((item or {}).get("friction_key") or "") for item in created}
            self.assertIn("paywall_before_core_workout_trial", created_keys)
            self.assertIn("false_positive_drift_in_high_volatility_windows", created_keys)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypotheses = runtime.list_hypotheses(domain=None, status=None, limit=20)
                self.assertEqual(len(hypotheses), 2)
                keys = {str((item or {}).get("friction_key") or "") for item in hypotheses}
                self.assertIn("paywall_before_core_workout_trial", keys)
                self.assertIn("false_positive_drift_in_high_volatility_windows", keys)
            finally:
                runtime.close()

    def test_daily_pipeline_command_runs_feedback_and_experiments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Daily pipeline hypothesis",
                    statement="Pipeline should evaluate this from artifact.",
                    proposed_change="Use threshold tuning.",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
            finally:
                runtime.close()

            feedback_path = root / "fitness_feedback.jsonl"
            feedback_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "p1",
                                "summary": "Paywall blocks trying workouts early",
                                "rating": 2,
                            }
                        ),
                        json.dumps(
                            {
                                "id": "p2",
                                "summary": "Paywall appears before core workout features",
                                "rating": 2,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            artifact_path = root / "market_ml_artifact.json"
            artifact_path.write_text(
                json.dumps(
                    {
                        "environment": "offline_backtest",
                        "baseline": {
                            "metrics": {
                                "precision_at_k": 0.31,
                                "false_positive_rate": 0.19,
                                "inference_latency_ms_p95": 230,
                            }
                        },
                        "candidate": {
                            "metrics": {
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.17,
                                "inference_latency_ms_p95": 210,
                            },
                            "sample_size": 450,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "pipeline_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 5,
                        },
                        "feedback_jobs": [
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "fitness_feedback.jsonl",
                                "input_format": "jsonl",
                                "report_path": "reports/fitness_inbox.json",
                            }
                        ],
                        "experiment_jobs": [
                            {
                                "hypothesis_id": hypothesis_id,
                                "artifact_path": "market_ml_artifact.json",
                            }
                        ],
                        "output_path": "reports/daily_pipeline.json",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                config_path=config_path,
                allow_missing_inputs=False,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_daily_pipeline(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("error_count") or 0), 0)
            self.assertEqual(int(payload.get("feedback_runs_count") or 0), 1)
            self.assertEqual(int(payload.get("experiment_runs_count") or 0), 1)

            feedback_runs = list(payload.get("feedback_runs") or [])
            self.assertTrue(bool(feedback_runs))
            self.assertEqual(str((feedback_runs[0] or {}).get("status") or ""), "ok")
            cycle = dict((feedback_runs[0] or {}).get("cycle") or {})
            self.assertGreaterEqual(int(cycle.get("proposal_count") or 0), 1)
            self.assertGreaterEqual(int(cycle.get("created_count") or 0), 1)

            experiment_runs = list(payload.get("experiment_runs") or [])
            self.assertTrue(bool(experiment_runs))
            self.assertEqual(str((experiment_runs[0] or {}).get("status") or ""), "ok")
            self.assertEqual(str((experiment_runs[0] or {}).get("verdict") or ""), "promote")

            output_path = root / "reports" / "daily_pipeline.json"
            feedback_report_path = root / "reports" / "fitness_inbox.json"
            self.assertTrue(output_path.exists())
            self.assertTrue(feedback_report_path.exists())

    def test_daily_pipeline_resolves_hypothesis_by_domain_and_friction_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Reduce false positive drift",
                    statement="Stabilizing calibrator should reduce false positive rate drift.",
                    proposed_change="Add drift-aware calibrator retraining schedule.",
                    friction_key="false_positive_drift",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
            finally:
                runtime.close()

            raw_feedback_path = root / "inputs" / "fitness_reviews.json"
            raw_feedback_path.parent.mkdir(parents=True, exist_ok=True)
            raw_feedback_path.write_text(
                json.dumps(
                    {
                        "reviews": [
                            {
                                "id": "feed-1",
                                "title": "Paywall before trial",
                                "text": "Paywall appears before workout trial starts.",
                                "rating": 2,
                            },
                            {
                                "id": "feed-2",
                                "title": "Paywall blocks tryout",
                                "text": "Paywall blocks seeing core workout options.",
                                "rating": 2,
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            artifact_path = root / "artifacts" / "market_ml_eval.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    {
                        "environment": "offline_backtest",
                        "baseline": {
                            "metrics": {
                                "precision_at_k": 0.30,
                                "false_positive_rate": 0.20,
                                "inference_latency_ms_p95": 225,
                            }
                        },
                        "candidate": {
                            "metrics": {
                                "precision_at_k": 0.35,
                                "false_positive_rate": 0.17,
                                "inference_latency_ms_p95": 210,
                            },
                            "sample_size": 480,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "pipeline_selector_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 5,
                            "preferred_statuses": ["testing", "queued", "validated", "rejected"],
                            "collect_experiment_debug": True,
                            "experiment_debug_output_dir": "reports/debug",
                            "include_decision_timeline": False,
                        },
                        "feed_jobs": [
                            {
                                "name": "fitness_reviews",
                                "url": "inputs/fitness_reviews.json",
                                "format": "json",
                                "records_path": "reviews",
                                "mapping": {
                                    "id": "id",
                                    "title": "title",
                                    "summary": "text",
                                    "review": "text",
                                    "rating": "rating",
                                },
                                "output_path": "analysis/fitness_feedback.jsonl",
                            }
                        ],
                        "feedback_jobs": [
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "analysis/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [
                            {
                                "domain": "market_ml",
                                "friction_key": "false_positive_drift",
                                "artifact_path": "artifacts/market_ml_eval.json",
                            }
                        ],
                        "output_path": "reports/daily_pipeline_selector.json",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                config_path=config_path,
                allow_missing_inputs=False,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_daily_pipeline(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("error_count") or 0), 0)
            self.assertEqual(int(payload.get("feed_runs_count") or 0), 1)
            self.assertEqual(int(payload.get("feedback_runs_count") or 0), 1)
            self.assertEqual(int(payload.get("experiment_runs_count") or 0), 1)

            feed_runs = list(payload.get("feed_runs") or [])
            self.assertTrue(bool(feed_runs))
            self.assertEqual(str((feed_runs[0] or {}).get("status") or ""), "ok")

            experiment_runs = list(payload.get("experiment_runs") or [])
            self.assertTrue(bool(experiment_runs))
            self.assertEqual(str((experiment_runs[0] or {}).get("hypothesis_id") or ""), hypothesis_id)
            resolution = dict((experiment_runs[0] or {}).get("resolution") or {})
            self.assertEqual(str(resolution.get("strategy") or ""), "selector")
            selected = dict(resolution.get("selected") or {})
            self.assertEqual(str(selected.get("hypothesis_id") or ""), hypothesis_id)
            self.assertEqual(str((experiment_runs[0] or {}).get("verdict") or ""), "promote")
            self.assertGreaterEqual(int((experiment_runs[0] or {}).get("failed_checks_count") or 0), 0)
            debug_report_path = Path(str((experiment_runs[0] or {}).get("debug_report_path") or ""))
            self.assertTrue(debug_report_path.exists())

    def test_daily_pipeline_blocked_experiment_writes_debug_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Guardrail block repro",
                    statement="Candidate should be blocked when false positive rate violates guardrail.",
                    proposed_change="Raise threshold aggressively.",
                    friction_key="false_positive_drift",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
                warmup = runtime.run_hypothesis_experiment(
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
                self.assertEqual(
                    str(((warmup.get("evaluation") or {}).get("verdict") or "")),
                    "promote",
                )
            finally:
                runtime.close()

            artifact_path = root / "artifacts" / "blocked_eval.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    {
                        "environment": "offline_backtest",
                        "baseline": {
                            "metrics": {
                                "precision_at_k": 0.30,
                                "false_positive_rate": 0.18,
                                "inference_latency_ms_p95": 210,
                            }
                        },
                        "candidate": {
                            "metrics": {
                                "precision_at_k": 0.38,
                                "false_positive_rate": 0.32,
                                "inference_latency_ms_p95": 215,
                            },
                            "sample_size": 500,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "pipeline_blocked_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "collect_experiment_debug": True,
                            "include_decision_timeline": False,
                            "compare_experiment_history": True,
                            "auto_retest_lane": True,
                            "guardrail_sample_multiplier": 1.1,
                            "min_sample_increment": 50,
                            "guardrail_safety_factor": 0.9,
                        },
                        "feedback_jobs": [],
                        "experiment_jobs": [
                            {
                                "hypothesis_id": hypothesis_id,
                                "artifact_path": "artifacts/blocked_eval.json",
                                "debug_report_path": "reports/blocked_debug.json",
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                config_path=config_path,
                allow_missing_inputs=False,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_daily_pipeline(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("error_count") or 0), 0)
            experiment_runs = list(payload.get("experiment_runs") or [])
            self.assertEqual(len(experiment_runs), 1)
            self.assertEqual(str((experiment_runs[0] or {}).get("verdict") or ""), "blocked_guardrail")
            self.assertGreaterEqual(int((experiment_runs[0] or {}).get("failed_checks_count") or 0), 1)
            side_by_side = dict((experiment_runs[0] or {}).get("side_by_side") or {})
            self.assertTrue(bool(side_by_side.get("has_previous")))
            self.assertEqual(
                str(((side_by_side.get("verdict_transition") or {}).get("previous") or "")),
                "promote",
            )
            self.assertEqual(
                str(((side_by_side.get("verdict_transition") or {}).get("current") or "")),
                "blocked_guardrail",
            )

            retest = dict((experiment_runs[0] or {}).get("retest") or {})
            self.assertTrue(bool(retest.get("queued")))
            self.assertEqual(str(retest.get("hypothesis_status") or ""), "queued")
            self.assertGreaterEqual(int(retest.get("recommended_sample_size") or 0), 550)
            self.assertTrue(bool(list(retest.get("safety_targets") or [])))
            self.assertEqual(int(payload.get("retest_runs_count") or 0), 1)

            debug_report_path = Path(str((experiment_runs[0] or {}).get("debug_report_path") or ""))
            self.assertTrue(debug_report_path.exists())
            debug_payload = json.loads(debug_report_path.read_text(encoding="utf-8"))
            hints = [str(item) for item in list(debug_payload.get("root_cause_hints") or [])]
            self.assertTrue(any("Guardrail violation" in hint for hint in hints))

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                refreshed = runtime.list_hypotheses(domain="market_ml", status=None, limit=10)
                matched = [item for item in refreshed if str(item.get("hypothesis_id") or "") == hypothesis_id]
                self.assertTrue(bool(matched))
                self.assertEqual(str((matched[0] or {}).get("status") or ""), "queued")
            finally:
                runtime.close()

    def test_execute_retests_command_runs_from_pipeline_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Execute retest lane",
                    statement="Queued retests should be executable from report payloads.",
                    proposed_change="Adjust threshold after guardrail failure.",
                    friction_key="false_positive_drift",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))

                blocked = runtime.run_hypothesis_experiment(
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
                self.assertEqual(str(((blocked.get("evaluation") or {}).get("verdict") or "")), "blocked_guardrail")
                trigger_run_id = str(blocked.get("run_id") or "")
                self.assertTrue(bool(trigger_run_id))

                queued = runtime.queue_hypothesis_retest_from_run(
                    run_id=trigger_run_id,
                    guardrail_sample_multiplier=1.1,
                    min_sample_increment=50,
                    guardrail_safety_factor=0.9,
                )
                self.assertTrue(bool(queued.get("queued")))
            finally:
                runtime.close()

            pipeline_report_path = root / "reports" / "daily_pipeline_report.json"
            pipeline_report_path.parent.mkdir(parents=True, exist_ok=True)
            pipeline_report_path.write_text(
                json.dumps(
                    {
                        "retest_runs": [
                            {
                                "hypothesis_id": hypothesis_id,
                                "run_id": trigger_run_id,
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_path = root / "reports" / "retest_execution_report.json"
            artifact_dir = root / "artifacts" / "retests"
            args = argparse.Namespace(
                pipeline_report_path=pipeline_report_path,
                max_runs=None,
                artifact_dir=artifact_dir,
                environment=None,
                notes_prefix="auto_retest",
                allow_missing_jobs=False,
                strict=False,
                output_path=output_path,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_execute_retests(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("error_count") or 0), 0)
            self.assertEqual(int(payload.get("executed_count") or 0), 1)
            self.assertTrue(output_path.exists())

            runs = list(payload.get("runs") or [])
            self.assertEqual(len(runs), 1)
            self.assertEqual(str((runs[0] or {}).get("hypothesis_id") or ""), hypothesis_id)
            side_by_side = dict((runs[0] or {}).get("side_by_side") or {})
            transition = dict(side_by_side.get("verdict_transition") or {})
            self.assertEqual(str(transition.get("previous") or ""), "blocked_guardrail")
            self.assertTrue(bool(str(transition.get("current") or "")))
            artifact_path = Path(str((runs[0] or {}).get("artifact_path") or ""))
            self.assertTrue(artifact_path.exists())

    def test_operator_cycle_runs_pull_daily_retest_and_writes_inbox_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator cycle hypothesis",
                    statement="Operator cycle should run pull/daily/retest and summarize outputs.",
                    proposed_change="Tighten threshold controls after guardrail violations.",
                    friction_key="false_positive_drift",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
            finally:
                runtime.close()

            raw_input_path = root / "inputs" / "fitness_reviews.json"
            raw_input_path.parent.mkdir(parents=True, exist_ok=True)
            raw_input_path.write_text(
                json.dumps(
                    {
                        "reviews": [
                            {
                                "id": "feed-op-1",
                                "title": "Paywall appears too early",
                                "text": "Paywall appears before I can try any workout.",
                                "rating": 2,
                            },
                            {
                                "id": "feed-op-2",
                                "title": "No free trial path",
                                "text": "No way to test features before paying.",
                                "rating": 2,
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            blocked_artifact_path = root / "artifacts" / "blocked_eval.json"
            blocked_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            blocked_artifact_path.write_text(
                json.dumps(
                    {
                        "environment": "offline_backtest",
                        "baseline": {
                            "metrics": {
                                "precision_at_k": 0.30,
                                "false_positive_rate": 0.18,
                                "inference_latency_ms_p95": 210,
                            }
                        },
                        "candidate": {
                            "metrics": {
                                "precision_at_k": 0.38,
                                "false_positive_rate": 0.32,
                                "inference_latency_ms_p95": 215,
                            },
                            "sample_size": 500,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 5,
                            "auto_retest_lane": True,
                            "guardrail_sample_multiplier": 1.1,
                            "min_sample_increment": 50,
                            "guardrail_safety_factor": 0.9,
                            "include_decision_timeline": False,
                            "collect_experiment_debug": True,
                        },
                        "feed_jobs": [
                            {
                                "name": "fitness_reviews",
                                "url": "inputs/fitness_reviews.json",
                                "format": "json",
                                "records_path": "reviews",
                                "mapping": {
                                    "id": "id",
                                    "title": "title",
                                    "summary": "text",
                                    "review": "text",
                                    "rating": "rating",
                                },
                                "output_path": "analysis/fitness_feedback.jsonl",
                            }
                        ],
                        "feedback_jobs": [
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "analysis/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [
                            {
                                "hypothesis_id": hypothesis_id,
                                "artifact_path": "artifacts/blocked_eval.json",
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                feed_names=None,
                feed_timeout_seconds=20.0,
                allow_missing_feeds=False,
                allow_missing_inputs=False,
                allow_missing_retests=False,
                retest_max_runs=None,
                retest_artifact_dir=None,
                retest_environment=None,
                retest_notes_prefix="operator_cycle_retest",
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("pull_feeds") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("daily_pipeline") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("execute_retests") or ""), "ok")
            self.assertEqual(int(payload.get("stage_error_count") or 0), 0)

            pull_report_path = Path(str(payload.get("pull_report_path") or ""))
            daily_report_path = Path(str(payload.get("daily_report_path") or ""))
            retest_report_path = Path(str(payload.get("retest_report_path") or ""))
            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(pull_report_path.exists())
            self.assertTrue(daily_report_path.exists())
            self.assertTrue(retest_report_path.exists())
            self.assertTrue(inbox_summary_path.exists())

            retest_payload = json.loads(retest_report_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(retest_payload.get("executed_count") or 0), 1)

            summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(str((summary.get("stage_statuses") or {}).get("daily_pipeline") or ""), "ok")
            self.assertGreaterEqual(int((summary.get("metrics") or {}).get("retest_delta_count") or 0), 1)
            self.assertGreaterEqual(len(list(summary.get("suggested_actions") or [])), 1)

    def test_verify_matrix_detects_match_mismatch_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                quant_hypothesis = runtime.register_hypothesis(
                    domain="quant_finance",
                    title="Quant regime slippage",
                    statement="Regime shifts increase slippage.",
                    proposed_change="Use regime-aware routing.",
                    friction_key="execution_slippage_regime_drift",
                )
                market_ml_hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Market ML drift",
                    statement="False positives drift higher in volatility spikes.",
                    proposed_change="Apply volatility-aware calibration.",
                    friction_key="false_positive_drift_in_high_volatility_windows",
                )
                self.assertTrue(bool(str(quant_hypothesis.get("hypothesis_id") or "")))
                self.assertTrue(bool(str(market_ml_hypothesis.get("hypothesis_id") or "")))
            finally:
                runtime.close()

            artifacts_dir = root / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            quant_artifact_path = artifacts_dir / "quant_eval.json"
            quant_artifact_path.write_text(
                json.dumps(
                    {
                        "environment": "offline_backtest",
                        "baseline": {
                            "metrics": {
                                "sharpe_ratio": 1.02,
                                "max_drawdown": 0.11,
                                "turnover": 4.5,
                            }
                        },
                        "candidate": {
                            "metrics": {
                                "sharpe_ratio": 1.24,
                                "max_drawdown": 0.1,
                                "turnover": 4.8,
                            },
                            "sample_size": 60,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            market_ml_artifact_path = artifacts_dir / "market_ml_eval.json"
            market_ml_artifact_path.write_text(
                json.dumps(
                    {
                        "environment": "offline_backtest",
                        "baseline": {
                            "metrics": {
                                "precision_at_k": 0.31,
                                "false_positive_rate": 0.18,
                                "inference_latency_ms_p95": 220.0,
                            }
                        },
                        "candidate": {
                            "metrics": {
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.25,
                                "inference_latency_ms_p95": 228.0,
                            },
                            "sample_size": 560,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            daily_config_path = root / "daily_config.json"
            daily_config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "hypothesis_lookup_limit": 200,
                            "preferred_statuses": ["testing", "queued", "validated", "rejected"],
                            "auto_retest_lane": False,
                            "include_decision_timeline": False,
                            "collect_experiment_debug": False,
                        },
                        "feedback_jobs": [],
                        "experiment_jobs": [
                            {
                                "domain": "quant_finance",
                                "friction_key": "execution_slippage_regime_drift",
                                "artifact_path": "artifacts/quant_eval.json",
                            },
                            {
                                "domain": "market_ml",
                                "friction_key": "false_positive_drift_in_high_volatility_windows",
                                "artifact_path": "artifacts/market_ml_eval.json",
                            },
                        ],
                        "output_path": "reports/daily_pipeline_report.json",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            daily_args = argparse.Namespace(
                config_path=daily_config_path,
                allow_missing_inputs=False,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            daily_out = io.StringIO()
            with redirect_stdout(daily_out):
                cmd_improvement_daily_pipeline(daily_args)
            daily_payload = json.loads(daily_out.getvalue())
            self.assertEqual(str(daily_payload.get("status") or ""), "ok")
            daily_report_path = root / "reports" / "daily_pipeline_report.json"
            self.assertTrue(daily_report_path.exists())

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "quant_promote_expected",
                                "domain": "quant_finance",
                                "friction_key": "execution_slippage_regime_drift",
                                "artifact_path": "artifacts/quant_eval.json",
                                "expected_verdict": "promote",
                            },
                            {
                                "scenario_id": "market_ml_expected_promote_but_blocked",
                                "domain": "market_ml",
                                "friction_key": "false_positive_drift_in_high_volatility_windows",
                                "artifact_path": "artifacts/market_ml_eval.json",
                                "expected_verdict": "promote",
                            },
                            {
                                "scenario_id": "missing_scenario",
                                "domain": "fitness_apps",
                                "friction_key": "paywall_before_core_workout_trial",
                                "expected_verdict": "promote",
                            },
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            verify_args = argparse.Namespace(
                matrix_path=matrix_path,
                report_path=daily_report_path,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix(verify_args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("drift_severity") or ""), "critical")
            summary = dict(payload.get("summary") or {})
            self.assertEqual(int(summary.get("total_scenarios") or 0), 3)
            self.assertEqual(int(summary.get("matched_count") or 0), 1)
            self.assertEqual(int(summary.get("mismatch_count") or 0), 1)
            self.assertEqual(int(summary.get("missing_count") or 0), 1)
            self.assertEqual(int(summary.get("invalid_count") or 0), 0)

            comparisons = list(payload.get("comparisons") or [])
            statuses = {str((item or {}).get("scenario_id") or ""): str((item or {}).get("status") or "") for item in comparisons}
            self.assertEqual(statuses.get("quant_promote_expected"), "matched")
            self.assertEqual(statuses.get("market_ml_expected_promote_but_blocked"), "mismatch")
            self.assertEqual(statuses.get("missing_scenario"), "missing_run")

    def test_verify_matrix_resolves_daily_report_from_operator_cycle_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            daily_report_path = root / "reports" / "daily_pipeline_report.json"
            daily_report_path.parent.mkdir(parents=True, exist_ok=True)
            daily_report_path.write_text(
                json.dumps(
                    {
                        "experiment_runs": [
                            {
                                "run_id": "exp_1",
                                "hypothesis_id": "hyp_1",
                                "artifact_path": str((root / "artifacts" / "quant_eval.json").resolve()),
                                "verdict": "promote",
                                "resolution": {
                                    "domain": "quant_finance",
                                    "friction_key": "execution_slippage_regime_drift",
                                },
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            operator_report_path = root / "operator_report.json"
            operator_report_path.write_text(
                json.dumps(
                    {
                        "daily_report_path": str(daily_report_path),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "quant_promote_expected",
                                "domain": "quant_finance",
                                "friction_key": "execution_slippage_regime_drift",
                                "expected_verdict": "promote",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            verify_args = argparse.Namespace(
                matrix_path=matrix_path,
                report_path=operator_report_path,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix(verify_args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("drift_severity") or ""), "none")
            summary = dict(payload.get("summary") or {})
            self.assertEqual(int(summary.get("matched_count") or 0), 1)
            self.assertEqual(int(summary.get("mismatch_count") or 0), 0)
            self.assertEqual(int(summary.get("missing_count") or 0), 0)

    def test_verify_matrix_alert_creates_delivered_interrupt_on_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            daily_report_path = root / "reports" / "daily_pipeline_report.json"
            daily_report_path.parent.mkdir(parents=True, exist_ok=True)
            daily_report_path.write_text(
                json.dumps(
                    {
                        "experiment_runs": [
                            {
                                "run_id": "exp_warn_1",
                                "hypothesis_id": "hyp_warn_1",
                                "artifact_path": str((root / "artifacts" / "market_ml_eval.json").resolve()),
                                "verdict": "blocked_guardrail",
                                "resolution": {
                                    "domain": "market_ml",
                                    "friction_key": "false_positive_drift_in_high_volatility_windows",
                                },
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "ml_expected_promote",
                                "domain": "market_ml",
                                "friction_key": "false_positive_drift_in_high_volatility_windows",
                                "expected_verdict": "promote",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                matrix_path=matrix_path,
                report_path=daily_report_path,
                alert_domain="markets",
                alert_urgency=None,
                alert_confidence=None,
                alert_max_items=3,
                output_path=None,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("drift_severity") or ""), "critical")
            self.assertTrue(bool(payload.get("alert_created")))
            alert = dict(payload.get("alert") or {})
            self.assertEqual(str(alert.get("status") or ""), "delivered")
            self.assertEqual(str(alert.get("domain") or ""), "markets")
            self.assertEqual(str(alert.get("drift_severity") or ""), "critical")
            self.assertIn("matrix_drift_detected", str(alert.get("reason") or ""))
            self.assertEqual(float(alert.get("urgency_score") or 0.0), 0.98)
            self.assertEqual(float(alert.get("confidence") or 0.0), 0.95)
            self.assertGreaterEqual(len(list(payload.get("mitigation_actions") or [])), 1)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                interrupts = runtime.list_interrupts(status="all", limit=20)
                self.assertEqual(len(interrupts), 1)
                self.assertEqual(str((interrupts[0] or {}).get("status") or ""), "delivered")
                self.assertEqual(str((interrupts[0] or {}).get("interrupt_id") or ""), str(alert.get("interrupt_id") or ""))
            finally:
                runtime.close()

    def test_verify_matrix_alert_skips_interrupt_when_no_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            daily_report_path = root / "reports" / "daily_pipeline_report.json"
            daily_report_path.parent.mkdir(parents=True, exist_ok=True)
            daily_report_path.write_text(
                json.dumps(
                    {
                        "experiment_runs": [
                            {
                                "run_id": "exp_ok_1",
                                "hypothesis_id": "hyp_ok_1",
                                "artifact_path": str((root / "artifacts" / "quant_eval.json").resolve()),
                                "verdict": "promote",
                                "resolution": {
                                    "domain": "quant_finance",
                                    "friction_key": "execution_slippage_regime_drift",
                                },
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "quant_expected_promote",
                                "domain": "quant_finance",
                                "friction_key": "execution_slippage_regime_drift",
                                "expected_verdict": "promote",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                matrix_path=matrix_path,
                report_path=daily_report_path,
                alert_domain="markets",
                alert_urgency=None,
                alert_confidence=None,
                alert_max_items=3,
                output_path=None,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("drift_severity") or ""), "none")
            self.assertFalse(bool(payload.get("alert_created")))
            self.assertIsNone(payload.get("alert"))
            self.assertGreaterEqual(len(list(payload.get("mitigation_actions") or [])), 1)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                interrupts = runtime.list_interrupts(status="all", limit=20)
                self.assertEqual(len(interrupts), 0)
            finally:
                runtime.close()

    def test_verify_matrix_alert_warn_tier_auto_scales_scores(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            daily_report_path = root / "reports" / "daily_pipeline_report.json"
            daily_report_path.parent.mkdir(parents=True, exist_ok=True)
            daily_report_path.write_text(
                json.dumps(
                    {
                        "experiment_runs": [
                            {
                                "run_id": "exp_warn_tier_1",
                                "hypothesis_id": "hyp_warn_tier_1",
                                "artifact_path": str((root / "artifacts" / "quant_eval.json").resolve()),
                                "verdict": "needs_iteration",
                                "resolution": {
                                    "domain": "quant_finance",
                                    "friction_key": "execution_slippage_regime_drift",
                                },
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "quant_expected_promote",
                                "domain": "quant_finance",
                                "friction_key": "execution_slippage_regime_drift",
                                "expected_verdict": "promote",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                matrix_path=matrix_path,
                report_path=daily_report_path,
                alert_domain="markets",
                alert_urgency=None,
                alert_confidence=None,
                alert_max_items=3,
                output_path=None,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("drift_severity") or ""), "warn")
            self.assertTrue(bool(payload.get("alert_created")))
            alert = dict(payload.get("alert") or {})
            self.assertEqual(float(alert.get("urgency_score") or 0.0), 0.9)
            self.assertEqual(float(alert.get("confidence") or 0.0), 0.86)


if __name__ == "__main__":
    unittest.main()
