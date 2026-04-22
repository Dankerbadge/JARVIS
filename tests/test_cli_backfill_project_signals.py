from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jarvis.cli import _resolve_project_backfill_options, cmd_plans_backfill_project_signals
from jarvis.providers.base import ProviderReviewArtifact, ReviewFeedbackSnapshot, ReviewStatusSnapshot
from jarvis.runtime import JarvisRuntime


class CliBackfillProjectSignalsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            "os.environ",
            {
                "JARVIS_BACKFILL_WARNING_POLICY_PROFILE": "",
                "JARVIS_BACKFILL_SUPPRESS_WARNING_CODES": "",
                "JARVIS_BACKFILL_MIN_WARNING_SEVERITY": "",
                "JARVIS_BACKFILL_EXIT_CODE_POLICY": "",
                "JARVIS_BACKFILL_WARNING_EXIT_CODE": "",
                "JARVIS_BACKFILL_ERROR_EXIT_CODE": "",
            },
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

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

    def _base_args(self, *, repo: Path, db: Path) -> argparse.Namespace:
        return argparse.Namespace(
            project_id="alpha",
            profile_key="nightly",
            actor="operator",
            preset="quick",
            execute=False,
            limit=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            summary_only=False,
            json_compact=False,
            output="json",
            color="auto",
            warning_policy_config=None,
            warning_policy_profile=None,
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
            since_updated_at=None,
            since_outcomes_at=None,
            since_review_artifacts_at=None,
            since_merge_outcomes_at=None,
            repo_path=repo,
            db_path=db,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
        )

    def test_resolve_project_backfill_options_preset_and_overrides(self) -> None:
        args = argparse.Namespace(
            preset="quick",
            limit=77,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=9,
            max_source_counts=2,
            max_signal_type_counts=3,
            include_raw_signals=True,
            include_raw_ingestions=None,
            warning_policy_profile="default",
            suppress_warning_code=[" source_counts_capped ", "SOURCE_COUNTS_CAPPED", ""],
            min_warning_severity="warning",
            exit_code_policy="warning",
            warning_exit_code=9,
            error_exit_code=11,
        )
        resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("preset")), "quick")
        self.assertEqual(int(resolved.get("limit") or 0), 77)
        self.assertTrue(bool(resolved.get("include_outcomes")))
        self.assertFalse(bool(resolved.get("include_review_artifacts")))
        self.assertFalse(bool(resolved.get("include_merge_outcomes")))
        self.assertEqual(int(resolved.get("top_signal_types") or 0), 9)
        self.assertEqual(int(resolved.get("max_source_counts") or 0), 2)
        self.assertEqual(int(resolved.get("max_signal_type_counts") or 0), 3)
        self.assertEqual(
            list(resolved.get("suppress_warning_codes") or []),
            ["source_counts_capped"],
        )
        self.assertEqual(str(resolved.get("min_warning_severity") or ""), "warning")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "warning")
        self.assertEqual(int(resolved.get("warning_exit_code") or 0), 9)
        self.assertEqual(int(resolved.get("error_exit_code") or 0), 11)
        self.assertTrue(bool(resolved.get("include_raw_signals")))
        self.assertFalse(bool(resolved.get("include_raw_ingestions")))

    def test_resolve_project_backfill_options_warning_policy_profile_strict_defaults(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_profile="strict",
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
        )
        resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("warning_policy_profile") or ""), "strict")
        resolution = dict(resolved.get("warning_policy_resolution") or {})
        profile_resolution = dict(resolution.get("profile") or {})
        self.assertEqual(str(profile_resolution.get("source") or ""), "explicit")
        self.assertEqual(str(resolved.get("min_warning_severity") or ""), "warning")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "warning")
        self.assertEqual(int(resolved.get("warning_exit_code") or 0), 2)
        self.assertEqual(int(resolved.get("error_exit_code") or 0), 3)
        self.assertEqual(list(resolved.get("suppress_warning_codes") or []), [])

    def test_resolve_project_backfill_options_warning_policy_profile_quiet_merge_and_override(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_profile="quiet",
            suppress_warning_code=[" candidate_scan_clipped "],
            min_warning_severity="error",
            exit_code_policy="error",
            warning_exit_code=9,
            error_exit_code=11,
        )
        resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("warning_policy_profile") or ""), "quiet")
        self.assertEqual(str(resolved.get("min_warning_severity") or ""), "error")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "error")
        self.assertEqual(int(resolved.get("warning_exit_code") or 0), 9)
        self.assertEqual(int(resolved.get("error_exit_code") or 0), 11)
        self.assertEqual(
            list(resolved.get("suppress_warning_codes") or []),
            [
                "candidate_scan_clipped",
                "signal_type_counts_capped",
                "source_counts_capped",
            ],
        )

    def test_resolve_project_backfill_options_warning_profile_from_env(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_profile=None,
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
        )
        with patch.dict(
            "os.environ",
            {"JARVIS_BACKFILL_WARNING_POLICY_PROFILE": "strict"},
            clear=False,
        ):
            resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("warning_policy_profile") or ""), "strict")
        resolution = dict(resolved.get("warning_policy_resolution") or {})
        profile_resolution = dict(resolution.get("profile") or {})
        self.assertEqual(str(profile_resolution.get("source") or ""), "env")
        self.assertEqual(str(resolved.get("min_warning_severity") or ""), "warning")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "warning")

    def test_resolve_project_backfill_options_suppress_warning_codes_from_env(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_profile="default",
            suppress_warning_code=[" signal_type_counts_capped ", "SOURCE_COUNTS_CAPPED"],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
        )
        with patch.dict(
            "os.environ",
            {"JARVIS_BACKFILL_SUPPRESS_WARNING_CODES": "candidate_scan_clipped, source_counts_capped"},
            clear=False,
        ):
            resolved = _resolve_project_backfill_options(args)
        self.assertEqual(
            list(resolved.get("suppress_warning_codes") or []),
            [
                "candidate_scan_clipped",
                "signal_type_counts_capped",
                "source_counts_capped",
            ],
        )

    def test_resolve_project_backfill_options_warning_exit_policy_from_env(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_profile="default",
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
        )
        with patch.dict(
            "os.environ",
            {
                "JARVIS_BACKFILL_MIN_WARNING_SEVERITY": "error",
                "JARVIS_BACKFILL_EXIT_CODE_POLICY": "error",
                "JARVIS_BACKFILL_WARNING_EXIT_CODE": "7",
                "JARVIS_BACKFILL_ERROR_EXIT_CODE": "11",
            },
            clear=False,
        ):
            resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("min_warning_severity") or ""), "error")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "error")
        self.assertEqual(int(resolved.get("warning_exit_code") or 0), 7)
        self.assertEqual(int(resolved.get("error_exit_code") or 0), 11)

    def test_resolve_project_backfill_options_invalid_env_values_emit_fallbacks(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_config=None,
            warning_policy_profile=None,
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
        )
        with patch.dict(
            "os.environ",
            {
                "JARVIS_BACKFILL_WARNING_POLICY_PROFILE": "not-a-profile",
                "JARVIS_BACKFILL_MIN_WARNING_SEVERITY": "extreme",
                "JARVIS_BACKFILL_EXIT_CODE_POLICY": "maybe",
                "JARVIS_BACKFILL_WARNING_EXIT_CODE": "abc",
                "JARVIS_BACKFILL_ERROR_EXIT_CODE": "def",
            },
            clear=False,
        ):
            resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("warning_policy_profile") or ""), "default")
        self.assertEqual(str(resolved.get("min_warning_severity") or ""), "info")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "off")
        self.assertEqual(int(resolved.get("warning_exit_code") or 0), 2)
        self.assertEqual(int(resolved.get("error_exit_code") or 0), 3)
        resolution = dict(resolved.get("warning_policy_resolution") or {})
        self.assertTrue(bool(resolution.get("has_fallbacks")))
        fallbacks = list(resolution.get("fallbacks") or [])
        fields = {str(item.get("field") or "") for item in fallbacks if isinstance(item, dict)}
        self.assertIn("warning_policy_profile", fields)
        self.assertIn("min_warning_severity", fields)
        self.assertIn("exit_code_policy", fields)
        self.assertIn("warning_exit_code", fields)
        self.assertIn("error_exit_code", fields)

    def test_resolve_project_backfill_options_warning_policy_from_config(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_config=None,
            warning_policy_profile=None,
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
        )
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "warning-policy.json"
            config_path.write_text(
                json.dumps(
                    {
                        "warning_policy_profile": "quiet",
                        "suppress_warning_codes": ["candidate_scan_clipped"],
                        "min_warning_severity": "error",
                        "exit_code_policy": "error",
                        "warning_exit_code": 7,
                        "error_exit_code": 11,
                    }
                ),
                encoding="utf-8",
            )
            args.warning_policy_config = config_path
            with patch.dict(
                "os.environ",
                {
                    "JARVIS_BACKFILL_WARNING_POLICY_PROFILE": "strict",
                    "JARVIS_BACKFILL_MIN_WARNING_SEVERITY": "warning",
                    "JARVIS_BACKFILL_EXIT_CODE_POLICY": "warning",
                    "JARVIS_BACKFILL_WARNING_EXIT_CODE": "4",
                    "JARVIS_BACKFILL_ERROR_EXIT_CODE": "5",
                },
                clear=False,
            ):
                resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("warning_policy_profile") or ""), "quiet")
        self.assertEqual(str(resolved.get("min_warning_severity") or ""), "error")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "error")
        self.assertEqual(int(resolved.get("warning_exit_code") or 0), 7)
        self.assertEqual(int(resolved.get("error_exit_code") or 0), 11)
        resolution = dict(resolved.get("warning_policy_resolution") or {})
        profile_resolution = dict(resolution.get("profile") or {})
        self.assertEqual(str(profile_resolution.get("source") or ""), "config")
        min_severity_resolution = dict(resolution.get("min_warning_severity") or {})
        self.assertEqual(str(min_severity_resolution.get("source") or ""), "config")
        self.assertTrue(bool(str(resolved.get("warning_policy_config_path") or "")))
        self.assertEqual(
            list(resolved.get("suppress_warning_codes") or []),
            [
                "candidate_scan_clipped",
                "signal_type_counts_capped",
                "source_counts_capped",
            ],
        )

    def test_resolve_project_backfill_options_warning_policy_from_repo_default_config(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_config=None,
            warning_policy_profile=None,
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
            repo_path=None,
        )
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".jarvis").mkdir(parents=True, exist_ok=True)
            config_path = repo / ".jarvis" / "backfill.warning_policy.json"
            config_path.write_text(
                json.dumps(
                    {
                        "warning_policy_profile": "strict",
                        "exit_code_policy": "warning",
                        "warning_exit_code": 6,
                    }
                ),
                encoding="utf-8",
            )
            args.repo_path = repo
            resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("warning_policy_profile") or ""), "strict")
        self.assertEqual(str(resolved.get("exit_code_policy") or ""), "warning")
        self.assertEqual(int(resolved.get("warning_exit_code") or 0), 6)
        self.assertEqual(str(resolved.get("warning_policy_config_source") or ""), "repo_default")
        resolution = dict(resolved.get("warning_policy_resolution") or {})
        profile_resolution = dict(resolution.get("profile") or {})
        self.assertEqual(str(profile_resolution.get("source") or ""), "config")
        self.assertTrue(bool(str(resolved.get("warning_policy_config_path") or "")))

    def test_resolve_project_backfill_options_explicit_config_overrides_repo_default(self) -> None:
        args = argparse.Namespace(
            preset="balanced",
            limit=None,
            include_outcomes=None,
            include_review_artifacts=None,
            include_merge_outcomes=None,
            skip_seen=None,
            load_since_from_cursor_profile=None,
            top_signal_types=None,
            max_source_counts=None,
            max_signal_type_counts=None,
            include_raw_signals=None,
            include_raw_ingestions=None,
            warning_policy_config=None,
            warning_policy_profile=None,
            suppress_warning_code=[],
            min_warning_severity=None,
            exit_code_policy=None,
            warning_exit_code=None,
            error_exit_code=None,
            repo_path=None,
        )
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".jarvis").mkdir(parents=True, exist_ok=True)
            repo_default = repo / ".jarvis" / "backfill.warning_policy.json"
            repo_default.write_text(
                json.dumps({"warning_policy_profile": "strict"}),
                encoding="utf-8",
            )
            explicit_path = repo / ".jarvis" / "warning-policy-explicit.json"
            explicit_path.write_text(
                json.dumps({"warning_policy_profile": "quiet"}),
                encoding="utf-8",
            )
            args.repo_path = repo
            args.warning_policy_config = explicit_path
            resolved = _resolve_project_backfill_options(args)
        self.assertEqual(str(resolved.get("warning_policy_profile") or ""), "quiet")
        self.assertEqual(str(resolved.get("warning_policy_config_source") or ""), "explicit")
        self.assertIn("warning-policy-explicit.json", str(resolved.get("warning_policy_config_path") or ""))

    def test_cmd_plans_backfill_project_signals_defaults_to_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                old_at = "2026-04-18T08:00:00+00:00"
                new_at = "2026-04-18T08:15:00+00:00"
                seed_since = "2026-04-18T08:05:00+00:00"
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli old",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli new",
                    recorded_at=new_at,
                )
                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": seed_since}},
                    actor="test",
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            self.assertFalse(bool(payload.get("execute")))
            result = dict(payload.get("result") or {})
            self.assertTrue(bool(result.get("dry_run")))
            self.assertFalse(bool(result.get("cursor_persisted")))
            backfill = dict(result.get("backfill") or {})
            self.assertEqual(int(backfill.get("signals_count") or 0), 1)
            self.assertTrue(bool(backfill.get("signals_omitted")))
            self.assertTrue(bool(backfill.get("ingestions_omitted")))
            summary = dict(result.get("summary") or {})
            self.assertEqual(int(summary.get("signals_count") or 0), 1)

    def test_cmd_plans_backfill_project_signals_execute_with_raw_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                old_at = "2026-04-18T08:40:00+00:00"
                new_at = "2026-04-18T08:55:00+00:00"
                seed_since = "2026-04-18T08:50:00+00:00"
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-exec-old",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli exec old",
                    recorded_at=old_at,
                )
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-exec-new",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli exec new",
                    recorded_at=new_at,
                )
                runtime.save_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                    source_cursors={"plan_outcomes": {"next_since": seed_since}},
                    actor="test",
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.execute = True
            args.include_raw_signals = True
            args.include_raw_ingestions = True
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            self.assertTrue(bool(payload.get("execute")))
            result = dict(payload.get("result") or {})
            self.assertFalse(bool(result.get("dry_run")))
            self.assertTrue(bool(result.get("cursor_persisted")))
            backfill = dict(result.get("backfill") or {})
            self.assertEqual(len(list(backfill.get("signals") or [])), 1)
            self.assertEqual(len(list(backfill.get("ingestions") or [])), 1)
            self.assertFalse(bool(backfill.get("signals_omitted")))
            self.assertFalse(bool(backfill.get("ingestions_omitted")))

            verify_runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                profile = verify_runtime.get_project_backfill_cursor_profile(
                    project_id="alpha",
                    profile_key="nightly",
                )
                source_cursors = dict(profile.get("source_cursors") or {})
                self.assertEqual(
                    str((source_cursors.get("plan_outcomes") or {}).get("next_since") or ""),
                    "2026-04-18T08:55:00+00:00",
                )
            finally:
                verify_runtime.close()

    def test_cmd_plans_backfill_project_signals_summary_only_compact_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-summary-only",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli summary only",
                    recorded_at="2026-04-18T09:20:00+00:00",
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.summary_only = True
            args.json_compact = True
            args.include_raw_signals = True
            args.include_raw_ingestions = True

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            raw = out.getvalue().strip()
            self.assertNotIn("\n", raw)
            payload = json.loads(raw)

            self.assertEqual(str(payload.get("project_id") or ""), "alpha")
            self.assertEqual(str(payload.get("preset") or ""), "quick")
            result = dict(payload.get("result") or {})
            self.assertIn("summary", result)
            self.assertIn("dry_run", result)
            self.assertIn("cursor_persisted", result)
            self.assertNotIn("backfill", result)

    def test_cmd_plans_backfill_project_signals_emits_operator_hints(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-hints-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli hints outcome",
                    recorded_at="2026-04-18T09:40:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-299",
                    number="299",
                    title="Hints CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/299",
                    api_url="https://api.example.test/pr/299",
                    base_branch="main",
                    head_branch="feature/hints-ci",
                    head_sha="deadbeef299",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef299",
                        web_url="https://example.test/pr/299",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-hints-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-hints-1",
                    plan_id="plan-cli-hints-review",
                    step_id="step-cli-hints-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="299",
                    branch="feature/hints-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cli-hints-2",
                    plan_id="plan-cli-hints-review",
                    step_id="step-cli-hints-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="299",
                    branch="feature/hints-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.include_merge_outcomes = True
            args.max_source_counts = 2
            args.max_signal_type_counts = 2
            args.summary_only = True
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            hints = list(payload.get("operator_hints") or [])
            self.assertGreaterEqual(len(hints), 3)
            self.assertEqual(int(payload.get("operator_hints_count") or 0), len(hints))
            codes = {str(item.get("code") or "") for item in hints}
            self.assertIn("candidate_scan_clipped", codes)
            self.assertIn("source_counts_capped", codes)
            self.assertIn("signal_type_counts_capped", codes)

            clipped = next(item for item in hints if str(item.get("code") or "") == "candidate_scan_clipped")
            details = dict(clipped.get("details") or {})
            unscanned_by_source = dict(details.get("unscanned_by_source") or {})
            self.assertEqual(int(unscanned_by_source.get("review_artifacts") or 0), 1)
            recommendations = list(clipped.get("recommended_actions") or [])
            self.assertTrue(any("--preset balanced" in str(item) for item in recommendations))

    def test_cmd_plans_backfill_project_signals_pretty_output_no_color(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-pretty",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli pretty",
                    recorded_at="2026-04-18T10:00:00+00:00",
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.summary_only = True
            args.output = "pretty"
            args.color = "never"
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            raw = out.getvalue()
            self.assertIn("JARVIS Backfill Summary", raw)
            self.assertIn("project=alpha", raw)
            self.assertIn("warning_profile=default", raw)
            self.assertIn("warning_policy_checksum=", raw)
            self.assertNotIn("\x1b[", raw)
            self.assertFalse(raw.lstrip().startswith("{"))

    def test_cmd_plans_backfill_project_signals_pretty_output_color_always(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-pretty-color",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli pretty color",
                    recorded_at="2026-04-18T10:20:00+00:00",
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.summary_only = True
            args.output = "pretty"
            args.color = "always"
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            raw = out.getvalue()
            self.assertIn("\x1b[", raw)

    def test_cmd_plans_backfill_project_signals_warnings_output_mode_clean(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-warnings-clean",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli warnings clean",
                    recorded_at="2026-04-18T10:40:00+00:00",
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.summary_only = True
            args.json_compact = True
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            raw = out.getvalue().strip()
            self.assertNotIn("\n", raw)
            payload = json.loads(raw)
            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("warning_policy_profile") or ""), "default")
            checksum = str(payload.get("warning_policy_checksum") or "")
            self.assertEqual(len(checksum), 64)
            self.assertFalse(bool(payload.get("has_warnings")))
            self.assertEqual(int(payload.get("warning_count") or 0), 0)

    def test_cmd_plans_backfill_project_signals_policy_output_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-policy-output",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli policy output",
                    recorded_at="2026-04-18T10:45:00+00:00",
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "policy"
            args.summary_only = True
            args.json_compact = True
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            raw = out.getvalue().strip()
            self.assertNotIn("\n", raw)
            payload = json.loads(raw)
            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertEqual(str(payload.get("project_id") or ""), "alpha")
            self.assertEqual(str(payload.get("warning_policy_profile") or ""), "default")
            checksum = str(payload.get("warning_policy_checksum") or "")
            self.assertEqual(len(checksum), 64)
            self.assertIn("warning_policy_resolution", payload)
            self.assertIn("signal_summary", payload)
            self.assertEqual(int(payload.get("warning_count") or 0), 0)

    def test_cmd_plans_backfill_project_signals_warnings_output_mode_with_hints(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-warnings-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli warnings outcome",
                    recorded_at="2026-04-18T10:55:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-399",
                    number="399",
                    title="Warnings CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/399",
                    api_url="https://api.example.test/pr/399",
                    base_branch="main",
                    head_branch="feature/warnings-ci",
                    head_sha="deadbeef399",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef399",
                        web_url="https://example.test/pr/399",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-warnings-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-warnings-1",
                    plan_id="plan-cli-warnings-review",
                    step_id="step-cli-warnings-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="399",
                    branch="feature/warnings-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cli-warnings-2",
                    plan_id="plan-cli-warnings-review",
                    step_id="step-cli-warnings-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="399",
                    branch="feature/warnings-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.include_merge_outcomes = True
            args.max_source_counts = 2
            args.max_signal_type_counts = 2
            args.summary_only = True
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("warning_policy_profile") or ""), "default")
            resolution = dict(payload.get("warning_policy_resolution") or {})
            profile_resolution = dict(resolution.get("profile") or {})
            self.assertEqual(str(profile_resolution.get("source") or ""), "profile_default")
            self.assertTrue(bool(payload.get("has_warnings")))
            self.assertGreaterEqual(int(payload.get("warning_count") or 0), 3)
            codes = list(payload.get("warning_codes") or [])
            self.assertIn("candidate_scan_clipped", codes)
            self.assertIn("source_counts_capped", codes)
            self.assertIn("signal_type_counts_capped", codes)
            signal_summary = dict(payload.get("signal_summary") or {})
            self.assertEqual(int(signal_summary.get("candidate_unscanned_count") or 0), 1)

    def test_cmd_plans_backfill_project_signals_warning_profile_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-warning-profile-quiet-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli warning profile quiet outcome",
                    recorded_at="2026-04-18T11:05:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-429",
                    number="429",
                    title="Warning Profile Quiet",
                    body_markdown="Body",
                    web_url="https://example.test/pr/429",
                    api_url="https://api.example.test/pr/429",
                    base_branch="main",
                    head_branch="feature/warning-profile-quiet",
                    head_sha="deadbeef429",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef429",
                        web_url="https://example.test/pr/429",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-warning-profile-quiet-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-warning-profile-quiet-1",
                    plan_id="plan-cli-warning-profile-quiet-review",
                    step_id="step-cli-warning-profile-quiet-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="429",
                    branch="feature/warning-profile-quiet",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cli-warning-profile-quiet-2",
                    plan_id="plan-cli-warning-profile-quiet-review",
                    step_id="step-cli-warning-profile-quiet-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="429",
                    branch="feature/warning-profile-quiet",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.include_merge_outcomes = True
            args.max_source_counts = 2
            args.max_signal_type_counts = 2
            args.summary_only = True
            args.warning_policy_profile = "quiet"
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertEqual(str(payload.get("warning_policy_profile") or ""), "quiet")
            resolution = dict(payload.get("warning_policy_resolution") or {})
            profile_resolution = dict(resolution.get("profile") or {})
            self.assertEqual(str(profile_resolution.get("source") or ""), "explicit")
            self.assertEqual(int(payload.get("exit_code") or 0), 0)
            codes = list(payload.get("warning_codes") or [])
            self.assertIn("candidate_scan_clipped", codes)
            self.assertNotIn("source_counts_capped", codes)
            self.assertNotIn("signal_type_counts_capped", codes)
            suppression = dict(payload.get("warning_suppression") or {})
            self.assertEqual(
                list(suppression.get("requested_codes") or []),
                ["signal_type_counts_capped", "source_counts_capped"],
            )
            self.assertEqual(
                list(suppression.get("applied_codes") or []),
                ["signal_type_counts_capped", "source_counts_capped"],
            )

    def test_cmd_plans_backfill_project_signals_warnings_suppression_by_code(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-warnings-suppress-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli warnings suppress outcome",
                    recorded_at="2026-04-18T11:10:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-499",
                    number="499",
                    title="Warnings Suppress CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/499",
                    api_url="https://api.example.test/pr/499",
                    base_branch="main",
                    head_branch="feature/warnings-suppress-ci",
                    head_sha="deadbeef499",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef499",
                        web_url="https://example.test/pr/499",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-warnings-suppress-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-warnings-suppress-1",
                    plan_id="plan-cli-warnings-suppress-review",
                    step_id="step-cli-warnings-suppress-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="499",
                    branch="feature/warnings-suppress-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cli-warnings-suppress-2",
                    plan_id="plan-cli-warnings-suppress-review",
                    step_id="step-cli-warnings-suppress-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="499",
                    branch="feature/warnings-suppress-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.include_merge_outcomes = True
            args.max_source_counts = 2
            args.max_signal_type_counts = 2
            args.summary_only = True
            args.suppress_warning_code = [
                "source_counts_capped",
                "signal_type_counts_capped",
            ]
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("status") or ""), "warning")
            self.assertTrue(bool(payload.get("has_warnings")))
            codes = list(payload.get("warning_codes") or [])
            self.assertIn("candidate_scan_clipped", codes)
            self.assertNotIn("source_counts_capped", codes)
            self.assertNotIn("signal_type_counts_capped", codes)
            suppression = dict(payload.get("warning_suppression") or {})
            self.assertEqual(
                list(suppression.get("requested_codes") or []),
                ["signal_type_counts_capped", "source_counts_capped"],
            )
            self.assertEqual(
                list(suppression.get("applied_codes") or []),
                ["signal_type_counts_capped", "source_counts_capped"],
            )
            self.assertEqual(int(suppression.get("suppressed_count") or 0), 2)

            args_all = self._base_args(repo=repo, db=db)
            args_all.output = "warnings"
            args_all.preset = "quick"
            args_all.limit = 1
            args_all.include_review_artifacts = True
            args_all.include_merge_outcomes = True
            args_all.max_source_counts = 2
            args_all.max_signal_type_counts = 2
            args_all.summary_only = True
            args_all.suppress_warning_code = [
                "candidate_scan_clipped",
                "source_counts_capped",
                "signal_type_counts_capped",
            ]
            out_all = io.StringIO()
            with redirect_stdout(out_all):
                cmd_plans_backfill_project_signals(args_all)
            payload_all = json.loads(out_all.getvalue())
            self.assertEqual(str(payload_all.get("status") or ""), "ok")
            self.assertFalse(bool(payload_all.get("has_warnings")))
            self.assertEqual(int(payload_all.get("warning_count") or 0), 0)

    def test_cmd_plans_backfill_project_signals_warnings_suppression_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-warnings-env-suppress-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli warnings env suppress outcome",
                    recorded_at="2026-04-18T11:20:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-529",
                    number="529",
                    title="Warnings Env Suppress CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/529",
                    api_url="https://api.example.test/pr/529",
                    base_branch="main",
                    head_branch="feature/warnings-env-suppress-ci",
                    head_sha="deadbeef529",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef529",
                        web_url="https://example.test/pr/529",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-warnings-env-suppress-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-warnings-env-suppress-1",
                    plan_id="plan-cli-warnings-env-suppress-review",
                    step_id="step-cli-warnings-env-suppress-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="529",
                    branch="feature/warnings-env-suppress-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cli-warnings-env-suppress-2",
                    plan_id="plan-cli-warnings-env-suppress-review",
                    step_id="step-cli-warnings-env-suppress-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="529",
                    branch="feature/warnings-env-suppress-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.include_merge_outcomes = True
            args.max_source_counts = 2
            args.max_signal_type_counts = 2
            args.summary_only = True
            with patch.dict(
                "os.environ",
                {
                    "JARVIS_BACKFILL_SUPPRESS_WARNING_CODES": (
                        "candidate_scan_clipped,source_counts_capped,signal_type_counts_capped"
                    )
                },
                clear=False,
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertFalse(bool(payload.get("has_warnings")))
            self.assertEqual(int(payload.get("warning_count") or 0), 0)
            suppression = dict(payload.get("warning_suppression") or {})
            self.assertEqual(
                list(suppression.get("requested_codes") or []),
                [
                    "candidate_scan_clipped",
                    "signal_type_counts_capped",
                    "source_counts_capped",
                ],
            )

    def test_cmd_plans_backfill_project_signals_warning_severity_filter_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-warning-severity-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli warning severity outcome",
                    recorded_at="2026-04-18T11:30:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-599",
                    number="599",
                    title="Warning Severity CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/599",
                    api_url="https://api.example.test/pr/599",
                    base_branch="main",
                    head_branch="feature/warning-severity-ci",
                    head_sha="deadbeef599",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef599",
                        web_url="https://example.test/pr/599",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-warning-severity-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-warning-severity-1",
                    plan_id="plan-cli-warning-severity-review",
                    step_id="step-cli-warning-severity-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="599",
                    branch="feature/warning-severity-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cli-warning-severity-2",
                    plan_id="plan-cli-warning-severity-review",
                    step_id="step-cli-warning-severity-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="599",
                    branch="feature/warning-severity-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.include_merge_outcomes = True
            args.max_source_counts = 2
            args.max_signal_type_counts = 2
            args.summary_only = True
            args.min_warning_severity = "error"
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_plans_backfill_project_signals(args)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertFalse(bool(payload.get("has_warnings")))
            self.assertEqual(int(payload.get("warning_count") or 0), 0)
            severity_filter = dict(payload.get("warning_severity_filter") or {})
            self.assertEqual(str(severity_filter.get("requested_min_severity") or ""), "error")
            self.assertGreaterEqual(int(severity_filter.get("filtered_out_count") or 0), 1)

    def test_cmd_plans_backfill_project_signals_warning_exit_code_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-exit-policy-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli exit policy outcome",
                    recorded_at="2026-04-18T11:50:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-699",
                    number="699",
                    title="Exit Policy CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/699",
                    api_url="https://api.example.test/pr/699",
                    base_branch="main",
                    head_branch="feature/exit-policy-ci",
                    head_sha="deadbeef699",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef699",
                        web_url="https://example.test/pr/699",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-exit-policy-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-exit-policy-1",
                    plan_id="plan-cli-exit-policy-review",
                    step_id="step-cli-exit-policy-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="699",
                    branch="feature/exit-policy-ci",
                    artifact=artifact.to_dict(),
                )
                runtime.security.store_merge_outcome(
                    approval_id="apr-cli-exit-policy-2",
                    plan_id="plan-cli-exit-policy-review",
                    step_id="step-cli-exit-policy-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="699",
                    branch="feature/exit-policy-ci",
                    merge_outcome="blocked",
                    review_decision="changes_requested",
                    outcome={"merge_outcome": "blocked", "review_decision": "changes_requested"},
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.include_merge_outcomes = True
            args.summary_only = True
            args.exit_code_policy = "warning"
            args.warning_exit_code = 9
            args.error_exit_code = 11
            out = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                with redirect_stdout(out):
                    cmd_plans_backfill_project_signals(args)
            self.assertEqual(int(cm.exception.code), 9)
            payload = json.loads(out.getvalue())
            self.assertEqual(int(payload.get("exit_code") or 0), 9)
            self.assertTrue(bool(payload.get("exit_triggered")))
            self.assertEqual(str(payload.get("max_warning_severity") or ""), "warning")
            self.assertEqual(str(payload.get("status") or ""), "warning")

            args_error = self._base_args(repo=repo, db=db)
            args_error.output = "warnings"
            args_error.preset = "quick"
            args_error.limit = 1
            args_error.include_review_artifacts = True
            args_error.include_merge_outcomes = True
            args_error.summary_only = True
            args_error.exit_code_policy = "error"
            args_error.warning_exit_code = 9
            args_error.error_exit_code = 11
            out_error = io.StringIO()
            with redirect_stdout(out_error):
                cmd_plans_backfill_project_signals(args_error)
            payload_error = json.loads(out_error.getvalue())
            self.assertEqual(int(payload_error.get("exit_code") or 0), 0)
            self.assertFalse(bool(payload_error.get("exit_triggered")))
            self.assertEqual(str(payload_error.get("status") or ""), "warning")

    def test_cmd_plans_backfill_project_signals_warning_exit_code_policy_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-exit-policy-env-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli exit policy env outcome",
                    recorded_at="2026-04-18T11:55:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-729",
                    number="729",
                    title="Exit Policy Env CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/729",
                    api_url="https://api.example.test/pr/729",
                    base_branch="main",
                    head_branch="feature/exit-policy-env-ci",
                    head_sha="deadbeef729",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef729",
                        web_url="https://example.test/pr/729",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-exit-policy-env-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-exit-policy-env-1",
                    plan_id="plan-cli-exit-policy-env-review",
                    step_id="step-cli-exit-policy-env-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="729",
                    branch="feature/exit-policy-env-ci",
                    artifact=artifact.to_dict(),
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.max_source_counts = 1
            args.summary_only = True
            with patch.dict(
                "os.environ",
                {
                    "JARVIS_BACKFILL_EXIT_CODE_POLICY": "warning",
                    "JARVIS_BACKFILL_WARNING_EXIT_CODE": "7",
                    "JARVIS_BACKFILL_ERROR_EXIT_CODE": "11",
                },
                clear=False,
            ):
                out = io.StringIO()
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stdout(out):
                        cmd_plans_backfill_project_signals(args)
            self.assertEqual(int(cm.exception.code), 7)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("exit_code_policy") or ""), "warning")
            self.assertEqual(int(payload.get("exit_code") or 0), 7)
            self.assertTrue(bool(payload.get("exit_triggered")))

    def test_cmd_plans_backfill_project_signals_warning_exit_code_policy_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-exit-policy-config-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli exit policy config outcome",
                    recorded_at="2026-04-18T12:05:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-759",
                    number="759",
                    title="Exit Policy Config CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/759",
                    api_url="https://api.example.test/pr/759",
                    base_branch="main",
                    head_branch="feature/exit-policy-config-ci",
                    head_sha="deadbeef759",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef759",
                        web_url="https://example.test/pr/759",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-exit-policy-config-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-exit-policy-config-1",
                    plan_id="plan-cli-exit-policy-config-review",
                    step_id="step-cli-exit-policy-config-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="759",
                    branch="feature/exit-policy-config-ci",
                    artifact=artifact.to_dict(),
                )
            finally:
                runtime.close()

            config_path = Path(td) / "warning-policy.json"
            config_path.write_text(
                json.dumps(
                    {
                        "warning_policy_profile": "strict",
                        "exit_code_policy": "warning",
                        "warning_exit_code": 8,
                        "error_exit_code": 11,
                    }
                ),
                encoding="utf-8",
            )
            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.max_source_counts = 1
            args.summary_only = True
            args.warning_policy_config = config_path
            out = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                with redirect_stdout(out):
                    cmd_plans_backfill_project_signals(args)
            self.assertEqual(int(cm.exception.code), 8)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("warning_policy_profile") or ""), "strict")
            self.assertEqual(str(payload.get("exit_code_policy") or ""), "warning")
            self.assertEqual(int(payload.get("exit_code") or 0), 8)
            resolution = dict(payload.get("warning_policy_resolution") or {})
            profile_resolution = dict(resolution.get("profile") or {})
            self.assertEqual(str(profile_resolution.get("source") or ""), "config")
            self.assertTrue(bool(payload.get("warning_policy_config_path")))

    def test_cmd_plans_backfill_project_signals_warning_exit_code_policy_from_repo_default_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-exit-policy-repo-default-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli exit policy repo default outcome",
                    recorded_at="2026-04-18T12:10:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-769",
                    number="769",
                    title="Exit Policy Repo Default CI",
                    body_markdown="Body",
                    web_url="https://example.test/pr/769",
                    api_url="https://api.example.test/pr/769",
                    base_branch="main",
                    head_branch="feature/exit-policy-repo-default-ci",
                    head_sha="deadbeef769",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef769",
                        web_url="https://example.test/pr/769",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-exit-policy-repo-default-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-exit-policy-repo-default-1",
                    plan_id="plan-cli-exit-policy-repo-default-review",
                    step_id="step-cli-exit-policy-repo-default-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="769",
                    branch="feature/exit-policy-repo-default-ci",
                    artifact=artifact.to_dict(),
                )
            finally:
                runtime.close()

            repo_policy = repo / ".jarvis" / "backfill.warning_policy.json"
            repo_policy.parent.mkdir(parents=True, exist_ok=True)
            repo_policy.write_text(
                json.dumps(
                    {
                        "warning_policy_profile": "strict",
                        "exit_code_policy": "warning",
                        "warning_exit_code": 6,
                        "error_exit_code": 11,
                    }
                ),
                encoding="utf-8",
            )

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.max_source_counts = 1
            args.summary_only = True
            out = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                with redirect_stdout(out):
                    cmd_plans_backfill_project_signals(args)
            self.assertEqual(int(cm.exception.code), 6)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("warning_policy_profile") or ""), "strict")
            self.assertEqual(str(payload.get("warning_policy_config_source") or ""), "repo_default")
            resolution = dict(payload.get("warning_policy_resolution") or {})
            profile_resolution = dict(resolution.get("profile") or {})
            self.assertEqual(str(profile_resolution.get("source") or ""), "config")
            self.assertTrue(bool(payload.get("warning_policy_config_path")))

    def test_cmd_plans_backfill_project_signals_warning_profile_strict_exit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo, db = self._make_repo(Path(td))
            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                runtime.plan_repo.record_outcome(
                    plan_id="plan-cli-warning-profile-strict-outcome",
                    repo_id="alpha",
                    branch="main",
                    status="failure",
                    touched_paths=["service.py"],
                    failure_family="ci",
                    summary="cli warning profile strict outcome",
                    recorded_at="2026-04-18T12:00:00+00:00",
                )
                artifact = ProviderReviewArtifact(
                    provider="fake",
                    repo_slug="acme/zenith",
                    external_id="pr-799",
                    number="799",
                    title="Warning Profile Strict",
                    body_markdown="Body",
                    web_url="https://example.test/pr/799",
                    api_url="https://api.example.test/pr/799",
                    base_branch="main",
                    head_branch="feature/warning-profile-strict",
                    head_sha="deadbeef799",
                    state="open",
                    draft=False,
                    status=ReviewStatusSnapshot(
                        review_state="open",
                        checks_state="failure",
                        blocking_contexts=("unit-tests",),
                        head_sha="deadbeef799",
                        web_url="https://example.test/pr/799",
                    ),
                    feedback=ReviewFeedbackSnapshot(
                        requested_reviewers=("alice",),
                        review_summary={"decision": "changes_requested"},
                        merge_outcome="blocked",
                        required_checks=("unit-tests",),
                        required_checks_configured=True,
                    ),
                    metadata={"repo_id": "alpha", "plan_id": "plan-cli-warning-profile-strict-review"},
                )
                runtime.security.store_review_artifact(
                    approval_id="apr-cli-warning-profile-strict-1",
                    plan_id="plan-cli-warning-profile-strict-review",
                    step_id="step-cli-warning-profile-strict-review",
                    provider="fake",
                    repo_id="alpha",
                    repo_slug="acme/zenith",
                    pr_number="799",
                    branch="feature/warning-profile-strict",
                    artifact=artifact.to_dict(),
                )
            finally:
                runtime.close()

            args = self._base_args(repo=repo, db=db)
            args.output = "warnings"
            args.preset = "quick"
            args.limit = 1
            args.include_review_artifacts = True
            args.max_source_counts = 1
            args.summary_only = True
            args.warning_policy_profile = "strict"
            out = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                with redirect_stdout(out):
                    cmd_plans_backfill_project_signals(args)
            self.assertEqual(int(cm.exception.code), 2)
            payload = json.loads(out.getvalue())
            self.assertEqual(str(payload.get("warning_policy_profile") or ""), "strict")
            self.assertEqual(str(payload.get("exit_code_policy") or ""), "warning")
            self.assertEqual(int(payload.get("exit_code") or 0), 2)
            self.assertTrue(bool(payload.get("exit_triggered")))
            self.assertEqual(str(payload.get("max_warning_severity") or ""), "warning")


if __name__ == "__main__":
    unittest.main()
