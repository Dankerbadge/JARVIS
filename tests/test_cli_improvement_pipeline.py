from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import jarvis.cli as cli_module
from jarvis.cli import (
    cmd_improvement_benchmark_frustrations,
    cmd_improvement_controlled_matrix_compact,
    cmd_improvement_controlled_matrix_runtime_alert,
    cmd_improvement_domain_smoke_cross_domain_compact,
    cmd_improvement_domain_smoke_cross_domain_runtime_alert,
    cmd_improvement_daily_pipeline,
    cmd_improvement_domain_smoke_outputs,
    cmd_improvement_domain_smoke_runtime_alert,
    cmd_improvement_draft_experiment_jobs,
    cmd_improvement_execute_retests,
    cmd_improvement_fitness_leaderboard,
    cmd_improvement_knowledge_brief,
    cmd_improvement_knowledge_bootstrap_route,
    cmd_improvement_knowledge_bootstrap_followup_rerun,
    cmd_improvement_knowledge_bootstrap_route_outputs,
    cmd_improvement_knowledge_brief_delta,
    cmd_improvement_knowledge_brief_delta_alert,
    cmd_improvement_operator_cycle,
    cmd_improvement_pull_feeds,
    cmd_improvement_run_experiment_artifact,
    cmd_improvement_seed_from_leaderboard,
    cmd_improvement_seed_hypotheses,
    cmd_improvement_verify_matrix,
    cmd_improvement_verify_matrix_compact,
    cmd_improvement_verify_matrix_coverage_alert,
    cmd_improvement_verify_matrix_guardrail_gate,
    cmd_improvement_verify_matrix_alert,
)
from jarvis.interrupts import InterruptDecision
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

    def test_pull_feeds_supports_app_store_presets_and_append_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs_dir = root / "inputs"
            inputs_dir.mkdir(parents=True, exist_ok=True)

            apple_csv = inputs_dir / "apple_reviews.csv"
            apple_csv.write_text(
                "\n".join(
                    [
                        "Review ID,Title,Review,Rating,Submission Date,App Version,Storefront",
                        "ios-r1,Too rigid onboarding,Plan was too intense for day one,2,2026-04-10T14:00:00Z,3.4.1,US",
                    ]
                ),
                encoding="utf-8",
            )
            google_csv = inputs_dir / "google_reviews.csv"
            google_csv.write_text(
                "\n".join(
                    [
                        "reviewId,content,score,at,reviewCreatedVersion,replyContent",
                        "and-r1,Paywall appeared before first full workout,1,2026-04-11T09:30:00Z,8.2.0,Thanks for the feedback",
                    ]
                ),
                encoding="utf-8",
            )

            config_path = root / "fitness_market_feeds.json"
            config_path.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "name": "apple_store_reviews",
                                "url": "inputs/apple_reviews.csv",
                                "format": "csv",
                                "source_preset": "apple_app_store_reviews_csv",
                                "write_mode": "overwrite",
                                "static_fields": {
                                    "source_context": "ios_app_store_export",
                                },
                                "output_path": "analysis/fitness_market_feedback.jsonl",
                            },
                            {
                                "name": "google_play_reviews",
                                "url": "inputs/google_reviews.csv",
                                "format": "csv",
                                "source_preset": "google_play_reviews_csv",
                                "write_mode": "append_dedupe",
                                "static_fields": {
                                    "source_context": "google_play_export",
                                },
                                "output_path": "analysis/fitness_market_feedback.jsonl",
                            },
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                config_path=config_path,
                feed_names=None,
                allow_missing=False,
                strict=False,
                timeout_seconds=20.0,
                output_path=None,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_pull_feeds(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("run_count") or 0), 2)
            self.assertEqual(int(payload.get("error_count") or 0), 0)

            runs = list(payload.get("runs") or [])
            self.assertEqual(len(runs), 2)
            self.assertEqual(str((runs[0] or {}).get("write_mode") or ""), "overwrite")
            self.assertEqual(str((runs[1] or {}).get("write_mode") or ""), "append_dedupe")
            self.assertEqual(int((runs[1] or {}).get("final_output_row_count") or 0), 2)
            self.assertEqual(int((runs[1] or {}).get("dedupe_replaced_count") or 0), 0)

            output_path = Path(str((runs[0] or {}).get("output_path") or ""))
            self.assertTrue(output_path.exists())
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 2)

            ios_row = next((row for row in rows if str(row.get("platform") or "") == "ios"), {})
            android_row = next((row for row in rows if str(row.get("platform") or "") == "android"), {})
            self.assertEqual(str(ios_row.get("source_schema") or ""), "apple_app_store_reviews_csv")
            self.assertEqual(str(android_row.get("source_schema") or ""), "google_play_reviews_csv")
            self.assertEqual(str(ios_row.get("source_context") or ""), "ios_app_store_export")
            self.assertEqual(str(android_row.get("source_context") or ""), "google_play_export")
            self.assertEqual(str(ios_row.get("summary") or ""), "Plan was too intense for day one")
            self.assertEqual(str(android_row.get("summary") or ""), "Paywall appeared before first full workout")
            self.assertEqual(str(ios_row.get("rating") or ""), "2")
            self.assertEqual(str(android_row.get("rating") or ""), "1")

    def test_pull_feeds_append_dedupe_is_idempotent_across_repeated_exports(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs_dir = root / "inputs"
            inputs_dir.mkdir(parents=True, exist_ok=True)

            apple_csv = inputs_dir / "apple_reviews.csv"
            apple_csv.write_text(
                "\n".join(
                    [
                        "Review ID,Title,Review,Rating,Submission Date,App Version,Storefront",
                        "ios-r1,Onboarding too hard,Day one workload felt too aggressive,2,2026-04-12T12:00:00Z,3.4.1,US",
                    ]
                ),
                encoding="utf-8",
            )

            config_path = root / "fitness_market_idempotent.json"
            config_path.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "name": "apple_store_reviews",
                                "url": "inputs/apple_reviews.csv",
                                "format": "csv",
                                "source_preset": "apple_app_store_reviews_csv",
                                "write_mode": "append_dedupe",
                                "dedupe_keys": ["platform", "id"],
                                "output_path": "analysis/fitness_market_feedback.jsonl",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                config_path=config_path,
                feed_names=None,
                allow_missing=False,
                strict=False,
                timeout_seconds=20.0,
                output_path=None,
                json_compact=False,
            )
            first_out = io.StringIO()
            with redirect_stdout(first_out):
                cmd_improvement_pull_feeds(args)
            first_payload = json.loads(first_out.getvalue())

            second_out = io.StringIO()
            with redirect_stdout(second_out):
                cmd_improvement_pull_feeds(args)
            second_payload = json.loads(second_out.getvalue())

            self.assertEqual(str(first_payload.get("status") or ""), "ok")
            self.assertEqual(str(second_payload.get("status") or ""), "ok")
            first_run = dict((first_payload.get("runs") or [{}])[0] or {})
            second_run = dict((second_payload.get("runs") or [{}])[0] or {})
            self.assertEqual(str(first_run.get("write_mode") or ""), "append_dedupe")
            self.assertEqual(str(second_run.get("write_mode") or ""), "append_dedupe")
            self.assertEqual(int(first_run.get("dedupe_replaced_count") or 0), 0)
            self.assertEqual(int(second_run.get("dedupe_replaced_count") or 0), 1)
            self.assertEqual(int(second_run.get("final_output_row_count") or 0), 1)

            output_path = Path(str(second_run.get("output_path") or ""))
            self.assertTrue(output_path.exists())
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(str((rows[0] or {}).get("platform") or ""), "ios")
            self.assertEqual(str((rows[0] or {}).get("id") or ""), "ios-r1")

    def test_fitness_leaderboard_reports_week_over_week_trend_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_path = root / "fitness_market_feedback.jsonl"
            rows = [
                {
                    "id": "cur-1",
                    "title": "Paywall too early",
                    "summary": "Paywall appears before first full workout trial.",
                    "review": "Paywall appears before first full workout trial.",
                    "created_at": "2026-04-20T10:00:00Z",
                    "platform": "ios",
                },
                {
                    "id": "cur-2",
                    "title": "Paywall too early",
                    "summary": "Paywall appears before first full workout trial.",
                    "review": "Paywall appears before first full workout trial.",
                    "created_at": "2026-04-19T10:00:00Z",
                    "platform": "android",
                },
                {
                    "id": "cur-3",
                    "title": "Crash on sync",
                    "summary": "Workout sync crash on wearable import.",
                    "review": "Workout sync crash on wearable import.",
                    "created_at": "2026-04-18T09:00:00Z",
                    "platform": "android",
                },
                {
                    "id": "prev-1",
                    "title": "Paywall too early",
                    "summary": "Paywall appears before first full workout trial.",
                    "review": "Paywall appears before first full workout trial.",
                    "created_at": "2026-04-13T09:00:00Z",
                    "platform": "ios",
                },
                {
                    "id": "prev-2",
                    "title": "Onboarding too intense",
                    "summary": "Beginner onboarding plan is too intense and rigid.",
                    "review": "Beginner onboarding plan is too intense and rigid.",
                    "created_at": "2026-04-12T08:00:00Z",
                    "platform": "ios",
                },
                {
                    "id": "old-1",
                    "title": "Old issue",
                    "summary": "Legacy complaint outside tracked windows.",
                    "review": "Legacy complaint outside tracked windows.",
                    "created_at": "2026-03-20T08:00:00Z",
                    "platform": "ios",
                },
            ]
            input_path.write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                encoding="utf-8",
            )

            output_path = root / "reports" / "fitness_leaderboard.json"
            args = argparse.Namespace(
                input_path=input_path,
                input_format="jsonl",
                domain="fitness_apps",
                source="market_reviews",
                timestamp_fields="created_at,at",
                as_of="2026-04-22T00:00:00Z",
                lookback_days=7,
                min_cluster_count=1,
                cluster_limit=20,
                leaderboard_limit=12,
                cooling_limit=10,
                trend_threshold=0.25,
                include_untimed_current=False,
                strict=False,
                output_path=output_path,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_fitness_leaderboard(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertTrue(output_path.exists())
            counts = dict(payload.get("counts") or {})
            self.assertEqual(int(counts.get("current_window_records") or 0), 3)
            self.assertEqual(int(counts.get("previous_window_records") or 0), 2)
            self.assertEqual(int(counts.get("older_records") or 0), 1)

            leaderboard = list(payload.get("leaderboard") or [])
            self.assertGreaterEqual(len(leaderboard), 2)
            paywall_entry = next(
                (
                    row
                    for row in leaderboard
                    if "paywall" in str(row.get("canonical_key") or "")
                ),
                {},
            )
            self.assertTrue(bool(paywall_entry))
            self.assertEqual(str(paywall_entry.get("trend") or ""), "rising")
            self.assertEqual(int(paywall_entry.get("signal_count_current") or 0), 2)
            self.assertEqual(int(paywall_entry.get("signal_count_previous") or 0), 1)
            self.assertGreater(float(paywall_entry.get("impact_score_delta") or 0.0), 0.0)

            cooling = list(payload.get("cooling_clusters") or [])
            self.assertTrue(
                any("onboarding" in str(row.get("canonical_key") or "") for row in cooling)
            )

    def test_fitness_leaderboard_surfaces_cross_app_displeasures_and_whitespace_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_path = root / "fitness_market_feedback.jsonl"
            rows = [
                {
                    "id": "cur-1",
                    "app_name": "FitNova",
                    "summary": "Paywall appears before first full workout trial.",
                    "review": "Paywall appears before first full workout trial.",
                    "created_at": "2026-04-20T10:00:00Z",
                },
                {
                    "id": "cur-2",
                    "app_name": "PulsePro",
                    "summary": "Paywall appears before first full workout trial.",
                    "review": "Paywall appears before first full workout trial.",
                    "created_at": "2026-04-19T10:00:00Z",
                },
                {
                    "id": "cur-3",
                    "app_name": "MyApp",
                    "summary": "Workout sync fails on wearable import.",
                    "review": "Workout sync fails on wearable import.",
                    "created_at": "2026-04-18T09:00:00Z",
                },
                {
                    "id": "prev-1",
                    "app_name": "FitNova",
                    "summary": "Paywall appears before first full workout trial.",
                    "review": "Paywall appears before first full workout trial.",
                    "created_at": "2026-04-13T09:00:00Z",
                },
            ]
            input_path.write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                input_path=input_path,
                input_format="jsonl",
                domain="fitness_apps",
                source="market_reviews",
                timestamp_fields="created_at,at",
                as_of="2026-04-22T00:00:00Z",
                lookback_days=7,
                min_cluster_count=1,
                cluster_limit=20,
                leaderboard_limit=12,
                cooling_limit=10,
                app_fields="app_name,source_context.app",
                top_apps_per_cluster=3,
                min_cross_app_count=2,
                own_app_aliases="MyApp",
                trend_threshold=0.25,
                include_untimed_current=False,
                strict=False,
                output_path=None,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_fitness_leaderboard(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            app_resolution = dict(payload.get("app_resolution") or {})
            self.assertEqual(int(app_resolution.get("known_app_records") or 0), 4)
            self.assertEqual(int(app_resolution.get("unknown_app_records") or 0), 0)

            shared_rows = [dict(item) for item in list(payload.get("shared_market_displeasures") or []) if isinstance(item, dict)]
            paywall_row = next(
                (
                    row
                    for row in shared_rows
                    if "paywall" in str(row.get("canonical_key") or "")
                ),
                {},
            )
            self.assertTrue(bool(paywall_row))
            self.assertGreaterEqual(int(paywall_row.get("cross_app_count_current") or 0), 2)
            top_apps_current = [dict(item) for item in list(paywall_row.get("top_apps_current") or []) if isinstance(item, dict)]
            app_ids = {str(item.get("app_identifier") or "") for item in top_apps_current}
            self.assertIn("fitnova", app_ids)
            self.assertIn("pulsepro", app_ids)

            white_space_rows = [dict(item) for item in list(payload.get("white_space_candidates") or []) if isinstance(item, dict)]
            self.assertTrue(
                any("paywall" in str(row.get("canonical_key") or "") for row in white_space_rows)
            )

    def test_fitness_leaderboard_resolves_app_from_source_context_string(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_path = root / "fitness_market_feedback.jsonl"
            rows = [
                {
                    "id": "cur-1",
                    "summary": "Paywall appears before first full workout trial.",
                    "review": "Paywall appears before first full workout trial.",
                    "source_context": "fitnova_store",
                    "created_at": "2026-04-20T10:00:00Z",
                },
                {
                    "id": "cur-2",
                    "summary": "Workout sync fails on wearable import.",
                    "review": "Workout sync fails on wearable import.",
                    "source_context": "pulsepro_store",
                    "created_at": "2026-04-19T10:00:00Z",
                },
            ]
            input_path.write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                input_path=input_path,
                input_format="jsonl",
                domain="fitness_apps",
                source="market_reviews",
                timestamp_fields="created_at,at",
                as_of="2026-04-22T00:00:00Z",
                lookback_days=7,
                min_cluster_count=1,
                cluster_limit=20,
                leaderboard_limit=12,
                cooling_limit=10,
                trend_threshold=0.25,
                include_untimed_current=False,
                strict=False,
                output_path=None,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_fitness_leaderboard(args)
            payload = json.loads(out.getvalue())

            app_resolution = dict(payload.get("app_resolution") or {})
            self.assertEqual(int(app_resolution.get("known_app_records") or 0), 2)
            self.assertEqual(int(app_resolution.get("unknown_app_records") or 0), 0)
            top_apps_window = [dict(item) for item in list(payload.get("top_apps_current_window") or []) if isinstance(item, dict)]
            app_ids = {str(item.get("app_identifier") or "") for item in top_apps_window}
            self.assertIn("fitnova_store", app_ids)
            self.assertIn("pulsepro_store", app_ids)

    def test_fitness_leaderboard_strict_fails_when_no_current_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_path = root / "fitness_market_feedback.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "id": "old-1",
                        "summary": "Very old complaint",
                        "review": "Very old complaint",
                        "created_at": "2025-01-01T00:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                input_path=input_path,
                input_format="jsonl",
                domain="fitness_apps",
                source="market_reviews",
                timestamp_fields="created_at",
                as_of="2026-04-22T00:00:00Z",
                lookback_days=7,
                min_cluster_count=1,
                cluster_limit=20,
                leaderboard_limit=10,
                cooling_limit=10,
                trend_threshold=0.25,
                include_untimed_current=False,
                strict=True,
                output_path=None,
                json_compact=False,
            )
            out = io.StringIO()
            with self.assertRaises(SystemExit) as raised:
                with redirect_stdout(out):
                    cmd_improvement_fitness_leaderboard(args)
            self.assertEqual(int(getattr(raised.exception, "code", 0) or 0), 2)

    def test_seed_from_leaderboard_creates_and_dedupes_hypotheses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            leaderboard_path = root / "fitness_leaderboard.json"
            leaderboard_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T12:00:00Z",
                        "as_of": "2026-04-22T12:00:00Z",
                        "domain": "fitness_apps",
                        "source": "market_reviews",
                        "leaderboard": [
                            {
                                "rank": 1,
                                "canonical_key": "paywall appears before first full workout trial",
                                "friction_key": "paywall_before_core_workout_trial",
                                "trend": "rising",
                                "impact_score_current": 9.2,
                                "impact_score_delta": 2.5,
                                "signal_count_current": 12,
                                "signal_count_previous": 7,
                                "cross_app_count_current": 3,
                                "top_apps_current": [
                                    {"app_identifier": "fitnova", "count": 6, "share": 0.5},
                                    {"app_identifier": "pulsepro", "count": 4, "share": 0.3333},
                                    {"app_identifier": "trainhero", "count": 2, "share": 0.1667},
                                ],
                                "example_summary": "Users hit paywall before completing a meaningful workout.",
                                "top_tags": [{"tag": "paywall", "count": 12}],
                            },
                            {
                                "rank": 2,
                                "canonical_key": "beginner onboarding too intense and rigid",
                                "friction_key": "onboarding_plan_too_rigid_for_beginner_adherence",
                                "trend": "new",
                                "impact_score_current": 7.8,
                                "impact_score_delta": 7.8,
                                "signal_count_current": 6,
                                "signal_count_previous": 0,
                                "cross_app_count_current": 2,
                                "top_apps_current": [
                                    {"app_identifier": "fitnova", "count": 3, "share": 0.5},
                                    {"app_identifier": "pulsepro", "count": 3, "share": 0.5},
                                ],
                                "example_summary": "Beginner plan intensity causes early drop-off.",
                                "top_tags": [{"tag": "onboarding", "count": 6}],
                            },
                            {
                                "rank": 3,
                                "canonical_key": "minor ui nit",
                                "friction_key": "minor_ui_nit",
                                "trend": "flat",
                                "impact_score_current": 1.0,
                                "impact_score_delta": 0.0,
                                "signal_count_current": 2,
                                "signal_count_previous": 2,
                                "example_summary": "Minor issue with no trend.",
                                "top_tags": [{"tag": "ux", "count": 2}],
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                leaderboard_path=leaderboard_path,
                domain="fitness_apps",
                source="fitness_leaderboard",
                trends="new,rising",
                limit=5,
                min_impact_score=0.0,
                min_impact_delta=0.0,
                owner="operator",
                lookup_limit=200,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            first_out = io.StringIO()
            with redirect_stdout(first_out):
                cmd_improvement_seed_from_leaderboard(args)
            first_payload = json.loads(first_out.getvalue())

            self.assertEqual(str(first_payload.get("status") or ""), "ok")
            self.assertEqual(int(first_payload.get("error_count") or 0), 0)
            self.assertEqual(int(first_payload.get("created_count") or 0), 2)
            self.assertGreaterEqual(int(first_payload.get("skipped_count") or 0), 1)
            self.assertTrue(
                any(str((row or {}).get("reason") or "") == "trend_filtered" for row in list(first_payload.get("skipped") or []))
            )

            second_out = io.StringIO()
            with redirect_stdout(second_out):
                cmd_improvement_seed_from_leaderboard(args)
            second_payload = json.loads(second_out.getvalue())
            self.assertEqual(int(second_payload.get("created_count") or 0), 0)
            self.assertEqual(int(second_payload.get("existing_count") or 0), 2)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypotheses = runtime.list_hypotheses(domain="fitness_apps", status=None, limit=20)
                self.assertEqual(len(hypotheses), 2)
                keys = {str((item or {}).get("friction_key") or "") for item in hypotheses}
                self.assertIn("paywall_before_core_workout_trial", keys)
                self.assertIn("onboarding_plan_too_rigid_for_beginner_adherence", keys)
                metadata = dict((hypotheses[0] or {}).get("metadata") or {})
                self.assertEqual(str(metadata.get("seed_source") or ""), "fitness_leaderboard")
                self.assertGreaterEqual(int(metadata.get("seed_cross_app_count_current") or 0), 2)
                self.assertTrue(bool(list(metadata.get("seed_top_apps_current") or [])))
            finally:
                runtime.close()

    def test_seed_from_leaderboard_can_target_whitespace_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            leaderboard_path = root / "fitness_leaderboard.json"
            leaderboard_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T12:00:00Z",
                        "as_of": "2026-04-22T12:00:00Z",
                        "domain": "fitness_apps",
                        "source": "market_reviews",
                        "leaderboard": [],
                        "white_space_candidates": [
                            {
                                "canonical_key": "paywall appears before first full workout trial",
                                "friction_key": "paywall_before_core_workout_trial",
                                "trend": "rising",
                                "impact_score_current": 8.4,
                                "impact_score_delta": 2.1,
                                "cross_app_count_current": 3,
                                "market_recurrence_score": 25.2,
                                "top_competitor_apps": [
                                    {"app_identifier": "fitnova", "count": 4, "share": 0.5},
                                    {"app_identifier": "pulsepro", "count": 4, "share": 0.5},
                                ],
                                "top_tags": [{"tag": "paywall", "count": 8}],
                                "example_summary": "Competitor users report paywall before meaningful workout value.",
                            },
                            {
                                "canonical_key": "minor ui nit",
                                "friction_key": "minor_ui_nit",
                                "trend": "rising",
                                "impact_score_current": 1.0,
                                "impact_score_delta": 0.1,
                                "cross_app_count_current": 1,
                                "market_recurrence_score": 1.0,
                                "top_competitor_apps": [{"app_identifier": "fitnova", "count": 1, "share": 1.0}],
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                leaderboard_path=leaderboard_path,
                domain="fitness_apps",
                source="fitness_leaderboard",
                trends="new,rising",
                limit=5,
                min_impact_score=0.0,
                min_impact_delta=0.0,
                entry_source="white_space_candidates",
                fallback_entry_source="none",
                min_cross_app_count=2,
                owner="operator",
                lookup_limit=200,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_seed_from_leaderboard(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("entry_source") or ""), "white_space_candidates")
            self.assertEqual(int(payload.get("requested_count") or 0), 2)
            self.assertEqual(int(payload.get("selected_count") or 0), 2)
            self.assertEqual(int(payload.get("created_count") or 0), 1)
            self.assertTrue(
                any(
                    str((row or {}).get("reason") or "") == "cross_app_count_below_min"
                    for row in list(payload.get("skipped") or [])
                )
            )

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypotheses = runtime.list_hypotheses(domain="fitness_apps", status=None, limit=20)
                self.assertEqual(len(hypotheses), 1)
                hypothesis = dict(hypotheses[0] or {})
                self.assertEqual(str(hypothesis.get("friction_key") or ""), "paywall_before_core_workout_trial")
                metadata = dict(hypothesis.get("metadata") or {})
                self.assertEqual(str(metadata.get("seed_entry_source") or ""), "white_space_candidates")
                self.assertEqual(int(metadata.get("seed_cross_app_count_current") or 0), 3)
            finally:
                runtime.close()

    def test_seed_from_leaderboard_falls_back_to_leaderboard_when_source_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            leaderboard_path = root / "fitness_leaderboard.json"
            leaderboard_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T12:00:00Z",
                        "as_of": "2026-04-22T12:00:00Z",
                        "domain": "fitness_apps",
                        "source": "market_reviews",
                        "leaderboard": [
                            {
                                "rank": 1,
                                "canonical_key": "paywall appears before first full workout trial",
                                "friction_key": "paywall_before_core_workout_trial",
                                "trend": "rising",
                                "impact_score_current": 8.5,
                                "impact_score_delta": 2.0,
                                "signal_count_current": 10,
                                "signal_count_previous": 6,
                                "cross_app_count_current": 1,
                                "top_apps_current": [{"app_identifier": "unknown_app", "count": 10, "share": 1.0}],
                            }
                        ],
                        "shared_market_displeasures": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                leaderboard_path=leaderboard_path,
                domain="fitness_apps",
                source="fitness_leaderboard",
                trends="new,rising",
                limit=5,
                min_impact_score=0.0,
                min_impact_delta=0.0,
                entry_source="shared_market_displeasures",
                fallback_entry_source="leaderboard",
                min_cross_app_count=0,
                owner="operator",
                lookup_limit=200,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_seed_from_leaderboard(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("requested_entry_source") or ""), "shared_market_displeasures")
            self.assertEqual(str(payload.get("entry_source") or ""), "leaderboard")
            self.assertTrue(bool(payload.get("fallback_triggered")))
            self.assertEqual(int(payload.get("created_count") or 0), 1)
            source_counts = dict(payload.get("available_entry_source_counts") or {})
            self.assertEqual(int(source_counts.get("shared_market_displeasures") or 0), 0)
            self.assertEqual(int(source_counts.get("leaderboard") or 0), 1)

    def test_seed_from_leaderboard_filters_low_signal_count_current(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            leaderboard_path = root / "fitness_leaderboard.json"
            leaderboard_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T12:00:00Z",
                        "as_of": "2026-04-22T12:00:00Z",
                        "domain": "fitness_apps",
                        "source": "market_reviews",
                        "leaderboard": [
                            {
                                "rank": 1,
                                "canonical_key": "paywall appears before first full workout trial",
                                "friction_key": "paywall_before_core_workout_trial",
                                "trend": "rising",
                                "impact_score_current": 8.5,
                                "impact_score_delta": 2.0,
                                "signal_count_current": 1,
                                "signal_count_previous": 0,
                                "cross_app_count_current": 2,
                            },
                            {
                                "rank": 2,
                                "canonical_key": "beginner onboarding too intense and rigid",
                                "friction_key": "onboarding_plan_too_rigid_for_beginner_adherence",
                                "trend": "new",
                                "impact_score_current": 7.0,
                                "impact_score_delta": 7.0,
                                "signal_count_current": 4,
                                "signal_count_previous": 0,
                                "cross_app_count_current": 2,
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                leaderboard_path=leaderboard_path,
                domain="fitness_apps",
                source="fitness_leaderboard",
                trends="new,rising",
                limit=5,
                min_impact_score=0.0,
                min_impact_delta=0.0,
                entry_source="leaderboard",
                fallback_entry_source="none",
                min_cross_app_count=0,
                min_signal_count_current=3,
                owner="operator",
                lookup_limit=200,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_seed_from_leaderboard(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(int(payload.get("min_signal_count_current") or 0), 3)
            self.assertEqual(int(payload.get("created_count") or 0), 1)
            self.assertTrue(
                any(
                    str((row or {}).get("reason") or "") == "signal_count_current_below_min"
                    for row in list(payload.get("skipped") or [])
                )
            )

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypotheses = runtime.list_hypotheses(domain="fitness_apps", status=None, limit=20)
                self.assertEqual(len(hypotheses), 1)
                self.assertEqual(
                    str((hypotheses[0] or {}).get("friction_key") or ""),
                    "onboarding_plan_too_rigid_for_beginner_adherence",
                )
            finally:
                runtime.close()

    def test_draft_experiment_jobs_from_seed_report_writes_templates_and_updates_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            leaderboard_path = root / "fitness_leaderboard.json"
            leaderboard_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T12:00:00Z",
                        "as_of": "2026-04-22T12:00:00Z",
                        "domain": "fitness_apps",
                        "source": "market_reviews",
                        "leaderboard": [
                            {
                                "rank": 1,
                                "canonical_key": "paywall appears before first full workout trial",
                                "friction_key": "paywall_before_core_workout_trial",
                                "trend": "rising",
                                "impact_score_current": 9.2,
                                "impact_score_delta": 2.5,
                                "signal_count_current": 12,
                                "signal_count_previous": 7,
                            },
                            {
                                "rank": 2,
                                "canonical_key": "beginner onboarding too intense and rigid",
                                "friction_key": "onboarding_plan_too_rigid_for_beginner_adherence",
                                "trend": "new",
                                "impact_score_current": 7.8,
                                "impact_score_delta": 7.8,
                                "signal_count_current": 6,
                                "signal_count_previous": 0,
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            seed_report_path = root / "reports" / "seed_report.json"
            seed_args = argparse.Namespace(
                leaderboard_path=leaderboard_path,
                domain="fitness_apps",
                source="fitness_leaderboard",
                trends="new,rising",
                limit=8,
                min_impact_score=0.0,
                min_impact_delta=0.0,
                owner="operator",
                lookup_limit=200,
                strict=False,
                output_path=seed_report_path,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            seed_out = io.StringIO()
            with redirect_stdout(seed_out):
                cmd_improvement_seed_from_leaderboard(seed_args)
            seed_payload = json.loads(seed_out.getvalue())
            created = [dict(item) for item in list(seed_payload.get("created") or []) if isinstance(item, dict)]
            self.assertEqual(len(created), 2)

            existing_hypothesis_id = str((created[0] or {}).get("hypothesis_id") or "")
            self.assertTrue(bool(existing_hypothesis_id))
            pipeline_config_path = root / "pipeline_config.json"
            pipeline_config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                        },
                        "feedback_jobs": [],
                        "experiment_jobs": [
                            {
                                "hypothesis_id": existing_hypothesis_id,
                                "domain": "fitness_apps",
                                "friction_key": "paywall_before_core_workout_trial",
                                "artifact_path": "artifacts/existing_artifact.json",
                                "environment": "controlled_rollout",
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            drafted_config_path = root / "reports" / "pipeline_with_drafts.json"
            draft_report_path = root / "reports" / "draft_experiment_jobs_report.json"
            draft_args = argparse.Namespace(
                seed_report_path=seed_report_path,
                include_existing=False,
                domain="fitness_apps",
                statuses="queued",
                limit=8,
                lookup_limit=400,
                pipeline_config_path=pipeline_config_path,
                write_config_path=drafted_config_path,
                in_place=False,
                artifacts_dir=Path("artifacts/experiments"),
                overwrite_artifacts=False,
                environment="controlled_rollout",
                default_sample_size=120,
                strict=False,
                output_path=draft_report_path,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            draft_out = io.StringIO()
            with redirect_stdout(draft_out):
                cmd_improvement_draft_experiment_jobs(draft_args)
            draft_payload = json.loads(draft_out.getvalue())

            self.assertEqual(str(draft_payload.get("status") or ""), "ok")
            self.assertEqual(int(draft_payload.get("drafted_count") or 0), 2)
            self.assertEqual(int(draft_payload.get("artifact_created_count") or 0), 2)
            self.assertEqual(int(draft_payload.get("config_appended_count") or 0), 1)
            self.assertTrue(draft_report_path.exists())
            self.assertTrue(drafted_config_path.exists())

            drafts = [dict(item) for item in list(draft_payload.get("drafts") or []) if isinstance(item, dict)]
            self.assertEqual(len(drafts), 2)
            for row in drafts:
                self.assertEqual(int(row.get("target_sample_size") or 0), 200)
                self.assertEqual(str(row.get("primary_metric") or ""), "retention_d30")
                self.assertTrue(bool(list(row.get("guardrails") or [])))
                artifact_path = Path(str(row.get("artifact_path") or ""))
                self.assertTrue(artifact_path.exists())
                artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                self.assertEqual(int(artifact_payload.get("sample_size") or 0), 200)
                metadata = dict(artifact_payload.get("metadata") or {})
                self.assertEqual(
                    str((metadata.get("success_criteria") or {}).get("metric") or ""),
                    "retention_d30",
                )

            updated_pipeline = json.loads(drafted_config_path.read_text(encoding="utf-8"))
            updated_jobs = [dict(item) for item in list(updated_pipeline.get("experiment_jobs") or []) if isinstance(item, dict)]
            self.assertEqual(len(updated_jobs), 2)
            updated_ids = {str(item.get("hypothesis_id") or "") for item in updated_jobs}
            expected_ids = {str(item.get("hypothesis_id") or "") for item in created}
            self.assertEqual(updated_ids, expected_ids)

    def test_draft_experiment_jobs_selects_queued_hypotheses_without_seed_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Reduce false positives in volatility spikes",
                    statement="False positives rise under volatility regime changes.",
                    proposed_change="Use volatility-aware confidence calibration.",
                    friction_key="false_positive_drift_in_high_volatility_windows",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
            finally:
                runtime.close()

            artifacts_dir = root / "artifacts" / "drafted"
            args = argparse.Namespace(
                seed_report_path=None,
                include_existing=False,
                domain="market_ml",
                statuses="queued",
                limit=5,
                lookup_limit=50,
                pipeline_config_path=None,
                write_config_path=None,
                in_place=False,
                artifacts_dir=artifacts_dir,
                overwrite_artifacts=False,
                environment=None,
                default_sample_size=150,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_draft_experiment_jobs(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("drafted_count") or 0), 1)
            self.assertEqual(int(payload.get("config_appended_count") or 0), 0)
            drafts = [dict(item) for item in list(payload.get("drafts") or []) if isinstance(item, dict)]
            self.assertEqual(len(drafts), 1)

            row = drafts[0]
            self.assertEqual(str(row.get("hypothesis_id") or ""), hypothesis_id)
            self.assertEqual(str((row.get("job") or {}).get("environment") or ""), "controlled_backtest")
            self.assertEqual(int(row.get("target_sample_size") or 0), 200)
            self.assertTrue(bool(list(row.get("guardrails") or [])))

            artifact_path = Path(str(row.get("artifact_path") or ""))
            self.assertTrue(artifact_path.exists())
            artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(str(artifact_payload.get("environment") or ""), "controlled_backtest")
            self.assertEqual(int(artifact_payload.get("sample_size") or 0), 200)

    def test_draft_experiment_jobs_outputs_pipeline_executable_artifact_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Boost precision while guarding fp and latency",
                    statement="A calibrated threshold should improve precision-at-k.",
                    proposed_change="Apply volatility-aware confidence calibration.",
                    friction_key="false_positive_drift_in_high_volatility_windows",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
            finally:
                runtime.close()

            drafted_config_path = root / "pipeline_drafted.json"
            draft_args = argparse.Namespace(
                seed_report_path=None,
                include_existing=False,
                domain="market_ml",
                statuses="queued",
                limit=5,
                lookup_limit=50,
                pipeline_config_path=root / "pipeline_base.json",
                write_config_path=drafted_config_path,
                in_place=False,
                artifacts_dir=Path("artifacts/drafted"),
                overwrite_artifacts=False,
                environment="controlled_backtest",
                default_sample_size=220,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            (root / "pipeline_base.json").write_text(
                json.dumps(
                    {
                        "defaults": {
                            "allow_missing_inputs": False,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            draft_out = io.StringIO()
            with redirect_stdout(draft_out):
                cmd_improvement_draft_experiment_jobs(draft_args)
            drafted_payload = json.loads(draft_out.getvalue())
            self.assertEqual(str(drafted_payload.get("status") or ""), "ok")
            self.assertEqual(int(drafted_payload.get("config_appended_count") or 0), 1)

            drafted_config = json.loads(drafted_config_path.read_text(encoding="utf-8"))
            experiment_jobs = [dict(item) for item in list(drafted_config.get("experiment_jobs") or []) if isinstance(item, dict)]
            self.assertEqual(len(experiment_jobs), 1)
            self.assertEqual(str((experiment_jobs[0] or {}).get("hypothesis_id") or ""), hypothesis_id)

            artifact_path = root / str((experiment_jobs[0] or {}).get("artifact_path") or "")
            self.assertTrue(artifact_path.exists())
            artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertTrue(bool(dict(artifact_payload.get("baseline_metrics") or {})))
            self.assertTrue(bool(dict(artifact_payload.get("candidate_metrics") or {})))

            daily_args = argparse.Namespace(
                config_path=drafted_config_path,
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

            self.assertEqual(int(daily_payload.get("error_count") or 0), 0)
            self.assertEqual(int(daily_payload.get("experiment_runs_count") or 0), 1)
            experiment_runs = [dict(item) for item in list(daily_payload.get("experiment_runs") or []) if isinstance(item, dict)]
            self.assertEqual(len(experiment_runs), 1)

    def test_draft_experiment_jobs_prioritizes_benchmark_targets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                fitness_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay paywall until first workout trial",
                    statement="Paywall appears before users complete a first workout.",
                    proposed_change="Delay paywall gate until one full workout completion.",
                    friction_key="paywall_before_core_workout_trial",
                )
                quant_hypothesis = runtime.register_hypothesis(
                    domain="quant_finance",
                    title="Reduce execution slippage during regime shifts",
                    statement="Execution slippage widens in volatile opens.",
                    proposed_change="Apply regime-aware order routing with spread guardrails.",
                    friction_key="execution_slippage_regime_drift",
                )
                fitness_hypothesis_id = str(fitness_hypothesis.get("hypothesis_id") or "")
                quant_hypothesis_id = str(quant_hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(fitness_hypothesis_id))
                self.assertTrue(bool(quant_hypothesis_id))
            finally:
                runtime.close()

            benchmark_report_path = root / "reports" / "frustration_benchmark_report.json"
            benchmark_report_path.parent.mkdir(parents=True, exist_ok=True)
            benchmark_report_path.write_text(
                json.dumps(
                    {
                        "summary": {"domain_count": 2},
                        "priority_board": [
                            {
                                "domain": "fitness_apps",
                                "friction_key": "paywall_before_core_workout_trial",
                                "trend": "rising",
                                "recurrence_score": 8,
                                "opportunity_score": 6.2,
                            },
                            {
                                "domain": "quant_finance",
                                "friction_key": "execution_slippage_regime_drift",
                                "trend": "new",
                                "recurrence_score": 3,
                                "opportunity_score": 1.4,
                            },
                        ],
                        "recurring_pains": [
                            {
                                "domain": "fitness_apps",
                                "friction_key": "paywall_before_core_workout_trial",
                                "hypothesis_ids": [fitness_hypothesis_id],
                            },
                            {
                                "domain": "quant_finance",
                                "friction_key": "execution_slippage_regime_drift",
                                "hypothesis_ids": [quant_hypothesis_id],
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            draft_args = argparse.Namespace(
                seed_report_path=None,
                benchmark_report_path=benchmark_report_path,
                benchmark_min_opportunity=2.0,
                include_existing=False,
                domain=None,
                statuses="queued",
                limit=5,
                lookup_limit=100,
                pipeline_config_path=None,
                write_config_path=None,
                in_place=False,
                artifacts_dir=root / "artifacts" / "benchmark_drafts",
                overwrite_artifacts=False,
                environment=None,
                default_sample_size=140,
                strict=False,
                output_path=None,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_draft_experiment_jobs(draft_args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("benchmark_target_count") or 0), 1)
            self.assertEqual(int(payload.get("candidate_seed_count") or 0), 1)
            self.assertEqual(int(payload.get("drafted_count") or 0), 1)
            drafts = [dict(item) for item in list(payload.get("drafts") or []) if isinstance(item, dict)]
            self.assertEqual(len(drafts), 1)

            draft = drafts[0]
            self.assertEqual(str(draft.get("hypothesis_id") or ""), fitness_hypothesis_id)
            source_hint = dict(draft.get("source_hint") or {})
            self.assertEqual(str(source_hint.get("seed_reason") or ""), "benchmark_priority")
            self.assertEqual(int(source_hint.get("benchmark_priority_rank") or 0), 1)
            self.assertEqual(float(source_hint.get("benchmark_opportunity_score") or 0.0), 6.2)

            artifact_path = Path(str(draft.get("artifact_path") or ""))
            self.assertTrue(artifact_path.exists())

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
            self.assertEqual(str(stage_statuses.get("fitness_leaderboard") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("draft_experiment_jobs") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("daily_pipeline") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("execute_retests") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("benchmark_frustrations") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("verify_matrix") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("verify_matrix_alert") or ""), "skipped_not_requested")
            self.assertEqual(int(payload.get("stage_error_count") or 0), 0)

            operator_report_path = Path(str(payload.get("operator_report_path") or ""))
            pull_report_path = Path(str(payload.get("pull_report_path") or ""))
            daily_report_path = Path(str(payload.get("daily_report_path") or ""))
            retest_report_path = Path(str(payload.get("retest_report_path") or ""))
            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(operator_report_path.exists())
            self.assertTrue(pull_report_path.exists())
            self.assertTrue(daily_report_path.exists())
            self.assertTrue(retest_report_path.exists())
            self.assertTrue(inbox_summary_path.exists())

            retest_payload = json.loads(retest_report_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(retest_payload.get("executed_count") or 0), 1)

            summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(str((summary.get("stage_statuses") or {}).get("daily_pipeline") or ""), "ok")
            self.assertGreaterEqual(int((summary.get("metrics") or {}).get("retest_delta_count") or 0), 1)
            self.assertFalse(bool((summary.get("promotion_lock") or {}).get("active")))
            self.assertEqual(int((summary.get("metrics") or {}).get("blocked_promotion_count") or 0), 0)
            self.assertEqual(len(list(summary.get("blocked_promotions") or [])), 0)
            self.assertGreaterEqual(len(list(summary.get("suggested_actions") or [])), 1)

    def test_operator_cycle_runs_benchmark_stage_with_cli_top_limit_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator benchmark stage hypothesis",
                    statement="Operator cycle benchmark stage should run and persist ranked outputs.",
                    proposed_change="Capture benchmark synthesis after controlled retest loops.",
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
                                "id": "feed-benchmark-1",
                                "title": "Paywall appears too early",
                                "text": "Paywall appears before I can try any workout.",
                                "rating": 2,
                            }
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

            config_path = root / "operator_cycle_benchmark_config.json"
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
                            "benchmark_top_limit": 9,
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

            output_dir = root / "output" / "operator_benchmark"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=True,
                benchmark_top_limit=4,
                benchmark_report_path=None,
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
            self.assertEqual(str(stage_statuses.get("benchmark_frustrations") or ""), "warning")
            self.assertEqual(str(stage_statuses.get("verify_matrix") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("verify_matrix_alert") or ""), "skipped_not_requested")

            benchmark_payload = dict(payload.get("benchmark") or {})
            self.assertEqual(str(benchmark_payload.get("status") or ""), "warning")
            self.assertEqual(int(benchmark_payload.get("top_limit") or 0), 4)
            self.assertEqual(str(benchmark_payload.get("top_limit_source") or ""), "cli_override")
            self.assertGreaterEqual(len(list(benchmark_payload.get("suggested_actions") or [])), 1)

            benchmark_report_path = Path(str(payload.get("benchmark_report_path") or ""))
            operator_report_path = Path(str(payload.get("operator_report_path") or ""))
            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(benchmark_report_path.exists())
            self.assertTrue(operator_report_path.exists())
            self.assertTrue(inbox_summary_path.exists())

            benchmark_report = json.loads(benchmark_report_path.read_text(encoding="utf-8"))
            self.assertEqual(int(benchmark_report.get("top_limit") or 0), 4)
            self.assertEqual(str(benchmark_report.get("status") or ""), "warning")

            summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(((summary.get("benchmark") or {}).get("top_limit_source") or "")),
                "cli_override",
            )
            self.assertEqual(
                str(((summary.get("stage_statuses") or {}).get("benchmark_frustrations") or "")),
                "warning",
            )
            self.assertEqual(
                str(((summary.get("stage_statuses") or {}).get("verify_matrix") or "")),
                "skipped_not_requested",
            )
            self.assertEqual(
                str(((summary.get("stage_statuses") or {}).get("verify_matrix_alert") or "")),
                "skipped_not_requested",
            )

    def test_operator_cycle_runs_verify_matrix_stage_and_gates_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator verify matrix stage hypothesis",
                    statement="Operator cycle should run verify-matrix and gate overall status on drift.",
                    proposed_change="Block advancement when expected verdicts mismatch.",
                    friction_key="false_positive_drift_in_high_volatility_windows",
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
                                "id": "feed-verify-1",
                                "title": "No free trial path",
                                "text": "No way to test features before paying.",
                                "rating": 2,
                            }
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
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 205,
                            },
                            "sample_size": 520,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "market_ml_expected_promote",
                                "domain": "market_ml",
                                "friction_key": "false_positive_drift_in_high_volatility_windows",
                                "artifact_path": "artifacts/blocked_eval.json",
                                "expected_verdict": "blocked_guardrail",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_verify_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 4,
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

            output_dir = root / "output" / "operator_verify"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=False,
                benchmark_top_limit=None,
                benchmark_report_path=None,
                verify_matrix_enable=True,
                verify_matrix_path=matrix_path,
                verify_matrix_report_path=None,
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

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(int(payload.get("stage_error_count") or 0), 0)
            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("benchmark_frustrations") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("verify_matrix") or ""), "warning")
            self.assertEqual(str(stage_statuses.get("verify_matrix_alert") or ""), "skipped_not_requested")

            verify_payload = dict(payload.get("verify_matrix") or {})
            self.assertEqual(str(verify_payload.get("status") or ""), "warning")
            self.assertEqual(str(verify_payload.get("matrix_path_source") or ""), "cli_override")
            self.assertEqual(str(verify_payload.get("drift_severity") or ""), "warn")

            verify_report_path = Path(str(payload.get("verify_matrix_report_path") or ""))
            self.assertTrue(verify_report_path.exists())
            verify_report = json.loads(verify_report_path.read_text(encoding="utf-8"))
            summary = dict(verify_report.get("summary") or {})
            self.assertGreaterEqual(
                int(summary.get("mismatch_count") or 0) + int(summary.get("missing_count") or 0),
                1,
            )

            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(inbox_summary_path.exists())
            inbox_summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("verify_matrix") or "")),
                "warning",
            )
            self.assertEqual(
                str((((inbox_summary.get("verify_matrix") or {}).get("matrix_path_source")) or "")),
                "cli_override",
            )
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("verify_matrix_alert") or "")),
                "skipped_not_requested",
            )

    def test_operator_cycle_runs_verify_matrix_alert_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator verify matrix alert stage hypothesis",
                    statement="Operator cycle should raise a verify-matrix alert when drift is detected.",
                    proposed_change="Escalate controlled-experiment matrix drift into interrupts.",
                    friction_key="false_positive_drift_in_high_volatility_windows",
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
                                "id": "feed-verify-alert-1",
                                "title": "No free trial path",
                                "text": "No way to test features before paying.",
                                "rating": 2,
                            }
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
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 205,
                            },
                            "sample_size": 520,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "market_ml_expected_promote",
                                "domain": "market_ml",
                                "friction_key": "false_positive_drift_in_high_volatility_windows",
                                "artifact_path": "artifacts/blocked_eval.json",
                                "expected_verdict": "blocked_guardrail",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_verify_alert_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 4,
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

            output_dir = root / "output" / "operator_verify_alert"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=False,
                benchmark_top_limit=None,
                benchmark_report_path=None,
                verify_matrix_enable=True,
                verify_matrix_path=matrix_path,
                verify_matrix_report_path=None,
                verify_matrix_alert_enable=True,
                verify_matrix_alert_domain="market_ml",
                verify_matrix_alert_max_items=2,
                verify_matrix_alert_urgency=None,
                verify_matrix_alert_confidence=None,
                verify_matrix_alert_report_path=None,
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

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(int(payload.get("stage_error_count") or 0), 0)
            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("verify_matrix") or ""), "warning")
            self.assertEqual(str(stage_statuses.get("verify_matrix_alert") or ""), "warning")

            verify_alert_payload = dict(payload.get("verify_matrix_alert") or {})
            self.assertEqual(str(verify_alert_payload.get("status") or ""), "warning")
            self.assertTrue(bool(verify_alert_payload.get("alert_created")))
            self.assertEqual(str(verify_alert_payload.get("alert_domain_source") or ""), "cli_override")
            self.assertEqual(str(verify_alert_payload.get("alert_max_items_source") or ""), "cli_override")
            alert = dict(verify_alert_payload.get("alert") or {})
            alert_interrupt_id = str(alert.get("interrupt_id") or "")
            self.assertTrue(bool(alert_interrupt_id))

            promotion_lock = dict(payload.get("promotion_lock") or {})
            self.assertTrue(bool(promotion_lock.get("active")))
            self.assertTrue(bool(promotion_lock.get("requires_acknowledgement")))
            self.assertIn(alert_interrupt_id, list(promotion_lock.get("blocking_interrupt_ids") or []))
            acknowledge_commands = [str(item) for item in list(promotion_lock.get("acknowledge_commands") or [])]
            self.assertTrue(any(alert_interrupt_id in item for item in acknowledge_commands))
            self.assertFalse(bool(promotion_lock.get("unlock_ready")))
            self.assertEqual(
                str((promotion_lock.get("blocking_interrupt_statuses") or {}).get(alert_interrupt_id) or ""),
                "delivered",
            )
            self.assertEqual(int(promotion_lock.get("blocked_promotion_count") or 0), 1)
            self.assertEqual(int(promotion_lock.get("promotion_candidates_count") or 0), 1)
            recheck_command = str(promotion_lock.get("recheck_command") or "")
            self.assertTrue(recheck_command.startswith("python3 -m jarvis.cli improvement operator-cycle"))
            self.assertIn("--verify-matrix-enable", recheck_command)
            self.assertIn("--verify-matrix-alert-enable", recheck_command)
            self.assertIn("--verify-matrix-path", recheck_command)
            self.assertEqual(
                list(promotion_lock.get("unlock_ready_commands") or []),
                [recheck_command],
            )
            self.assertEqual(
                str(promotion_lock.get("first_unlock_ready_command") or ""),
                recheck_command,
            )

            self.assertEqual(list(payload.get("promotions") or []), [])
            blocked_promotions = [dict(item) for item in list(payload.get("blocked_promotions") or []) if isinstance(item, dict)]
            self.assertEqual(len(blocked_promotions), 1)
            self.assertEqual(str(blocked_promotions[0].get("blocked_by") or ""), "verify_matrix_alert")
            unlock_readiness = dict(blocked_promotions[0].get("unlock_readiness") or {})
            self.assertFalse(bool(unlock_readiness.get("unlock_ready")))
            self.assertTrue(bool(unlock_readiness.get("requires_acknowledgement")))
            self.assertIn(alert_interrupt_id, list(unlock_readiness.get("blocking_interrupt_ids") or []))
            self.assertEqual(
                str((unlock_readiness.get("blocking_interrupt_statuses") or {}).get(alert_interrupt_id) or ""),
                "delivered",
            )
            unlock_ack_commands = [str(item) for item in list(unlock_readiness.get("acknowledge_commands") or [])]
            self.assertTrue(any(alert_interrupt_id in item for item in unlock_ack_commands))
            self.assertEqual(str(unlock_readiness.get("recheck_command") or ""), recheck_command)
            self.assertEqual(
                list(unlock_readiness.get("unlock_ready_commands") or []),
                [recheck_command],
            )
            self.assertEqual(
                str(unlock_readiness.get("first_unlock_ready_command") or ""),
                recheck_command,
            )
            self.assertEqual(int((payload.get("metrics") or {}).get("blocked_promotion_count") or 0), 1)
            self.assertEqual(int((payload.get("metrics") or {}).get("promotion_count") or 0), 0)

            verify_alert_report_path = Path(str(payload.get("verify_matrix_alert_report_path") or ""))
            self.assertTrue(verify_alert_report_path.exists())
            verify_alert_report = json.loads(verify_alert_report_path.read_text(encoding="utf-8"))
            self.assertTrue(bool(verify_alert_report.get("alert_created")))

            operator_report_path = Path(str(payload.get("operator_report_path") or ""))
            self.assertTrue(operator_report_path.exists())
            operator_report = json.loads(operator_report_path.read_text(encoding="utf-8"))
            report_promotion_lock = dict(operator_report.get("promotion_lock") or {})
            self.assertEqual(
                list(report_promotion_lock.get("unlock_ready_commands") or []),
                [recheck_command],
            )
            self.assertEqual(
                str(report_promotion_lock.get("first_unlock_ready_command") or ""),
                recheck_command,
            )

            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(inbox_summary_path.exists())
            inbox_summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("verify_matrix_alert") or "")),
                "warning",
            )
            self.assertTrue(bool(((inbox_summary.get("verify_matrix_alert") or {}).get("alert_created"))))
            self.assertTrue(bool(((inbox_summary.get("promotion_lock") or {}).get("active"))))
            summary_promotion_lock = dict(inbox_summary.get("promotion_lock") or {})
            self.assertEqual(
                list(summary_promotion_lock.get("unlock_ready_commands") or []),
                [recheck_command],
            )
            self.assertEqual(
                str(summary_promotion_lock.get("first_unlock_ready_command") or ""),
                recheck_command,
            )
            self.assertEqual(list(inbox_summary.get("promotions") or []), [])
            summary_blocked_promotions = [
                dict(item) for item in list(inbox_summary.get("blocked_promotions") or []) if isinstance(item, dict)
            ]
            self.assertEqual(len(summary_blocked_promotions), 1)
            summary_unlock = dict(summary_blocked_promotions[0].get("unlock_readiness") or {})
            self.assertEqual(str(summary_unlock.get("recheck_command") or ""), recheck_command)
            self.assertEqual(
                list(summary_unlock.get("unlock_ready_commands") or []),
                [recheck_command],
            )
            self.assertEqual(
                str(summary_unlock.get("first_unlock_ready_command") or ""),
                recheck_command,
            )
            self.assertFalse(bool(summary_unlock.get("unlock_ready")))

    def test_operator_cycle_runs_knowledge_brief_delta_alert_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator knowledge delta alert hypothesis",
                    statement="Operator cycle should surface knowledge-delta alert stage results when enabled.",
                    proposed_change="Escalate knowledge brief drift regressions into interrupts.",
                    friction_key="fitness_onboarding_paywall_fatigue",
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
                                "id": "feed-knowledge-alert-1",
                                "title": "No free trial path",
                                "text": "No way to test features before paying.",
                                "rating": 2,
                            }
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
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 205,
                            },
                            "sample_size": 520,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_knowledge_alert_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 4,
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

            expected_knowledge_brief_report_path = root / "reports" / "knowledge_brief_report.json"
            expected_delta_alert_report_path = root / "reports" / "knowledge_brief_delta_alert_report.json"
            output_dir = root / "output" / "operator_knowledge_delta_alert"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=False,
                benchmark_top_limit=None,
                benchmark_report_path=None,
                verify_matrix_enable=False,
                verify_matrix_path=None,
                verify_matrix_report_path=None,
                verify_matrix_alert_enable=False,
                verify_matrix_alert_domain=None,
                verify_matrix_alert_max_items=None,
                verify_matrix_alert_urgency=None,
                verify_matrix_alert_confidence=None,
                verify_matrix_alert_report_path=None,
                knowledge_brief_enable=True,
                knowledge_brief_query=None,
                knowledge_brief_snapshot_label=None,
                knowledge_brief_report_path=expected_knowledge_brief_report_path,
                knowledge_delta_alert_enable=True,
                knowledge_delta_alert_domain="operations",
                knowledge_delta_alert_max_items=3,
                knowledge_delta_alert_urgency=None,
                knowledge_delta_alert_confidence=None,
                knowledge_delta_alert_report_path=expected_delta_alert_report_path,
                knowledge_brief_delta_alert_enable=True,
                knowledge_brief_delta_alert_domain="operations",
                knowledge_brief_delta_alert_max_items=3,
                knowledge_brief_delta_alert_urgency=None,
                knowledge_brief_delta_alert_confidence=None,
                knowledge_brief_delta_alert_report_path=expected_delta_alert_report_path,
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

            original_invoke = cli_module._invoke_cli_json_command
            stage_call_order: list[str] = []

            def patched_invoke(command_fn: Any, *, args: argparse.Namespace) -> dict[str, Any]:
                if getattr(command_fn, "__name__", "") == "cmd_improvement_knowledge_brief":
                    stage_call_order.append("knowledge_brief")
                    staged_output_path = Path(str(getattr(args, "output_path", expected_knowledge_brief_report_path)))
                    payload = {
                        "status": "ok",
                        "suggested_actions": ["Continue ingesting cross-domain signals."],
                        "knowledge_snapshot": {
                            "status": "ok",
                            "path": str(root / "analysis" / "improvement" / "knowledge_snapshots" / "stub_snapshot.json"),
                        },
                        "output_path": str(staged_output_path),
                    }
                    staged_output_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    return payload
                if getattr(command_fn, "__name__", "") == "cmd_improvement_knowledge_brief_delta_alert":
                    stage_call_order.append("knowledge_brief_delta_alert")
                    staged_output_path = Path(str(getattr(args, "output_path", expected_delta_alert_report_path)))
                    payload = {
                        "status": "warning",
                        "alert_created": True,
                        "drift_severity": "warn",
                        "mitigation_actions": ["Mitigate X"],
                        "acknowledge_commands": [
                            "python3 -m jarvis.cli interrupts acknowledge int_x --actor operator"
                        ],
                        "output_path": str(staged_output_path),
                    }
                    staged_output_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    return payload
                return original_invoke(command_fn, args=args)

            out = io.StringIO()
            with patch("jarvis.cli._invoke_cli_json_command", side_effect=patched_invoke):
                with redirect_stdout(out):
                    cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("knowledge_brief") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("knowledge_brief_delta_alert") or ""), "warning")
            self.assertEqual(stage_call_order[:2], ["knowledge_brief", "knowledge_brief_delta_alert"])

            knowledge_brief_payload = dict(payload.get("knowledge_brief") or {})
            self.assertEqual(str(knowledge_brief_payload.get("status") or ""), "ok")
            knowledge_brief_report_path = Path(str(payload.get("knowledge_brief_report_path") or ""))
            self.assertTrue(knowledge_brief_report_path.exists())
            knowledge_brief_report = json.loads(knowledge_brief_report_path.read_text(encoding="utf-8"))
            self.assertEqual(str(knowledge_brief_report.get("status") or ""), "ok")

            knowledge_alert_payload = dict(payload.get("knowledge_brief_delta_alert") or {})
            self.assertEqual(str(knowledge_alert_payload.get("status") or ""), "warning")
            self.assertTrue(bool(knowledge_alert_payload.get("alert_created")))
            self.assertEqual(str(knowledge_alert_payload.get("drift_severity") or ""), "warn")

            knowledge_alert_report_path = Path(str(payload.get("knowledge_brief_delta_alert_report_path") or ""))
            self.assertTrue(knowledge_alert_report_path.exists())
            report_payload = json.loads(knowledge_alert_report_path.read_text(encoding="utf-8"))
            self.assertEqual(str(report_payload.get("status") or ""), "warning")
            self.assertTrue(bool(report_payload.get("alert_created")))
            self.assertEqual(
                Path(str(report_payload.get("output_path") or "")).resolve(),
                expected_delta_alert_report_path.resolve(),
            )

            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(inbox_summary_path.exists())
            inbox_summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("knowledge_brief") or "")),
                "ok",
            )
            self.assertEqual(
                str((((inbox_summary.get("knowledge_brief") or {}).get("status")) or "")),
                "ok",
            )
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("knowledge_brief_delta_alert") or "")),
                "warning",
            )
            self.assertEqual(
                str((((inbox_summary.get("knowledge_brief_delta_alert") or {}).get("status")) or "")),
                "warning",
            )

    def test_operator_cycle_keeps_status_ok_when_knowledge_delta_alert_is_bootstrap_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator knowledge bootstrap-skip hypothesis",
                    statement=(
                        "Operator cycle should not escalate overall status when knowledge-delta alert is bootstrap-skipped."
                    ),
                    proposed_change="Treat bootstrap skip as a non-regression state while knowledge snapshots warm up.",
                    friction_key="fitness_onboarding_paywall_fatigue",
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
                                "id": "feed-knowledge-bootstrap-1",
                                "title": "Trial path still confusing",
                                "text": "I still hit pricing before understanding the flow.",
                                "rating": 2,
                            }
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
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 205,
                            },
                            "sample_size": 520,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_knowledge_bootstrap_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 4,
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

            expected_knowledge_brief_report_path = root / "reports" / "knowledge_brief_report.json"
            expected_delta_alert_report_path = root / "reports" / "knowledge_brief_delta_alert_report.json"
            output_dir = root / "output" / "operator_knowledge_delta_bootstrap"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=False,
                benchmark_top_limit=None,
                benchmark_report_path=None,
                verify_matrix_enable=False,
                verify_matrix_path=None,
                verify_matrix_report_path=None,
                verify_matrix_alert_enable=False,
                verify_matrix_alert_domain=None,
                verify_matrix_alert_max_items=None,
                verify_matrix_alert_urgency=None,
                verify_matrix_alert_confidence=None,
                verify_matrix_alert_report_path=None,
                knowledge_brief_enable=True,
                knowledge_brief_query=None,
                knowledge_brief_snapshot_label=None,
                knowledge_brief_report_path=expected_knowledge_brief_report_path,
                knowledge_delta_alert_enable=True,
                knowledge_delta_alert_domain="operations",
                knowledge_delta_alert_max_items=3,
                knowledge_delta_alert_urgency=None,
                knowledge_delta_alert_confidence=None,
                knowledge_delta_alert_report_path=expected_delta_alert_report_path,
                knowledge_brief_delta_alert_enable=True,
                knowledge_brief_delta_alert_domain="operations",
                knowledge_brief_delta_alert_max_items=3,
                knowledge_brief_delta_alert_urgency=None,
                knowledge_brief_delta_alert_confidence=None,
                knowledge_brief_delta_alert_report_path=expected_delta_alert_report_path,
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

            original_invoke = cli_module._invoke_cli_json_command

            def patched_invoke(command_fn: Any, *, args: argparse.Namespace) -> dict[str, Any]:
                if getattr(command_fn, "__name__", "") == "cmd_improvement_knowledge_brief":
                    staged_output_path = Path(str(getattr(args, "output_path", expected_knowledge_brief_report_path)))
                    payload = {
                        "status": "ok",
                        "suggested_actions": ["Continue ingesting cross-domain signals."],
                        "knowledge_snapshot": {
                            "status": "ok",
                            "path": str(
                                root / "analysis" / "improvement" / "knowledge_snapshots" / "stub_snapshot.json"
                            ),
                        },
                        "output_path": str(staged_output_path),
                    }
                    staged_output_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    return payload
                if getattr(command_fn, "__name__", "") == "cmd_improvement_knowledge_brief_delta_alert":
                    staged_output_path = Path(str(getattr(args, "output_path", expected_delta_alert_report_path)))
                    payload = {
                        "status": "skipped_bootstrap",
                        "alert_created": False,
                        "alert": None,
                        "drift_severity": "none",
                        "mitigation_actions": [
                            "Bootstrap in progress: collect one more knowledge snapshot before delta alerting."
                        ],
                        "delta": {
                            "status": "skipped_bootstrap",
                            "bootstrap_required": True,
                            "domain_deltas": [],
                        },
                        "output_path": str(staged_output_path),
                    }
                    staged_output_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    return payload
                return original_invoke(command_fn, args=args)

            out = io.StringIO()
            with patch("jarvis.cli._invoke_cli_json_command", side_effect=patched_invoke):
                with redirect_stdout(out):
                    cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("knowledge_brief") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("knowledge_brief_delta_alert") or ""), "skipped_bootstrap")

            knowledge_alert_payload = dict(payload.get("knowledge_brief_delta_alert") or {})
            self.assertEqual(str(knowledge_alert_payload.get("status") or ""), "skipped_bootstrap")
            self.assertFalse(bool(knowledge_alert_payload.get("alert_created")))
            self.assertIsNone(knowledge_alert_payload.get("alert"))
            bootstrap_state = dict(payload.get("knowledge_bootstrap_state") or {})
            self.assertTrue(bool(bootstrap_state))
            self.assertTrue(bool(bootstrap_state.get("active")))
            self.assertEqual(str(bootstrap_state.get("phase") or ""), "bootstrap_pending")
            self.assertTrue(bool(bootstrap_state.get("bootstrap_required")))
            self.assertGreaterEqual(int(bootstrap_state.get("minimum_required_snapshot_count") or 0), 2)
            self.assertGreaterEqual(int(bootstrap_state.get("versioned_snapshot_count") or 0), 0)
            self.assertGreaterEqual(int(bootstrap_state.get("indexed_snapshot_count") or 0), 0)
            next_action_command = str(bootstrap_state.get("next_action_command") or "")
            self.assertTrue(bool(next_action_command))
            self.assertIn("improvement operator-cycle", next_action_command)
            self.assertIn("--knowledge-brief-enable", next_action_command)
            self.assertIn("--knowledge-delta-alert-enable", next_action_command)

            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(inbox_summary_path.exists())
            inbox_summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("knowledge_brief_delta_alert") or "")),
                "skipped_bootstrap",
            )
            summary_bootstrap_state = dict(inbox_summary.get("knowledge_bootstrap_state") or {})
            self.assertTrue(bool(summary_bootstrap_state.get("active")))
            self.assertEqual(str(summary_bootstrap_state.get("phase") or ""), "bootstrap_pending")
            self.assertTrue(bool(summary_bootstrap_state.get("bootstrap_required")))
            self.assertEqual(
                str(summary_bootstrap_state.get("next_action_command") or ""),
                next_action_command,
            )
            self.assertNotEqual(str(inbox_summary.get("status") or ""), "warning")

    def test_operator_cycle_marks_knowledge_brief_delta_alert_stage_skipped_when_not_requested(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator knowledge delta alert skipped hypothesis",
                    statement="Operator cycle should mark knowledge-delta alert stage skipped when not requested.",
                    proposed_change="Keep knowledge-delta alert optional by default.",
                    friction_key="fitness_onboarding_paywall_fatigue",
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
                                "id": "feed-knowledge-skipped-1",
                                "title": "Workout progression is unclear",
                                "text": "I can't tell how sessions are adapting.",
                                "rating": 2,
                            }
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
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 205,
                            },
                            "sample_size": 520,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_knowledge_alert_skipped_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 4,
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

            output_dir = root / "output" / "operator_knowledge_delta_alert_skipped"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=False,
                benchmark_top_limit=None,
                benchmark_report_path=None,
                verify_matrix_enable=False,
                verify_matrix_path=None,
                verify_matrix_report_path=None,
                verify_matrix_alert_enable=False,
                verify_matrix_alert_domain=None,
                verify_matrix_alert_max_items=None,
                verify_matrix_alert_urgency=None,
                verify_matrix_alert_confidence=None,
                verify_matrix_alert_report_path=None,
                knowledge_brief_enable=False,
                knowledge_brief_query=None,
                knowledge_brief_snapshot_label=None,
                knowledge_brief_report_path=None,
                knowledge_delta_alert_enable=False,
                knowledge_delta_alert_domain=None,
                knowledge_delta_alert_max_items=None,
                knowledge_delta_alert_urgency=None,
                knowledge_delta_alert_confidence=None,
                knowledge_delta_alert_report_path=None,
                knowledge_brief_delta_alert_enable=False,
                knowledge_brief_delta_alert_domain=None,
                knowledge_brief_delta_alert_max_items=None,
                knowledge_brief_delta_alert_urgency=None,
                knowledge_brief_delta_alert_confidence=None,
                knowledge_brief_delta_alert_report_path=None,
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

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("knowledge_brief") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("knowledge_brief_delta_alert") or ""), "skipped_not_requested")
            bootstrap_state = dict(payload.get("knowledge_bootstrap_state") or {})
            self.assertTrue(bool(bootstrap_state))
            self.assertFalse(bool(bootstrap_state.get("active")))
            self.assertEqual(str(bootstrap_state.get("phase") or ""), "not_requested")
            self.assertFalse(bool(bootstrap_state.get("bootstrap_required")))
            self.assertFalse(bool(bootstrap_state.get("next_action_command")))

            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(inbox_summary_path.exists())
            inbox_summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("knowledge_brief") or "")),
                "skipped_not_requested",
            )
            self.assertEqual(
                str(((inbox_summary.get("stage_statuses") or {}).get("knowledge_brief_delta_alert") or "")),
                "skipped_not_requested",
            )
            summary_bootstrap_state = dict(inbox_summary.get("knowledge_bootstrap_state") or {})
            self.assertFalse(bool(summary_bootstrap_state.get("active")))
            self.assertEqual(str(summary_bootstrap_state.get("phase") or ""), "not_requested")
            self.assertFalse(bool(summary_bootstrap_state.get("bootstrap_required")))

    def test_operator_cycle_knowledge_bootstrap_state_reports_ready_phase(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator knowledge ready-phase hypothesis",
                    statement="Operator cycle should report ready phase when knowledge bootstrap is complete.",
                    proposed_change="Expose ready bootstrap phase when delta alerting is active and non-bootstrap.",
                    friction_key="fitness_onboarding_paywall_fatigue",
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
                                "id": "feed-knowledge-ready-1",
                                "title": "Adaptation confidence",
                                "text": "Plans adapt clearly after the first week.",
                                "rating": 4,
                            }
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
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 205,
                            },
                            "sample_size": 520,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_knowledge_ready_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 4,
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

            expected_knowledge_brief_report_path = root / "reports" / "knowledge_brief_report.json"
            expected_delta_alert_report_path = root / "reports" / "knowledge_brief_delta_alert_report.json"
            output_dir = root / "output" / "operator_knowledge_ready"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=False,
                benchmark_top_limit=None,
                benchmark_report_path=None,
                verify_matrix_enable=False,
                verify_matrix_path=None,
                verify_matrix_report_path=None,
                verify_matrix_alert_enable=False,
                verify_matrix_alert_domain=None,
                verify_matrix_alert_max_items=None,
                verify_matrix_alert_urgency=None,
                verify_matrix_alert_confidence=None,
                verify_matrix_alert_report_path=None,
                knowledge_brief_enable=True,
                knowledge_brief_query=None,
                knowledge_brief_snapshot_label=None,
                knowledge_brief_report_path=expected_knowledge_brief_report_path,
                knowledge_delta_alert_enable=True,
                knowledge_delta_alert_domain="operations",
                knowledge_delta_alert_max_items=3,
                knowledge_delta_alert_urgency=None,
                knowledge_delta_alert_confidence=None,
                knowledge_delta_alert_report_path=expected_delta_alert_report_path,
                knowledge_brief_delta_alert_enable=True,
                knowledge_brief_delta_alert_domain="operations",
                knowledge_brief_delta_alert_max_items=3,
                knowledge_brief_delta_alert_urgency=None,
                knowledge_brief_delta_alert_confidence=None,
                knowledge_brief_delta_alert_report_path=expected_delta_alert_report_path,
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

            original_invoke = cli_module._invoke_cli_json_command

            def patched_invoke(command_fn: Any, *, args: argparse.Namespace) -> dict[str, Any]:
                if getattr(command_fn, "__name__", "") == "cmd_improvement_knowledge_brief":
                    staged_output_path = Path(str(getattr(args, "output_path", expected_knowledge_brief_report_path)))
                    payload = {
                        "status": "ok",
                        "suggested_actions": ["Continue ingesting cross-domain signals."],
                        "knowledge_snapshot": {
                            "status": "ok",
                            "path": str(
                                root / "analysis" / "improvement" / "knowledge_snapshots" / "stub_snapshot.json"
                            ),
                        },
                        "output_path": str(staged_output_path),
                    }
                    staged_output_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    return payload
                if getattr(command_fn, "__name__", "") == "cmd_improvement_knowledge_brief_delta_alert":
                    staged_output_path = Path(str(getattr(args, "output_path", expected_delta_alert_report_path)))
                    payload = {
                        "status": "ok",
                        "alert_created": False,
                        "alert": None,
                        "drift_severity": "none",
                        "mitigation_actions": [],
                        "delta": {
                            "status": "ok",
                            "bootstrap_required": False,
                            "domain_deltas": [],
                        },
                        "output_path": str(staged_output_path),
                    }
                    staged_output_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    return payload
                return original_invoke(command_fn, args=args)

            out = io.StringIO()
            with patch("jarvis.cli._invoke_cli_json_command", side_effect=patched_invoke):
                with redirect_stdout(out):
                    cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            bootstrap_state = dict(payload.get("knowledge_bootstrap_state") or {})
            self.assertEqual(str(bootstrap_state.get("phase") or ""), "ready")
            self.assertFalse(bool(bootstrap_state.get("bootstrap_required")))

            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertTrue(inbox_summary_path.exists())
            inbox_summary = json.loads(inbox_summary_path.read_text(encoding="utf-8"))
            summary_bootstrap_state = dict(inbox_summary.get("knowledge_bootstrap_state") or {})
            self.assertEqual(str(summary_bootstrap_state.get("phase") or ""), "ready")
            self.assertFalse(bool(summary_bootstrap_state.get("bootstrap_required")))

    def test_operator_cycle_blocked_promotions_unlock_ready_when_interrupts_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Operator unlock-ready hypothesis",
                    statement="Blocked promotions should become unlock-ready when interrupts are acknowledged.",
                    proposed_change="Expose unlock readiness from interrupt statuses.",
                    friction_key="false_positive_drift_in_high_volatility_windows",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))

                acknowledged_interrupt_id = "int_unlock_ready_ack_1"
                runtime.interrupt_store.store(
                    InterruptDecision(
                        interrupt_id=acknowledged_interrupt_id,
                        candidate_id="cand_unlock_ready_ack_1",
                        domain="market_ml",
                        reason=(
                            "matrix_drift_detected severity=critical mismatches=1 missing=0 "
                            "invalid=0 guardrail_mismatches=1 top=market_ml_expected_promote"
                        ),
                        urgency_score=0.95,
                        confidence=0.93,
                        suppression_window_hit=False,
                        delivered=True,
                        why_now="critical drift gate test",
                        why_not_later="promotion should remain blocked until acknowledged",
                        status="delivered",
                    )
                )
                runtime.acknowledge_interrupt(acknowledged_interrupt_id, actor="tester")
            finally:
                runtime.close()

            raw_input_path = root / "inputs" / "fitness_reviews.json"
            raw_input_path.parent.mkdir(parents=True, exist_ok=True)
            raw_input_path.write_text(
                json.dumps(
                    {
                        "reviews": [
                            {
                                "id": "feed-unlock-ready-1",
                                "title": "No free trial path",
                                "text": "No way to test features before paying.",
                                "rating": 2,
                            }
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
                                "precision_at_k": 0.36,
                                "false_positive_rate": 0.16,
                                "inference_latency_ms_p95": 205,
                            },
                            "sample_size": 520,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "market_ml_expected_promote",
                                "domain": "market_ml",
                                "friction_key": "false_positive_drift_in_high_volatility_windows",
                                "artifact_path": "artifacts/blocked_eval.json",
                                "expected_verdict": "blocked_guardrail",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_unlock_ready_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 4,
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

            output_dir = root / "output" / "operator_unlock_ready"
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=output_dir,
                inbox_summary_path=None,
                operator_report_path=None,
                benchmark_enable=False,
                benchmark_top_limit=None,
                benchmark_report_path=None,
                verify_matrix_enable=True,
                verify_matrix_path=matrix_path,
                verify_matrix_report_path=None,
                verify_matrix_alert_enable=True,
                verify_matrix_alert_domain="market_ml",
                verify_matrix_alert_max_items=2,
                verify_matrix_alert_urgency=None,
                verify_matrix_alert_confidence=None,
                verify_matrix_alert_report_path=None,
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

            ack_command = (
                f"python3 -m jarvis.cli interrupts acknowledge {acknowledged_interrupt_id} --actor operator"
            )
            original_invoke = cli_module._invoke_cli_json_command

            def patched_invoke(command_fn: Any, *, args: argparse.Namespace) -> dict[str, Any]:
                if getattr(command_fn, "__name__", "") == "cmd_improvement_verify_matrix_alert":
                    return {
                        "status": "warning",
                        "alert_created": True,
                        "error_count": 0,
                        "alert": {
                            "interrupt_id": acknowledged_interrupt_id,
                            "domain": "market_ml",
                            "status": "acknowledged",
                            "drift_severity": "critical",
                        },
                        "acknowledge_commands": [ack_command],
                        "mitigation_actions": ["Escalate matrix drift review."],
                    }
                return original_invoke(command_fn, args=args)

            out = io.StringIO()
            with patch("jarvis.cli._invoke_cli_json_command", side_effect=patched_invoke):
                with redirect_stdout(out):
                    cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            promotion_lock = dict(payload.get("promotion_lock") or {})
            self.assertTrue(bool(promotion_lock.get("active")))
            self.assertTrue(bool(promotion_lock.get("unlock_ready")))
            self.assertEqual(
                str((promotion_lock.get("blocking_interrupt_statuses") or {}).get(acknowledged_interrupt_id) or ""),
                "acknowledged",
            )
            recheck_command = str(promotion_lock.get("recheck_command") or "")
            self.assertEqual(
                list(promotion_lock.get("unlock_ready_commands") or []),
                [recheck_command],
            )
            self.assertEqual(
                str(promotion_lock.get("first_unlock_ready_command") or ""),
                recheck_command,
            )

            blocked_promotions = [dict(item) for item in list(payload.get("blocked_promotions") or []) if isinstance(item, dict)]
            self.assertEqual(len(blocked_promotions), 1)
            unlock_readiness = dict(blocked_promotions[0].get("unlock_readiness") or {})
            self.assertTrue(bool(unlock_readiness.get("unlock_ready")))
            self.assertEqual(str(unlock_readiness.get("status") or ""), "ready_to_recheck")
            self.assertFalse(bool(unlock_readiness.get("requires_acknowledgement")))
            self.assertEqual(
                str((unlock_readiness.get("blocking_interrupt_statuses") or {}).get(acknowledged_interrupt_id) or ""),
                "acknowledged",
            )
            self.assertIn(ack_command, list(unlock_readiness.get("acknowledge_commands") or []))
            self.assertEqual(
                list(unlock_readiness.get("unlock_ready_commands") or []),
                [recheck_command],
            )
            self.assertEqual(
                str(unlock_readiness.get("first_unlock_ready_command") or ""),
                recheck_command,
            )

            self.assertEqual(list(payload.get("promotions") or []), [])
            self.assertEqual(int((payload.get("metrics") or {}).get("blocked_promotion_count") or 0), 1)

    def test_operator_cycle_resolves_relative_output_dir_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            raw_input_path = root / "inputs" / "fitness_reviews.json"
            raw_input_path.parent.mkdir(parents=True, exist_ok=True)
            raw_input_path.write_text(
                json.dumps(
                    {
                        "reviews": [
                            {
                                "id": "feed-rel-1",
                                "title": "Paywall appears too early",
                                "text": "Paywall appears before I can try a complete workout.",
                                "rating": 2,
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_relative_output_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "min_cluster_count": 1,
                            "proposal_limit": 3,
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
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            relative_output_dir = Path("reports/operator")
            args = argparse.Namespace(
                config_path=config_path,
                output_dir=relative_output_dir,
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
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with redirect_stdout(out):
                    cmd_improvement_operator_cycle(args)
            finally:
                os.chdir(previous_cwd)

            payload = json.loads(out.getvalue())
            expected_output_dir = (root / relative_output_dir).resolve()
            self.assertEqual(str(payload.get("output_dir") or ""), str(expected_output_dir))

            pull_report_path = Path(str(payload.get("pull_report_path") or ""))
            daily_report_path = Path(str(payload.get("daily_report_path") or ""))
            retest_report_path = Path(str(payload.get("retest_report_path") or ""))
            inbox_summary_path = Path(str(payload.get("inbox_summary_path") or ""))
            self.assertEqual(pull_report_path.parent, expected_output_dir)
            self.assertEqual(daily_report_path.parent, expected_output_dir)
            self.assertEqual(retest_report_path.parent, expected_output_dir)
            self.assertEqual(inbox_summary_path.parent, expected_output_dir)
            self.assertTrue(pull_report_path.exists())
            self.assertTrue(daily_report_path.exists())
            self.assertTrue(retest_report_path.exists())
            self.assertTrue(inbox_summary_path.exists())

    def test_operator_cycle_runs_draft_stage_before_daily_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Draft stage integration hypothesis",
                    statement="Operator cycle should draft a runnable experiment before daily execution.",
                    proposed_change="Bootstrap controlled artifact for queued hypothesis.",
                    friction_key="latency_spikes_during_feature_enrichment",
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
                                "id": "feed-draft-1",
                                "title": "Onboarding friction",
                                "text": "Onboarding still feels too rigid for beginners.",
                                "rating": 2,
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            seed_report_path = root / "reports" / "seed_report.json"
            seed_report_path.parent.mkdir(parents=True, exist_ok=True)
            seed_report_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-22T12:00:00Z",
                        "domain": "market_ml",
                        "created": [
                            {
                                "hypothesis_id": hypothesis_id,
                                "reason": "seed_report_row",
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_with_draft_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "allow_missing_inputs": False,
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
                        "feedback_jobs": [],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_draft"
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
                draft_enable=True,
                draft_seed_report_path=seed_report_path,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain="market_ml",
                draft_statuses="queued",
                draft_limit=6,
                draft_lookup_limit=200,
                draft_include_existing=False,
                draft_overwrite_artifacts=True,
                draft_environment="controlled_backtest",
                draft_default_sample_size=140,
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
            self.assertEqual(str(stage_statuses.get("fitness_leaderboard") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "skipped_not_requested")
            self.assertEqual(str(stage_statuses.get("draft_experiment_jobs") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("daily_pipeline") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("execute_retests") or ""), "ok")

            draft_report_path = Path(str(payload.get("draft_report_path") or ""))
            daily_config_path = Path(str(payload.get("daily_config_path") or ""))
            daily_report_path = Path(str(payload.get("daily_report_path") or ""))
            self.assertTrue(draft_report_path.exists())
            self.assertTrue(daily_config_path.exists())
            self.assertTrue(daily_report_path.exists())
            self.assertNotEqual(daily_config_path, config_path)

            draft_report = json.loads(draft_report_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(draft_report.get("drafted_count") or 0), 1)
            self.assertGreaterEqual(int(draft_report.get("config_appended_count") or 0), 1)

            daily_payload = json.loads(daily_report_path.read_text(encoding="utf-8"))
            self.assertEqual(int(daily_payload.get("error_count") or 0), 0)
            self.assertGreaterEqual(int(daily_payload.get("experiment_runs_count") or 0), 1)
            experiment_runs = [row for row in list(daily_payload.get("experiment_runs") or []) if isinstance(row, dict)]
            self.assertTrue(any(str(row.get("hypothesis_id") or "") == hypothesis_id for row in experiment_runs))

    def test_operator_cycle_draft_uses_domain_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Domain default draft selection should include testing lane",
                    statement="Operator cycle should resolve draft defaults from config per domain.",
                    proposed_change="Use domain defaults for statuses, limits, and environment.",
                    success_criteria={
                        "metric": "precision_at_k",
                        "direction": "increase",
                        "min_effect": 0.03,
                    },
                    friction_key="feature_store_latency_spike_near_open",
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
                runtime.hypothesis_lab.set_hypothesis_status(
                    hypothesis_id=hypothesis_id,
                    status="testing",
                )
            finally:
                runtime.close()

            raw_input_path = root / "inputs" / "fitness_reviews.json"
            raw_input_path.parent.mkdir(parents=True, exist_ok=True)
            raw_input_path.write_text(
                json.dumps(
                    {
                        "reviews": [
                            {
                                "id": "feed-draft-defaults-1",
                                "title": "Onboarding friction",
                                "text": "Onboarding still feels rigid for beginners.",
                                "rating": 2,
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_draft_domain_defaults_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "allow_missing_inputs": False,
                            "draft_statuses": "queued",
                            "draft_statuses_by_domain": {
                                "market_ml": "testing",
                            },
                            "draft_limit": 8,
                            "draft_limit_by_domain": {
                                "market_ml": 3,
                            },
                            "draft_lookup_limit": 400,
                            "draft_lookup_limit_by_domain": {
                                "market_ml": 80,
                            },
                            "draft_environment_by_domain": {
                                "market_ml": "controlled_backtest",
                            },
                            "draft_default_sample_size": 100,
                            "draft_default_sample_size_by_domain": {
                                "market_ml": 175,
                            },
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
                        "feedback_jobs": [],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_draft_domain_defaults"
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
                draft_enable=True,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain="market_ml",
                draft_statuses=None,
                draft_limit=None,
                draft_lookup_limit=None,
                draft_include_existing=False,
                draft_overwrite_artifacts=True,
                draft_environment=None,
                draft_default_sample_size=None,
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
            self.assertEqual(str(stage_statuses.get("draft_experiment_jobs") or ""), "ok")

            draft_payload = dict(payload.get("draft") or {})
            self.assertEqual(str(draft_payload.get("requested_statuses") or ""), "testing")
            self.assertEqual(str(draft_payload.get("statuses_source") or ""), "config_by_domain")
            self.assertEqual(int(draft_payload.get("resolved_limit") or 0), 3)
            self.assertEqual(str(draft_payload.get("limit_source") or ""), "config_by_domain")
            self.assertEqual(int(draft_payload.get("resolved_lookup_limit") or 0), 80)
            self.assertEqual(str(draft_payload.get("lookup_limit_source") or ""), "config_by_domain")
            self.assertEqual(str(draft_payload.get("resolved_environment") or ""), "controlled_backtest")
            self.assertEqual(str(draft_payload.get("environment_source") or ""), "config_by_domain")
            self.assertEqual(int(draft_payload.get("resolved_default_sample_size") or 0), 175)
            self.assertEqual(str(draft_payload.get("default_sample_size_source") or ""), "config_by_domain")
            self.assertGreaterEqual(int(draft_payload.get("drafted_count") or 0), 1)

            draft_report_path = Path(str(payload.get("draft_report_path") or ""))
            self.assertTrue(draft_report_path.exists())
            draft_report = json.loads(draft_report_path.read_text(encoding="utf-8"))
            drafts = [dict(item) for item in list(draft_report.get("drafts") or []) if isinstance(item, dict)]
            self.assertEqual(len(drafts), 1)
            self.assertEqual(str(drafts[0].get("hypothesis_id") or ""), hypothesis_id)
            artifact_path = Path(str(drafts[0].get("artifact_path") or ""))
            self.assertTrue(artifact_path.exists())
            artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(str(artifact_payload.get("environment") or ""), "controlled_backtest")
            self.assertEqual(int(artifact_payload.get("sample_size") or 0), 175)

    def test_operator_cycle_draft_cli_overrides_domain_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="CLI override draft selection should take precedence",
                    statement="Operator cycle should prioritize explicit draft overrides.",
                    proposed_change="Use CLI values for draft thresholds and environment.",
                    success_criteria={
                        "metric": "precision_at_k",
                        "direction": "increase",
                        "min_effect": 0.03,
                    },
                    friction_key="feature_store_latency_spike_near_open",
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
                                "id": "feed-draft-overrides-1",
                                "title": "Onboarding friction",
                                "text": "Onboarding still feels rigid for beginners.",
                                "rating": 2,
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_draft_cli_override_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "allow_missing_inputs": False,
                            "draft_statuses_by_domain": {
                                "market_ml": "testing",
                            },
                            "draft_limit_by_domain": {
                                "market_ml": 3,
                            },
                            "draft_lookup_limit_by_domain": {
                                "market_ml": 80,
                            },
                            "draft_environment_by_domain": {
                                "market_ml": "controlled_backtest",
                            },
                            "draft_default_sample_size_by_domain": {
                                "market_ml": 175,
                            },
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
                        "feedback_jobs": [],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_draft_cli_overrides"
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
                draft_enable=True,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain="market_ml",
                draft_statuses="queued",
                draft_limit=5,
                draft_lookup_limit=50,
                draft_include_existing=False,
                draft_overwrite_artifacts=True,
                draft_environment="sandbox",
                draft_default_sample_size=90,
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
            self.assertEqual(str(stage_statuses.get("draft_experiment_jobs") or ""), "ok")

            draft_payload = dict(payload.get("draft") or {})
            self.assertEqual(str(draft_payload.get("requested_statuses") or ""), "queued")
            self.assertEqual(str(draft_payload.get("statuses_source") or ""), "cli_override")
            self.assertEqual(int(draft_payload.get("resolved_limit") or 0), 5)
            self.assertEqual(str(draft_payload.get("limit_source") or ""), "cli_override")
            self.assertEqual(int(draft_payload.get("resolved_lookup_limit") or 0), 50)
            self.assertEqual(str(draft_payload.get("lookup_limit_source") or ""), "cli_override")
            self.assertEqual(str(draft_payload.get("resolved_environment") or ""), "sandbox")
            self.assertEqual(str(draft_payload.get("environment_source") or ""), "cli_override")
            self.assertEqual(int(draft_payload.get("resolved_default_sample_size") or 0), 90)
            self.assertEqual(str(draft_payload.get("default_sample_size_source") or ""), "cli_override")
            self.assertGreaterEqual(int(draft_payload.get("drafted_count") or 0), 1)

            draft_report_path = Path(str(payload.get("draft_report_path") or ""))
            self.assertTrue(draft_report_path.exists())
            draft_report = json.loads(draft_report_path.read_text(encoding="utf-8"))
            drafts = [dict(item) for item in list(draft_report.get("drafts") or []) if isinstance(item, dict)]
            self.assertEqual(len(drafts), 1)
            self.assertEqual(str(drafts[0].get("hypothesis_id") or ""), hypothesis_id)
            artifact_path = Path(str(drafts[0].get("artifact_path") or ""))
            self.assertTrue(artifact_path.exists())
            artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(str(artifact_payload.get("environment") or ""), "sandbox")
            self.assertEqual(int(artifact_payload.get("sample_size") or 0), 90)

    def test_operator_cycle_seed_stage_resolves_explicit_input_path_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            seed_input_path = root / "configs" / "data" / "fitness_feedback.jsonl"
            seed_input_path.parent.mkdir(parents=True, exist_ok=True)
            seed_input_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "seed-path-1",
                                "title": "Paywall too early",
                                "summary": "Paywall appears before a full trial workout.",
                                "review": "Paywall appears before a full trial workout.",
                                "rating": 2,
                                "created_at": "2026-04-21T10:00:00Z",
                            },
                            sort_keys=True,
                        ),
                        json.dumps(
                            {
                                "id": "seed-path-2",
                                "title": "Onboarding too rigid",
                                "summary": "The onboarding plan is too intense for beginners.",
                                "review": "The onboarding plan is too intense for beginners.",
                                "rating": 2,
                                "created_at": "2026-04-21T12:00:00Z",
                            },
                            sort_keys=True,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_seed_path_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_seed_path"
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
                seed_enable=True,
                seed_leaderboard_input_path=Path("configs/data/fitness_feedback.jsonl"),
                seed_leaderboard_input_format="jsonl",
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source="market_reviews",
                seed_hypothesis_source="fitness_leaderboard",
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with redirect_stdout(out):
                    cmd_improvement_operator_cycle(args)
            finally:
                os.chdir(previous_cwd)

            payload = json.loads(out.getvalue())
            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("fitness_leaderboard") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")
            self.assertIn(
                str(stage_statuses.get("draft_experiment_jobs") or ""),
                {"ok", "warning"},
            )

            leaderboard_payload = dict(payload.get("fitness_leaderboard") or {})
            self.assertGreaterEqual(int(leaderboard_payload.get("leaderboard_count") or 0), 1)

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            total_seeded = int(seed_payload.get("created_count") or 0) + int(seed_payload.get("existing_count") or 0)
            self.assertGreaterEqual(total_seeded, 1)

    def test_operator_cycle_auto_broadens_draft_statuses_for_existing_seed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            seed_input_path = root / "configs" / "data" / "fitness_feedback.jsonl"
            seed_input_path.parent.mkdir(parents=True, exist_ok=True)
            seed_input_path.write_text(
                json.dumps(
                    {
                        "id": "seed-existing-1",
                        "title": "Paywall too early",
                        "summary": "I hit a paywall before I could complete a real workout trial.",
                        "review": "I hit a paywall before I could complete a real workout trial.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T10:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            leaderboard_args = argparse.Namespace(
                input_path=seed_input_path,
                input_format="jsonl",
                domain="fitness_apps",
                source="market_reviews",
                timestamp_fields="created_at",
                as_of="2026-04-22T12:00:00Z",
                lookback_days=7,
                min_cluster_count=1,
                cluster_limit=20,
                leaderboard_limit=12,
                cooling_limit=10,
                app_fields="app_name,source_context.app",
                top_apps_per_cluster=3,
                min_cross_app_count=1,
                own_app_aliases=None,
                trend_threshold=0.25,
                include_untimed_current=False,
                strict=False,
                output_path=None,
                json_compact=False,
            )
            leaderboard_out = io.StringIO()
            with redirect_stdout(leaderboard_out):
                cmd_improvement_fitness_leaderboard(leaderboard_args)
            leaderboard_payload = json.loads(leaderboard_out.getvalue())
            first_row = dict((leaderboard_payload.get("leaderboard") or [{}])[0] or {})
            friction_key = str(first_row.get("friction_key") or "").strip()
            self.assertTrue(bool(friction_key))

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title=f"fitness_apps: reduce '{friction_key}' frustration",
                    statement="Existing hypothesis should be reused by seed stage.",
                    proposed_change="Keep controlled validation lane active.",
                    friction_key=friction_key,
                )
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
                self.assertTrue(bool(hypothesis_id))
                runtime.hypothesis_lab.set_hypothesis_status(
                    hypothesis_id=hypothesis_id,
                    status="validated",
                )
            finally:
                runtime.close()

            config_path = root / "configs" / "operator_cycle_auto_draft_status_config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_auto_draft_status"
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
                seed_enable=True,
                seed_domains="fitness_apps",
                seed_leaderboard_input_path=Path("configs/data/fitness_feedback.jsonl"),
                seed_leaderboard_input_format="jsonl",
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source="market_reviews",
                seed_hypothesis_source="fitness_leaderboard",
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source="leaderboard",
                seed_fallback_entry_source="leaderboard",
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=1,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=True,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain="fitness_apps",
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=True,
                draft_overwrite_artifacts=False,
                draft_environment="controlled_rollout",
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with redirect_stdout(out):
                    cmd_improvement_operator_cycle(args)
            finally:
                os.chdir(previous_cwd)

            payload = json.loads(out.getvalue())
            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("draft_experiment_jobs") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(int(seed_payload.get("created_count") or 0), 0)
            self.assertGreaterEqual(int(seed_payload.get("existing_count") or 0), 1)

            draft_payload = dict(payload.get("draft") or {})
            self.assertTrue(bool(draft_payload.get("statuses_auto_broadened")))
            self.assertEqual(
                str(draft_payload.get("requested_statuses") or ""),
                "queued,testing,validated,rejected",
            )
            self.assertGreaterEqual(int(draft_payload.get("selected_hypotheses_count") or 0), 1)
            self.assertGreaterEqual(int(draft_payload.get("drafted_count") or 0), 1)

    def test_operator_cycle_seed_stage_supports_multi_domain_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            quant_input_path = root / "configs" / "inputs" / "quant_feedback.jsonl"
            quant_input_path.parent.mkdir(parents=True, exist_ok=True)
            quant_input_path.write_text(
                json.dumps(
                    {
                        "id": "quant-1",
                        "title": "Regime slippage around macro releases",
                        "summary": "Execution slippage spikes during macro volatility windows.",
                        "review": "Execution slippage spikes during macro volatility windows.",
                        "severity": 4,
                        "created_at": "2026-04-21T10:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before trial workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_multi_domain_seed_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "quant_finance",
                                "source": "research_notes",
                                "input_path": "inputs/quant_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_seed_multi_domain"
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
                seed_enable=True,
                seed_domains="quant_finance,fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("fitness_leaderboard") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")
            self.assertIn(str(stage_statuses.get("draft_experiment_jobs") or ""), {"ok", "warning"})

            seed_domains = [str(item) for item in list(payload.get("seed_domains") or [])]
            self.assertEqual(set(seed_domains), {"quant_finance", "fitness_apps"})

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 2)
            run_domains = {str(row.get("domain") or "") for row in seed_domain_runs}
            self.assertEqual(run_domains, {"quant_finance", "fitness_apps"})

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(int(seed_payload.get("domain_count") or 0), 2)
            combined_report_path = Path(str(seed_payload.get("combined_output_path") or ""))
            self.assertTrue(combined_report_path.exists())

    def test_operator_cycle_seed_uses_domain_signal_threshold_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                (
                    json.dumps(
                        {
                            "id": "market-1",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T10:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                    + json.dumps(
                        {
                            "id": "market-2",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T12:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ),
                encoding="utf-8",
            )

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_domain_thresholds.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_min_signal_count_current": 1,
                            "seed_min_signal_count_current_by_domain": {
                                "market_ml": 3,
                            },
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_domain_signal_thresholds"
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
                seed_enable=True,
                seed_domains="market_ml,fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source="leaderboard",
                seed_fallback_entry_source="leaderboard",
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=0,
                seed_min_signal_count_current=None,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 2)
            runs_by_domain = {str(row.get("domain") or ""): row for row in seed_domain_runs}

            market_seed = dict((runs_by_domain.get("market_ml") or {}).get("seed_from_leaderboard") or {})
            self.assertEqual(int(market_seed.get("min_signal_count_current") or 0), 3)
            self.assertEqual(str((runs_by_domain.get("market_ml") or {}).get("seed_min_signal_count_current_source") or ""), "config_by_domain")
            self.assertEqual(int(market_seed.get("created_count") or 0), 0)
            self.assertTrue(
                any(
                    str((row or {}).get("reason") or "") == "signal_count_current_below_min"
                    for row in list(market_seed.get("skipped") or [])
                )
            )

            fitness_seed = dict((runs_by_domain.get("fitness_apps") or {}).get("seed_from_leaderboard") or {})
            self.assertEqual(int(fitness_seed.get("min_signal_count_current") or 0), 1)
            self.assertEqual(str((runs_by_domain.get("fitness_apps") or {}).get("seed_min_signal_count_current_source") or ""), "config_global")
            self.assertGreaterEqual(int(fitness_seed.get("created_count") or 0), 1)

    def test_operator_cycle_seed_cli_signal_threshold_overrides_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                (
                    json.dumps(
                        {
                            "id": "market-1",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T10:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                    + json.dumps(
                        {
                            "id": "market-2",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T12:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ),
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_signal_threshold_override.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_min_signal_count_current_by_domain": {
                                "market_ml": 3,
                            },
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_signal_threshold_override"
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
                seed_enable=True,
                seed_domains="market_ml",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="market_ml",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source="leaderboard",
                seed_fallback_entry_source="leaderboard",
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=0,
                seed_min_signal_count_current=1,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(int(seed_payload.get("min_signal_count_current") or 0), 1)
            self.assertGreaterEqual(int(seed_payload.get("created_count") or 0), 1)

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 1)
            only_run = dict(seed_domain_runs[0] or {})
            self.assertEqual(str(only_run.get("seed_min_signal_count_current_source") or ""), "cli_override")

    def test_operator_cycle_seed_uses_domain_cross_app_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                (
                    json.dumps(
                        {
                            "id": "market-1",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T10:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                    + json.dumps(
                        {
                            "id": "market-2",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T12:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ),
                encoding="utf-8",
            )

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_cross_app_thresholds.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_min_cross_app_count": 1,
                            "seed_min_cross_app_count_by_domain": {
                                "fitness_apps": 2,
                            },
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_domain_cross_app_thresholds"
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
                seed_enable=True,
                seed_domains="market_ml,fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source="leaderboard",
                seed_fallback_entry_source="leaderboard",
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=1,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 2)
            runs_by_domain = {str(row.get("domain") or ""): row for row in seed_domain_runs}

            market_seed = dict((runs_by_domain.get("market_ml") or {}).get("seed_from_leaderboard") or {})
            self.assertEqual(int(market_seed.get("min_cross_app_count") or 0), 1)
            self.assertEqual(str((runs_by_domain.get("market_ml") or {}).get("seed_min_cross_app_count_source") or ""), "config_global")
            self.assertGreaterEqual(int(market_seed.get("created_count") or 0), 1)

            fitness_seed = dict((runs_by_domain.get("fitness_apps") or {}).get("seed_from_leaderboard") or {})
            self.assertEqual(int(fitness_seed.get("min_cross_app_count") or 0), 2)
            self.assertEqual(str((runs_by_domain.get("fitness_apps") or {}).get("seed_min_cross_app_count_source") or ""), "config_by_domain")
            self.assertEqual(int(fitness_seed.get("created_count") or 0), 0)
            self.assertTrue(
                any(
                    str((row or {}).get("reason") or "") == "cross_app_count_below_min"
                    for row in list(fitness_seed.get("skipped") or [])
                )
            )

    def test_operator_cycle_seed_cli_cross_app_threshold_overrides_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_cross_app_threshold_override.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_min_cross_app_count_by_domain": {
                                "fitness_apps": 3,
                            },
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_cross_app_threshold_override"
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
                seed_enable=True,
                seed_domains="fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source="leaderboard",
                seed_fallback_entry_source="leaderboard",
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=1,
                seed_min_signal_count_current=1,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(int(seed_payload.get("min_cross_app_count") or 0), 1)
            self.assertGreaterEqual(int(seed_payload.get("created_count") or 0), 1)

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 1)
            only_run = dict(seed_domain_runs[0] or {})
            self.assertEqual(str(only_run.get("seed_min_cross_app_count_source") or ""), "cli_override")

    def test_operator_cycle_seed_uses_domain_entry_source_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_entry_source_defaults.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_entry_source_by_domain": {
                                "fitness_apps": "shared_market_displeasures",
                            },
                            "seed_fallback_entry_source_by_domain": {
                                "fitness_apps": "leaderboard",
                            },
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_entry_source_defaults"
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
                seed_enable=True,
                seed_domains="fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source=None,
                seed_fallback_entry_source=None,
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=1,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(str(seed_payload.get("requested_entry_source") or ""), "shared_market_displeasures")
            self.assertEqual(str(seed_payload.get("entry_source") or ""), "leaderboard")
            self.assertTrue(bool(seed_payload.get("fallback_triggered")))
            self.assertEqual(str(seed_payload.get("fallback_entry_source") or ""), "leaderboard")
            self.assertGreaterEqual(int(seed_payload.get("created_count") or 0), 1)

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 1)
            only_run = dict(seed_domain_runs[0] or {})
            self.assertEqual(str(only_run.get("seed_entry_source_source") or ""), "config_by_domain")
            self.assertEqual(str(only_run.get("seed_fallback_entry_source_source") or ""), "config_by_domain")

    def test_operator_cycle_seed_cli_entry_sources_override_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_entry_source_override.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_entry_source_by_domain": {
                                "fitness_apps": "shared_market_displeasures",
                            },
                            "seed_fallback_entry_source_by_domain": {
                                "fitness_apps": "none",
                            },
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_entry_source_override"
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
                seed_enable=True,
                seed_domains="fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source="shared_market_displeasures",
                seed_fallback_entry_source="leaderboard",
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=1,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(str(seed_payload.get("requested_entry_source") or ""), "shared_market_displeasures")
            self.assertEqual(str(seed_payload.get("fallback_entry_source") or ""), "leaderboard")
            self.assertTrue(bool(seed_payload.get("fallback_triggered")))
            self.assertGreaterEqual(int(seed_payload.get("created_count") or 0), 1)

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 1)
            only_run = dict(seed_domain_runs[0] or {})
            self.assertEqual(str(only_run.get("seed_entry_source_source") or ""), "cli_override")
            self.assertEqual(str(only_run.get("seed_fallback_entry_source_source") or ""), "cli_override")

    def test_operator_cycle_seed_uses_domain_trend_and_impact_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                (
                    json.dumps(
                        {
                            "id": "market-1",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T10:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                    + json.dumps(
                        {
                            "id": "market-2",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T12:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ),
                encoding="utf-8",
            )

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_trend_impact_defaults.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_trends": "new,rising",
                            "seed_trends_by_domain": {
                                "market_ml": "new",
                            },
                            "seed_min_impact_score": 0.0,
                            "seed_min_impact_score_by_domain": {
                                "market_ml": 99.0,
                            },
                            "seed_min_impact_delta": 0.0,
                            "seed_min_impact_delta_by_domain": {
                                "market_ml": 0.5,
                            },
                            "seed_min_cross_app_count": 1,
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_trend_impact_defaults"
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
                seed_enable=True,
                seed_domains="market_ml,fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends=None,
                seed_limit=8,
                seed_min_impact_score=None,
                seed_min_impact_delta=None,
                seed_entry_source=None,
                seed_fallback_entry_source=None,
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=None,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 2)
            runs_by_domain = {str(row.get("domain") or ""): row for row in seed_domain_runs}

            market_seed = dict((runs_by_domain.get("market_ml") or {}).get("seed_from_leaderboard") or {})
            self.assertEqual(sorted(str(item) for item in list(market_seed.get("trend_filters") or [])), ["new"])
            self.assertEqual(float(market_seed.get("min_impact_score") or 0.0), 99.0)
            self.assertEqual(float(market_seed.get("min_impact_delta") or 0.0), 0.5)
            self.assertEqual(int(market_seed.get("created_count") or 0), 0)
            self.assertTrue(
                any(
                    str((row or {}).get("reason") or "") == "impact_score_below_min"
                    for row in list(market_seed.get("skipped") or [])
                )
            )
            self.assertEqual(str((runs_by_domain.get("market_ml") or {}).get("seed_trends_source") or ""), "config_by_domain")
            self.assertEqual(str((runs_by_domain.get("market_ml") or {}).get("seed_min_impact_score_source") or ""), "config_by_domain")
            self.assertEqual(str((runs_by_domain.get("market_ml") or {}).get("seed_min_impact_delta_source") or ""), "config_by_domain")

            fitness_seed = dict((runs_by_domain.get("fitness_apps") or {}).get("seed_from_leaderboard") or {})
            self.assertEqual(sorted(str(item) for item in list(fitness_seed.get("trend_filters") or [])), ["new", "rising"])
            self.assertEqual(float(fitness_seed.get("min_impact_score") or 0.0), 0.0)
            self.assertEqual(float(fitness_seed.get("min_impact_delta") or 0.0), 0.0)
            self.assertGreaterEqual(int(fitness_seed.get("created_count") or 0), 1)
            self.assertEqual(str((runs_by_domain.get("fitness_apps") or {}).get("seed_trends_source") or ""), "config_global")
            self.assertEqual(str((runs_by_domain.get("fitness_apps") or {}).get("seed_min_impact_score_source") or ""), "config_global")
            self.assertEqual(str((runs_by_domain.get("fitness_apps") or {}).get("seed_min_impact_delta_source") or ""), "config_global")

    def test_operator_cycle_seed_cli_trend_and_impact_overrides_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                (
                    json.dumps(
                        {
                            "id": "market-1",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T10:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                    + json.dumps(
                        {
                            "id": "market-2",
                            "title": "Classifier false positives spike",
                            "summary": "Classifier false positives spike during volatility windows.",
                            "review": "Classifier false positives spike during volatility windows.",
                            "severity": 4,
                            "created_at": "2026-04-21T12:00:00Z",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ),
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_trend_impact_override.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_trends_by_domain": {
                                "market_ml": "new",
                            },
                            "seed_min_impact_score_by_domain": {
                                "market_ml": 99.0,
                            },
                            "seed_min_impact_delta_by_domain": {
                                "market_ml": 0.5,
                            },
                            "seed_min_cross_app_count": 1,
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_trend_impact_override"
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
                seed_enable=True,
                seed_domains="market_ml",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="market_ml",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends="new,rising",
                seed_limit=8,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_entry_source=None,
                seed_fallback_entry_source=None,
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=None,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(sorted(str(item) for item in list(seed_payload.get("trend_filters") or [])), ["new", "rising"])
            self.assertEqual(float(seed_payload.get("min_impact_score") or 0.0), 0.0)
            self.assertEqual(float(seed_payload.get("min_impact_delta") or 0.0), 0.0)
            self.assertGreaterEqual(int(seed_payload.get("created_count") or 0), 1)

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 1)
            only_run = dict(seed_domain_runs[0] or {})
            self.assertEqual(str(only_run.get("seed_trends_source") or ""), "cli_override")
            self.assertEqual(str(only_run.get("seed_min_impact_score_source") or ""), "cli_override")
            self.assertEqual(str(only_run.get("seed_min_impact_delta_source") or ""), "cli_override")

    def test_operator_cycle_seed_uses_domain_limit_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                json.dumps(
                    {
                        "id": "market-1",
                        "title": "Classifier false positives spike",
                        "summary": "Classifier false positives spike during volatility windows.",
                        "review": "Classifier false positives spike during volatility windows.",
                        "severity": 4,
                        "created_at": "2026-04-21T10:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_seed_limits_defaults.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_limit": 2,
                            "seed_limit_by_domain": {
                                "fitness_apps": 1,
                            },
                            "seed_lookup_limit": 3,
                            "seed_lookup_limit_by_domain": {
                                "fitness_apps": 5,
                            },
                            "seed_min_cross_app_count": 1,
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_seed_limits_defaults"
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
                seed_enable=True,
                seed_domains="market_ml,fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends=None,
                seed_limit=None,
                seed_min_impact_score=None,
                seed_min_impact_delta=None,
                seed_entry_source=None,
                seed_fallback_entry_source=None,
                seed_owner="operator",
                seed_lookup_limit=None,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=None,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 2)
            runs_by_domain = {str(row.get("domain") or ""): row for row in seed_domain_runs}

            market_run = dict(runs_by_domain.get("market_ml") or {})
            self.assertEqual(int(market_run.get("seed_limit") or 0), 2)
            self.assertEqual(str(market_run.get("seed_limit_source") or ""), "config_global")
            self.assertEqual(int(market_run.get("seed_lookup_limit") or 0), 3)
            self.assertEqual(str(market_run.get("seed_lookup_limit_source") or ""), "config_global")

            fitness_run = dict(runs_by_domain.get("fitness_apps") or {})
            self.assertEqual(int(fitness_run.get("seed_limit") or 0), 1)
            self.assertEqual(str(fitness_run.get("seed_limit_source") or ""), "config_by_domain")
            self.assertEqual(int(fitness_run.get("seed_lookup_limit") or 0), 5)
            self.assertEqual(str(fitness_run.get("seed_lookup_limit_source") or ""), "config_by_domain")

    def test_operator_cycle_seed_cli_limits_override_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                json.dumps(
                    {
                        "id": "market-1",
                        "title": "Classifier false positives spike",
                        "summary": "Classifier false positives spike during volatility windows.",
                        "review": "Classifier false positives spike during volatility windows.",
                        "severity": 4,
                        "created_at": "2026-04-21T10:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_seed_limits_override.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_limit_by_domain": {
                                "market_ml": 5,
                            },
                            "seed_lookup_limit_by_domain": {
                                "market_ml": 9,
                            },
                            "seed_min_cross_app_count": 1,
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_seed_limits_override"
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
                seed_enable=True,
                seed_domains="market_ml",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="market_ml",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends=None,
                seed_limit=1,
                seed_min_impact_score=None,
                seed_min_impact_delta=None,
                seed_entry_source=None,
                seed_fallback_entry_source=None,
                seed_owner="operator",
                seed_lookup_limit=2,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=12,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=None,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertGreaterEqual(int(seed_payload.get("created_count") or 0), 1)

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 1)
            only_run = dict(seed_domain_runs[0] or {})
            self.assertEqual(int(only_run.get("seed_limit") or 0), 1)
            self.assertEqual(str(only_run.get("seed_limit_source") or ""), "cli_override")
            self.assertEqual(int(only_run.get("seed_lookup_limit") or 0), 2)
            self.assertEqual(str(only_run.get("seed_lookup_limit_source") or ""), "cli_override")

    def test_operator_cycle_seed_uses_domain_leaderboard_window_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                json.dumps(
                    {
                        "id": "market-1",
                        "title": "Classifier false positives spike",
                        "summary": "Classifier false positives spike during volatility windows.",
                        "review": "Classifier false positives spike during volatility windows.",
                        "severity": 4,
                        "created_at": "2026-04-21T10:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            fitness_input_path = root / "configs" / "inputs" / "fitness_feedback.jsonl"
            fitness_input_path.parent.mkdir(parents=True, exist_ok=True)
            fitness_input_path.write_text(
                json.dumps(
                    {
                        "id": "fitness-1",
                        "title": "Paywall appears before workout completion",
                        "summary": "Users hit paywall before completing their first workout.",
                        "review": "Users hit paywall before completing their first workout.",
                        "rating": 2,
                        "app_name": "FitNova",
                        "created_at": "2026-04-21T11:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_leaderboard_window_defaults.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_lookback_days": 7,
                            "seed_lookback_days_by_domain": {
                                "market_ml": 14,
                            },
                            "seed_leaderboard_limit": 12,
                            "seed_leaderboard_limit_by_domain": {
                                "fitness_apps": 5,
                            },
                            "seed_trend_threshold": 0.25,
                            "seed_trend_threshold_by_domain": {
                                "market_ml": 0.4,
                            },
                            "seed_min_cross_app_count": 1,
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                            {
                                "domain": "fitness_apps",
                                "source": "app_store_reviews",
                                "input_path": "inputs/fitness_feedback.jsonl",
                                "input_format": "jsonl",
                            },
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_leaderboard_window_defaults"
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
                seed_enable=True,
                seed_domains="market_ml,fitness_apps",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends=None,
                seed_limit=None,
                seed_min_impact_score=None,
                seed_min_impact_delta=None,
                seed_entry_source=None,
                seed_fallback_entry_source=None,
                seed_owner="operator",
                seed_lookup_limit=None,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=None,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=None,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=None,
                seed_own_app_aliases=None,
                seed_trend_threshold=None,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 2)
            runs_by_domain = {str(row.get("domain") or ""): row for row in seed_domain_runs}

            market_run = dict(runs_by_domain.get("market_ml") or {})
            self.assertEqual(int(market_run.get("seed_lookback_days") or 0), 14)
            self.assertEqual(str(market_run.get("seed_lookback_days_source") or ""), "config_by_domain")
            self.assertEqual(int(market_run.get("seed_leaderboard_limit") or 0), 12)
            self.assertEqual(str(market_run.get("seed_leaderboard_limit_source") or ""), "config_global")
            self.assertEqual(float(market_run.get("seed_trend_threshold") or 0.0), 0.4)
            self.assertEqual(str(market_run.get("seed_trend_threshold_source") or ""), "config_by_domain")

            fitness_run = dict(runs_by_domain.get("fitness_apps") or {})
            self.assertEqual(int(fitness_run.get("seed_lookback_days") or 0), 7)
            self.assertEqual(str(fitness_run.get("seed_lookback_days_source") or ""), "config_global")
            self.assertEqual(int(fitness_run.get("seed_leaderboard_limit") or 0), 5)
            self.assertEqual(str(fitness_run.get("seed_leaderboard_limit_source") or ""), "config_by_domain")
            self.assertEqual(float(fitness_run.get("seed_trend_threshold") or 0.0), 0.25)
            self.assertEqual(str(fitness_run.get("seed_trend_threshold_source") or ""), "config_global")

    def test_operator_cycle_seed_cli_leaderboard_window_overrides_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            market_input_path = root / "configs" / "inputs" / "market_ml_feedback.jsonl"
            market_input_path.parent.mkdir(parents=True, exist_ok=True)
            market_input_path.write_text(
                json.dumps(
                    {
                        "id": "market-1",
                        "title": "Classifier false positives spike",
                        "summary": "Classifier false positives spike during volatility windows.",
                        "review": "Classifier false positives spike during volatility windows.",
                        "severity": 4,
                        "created_at": "2026-04-21T10:00:00Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            config_path = root / "configs" / "operator_cycle_leaderboard_window_override.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "seed_lookback_days_by_domain": {
                                "market_ml": 14,
                            },
                            "seed_leaderboard_limit_by_domain": {
                                "market_ml": 15,
                            },
                            "seed_trend_threshold_by_domain": {
                                "market_ml": 0.4,
                            },
                            "seed_min_cross_app_count": 1,
                            "seed_min_signal_count_current": 1,
                        },
                        "feed_jobs": [],
                        "feedback_jobs": [
                            {
                                "domain": "market_ml",
                                "source": "incident_log",
                                "input_path": "inputs/market_ml_feedback.jsonl",
                                "input_format": "jsonl",
                            }
                        ],
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_leaderboard_window_override"
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
                seed_enable=True,
                seed_domains="market_ml",
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="market_ml",
                seed_source=None,
                seed_hypothesis_source=None,
                seed_trends=None,
                seed_limit=None,
                seed_min_impact_score=None,
                seed_min_impact_delta=None,
                seed_entry_source=None,
                seed_fallback_entry_source=None,
                seed_owner="operator",
                seed_lookup_limit=None,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=3,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=4,
                seed_cooling_limit=10,
                seed_app_fields="app_name,source_context.app",
                seed_top_apps_per_cluster=3,
                seed_min_cross_app_count=None,
                seed_min_signal_count_current=None,
                seed_own_app_aliases=None,
                seed_trend_threshold=0.1,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=False,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain=None,
                draft_statuses="queued",
                draft_limit=8,
                draft_lookup_limit=400,
                draft_include_existing=False,
                draft_overwrite_artifacts=False,
                draft_environment=None,
                draft_default_sample_size=100,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_operator_cycle(args)
            payload = json.loads(out.getvalue())

            stage_statuses = dict(payload.get("stage_statuses") or {})
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")

            seed_domain_runs = [row for row in list(payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
            self.assertEqual(len(seed_domain_runs), 1)
            only_run = dict(seed_domain_runs[0] or {})
            self.assertEqual(int(only_run.get("seed_lookback_days") or 0), 3)
            self.assertEqual(str(only_run.get("seed_lookback_days_source") or ""), "cli_override")
            self.assertEqual(int(only_run.get("seed_leaderboard_limit") or 0), 4)
            self.assertEqual(str(only_run.get("seed_leaderboard_limit_source") or ""), "cli_override")
            self.assertEqual(float(only_run.get("seed_trend_threshold") or 0.0), 0.1)
            self.assertEqual(str(only_run.get("seed_trend_threshold_source") or ""), "cli_override")

    def test_operator_cycle_runs_seed_and_draft_stages_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            raw_input_path = root / "inputs" / "fitness_reviews.json"
            raw_input_path.parent.mkdir(parents=True, exist_ok=True)
            raw_input_path.write_text(
                json.dumps(
                    {
                        "reviews": [
                            {
                                "id": "seed-feed-1",
                                "title": "Paywall blocks workout trial",
                                "text": "Paywall appears before I can finish a single workout.",
                                "rating": 1,
                                "created_at": "2026-04-21T10:00:00Z",
                            },
                            {
                                "id": "seed-feed-2",
                                "title": "Paywall too early",
                                "text": "I am asked to subscribe before trying a complete session.",
                                "rating": 2,
                                "created_at": "2026-04-21T12:30:00Z",
                            },
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            config_path = root / "operator_cycle_seed_and_draft_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "owner": "operator",
                            "auto_register": True,
                            "allow_missing_inputs": False,
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
                                    "created_at": "created_at",
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
                        "experiment_jobs": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_dir = root / "output" / "operator_seed_and_draft"
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
                seed_enable=True,
                seed_leaderboard_input_path=None,
                seed_leaderboard_input_format=None,
                seed_leaderboard_report_path=None,
                seed_report_path=None,
                seed_domain="fitness_apps",
                seed_source="market_reviews",
                seed_hypothesis_source="fitness_leaderboard",
                seed_trends="new,rising",
                seed_limit=6,
                seed_min_impact_score=0.0,
                seed_min_impact_delta=0.0,
                seed_min_signal_count_current=1,
                seed_owner="operator",
                seed_lookup_limit=200,
                seed_as_of="2026-04-22T12:00:00Z",
                seed_lookback_days=7,
                seed_min_cluster_count=1,
                seed_cluster_limit=20,
                seed_leaderboard_limit=10,
                seed_cooling_limit=10,
                seed_trend_threshold=0.25,
                seed_timestamp_fields="created_at",
                seed_include_untimed_current=False,
                draft_enable=True,
                draft_seed_report_path=None,
                draft_config_path=None,
                draft_output_config_path=None,
                draft_report_path=None,
                draft_artifacts_dir=None,
                draft_domain="fitness_apps",
                draft_statuses="queued",
                draft_limit=6,
                draft_lookup_limit=200,
                draft_include_existing=False,
                draft_overwrite_artifacts=True,
                draft_environment=None,
                draft_default_sample_size=120,
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
            self.assertEqual(str(stage_statuses.get("fitness_leaderboard") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("seed_from_leaderboard") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("draft_experiment_jobs") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("daily_pipeline") or ""), "ok")
            self.assertEqual(str(stage_statuses.get("execute_retests") or ""), "ok")

            seed_payload = dict(payload.get("seed_from_leaderboard") or {})
            self.assertEqual(int(seed_payload.get("min_signal_count_current") or 0), 1)
            created_rows = [row for row in list(seed_payload.get("created") or []) if isinstance(row, dict)]
            self.assertGreaterEqual(len(created_rows), 1)

            draft_payload = dict(payload.get("draft") or {})
            self.assertGreaterEqual(int(draft_payload.get("drafted_count") or 0), 1)

            seeded_ids = {str(row.get("hypothesis_id") or "") for row in created_rows if str(row.get("hypothesis_id") or "")}
            self.assertTrue(bool(seeded_ids))

            daily_report_path = Path(str(payload.get("daily_report_path") or ""))
            self.assertTrue(daily_report_path.exists())
            daily_payload = json.loads(daily_report_path.read_text(encoding="utf-8"))
            self.assertEqual(int(daily_payload.get("error_count") or 0), 0)
            self.assertGreaterEqual(int(daily_payload.get("experiment_runs_count") or 0), 1)
            experiment_hypothesis_ids = {
                str(row.get("hypothesis_id") or "")
                for row in list(daily_payload.get("experiment_runs") or [])
                if isinstance(row, dict)
            }
            self.assertTrue(any(hypothesis_id in experiment_hypothesis_ids for hypothesis_id in seeded_ids))

    def test_benchmark_frustrations_ranks_cross_domain_pains_and_win_rates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            daily_report_path = root / "reports" / "daily_pipeline_report.json"
            daily_report_path.parent.mkdir(parents=True, exist_ok=True)
            daily_report_path.write_text(
                json.dumps(
                    {
                        "experiment_runs": [
                            {
                                "run_id": "exp_quant_1",
                                "hypothesis_id": "hyp_quant_1",
                                "verdict": "promote",
                                "resolution": {
                                    "domain": "quant_finance",
                                    "friction_key": "execution_slippage_regime_drift",
                                },
                            },
                            {
                                "run_id": "exp_fit_1",
                                "hypothesis_id": "hyp_fit_1",
                                "verdict": "blocked_guardrail",
                                "resolution": {
                                    "domain": "fitness_apps",
                                    "friction_key": "paywall_before_core_workout_trial",
                                },
                            },
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            operator_report_path = root / "reports" / "operator_cycle_report.json"
            operator_report_path.write_text(
                json.dumps(
                    {
                        "daily_report_path": str(daily_report_path),
                        "seed_domain_runs": [
                            {
                                "domain": "quant_finance",
                                "fitness_leaderboard": {
                                    "domain": "quant_finance",
                                    "leaderboard": [
                                        {
                                            "friction_key": "execution_slippage_regime_drift",
                                            "canonical_key": "execution slippage regime drift",
                                            "trend": "rising",
                                            "signal_count_current": 4,
                                            "signal_count_previous": 2,
                                            "impact_score_current": 6.1,
                                            "impact_score_delta": 1.4,
                                        }
                                    ],
                                },
                            },
                            {
                                "domain": "fitness_apps",
                                "fitness_leaderboard": {
                                    "domain": "fitness_apps",
                                    "leaderboard": [
                                        {
                                            "friction_key": "paywall_before_core_workout_trial",
                                            "canonical_key": "paywall before core workout trial",
                                            "trend": "rising",
                                            "signal_count_current": 6,
                                            "signal_count_previous": 4,
                                            "impact_score_current": 7.8,
                                            "impact_score_delta": 2.3,
                                        },
                                        {
                                            "friction_key": "onboarding_plan_too_rigid_for_beginner_adherence",
                                            "canonical_key": "onboarding too rigid",
                                            "trend": "new",
                                            "signal_count_current": 2,
                                            "signal_count_previous": 0,
                                            "impact_score_current": 4.0,
                                            "impact_score_delta": 4.0,
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                report_path=operator_report_path,
                top_limit=5,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_benchmark_frustrations(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            summary = dict(payload.get("summary") or {})
            self.assertEqual(int(summary.get("domain_count") or 0), 2)
            self.assertGreaterEqual(int(summary.get("recurring_pain_count") or 0), 3)
            self.assertGreaterEqual(int(summary.get("implementation_count") or 0), 2)

            recurring = [row for row in list(payload.get("recurring_pains") or []) if isinstance(row, dict)]
            self.assertGreaterEqual(len(recurring), 3)
            self.assertEqual(str((recurring[0] or {}).get("friction_key") or ""), "paywall_before_core_workout_trial")

            win_rates = [row for row in list(payload.get("implementation_win_rates") or []) if isinstance(row, dict)]
            self.assertGreaterEqual(len(win_rates), 2)
            quant_row = next(
                (row for row in win_rates if str(row.get("domain") or "") == "quant_finance"),
                {},
            )
            self.assertEqual(float(quant_row.get("win_rate") or 0.0), 1.0)

            priority = [row for row in list(payload.get("priority_board") or []) if isinstance(row, dict)]
            self.assertGreaterEqual(len(priority), 3)
            self.assertEqual(str((priority[0] or {}).get("friction_key") or ""), "paywall_before_core_workout_trial")
            self.assertGreater(float((priority[0] or {}).get("opportunity_score") or 0.0), 0.0)

    def test_benchmark_frustrations_warns_when_report_lacks_seed_rankings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            daily_report_path = root / "daily_pipeline_report.json"
            daily_report_path.write_text(
                json.dumps(
                    {
                        "experiment_runs": [
                            {
                                "run_id": "exp_ml_1",
                                "hypothesis_id": "hyp_ml_1",
                                "verdict": "promote",
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

            args = argparse.Namespace(
                report_path=daily_report_path,
                top_limit=5,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_benchmark_frustrations(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            gaps = [str(item) for item in list(payload.get("data_gaps") or [])]
            self.assertIn("missing_recurring_pain_rows", gaps)
            win_rates = [row for row in list(payload.get("implementation_win_rates") or []) if isinstance(row, dict)]
            self.assertEqual(len(win_rates), 1)
            self.assertEqual(str((win_rates[0] or {}).get("domain") or ""), "market_ml")

    def test_benchmark_frustrations_recovers_missing_domain_and_friction_from_seed_context(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            daily_report_path = root / "reports" / "daily_pipeline_report.json"
            daily_report_path.parent.mkdir(parents=True, exist_ok=True)
            daily_report_path.write_text(
                json.dumps(
                    {
                        "experiment_runs": [
                            {
                                "run_id": "exp_fit_seeded",
                                "hypothesis_id": "hyp_fit_seeded",
                                "verdict": "promote",
                                "resolution": {},
                            },
                            {
                                "run_id": "exp_market_seeded",
                                "hypothesis_id": "hyp_market_seeded",
                                "verdict": "blocked_guardrail",
                                "resolution": {
                                    "domain": "market_ml",
                                    "friction_key": "false_positive_drift_high_volatility_windows",
                                },
                            },
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            operator_report_path = root / "reports" / "operator_cycle_report.json"
            operator_report_path.write_text(
                json.dumps(
                    {
                        "daily_report_path": str(daily_report_path),
                        "seed_domain_runs": [
                            {
                                "domain": "fitness_apps",
                                "fitness_leaderboard": {
                                    "domain": "fitness_apps",
                                    "leaderboard": [
                                        {
                                            "friction_key": "paywall_before_core_workout_trial",
                                            "canonical_key": "paywall before core workout trial",
                                            "trend": "rising",
                                            "signal_count_current": 5,
                                            "signal_count_previous": 3,
                                            "impact_score_current": 7.0,
                                            "impact_score_delta": 1.5,
                                        }
                                    ],
                                },
                                "seed_from_leaderboard": {
                                    "existing": [
                                        {
                                            "hypothesis_id": "hyp_fit_seeded",
                                            "domain": "fitness_apps",
                                            "friction_key": "paywall before core workout trial",
                                        }
                                    ]
                                },
                            },
                            {
                                "domain": "market_ml",
                                "fitness_leaderboard": {
                                    "domain": "market_ml",
                                    "leaderboard": [
                                        {
                                            "friction_key": "false_positive_drift_in_high_volatility_windows",
                                            "canonical_key": "false positive drift high volatility windows",
                                            "trend": "new",
                                            "signal_count_current": 3,
                                            "signal_count_previous": 0,
                                            "impact_score_current": 6.0,
                                            "impact_score_delta": 6.0,
                                        }
                                    ],
                                },
                                "seed_from_leaderboard": {
                                    "existing": [
                                        {
                                            "hypothesis_id": "hyp_market_seeded",
                                            "domain": "market_ml",
                                            "friction_key": "false positive drift in high volatility windows",
                                        }
                                    ]
                                },
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                report_path=operator_report_path,
                top_limit=10,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_benchmark_frustrations(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            summary = dict(payload.get("summary") or {})
            self.assertEqual(int(summary.get("implementation_count") or 0), 2)

            win_rates = [row for row in list(payload.get("implementation_win_rates") or []) if isinstance(row, dict)]
            fit_row = next(
                (row for row in win_rates if str(row.get("domain") or "") == "fitness_apps"),
                {},
            )
            self.assertEqual(str(fit_row.get("friction_key") or ""), "paywall_before_core_workout_trial")
            self.assertEqual(float(fit_row.get("win_rate") or 0.0), 1.0)
            self.assertIn("hyp_fit_seeded", [str(item) for item in list(fit_row.get("hypothesis_ids") or [])])

            market_row = next(
                (row for row in win_rates if str(row.get("domain") or "") == "market_ml"),
                {},
            )
            self.assertEqual(
                str(market_row.get("friction_key") or ""),
                "false_positive_drift_high_volatility_windows",
            )
            self.assertEqual(float(market_row.get("guardrail_block_rate") or 0.0), 1.0)

            priority_rows = [row for row in list(payload.get("priority_board") or []) if isinstance(row, dict)]
            market_priority = next(
                (
                    row
                    for row in priority_rows
                    if str(row.get("domain") or "") == "market_ml"
                    and str(row.get("friction_key") or "") == "false_positive_drift_in_high_volatility_windows"
                ),
                {},
            )
            self.assertEqual(int(market_priority.get("implementation_run_count") or 0), 1)
            self.assertEqual(str(market_priority.get("implementation_match_strategy") or ""), "hypothesis_id")
            self.assertEqual(
                str(market_priority.get("implementation_matched_hypothesis_id") or ""),
                "hyp_market_seeded",
            )

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

    def test_verify_matrix_compact_warns_when_required_domains_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)

            recheck_command = (
                "python3 -m jarvis.cli improvement verify-matrix-alert "
                "--matrix-path matrix.json --report-path reports/daily_pipeline_report.json"
            )
            acknowledge_command = "python3 -m jarvis.cli interrupts acknowledge intr_verify_warn --actor operator"
            report_path.write_text(
                json.dumps(
                    {
                        "verify_matrix": {
                            "status": "warning",
                            "drift_severity": "critical",
                            "summary": {
                                "mismatch_count": 1,
                                "missing_count": 1,
                                "invalid_count": 0,
                            },
                            "comparisons": [
                                {
                                    "scenario_id": "quant_expected_promote",
                                    "domain": "quant_finance",
                                    "status": "matched",
                                },
                                {
                                    "scenario_id": "market_ml_expected_promote",
                                    "domain": "market_ml",
                                    "status": "mismatch",
                                },
                            ],
                        },
                        "verify_matrix_alert": {
                            "status": "warning",
                            "drift_severity": "critical",
                            "acknowledge_commands": [
                                acknowledge_command,
                            ],
                            "alert": {
                                "top_scenarios": [
                                    "market_ml_expected_promote",
                                ],
                            },
                        },
                        "promotion_lock": {
                            "acknowledge_commands": [
                                acknowledge_command,
                            ],
                            "recheck_command": recheck_command,
                        },
                        "verify_matrix_report_path": str((root / "reports" / "verify_matrix_report.json").resolve()),
                        "verify_matrix_alert_report_path": str((root / "reports" / "verify_matrix_alert_report.json").resolve()),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            compact_path = root / "artifacts" / "verify_matrix_compact.json"
            markdown_path = root / "artifacts" / "verify_matrix_compact.md"
            args = argparse.Namespace(
                report_path=report_path,
                output_path=compact_path,
                markdown_path=markdown_path,
                strict=False,
                json_compact=False,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_compact(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertGreater(int(payload.get("missing_domain_count") or 0), 0)
            required_domains = [str(item) for item in list(payload.get("required_domains") or [])]
            self.assertEqual(
                required_domains,
                ["quant_finance", "kalshi_weather", "fitness_apps", "market_ml"],
            )
            unlock_ready_commands = [
                str(item).strip()
                for item in list(payload.get("unlock_ready_commands") or [])
                if str(item).strip()
            ]
            first_unlock_ready_command = str(payload.get("first_unlock_ready_command") or "")
            self.assertEqual(first_unlock_ready_command, unlock_ready_commands[0] if unlock_ready_commands else "none")

            self.assertTrue(compact_path.exists())
            compact_payload = json.loads(compact_path.read_text(encoding="utf-8"))
            self.assertEqual(str(compact_payload.get("status") or ""), "warning")
            self.assertEqual(int(compact_payload.get("missing_domain_count") or 0), int(payload.get("missing_domain_count") or 0))
            self.assertEqual(
                str(compact_payload.get("first_unlock_ready_command") or ""),
                first_unlock_ready_command,
            )

            self.assertTrue(markdown_path.exists())
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("first_unlock_ready_command", markdown)
            self.assertIn(first_unlock_ready_command, markdown)

    def test_verify_matrix_compact_ok_when_all_required_domains_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)

            recheck_command = (
                "python3 -m jarvis.cli improvement verify-matrix-alert "
                "--matrix-path matrix.json --report-path reports/daily_pipeline_report.json"
            )
            report_path.write_text(
                json.dumps(
                    {
                        "verify_matrix": {
                            "status": "ok",
                            "drift_severity": "none",
                            "summary": {
                                "mismatch_count": 0,
                                "missing_count": 0,
                                "invalid_count": 0,
                            },
                            "comparisons": [
                                {
                                    "scenario_id": "quant_expected_promote",
                                    "domain": "quant_finance",
                                    "status": "matched",
                                },
                                {
                                    "scenario_id": "kalshi_expected_promote",
                                    "domain": "kalshi_weather",
                                    "status": "matched",
                                },
                                {
                                    "scenario_id": "fitness_expected_promote",
                                    "domain": "fitness_apps",
                                    "status": "matched",
                                },
                                {
                                    "scenario_id": "market_expected_promote",
                                    "domain": "market_ml",
                                    "status": "matched",
                                },
                            ],
                        },
                        "verify_matrix_alert": {
                            "status": "ok",
                            "drift_severity": "none",
                            "acknowledge_commands": [],
                            "alert": {},
                        },
                        "promotion_lock": {
                            "acknowledge_commands": [],
                            "recheck_command": recheck_command,
                        },
                        "verify_matrix_report_path": str((root / "reports" / "verify_matrix_report.json").resolve()),
                        "verify_matrix_alert_report_path": str((root / "reports" / "verify_matrix_alert_report.json").resolve()),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            compact_path = root / "artifacts" / "verify_matrix_compact.json"
            markdown_path = root / "artifacts" / "verify_matrix_compact.md"
            args = argparse.Namespace(
                report_path=report_path,
                output_path=compact_path,
                markdown_path=markdown_path,
                strict=False,
                json_compact=False,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_compact(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("missing_domain_count") or 0), 0)
            self.assertEqual(str(payload.get("first_missing_domain") or ""), "none")
            required_domains = [str(item) for item in list(payload.get("required_domains") or [])]
            self.assertEqual(
                required_domains,
                ["quant_finance", "kalshi_weather", "fitness_apps", "market_ml"],
            )

            unlock_ready_commands = [
                str(item).strip()
                for item in list(payload.get("unlock_ready_commands") or [])
                if str(item).strip()
            ]
            first_unlock_ready_command = str(payload.get("first_unlock_ready_command") or "")
            self.assertEqual(first_unlock_ready_command, unlock_ready_commands[0] if unlock_ready_commands else "none")

            self.assertTrue(compact_path.exists())
            compact_payload = json.loads(compact_path.read_text(encoding="utf-8"))
            self.assertEqual(str(compact_payload.get("status") or ""), "ok")
            self.assertEqual(int(compact_payload.get("missing_domain_count") or 0), 0)
            self.assertEqual(str(compact_payload.get("first_missing_domain") or ""), "none")
            self.assertTrue(markdown_path.exists())

    def test_verify_matrix_compact_emits_github_outputs_and_summary_with_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)

            recheck_command = (
                "python3 -m jarvis.cli improvement verify-matrix-alert "
                "--matrix-path matrix.json --report-path reports/daily_pipeline_report.json"
            )
            acknowledge_command = "python3 -m jarvis.cli interrupts acknowledge intr_verify_warn --actor operator"
            report_path.write_text(
                json.dumps(
                    {
                        "verify_matrix": {
                            "status": "warning",
                            "drift_severity": "critical",
                            "summary": {
                                "mismatch_count": 1,
                                "missing_count": 1,
                                "invalid_count": 0,
                            },
                            "comparisons": [
                                {
                                    "scenario_id": "quant_expected_promote",
                                    "domain": "quant_finance",
                                    "status": "matched",
                                },
                                {
                                    "scenario_id": "market_ml_expected_promote",
                                    "domain": "market_ml",
                                    "status": "mismatch",
                                },
                            ],
                        },
                        "verify_matrix_alert": {
                            "status": "warning",
                            "drift_severity": "critical",
                            "acknowledge_commands": [
                                acknowledge_command,
                            ],
                            "alert": {
                                "top_scenarios": [
                                    "market_ml_expected_promote",
                                ],
                            },
                        },
                        "promotion_lock": {
                            "acknowledge_commands": [
                                acknowledge_command,
                            ],
                            "recheck_command": recheck_command,
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            compact_path = root / "artifacts" / "verify_matrix_compact.json"
            markdown_path = root / "artifacts" / "verify_matrix_compact.md"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)
            args = argparse.Namespace(
                report_path=report_path,
                output_path=compact_path,
                markdown_path=markdown_path,
                emit_github_output=True,
                summary_heading="Verify Matrix Compact Coverage",
                summary_include_markdown=True,
                strict=False,
                json_compact=False,
            )

            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_verify_matrix_compact(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"verify_matrix_compact_path={compact_path.resolve()}", output_lines)
            self.assertIn(f"verify_matrix_compact_markdown_path={markdown_path.resolve()}", output_lines)
            self.assertIn("verify_matrix_compact_status=warning", output_lines)
            self.assertIn("verify_matrix_status=warning", output_lines)
            self.assertIn("verify_matrix_drift_severity=critical", output_lines)
            self.assertIn("missing_domain_count=2", output_lines)
            self.assertIn("verify_matrix_required_domain_missing_count=2", output_lines)
            self.assertIn("verify_matrix_first_missing_domain=kalshi_weather", output_lines)
            self.assertIn(f"verify_matrix_recheck_command={recheck_command}", output_lines)
            self.assertIn(f"verify_matrix_first_unlock_ready_command={recheck_command}", output_lines)
            self.assertIn(f"first_unlock_ready_command={recheck_command}", output_lines)
            self.assertTrue(any(line.startswith("operator_ack_bundle_command_sequence=") for line in output_lines))
            self.assertIn("first_top_scenario=market_ml_expected_promote", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Verify Matrix Compact Coverage", summary)
            self.assertIn("- status: `warning`", summary)
            self.assertIn("- verify_matrix_status: `warning`", summary)
            self.assertIn("- drift_severity: `critical`", summary)
            self.assertIn("- missing_domain_count: `2`", summary)
            self.assertIn("- missing_domains_csv: `kalshi_weather,fitness_apps`", summary)
            self.assertIn(f"- first_unlock_ready_command: `{recheck_command}`", summary)
            self.assertIn("# Verify Matrix Compact Coverage", summary)

    def test_verify_matrix_coverage_alert_creates_interrupt_and_updates_compact_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            compact_path = root / "artifacts" / "verify_matrix_compact.json"
            compact_path.parent.mkdir(parents=True, exist_ok=True)
            recheck_command = (
                "python3 -m jarvis.cli improvement verify-matrix "
                "--matrix-path matrix.json --report-path reports/daily_pipeline_report.json"
            )
            compact_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "missing_domain_count": 2,
                        "missing_domains_csv": "kalshi_weather,fitness_apps",
                        "first_missing_domain": "kalshi_weather",
                        "acknowledge_commands": [],
                        "recheck_command": recheck_command,
                        "repair_commands": [],
                        "suggested_actions": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            alert_path = root / "artifacts" / "verify_matrix_coverage_alert.json"
            args = argparse.Namespace(
                compact_path=compact_path,
                output_path=alert_path,
                missing_domain_count=None,
                missing_domains_csv=None,
                first_missing_domain=None,
                compact_status=None,
                recheck_command=None,
                first_unlock_ready_command=None,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_coverage_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertTrue(bool(payload.get("alert_created")))
            interrupt_id = str(payload.get("interrupt_id") or "").strip()
            self.assertTrue(bool(interrupt_id))
            acknowledge_command = str(payload.get("acknowledge_command") or "").strip()
            self.assertIn(interrupt_id, acknowledge_command)
            self.assertEqual(str(payload.get("recheck_command") or ""), recheck_command)
            self.assertEqual(str(payload.get("first_unlock_ready_command") or ""), recheck_command)
            self.assertEqual(str(payload.get("first_repair_command") or ""), acknowledge_command)
            self.assertEqual(str(payload.get("verify_matrix_coverage_recheck_command") or ""), recheck_command)
            self.assertEqual(
                str(payload.get("verify_matrix_coverage_first_repair_command") or ""),
                acknowledge_command,
            )
            self.assertTrue(alert_path.exists())

            compact_payload = json.loads(compact_path.read_text(encoding="utf-8"))
            self.assertEqual(
                str(Path(str(compact_payload.get("coverage_alert_path") or "")).resolve()),
                str(alert_path.resolve()),
            )
            self.assertEqual(str(compact_payload.get("coverage_interrupt_id") or ""), interrupt_id)
            self.assertEqual(str(compact_payload.get("first_unlock_ready_command") or ""), recheck_command)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                interrupts = runtime.list_interrupts(status="all", limit=20)
                self.assertEqual(len(interrupts), 1)
                self.assertEqual(str((interrupts[0] or {}).get("status") or ""), "delivered")
                self.assertEqual(str((interrupts[0] or {}).get("interrupt_id") or ""), interrupt_id)
            finally:
                runtime.close()

    def test_verify_matrix_coverage_alert_emits_github_outputs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            compact_path = root / "artifacts" / "verify_matrix_compact.json"
            compact_path.parent.mkdir(parents=True, exist_ok=True)
            recheck_command = (
                "python3 -m jarvis.cli improvement verify-matrix "
                "--matrix-path matrix.json --report-path reports/daily_pipeline_report.json"
            )
            compact_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "missing_domain_count": 2,
                        "missing_domains_csv": "kalshi_weather,fitness_apps",
                        "first_missing_domain": "kalshi_weather",
                        "acknowledge_commands": [],
                        "recheck_command": recheck_command,
                        "repair_commands": [],
                        "suggested_actions": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            alert_path = root / "artifacts" / "verify_matrix_coverage_alert.json"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)
            args = argparse.Namespace(
                compact_path=compact_path,
                output_path=alert_path,
                missing_domain_count=None,
                missing_domains_csv=None,
                first_missing_domain=None,
                compact_status=None,
                recheck_command=None,
                first_unlock_ready_command=None,
                emit_github_output=True,
                summary_heading="Verify Matrix Coverage Interrupt Alert",
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_verify_matrix_coverage_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"verify_matrix_coverage_alert_path={alert_path.resolve()}", output_lines)
            self.assertIn(
                f"verify_matrix_coverage_interrupt_id={payload.get('verify_matrix_coverage_interrupt_id')}",
                output_lines,
            )
            self.assertIn(
                f"verify_matrix_coverage_alert_created={int(payload.get('verify_matrix_coverage_alert_created') or 0)}",
                output_lines,
            )
            self.assertIn(
                f"verify_matrix_coverage_acknowledge_command={payload.get('verify_matrix_coverage_acknowledge_command')}",
                output_lines,
            )
            self.assertIn(
                f"verify_matrix_coverage_recheck_command={payload.get('verify_matrix_coverage_recheck_command')}",
                output_lines,
            )
            self.assertIn(
                f"verify_matrix_coverage_first_repair_command={payload.get('verify_matrix_coverage_first_repair_command')}",
                output_lines,
            )
            self.assertIn(
                f"verify_matrix_coverage_runtime_error={payload.get('verify_matrix_coverage_runtime_error')}",
                output_lines,
            )

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Verify Matrix Coverage Interrupt Alert", summary)
            self.assertIn(
                f"- interrupt_id: `{payload.get('verify_matrix_coverage_interrupt_id')}`",
                summary,
            )
            self.assertIn(
                f"- alert_created: `{int(payload.get('verify_matrix_coverage_alert_created') or 0)}`",
                summary,
            )
            self.assertIn("- missing_domain_count: `2`", summary)
            self.assertIn("- first_missing_domain: `kalshi_weather`", summary)
            self.assertIn("- missing_domains_csv: `kalshi_weather,fitness_apps`", summary)
            self.assertIn(
                f"- acknowledge_command: `{payload.get('verify_matrix_coverage_acknowledge_command')}`",
                summary,
            )
            self.assertIn(
                f"- recheck_command: `{payload.get('verify_matrix_coverage_recheck_command')}`",
                summary,
            )
            self.assertIn(
                f"- first_repair_command: `{payload.get('verify_matrix_coverage_first_repair_command')}`",
                summary,
            )
            self.assertIn("- runtime_error: `none`", summary)

    def test_verify_matrix_coverage_alert_skips_interrupt_when_missing_domain_count_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            compact_path = root / "artifacts" / "verify_matrix_compact.json"
            compact_path.parent.mkdir(parents=True, exist_ok=True)
            compact_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "missing_domain_count": 0,
                        "missing_domains_csv": "none",
                        "first_missing_domain": "none",
                        "acknowledge_commands": [],
                        "recheck_command": "python3 -m jarvis.cli improvement verify-matrix --matrix-path matrix.json",
                        "repair_commands": [],
                        "suggested_actions": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            alert_path = root / "artifacts" / "verify_matrix_coverage_alert.json"
            args = argparse.Namespace(
                compact_path=compact_path,
                output_path=alert_path,
                missing_domain_count=None,
                missing_domains_csv=None,
                first_missing_domain=None,
                compact_status=None,
                recheck_command=None,
                first_unlock_ready_command=None,
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_coverage_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertFalse(bool(payload.get("alert_created")))
            self.assertIsNone(payload.get("interrupt_id"))
            self.assertEqual(str(payload.get("verify_matrix_coverage_interrupt_id") or ""), "none")
            self.assertEqual(int(payload.get("verify_matrix_coverage_alert_created") or 0), 0)
            self.assertTrue(alert_path.exists())

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                interrupts = runtime.list_interrupts(status="all", limit=20)
                self.assertEqual(len(interrupts), 0)
            finally:
                runtime.close()

    def test_verify_matrix_guardrail_gate_ok_in_strict_mode_when_gate_conditions_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "stage_error_count": 0,
                        "verify_matrix": {
                            "status": "ok",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_path = root / "artifacts" / "verify_matrix_guardrail_gate.json"
            args = argparse.Namespace(
                report_path=report_path,
                output_path=output_path,
                strict=True,
                json_compact=False,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_verify_matrix_guardrail_gate(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(int(payload.get("stage_error_count") or 0), 0)
            self.assertEqual(str(payload.get("verify_matrix_status") or ""), "ok")
            self.assertEqual(str(payload.get("failure_reason") or ""), "none")
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(str(written.get("status") or ""), "ok")
            self.assertEqual(str(written.get("failure_reason") or ""), "none")

    def test_verify_matrix_guardrail_gate_strict_raises_when_stage_error_count_positive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "stage_error_count": 2,
                        "verify_matrix": {
                            "status": "ok",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_path = root / "artifacts" / "verify_matrix_guardrail_gate.json"
            args = argparse.Namespace(
                report_path=report_path,
                output_path=output_path,
                strict=True,
                json_compact=False,
            )

            with self.assertRaises(SystemExit) as raised:
                cmd_improvement_verify_matrix_guardrail_gate(args)
            self.assertIn(
                "operator_guardrail_gate_failed:stage_error_count>0",
                str(raised.exception),
            )
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(str(written.get("status") or ""), "warning")
            self.assertIn(
                "operator_guardrail_gate_failed:stage_error_count>0",
                str(written.get("failure_reason") or ""),
            )

    def test_verify_matrix_guardrail_gate_strict_raises_when_verify_matrix_status_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "stage_error_count": 0,
                        "verify_matrix": {
                            "status": "warning",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_path = root / "artifacts" / "verify_matrix_guardrail_gate.json"
            args = argparse.Namespace(
                report_path=report_path,
                output_path=output_path,
                strict=True,
                json_compact=False,
            )

            with self.assertRaises(SystemExit) as raised:
                cmd_improvement_verify_matrix_guardrail_gate(args)
            self.assertIn(
                "operator_guardrail_gate_failed:verify_matrix_status_not_ok",
                str(raised.exception),
            )
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(str(written.get("status") or ""), "warning")
            self.assertIn(
                "operator_guardrail_gate_failed:verify_matrix_status_not_ok",
                str(written.get("failure_reason") or ""),
            )

    def test_verify_matrix_guardrail_gate_emits_github_outputs_and_summary_before_strict_exit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "stage_error_count": 2,
                        "verify_matrix": {
                            "status": "warning",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            output_path = root / "artifacts" / "verify_matrix_guardrail_gate.json"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                report_path=report_path,
                output_path=output_path,
                emit_github_output=True,
                summary_heading="Operator Guardrail Gate",
                strict=True,
                json_compact=False,
            )

            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with self.assertRaises(SystemExit) as raised:
                    with redirect_stdout(out):
                        cmd_improvement_verify_matrix_guardrail_gate(args)
            self.assertIn(
                "operator_guardrail_gate_failed:stage_error_count>0",
                str(raised.exception),
            )

            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(int(payload.get("stage_error_count") or 0), 2)
            self.assertEqual(str(payload.get("verify_matrix_status") or ""), "warning")
            self.assertTrue(output_path.exists())

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"guardrail_gate_report={report_path.resolve()}", output_lines)
            self.assertIn("guardrail_gate_operator_status=warning", output_lines)
            self.assertIn("guardrail_gate_stage_error_count=2", output_lines)
            self.assertIn("guardrail_gate_verify_matrix_status=warning", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Operator Guardrail Gate", summary)
            self.assertIn("- operator_status: `warning`", summary)
            self.assertIn("- stage_error_count: `2`", summary)
            self.assertIn("- verify_matrix_status: `warning`", summary)

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

    def test_domain_smoke_outputs_emits_github_outputs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            domain = "fitness_apps"
            artifact_root = root / "output" / "ci" / "domain_smoke"
            summary_path = artifact_root / domain / f"{domain}_smoke_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "domain": domain,
                        "pull_report_path": str((summary_path.parent / "pull_report.json").resolve()),
                        "leaderboard_report_path": str((summary_path.parent / "leaderboard_report.json").resolve()),
                        "seed_report_path": str((summary_path.parent / "seed_report.json").resolve()),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                domain=domain,
                artifact_root=artifact_root,
                summary_path=None,
                emit_github_output=True,
                summary_heading="Domain Smoke",
                output_path=None,
                json_compact=False,
            )
            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_domain_smoke_outputs(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("domain") or ""), domain)
            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("reported_domain") or ""), domain)
            self.assertEqual(str(payload.get("reason") or ""), "none")
            self.assertEqual(int(payload.get("smoke_blocking") or 0), 0)
            self.assertEqual(str(payload.get("summary_path") or ""), str(summary_path.resolve()))

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"domain={domain}", output_lines)
            self.assertIn(f"summary_path={summary_path.resolve()}", output_lines)
            self.assertIn("status=ok", output_lines)
            self.assertIn(f"reported_domain={domain}", output_lines)
            self.assertIn("reason=none", output_lines)
            self.assertIn("smoke_blocking=0", output_lines)
            self.assertIn(
                f"pull_report_path={str((summary_path.parent / 'pull_report.json').resolve())}",
                output_lines,
            )
            self.assertIn(
                f"leaderboard_report_path={str((summary_path.parent / 'leaderboard_report.json').resolve())}",
                output_lines,
            )
            self.assertIn(
                f"seed_report_path={str((summary_path.parent / 'seed_report.json').resolve())}",
                output_lines,
            )

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Domain Smoke", summary)
            self.assertIn(f"- domain: `{domain}`", summary)
            self.assertIn("- status: `ok`", summary)
            self.assertIn("- reason: `none`", summary)
            self.assertIn(f"- summary_path: `{summary_path.resolve()}`", summary)

    def test_domain_smoke_runtime_alert_creates_interrupt_and_emits_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            domain = "kalshi_weather"
            summary_path = root / "output" / "ci" / "domain_smoke" / domain / f"{domain}_smoke_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "domain": domain,
                        "reason": "domain_smoke_status_not_ok:warning",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            alert_path = summary_path.parent / f"{domain}_smoke_alert.json"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)
            rerun_command = (
                "./scripts/run_improvement_domain_smoke.sh "
                "./configs/improvement_operator_knowledge_stack.json "
                f"{domain} --output-dir output/ci/domain_smoke/{domain} --allow-missing"
            )

            args = argparse.Namespace(
                domain=domain,
                smoke_status="warning",
                smoke_reason="domain_smoke_status_not_ok:warning",
                summary_path=summary_path,
                pull_report_path=str((summary_path.parent / "pull_report.json").resolve()),
                leaderboard_report_path=str((summary_path.parent / "leaderboard_report.json").resolve()),
                seed_report_path=str((summary_path.parent / "seed_report.json").resolve()),
                rerun_command=rerun_command,
                output_path=alert_path,
                emit_github_output=True,
                summary_heading="Domain Smoke Interrupt Alert",
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_domain_smoke_runtime_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("domain") or ""), domain)
            self.assertTrue(bool(payload.get("alert_created")))
            interrupt_id = str(payload.get("interrupt_id") or "").strip()
            self.assertTrue(bool(interrupt_id))
            acknowledge_command = str(payload.get("acknowledge_command") or "").strip()
            self.assertIn(interrupt_id, acknowledge_command)
            self.assertIn(str(db.resolve()), acknowledge_command)
            self.assertEqual(str(payload.get("rerun_command") or ""), rerun_command)
            self.assertEqual(str(payload.get("runtime_error_output") or ""), "none")
            self.assertEqual(str(payload.get("alert_path") or ""), str(alert_path.resolve()))
            self.assertTrue(alert_path.exists())

            alert_payload = json.loads(alert_path.read_text(encoding="utf-8"))
            self.assertEqual(str(alert_payload.get("domain") or ""), domain)
            self.assertEqual(str(alert_payload.get("smoke_status") or ""), "warning")
            self.assertTrue(bool(alert_payload.get("alert_created")))
            self.assertEqual(str(alert_payload.get("interrupt_id") or ""), interrupt_id)

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"alert_path={alert_path.resolve()}", output_lines)
            self.assertIn(f"interrupt_id={interrupt_id}", output_lines)
            self.assertIn("alert_created=1", output_lines)
            self.assertIn(f"acknowledge_command={acknowledge_command}", output_lines)
            self.assertIn(f"rerun_command={rerun_command}", output_lines)
            self.assertIn("runtime_error=none", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Domain Smoke Interrupt Alert", summary)
            self.assertIn(f"- domain: `{domain}`", summary)
            self.assertIn(f"- interrupt_id: `{interrupt_id}`", summary)
            self.assertIn("- alert_created: `1`", summary)
            self.assertIn("- smoke_status: `warning`", summary)
            self.assertIn("- smoke_reason: `domain_smoke_status_not_ok:warning`", summary)
            self.assertIn(f"- rerun_command: `{rerun_command}`", summary)
            self.assertIn(f"- acknowledge_command: `{acknowledge_command}`", summary)
            self.assertIn("- runtime_error: `none`", summary)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                interrupts = runtime.list_interrupts(status="all", limit=20)
                self.assertEqual(len(interrupts), 1)
                self.assertEqual(str((interrupts[0] or {}).get("status") or ""), "delivered")
                self.assertEqual(str((interrupts[0] or {}).get("interrupt_id") or ""), interrupt_id)
            finally:
                runtime.close()

    def test_domain_smoke_cross_domain_compact_emits_outputs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifacts_root = root / "output" / "ci" / "domain_smoke_artifacts"
            quant_dir = artifacts_root / "domain-smoke-quant_finance"
            fitness_dir = artifacts_root / "domain-smoke-fitness_apps"
            quant_dir.mkdir(parents=True, exist_ok=True)
            fitness_dir.mkdir(parents=True, exist_ok=True)

            (quant_dir / "quant_finance_smoke_summary.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "domain": "quant_finance",
                        "reason": "none",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            rerun_command = (
                "./scripts/run_improvement_domain_smoke.sh "
                "./configs/improvement_operator_knowledge_stack.json "
                "fitness_apps --output-dir output/ci/domain_smoke/fitness_apps --allow-missing"
            )
            acknowledge_command = (
                "python3 -m jarvis.cli interrupts acknowledge int_smoke_fitness "
                "--actor operator --db-path /tmp/fake_domain_smoke.db"
            )
            (fitness_dir / "fitness_apps_smoke_summary.json").write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "domain": "fitness_apps",
                        "reason": "domain_smoke_status_not_ok:warning",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (fitness_dir / "fitness_apps_smoke_alert.json").write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "domain": "fitness_apps",
                        "smoke_status": "warning",
                        "smoke_reason": "domain_smoke_status_not_ok:warning",
                        "alert_created": True,
                        "interrupt_id": "int_smoke_fitness",
                        "acknowledge_command": acknowledge_command,
                        "rerun_command": rerun_command,
                        "runtime_error": None,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            summary_path = root / "output" / "ci" / "domain_smoke" / "domain_smoke_cross_domain_summary.json"
            markdown_path = root / "output" / "ci" / "domain_smoke" / "domain_smoke_cross_domain_summary.md"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                artifacts_root=artifacts_root,
                output_path=summary_path,
                markdown_path=markdown_path,
                emit_github_output=True,
                summary_heading="Domain Smoke Cross-Domain Summary",
                json_compact=False,
            )
            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_domain_smoke_cross_domain_compact(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(int(payload.get("domain_count") or 0), 2)
            self.assertEqual(int(payload.get("warning_count") or 0), 1)
            self.assertEqual(int(payload.get("blocking_count") or 0), 1)
            self.assertEqual(int(payload.get("alerts_created_count") or 0), 1)
            self.assertEqual(str(payload.get("top_domain") or ""), "fitness_apps")
            self.assertEqual(str(payload.get("cross_domain_status") or ""), "warning")
            self.assertTrue(summary_path.exists())
            self.assertTrue(markdown_path.exists())

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"summary_path={summary_path.resolve()}", output_lines)
            self.assertIn(f"summary_markdown_path={markdown_path.resolve()}", output_lines)
            self.assertIn("cross_domain_status=warning", output_lines)
            self.assertIn("domain_count=2", output_lines)
            self.assertIn("warning_count=1", output_lines)
            self.assertIn("blocking_count=1", output_lines)
            self.assertIn("alerts_created_count=1", output_lines)
            self.assertIn("top_domain=fitness_apps", output_lines)
            self.assertIn("top_risk_score=95", output_lines)
            self.assertIn("acknowledge_command_count=1", output_lines)
            self.assertIn(f"first_acknowledge_command={acknowledge_command}", output_lines)
            self.assertIn("rerun_command_count=1", output_lines)
            self.assertIn(f"first_rerun_command={rerun_command}", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Domain Smoke Cross-Domain Summary", summary)
            self.assertIn("- status: `warning`", summary)
            self.assertIn("- domain_count: `2`", summary)
            self.assertIn("- warning_count: `1`", summary)
            self.assertIn("- blocking_count: `1`", summary)
            self.assertIn("- alerts_created_count: `1`", summary)
            self.assertIn("- top_domain: `fitness_apps`", summary)
            self.assertIn("- top_risk_score: `95`", summary)

    def test_domain_smoke_cross_domain_runtime_alert_creates_interrupt_updates_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)

            summary_path = root / "output" / "ci" / "domain_smoke" / "domain_smoke_cross_domain_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            rerun_command = (
                "./scripts/run_improvement_domain_smoke.sh "
                "./configs/improvement_operator_knowledge_stack.json "
                "fitness_apps --output-dir output/ci/domain_smoke/fitness_apps --allow-missing"
            )
            per_domain_ack = (
                "python3 -m jarvis.cli interrupts acknowledge int_smoke_fitness "
                "--actor operator --db-path /tmp/fake_domain_smoke.db"
            )
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "warning_count": 1,
                        "blocking_count": 1,
                        "top_domain": "fitness_apps",
                        "top_risk_score": 95,
                        "top_risks": [
                            {
                                "domain": "fitness_apps",
                                "smoke_reason": "domain_smoke_status_not_ok:warning",
                                "rerun_command": rerun_command,
                            }
                        ],
                        "domains": [
                            {
                                "domain": "fitness_apps",
                                "acknowledge_command": per_domain_ack,
                            }
                        ],
                        "suggested_actions": [f"[fitness_apps] rerun smoke loop: {rerun_command}"],
                        "operator_ack_bundle": {
                            "status": "ready",
                            "command_count": 1,
                            "commands": [per_domain_ack],
                            "command_sequence": per_domain_ack,
                            "first_command": per_domain_ack,
                            "per_domain_command_count": 1,
                            "cross_domain_command_count": 0,
                            "cross_domain_interrupt_id": None,
                            "cross_domain_acknowledge_command": None,
                        },
                        "acknowledge_bundle_commands": [per_domain_ack],
                        "acknowledge_bundle_command_sequence": per_domain_ack,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            alert_path = root / "output" / "ci" / "domain_smoke" / "domain_smoke_cross_domain_alert.json"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                summary_path=summary_path,
                warning_count=1,
                blocking_count=1,
                top_domain="fitness_apps",
                top_risk_score=95,
                output_path=alert_path,
                emit_github_output=True,
                summary_heading="Domain Smoke Cross-Domain Interrupt Alert",
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )
            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_domain_smoke_cross_domain_runtime_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(int(payload.get("cross_domain_alert_created") or 0), 1)
            interrupt_id = str(payload.get("cross_domain_interrupt_id") or "").strip()
            self.assertTrue(bool(interrupt_id))
            acknowledge_command = str(payload.get("cross_domain_acknowledge_command") or "").strip()
            self.assertIn(interrupt_id, acknowledge_command)
            self.assertIn(str(db.resolve()), acknowledge_command)
            self.assertEqual(str(payload.get("cross_domain_rerun_command") or ""), rerun_command)
            self.assertEqual(str(payload.get("cross_domain_runtime_error") or ""), "none")
            self.assertTrue(alert_path.exists())

            updated_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(str(updated_summary.get("cross_domain_interrupt_id") or ""), interrupt_id)
            self.assertEqual(str(updated_summary.get("cross_domain_alert_path") or ""), str(alert_path.resolve()))
            self.assertGreaterEqual(int(updated_summary.get("acknowledge_command_count") or 0), 2)
            self.assertGreaterEqual(int(updated_summary.get("rerun_command_count") or 0), 1)

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"cross_domain_alert_path={alert_path.resolve()}", output_lines)
            self.assertIn(f"cross_domain_interrupt_id={interrupt_id}", output_lines)
            self.assertIn("cross_domain_alert_created=1", output_lines)
            self.assertIn(f"cross_domain_acknowledge_command={acknowledge_command}", output_lines)
            self.assertIn(f"cross_domain_rerun_command={rerun_command}", output_lines)
            self.assertIn("cross_domain_runtime_error=none", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Domain Smoke Cross-Domain Interrupt Alert", summary)
            self.assertIn(f"- interrupt_id: `{interrupt_id}`", summary)
            self.assertIn("- alert_created: `1`", summary)
            self.assertIn("- warning_count: `1`", summary)
            self.assertIn("- blocking_count: `1`", summary)
            self.assertIn("- top_domain: `fitness_apps`", summary)
            self.assertIn("- top_risk_score: `95`", summary)
            self.assertIn(f"- rerun_command: `{rerun_command}`", summary)
            self.assertIn(f"- acknowledge_command: `{acknowledge_command}`", summary)
            self.assertIn("- runtime_error: `none`", summary)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                interrupts = runtime.list_interrupts(status="all", limit=20)
                self.assertEqual(len(interrupts), 1)
                self.assertEqual(str((interrupts[0] or {}).get("status") or ""), "delivered")
                self.assertEqual(str((interrupts[0] or {}).get("interrupt_id") or ""), interrupt_id)
            finally:
                runtime.close()

    def test_controlled_matrix_compact_emits_github_outputs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact_root = root / "artifacts"
            artifact_root.mkdir(parents=True, exist_ok=True)
            daily_report_path = artifact_root / "daily_pipeline_report.json"
            daily_report_path.write_text(
                json.dumps({"status": "warning"}, indent=2),
                encoding="utf-8",
            )
            verify_alert_path = artifact_root / "verify_matrix_alert_report.json"
            acknowledge_command = (
                "python3 -m jarvis.cli interrupts acknowledge "
                "int_matrix_alert_1 --actor operator"
            )
            rerun_command = (
                "./scripts/run_improvement_verify_matrix_alert.sh "
                "./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json "
                "output/ci/controlled_matrix/daily_pipeline_report.json "
                "--output-path output/ci/controlled_matrix/verify_matrix_alert_report.json "
                "--json-compact --alert-domain operations --alert-max-items 4"
            )
            verify_alert_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "drift_severity": "critical",
                        "severity_profile": {
                            "mismatch_count": 1,
                            "missing_count": 2,
                            "invalid_count": 0,
                            "guardrail_mismatch_count": 1,
                            "score": 6,
                        },
                        "alert_created": True,
                        "alert": {
                            "interrupt_id": "int_matrix_alert_1",
                            "top_scenarios": [
                                "market_ml_expected_promote",
                                "kalshi_weather_expected_iteration",
                            ],
                        },
                        "acknowledge_commands": [acknowledge_command],
                        "mitigation_actions": [
                            "Escalate immediately: freeze affected promotions until matrix drift is resolved.",
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            summary_path = artifact_root / "controlled_matrix_summary.json"
            summary_markdown_path = artifact_root / "controlled_matrix_summary.md"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                artifact_root=artifact_root,
                daily_report_path=daily_report_path,
                verify_alert_path=verify_alert_path,
                output_path=summary_path,
                markdown_path=summary_markdown_path,
                rerun_command=rerun_command,
                emit_github_output=True,
                summary_heading="Controlled Matrix Drift Summary",
                json_compact=False,
            )
            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_controlled_matrix_compact(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("drift_severity") or ""), "critical")
            self.assertEqual(str(payload.get("matrix_interrupt_id") or ""), "int_matrix_alert_1")
            self.assertEqual(str(payload.get("first_repair_command") or ""), acknowledge_command)
            self.assertEqual(str(payload.get("rerun_command") or ""), rerun_command)
            self.assertTrue(summary_path.exists())
            self.assertTrue(summary_markdown_path.exists())

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"matrix_summary_path={summary_path.resolve()}", output_lines)
            self.assertIn(f"matrix_summary_markdown_path={summary_markdown_path.resolve()}", output_lines)
            self.assertIn("matrix_status=warning", output_lines)
            self.assertIn("drift_severity=critical", output_lines)
            self.assertIn("drift_score=6", output_lines)
            self.assertIn("mismatch_count=1", output_lines)
            self.assertIn("missing_count=2", output_lines)
            self.assertIn("invalid_count=0", output_lines)
            self.assertIn("guardrail_mismatch_count=1", output_lines)
            self.assertIn("alert_created=1", output_lines)
            self.assertIn("matrix_interrupt_id=int_matrix_alert_1", output_lines)
            self.assertIn("acknowledge_command_count=1", output_lines)
            self.assertIn(f"first_acknowledge_command={acknowledge_command}", output_lines)
            self.assertIn("repair_command_count=2", output_lines)
            self.assertIn(f"first_repair_command={acknowledge_command}", output_lines)
            self.assertIn("mitigation_action_count=1", output_lines)
            self.assertIn("top_scenario_count=2", output_lines)
            self.assertIn("first_top_scenario=market_ml_expected_promote", output_lines)
            self.assertIn(f"rerun_command={rerun_command}", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Controlled Matrix Drift Summary", summary)
            self.assertIn("- status: `warning`", summary)
            self.assertIn("- drift_severity: `critical`", summary)
            self.assertIn("- drift_score: `6`", summary)
            self.assertIn("- mismatch_count: `1`", summary)
            self.assertIn("- missing_count: `2`", summary)
            self.assertIn("- guardrail_mismatch_count: `1`", summary)
            self.assertIn("- interrupt_id: `int_matrix_alert_1`", summary)
            self.assertIn("- acknowledge_command_count: `1`", summary)
            self.assertIn(f"- first_acknowledge_command: `{acknowledge_command}`", summary)
            self.assertIn("- repair_command_count: `2`", summary)
            self.assertIn(f"- first_repair_command: `{acknowledge_command}`", summary)
            self.assertIn(f"- rerun_command: `{rerun_command}`", summary)

    def test_controlled_matrix_runtime_alert_creates_interrupt_updates_summary_and_emits_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            artifact_root = root / "artifacts"
            artifact_root.mkdir(parents=True, exist_ok=True)

            rerun_command = (
                "python3 -m jarvis.cli improvement verify-matrix-alert "
                "--matrix-path matrix.json --report-path daily_pipeline_report.json"
            )
            summary_path = artifact_root / "controlled_matrix_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "warning",
                        "drift_severity": "critical",
                        "acknowledge_commands": [],
                        "repair_commands": [],
                        "suggested_actions": [],
                        "first_repair_command": rerun_command,
                        "first_suggested_action": f"rerun controlled matrix triage: {rerun_command}",
                        "rerun_command": rerun_command,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            alert_path = artifact_root / "controlled_matrix_runtime_alert.json"
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)
            args = argparse.Namespace(
                summary_path=summary_path,
                output_path=alert_path,
                daily_outcome="failure",
                matrix_status="warning",
                drift_severity="critical",
                first_repair_command=None,
                first_suggested_action=None,
                rerun_command=None,
                emit_github_output=True,
                summary_heading="Controlled Matrix Runtime Interrupt Alert",
                strict=False,
                json_compact=False,
                repo_path=repo,
                db_path=db,
            )

            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_controlled_matrix_runtime_alert(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(int(payload.get("matrix_runtime_alert_created") or 0), 1)
            interrupt_id = str(payload.get("matrix_runtime_interrupt_id") or "").strip()
            self.assertTrue(bool(interrupt_id))
            acknowledge_command = str(payload.get("matrix_runtime_acknowledge_command") or "").strip()
            self.assertIn(interrupt_id, acknowledge_command)
            self.assertIn(str(db.resolve()), acknowledge_command)
            self.assertEqual(str(payload.get("matrix_runtime_first_repair_command") or ""), rerun_command)
            self.assertEqual(str(payload.get("matrix_runtime_error") or ""), "none")
            self.assertTrue(alert_path.exists())

            updated_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(bool(updated_summary.get("runtime_alert_created")))
            self.assertEqual(str(updated_summary.get("runtime_interrupt_id") or ""), interrupt_id)
            self.assertEqual(str(updated_summary.get("runtime_first_repair_command") or ""), rerun_command)
            self.assertIn(acknowledge_command, list(updated_summary.get("acknowledge_commands") or []))
            self.assertGreaterEqual(int(updated_summary.get("repair_command_count") or 0), 1)
            self.assertGreaterEqual(int(updated_summary.get("suggested_action_count") or 0), 1)

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"matrix_runtime_alert_path={alert_path.resolve()}", output_lines)
            self.assertIn(f"matrix_runtime_interrupt_id={interrupt_id}", output_lines)
            self.assertIn("matrix_runtime_alert_created=1", output_lines)
            self.assertIn(f"matrix_runtime_acknowledge_command={acknowledge_command}", output_lines)
            self.assertIn(f"matrix_runtime_first_repair_command={rerun_command}", output_lines)
            self.assertIn("matrix_runtime_error=none", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Controlled Matrix Runtime Interrupt Alert", summary)
            self.assertIn(f"- interrupt_id: `{interrupt_id}`", summary)
            self.assertIn("- alert_created: `1`", summary)
            self.assertIn("- daily_outcome: `failure`", summary)
            self.assertIn("- matrix_status: `warning`", summary)
            self.assertIn("- drift_severity: `critical`", summary)
            self.assertIn(f"- acknowledge_command: `{acknowledge_command}`", summary)
            self.assertIn(f"- first_repair_command: `{rerun_command}`", summary)
            self.assertIn("- runtime_error: `none`", summary)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                interrupts = runtime.list_interrupts(status="all", limit=20)
                self.assertEqual(len(interrupts), 1)
                self.assertEqual(str((interrupts[0] or {}).get("status") or ""), "delivered")
                self.assertEqual(str((interrupts[0] or {}).get("interrupt_id") or ""), interrupt_id)
            finally:
                runtime.close()

    def test_knowledge_bootstrap_followup_rerun_executes_next_action_and_regenerates_post_route(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            route_artifact_path = root / "artifacts" / "knowledge_bootstrap_route.json"
            route_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            operator_report_path = root / "output" / "ci" / "operator_cycle" / "operator_cycle_report.json"
            post_route_artifact_path = root / "output" / "ci" / "knowledge_bootstrap_route_post_bootstrap.json"
            output_path = root / "output" / "ci" / "knowledge_bootstrap_followup_outputs.json"

            write_report_script = root / "scripts" / "write_operator_report.py"
            write_report_script.parent.mkdir(parents=True, exist_ok=True)
            write_report_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        "",
                        f"report_path = Path({str(operator_report_path)!r})",
                        "report_path.parent.mkdir(parents=True, exist_ok=True)",
                        "report_path.write_text(",
                        "    json.dumps(",
                        "        {",
                        "            'knowledge_bootstrap_state': {",
                        "                'phase': 'ready',",
                        "                'bootstrap_required': False,",
                        "                'next_action': 'Knowledge bootstrap ready; continue cadence.',",
                        "                'next_action_command': 'python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable --knowledge-delta-alert-enable',",
                        "            },",
                        "            'stage_statuses': {",
                        "                'knowledge_brief': 'ok',",
                        "                'knowledge_brief_delta_alert': 'ok',",
                        "            },",
                        "        },",
                        "        indent=2,",
                        "    ),",
                        "    encoding='utf-8',",
                        ")",
                    ]
                ),
                encoding="utf-8",
            )
            next_action_command = f'python3 "{write_report_script}"'
            route_artifact_path.write_text(
                json.dumps({"next_action_command": next_action_command}, indent=2),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                route_artifact_path=route_artifact_path,
                operator_report_path=operator_report_path,
                post_route_artifact_path=post_route_artifact_path,
                output_path=output_path,
                strict=False,
                json_compact=False,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_knowledge_bootstrap_followup_rerun(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("next_action_command") or ""), next_action_command)
            self.assertEqual(str(payload.get("post_status") or ""), "ok")
            self.assertEqual(str(payload.get("post_phase") or ""), "ready")
            self.assertEqual(str(payload.get("post_route") or ""), "run_cycle")
            self.assertEqual(str(payload.get("output_path") or ""), str(output_path.resolve()))
            self.assertTrue(output_path.exists())
            self.assertTrue(post_route_artifact_path.exists())

            post_payload = json.loads(post_route_artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(str(post_payload.get("status") or ""), "ok")
            self.assertEqual(str(post_payload.get("phase") or ""), "ready")
            self.assertEqual(str(post_payload.get("route") or ""), "run_cycle")

    def test_knowledge_bootstrap_followup_rerun_requires_next_action_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            route_artifact_path = root / "artifacts" / "knowledge_bootstrap_route.json"
            route_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            route_artifact_path.write_text(json.dumps({}, indent=2), encoding="utf-8")
            args = argparse.Namespace(
                route_artifact_path=route_artifact_path,
                operator_report_path=root / "output" / "ci" / "operator_cycle" / "operator_cycle_report.json",
                post_route_artifact_path=root / "output" / "ci" / "knowledge_bootstrap_route_post_bootstrap.json",
                output_path=None,
                strict=False,
                json_compact=False,
            )

            with self.assertRaises(SystemExit) as raised:
                cmd_improvement_knowledge_bootstrap_followup_rerun(args)
            self.assertEqual(str(raised.exception), "missing_bootstrap_next_action_command")

    def test_knowledge_bootstrap_followup_rerun_requires_operator_report_after_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            route_artifact_path = root / "artifacts" / "knowledge_bootstrap_route.json"
            route_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            route_artifact_path.write_text(
                json.dumps({"next_action_command": "true"}, indent=2),
                encoding="utf-8",
            )
            operator_report_path = root / "output" / "ci" / "operator_cycle" / "operator_cycle_report.json"
            args = argparse.Namespace(
                route_artifact_path=route_artifact_path,
                operator_report_path=operator_report_path,
                post_route_artifact_path=root / "output" / "ci" / "knowledge_bootstrap_route_post_bootstrap.json",
                output_path=None,
                strict=False,
                json_compact=False,
            )

            with self.assertRaises(SystemExit) as raised:
                cmd_improvement_knowledge_bootstrap_followup_rerun(args)
            self.assertEqual(
                str(raised.exception),
                f"missing_operator_report_after_bootstrap_followup:{operator_report_path.resolve()}",
            )

    def test_knowledge_bootstrap_followup_rerun_emits_github_outputs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            route_artifact_path = root / "artifacts" / "knowledge_bootstrap_route.json"
            route_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            operator_report_path = root / "output" / "ci" / "operator_cycle" / "operator_cycle_report.json"
            post_route_artifact_path = root / "output" / "ci" / "knowledge_bootstrap_route_post_bootstrap.json"

            write_report_script = root / "scripts" / "write_operator_report.py"
            write_report_script.parent.mkdir(parents=True, exist_ok=True)
            write_report_script.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        "",
                        f"report_path = Path({str(operator_report_path)!r})",
                        "report_path.parent.mkdir(parents=True, exist_ok=True)",
                        "report_path.write_text(",
                        "    json.dumps(",
                        "        {",
                        "            'knowledge_bootstrap_state': {",
                        "                'phase': 'ready',",
                        "                'bootstrap_required': False,",
                        "                'next_action': 'Knowledge bootstrap ready; continue cadence.',",
                        "                'next_action_command': 'python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable --knowledge-delta-alert-enable',",
                        "            },",
                        "            'stage_statuses': {",
                        "                'knowledge_brief': 'ok',",
                        "                'knowledge_brief_delta_alert': 'ok',",
                        "            },",
                        "        },",
                        "        indent=2,",
                        "    ),",
                        "    encoding='utf-8',",
                        ")",
                    ]
                ),
                encoding="utf-8",
            )
            next_action_command = f'python3 "{write_report_script}"'
            route_artifact_path.write_text(
                json.dumps({"next_action_command": next_action_command}, indent=2),
                encoding="utf-8",
            )

            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                route_artifact_path=route_artifact_path,
                operator_report_path=operator_report_path,
                post_route_artifact_path=post_route_artifact_path,
                emit_github_output=True,
                summary_heading="Bootstrap Follow-Up",
                output_path=None,
                strict=False,
                json_compact=False,
            )

            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_knowledge_bootstrap_followup_rerun(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("post_status") or ""), "ok")
            self.assertEqual(str(payload.get("post_phase") or ""), "ready")
            self.assertEqual(str(payload.get("post_route") or ""), "run_cycle")

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"bootstrap_followup_command={next_action_command}", output_lines)
            self.assertIn("bootstrap_followup_status=ok", output_lines)
            self.assertIn("bootstrap_followup_phase=ready", output_lines)
            self.assertIn("bootstrap_followup_route=run_cycle", output_lines)

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Bootstrap Follow-Up", summary)
            self.assertIn(f"- command: `{next_action_command}`", summary)
            self.assertIn("- post_status: `ok`", summary)
            self.assertIn("- post_phase: `ready`", summary)
            self.assertIn("- post_route: `run_cycle`", summary)

    def test_knowledge_bootstrap_route_outputs_normalizes_bootstrap_artifact_and_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            artifact_path = root / "artifacts" / "knowledge_bootstrap_route.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    {
                        "status": " warning ",
                        "phase": " bootstrap_pending ",
                        "route": " bootstrap ",
                        "next_action": " Capture one more snapshot. ",
                        "next_action_command": " python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable ",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            output_path = root / "artifacts" / "knowledge_bootstrap_route_outputs.json"
            args = argparse.Namespace(
                artifact_path=artifact_path,
                artifact_source=" route_initial ",
                output_path=output_path,
                strict=False,
                json_compact=False,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_knowledge_bootstrap_route_outputs(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("phase") or ""), "bootstrap_pending")
            self.assertEqual(str(payload.get("route") or ""), "bootstrap")
            self.assertEqual(int(payload.get("route_blocking") or 0), 0)
            self.assertEqual(str(payload.get("next_action") or ""), "Capture one more snapshot.")
            self.assertEqual(
                str(payload.get("next_action_command") or ""),
                "python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable",
            )
            self.assertEqual(str(payload.get("artifact_source") or ""), "route_initial")
            self.assertEqual(str(payload.get("output_path") or ""), str(output_path.resolve()))
            self.assertTrue(output_path.exists())

            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(str(stored.get("status") or ""), "warning")
            self.assertEqual(str(stored.get("route") or ""), "bootstrap")
            self.assertEqual(int(stored.get("route_blocking") or 0), 0)

    def test_knowledge_bootstrap_route_outputs_emits_github_outputs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            artifact_path = root / "artifacts" / "knowledge_bootstrap_route.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "phase": "ready",
                        "route": "run_cycle",
                        "next_action": "Continue cadence.",
                        "next_action_command": "python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            github_output_path = root / "ci" / "github_output.txt"
            github_step_summary_path = root / "ci" / "github_step_summary.md"
            github_output_path.parent.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                artifact_path=artifact_path,
                artifact_source="post_bootstrap",
                emit_github_output=True,
                summary_heading="Knowledge Bootstrap Route (Effective)",
                summary_include_artifact_source=True,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output_path),
                    "GITHUB_STEP_SUMMARY": str(github_step_summary_path),
                },
                clear=False,
            ):
                with redirect_stdout(out):
                    cmd_improvement_knowledge_bootstrap_route_outputs(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("phase") or ""), "ready")
            self.assertEqual(str(payload.get("route") or ""), "run_cycle")
            self.assertEqual(str(payload.get("artifact_source") or ""), "post_bootstrap")

            output_lines = github_output_path.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"artifact_path={artifact_path.resolve()}", output_lines)
            self.assertIn("artifact_source=post_bootstrap", output_lines)
            self.assertIn("status=ok", output_lines)
            self.assertIn("phase=ready", output_lines)
            self.assertIn("route=run_cycle", output_lines)
            self.assertIn("route_blocking=0", output_lines)
            self.assertIn("next_action=Continue cadence.", output_lines)
            self.assertIn(
                "next_action_command=python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable",
                output_lines,
            )

            summary = github_step_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Knowledge Bootstrap Route (Effective)", summary)
            self.assertIn("- artifact_source: `post_bootstrap`", summary)
            self.assertIn("- status: `ok`", summary)
            self.assertIn("- phase: `ready`", summary)
            self.assertIn("- route: `run_cycle`", summary)
            self.assertIn("- route_blocking: `0`", summary)
            self.assertIn("- next_action: `Continue cadence.`", summary)
            self.assertIn(
                "- next_action_command: `python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable`",
                summary,
            )

    def test_knowledge_bootstrap_route_outputs_missing_artifact_returns_fallback_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            artifact_path = root / "artifacts" / "missing_route.json"
            args = argparse.Namespace(
                artifact_path=artifact_path,
                artifact_source=None,
                output_path=None,
                strict=False,
                json_compact=False,
            )

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_knowledge_bootstrap_route_outputs(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("phase") or ""), "unknown")
            self.assertEqual(str(payload.get("route") or ""), "noop")
            self.assertEqual(int(payload.get("route_blocking") or 0), 1)
            self.assertEqual(
                str(payload.get("next_action") or ""),
                "knowledge bootstrap route artifact missing",
            )
            self.assertEqual(str(payload.get("next_action_command") or ""), "none")
            self.assertEqual(str(payload.get("artifact_path") or ""), str(artifact_path.resolve()))

    def test_knowledge_bootstrap_route_outputs_strict_raises_when_route_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            artifact_path = root / "artifacts" / "missing_route.json"
            args = argparse.Namespace(
                artifact_path=artifact_path,
                artifact_source=None,
                output_path=None,
                strict=True,
                json_compact=False,
            )

            out = io.StringIO()
            with self.assertRaises(SystemExit) as raised:
                with redirect_stdout(out):
                    cmd_improvement_knowledge_bootstrap_route_outputs(args)
            self.assertEqual(int(getattr(raised.exception, "code", 0) or 0), 2)

            payload = json.loads(out.getvalue())
            self.assertEqual(int(payload.get("route_blocking") or 0), 1)
            self.assertEqual(str(payload.get("route") or ""), "noop")

    def test_improvement_knowledge_bootstrap_route_reports_bootstrap_pending(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "knowledge_bootstrap_state": {
                            "phase": "bootstrap_pending",
                            "bootstrap_required": True,
                            "next_action_command": "python3 -m jarvis.cli improvement operator-cycle --knowledge-brief-enable --knowledge-delta-alert-enable",
                            "next_action": "Bootstrap in progress. Collect one more snapshot before delta alerting.",
                        },
                        "stage_statuses": {
                            "knowledge_brief": "ok",
                            "knowledge_brief_delta_alert": "skipped_bootstrap",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                report_path=report_path,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_knowledge_bootstrap_route(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("phase") or ""), "bootstrap_pending")
            self.assertEqual(str(payload.get("route") or ""), "bootstrap")
            self.assertTrue(bool(payload.get("bootstrap_required")))
            self.assertTrue(bool(str(payload.get("next_action_command") or "").strip()))

    def test_improvement_knowledge_bootstrap_route_reports_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "knowledge_bootstrap_state": {
                            "phase": "ready",
                            "bootstrap_required": False,
                            "next_action_command": "python3 -m jarvis.cli improvement operator-cycle --knowledge-delta-alert-enable",
                            "next_action": "Run monitoring and respond to regression alerts.",
                        },
                        "stage_statuses": {
                            "knowledge_brief": "ok",
                            "knowledge_brief_delta_alert": "ok",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                report_path=report_path,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_knowledge_bootstrap_route(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("phase") or ""), "ready")
            self.assertEqual(str(payload.get("route") or ""), "run_cycle")
            self.assertFalse(bool(payload.get("bootstrap_required")))

    def test_improvement_knowledge_bootstrap_route_infers_phase_from_stage_status_when_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "stage_statuses": {
                            "knowledge_brief_delta_alert": "skipped_not_requested",
                        }
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                report_path=report_path,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_knowledge_bootstrap_route(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("phase") or ""), "not_requested")
            self.assertEqual(str(payload.get("route") or ""), "noop")

    def test_improvement_knowledge_bootstrap_route_warns_when_state_and_stage_status_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            non_strict_args = argparse.Namespace(
                report_path=report_path,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            non_strict_out = io.StringIO()
            with redirect_stdout(non_strict_out):
                cmd_improvement_knowledge_bootstrap_route(non_strict_args)
            payload = json.loads(non_strict_out.getvalue())
            self.assertEqual(str(payload.get("status") or ""), "warning")

            strict_args = argparse.Namespace(
                report_path=report_path,
                output_path=None,
                strict=True,
                json_compact=False,
            )
            strict_out = io.StringIO()
            with self.assertRaises(SystemExit) as raised:
                with redirect_stdout(strict_out):
                    cmd_improvement_knowledge_bootstrap_route(strict_args)
            self.assertEqual(int(getattr(raised.exception, "code", 0) or 0), 2)

    def test_improvement_knowledge_bootstrap_route_defaults_domains_when_report_contains_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_repo(root)

            config_path = root / "configs" / "operator_config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({"defaults": {}}, indent=2), encoding="utf-8")

            report_path = root / "reports" / "operator_cycle_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "config_path": str(config_path),
                        "output_dir": str(root / "output" / "operator"),
                        "knowledge_bootstrap_state": {
                            "phase": "bootstrap_pending",
                            "bootstrap_required": True,
                        },
                        "knowledge_brief_delta_alert": {
                            "domains": "None",
                        },
                        "stage_statuses": {
                            "knowledge_brief_delta_alert": "skipped_bootstrap",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                report_path=report_path,
                output_path=None,
                strict=False,
                json_compact=False,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_knowledge_bootstrap_route(args)
            payload = json.loads(out.getvalue())

            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("route") or ""), "bootstrap")
            next_action_command = str(payload.get("next_action_command") or "")
            self.assertIn("--knowledge-delta-domains", next_action_command)
            self.assertNotIn("--knowledge-delta-domains none", next_action_command.lower())
            self.assertIn(
                "--knowledge-delta-domains quant_finance,kalshi_weather,fitness_apps,market_ml",
                next_action_command,
            )

    def test_improvement_knowledge_brief_summarizes_seeded_domain_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                fitness_friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before trying a workout trial.",
                    severity=5,
                    symptom_tags=["paywall", "trial", "pricing"],
                )
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="Paywall blocks first workout until subscription.",
                    severity=4,
                    symptom_tags=["paywall", "onboarding"],
                )
                runtime.record_domain_friction(
                    domain="quant_finance",
                    source="desk_notes",
                    summary="Execution slippage worsens during regime drift.",
                    severity=4,
                    symptom_tags=["slippage", "regime", "latency"],
                )
                runtime.record_domain_friction(
                    domain="kalshi_weather",
                    source="market_notes",
                    summary="Weather market calibration lags after forecast updates.",
                    severity=3,
                    symptom_tags=["calibration", "weather", "pricing"],
                )
                runtime.record_domain_friction(
                    domain="market_ml",
                    source="model_review",
                    summary="False positives drift higher in volatile windows.",
                    severity=4,
                    symptom_tags=["drift", "false_positive", "volatility"],
                )

                fitness_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Reduce paywall friction",
                    statement="Removing early paywall exposure should improve trial-to-retention conversion.",
                    proposed_change="Move the paywall after the first meaningful workout trial.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(fitness_friction.get("friction_id") or "")],
                )
                quant_hypothesis = runtime.register_hypothesis(
                    domain="quant_finance",
                    title="Cut regime slippage",
                    statement="Regime-aware execution should lower slippage in volatile sessions.",
                    proposed_change="Route orders with regime-aware execution controls.",
                    friction_key="execution_slippage_regime_drift",
                )
                kalshi_hypothesis = runtime.register_hypothesis(
                    domain="kalshi_weather",
                    title="Improve weather calibration",
                    statement="Calibrated probability bands should improve weather-market pricing.",
                    proposed_change="Add forecast ensemble calibration before order generation.",
                    friction_key="weather_probability_calibration_drift",
                )
                market_ml_hypothesis = runtime.register_hypothesis(
                    domain="market_ml",
                    title="Reduce false positives",
                    statement="Better volatility-aware calibration should suppress false positives.",
                    proposed_change="Tighten the model threshold under volatile conditions.",
                    friction_key="false_positive_drift_in_high_volatility_windows",
                )

                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(fitness_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.12,
                        "conversion_rate": 0.08,
                        "support_ticket_rate": 0.17,
                    },
                    candidate_metrics={
                        "retention_d30": 0.18,
                        "conversion_rate": 0.13,
                        "support_ticket_rate": 0.11,
                    },
                    sample_size=240,
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(quant_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "slippage_bps": 12.0,
                        "fill_rate": 0.91,
                        "execution_latency_ms_p95": 180.0,
                    },
                    candidate_metrics={
                        "slippage_bps": 15.0,
                        "fill_rate": 0.88,
                        "execution_latency_ms_p95": 190.0,
                    },
                    sample_size=180,
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(kalshi_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "brier_skill": 0.01,
                        "probability_calibration_error": 0.22,
                        "edge_capture": 0.07,
                    },
                    candidate_metrics={
                        "brier_skill": 0.04,
                        "probability_calibration_error": 0.14,
                        "edge_capture": 0.11,
                    },
                    sample_size=190,
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(market_ml_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "precision_at_k": 0.31,
                        "false_positive_rate": 0.18,
                        "inference_latency_ms_p95": 220.0,
                    },
                    candidate_metrics={
                        "precision_at_k": 0.36,
                        "false_positive_rate": 0.26,
                        "inference_latency_ms_p95": 228.0,
                    },
                    sample_size=260,
                )

                args = argparse.Namespace(
                    domains="fitness_apps,kalshi_weather,quant_finance,market_ml",
                    query="",
                    displeasure_limit=6,
                    hypothesis_limit=6,
                    experiment_limit=6,
                    controlled_test_limit=6,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                out = io.StringIO()
                with redirect_stdout(out):
                    cmd_improvement_knowledge_brief(args)
                payload = json.loads(out.getvalue())

                self.assertEqual(str(payload.get("status") or ""), "ok")

                domains = list(payload.get("domains") or [])
                domain_names = {
                    str(item.get("domain") or item.get("name") or item) if isinstance(item, dict) else str(item)
                    for item in domains
                }
                self.assertTrue({"fitness_apps", "kalshi_weather", "quant_finance", "market_ml"}.issubset(domain_names))

                domain_briefs = [dict(item) for item in list(payload.get("domain_briefs") or []) if isinstance(item, dict)]
                self.assertGreaterEqual(len(domain_briefs), 4)
                fitness_brief = next(
                    (row for row in domain_briefs if str(row.get("domain") or "") == "fitness_apps"),
                    {},
                )
                self.assertTrue(bool(fitness_brief))
                self.assertIn("paywall", json.dumps(fitness_brief, sort_keys=True).lower())

                priority_board = [dict(item) for item in list(payload.get("cross_domain_priority_board") or []) if isinstance(item, dict)]
                self.assertTrue(bool(priority_board))
                top_priority = priority_board[0]
                self.assertTrue(bool(str(top_priority.get("domain") or "").strip()))
                self.assertTrue(bool(str(top_priority.get("title") or top_priority.get("summary") or "").strip()))

                debug_hotspots = [dict(item) for item in list(payload.get("debug_hotspots") or []) if isinstance(item, dict)]
                self.assertTrue(bool(debug_hotspots))
                self.assertTrue(
                    any(
                        str(row.get("domain") or "") in {"fitness_apps", "quant_finance", "kalshi_weather", "market_ml"}
                        for row in debug_hotspots
                    )
                )

                controlled_test_candidates = [dict(item) for item in list(payload.get("controlled_test_candidates") or []) if isinstance(item, dict)]
                self.assertTrue(bool(controlled_test_candidates))
                self.assertTrue(
                    any(
                        str(row.get("hypothesis_id") or "").strip()
                        for row in controlled_test_candidates
                    )
                )
                self.assertTrue(
                    any(
                        "fitness_apps" == str(row.get("domain") or "")
                        or "paywall" in json.dumps(row, sort_keys=True).lower()
                        for row in controlled_test_candidates
                    )
                )
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_query_prioritizes_paywall_items(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before the first workout trial.",
                    severity=5,
                    symptom_tags=["paywall", "trial"],
                )
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="Paywall blocks workout plan setup.",
                    severity=4,
                    symptom_tags=["paywall", "pricing"],
                )
                runtime.record_domain_friction(
                    domain="quant_finance",
                    source="desk_notes",
                    summary="Execution latency spikes under volatility.",
                    severity=3,
                    symptom_tags=["latency", "volatility"],
                )
                runtime.record_domain_friction(
                    domain="market_ml",
                    source="model_review",
                    summary="False positives increase during noisy periods.",
                    severity=3,
                    symptom_tags=["false_positive", "noise"],
                )

                fitness_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Shift the paywall later",
                    statement="Delaying the paywall should improve workout trial completion.",
                    proposed_change="Expose the paywall after core utility is demonstrated.",
                    friction_key="paywall_before_core_workout_trial",
                )
                runtime.register_hypothesis(
                    domain="quant_finance",
                    title="Reduce slippage noise",
                    statement="Execution tuning should mitigate latency spikes.",
                    proposed_change="Add routing safeguards for volatile conditions.",
                    friction_key="execution_latency_spike",
                )
                runtime.register_hypothesis(
                    domain="market_ml",
                    title="Tame false positives",
                    statement="Volatility-aware thresholds should suppress false positives.",
                    proposed_change="Calibrate the classifier under noisy windows.",
                    friction_key="false_positive_drift",
                )

                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(fitness_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.10,
                        "conversion_rate": 0.07,
                    },
                    candidate_metrics={
                        "retention_d30": 0.16,
                        "conversion_rate": 0.12,
                    },
                    sample_size=120,
                )

                args = argparse.Namespace(
                    domains="fitness_apps,quant_finance,market_ml",
                    query="paywall",
                    displeasure_limit=4,
                    hypothesis_limit=4,
                    experiment_limit=4,
                    controlled_test_limit=4,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                out = io.StringIO()
                with redirect_stdout(out):
                    cmd_improvement_knowledge_brief(args)
                payload = json.loads(out.getvalue())

                self.assertEqual(str(payload.get("status") or ""), "ok")

                domain_briefs = [dict(item) for item in list(payload.get("domain_briefs") or []) if isinstance(item, dict)]
                self.assertTrue(bool(domain_briefs))
                top_domain_brief = domain_briefs[0]
                self.assertEqual(str(top_domain_brief.get("domain") or ""), "fitness_apps")
                self.assertIn("paywall", json.dumps(top_domain_brief, sort_keys=True).lower())

                priority_board = [dict(item) for item in list(payload.get("cross_domain_priority_board") or []) if isinstance(item, dict)]
                self.assertTrue(bool(priority_board))
                self.assertEqual(str(priority_board[0].get("domain") or ""), "fitness_apps")
                self.assertIn("paywall", json.dumps(priority_board[0], sort_keys=True).lower())

                debug_hotspots = [dict(item) for item in list(payload.get("debug_hotspots") or []) if isinstance(item, dict)]
                self.assertTrue(bool(debug_hotspots))
                self.assertEqual(str(debug_hotspots[0].get("domain") or ""), "fitness_apps")
                self.assertIn("paywall", json.dumps(debug_hotspots[0], sort_keys=True).lower())

                controlled_test_candidates = [dict(item) for item in list(payload.get("controlled_test_candidates") or []) if isinstance(item, dict)]
                self.assertTrue(bool(controlled_test_candidates))
                self.assertEqual(str(controlled_test_candidates[0].get("domain") or ""), "fitness_apps")
                self.assertIn("paywall", json.dumps(controlled_test_candidates[0], sort_keys=True).lower())
                self.assertTrue(
                    any(
                        str(row.get("hypothesis_id") or "") == str(fitness_hypothesis.get("hypothesis_id") or "")
                        for row in controlled_test_candidates
                    )
                )
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_writes_snapshot_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before trying workouts.",
                    severity=5,
                    symptom_tags=["paywall", "trial"],
                )
                hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay the paywall",
                    statement="Delaying the paywall should improve trial completion.",
                    proposed_change="Show the paywall after the first core workout.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.1,
                        "conversion_rate": 0.07,
                    },
                    candidate_metrics={
                        "retention_d30": 0.15,
                        "conversion_rate": 0.12,
                    },
                    sample_size=120,
                )

                args = argparse.Namespace(
                    domains="fitness_apps",
                    query="",
                    displeasure_limit=4,
                    hypothesis_limit=4,
                    experiment_limit=4,
                    controlled_test_limit=4,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                out = io.StringIO()
                with redirect_stdout(out):
                    cmd_improvement_knowledge_brief(args)
                payload = json.loads(out.getvalue())

                snapshot = dict(payload.get("knowledge_snapshot") or {})
                self.assertTrue(bool(snapshot))
                self.assertTrue(bool(snapshot.get("enabled")))

                snapshot_path = Path(str(snapshot.get("path") or "")).resolve()
                self.assertTrue(snapshot_path.exists())
                expected_dir = (repo / "analysis" / "improvement" / "knowledge_snapshots").resolve()
                self.assertEqual(snapshot_path.parent, expected_dir)

                snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
                self.assertIn("generated_at", snapshot_payload)
                self.assertIn("domains", snapshot_payload)
                self.assertIn("domain_briefs", snapshot_payload)
                self.assertEqual(str(snapshot_payload.get("status") or ""), str(payload.get("status") or ""))
                self.assertEqual(
                    list(snapshot_payload.get("domains") or []),
                    list(payload.get("domains") or []),
                )
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_can_disable_snapshot_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before trying workouts.",
                    severity=5,
                    symptom_tags=["paywall", "trial"],
                )
                runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay the paywall",
                    statement="Delaying the paywall should improve trial completion.",
                    proposed_change="Show the paywall after the first core workout.",
                    friction_key="paywall_before_core_workout_trial",
                )

                args = argparse.Namespace(
                    domains="fitness_apps",
                    query="",
                    displeasure_limit=4,
                    hypothesis_limit=4,
                    experiment_limit=4,
                    controlled_test_limit=4,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    write_snapshot=False,
                    repo_path=repo,
                    db_path=db,
                )
                out = io.StringIO()
                with redirect_stdout(out):
                    cmd_improvement_knowledge_brief(args)
                payload = json.loads(out.getvalue())

                snapshot = dict(payload.get("knowledge_snapshot") or {})
                self.assertTrue(bool(snapshot))
                self.assertFalse(bool(snapshot.get("enabled")))
                self.assertTrue(bool(snapshot.get("reason") or snapshot.get("disabled_reason") or snapshot.get("flag")))
                self.assertFalse(bool(snapshot.get("path")))
                self.assertFalse(
                    (repo / "analysis" / "improvement" / "knowledge_snapshots").exists()
                )
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_delta_returns_skipped_bootstrap_with_single_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before first workout completion.",
                    severity=4,
                    symptom_tags=["paywall", "trial"],
                )
                hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay paywall to post-workout",
                    statement="Delaying paywall should improve activation.",
                    proposed_change="Move paywall gating after first core workout.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.1,
                        "crash_rate": 0.006,
                        "unsubscribe_rate": 0.06,
                    },
                    candidate_metrics={
                        "retention_d30": 0.14,
                        "crash_rate": 0.007,
                        "unsubscribe_rate": 0.065,
                    },
                    sample_size=240,
                )

                brief_args = argparse.Namespace(
                    domains="fitness_apps",
                    query="",
                    displeasure_limit=6,
                    hypothesis_limit=6,
                    experiment_limit=6,
                    controlled_test_limit=6,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                brief_out = io.StringIO()
                with redirect_stdout(brief_out):
                    cmd_improvement_knowledge_brief(brief_args)
                brief_payload = json.loads(brief_out.getvalue())
                snapshot = dict(brief_payload.get("knowledge_snapshot") or {})
                snapshot_path = Path(str(snapshot.get("path") or "")).resolve()
                latest_path = Path(str(snapshot.get("latest_path") or "")).resolve()
                self.assertTrue(snapshot_path.exists())
                self.assertTrue(latest_path.exists())
                snapshot_path.unlink()
                self.assertFalse(snapshot_path.exists())
                self.assertTrue(latest_path.exists())

                delta_args = argparse.Namespace(
                    domains=None,
                    current_snapshot_path=None,
                    previous_snapshot_path=None,
                    snapshot_dir=None,
                    top_limit=10,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                delta_out = io.StringIO()
                with redirect_stdout(delta_out):
                    cmd_improvement_knowledge_brief_delta(delta_args)
                payload = json.loads(delta_out.getvalue())

                self.assertEqual(str(payload.get("status") or ""), "skipped_bootstrap")
                self.assertTrue(bool(payload.get("bootstrap_required")))
                self.assertEqual(list(payload.get("domain_deltas") or []), [])
                suggested_actions = [
                    str(item)
                    for item in list(payload.get("suggested_actions") or [])
                    if str(item or "").strip()
                ]
                self.assertTrue(bool(suggested_actions))
                self.assertTrue(
                    any(
                        "second snapshot" in action.lower()
                        or "bootstrap" in action.lower()
                        for action in suggested_actions
                    )
                )
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_delta_alert_returns_skipped_bootstrap_without_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before first workout completion.",
                    severity=4,
                    symptom_tags=["paywall", "trial"],
                )
                hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay paywall to post-workout",
                    statement="Delaying paywall should improve activation.",
                    proposed_change="Move paywall gating after first core workout.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.1,
                        "crash_rate": 0.006,
                        "unsubscribe_rate": 0.06,
                    },
                    candidate_metrics={
                        "retention_d30": 0.14,
                        "crash_rate": 0.007,
                        "unsubscribe_rate": 0.065,
                    },
                    sample_size=240,
                )

                brief_args = argparse.Namespace(
                    domains="fitness_apps",
                    query="",
                    displeasure_limit=6,
                    hypothesis_limit=6,
                    experiment_limit=6,
                    controlled_test_limit=6,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                brief_out = io.StringIO()
                with redirect_stdout(brief_out):
                    cmd_improvement_knowledge_brief(brief_args)
                brief_payload = json.loads(brief_out.getvalue())
                snapshot = dict(brief_payload.get("knowledge_snapshot") or {})
                snapshot_path = Path(str(snapshot.get("path") or "")).resolve()
                latest_path = Path(str(snapshot.get("latest_path") or "")).resolve()
                self.assertTrue(snapshot_path.exists())
                self.assertTrue(latest_path.exists())
                snapshot_path.unlink()
                self.assertFalse(snapshot_path.exists())
                self.assertTrue(latest_path.exists())

                delta_alert_args = argparse.Namespace(
                    domains=None,
                    current_snapshot_path=None,
                    previous_snapshot_path=None,
                    snapshot_dir=None,
                    top_limit=10,
                    alert_domain="operations",
                    alert_urgency=None,
                    alert_confidence=None,
                    alert_max_items=3,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                delta_alert_out = io.StringIO()
                with redirect_stdout(delta_alert_out):
                    cmd_improvement_knowledge_brief_delta_alert(delta_alert_args)
                payload = json.loads(delta_alert_out.getvalue())

                self.assertEqual(str(payload.get("status") or ""), "skipped_bootstrap")
                self.assertFalse(bool(payload.get("alert_created")))
                self.assertIsNone(payload.get("alert"))
                delta_payload = dict(payload.get("delta") or {})
                self.assertTrue(bool(delta_payload.get("bootstrap_required")))

                runtime_verify = JarvisRuntime(db_path=db, repo_path=repo)
                try:
                    interrupts = runtime_verify.list_interrupts(status="all", limit=20)
                    self.assertEqual(len(interrupts), 0)
                finally:
                    runtime_verify.close()
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_delta_compares_latest_vs_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                initial_friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before the first workout trial.",
                    severity=4,
                    symptom_tags=["paywall", "trial"],
                )
                initial_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay paywall until first value moment",
                    statement="Delaying paywall exposure should improve trial retention.",
                    proposed_change="Show paywall after the first meaningful workout completion.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(initial_friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(initial_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.11,
                        "crash_rate": 0.006,
                        "unsubscribe_rate": 0.06,
                    },
                    candidate_metrics={
                        "retention_d30": 0.16,
                        "crash_rate": 0.007,
                        "unsubscribe_rate": 0.065,
                    },
                    sample_size=260,
                )

                brief_args = argparse.Namespace(
                    domains="fitness_apps",
                    query="",
                    displeasure_limit=6,
                    hypothesis_limit=6,
                    experiment_limit=6,
                    controlled_test_limit=6,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                first_out = io.StringIO()
                with redirect_stdout(first_out):
                    cmd_improvement_knowledge_brief(brief_args)
                first_payload = json.loads(first_out.getvalue())
                first_snapshot = dict(first_payload.get("knowledge_snapshot") or {})
                first_snapshot_path = Path(str(first_snapshot.get("path") or "")).resolve()
                self.assertTrue(first_snapshot_path.exists())

                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="New onboarding path keeps the paywall in front of first workout.",
                    severity=5,
                    symptom_tags=["paywall", "onboarding", "blocking"],
                )
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="Workout start flow now triggers extra pricing friction before setup.",
                    severity=5,
                    symptom_tags=["paywall", "pricing", "friction"],
                )
                worsening_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Aggressive upsell gate on onboarding",
                    statement="An aggressive upsell gate may increase revenue at the cost of trust.",
                    proposed_change="Require subscription gate before plan customization.",
                    friction_key="paywall_before_core_workout_trial",
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(worsening_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.11,
                        "crash_rate": 0.009,
                        "unsubscribe_rate": 0.07,
                    },
                    candidate_metrics={
                        "retention_d30": 0.17,
                        "crash_rate": 0.03,
                        "unsubscribe_rate": 0.12,
                    },
                    sample_size=280,
                )

                second_out = io.StringIO()
                with redirect_stdout(second_out):
                    cmd_improvement_knowledge_brief(brief_args)
                second_payload = json.loads(second_out.getvalue())
                second_snapshot = dict(second_payload.get("knowledge_snapshot") or {})
                second_snapshot_path = Path(str(second_snapshot.get("path") or "")).resolve()
                self.assertTrue(second_snapshot_path.exists())

                delta_args = argparse.Namespace(
                    domains=None,
                    current_snapshot_path=None,
                    previous_snapshot_path=None,
                    snapshot_dir=None,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                delta_out = io.StringIO()
                with redirect_stdout(delta_out):
                    cmd_improvement_knowledge_brief_delta(delta_args)
                payload = json.loads(delta_out.getvalue())

                self.assertEqual(str(payload.get("status") or ""), "ok")

                resolved_current_raw = str(payload.get("current_snapshot_path") or "")
                resolved_previous_raw = str(payload.get("previous_snapshot_path") or "")
                self.assertTrue(bool(resolved_current_raw))
                self.assertTrue(bool(resolved_previous_raw))

                resolved_current_path = Path(resolved_current_raw).resolve()
                resolved_previous_path = Path(resolved_previous_raw).resolve()
                self.assertTrue(resolved_current_path.exists())
                self.assertTrue(resolved_previous_path.exists())
                self.assertEqual(resolved_current_path, second_snapshot_path)
                self.assertEqual(resolved_previous_path, first_snapshot_path)

                domain_deltas = [dict(item) for item in list(payload.get("domain_deltas") or []) if isinstance(item, dict)]
                self.assertTrue(bool(domain_deltas))
                fitness_delta = next((row for row in domain_deltas if str(row.get("domain") or "") == "fitness_apps"), {})
                self.assertTrue(bool(fitness_delta))

                worsening_score = float(fitness_delta.get("worsening_score") or 0.0)
                domain_friction_rows = [
                    dict(item)
                    for item in list(
                        fitness_delta.get("friction_acceleration_rows")
                        or fitness_delta.get("friction_accelerations")
                        or fitness_delta.get("friction_deltas")
                        or []
                    )
                    if isinstance(item, dict)
                ]
                global_friction_rows = [
                    dict(item)
                    for item in list(
                        payload.get("friction_acceleration_rows")
                        or payload.get("friction_accelerations")
                        or payload.get("friction_deltas")
                        or []
                    )
                    if isinstance(item, dict)
                ]
                fitness_friction_rows = [
                    row
                    for row in [*domain_friction_rows, *global_friction_rows]
                    if str(row.get("domain") or "") == "fitness_apps"
                ]
                self.assertTrue(
                    worsening_score > 0.0
                    or any(
                        float(
                            row.get("acceleration")
                            or row.get("impact_score_delta")
                            or row.get("signal_count_delta")
                            or row.get("worsening_score")
                            or 0.0
                        )
                        > 0.0
                        for row in fitness_friction_rows
                    )
                )
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_delta_supports_explicit_snapshot_paths_and_domain_filter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                fitness_friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Users see paywall before first workout trial.",
                    severity=4,
                    symptom_tags=["paywall", "trial"],
                )
                fitness_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay paywall to post-workout",
                    statement="Delaying paywall should improve activation.",
                    proposed_change="Move paywall gating after first core workout.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(fitness_friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(fitness_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.1,
                        "crash_rate": 0.006,
                        "unsubscribe_rate": 0.06,
                    },
                    candidate_metrics={
                        "retention_d30": 0.14,
                        "crash_rate": 0.007,
                        "unsubscribe_rate": 0.065,
                    },
                    sample_size=240,
                )

                quant_friction = runtime.record_domain_friction(
                    domain="quant_finance",
                    source="desk_notes",
                    summary="Execution slippage widens in volatility spikes.",
                    severity=3,
                    symptom_tags=["slippage", "volatility"],
                )
                quant_hypothesis = runtime.register_hypothesis(
                    domain="quant_finance",
                    title="Improve volatility routing",
                    statement="Volatility routing should reduce slippage.",
                    proposed_change="Route orders through regime-aware controls.",
                    friction_key="execution_slippage_regime_drift",
                    friction_ids=[str(quant_friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(quant_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "sharpe_ratio": 0.82,
                        "max_drawdown": 0.08,
                        "turnover": 3.2,
                    },
                    candidate_metrics={
                        "sharpe_ratio": 1.04,
                        "max_drawdown": 0.08,
                        "turnover": 3.5,
                    },
                    sample_size=80,
                )

                brief_args = argparse.Namespace(
                    domains="fitness_apps,quant_finance",
                    query="",
                    displeasure_limit=6,
                    hypothesis_limit=6,
                    experiment_limit=6,
                    controlled_test_limit=6,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )

                first_out = io.StringIO()
                with redirect_stdout(first_out):
                    cmd_improvement_knowledge_brief(brief_args)
                first_payload = json.loads(first_out.getvalue())
                first_snapshot = dict(first_payload.get("knowledge_snapshot") or {})
                first_snapshot_path = Path(str(first_snapshot.get("path") or "")).resolve()
                self.assertTrue(first_snapshot_path.exists())

                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="New flow increases onboarding paywall complaints and drop-off.",
                    severity=5,
                    symptom_tags=["paywall", "dropoff", "onboarding"],
                )
                worsening_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Introduce hard subscription gate",
                    statement="A hard gate may monetize quickly but raises risk.",
                    proposed_change="Require subscription before workout plan setup.",
                    friction_key="paywall_before_core_workout_trial",
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(worsening_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.1,
                        "crash_rate": 0.008,
                        "unsubscribe_rate": 0.07,
                    },
                    candidate_metrics={
                        "retention_d30": 0.15,
                        "crash_rate": 0.025,
                        "unsubscribe_rate": 0.1,
                    },
                    sample_size=260,
                )

                second_out = io.StringIO()
                with redirect_stdout(second_out):
                    cmd_improvement_knowledge_brief(brief_args)
                second_payload = json.loads(second_out.getvalue())
                second_snapshot = dict(second_payload.get("knowledge_snapshot") or {})
                second_snapshot_path = Path(str(second_snapshot.get("path") or "")).resolve()
                self.assertTrue(second_snapshot_path.exists())

                delta_args = argparse.Namespace(
                    domains="fitness_apps",
                    current_snapshot_path=second_snapshot_path,
                    previous_snapshot_path=first_snapshot_path,
                    snapshot_dir=None,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                delta_out = io.StringIO()
                with redirect_stdout(delta_out):
                    cmd_improvement_knowledge_brief_delta(delta_args)
                payload = json.loads(delta_out.getvalue())

                self.assertEqual(str(payload.get("status") or ""), "ok")
                self.assertEqual(
                    Path(str(payload.get("current_snapshot_path") or "")).resolve(),
                    second_snapshot_path,
                )
                self.assertEqual(
                    Path(str(payload.get("previous_snapshot_path") or "")).resolve(),
                    first_snapshot_path,
                )

                domain_deltas = [dict(item) for item in list(payload.get("domain_deltas") or []) if isinstance(item, dict)]
                self.assertTrue(bool(domain_deltas))
                filtered_domains = {
                    str(row.get("domain") or row.get("name") or row.get("domain_key") or "").strip()
                    for row in domain_deltas
                }
                self.assertEqual(filtered_domains, {"fitness_apps"})

                selection_sources = [
                    str(payload.get("current_snapshot_selection_source") or ""),
                    str(payload.get("previous_snapshot_selection_source") or ""),
                    str(payload.get("current_snapshot_path_source") or ""),
                    str(payload.get("previous_snapshot_path_source") or ""),
                    str(payload.get("snapshot_selection_source") or ""),
                ]
                snapshot_selection = payload.get("snapshot_selection")
                if isinstance(snapshot_selection, dict):
                    selection_sources.extend(
                        [
                            str(snapshot_selection.get("current_source") or ""),
                            str(snapshot_selection.get("previous_source") or ""),
                            str(snapshot_selection.get("source") or ""),
                        ]
                    )
                normalized_sources = [value.strip().lower() for value in selection_sources if value.strip()]
                self.assertTrue(bool(normalized_sources))
                self.assertTrue(
                    any("explicit" in source or "arg" in source or "cli" in source for source in normalized_sources)
                )
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_delta_alert_creates_delivered_interrupt_on_regression(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                initial_friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before first workout completion.",
                    severity=4,
                    symptom_tags=["paywall", "trial"],
                )
                initial_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay paywall until first value moment",
                    statement="Delaying paywall should improve trial retention.",
                    proposed_change="Show paywall after the first meaningful workout completion.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(initial_friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(initial_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.11,
                        "crash_rate": 0.006,
                        "unsubscribe_rate": 0.06,
                    },
                    candidate_metrics={
                        "retention_d30": 0.16,
                        "crash_rate": 0.007,
                        "unsubscribe_rate": 0.065,
                    },
                    sample_size=260,
                )

                brief_args = argparse.Namespace(
                    domains="fitness_apps",
                    query="",
                    displeasure_limit=6,
                    hypothesis_limit=6,
                    experiment_limit=6,
                    controlled_test_limit=6,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                first_out = io.StringIO()
                with redirect_stdout(first_out):
                    cmd_improvement_knowledge_brief(brief_args)
                first_payload = json.loads(first_out.getvalue())
                first_snapshot = dict(first_payload.get("knowledge_snapshot") or {})
                first_snapshot_path = Path(str(first_snapshot.get("path") or "")).resolve()
                self.assertTrue(first_snapshot_path.exists())

                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="New onboarding path keeps paywall in front of first workout.",
                    severity=5,
                    symptom_tags=["paywall", "onboarding", "blocking"],
                )
                runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="support_tickets",
                    summary="Workout start flow now triggers extra pricing friction before setup.",
                    severity=5,
                    symptom_tags=["paywall", "pricing", "friction"],
                )
                worsening_hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Aggressive upsell gate on onboarding",
                    statement="A hard upsell gate may increase revenue at the cost of trust.",
                    proposed_change="Require subscription gate before plan customization.",
                    friction_key="paywall_before_core_workout_trial",
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(worsening_hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.11,
                        "crash_rate": 0.009,
                        "unsubscribe_rate": 0.07,
                    },
                    candidate_metrics={
                        "retention_d30": 0.17,
                        "crash_rate": 0.03,
                        "unsubscribe_rate": 0.12,
                    },
                    sample_size=280,
                )

                second_out = io.StringIO()
                with redirect_stdout(second_out):
                    cmd_improvement_knowledge_brief(brief_args)
                second_payload = json.loads(second_out.getvalue())
                second_snapshot = dict(second_payload.get("knowledge_snapshot") or {})
                second_snapshot_path = Path(str(second_snapshot.get("path") or "")).resolve()
                self.assertTrue(second_snapshot_path.exists())

                delta_alert_args = argparse.Namespace(
                    domains="fitness_apps",
                    current_snapshot_path=None,
                    previous_snapshot_path=None,
                    snapshot_dir=None,
                    top_limit=10,
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
                delta_alert_out = io.StringIO()
                with redirect_stdout(delta_alert_out):
                    cmd_improvement_knowledge_brief_delta_alert(delta_alert_args)
                payload = json.loads(delta_alert_out.getvalue())

                status = str(payload.get("status") or "").strip().lower()
                self.assertTrue(bool(status))
                self.assertNotEqual(status, "ok")
                self.assertTrue(bool(payload.get("alert_created")))

                alert = dict(payload.get("alert") or {})
                alert_interrupt_id = str(alert.get("interrupt_id") or "")
                self.assertTrue(bool(alert_interrupt_id))
                self.assertEqual(str(alert.get("status") or ""), "delivered")

                acknowledge_command = str(alert.get("acknowledge_command") or "")
                acknowledge_commands = [str(item) for item in list(payload.get("acknowledge_commands") or [])]
                self.assertTrue(bool(acknowledge_command) or bool(acknowledge_commands))
                if acknowledge_command:
                    self.assertIn(alert_interrupt_id, acknowledge_command)
                if acknowledge_commands:
                    self.assertTrue(any(alert_interrupt_id in item for item in acknowledge_commands))

                runtime_verify = JarvisRuntime(db_path=db, repo_path=repo)
                try:
                    interrupts = runtime_verify.list_interrupts(status="all", limit=20)
                    self.assertEqual(len(interrupts), 1)
                    self.assertEqual(str((interrupts[0] or {}).get("status") or ""), "delivered")
                    self.assertEqual(
                        str((interrupts[0] or {}).get("interrupt_id") or ""),
                        alert_interrupt_id,
                    )
                finally:
                    runtime_verify.close()
            finally:
                runtime.close()

    def test_improvement_knowledge_brief_delta_alert_skips_interrupt_when_delta_stable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                friction = runtime.record_domain_friction(
                    domain="fitness_apps",
                    source="app_store_reviews",
                    summary="Paywall appears before first workout completion.",
                    severity=4,
                    symptom_tags=["paywall", "trial"],
                )
                hypothesis = runtime.register_hypothesis(
                    domain="fitness_apps",
                    title="Delay paywall to post-workout",
                    statement="Delaying paywall should improve activation.",
                    proposed_change="Move paywall gating after first core workout.",
                    friction_key="paywall_before_core_workout_trial",
                    friction_ids=[str(friction.get("friction_id") or "")],
                )
                runtime.run_hypothesis_experiment(
                    hypothesis_id=str(hypothesis.get("hypothesis_id") or ""),
                    environment="sandbox",
                    baseline_metrics={
                        "retention_d30": 0.1,
                        "crash_rate": 0.006,
                        "unsubscribe_rate": 0.06,
                    },
                    candidate_metrics={
                        "retention_d30": 0.14,
                        "crash_rate": 0.007,
                        "unsubscribe_rate": 0.065,
                    },
                    sample_size=240,
                )

                brief_args = argparse.Namespace(
                    domains="fitness_apps",
                    query="",
                    displeasure_limit=6,
                    hypothesis_limit=6,
                    experiment_limit=6,
                    controlled_test_limit=6,
                    min_cluster_count=1,
                    output_path=None,
                    strict=False,
                    json_compact=False,
                    repo_path=repo,
                    db_path=db,
                )
                first_out = io.StringIO()
                with redirect_stdout(first_out):
                    cmd_improvement_knowledge_brief(brief_args)
                first_payload = json.loads(first_out.getvalue())
                first_snapshot = dict(first_payload.get("knowledge_snapshot") or {})
                self.assertTrue(Path(str(first_snapshot.get("path") or "")).resolve().exists())

                second_out = io.StringIO()
                with redirect_stdout(second_out):
                    cmd_improvement_knowledge_brief(brief_args)
                second_payload = json.loads(second_out.getvalue())
                second_snapshot = dict(second_payload.get("knowledge_snapshot") or {})
                self.assertTrue(Path(str(second_snapshot.get("path") or "")).resolve().exists())

                delta_alert_args = argparse.Namespace(
                    domains=None,
                    current_snapshot_path=None,
                    previous_snapshot_path=None,
                    snapshot_dir=None,
                    top_limit=10,
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
                delta_alert_out = io.StringIO()
                with redirect_stdout(delta_alert_out):
                    cmd_improvement_knowledge_brief_delta_alert(delta_alert_args)
                payload = json.loads(delta_alert_out.getvalue())

                self.assertFalse(bool(payload.get("alert_created")))
                self.assertIsNone(payload.get("alert"))

                runtime_verify = JarvisRuntime(db_path=db, repo_path=repo)
                try:
                    interrupts = runtime_verify.list_interrupts(status="all", limit=20)
                    self.assertEqual(len(interrupts), 0)
                finally:
                    runtime_verify.close()
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
