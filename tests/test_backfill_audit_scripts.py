from __future__ import annotations

import http.server
import importlib.util
import json
import random
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class BackfillAuditScriptsTests(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _write_artifact(
        self,
        *,
        path: Path,
        project_id: str,
        profile: str,
        checksum: str,
        exported_at: datetime,
        warning_codes: list[str] | None = None,
        drift_changed: bool = False,
        drift_guardrail_triggered: bool = False,
    ) -> None:
        payload = {
            "status": "ok",
            "project_id": project_id,
            "warning_count": int(len(warning_codes or [])),
            "warning_codes": list(warning_codes or []),
            "warning_policy_profile": profile,
            "warning_policy_checksum": checksum,
            "warning_policy_config_source": "explicit",
            "warning_policy_config_path": "/tmp/policy.json",
            "exit_code_policy": "off",
            "max_warning_severity": "none",
            "_audit": {
                "exported_at": exported_at.isoformat(),
                "policy_drift": {
                    "changed": bool(drift_changed),
                    "guardrail_triggered": bool(drift_guardrail_triggered),
                },
            },
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_export_script_module(self):
        repo = self._repo_root()
        script_path = repo / "scripts" / "export_backfill_warning_audit.py"
        spec = importlib.util.spec_from_file_location("export_backfill_warning_audit", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    def test_compare_script_policy_core_projection(self) -> None:
        repo = self._repo_root()
        compare_script = repo / "scripts" / "compare_backfill_policy_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            before = tmp / "before.json"
            after = tmp / "after.json"
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=before,
                project_id="alpha",
                profile="quiet",
                checksum="a" * 64,
                exported_at=now - timedelta(minutes=5),
            )
            self._write_artifact(
                path=after,
                project_id="alpha",
                profile="strict",
                checksum="b" * 64,
                exported_at=now,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(compare_script),
                    str(before),
                    str(after),
                    "--projection-profile",
                    "policy_core",
                    "--summary-only",
                    "--json-compact",
                    "--allow-changes",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(bool(payload.get("changed")))
            self.assertEqual(str(payload.get("projection_profile") or ""), "policy_core")
            changed_fields = list(payload.get("changed_fields") or [])
            self.assertIn("warning_policy_profile", changed_fields)
            self.assertIn("warning_policy_checksum", changed_fields)
            self.assertEqual(int(payload.get("changed_field_count") or 0), 2)

    def test_compare_script_policy_core_ignores_warning_noise(self) -> None:
        repo = self._repo_root()
        compare_script = repo / "scripts" / "compare_backfill_policy_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            before = tmp / "before.json"
            after = tmp / "after.json"
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=before,
                project_id="alpha",
                profile="quiet",
                checksum="a" * 64,
                exported_at=now - timedelta(minutes=5),
                warning_codes=["candidate_scan_clipped"],
            )
            after_payload = json.loads(before.read_text(encoding="utf-8"))
            after_payload["warning_codes"] = ["source_counts_capped", "signal_type_counts_capped"]
            after_payload["max_warning_severity"] = "warning"
            after_payload["warning_policy_config_path"] = "/tmp/another-policy.json"
            after.write_text(json.dumps(after_payload, indent=2), encoding="utf-8")

            full_proc = subprocess.run(
                [
                    sys.executable,
                    str(compare_script),
                    str(before),
                    str(after),
                    "--projection-profile",
                    "full",
                    "--summary-only",
                    "--json-compact",
                    "--allow-changes",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(full_proc.returncode, 0, msg=full_proc.stderr)
            full_payload = json.loads(full_proc.stdout)
            self.assertTrue(bool(full_payload.get("changed")))
            self.assertGreater(int(full_payload.get("changed_field_count") or 0), 0)

            core_proc = subprocess.run(
                [
                    sys.executable,
                    str(compare_script),
                    str(before),
                    str(after),
                    "--projection-profile",
                    "policy_core",
                    "--summary-only",
                    "--json-compact",
                    "--allow-changes",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(core_proc.returncode, 0, msg=core_proc.stderr)
            core_payload = json.loads(core_proc.stdout)
            self.assertFalse(bool(core_payload.get("changed")))
            self.assertEqual(int(core_payload.get("changed_field_count") or 0), 0)

    def test_summarize_and_prune_scripts(self) -> None:
        repo = self._repo_root()
        summarize_script = repo / "scripts" / "summarize_backfill_warning_audits.py"
        prune_script = repo / "scripts" / "prune_backfill_warning_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_old.json",
                project_id="alpha",
                profile="quiet",
                checksum="1" * 64,
                exported_at=now - timedelta(hours=2),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=True,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "alpha_new.json",
                project_id="alpha",
                profile="quiet",
                checksum="1" * 64,
                exported_at=now - timedelta(hours=1),
                warning_codes=[],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "beta_new.json",
                project_id="beta",
                profile="strict",
                checksum="2" * 64,
                exported_at=now - timedelta(minutes=30),
                warning_codes=[],
                drift_changed=False,
                drift_guardrail_triggered=True,
            )

            summarize_proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(summarize_proc.returncode, 0, msg=summarize_proc.stderr)
            summary = json.loads(summarize_proc.stdout)
            self.assertEqual(int(summary.get("total_runs") or 0), 3)
            self.assertEqual(int(summary.get("policy_drift_changed_count") or 0), 1)
            self.assertEqual(int(summary.get("policy_drift_guardrail_triggered_count") or 0), 1)
            self.assertEqual(str(summary.get("rollup_mode") or ""), "full")

            summarize_dashboard_proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(summarize_dashboard_proc.returncode, 0, msg=summarize_dashboard_proc.stderr)
            dashboard = json.loads(summarize_dashboard_proc.stdout)
            self.assertEqual(str(dashboard.get("rollup_mode") or ""), "dashboard")
            self.assertEqual(int(dashboard.get("total_runs") or 0), 3)
            self.assertEqual(int(dashboard.get("project_count") or 0), 2)
            alerts = dict(dashboard.get("alerts") or {})
            self.assertFalse(bool(alerts.get("enabled")))
            self.assertFalse(bool(alerts.get("triggered")))
            projects = list(dashboard.get("projects") or [])
            self.assertEqual(len(projects), 2)
            alpha = next((row for row in projects if str(row.get("project_id") or "") == "alpha"), None)
            beta = next((row for row in projects if str(row.get("project_id") or "") == "beta"), None)
            self.assertIsNotNone(alpha)
            self.assertIsNotNone(beta)
            self.assertEqual(int((alpha or {}).get("run_count") or 0), 2)
            self.assertEqual(int((beta or {}).get("run_count") or 0), 1)
            self.assertEqual(int((alpha or {}).get("total_warning_count") or 0), 1)
            self.assertEqual(int((beta or {}).get("guardrail_triggered_count") or 0), 1)

            summarize_dashboard_alert_proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--dashboard-alert-guardrail-triggered-count-threshold",
                    "1",
                    "--dashboard-alert-policy-drift-changed-rate-threshold",
                    "0.30",
                    "--dashboard-alert-project-guardrail-triggered-count-threshold",
                    "1",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                summarize_dashboard_alert_proc.returncode,
                0,
                msg=summarize_dashboard_alert_proc.stderr,
            )
            dashboard_alert = json.loads(summarize_dashboard_alert_proc.stdout)
            alerts_alert = dict(dashboard_alert.get("alerts") or {})
            self.assertTrue(bool(alerts_alert.get("enabled")))
            self.assertTrue(bool(alerts_alert.get("triggered")))
            triggered_rules = set(str(item or "") for item in list(alerts_alert.get("triggered_rules") or []))
            self.assertIn("guardrail_triggered_count_threshold", triggered_rules)
            self.assertIn("policy_drift_changed_rate_threshold", triggered_rules)
            self.assertIn("project_guardrail_triggered_count_threshold", triggered_rules)
            metrics = dict(alerts_alert.get("metrics") or {})
            self.assertEqual(int(metrics.get("guardrail_triggered_count") or 0), 1)
            self.assertEqual(int(metrics.get("policy_drift_changed_count") or 0), 1)
            self.assertEqual(int(metrics.get("max_project_guardrail_triggered_count") or 0), 1)

            prune_dry_proc = subprocess.run(
                [
                    sys.executable,
                    str(prune_script),
                    "--input-dir",
                    str(tmp),
                    "--keep-per-project",
                    "1",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(prune_dry_proc.returncode, 0, msg=prune_dry_proc.stderr)
            prune_dry = json.loads(prune_dry_proc.stdout)
            self.assertTrue(bool(prune_dry.get("dry_run")))
            self.assertEqual(int(prune_dry.get("selected_for_prune") or 0), 1)
            self.assertEqual(len(list(tmp.glob("*.json"))), 3)

            prune_exec_proc = subprocess.run(
                [
                    sys.executable,
                    str(prune_script),
                    "--input-dir",
                    str(tmp),
                    "--keep-per-project",
                    "1",
                    "--execute",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(prune_exec_proc.returncode, 0, msg=prune_exec_proc.stderr)
            prune_exec = json.loads(prune_exec_proc.stdout)
            self.assertFalse(bool(prune_exec.get("dry_run")))
            self.assertEqual(int(prune_exec.get("pruned_count") or 0), 1)
            self.assertEqual(len(list(tmp.glob("*.json"))), 2)

    def test_summarize_script_include_bridge_bundle(self) -> None:
        repo = self._repo_root()
        summarize_script = repo / "scripts" / "summarize_backfill_warning_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_prev.json",
                project_id="alpha",
                profile="quiet",
                checksum="a" * 64,
                exported_at=now - timedelta(hours=2),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="b" * 64,
                exported_at=now - timedelta(hours=1),
                warning_codes=[],
                drift_changed=True,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "beta_now.json",
                project_id="beta",
                profile="quiet",
                checksum="c" * 64,
                exported_at=now - timedelta(minutes=20),
                warning_codes=[],
                drift_changed=False,
                drift_guardrail_triggered=True,
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-policy-drift-severity",
                    "warn",
                    "--bridge-alert-project-severity-override",
                    "alpha=warn@policy_only",
                    "--bridge-include-markdown",
                    "--bridge-markdown-max-projects",
                    "1",
                    "--bridge-markdown-alert-compact",
                    "--bridge-markdown-hide-suppression-section",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-hide-empty-families",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-order",
                    "severity_then_project",
                    "--bridge-markdown-triggered-rule-detail-max",
                    "0",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(str(payload.get("rollup_mode") or ""), "dashboard")
            bridge = dict(payload.get("bridge") or {})
            self.assertEqual(str(bridge.get("kind") or ""), "backfill_warning_bridge")
            self.assertEqual(int(bridge.get("total_runs") or 0), 3)
            alerts = dict(bridge.get("alerts") or {})
            self.assertTrue(bool(alerts.get("enabled")))
            self.assertTrue(bool(alerts.get("triggered")))
            self.assertFalse(bool(alerts.get("exit_triggered")))
            self.assertEqual(str(alerts.get("max_triggered_severity") or ""), "warn")
            self.assertEqual(dict(alerts.get("project_severity_overrides") or {}), {"alpha": "warn"})
            self.assertEqual(dict(alerts.get("project_severity_override_scopes") or {}), {"alpha": "policy_only"})
            warn_rules = set(str(item or "") for item in list(alerts.get("triggered_warn_rules") or []))
            self.assertIn("policy_drift_count_threshold", warn_rules)
            markdown = str(payload.get("bridge_markdown") or "")
            self.assertIn("# Backfill Warning Bridge Briefing", markdown)
            self.assertIn("Alert Detail Mode: `compact`", markdown)
            self.assertIn("Suppression Section: `hidden`", markdown)
            self.assertNotIn("Family Projects (all_current)", markdown)
            self.assertIn("Family Projects Mode: `counts_only`", markdown)
            self.assertIn("Family Projects Source: `all_current`", markdown)
            self.assertIn("Family Projects Severity Filter: `all`", markdown)
            self.assertIn("Family Projects Order: `severity_then_project`", markdown)
            self.assertIn("Family Projects Count Order: `by_family`", markdown)
            self.assertIn("Family Projects Count Render Mode: `full_fields`", markdown)
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", markdown)
            self.assertIn("Family Projects Count Export Mode: `inline`", markdown)
            self.assertIn("Family Projects Count Table Style: `full`", markdown)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", markdown)
            self.assertIn("Family Projects Count Threshold Mode: `off`", markdown)
            self.assertIn("Family Projects Count Min All: `0`", markdown)
            self.assertIn("Family Projects Empty Families: `hidden`", markdown)
            self.assertIn("Family Projects Counts:", markdown)
            self.assertIn("policy_only:warn=1 error=0 all=1", markdown)
            self.assertIn("guardrail_only:", markdown)
            self.assertIn("both:warn=0 error=0 all=0", markdown)
            self.assertNotIn("all=[alpha]", markdown)
            self.assertNotIn("both:warn=[", markdown)
            self.assertNotIn("Suppression Digest Counts", markdown)
            self.assertNotIn("Triggered Rule Detail:", markdown)
            self.assertIn("`alpha`", markdown)
            self.assertNotIn("`beta`", markdown)

            proc_family_cap = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-max-items",
                    "0",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc_family_cap.returncode, 0, msg=proc_family_cap.stderr)
            family_cap_payload = json.loads(proc_family_cap.stdout)
            family_cap_markdown = str(family_cap_payload.get("bridge_markdown") or "")
            self.assertIn("Family Projects Max Items: `0`", family_cap_markdown)
            self.assertIn("all=[none (+1 more)]", family_cap_markdown)

            proc_count_min_all = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-min-all",
                    "2",
                    "--bridge-markdown-family-projects-count-threshold-mode",
                    "all_min",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc_count_min_all.returncode, 0, msg=proc_count_min_all.stderr)
            count_min_payload = json.loads(proc_count_min_all.stdout)
            count_min_markdown = str(count_min_payload.get("bridge_markdown") or "")
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", count_min_markdown)
            self.assertIn("Family Projects Count Export Mode: `inline`", count_min_markdown)
            self.assertIn("Family Projects Count Table Style: `full`", count_min_markdown)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", count_min_markdown)
            self.assertIn("Family Projects Count Threshold Mode: `all_min`", count_min_markdown)
            self.assertIn("Family Projects Count Min All: `2`", count_min_markdown)
            self.assertIn("Family Projects Counts: `none`", count_min_markdown)

            proc_count_top_n = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-order",
                    "by_total_desc",
                    "--bridge-markdown-family-projects-count-top-n",
                    "1",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc_count_top_n.returncode, 0, msg=proc_count_top_n.stderr)
            count_top_n_payload = json.loads(proc_count_top_n.stdout)
            count_top_n_markdown = str(count_top_n_payload.get("bridge_markdown") or "")
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", count_top_n_markdown)
            self.assertIn("Family Projects Count Export Mode: `inline`", count_top_n_markdown)
            self.assertIn("Family Projects Count Table Style: `full`", count_top_n_markdown)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", count_top_n_markdown)
            self.assertIn("Family Projects Count Top N: `1`", count_top_n_markdown)
            self.assertIn("Family Projects Count Rows: `shown=1 total=3 omitted=2`", count_top_n_markdown)
            self.assertIn("Family Projects Counts: `guardrail_only:warn=0 error=1 all=1`", count_top_n_markdown)

            proc_count_render_nonzero = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-render-mode",
                    "nonzero_buckets",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc_count_render_nonzero.returncode, 0, msg=proc_count_render_nonzero.stderr)
            count_render_nonzero_payload = json.loads(proc_count_render_nonzero.stdout)
            count_render_nonzero_markdown = str(count_render_nonzero_payload.get("bridge_markdown") or "")
            self.assertIn("Family Projects Count Render Mode: `nonzero_buckets`", count_render_nonzero_markdown)
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", count_render_nonzero_markdown)
            self.assertIn("Family Projects Count Export Mode: `inline`", count_render_nonzero_markdown)
            self.assertIn("Family Projects Count Table Style: `full`", count_render_nonzero_markdown)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", count_render_nonzero_markdown)
            self.assertIn(
                "Family Projects Counts: `policy_only:error=1 all=1; guardrail_only:error=1 all=1; both:all=0`",
                count_render_nonzero_markdown,
            )

            proc_count_export_table = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc_count_export_table.returncode, 0, msg=proc_count_export_table.stderr)
            count_export_table_payload = json.loads(proc_count_export_table.stdout)
            count_export_table_markdown = str(count_export_table_payload.get("bridge_markdown") or "")
            self.assertIn("Family Projects Count Export Mode: `table`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Style: `full`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Family Label Mode: `raw`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Header Label Mode: `title`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Family Label Overrides: `none`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Metric Label Mode: `title`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Metric Label Overrides: `none`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Row Order Mode: `count_order`", count_export_table_markdown)
            self.assertIn("Family Projects Count Table Include Schema Signature: `False`", count_export_table_markdown)
            self.assertIn("Family Projects Count Inline Family Label Mode: `raw`", count_export_table_markdown)
            self.assertIn("Family Projects Count Inline Bucket Label Mode: `raw`", count_export_table_markdown)
            self.assertIn("Family Projects Count Label Override Diagnostics: `False`", count_export_table_markdown)
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Counters: `resolved_family_count=0 resolved_metric_count=0 family_malformed_count=0 metric_malformed_count=0`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity Mode: `off`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Triggered: `False`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity: `none`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON: `False`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Mode: `full`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Key Prefix Mode: `bridge_`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Profile: `compact_min`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_only`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override CI Policy Mode: `off`",
                count_export_table_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Fail CI Recommended: `False`",
                count_export_table_markdown,
            )
            self.assertIn("- Family Projects Counts Table:", count_export_table_markdown)
            self.assertIn("| Family | Warn | Error | All |", count_export_table_markdown)
            self.assertIn("| policy_only | 0 | 1 | 1 |", count_export_table_markdown)
            self.assertIn("| guardrail_only | 0 | 1 | 1 |", count_export_table_markdown)
            self.assertIn("| both | 0 | 0 | 0 |", count_export_table_markdown)
            self.assertNotIn("Family Projects Counts: `policy_only:", count_export_table_markdown)

            proc_count_export_table_row_order_canonical = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-order",
                    "by_total_desc",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-row-order-mode",
                    "canonical",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_row_order_canonical.returncode,
                0,
                msg=proc_count_export_table_row_order_canonical.stderr,
            )
            count_export_table_row_order_canonical_payload = json.loads(
                proc_count_export_table_row_order_canonical.stdout
            )
            count_export_table_row_order_canonical_markdown = str(
                count_export_table_row_order_canonical_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Row Order Mode: `canonical`",
                count_export_table_row_order_canonical_markdown,
            )
            self.assertLess(
                count_export_table_row_order_canonical_markdown.index("| policy_only | 0 | 1 | 1 |"),
                count_export_table_row_order_canonical_markdown.index("| guardrail_only | 0 | 1 | 1 |"),
            )

            proc_count_export_table_row_order_sorted = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-order",
                    "by_total_desc",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-row-order-mode",
                    "sorted",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_row_order_sorted.returncode,
                0,
                msg=proc_count_export_table_row_order_sorted.stderr,
            )
            count_export_table_row_order_sorted_payload = json.loads(
                proc_count_export_table_row_order_sorted.stdout
            )
            count_export_table_row_order_sorted_markdown = str(
                count_export_table_row_order_sorted_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Row Order Mode: `sorted`",
                count_export_table_row_order_sorted_markdown,
            )
            self.assertLess(
                count_export_table_row_order_sorted_markdown.index("| both | 0 | 0 | 0 |"),
                count_export_table_row_order_sorted_markdown.index("| guardrail_only | 0 | 1 | 1 |"),
            )

            proc_count_export_table_schema_signature = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-include-schema-signature",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_schema_signature.returncode,
                0,
                msg=proc_count_export_table_schema_signature.stderr,
            )
            count_export_table_schema_signature_payload = json.loads(
                proc_count_export_table_schema_signature.stdout
            )
            count_export_table_schema_signature_markdown = str(
                count_export_table_schema_signature_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Include Schema Signature: `True`",
                count_export_table_schema_signature_markdown,
            )
            self.assertIn(
                "Family Projects Counts Table Schema Signature:",
                count_export_table_schema_signature_markdown,
            )
            self.assertIn(
                "\"columns\":[\"family\",\"warn\",\"error\",\"all\"]",
                count_export_table_schema_signature_markdown,
            )
            self.assertIn(
                "\"headers\":[\"Family\",\"Warn\",\"Error\",\"All\"]",
                count_export_table_schema_signature_markdown,
            )

            proc_count_export_table_minimal = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-style",
                    "minimal",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_minimal.returncode,
                0,
                msg=proc_count_export_table_minimal.stderr,
            )
            count_export_table_minimal_payload = json.loads(proc_count_export_table_minimal.stdout)
            count_export_table_minimal_markdown = str(
                count_export_table_minimal_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Style: `minimal`",
                count_export_table_minimal_markdown,
            )
            self.assertIn(
                "Family Projects Count Table Empty Mode: `inline_none`",
                count_export_table_minimal_markdown,
            )
            self.assertIn("| Family | Error | All |", count_export_table_minimal_markdown)
            self.assertNotIn("| Family | Warn | Error | All |", count_export_table_minimal_markdown)
            self.assertIn("| policy_only | 1 | 1 |", count_export_table_minimal_markdown)
            self.assertIn("| guardrail_only | 1 | 1 |", count_export_table_minimal_markdown)
            self.assertIn("| both | 0 | 0 |", count_export_table_minimal_markdown)

            proc_count_export_table_title = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-family-label-mode",
                    "title",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_title.returncode,
                0,
                msg=proc_count_export_table_title.stderr,
            )
            count_export_table_title_payload = json.loads(proc_count_export_table_title.stdout)
            count_export_table_title_markdown = str(
                count_export_table_title_payload.get("bridge_markdown") or ""
            )
            self.assertIn("Family Projects Count Export Mode: `table`", count_export_table_title_markdown)
            self.assertIn("Family Projects Count Table Family Label Mode: `title`", count_export_table_title_markdown)
            self.assertIn("| Family | Warn | Error | All |", count_export_table_title_markdown)
            self.assertIn("| Policy Only | 0 | 1 | 1 |", count_export_table_title_markdown)
            self.assertIn("| Guardrail Only | 0 | 1 | 1 |", count_export_table_title_markdown)
            self.assertIn("| Both | 0 | 0 | 0 |", count_export_table_title_markdown)
            self.assertNotIn("| policy_only | 0 | 1 | 1 |", count_export_table_title_markdown)

            proc_count_export_table_header_raw = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-header-label-mode",
                    "raw",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_header_raw.returncode,
                0,
                msg=proc_count_export_table_header_raw.stderr,
            )
            count_export_table_header_raw_payload = json.loads(proc_count_export_table_header_raw.stdout)
            count_export_table_header_raw_markdown = str(
                count_export_table_header_raw_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Header Label Mode: `raw`",
                count_export_table_header_raw_markdown,
            )
            self.assertIn("| family | Warn | Error | All |", count_export_table_header_raw_markdown)
            self.assertNotIn("| Family | Warn | Error | All |", count_export_table_header_raw_markdown)

            proc_count_export_table_override = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,guardrail_only=Guardrail Lane,both=Cross Lane",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_override.returncode,
                0,
                msg=proc_count_export_table_override.stderr,
            )
            count_export_table_override_payload = json.loads(proc_count_export_table_override.stdout)
            count_export_table_override_markdown = str(
                count_export_table_override_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Family Label Overrides:",
                count_export_table_override_markdown,
            )
            self.assertIn("policy_only=Policy Lane", count_export_table_override_markdown)
            self.assertIn("guardrail_only=Guardrail Lane", count_export_table_override_markdown)
            self.assertIn("both=Cross Lane", count_export_table_override_markdown)
            self.assertIn("| Policy Lane | 0 | 1 | 1 |", count_export_table_override_markdown)
            self.assertIn("| Guardrail Lane | 0 | 1 | 1 |", count_export_table_override_markdown)
            self.assertIn("| Cross Lane | 0 | 0 | 0 |", count_export_table_override_markdown)
            self.assertNotIn("| policy_only | 0 | 1 | 1 |", count_export_table_override_markdown)

            proc_count_export_table_metric_raw = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-metric-label-mode",
                    "raw",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_metric_raw.returncode,
                0,
                msg=proc_count_export_table_metric_raw.stderr,
            )
            count_export_table_metric_raw_payload = json.loads(proc_count_export_table_metric_raw.stdout)
            count_export_table_metric_raw_markdown = str(
                count_export_table_metric_raw_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Metric Label Mode: `raw`",
                count_export_table_metric_raw_markdown,
            )
            self.assertIn("| Family | warn | error | all |", count_export_table_metric_raw_markdown)
            self.assertNotIn("| Family | Warn | Error | All |", count_export_table_metric_raw_markdown)

            proc_count_export_table_metric_override = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=Critical,all=Total",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_metric_override.returncode,
                0,
                msg=proc_count_export_table_metric_override.stderr,
            )
            count_export_table_metric_override_payload = json.loads(proc_count_export_table_metric_override.stdout)
            count_export_table_metric_override_markdown = str(
                count_export_table_metric_override_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Table Metric Label Overrides:",
                count_export_table_metric_override_markdown,
            )
            self.assertIn("warn=Warning", count_export_table_metric_override_markdown)
            self.assertIn("error=Critical", count_export_table_metric_override_markdown)
            self.assertIn("all=Total", count_export_table_metric_override_markdown)
            self.assertIn("| Family | Warning | Critical | Total |", count_export_table_metric_override_markdown)
            self.assertNotIn("| Family | Warn | Error | All |", count_export_table_metric_override_markdown)

            proc_count_inline_label_title = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-inline-family-label-mode",
                    "title",
                    "--bridge-markdown-family-projects-count-inline-bucket-label-mode",
                    "title",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_inline_label_title.returncode,
                0,
                msg=proc_count_inline_label_title.stderr,
            )
            count_inline_label_title_payload = json.loads(proc_count_inline_label_title.stdout)
            count_inline_label_title_markdown = str(count_inline_label_title_payload.get("bridge_markdown") or "")
            self.assertIn(
                "Family Projects Count Inline Family Label Mode: `title`",
                count_inline_label_title_markdown,
            )
            self.assertIn(
                "Family Projects Count Inline Bucket Label Mode: `title`",
                count_inline_label_title_markdown,
            )
            self.assertIn(
                "Family Projects Counts: `Policy Only:Warn=0 Error=1 All=1; Guardrail Only:Warn=0 Error=1 All=1; Both:Warn=0 Error=0 All=0`",
                count_inline_label_title_markdown,
            )

            proc_count_override_diagnostics = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-severity",
                    "warn",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics.returncode,
                0,
                msg=proc_count_override_diagnostics.stderr,
            )
            count_override_diagnostics_payload = json.loads(proc_count_override_diagnostics.stdout)
            count_override_diagnostics_markdown = str(
                count_override_diagnostics_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics: `True`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Counters: `resolved_family_count=1 resolved_metric_count=1 family_malformed_count=3 metric_malformed_count=2`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity Mode: `warn`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Triggered: `True`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity: `warn`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON: `True`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Mode: `full`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Key Prefix Mode: `bridge_`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Profile: `compact_min`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_only`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override CI Policy Mode: `strict`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Fail CI Recommended: `True`",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Detail:",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Detail:",
                count_override_diagnostics_markdown,
            )
            self.assertIn("\"ci_policy_mode\":\"strict\"", count_override_diagnostics_markdown)
            self.assertIn("\"fail_ci_recommended\":true", count_override_diagnostics_markdown)
            self.assertIn("\"family_malformed_count\":3", count_override_diagnostics_markdown)
            self.assertIn("\"metric_malformed_count\":2", count_override_diagnostics_markdown)
            self.assertIn("\"json_compact_token_sort_mode\":\"input_order\"", count_override_diagnostics_markdown)
            self.assertIn("\"json_compact_token_max_per_scope\":0", count_override_diagnostics_markdown)
            self.assertIn("\"json_compact_token_overflow_suffix\":\"+{omitted} more\"", count_override_diagnostics_markdown)
            self.assertIn("\"json_compact_token_overflow_suffix_mode\":\"include\"", count_override_diagnostics_markdown)
            self.assertIn(
                "\"json_compact_token_omitted_count_visibility_mode\":\"always\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_list_guard_mode\":\"off\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_list_key_mode\":\"always\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_scope_fallback_mode\":\"selected_only\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_truncation_indicator_mode\":\"off\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_scope_priority_mode\":\"family_first\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_fallback_emission_mode\":\"first_success_only\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_fallback_source_marker_mode\":\"off\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_fallback_source_marker_activation_mode\":\"always\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_selected_scope_marker_mode\":\"off\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_key_naming_mode\":\"default\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_suppression_mode\":\"off\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_summary_visibility_mode\":\"always\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_scope_order_mode\":\"priority\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_list_visibility_mode\":\"always\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_key_prefix_mode\":\"inherit\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_boolean_type_visibility_mode\":\"all\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_summary_list_order_mode\":\"insertion\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_summary_family_visibility_mode\":\"all\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_per_scope_family_visibility_mode\":\"all\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_summary_boolean_family_visibility_mode\":\"all\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_profile_mode\":\"off\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_profile_mode_source\":\"baseline_default\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_key_naming_mode_source\":\"baseline_default\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn(
                "\"json_compact_token_marker_summary_boolean_family_visibility_mode_source\":\"baseline_default\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn("\"json_compact_token_scope_mode\":\"both\"", count_override_diagnostics_markdown)
            self.assertIn("\"json_compact_token_dedup_mode\":\"off\"", count_override_diagnostics_markdown)
            self.assertIn("\"json_compact_token_normalization_mode\":\"preserve\"", count_override_diagnostics_markdown)
            self.assertIn("\"json_compact_token_sanitization_mode\":\"off\"", count_override_diagnostics_markdown)
            self.assertIn(
                "\"json_compact_token_sanitization_replacement_char\":\"_\"",
                count_override_diagnostics_markdown,
            )
            self.assertIn("\"json_compact_token_min_length\":1", count_override_diagnostics_markdown)
            self.assertIn("\"severity_mode\":\"warn\"", count_override_diagnostics_markdown)
            self.assertIn("\"triggered\":true", count_override_diagnostics_markdown)
            self.assertIn("badtoken", count_override_diagnostics_markdown)
            self.assertIn("unknown=Nope", count_override_diagnostics_markdown)
            self.assertIn("both=", count_override_diagnostics_markdown)
            self.assertIn("error=", count_override_diagnostics_markdown)
            self.assertIn("noeq", count_override_diagnostics_markdown)
            self.assertIn("| Policy Lane | 0 | 1 | 1 |", count_override_diagnostics_markdown)

            proc_count_override_diagnostics_compact = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact.returncode,
                0,
                msg=proc_count_override_diagnostics_compact.stderr,
            )
            count_override_diagnostics_compact_payload = json.loads(
                proc_count_override_diagnostics_compact.stdout
            )
            count_override_diagnostics_compact_markdown = str(
                count_override_diagnostics_compact_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON: `True`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Mode: `compact`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Key Prefix Mode: `count_override_`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Profile: `compact_min`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_only`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_profile\":\"compact_min\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_include_mode\":\"counts_only\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_sort_mode\":\"input_order\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_max_per_scope\":0",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix\":\"+{omitted} more\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix_mode\":\"include\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"always\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"off\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"always\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_mode\":\"both\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_dedup_mode\":\"off\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_replacement_char\":\"_\"",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_min_length\":1",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_diagnostics_enabled\":false",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_fail_ci_recommended\":true",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_family_malformed_count\":3",
                count_override_diagnostics_compact_markdown,
            )
            self.assertIn(
                "\"count_override_metric_malformed_count\":2",
                count_override_diagnostics_compact_markdown,
            )
            self.assertNotIn("\"counters\":{", count_override_diagnostics_compact_markdown)
            self.assertNotIn("\"family\":{", count_override_diagnostics_compact_markdown)
            self.assertNotIn("\"metric\":{", count_override_diagnostics_compact_markdown)

            proc_count_override_diagnostics_compact_full = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-profile",
                    "compact_full",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_full.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_full.stderr,
            )
            count_override_diagnostics_compact_full_payload = json.loads(
                proc_count_override_diagnostics_compact_full.stdout
            )
            count_override_diagnostics_compact_full_markdown = str(
                count_override_diagnostics_compact_full_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Profile: `compact_full`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_plus_tokens`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_profile\":\"compact_full\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_include_mode\":\"counts_plus_tokens\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_sort_mode\":\"input_order\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_max_per_scope\":0",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix\":\"+{omitted} more\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix_mode\":\"include\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"always\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"off\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"always\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_mode\":\"both\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_dedup_mode\":\"off\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_replacement_char\":\"_\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_min_length\":1",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_table_row_order_mode\":\"count_order\"",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_family_malformed_tokens\":[\"badtoken\",\"unknown=Nope\",\"both=\"]",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_metric_malformed_tokens\":[\"error=\",\"noeq\"]",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_family_malformed_tokens_omitted_count\":0",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertIn(
                "\"count_override_metric_malformed_tokens_omitted_count\":0",
                count_override_diagnostics_compact_full_markdown,
            )
            self.assertNotIn("\"counters\":{", count_override_diagnostics_compact_full_markdown)
            self.assertNotIn("\"family\":{", count_override_diagnostics_compact_full_markdown)
            self.assertNotIn("\"metric\":{", count_override_diagnostics_compact_full_markdown)

            proc_count_override_diagnostics_compact_limited = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-profile",
                    "compact_full",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens_if_truncated",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sort-mode",
                    "lexicographic",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-max-per-scope",
                    "2",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix",
                    "[+{omitted}]",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix-mode",
                    "suppress",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
                    "off",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
                    "require_nonempty_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_truncated",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "summary_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode",
                    "all_eligible",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
                    "off",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode",
                    "if_true_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode",
                    "priority",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-list-visibility-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
                    "lexicographic",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode",
                    "all",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-boolean-family-visibility-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "off",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "family_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-dedup-mode",
                    "on",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-normalization-mode",
                    "lower",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-mode",
                    "ascii_safe",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-replacement-char",
                    "-",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "3",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,BådToken,badtoken,Unknown=Nope,both=,BADTOKEN",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,ERROR=,noeq,error=,NÖEQ",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_limited.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_limited.stderr,
            )
            count_override_diagnostics_compact_limited_payload = json.loads(
                proc_count_override_diagnostics_compact_limited.stdout
            )
            count_override_diagnostics_compact_limited_markdown = str(
                count_override_diagnostics_compact_limited_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_plus_tokens_if_truncated`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `lexicographic`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `2`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `[+{omitted}]`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `suppress`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `off`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `require_nonempty_tokens`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `if_truncated`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode: `summary_only`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Priority Mode: `metric_first`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Emission Mode: `all_eligible`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode: `summary`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode: `always`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode: `summary`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode: `short`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode: `off`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Visibility Mode: `if_true_only`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Scope Order Mode: `priority`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker List Visibility Mode: `if_nonempty`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Prefix Mode: `markers`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Boolean Type Visibility Mode: `selected_only`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary List Order Mode: `lexicographic`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Family Visibility Mode: `selected_only`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Per Scope Family Visibility Mode: `all`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Boolean Family Visibility Mode: `selected_only`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode: `off`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `family_only`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `on`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Normalization Mode: `lower`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Mode: `ascii_safe`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `-`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `3`",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_include_mode\":\"counts_plus_tokens_if_truncated\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_mode\":\"family_only\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_dedup_mode\":\"on\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_normalization_mode\":\"lower\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_mode\":\"ascii_safe\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_replacement_char\":\"-\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_min_length\":3",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"off\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"require_nonempty_tokens\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"if_truncated\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_truncation_indicator_mode\":\"summary_only\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_priority_mode\":\"metric_first\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_emission_mode\":\"all_eligible\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_mode\":\"summary\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_activation_mode\":\"always\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_selected_scope_marker_mode\":\"summary\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_suppression_mode\":\"off\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_visibility_mode\":\"if_true_only\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode\":\"priority\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_list_visibility_mode\":\"if_nonempty\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode\":\"markers\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_boolean_type_visibility_mode\":\"selected_only\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_list_order_mode\":\"lexicographic\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_family_visibility_mode\":\"selected_only\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_per_scope_family_visibility_mode\":\"all\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode\":\"selected_only\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode\":\"off\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode_source\":\"baseline_default\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode_source\":\"explicit_input\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_marker_sel_used\":true",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_marker_sel_scopes\":[\"family\"]",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_tokens_trunc\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_sel_used\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_sel_scopes\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_tokens_trunc\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_selected_source_used\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_selected_source_scopes\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_malformed_tokens_truncated\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_fallback_used\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_fallback_source_scopes\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_fb_scopes\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_fb_scopes\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix_mode\":\"suppress\"",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertIn(
                "\"count_override_family_malformed_tokens\":[\"ba-dtoken\",\"badtoken\"]",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_family_malformed_tokens_omitted_count\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens\":",
                count_override_diagnostics_compact_limited_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_omitted_count\":",
                count_override_diagnostics_compact_limited_markdown,
            )

            proc_count_override_diagnostics_compact_marker_list_order = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
                    "lexicographic",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_marker_list_order.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_marker_list_order.stderr,
            )
            count_override_diagnostics_compact_marker_list_order_markdown = str(
                json.loads(proc_count_override_diagnostics_compact_marker_list_order.stdout).get("bridge_markdown")
                or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary List Order Mode: `lexicographic`",
                count_override_diagnostics_compact_marker_list_order_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_list_order_mode\":\"lexicographic\"",
                count_override_diagnostics_compact_marker_list_order_markdown,
            )
            self.assertIn(
                "\"count_override_marker_sel_scopes\":[\"family\",\"metric\"]",
                count_override_diagnostics_compact_marker_list_order_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_sel_scopes\":[\"metric\",\"family\"]",
                count_override_diagnostics_compact_marker_list_order_markdown,
            )

            proc_count_override_diagnostics_compact_summary_family_visibility = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode",
                    "fallback_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
                    "lexicographic",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_summary_family_visibility.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_summary_family_visibility.stderr,
            )
            count_override_diagnostics_compact_summary_family_visibility_markdown = str(
                json.loads(
                    proc_count_override_diagnostics_compact_summary_family_visibility.stdout
                ).get("bridge_markdown")
                or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Family Visibility Mode: `fallback_only`",
                count_override_diagnostics_compact_summary_family_visibility_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_family_visibility_mode\":\"fallback_only\"",
                count_override_diagnostics_compact_summary_family_visibility_markdown,
            )
            self.assertIn(
                "\"count_override_marker_sel_used\":true",
                count_override_diagnostics_compact_summary_family_visibility_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_sel_scopes\":",
                count_override_diagnostics_compact_summary_family_visibility_markdown,
            )

            proc_count_override_diagnostics_compact_per_scope_family_visibility = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "all",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_per_scope_family_visibility.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_per_scope_family_visibility.stderr,
            )
            count_override_diagnostics_compact_per_scope_family_visibility_markdown = str(
                json.loads(
                    proc_count_override_diagnostics_compact_per_scope_family_visibility.stdout
                ).get("bridge_markdown")
                or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Per Scope Family Visibility Mode: `selected_only`",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_per_scope_family_visibility_mode\":\"selected_only\"",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )
            self.assertIn(
                "\"count_override_marker_family_sel_source\":true",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )
            self.assertIn(
                "\"count_override_marker_metric_sel_source\":true",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_family_fb_source\":",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_metric_fb_source\":",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_family_tokens_trunc\":",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_metric_tokens_trunc\":",
                count_override_diagnostics_compact_per_scope_family_visibility_markdown,
            )

            proc_count_override_diagnostics_compact_marker_profile_strict_minimal = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_minimal",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_marker_profile_strict_minimal.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_marker_profile_strict_minimal.stderr,
            )
            count_override_diagnostics_compact_marker_profile_strict_minimal_markdown = str(
                json.loads(
                    proc_count_override_diagnostics_compact_marker_profile_strict_minimal.stdout
                ).get("bridge_markdown")
                or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode: `strict_minimal`",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode\":\"strict_minimal\"",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode_source\":\"explicit_input\"",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode_source\":\"profile_default\"",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode\":\"markers\"",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode\":\"selected_only\"",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode_source\":\"profile_default\"",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_marker_sel_used\":true",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertIn(
                "\"count_override_marker_sel_scopes\":",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_fb_used\":",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )
            self.assertNotIn(
                "\"count_override_marker_fb_scopes\":",
                count_override_diagnostics_compact_marker_profile_strict_minimal_markdown,
            )

            proc_count_override_diagnostics_compact_marker_profile_strict_debug = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_debug",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_marker_profile_strict_debug.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_marker_profile_strict_debug.stderr,
            )
            count_override_diagnostics_compact_marker_profile_strict_debug_markdown = str(
                json.loads(
                    proc_count_override_diagnostics_compact_marker_profile_strict_debug.stdout
                ).get("bridge_markdown")
                or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode: `strict_debug`",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode\":\"strict_debug\"",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode_source\":\"explicit_input\"",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode\":\"markers\"",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode_source\":\"profile_default\"",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode\":\"canonical\"",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode_source\":\"profile_default\"",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_marker_family_selected_source\":true",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_marker_metric_selected_source\":true",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_marker_family_fallback_source\":false",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_marker_metric_fallback_source\":false",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_marker_family_malformed_tokens_truncated\":",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )
            self.assertIn(
                "\"count_override_marker_metric_malformed_tokens_truncated\":",
                count_override_diagnostics_compact_marker_profile_strict_debug_markdown,
            )

            proc_count_override_diagnostics_compact_sparse_guard = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "metric_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "99",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
                    "require_nonempty_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "summary_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
                    "omit_when_no_token_payload",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
                    "always",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_sparse_guard.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_sparse_guard.stderr,
            )
            count_override_diagnostics_compact_sparse_guard_markdown = str(
                json.loads(proc_count_override_diagnostics_compact_sparse_guard.stdout).get("bridge_markdown")
                or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `require_nonempty_tokens`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `if_nonempty`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode: `summary_only`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode: `summary`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode: `always`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode: `summary`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode: `short`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode: `omit_when_no_token_payload`",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"always\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"require_nonempty_tokens\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"if_nonempty\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_truncation_indicator_mode\":\"summary_only\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_mode\":\"summary\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_activation_mode\":\"always\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_selected_scope_marker_mode\":\"summary\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_suppression_mode\":\"omit_when_no_token_payload\"",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens\":",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_omitted_count\":",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertNotIn(
                "\"count_override_fb_used\":",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertNotIn(
                "\"count_override_fb_scopes\":",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertNotIn(
                "\"count_override_sel_used\":",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertNotIn(
                "\"count_override_sel_scopes\":",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )
            self.assertNotIn(
                "\"count_override_tokens_trunc\":",
                count_override_diagnostics_compact_sparse_guard_markdown,
            )

            proc_count_override_diagnostics_compact_sparse_fallback = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "metric_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "8",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
                    "require_nonempty_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "auto_expand_when_empty",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "off",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode",
                    "all_eligible",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "fallback_only",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "per_scope",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
                    "off",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode",
                    "always",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode",
                    "canonical",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_truncated",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "1",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
                    "off",
                    "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_override_diagnostics_compact_sparse_fallback.returncode,
                0,
                msg=proc_count_override_diagnostics_compact_sparse_fallback.stderr,
            )
            count_override_diagnostics_compact_sparse_fallback_markdown = str(
                json.loads(proc_count_override_diagnostics_compact_sparse_fallback.stdout).get("bridge_markdown")
                or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `if_truncated`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `auto_expand_when_empty`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode: `off`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Priority Mode: `metric_first`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Emission Mode: `all_eligible`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode: `per_scope`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode: `fallback_only`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode: `per_scope`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode: `short`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode: `off`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Visibility Mode: `always`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Scope Order Mode: `canonical`",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"if_truncated\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"auto_expand_when_empty\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_truncation_indicator_mode\":\"off\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_priority_mode\":\"metric_first\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_emission_mode\":\"all_eligible\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_mode\":\"per_scope\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_activation_mode\":\"fallback_only\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_selected_scope_marker_mode\":\"per_scope\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_suppression_mode\":\"off\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_visibility_mode\":\"always\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode\":\"canonical\"",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_family_malformed_tokens\":[\"badtoken\",\"unknown=Nope\",\"both=\"]",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_metric_malformed_tokens\":[\"error=\",\"noeq\"]",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_fb_used\":true",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_metric_fb_source\":true",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertIn(
                "\"count_override_family_fb_source\":true",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertLess(
                count_override_diagnostics_compact_sparse_fallback_markdown.find("\"count_override_family_fb_source\":"),
                count_override_diagnostics_compact_sparse_fallback_markdown.find("\"count_override_metric_fb_source\":"),
            )
            self.assertNotIn(
                "\"count_override_fallback_used\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_fallback_source\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_family_fallback_source\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_selected_source_used\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_family_selected_source\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_selected_source\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_truncated\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_family_malformed_tokens_truncated\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_family_malformed_tokens_omitted_count\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_omitted_count\":",
                count_override_diagnostics_compact_sparse_fallback_markdown,
            )

            proc_count_export_table_empty = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-threshold-mode",
                    "all_min",
                    "--bridge-markdown-family-projects-count-min-all",
                    "2",
                    "--bridge-markdown-family-projects-count-table-empty-mode",
                    "table_empty",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_export_table_empty.returncode,
                0,
                msg=proc_count_export_table_empty.stderr,
            )
            count_export_table_empty_payload = json.loads(proc_count_export_table_empty.stdout)
            count_export_table_empty_markdown = str(
                count_export_table_empty_payload.get("bridge_markdown") or ""
            )
            self.assertIn("Family Projects Count Export Mode: `table`", count_export_table_empty_markdown)
            self.assertIn("Family Projects Count Table Style: `full`", count_export_table_empty_markdown)
            self.assertIn("Family Projects Count Table Empty Mode: `table_empty`", count_export_table_empty_markdown)
            self.assertIn("- Family Projects Counts Table:", count_export_table_empty_markdown)
            self.assertIn("| Family | Warn | Error | All |", count_export_table_empty_markdown)
            self.assertIn("| (none) | 0 | 0 | 0 |", count_export_table_empty_markdown)
            self.assertNotIn("Family Projects Counts: `none`", count_export_table_empty_markdown)

            proc_count_visibility_nonzero_all = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-include-counts",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-visibility-mode",
                    "nonzero_all",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                proc_count_visibility_nonzero_all.returncode,
                0,
                msg=proc_count_visibility_nonzero_all.stderr,
            )
            count_visibility_nonzero_all_payload = json.loads(proc_count_visibility_nonzero_all.stdout)
            count_visibility_nonzero_all_markdown = str(
                count_visibility_nonzero_all_payload.get("bridge_markdown") or ""
            )
            self.assertIn(
                "Family Projects Count Visibility Mode: `nonzero_all`",
                count_visibility_nonzero_all_markdown,
            )
            self.assertIn("Family Projects Count Export Mode: `inline`", count_visibility_nonzero_all_markdown)
            self.assertIn("Family Projects Count Table Style: `full`", count_visibility_nonzero_all_markdown)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", count_visibility_nonzero_all_markdown)
            self.assertIn(
                "Family Projects Counts: `policy_only:warn=0 error=1 all=1; guardrail_only:warn=0 error=1 all=1`",
                count_visibility_nonzero_all_markdown,
            )
            self.assertNotIn("both:all=0", count_visibility_nonzero_all_markdown)

    def test_publish_bridge_markdown_helper_dry_run_and_skip(self) -> None:
        repo = self._repo_root()
        publish_script = repo / "scripts" / "publish_bridge_markdown.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            markdown_path = tmp / "bridge.md"
            markdown_path.write_text("# Bridge\n\nAlpha delta ready.\n", encoding="utf-8")

            dry_proc = subprocess.run(
                [
                    sys.executable,
                    str(publish_script),
                    "--markdown-path",
                    str(markdown_path),
                    "--webhook-url",
                    "https://example.invalid/webhook",
                    "--dry-run",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(dry_proc.returncode, 0, msg=dry_proc.stderr)
            dry_payload = json.loads(dry_proc.stdout)
            self.assertEqual(str(dry_payload.get("status") or ""), "ok")
            self.assertTrue(bool(dry_payload.get("dry_run")))
            self.assertTrue(bool(dry_payload.get("would_post")))
            self.assertFalse(bool(dry_payload.get("posted")))
            self.assertFalse(bool(dry_payload.get("skipped")))
            self.assertIn("example.invalid", str(dry_payload.get("webhook_target") or ""))
            self.assertIn("retry_policy", dry_payload)

            dry_compact_proc = subprocess.run(
                [
                    sys.executable,
                    str(publish_script),
                    "--markdown-path",
                    str(markdown_path),
                    "--webhook-url",
                    "https://example.invalid/webhook",
                    "--dry-run",
                    "--dry-run-output-mode",
                    "preview_only",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(dry_compact_proc.returncode, 0, msg=dry_compact_proc.stderr)
            dry_compact_payload = json.loads(dry_compact_proc.stdout)
            self.assertEqual(str(dry_compact_payload.get("status") or ""), "ok")
            self.assertTrue(bool(dry_compact_payload.get("dry_run")))
            self.assertEqual(
                str(dry_compact_payload.get("dry_run_output_mode") or ""),
                "preview_only",
            )
            self.assertIn("payload_preview", dry_compact_payload)
            self.assertNotIn("retry_policy", dry_compact_payload)
            self.assertNotIn("attempts", dry_compact_payload)
            self.assertNotIn("attempt_count", dry_compact_payload)

            skip_proc = subprocess.run(
                [
                    sys.executable,
                    str(publish_script),
                    "--markdown-path",
                    str(markdown_path),
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(skip_proc.returncode, 0, msg=skip_proc.stderr)
            skip_payload = json.loads(skip_proc.stdout)
            self.assertEqual(str(skip_payload.get("status") or ""), "ok")
            self.assertFalse(bool(skip_payload.get("would_post")))
            self.assertFalse(bool(skip_payload.get("posted")))
            self.assertTrue(bool(skip_payload.get("skipped")))
            self.assertEqual(str(skip_payload.get("skip_reason") or ""), "webhook_url_missing")

    def test_publish_bridge_markdown_helper_retries_and_backoff(self) -> None:
        repo = self._repo_root()
        publish_script = repo / "scripts" / "publish_bridge_markdown.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            markdown_path = tmp / "bridge.md"
            markdown_path.write_text("# Bridge\n\nAlpha delta ready.\n", encoding="utf-8")

            class FlakyHandler(http.server.BaseHTTPRequestHandler):
                status_sequence = [500, 500, 200]
                request_count = 0
                request_bodies: list[str] = []

                def do_POST(self):  # noqa: N802
                    cls = type(self)
                    cls.request_count += 1
                    idx = min(cls.request_count - 1, len(cls.status_sequence) - 1)
                    status = int(cls.status_sequence[idx])
                    content_len = int(self.headers.get("Content-Length", "0") or 0)
                    body = self.rfile.read(content_len).decode("utf-8", errors="replace")
                    cls.request_bodies.append(body)
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

                def log_message(self, format, *args):  # noqa: A003
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FlakyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                webhook_url = f"http://127.0.0.1:{int(server.server_address[1])}/bridge-hook"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(publish_script),
                        "--markdown-path",
                        str(markdown_path),
                        "--webhook-url",
                        webhook_url,
                        "--retry-attempts",
                        "2",
                        "--retry-backoff-seconds",
                        "0",
                        "--retry-backoff-multiplier",
                        "1",
                        "--retry-max-backoff-seconds",
                        "0",
                        "--retry-on-http-status",
                        "500",
                        "--json-compact",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    cwd=str(repo),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(str(payload.get("status") or ""), "ok")
            self.assertTrue(bool(payload.get("posted")))
            self.assertEqual(int(payload.get("http_status") or 0), 200)
            self.assertEqual(int(payload.get("attempt_count") or 0), 3)
            self.assertEqual(int(payload.get("retries_attempted") or 0), 2)
            self.assertEqual(int(payload.get("retry_scheduled_count") or 0), 2)
            attempts = list(payload.get("attempts") or [])
            self.assertEqual(len(attempts), 3)
            first_started = datetime.fromisoformat(str(payload.get("first_attempt_started_at") or ""))
            last_finished = datetime.fromisoformat(str(payload.get("last_attempt_finished_at") or ""))
            self.assertLessEqual(first_started, last_finished)
            for attempt in attempts:
                datetime.fromisoformat(str(attempt.get("started_at") or ""))
                datetime.fromisoformat(str(attempt.get("finished_at") or ""))
                self.assertGreaterEqual(float(attempt.get("elapsed_ms") or 0.0), 0.0)
            self.assertTrue(bool(attempts[0].get("will_retry")))
            self.assertTrue(bool(attempts[1].get("will_retry")))
            self.assertFalse(bool(attempts[2].get("will_retry")))
            self.assertFalse(bool(attempts[0].get("next_attempt_at")))
            self.assertFalse(bool(attempts[1].get("next_attempt_at")))
            self.assertFalse(bool(attempts[2].get("next_attempt_at")))
            self.assertEqual(int(FlakyHandler.request_count), 3)
            self.assertGreaterEqual(len(FlakyHandler.request_bodies), 1)
            self.assertIn("Alpha delta ready.", FlakyHandler.request_bodies[0])

    def test_publish_bridge_markdown_helper_retry_jitter_seeded(self) -> None:
        repo = self._repo_root()
        publish_script = repo / "scripts" / "publish_bridge_markdown.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            markdown_path = tmp / "bridge.md"
            markdown_path.write_text("# Bridge\n\nAlpha delta ready.\n", encoding="utf-8")

            class FlakyHandler(http.server.BaseHTTPRequestHandler):
                status_sequence = [500, 500, 200]
                request_count = 0

                def do_POST(self):  # noqa: N802
                    cls = type(self)
                    cls.request_count += 1
                    idx = min(cls.request_count - 1, len(cls.status_sequence) - 1)
                    status = int(cls.status_sequence[idx])
                    content_len = int(self.headers.get("Content-Length", "0") or 0)
                    _ = self.rfile.read(content_len)
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

                def log_message(self, format, *args):  # noqa: A003
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FlakyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                webhook_url = f"http://127.0.0.1:{int(server.server_address[1])}/bridge-hook"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(publish_script),
                        "--markdown-path",
                        str(markdown_path),
                        "--webhook-url",
                        webhook_url,
                        "--retry-attempts",
                        "2",
                        "--retry-backoff-seconds",
                        "0",
                        "--retry-backoff-multiplier",
                        "1",
                        "--retry-max-backoff-seconds",
                        "0",
                        "--retry-jitter-seconds",
                        "0.25",
                        "--retry-jitter-seed",
                        "7",
                        "--retry-on-http-status",
                        "500",
                        "--json-compact",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    cwd=str(repo),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            retry_policy = dict(payload.get("retry_policy") or {})
            self.assertAlmostEqual(float(retry_policy.get("jitter_seconds") or 0.0), 0.25)
            self.assertEqual(int(retry_policy.get("jitter_seed") or 0), 7)
            attempts = list(payload.get("attempts") or [])
            self.assertEqual(len(attempts), 3)
            first_started = datetime.fromisoformat(str(payload.get("first_attempt_started_at") or ""))
            last_finished = datetime.fromisoformat(str(payload.get("last_attempt_finished_at") or ""))
            self.assertLessEqual(first_started, last_finished)
            expected_rng = random.Random(7)
            expected_jitter_1 = expected_rng.uniform(0.0, 0.25)
            expected_jitter_2 = expected_rng.uniform(0.0, 0.25)
            self.assertAlmostEqual(
                float(attempts[0].get("base_backoff_seconds") or 0.0), 0.0, places=7
            )
            self.assertAlmostEqual(
                float(attempts[1].get("base_backoff_seconds") or 0.0), 0.0, places=7
            )
            self.assertAlmostEqual(
                float(attempts[0].get("jitter_seconds") or 0.0),
                expected_jitter_1,
                places=7,
            )
            self.assertAlmostEqual(
                float(attempts[1].get("jitter_seconds") or 0.0),
                expected_jitter_2,
                places=7,
            )
            self.assertAlmostEqual(
                float(attempts[0].get("backoff_seconds") or 0.0),
                expected_jitter_1,
                places=7,
            )
            self.assertAlmostEqual(
                float(attempts[1].get("backoff_seconds") or 0.0),
                expected_jitter_2,
                places=7,
            )
            self.assertAlmostEqual(
                float(attempts[2].get("jitter_seconds") or 0.0), 0.0, places=7
            )
            self.assertAlmostEqual(
                float(attempts[2].get("backoff_seconds") or 0.0), 0.0, places=7
            )
            for attempt in attempts:
                started_at = datetime.fromisoformat(str(attempt.get("started_at") or ""))
                finished_at = datetime.fromisoformat(str(attempt.get("finished_at") or ""))
                self.assertLessEqual(started_at, finished_at)
                self.assertGreaterEqual(float(attempt.get("elapsed_ms") or 0.0), 0.0)
            next_1 = datetime.fromisoformat(str(attempts[0].get("next_attempt_at") or ""))
            next_2 = datetime.fromisoformat(str(attempts[1].get("next_attempt_at") or ""))
            finished_1 = datetime.fromisoformat(str(attempts[0].get("finished_at") or ""))
            finished_2 = datetime.fromisoformat(str(attempts[1].get("finished_at") or ""))
            self.assertGreater(next_1, finished_1)
            self.assertGreater(next_2, finished_2)
            self.assertFalse(bool(attempts[2].get("next_attempt_at")))

    def test_publish_bridge_markdown_helper_error_body_preview(self) -> None:
        repo = self._repo_root()
        publish_script = repo / "scripts" / "publish_bridge_markdown.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            markdown_path = tmp / "bridge.md"
            markdown_path.write_text("# Bridge\n\nAlpha delta ready.\n", encoding="utf-8")

            class ErrorBodyHandler(http.server.BaseHTTPRequestHandler):
                request_count = 0

                def do_POST(self):  # noqa: N802
                    cls = type(self)
                    cls.request_count += 1
                    content_len = int(self.headers.get("Content-Length", "0") or 0)
                    _ = self.rfile.read(content_len)
                    payload = b'{"error":"upstream overloaded for alpha lane"}'
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, format, *args):  # noqa: A003
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ErrorBodyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                webhook_url = f"http://127.0.0.1:{int(server.server_address[1])}/bridge-hook"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(publish_script),
                        "--markdown-path",
                        str(markdown_path),
                        "--webhook-url",
                        webhook_url,
                        "--retry-attempts",
                        "0",
                        "--error-body-preview-chars",
                        "20",
                        "--json-compact",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    cwd=str(repo),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(str(payload.get("status") or ""), "error")
            self.assertEqual(int(payload.get("error_body_preview_chars") or 0), 20)
            self.assertFalse(bool(payload.get("posted")))
            self.assertEqual(int(payload.get("attempt_count") or 0), 1)
            self.assertEqual(int(ErrorBodyHandler.request_count), 1)
            attempts = list(payload.get("attempts") or [])
            self.assertEqual(len(attempts), 1)
            preview = str(attempts[0].get("error_body_preview") or "")
            self.assertTrue(preview)
            self.assertIn("upstream", preview)
            self.assertEqual(str(payload.get("last_error_body_preview") or ""), preview)

    def test_publish_bridge_markdown_helper_retry_diagnostics_minimal(self) -> None:
        repo = self._repo_root()
        publish_script = repo / "scripts" / "publish_bridge_markdown.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            markdown_path = tmp / "bridge.md"
            markdown_path.write_text("# Bridge\n\nAlpha delta ready.\n", encoding="utf-8")

            class FlakyHandler(http.server.BaseHTTPRequestHandler):
                status_sequence = [500, 200]
                request_count = 0

                def do_POST(self):  # noqa: N802
                    cls = type(self)
                    cls.request_count += 1
                    idx = min(cls.request_count - 1, len(cls.status_sequence) - 1)
                    status = int(cls.status_sequence[idx])
                    content_len = int(self.headers.get("Content-Length", "0") or 0)
                    _ = self.rfile.read(content_len)
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

                def log_message(self, format, *args):  # noqa: A003
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FlakyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                webhook_url = f"http://127.0.0.1:{int(server.server_address[1])}/bridge-hook"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(publish_script),
                        "--markdown-path",
                        str(markdown_path),
                        "--webhook-url",
                        webhook_url,
                        "--retry-attempts",
                        "1",
                        "--retry-backoff-seconds",
                        "0",
                        "--retry-backoff-multiplier",
                        "1",
                        "--retry-max-backoff-seconds",
                        "0",
                        "--retry-on-http-status",
                        "500",
                        "--retry-diagnostics-mode",
                        "minimal",
                        "--json-compact",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    cwd=str(repo),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(str(payload.get("retry_diagnostics_mode") or ""), "minimal")
            self.assertEqual(int(payload.get("attempt_count") or 0), 2)
            attempts = list(payload.get("attempts") or [])
            self.assertEqual(len(attempts), 2)
            for attempt in attempts:
                keys = set(str(key) for key in attempt.keys())
                self.assertEqual(
                    keys,
                    {"attempt_number", "http_status", "error", "success", "will_retry"},
                )
            self.assertEqual(int(FlakyHandler.request_count), 2)

    def test_summarize_bridge_bundle_rule_suppression(self) -> None:
        repo = self._repo_root()
        summarize_script = repo / "scripts" / "summarize_backfill_warning_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_prev.json",
                project_id="alpha",
                profile="quiet",
                checksum="a" * 64,
                exported_at=now - timedelta(hours=2),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="b" * 64,
                exported_at=now - timedelta(hours=1),
                warning_codes=[],
                drift_changed=True,
                drift_guardrail_triggered=False,
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            bridge = dict(payload.get("bridge") or {})
            alerts = dict(bridge.get("alerts") or {})
            self.assertTrue(bool(alerts.get("enabled")))
            self.assertFalse(bool(alerts.get("triggered")))
            self.assertFalse(bool(alerts.get("exit_triggered")))
            self.assertIn("policy_drift_count_threshold", list(alerts.get("triggered_rules_raw") or []))
            self.assertIn("policy_drift_count_threshold", list(alerts.get("suppressed_triggered_rules") or []))
            self.assertEqual(dict(alerts.get("project_suppression_scopes") or {}), {"alpha": "policy_only"})

    def test_bridge_script_daily_deltas(self) -> None:
        repo = self._repo_root()
        bridge_script = repo / "scripts" / "build_backfill_warning_bridge.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            alpha_prev = tmp / "alpha_prev.json"
            alpha_now = tmp / "alpha_now.json"
            beta_now = tmp / "beta_now.json"

            self._write_artifact(
                path=alpha_prev,
                project_id="alpha",
                profile="quiet",
                checksum="a" * 64,
                exported_at=now - timedelta(hours=2),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=alpha_now,
                project_id="alpha",
                profile="quiet",
                checksum="b" * 64,
                exported_at=now - timedelta(hours=1),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=True,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=beta_now,
                project_id="beta",
                profile="strict",
                checksum="c" * 64,
                exported_at=now - timedelta(minutes=30),
                warning_codes=[],
                drift_changed=False,
                drift_guardrail_triggered=True,
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(str(payload.get("kind") or ""), "backfill_warning_bridge")
            self.assertEqual(int(payload.get("total_runs") or 0), 3)
            self.assertEqual(int(payload.get("project_count") or 0), 2)
            summary = dict(payload.get("summary") or {})
            self.assertEqual(int(summary.get("projects_with_previous") or 0), 1)
            self.assertEqual(int(summary.get("projects_with_policy_drift") or 0), 1)
            self.assertEqual(int(summary.get("projects_with_guardrail_triggered") or 0), 1)
            alerts = dict(payload.get("alerts") or {})
            self.assertFalse(bool(alerts.get("enabled")))
            self.assertFalse(bool(alerts.get("triggered")))

            projects = list(payload.get("projects") or [])
            alpha = next((row for row in projects if str(row.get("project_id") or "") == "alpha"), None)
            beta = next((row for row in projects if str(row.get("project_id") or "") == "beta"), None)
            self.assertIsNotNone(alpha)
            self.assertIsNotNone(beta)
            alpha_delta = dict((alpha or {}).get("delta_from_previous") or {})
            self.assertTrue(bool(alpha_delta.get("has_previous")))
            self.assertTrue(bool(alpha_delta.get("policy_drift_changed")))
            self.assertIn("warning_policy_checksum", list(alpha_delta.get("changed_fields") or []))
            beta_delta = dict((beta or {}).get("delta_from_previous") or {})
            self.assertFalse(bool(beta_delta.get("has_previous")))

            alert_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-guardrail-rate-threshold",
                    "0.5",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(alert_proc.returncode, 13, msg=alert_proc.stderr)
            alert_payload = json.loads(alert_proc.stdout)
            alert_block = dict(alert_payload.get("alerts") or {})
            self.assertTrue(bool(alert_block.get("enabled")))
            self.assertTrue(bool(alert_block.get("triggered")))
            self.assertTrue(bool(alert_block.get("exit_triggered")))
            self.assertEqual(str(alert_block.get("max_triggered_severity") or ""), "error")
            triggered_rules = set(str(item or "") for item in list(alert_block.get("triggered_rules") or []))
            self.assertIn("policy_drift_count_threshold", triggered_rules)
            self.assertIn("guardrail_rate_threshold", triggered_rules)
            triggered_error_rules = set(
                str(item or "") for item in list(alert_block.get("triggered_error_rules") or [])
            )
            self.assertIn("policy_drift_count_threshold", triggered_error_rules)
            self.assertIn("guardrail_rate_threshold", triggered_error_rules)
            self.assertEqual(int(alert_block.get("exit_code_when_triggered") or 0), 13)

            warn_only_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-policy-drift-severity",
                    "warn",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(warn_only_proc.returncode, 0, msg=warn_only_proc.stderr)
            warn_payload = json.loads(warn_only_proc.stdout)
            warn_alert_block = dict(warn_payload.get("alerts") or {})
            self.assertTrue(bool(warn_alert_block.get("enabled")))
            self.assertTrue(bool(warn_alert_block.get("triggered")))
            self.assertFalse(bool(warn_alert_block.get("exit_triggered")))
            self.assertEqual(str(warn_alert_block.get("max_triggered_severity") or ""), "warn")
            warn_rules = set(str(item or "") for item in list(warn_alert_block.get("triggered_warn_rules") or []))
            self.assertIn("policy_drift_count_threshold", warn_rules)
            self.assertEqual(list(warn_alert_block.get("triggered_error_rules") or []), [])

            per_project_override_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-policy-drift-severity",
                    "error",
                    "--bridge-alert-project-severity-override",
                    "alpha=warn",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(per_project_override_proc.returncode, 0, msg=per_project_override_proc.stderr)
            override_payload = json.loads(per_project_override_proc.stdout)
            override_alerts = dict(override_payload.get("alerts") or {})
            self.assertTrue(bool(override_alerts.get("triggered")))
            self.assertFalse(bool(override_alerts.get("exit_triggered")))
            self.assertEqual(str(override_alerts.get("max_triggered_severity") or ""), "warn")
            self.assertIn(
                "policy_drift_count_threshold",
                set(str(item or "") for item in list(override_alerts.get("triggered_warn_rules") or [])),
            )
            self.assertEqual(list(override_alerts.get("triggered_error_rules") or []), [])
            self.assertEqual(
                dict(override_alerts.get("project_severity_overrides") or {}),
                {"alpha": "warn"},
            )
            self.assertIn("alpha", list(override_alerts.get("project_severity_overrides_applied") or []))
            self.assertEqual(
                dict(override_alerts.get("project_severity_override_scopes") or {}),
                {"alpha": "both"},
            )

            scope_policy_only_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-guardrail-count-threshold",
                    "1",
                    "--bridge-alert-guardrail-severity",
                    "error",
                    "--bridge-alert-project-severity-override",
                    "beta=warn@policy_only",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(scope_policy_only_proc.returncode, 13, msg=scope_policy_only_proc.stderr)
            scope_policy_only_payload = json.loads(scope_policy_only_proc.stdout)
            scope_policy_only_alerts = dict(scope_policy_only_payload.get("alerts") or {})
            self.assertTrue(bool(scope_policy_only_alerts.get("triggered")))
            self.assertTrue(bool(scope_policy_only_alerts.get("exit_triggered")))
            self.assertIn(
                "guardrail_count_threshold",
                list(scope_policy_only_alerts.get("triggered_error_rules") or []),
            )
            self.assertEqual(
                dict(scope_policy_only_alerts.get("project_severity_override_scopes") or {}),
                {"beta": "policy_only"},
            )

            scope_guardrail_only_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-guardrail-count-threshold",
                    "1",
                    "--bridge-alert-guardrail-severity",
                    "error",
                    "--bridge-alert-project-severity-override",
                    "beta=warn@guardrail_only",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(scope_guardrail_only_proc.returncode, 0, msg=scope_guardrail_only_proc.stderr)
            scope_guardrail_only_payload = json.loads(scope_guardrail_only_proc.stdout)
            scope_guardrail_only_alerts = dict(scope_guardrail_only_payload.get("alerts") or {})
            self.assertTrue(bool(scope_guardrail_only_alerts.get("triggered")))
            self.assertFalse(bool(scope_guardrail_only_alerts.get("exit_triggered")))
            self.assertIn(
                "guardrail_count_threshold",
                list(scope_guardrail_only_alerts.get("triggered_warn_rules") or []),
            )
            self.assertEqual(
                dict(scope_guardrail_only_alerts.get("project_severity_override_scopes") or {}),
                {"beta": "guardrail_only"},
            )

            suppress_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-policy-drift-severity",
                    "error",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(suppress_proc.returncode, 0, msg=suppress_proc.stderr)
            suppress_payload = json.loads(suppress_proc.stdout)
            suppress_alerts = dict(suppress_payload.get("alerts") or {})
            self.assertTrue(bool(suppress_alerts.get("enabled")))
            self.assertFalse(bool(suppress_alerts.get("triggered")))
            self.assertFalse(bool(suppress_alerts.get("exit_triggered")))
            self.assertEqual(list(suppress_alerts.get("triggered_rules") or []), [])
            self.assertIn(
                "policy_drift_count_threshold",
                list(suppress_alerts.get("triggered_rules_raw") or []),
            )
            self.assertIn(
                "policy_drift_count_threshold",
                list(suppress_alerts.get("suppressed_triggered_rules") or []),
            )
            self.assertIn(
                "policy_drift_count_threshold",
                list(suppress_alerts.get("suppressed_rules_applied") or []),
            )
            self.assertEqual(dict(suppress_alerts.get("project_suppression_scopes") or {}), {})

            scoped_suppress_match_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-policy-drift-severity",
                    "error",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(scoped_suppress_match_proc.returncode, 0, msg=scoped_suppress_match_proc.stderr)
            scoped_suppress_match_payload = json.loads(scoped_suppress_match_proc.stdout)
            scoped_suppress_match_alerts = dict(scoped_suppress_match_payload.get("alerts") or {})
            self.assertFalse(bool(scoped_suppress_match_alerts.get("triggered")))
            self.assertFalse(bool(scoped_suppress_match_alerts.get("exit_triggered")))
            self.assertIn(
                "policy_drift_count_threshold",
                list(scoped_suppress_match_alerts.get("suppressed_rules_applied") or []),
            )
            self.assertEqual(
                dict(scoped_suppress_match_alerts.get("project_suppression_scopes") or {}),
                {"alpha": "policy_only"},
            )
            self.assertIn(
                "alpha",
                list(scoped_suppress_match_alerts.get("project_suppression_scopes_applied") or []),
            )

            scoped_suppress_miss_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-policy-drift-severity",
                    "error",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@guardrail_only",
                    "--bridge-alert-exit-code",
                    "13",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(scoped_suppress_miss_proc.returncode, 13, msg=scoped_suppress_miss_proc.stderr)
            scoped_suppress_miss_payload = json.loads(scoped_suppress_miss_proc.stdout)
            scoped_suppress_miss_alerts = dict(scoped_suppress_miss_payload.get("alerts") or {})
            self.assertTrue(bool(scoped_suppress_miss_alerts.get("triggered")))
            self.assertTrue(bool(scoped_suppress_miss_alerts.get("exit_triggered")))
            self.assertEqual(list(scoped_suppress_miss_alerts.get("suppressed_rules_applied") or []), [])
            self.assertIn(
                "alpha",
                list(scoped_suppress_miss_alerts.get("project_suppression_scopes_unused") or []),
            )

            markdown_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-include-counts",
                    "--markdown-family-projects-hide-empty-families",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(markdown_proc.returncode, 0, msg=markdown_proc.stderr)
            text = str(markdown_proc.stdout or "")
            self.assertIn("# Backfill Warning Bridge Briefing", text)
            self.assertIn("## Project Deltas", text)
            self.assertIn("Alerts Triggered", text)
            self.assertIn("Max Triggered Severity", text)
            self.assertIn("Suppressed Triggered Rules", text)
            self.assertIn("Triggered Rules By Family", text)
            self.assertNotIn("Triggered Family Projects", text)
            self.assertIn("Family Projects Mode: `counts_only`", text)
            self.assertIn("Family Projects Source: `triggered`", text)
            self.assertIn("Family Projects Severity Filter: `all`", text)
            self.assertIn("Family Projects Count Order: `by_family`", text)
            self.assertIn("Family Projects Count Render Mode: `full_fields`", text)
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", text)
            self.assertIn("Family Projects Count Threshold Mode: `off`", text)
            self.assertIn("Family Projects Count Min All: `0`", text)
            self.assertIn("Family Projects Empty Families: `hidden`", text)
            self.assertIn("Family Projects Counts:", text)
            self.assertIn("policy_only:warn=0 error=1 all=1", text)
            self.assertNotIn("all=[alpha]", text)
            self.assertNotIn("both:warn=[", text)
            self.assertIn("Suppressed Triggered Rules By Family", text)
            self.assertIn("policy_only=[policy_drift_count_threshold]", text)
            self.assertIn("guardrail_only=[none]", text)
            self.assertIn("Project Suppression Scopes", text)
            self.assertIn("Suppression Digest Counts", text)
            self.assertIn("requested=1 applied=1", text)
            self.assertIn("alpha@policy_only", text)
            self.assertIn("Triggered Rule Detail", text)
            self.assertIn("scope_matched=`True`", text)
            self.assertIn("suppressed=`True`", text)
            self.assertIn("`alpha`", text)
            self.assertNotIn("`beta`", text)
            self.assertIn("Truncated project rows", text)

            markdown_family_all_current_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                markdown_family_all_current_proc.returncode,
                0,
                msg=markdown_family_all_current_proc.stderr,
            )
            family_all_current_text = str(markdown_family_all_current_proc.stdout or "")
            self.assertIn("Family Projects (all_current)", family_all_current_text)
            self.assertIn("Family Projects Source: `all_current`", family_all_current_text)
            self.assertIn("Family Projects Severity Filter: `all`", family_all_current_text)
            self.assertIn("policy_only:", family_all_current_text)
            self.assertIn("guardrail_only:", family_all_current_text)
            self.assertIn("all=[alpha]", family_all_current_text)
            self.assertIn("beta", family_all_current_text)

            markdown_family_union_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-source",
                    "triggered_or_current",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                markdown_family_union_proc.returncode,
                0,
                msg=markdown_family_union_proc.stderr,
            )
            family_union_text = str(markdown_family_union_proc.stdout or "")
            self.assertIn("Family Projects (triggered_or_current)", family_union_text)
            self.assertIn("Family Projects Source: `triggered_or_current`", family_union_text)
            self.assertIn("alpha", family_union_text)
            self.assertIn("beta", family_union_text)

            markdown_family_warn_only_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-severity",
                    "warn_only",
                    "--bridge-alert-policy-drift-severity",
                    "warn",
                    "--bridge-alert-guardrail-severity",
                    "warn",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                markdown_family_warn_only_proc.returncode,
                0,
                msg=markdown_family_warn_only_proc.stderr,
            )
            family_warn_only_text = str(markdown_family_warn_only_proc.stdout or "")
            self.assertIn("Family Projects Severity Filter: `warn_only`", family_warn_only_text)
            self.assertIn("policy_only:warn=[alpha]", family_warn_only_text)
            self.assertIn("guardrail_only:warn=[beta]", family_warn_only_text)
            self.assertIn("error=[none]", family_warn_only_text)

            markdown_family_error_only_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-severity",
                    "error_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                markdown_family_error_only_proc.returncode,
                0,
                msg=markdown_family_error_only_proc.stderr,
            )
            family_error_only_text = str(markdown_family_error_only_proc.stdout or "")
            self.assertIn("Family Projects Severity Filter: `error_only`", family_error_only_text)
            self.assertIn("policy_only:", family_error_only_text)
            self.assertIn("guardrail_only:", family_error_only_text)
            self.assertIn("error=[alpha]", family_error_only_text)
            self.assertIn("error=[beta]", family_error_only_text)
            self.assertIn("warn=[none]", family_error_only_text)

            markdown_family_max_items_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-max-items",
                    "0",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                markdown_family_max_items_proc.returncode,
                0,
                msg=markdown_family_max_items_proc.stderr,
            )
            family_max_items_text = str(markdown_family_max_items_proc.stdout or "")
            self.assertIn("Family Projects Max Items: `0`", family_max_items_text)
            self.assertIn("all=[none (+1 more)]", family_max_items_text)

            markdown_compact_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-alert-compact",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(markdown_compact_proc.returncode, 0, msg=markdown_compact_proc.stderr)
            compact_text = str(markdown_compact_proc.stdout or "")
            self.assertIn("Alert Detail Mode: `compact`", compact_text)
            self.assertIn("Triggered Rules By Family", compact_text)
            self.assertIn("Suppression Digest Counts", compact_text)
            self.assertNotIn("Suppressed Rules Requested", compact_text)
            self.assertNotIn("Triggered Rule Detail:", compact_text)

            markdown_hidden_suppression_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-hide-suppression-section",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                markdown_hidden_suppression_proc.returncode,
                0,
                msg=markdown_hidden_suppression_proc.stderr,
            )
            hidden_text = str(markdown_hidden_suppression_proc.stdout or "")
            self.assertIn("Suppression Section: `hidden`", hidden_text)
            self.assertNotIn("Suppressed Triggered Rules", hidden_text)
            self.assertNotIn("Suppression Digest Counts", hidden_text)
            self.assertNotIn("scope_matched=", hidden_text)

            markdown_detail_cap_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-max-projects",
                    "1",
                    "--markdown-triggered-rule-detail-max",
                    "0",
                    "--bridge-alert-policy-drift-count-threshold",
                    "1",
                    "--bridge-alert-suppress-rule",
                    "policy_drift_count_threshold",
                    "--bridge-alert-project-suppress-scope",
                    "alpha@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(markdown_detail_cap_proc.returncode, 0, msg=markdown_detail_cap_proc.stderr)
            detail_cap_text = str(markdown_detail_cap_proc.stdout or "")
            self.assertNotIn("Triggered Rule Detail:", detail_cap_text)
            self.assertIn("Triggered Rule Detail Truncated", detail_cap_text)

    def test_bridge_script_family_project_ordering_modes(self) -> None:
        repo = self._repo_root()
        bridge_script = repo / "scripts" / "build_backfill_warning_bridge.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_prev.json",
                project_id="alpha",
                profile="quiet",
                checksum="a" * 64,
                exported_at=now - timedelta(hours=3),
                warning_codes=[],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="quiet",
                checksum="b" * 64,
                exported_at=now - timedelta(hours=2),
                warning_codes=[],
                drift_changed=True,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "aardvark_prev.json",
                project_id="aardvark",
                profile="quiet",
                checksum="c" * 64,
                exported_at=now - timedelta(hours=1, minutes=30),
                warning_codes=[],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            self._write_artifact(
                path=tmp / "aardvark_now.json",
                project_id="aardvark",
                profile="quiet",
                checksum="d" * 64,
                exported_at=now - timedelta(hours=1),
                warning_codes=[],
                drift_changed=True,
                drift_guardrail_triggered=False,
            )

            alphabetical_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-order",
                    "alphabetical",
                    "--bridge-alert-project-severity-override",
                    "aardvark=warn@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(alphabetical_proc.returncode, 0, msg=alphabetical_proc.stderr)
            alphabetical_text = str(alphabetical_proc.stdout or "")
            self.assertIn("Family Projects Order: `alphabetical`", alphabetical_text)
            self.assertIn(
                "policy_only:warn=[aardvark] error=[alpha] all=[aardvark, alpha]",
                alphabetical_text,
            )

            severity_order_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-order",
                    "severity_then_project",
                    "--bridge-alert-project-severity-override",
                    "aardvark=warn@policy_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(severity_order_proc.returncode, 0, msg=severity_order_proc.stderr)
            severity_order_text = str(severity_order_proc.stdout or "")
            self.assertIn("Family Projects Order: `severity_then_project`", severity_order_text)
            self.assertIn(
                "policy_only:warn=[aardvark] error=[alpha] all=[alpha, aardvark]",
                severity_order_text,
            )

    def test_bridge_script_family_project_count_order_modes(self) -> None:
        repo = self._repo_root()
        bridge_script = repo / "scripts" / "build_backfill_warning_bridge.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            seed_rows = [
                ("alpha", False, False, True, False),
                ("beta", False, False, False, True),
                ("gamma", False, False, False, True),
                ("delta", False, False, True, True),
            ]
            for project_id, prev_drift, prev_guardrail, now_drift, now_guardrail in seed_rows:
                prev_checksum = f"{project_id[0]}" * 64
                now_checksum = ("9" * 64) if now_drift else prev_checksum
                self._write_artifact(
                    path=tmp / f"{project_id}_prev.json",
                    project_id=project_id,
                    profile="quiet",
                    checksum=prev_checksum,
                    exported_at=now - timedelta(hours=2),
                    warning_codes=[],
                    drift_changed=prev_drift,
                    drift_guardrail_triggered=prev_guardrail,
                )
                self._write_artifact(
                    path=tmp / f"{project_id}_now.json",
                    project_id=project_id,
                    profile="quiet",
                    checksum=now_checksum,
                    exported_at=now - timedelta(hours=1),
                    warning_codes=[],
                    drift_changed=now_drift,
                    drift_guardrail_triggered=now_guardrail,
                )

            by_family_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_family",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(by_family_proc.returncode, 0, msg=by_family_proc.stderr)
            by_family_text = str(by_family_proc.stdout or "")
            self.assertIn("Family Projects Count Order: `by_family`", by_family_text)
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", by_family_text)
            self.assertIn("Family Projects Count Export Mode: `inline`", by_family_text)
            self.assertIn("Family Projects Count Table Style: `full`", by_family_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", by_family_text)
            self.assertIn("Family Projects Count Threshold Mode: `off`", by_family_text)
            self.assertIn(
                "Family Projects Counts: `policy_only:warn=0 error=2 all=2; guardrail_only:warn=0 error=3 all=3; both:warn=0 error=1 all=1`",
                by_family_text,
            )

            by_total_desc_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(by_total_desc_proc.returncode, 0, msg=by_total_desc_proc.stderr)
            by_total_desc_text = str(by_total_desc_proc.stdout or "")
            self.assertIn("Family Projects Count Order: `by_total_desc`", by_total_desc_text)
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", by_total_desc_text)
            self.assertIn("Family Projects Count Export Mode: `inline`", by_total_desc_text)
            self.assertIn("Family Projects Count Table Style: `full`", by_total_desc_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", by_total_desc_text)
            self.assertIn("Family Projects Count Threshold Mode: `off`", by_total_desc_text)
            self.assertIn(
                "Family Projects Counts: `guardrail_only:warn=0 error=3 all=3; policy_only:warn=0 error=2 all=2; both:warn=0 error=1 all=1`",
                by_total_desc_text,
            )

            min_all_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-min-all",
                    "3",
                    "--markdown-family-projects-count-threshold-mode",
                    "all_min",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(min_all_proc.returncode, 0, msg=min_all_proc.stderr)
            min_all_text = str(min_all_proc.stdout or "")
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", min_all_text)
            self.assertIn("Family Projects Count Export Mode: `inline`", min_all_text)
            self.assertIn("Family Projects Count Table Style: `full`", min_all_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", min_all_text)
            self.assertIn("Family Projects Count Threshold Mode: `all_min`", min_all_text)
            self.assertIn("Family Projects Count Min All: `3`", min_all_text)
            self.assertIn(
                "Family Projects Counts: `guardrail_only:warn=0 error=3 all=3`",
                min_all_text,
            )

            min_all_off_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-min-all",
                    "3",
                    "--markdown-family-projects-count-threshold-mode",
                    "off",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(min_all_off_proc.returncode, 0, msg=min_all_off_proc.stderr)
            min_all_off_text = str(min_all_off_proc.stdout or "")
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", min_all_off_text)
            self.assertIn("Family Projects Count Export Mode: `inline`", min_all_off_text)
            self.assertIn("Family Projects Count Table Style: `full`", min_all_off_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", min_all_off_text)
            self.assertIn("Family Projects Count Threshold Mode: `off`", min_all_off_text)
            self.assertIn(
                "Family Projects Counts: `guardrail_only:warn=0 error=3 all=3; policy_only:warn=0 error=2 all=2; both:warn=0 error=1 all=1`",
                min_all_off_text,
            )

            top_n_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-top-n",
                    "2",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(top_n_proc.returncode, 0, msg=top_n_proc.stderr)
            top_n_text = str(top_n_proc.stdout or "")
            self.assertIn("Family Projects Count Top N: `2`", top_n_text)
            self.assertIn("Family Projects Count Render Mode: `full_fields`", top_n_text)
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", top_n_text)
            self.assertIn("Family Projects Count Export Mode: `inline`", top_n_text)
            self.assertIn("Family Projects Count Table Style: `full`", top_n_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", top_n_text)
            self.assertIn("Family Projects Count Rows: `shown=2 total=3 omitted=1`", top_n_text)
            self.assertIn(
                "Family Projects Counts: `guardrail_only:warn=0 error=3 all=3; policy_only:warn=0 error=2 all=2`",
                top_n_text,
            )

            nonzero_render_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-render-mode",
                    "nonzero_buckets",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(nonzero_render_proc.returncode, 0, msg=nonzero_render_proc.stderr)
            nonzero_render_text = str(nonzero_render_proc.stdout or "")
            self.assertIn("Family Projects Count Render Mode: `nonzero_buckets`", nonzero_render_text)
            self.assertIn("Family Projects Count Visibility Mode: `all_rows`", nonzero_render_text)
            self.assertIn("Family Projects Count Export Mode: `inline`", nonzero_render_text)
            self.assertIn("Family Projects Count Table Style: `full`", nonzero_render_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", nonzero_render_text)
            self.assertIn(
                "Family Projects Counts: `guardrail_only:error=3 all=3; policy_only:error=2 all=2; both:error=1 all=1`",
                nonzero_render_text,
            )

            table_export_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_proc.returncode, 0, msg=table_export_proc.stderr)
            table_export_text = str(table_export_proc.stdout or "")
            self.assertIn("Family Projects Count Export Mode: `table`", table_export_text)
            self.assertIn("Family Projects Count Table Style: `full`", table_export_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", table_export_text)
            self.assertIn("Family Projects Count Table Family Label Mode: `raw`", table_export_text)
            self.assertIn("Family Projects Count Table Header Label Mode: `title`", table_export_text)
            self.assertIn("Family Projects Count Table Family Label Overrides: `none`", table_export_text)
            self.assertIn("Family Projects Count Table Metric Label Mode: `title`", table_export_text)
            self.assertIn("Family Projects Count Table Metric Label Overrides: `none`", table_export_text)
            self.assertIn("Family Projects Count Table Row Order Mode: `count_order`", table_export_text)
            self.assertIn("Family Projects Count Table Include Schema Signature: `False`", table_export_text)
            self.assertIn("Family Projects Count Inline Family Label Mode: `raw`", table_export_text)
            self.assertIn("Family Projects Count Inline Bucket Label Mode: `raw`", table_export_text)
            self.assertIn("Family Projects Count Label Override Diagnostics: `False`", table_export_text)
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Counters: `resolved_family_count=0 resolved_metric_count=0 family_malformed_count=0 metric_malformed_count=0`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity Mode: `off`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Triggered: `False`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity: `none`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON: `False`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Mode: `full`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Key Prefix Mode: `bridge_`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Profile: `compact_min`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_only`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                table_export_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                table_export_text,
            )
            self.assertIn("Family Projects Count Label Override CI Policy Mode: `off`", table_export_text)
            self.assertIn(
                "Family Projects Count Label Override Fail CI Recommended: `False`",
                table_export_text,
            )
            self.assertIn("- Family Projects Counts Table:", table_export_text)
            self.assertIn("| Family | Warn | Error | All |", table_export_text)
            self.assertIn("| guardrail_only | 0 | 3 | 3 |", table_export_text)
            self.assertIn("| policy_only | 0 | 2 | 2 |", table_export_text)
            self.assertIn("| both | 0 | 1 | 1 |", table_export_text)

            table_export_row_order_canonical_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-row-order-mode",
                    "canonical",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_row_order_canonical_proc.returncode,
                0,
                msg=table_export_row_order_canonical_proc.stderr,
            )
            table_export_row_order_canonical_text = str(table_export_row_order_canonical_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Table Row Order Mode: `canonical`",
                table_export_row_order_canonical_text,
            )
            self.assertLess(
                table_export_row_order_canonical_text.index("| policy_only | 0 | 2 | 2 |"),
                table_export_row_order_canonical_text.index("| guardrail_only | 0 | 3 | 3 |"),
            )

            table_export_row_order_sorted_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-row-order-mode",
                    "sorted",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_row_order_sorted_proc.returncode,
                0,
                msg=table_export_row_order_sorted_proc.stderr,
            )
            table_export_row_order_sorted_text = str(table_export_row_order_sorted_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Table Row Order Mode: `sorted`",
                table_export_row_order_sorted_text,
            )
            self.assertLess(
                table_export_row_order_sorted_text.index("| both | 0 | 1 | 1 |"),
                table_export_row_order_sorted_text.index("| guardrail_only | 0 | 3 | 3 |"),
            )

            table_export_schema_signature_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-include-schema-signature",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_schema_signature_proc.returncode,
                0,
                msg=table_export_schema_signature_proc.stderr,
            )
            table_export_schema_signature_text = str(table_export_schema_signature_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Table Include Schema Signature: `True`",
                table_export_schema_signature_text,
            )
            self.assertIn("Family Projects Counts Table Schema Signature:", table_export_schema_signature_text)
            self.assertIn("\"columns\":[\"family\",\"warn\",\"error\",\"all\"]", table_export_schema_signature_text)
            self.assertIn("\"headers\":[\"Family\",\"Warn\",\"Error\",\"All\"]", table_export_schema_signature_text)

            table_export_minimal_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-style",
                    "minimal",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_minimal_proc.returncode, 0, msg=table_export_minimal_proc.stderr)
            table_export_minimal_text = str(table_export_minimal_proc.stdout or "")
            self.assertIn("Family Projects Count Table Style: `minimal`", table_export_minimal_text)
            self.assertIn("Family Projects Count Table Empty Mode: `inline_none`", table_export_minimal_text)
            self.assertIn("| Family | Error | All |", table_export_minimal_text)
            self.assertNotIn("| Family | Warn | Error | All |", table_export_minimal_text)
            self.assertIn("| guardrail_only | 3 | 3 |", table_export_minimal_text)
            self.assertIn("| policy_only | 2 | 2 |", table_export_minimal_text)
            self.assertIn("| both | 1 | 1 |", table_export_minimal_text)

            table_export_title_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-family-label-mode",
                    "title",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_title_proc.returncode, 0, msg=table_export_title_proc.stderr)
            table_export_title_text = str(table_export_title_proc.stdout or "")
            self.assertIn("Family Projects Count Export Mode: `table`", table_export_title_text)
            self.assertIn("Family Projects Count Table Family Label Mode: `title`", table_export_title_text)
            self.assertIn("| Family | Warn | Error | All |", table_export_title_text)
            self.assertIn("| Guardrail Only | 0 | 3 | 3 |", table_export_title_text)
            self.assertIn("| Policy Only | 0 | 2 | 2 |", table_export_title_text)
            self.assertIn("| Both | 0 | 1 | 1 |", table_export_title_text)
            self.assertNotIn("| guardrail_only | 0 | 3 | 3 |", table_export_title_text)

            table_export_header_raw_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-header-label-mode",
                    "raw",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_header_raw_proc.returncode, 0, msg=table_export_header_raw_proc.stderr)
            table_export_header_raw_text = str(table_export_header_raw_proc.stdout or "")
            self.assertIn("Family Projects Count Table Header Label Mode: `raw`", table_export_header_raw_text)
            self.assertIn("| family | Warn | Error | All |", table_export_header_raw_text)
            self.assertNotIn("| Family | Warn | Error | All |", table_export_header_raw_text)

            table_export_override_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,guardrail_only=Guardrail Lane,both=Cross Lane",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_override_proc.returncode, 0, msg=table_export_override_proc.stderr)
            table_export_override_text = str(table_export_override_proc.stdout or "")
            self.assertIn("Family Projects Count Table Family Label Overrides:", table_export_override_text)
            self.assertIn("policy_only=Policy Lane", table_export_override_text)
            self.assertIn("guardrail_only=Guardrail Lane", table_export_override_text)
            self.assertIn("both=Cross Lane", table_export_override_text)
            self.assertIn("| Guardrail Lane | 0 | 3 | 3 |", table_export_override_text)
            self.assertIn("| Policy Lane | 0 | 2 | 2 |", table_export_override_text)
            self.assertIn("| Cross Lane | 0 | 1 | 1 |", table_export_override_text)
            self.assertNotIn("| guardrail_only | 0 | 3 | 3 |", table_export_override_text)

            table_export_metric_raw_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-metric-label-mode",
                    "raw",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_metric_raw_proc.returncode, 0, msg=table_export_metric_raw_proc.stderr)
            table_export_metric_raw_text = str(table_export_metric_raw_proc.stdout or "")
            self.assertIn("Family Projects Count Table Metric Label Mode: `raw`", table_export_metric_raw_text)
            self.assertIn("| Family | warn | error | all |", table_export_metric_raw_text)
            self.assertNotIn("| Family | Warn | Error | All |", table_export_metric_raw_text)

            table_export_metric_override_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=Critical,all=Total",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_metric_override_proc.returncode,
                0,
                msg=table_export_metric_override_proc.stderr,
            )
            table_export_metric_override_text = str(table_export_metric_override_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Table Metric Label Overrides:",
                table_export_metric_override_text,
            )
            self.assertIn("warn=Warning", table_export_metric_override_text)
            self.assertIn("error=Critical", table_export_metric_override_text)
            self.assertIn("all=Total", table_export_metric_override_text)
            self.assertIn("| Family | Warning | Critical | Total |", table_export_metric_override_text)
            self.assertNotIn("| Family | Warn | Error | All |", table_export_metric_override_text)

            table_export_inline_title_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-order",
                    "by_total_desc",
                    "--markdown-family-projects-count-inline-family-label-mode",
                    "title",
                    "--markdown-family-projects-count-inline-bucket-label-mode",
                    "title",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_inline_title_proc.returncode, 0, msg=table_export_inline_title_proc.stderr)
            table_export_inline_title_text = str(table_export_inline_title_proc.stdout or "")
            self.assertIn("Family Projects Count Inline Family Label Mode: `title`", table_export_inline_title_text)
            self.assertIn("Family Projects Count Inline Bucket Label Mode: `title`", table_export_inline_title_text)
            self.assertIn(
                "Family Projects Counts: `Guardrail Only:Warn=0 Error=3 All=3; Policy Only:Warn=0 Error=2 All=2; Both:Warn=0 Error=1 All=1`",
                table_export_inline_title_text,
            )

            table_export_diag_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics",
                    "--markdown-family-projects-count-label-override-diagnostics-severity",
                    "warn",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_diag_proc.returncode, 0, msg=table_export_diag_proc.stderr)
            table_export_diag_text = str(table_export_diag_proc.stdout or "")
            self.assertIn("Family Projects Count Label Override Diagnostics: `True`", table_export_diag_text)
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Counters: `resolved_family_count=1 resolved_metric_count=1 family_malformed_count=3 metric_malformed_count=2`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity Mode: `warn`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Triggered: `True`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics Severity: `warn`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON: `True`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Mode: `full`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Key Prefix Mode: `bridge_`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Profile: `compact_min`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_only`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override CI Policy Mode: `strict`",
                table_export_diag_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Fail CI Recommended: `True`",
                table_export_diag_text,
            )
            self.assertIn("Family Projects Count Label Override Diagnostics Detail:", table_export_diag_text)
            self.assertIn("Family Projects Count Label Override Diagnostics JSON Detail:", table_export_diag_text)
            self.assertIn("\"ci_policy_mode\":\"strict\"", table_export_diag_text)
            self.assertIn("\"fail_ci_recommended\":true", table_export_diag_text)
            self.assertIn("\"family_malformed_count\":3", table_export_diag_text)
            self.assertIn("\"metric_malformed_count\":2", table_export_diag_text)
            self.assertIn("\"json_compact_token_sort_mode\":\"input_order\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_max_per_scope\":0", table_export_diag_text)
            self.assertIn("\"json_compact_token_overflow_suffix\":\"+{omitted} more\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_overflow_suffix_mode\":\"include\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_omitted_count_visibility_mode\":\"always\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_list_guard_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_list_key_mode\":\"always\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_scope_fallback_mode\":\"selected_only\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_truncation_indicator_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_scope_priority_mode\":\"family_first\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_fallback_emission_mode\":\"first_success_only\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_fallback_source_marker_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_fallback_source_marker_activation_mode\":\"always\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_selected_scope_marker_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_key_naming_mode\":\"default\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_suppression_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_summary_visibility_mode\":\"always\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_scope_order_mode\":\"priority\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_list_visibility_mode\":\"always\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_key_prefix_mode\":\"inherit\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_boolean_type_visibility_mode\":\"all\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_summary_list_order_mode\":\"insertion\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_summary_family_visibility_mode\":\"all\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_per_scope_family_visibility_mode\":\"all\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_summary_boolean_family_visibility_mode\":\"all\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_profile_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_profile_mode_source\":\"baseline_default\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_key_naming_mode_source\":\"baseline_default\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_marker_summary_boolean_family_visibility_mode_source\":\"baseline_default\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_scope_mode\":\"both\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_dedup_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_normalization_mode\":\"preserve\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_sanitization_mode\":\"off\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_sanitization_replacement_char\":\"_\"", table_export_diag_text)
            self.assertIn("\"json_compact_token_min_length\":1", table_export_diag_text)
            self.assertIn("\"severity_mode\":\"warn\"", table_export_diag_text)
            self.assertIn("\"triggered\":true", table_export_diag_text)
            self.assertIn("badtoken", table_export_diag_text)
            self.assertIn("unknown=Nope", table_export_diag_text)
            self.assertIn("both=", table_export_diag_text)
            self.assertIn("error=", table_export_diag_text)
            self.assertIn("noeq", table_export_diag_text)
            self.assertIn("| Policy Lane | 0 | 2 | 2 |", table_export_diag_text)

            table_export_diag_compact_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_diag_compact_proc.returncode, 0, msg=table_export_diag_compact_proc.stderr)
            table_export_diag_compact_text = str(table_export_diag_compact_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON: `True`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Mode: `compact`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Key Prefix Mode: `count_override_`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Profile: `compact_min`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_only`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_profile\":\"compact_min\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_include_mode\":\"counts_only\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_sort_mode\":\"input_order\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_max_per_scope\":0",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix\":\"+{omitted} more\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix_mode\":\"include\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"always\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"off\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"always\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_mode\":\"both\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_dedup_mode\":\"off\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_replacement_char\":\"_\"",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_compact_token_min_length\":1",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_diagnostics_enabled\":false",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_fail_ci_recommended\":true",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_family_malformed_count\":3",
                table_export_diag_compact_text,
            )
            self.assertIn(
                "\"count_override_metric_malformed_count\":2",
                table_export_diag_compact_text,
            )
            self.assertNotIn("\"counters\":{", table_export_diag_compact_text)
            self.assertNotIn("\"family\":{", table_export_diag_compact_text)
            self.assertNotIn("\"metric\":{", table_export_diag_compact_text)

            table_export_diag_compact_none_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "none",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_none_proc.returncode,
                0,
                msg=table_export_diag_compact_none_proc.stderr,
            )
            table_export_diag_compact_none_text = str(table_export_diag_compact_none_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `none`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `input_order`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `0`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `+{omitted} more`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `include`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `off`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `always`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `both`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `off`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `_`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `1`",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_include_mode\":\"none\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_sort_mode\":\"input_order\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_max_per_scope\":0",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix\":\"+{omitted} more\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix_mode\":\"include\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"always\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"off\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"always\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_mode\":\"both\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_dedup_mode\":\"off\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_replacement_char\":\"_\"",
                table_export_diag_compact_none_text,
            )
            self.assertIn(
                "\"count_override_compact_token_min_length\":1",
                table_export_diag_compact_none_text,
            )
            self.assertNotIn("\"count_override_resolved_family_count\":", table_export_diag_compact_none_text)
            self.assertNotIn("\"count_override_family_malformed_count\":", table_export_diag_compact_none_text)
            self.assertNotIn("\"count_override_family_malformed_tokens\":", table_export_diag_compact_none_text)
            self.assertNotIn("\"count_override_metric_malformed_tokens\":", table_export_diag_compact_none_text)
            self.assertIn("\"count_override_fail_ci_recommended\":true", table_export_diag_compact_none_text)

            table_export_diag_compact_limited_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-profile",
                    "compact_full",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens_if_truncated",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sort-mode",
                    "lexicographic",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-max-per-scope",
                    "2",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix",
                    "[+{omitted}]",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix-mode",
                    "suppress",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
                    "off",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
                    "require_nonempty_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_truncated",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "summary_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode",
                    "all_eligible",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
                    "off",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode",
                    "if_true_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode",
                    "priority",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-list-visibility-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
                    "lexicographic",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode",
                    "all",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-boolean-family-visibility-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "off",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "family_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-dedup-mode",
                    "on",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-normalization-mode",
                    "lower",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-mode",
                    "ascii_safe",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-replacement-char",
                    "-",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "3",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,BådToken,badtoken,Unknown=Nope,both=,BADTOKEN",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,ERROR=,noeq,error=,NÖEQ",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_limited_proc.returncode,
                0,
                msg=table_export_diag_compact_limited_proc.stderr,
            )
            table_export_diag_compact_limited_text = str(table_export_diag_compact_limited_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Include Mode: `counts_plus_tokens_if_truncated`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: `lexicographic`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: `2`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: `[+{omitted}]`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: `suppress`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `off`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `require_nonempty_tokens`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `if_truncated`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode: `summary_only`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Priority Mode: `metric_first`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Emission Mode: `all_eligible`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode: `summary`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode: `always`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode: `summary`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode: `short`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode: `off`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Visibility Mode: `if_true_only`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Scope Order Mode: `priority`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker List Visibility Mode: `if_nonempty`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Prefix Mode: `markers`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Boolean Type Visibility Mode: `selected_only`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary List Order Mode: `lexicographic`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Family Visibility Mode: `selected_only`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Per Scope Family Visibility Mode: `all`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Boolean Family Visibility Mode: `selected_only`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode: `off`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: `family_only`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: `on`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Normalization Mode: `lower`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Mode: `ascii_safe`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: `-`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: `3`",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_include_mode\":\"counts_plus_tokens_if_truncated\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_mode\":\"family_only\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_dedup_mode\":\"on\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_normalization_mode\":\"lower\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_mode\":\"ascii_safe\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_sanitization_replacement_char\":\"-\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_min_length\":3",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"off\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"require_nonempty_tokens\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"if_truncated\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_truncation_indicator_mode\":\"summary_only\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_priority_mode\":\"metric_first\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_emission_mode\":\"all_eligible\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_mode\":\"summary\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_activation_mode\":\"always\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_selected_scope_marker_mode\":\"summary\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_suppression_mode\":\"off\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_visibility_mode\":\"if_true_only\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode\":\"priority\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_list_visibility_mode\":\"if_nonempty\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode\":\"markers\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_boolean_type_visibility_mode\":\"selected_only\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_list_order_mode\":\"lexicographic\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_family_visibility_mode\":\"selected_only\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_per_scope_family_visibility_mode\":\"all\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode\":\"selected_only\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode\":\"off\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode_source\":\"baseline_default\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode_source\":\"explicit_input\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_marker_sel_used\":true",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_marker_sel_scopes\":[\"family\"]",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_marker_tokens_trunc\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_sel_used\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_sel_scopes\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_tokens_trunc\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_selected_source_used\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_selected_source_scopes\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_malformed_tokens_truncated\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_fallback_used\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_fallback_source_scopes\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_fb_scopes\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_marker_fb_scopes\":",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_compact_token_overflow_suffix_mode\":\"suppress\"",
                table_export_diag_compact_limited_text,
            )
            self.assertIn(
                "\"count_override_family_malformed_tokens\":[\"ba-dtoken\",\"badtoken\"]",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_family_malformed_tokens_omitted_count\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens\":",
                table_export_diag_compact_limited_text,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_omitted_count\":",
                table_export_diag_compact_limited_text,
            )

            table_export_diag_compact_marker_list_order_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
                    "lexicographic",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_marker_list_order_proc.returncode,
                0,
                msg=table_export_diag_compact_marker_list_order_proc.stderr,
            )
            table_export_diag_compact_marker_list_order_text = str(
                table_export_diag_compact_marker_list_order_proc.stdout or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary List Order Mode: `lexicographic`",
                table_export_diag_compact_marker_list_order_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_list_order_mode\":\"lexicographic\"",
                table_export_diag_compact_marker_list_order_text,
            )
            self.assertIn(
                "\"count_override_marker_sel_scopes\":[\"family\",\"metric\"]",
                table_export_diag_compact_marker_list_order_text,
            )
            self.assertNotIn(
                "\"count_override_marker_sel_scopes\":[\"metric\",\"family\"]",
                table_export_diag_compact_marker_list_order_text,
            )

            table_export_diag_compact_summary_family_visibility_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode",
                    "fallback_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
                    "lexicographic",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_summary_family_visibility_proc.returncode,
                0,
                msg=table_export_diag_compact_summary_family_visibility_proc.stderr,
            )
            table_export_diag_compact_summary_family_visibility_text = str(
                table_export_diag_compact_summary_family_visibility_proc.stdout or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Family Visibility Mode: `fallback_only`",
                table_export_diag_compact_summary_family_visibility_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_family_visibility_mode\":\"fallback_only\"",
                table_export_diag_compact_summary_family_visibility_text,
            )
            self.assertIn(
                "\"count_override_marker_sel_used\":true",
                table_export_diag_compact_summary_family_visibility_text,
            )
            self.assertNotIn(
                "\"count_override_marker_sel_scopes\":",
                table_export_diag_compact_summary_family_visibility_text,
            )

            table_export_diag_compact_per_scope_family_visibility_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
                    "markers",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
                    "all",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_per_scope_family_visibility_proc.returncode,
                0,
                msg=table_export_diag_compact_per_scope_family_visibility_proc.stderr,
            )
            table_export_diag_compact_per_scope_family_visibility_text = str(
                table_export_diag_compact_per_scope_family_visibility_proc.stdout or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Per Scope Family Visibility Mode: `selected_only`",
                table_export_diag_compact_per_scope_family_visibility_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_per_scope_family_visibility_mode\":\"selected_only\"",
                table_export_diag_compact_per_scope_family_visibility_text,
            )
            self.assertIn(
                "\"count_override_marker_family_sel_source\":true",
                table_export_diag_compact_per_scope_family_visibility_text,
            )
            self.assertIn(
                "\"count_override_marker_metric_sel_source\":true",
                table_export_diag_compact_per_scope_family_visibility_text,
            )
            self.assertNotIn(
                "\"count_override_marker_family_fb_source\":",
                table_export_diag_compact_per_scope_family_visibility_text,
            )
            self.assertNotIn(
                "\"count_override_marker_metric_fb_source\":",
                table_export_diag_compact_per_scope_family_visibility_text,
            )
            self.assertNotIn(
                "\"count_override_marker_family_tokens_trunc\":",
                table_export_diag_compact_per_scope_family_visibility_text,
            )
            self.assertNotIn(
                "\"count_override_marker_metric_tokens_trunc\":",
                table_export_diag_compact_per_scope_family_visibility_text,
            )

            table_export_diag_compact_marker_profile_strict_minimal_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_minimal",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_marker_profile_strict_minimal_proc.returncode,
                0,
                msg=table_export_diag_compact_marker_profile_strict_minimal_proc.stderr,
            )
            table_export_diag_compact_marker_profile_strict_minimal_text = str(
                table_export_diag_compact_marker_profile_strict_minimal_proc.stdout or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode: `strict_minimal`",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode\":\"strict_minimal\"",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode_source\":\"explicit_input\"",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode_source\":\"profile_default\"",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode\":\"markers\"",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode\":\"selected_only\"",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_boolean_family_visibility_mode_source\":\"profile_default\"",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_marker_sel_used\":true",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertIn(
                "\"count_override_marker_sel_scopes\":",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertNotIn(
                "\"count_override_marker_fb_used\":",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )
            self.assertNotIn(
                "\"count_override_marker_fb_scopes\":",
                table_export_diag_compact_marker_profile_strict_minimal_text,
            )

            table_export_diag_compact_marker_profile_strict_debug_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_debug",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_marker_profile_strict_debug_proc.returncode,
                0,
                msg=table_export_diag_compact_marker_profile_strict_debug_proc.stderr,
            )
            table_export_diag_compact_marker_profile_strict_debug_text = str(
                table_export_diag_compact_marker_profile_strict_debug_proc.stdout or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode: `strict_debug`",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode\":\"strict_debug\"",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_mode_source\":\"explicit_input\"",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode\":\"markers\"",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_prefix_mode_source\":\"profile_default\"",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode\":\"canonical\"",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode_source\":\"profile_default\"",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_marker_family_selected_source\":true",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_marker_metric_selected_source\":true",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_marker_family_fallback_source\":false",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_marker_metric_fallback_source\":false",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_marker_family_malformed_tokens_truncated\":",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )
            self.assertIn(
                "\"count_override_marker_metric_malformed_tokens_truncated\":",
                table_export_diag_compact_marker_profile_strict_debug_text,
            )

            table_export_diag_compact_sparse_guard_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "metric_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "99",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
                    "require_nonempty_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "selected_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "summary_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "always",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "summary",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
                    "omit_when_no_token_payload",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
                    "always",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_sparse_guard_proc.returncode,
                0,
                msg=table_export_diag_compact_sparse_guard_proc.stderr,
            )
            table_export_diag_compact_sparse_guard_text = str(
                table_export_diag_compact_sparse_guard_proc.stdout or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: `always`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: `require_nonempty_tokens`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `if_nonempty`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `selected_only`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode: `summary_only`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode: `summary`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode: `always`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode: `summary`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode: `short`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode: `omit_when_no_token_payload`",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_omitted_count_visibility_mode\":\"always\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_guard_mode\":\"require_nonempty_tokens\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"if_nonempty\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"selected_only\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_truncation_indicator_mode\":\"summary_only\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_mode\":\"summary\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_activation_mode\":\"always\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_selected_scope_marker_mode\":\"summary\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_suppression_mode\":\"omit_when_no_token_payload\"",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens\":",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_omitted_count\":",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertNotIn(
                "\"count_override_fb_used\":",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertNotIn(
                "\"count_override_fb_scopes\":",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertNotIn(
                "\"count_override_sel_used\":",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertNotIn(
                "\"count_override_sel_scopes\":",
                table_export_diag_compact_sparse_guard_text,
            )
            self.assertNotIn(
                "\"count_override_tokens_trunc\":",
                table_export_diag_compact_sparse_guard_text,
            )

            table_export_diag_compact_sparse_fallback_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "metric_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "8",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
                    "require_nonempty_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_nonempty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
                    "auto_expand_when_empty",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
                    "off",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
                    "metric_first",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode",
                    "all_eligible",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
                    "fallback_only",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
                    "per_scope",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
                    "short",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
                    "off",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode",
                    "always",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode",
                    "canonical",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
                    "both",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
                    "if_truncated",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
                    "1",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
                    "off",
                    "--markdown-family-projects-count-label-override-ci-policy-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken,unknown=Nope,both=",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(
                table_export_diag_compact_sparse_fallback_proc.returncode,
                0,
                msg=table_export_diag_compact_sparse_fallback_proc.stderr,
            )
            table_export_diag_compact_sparse_fallback_text = str(
                table_export_diag_compact_sparse_fallback_proc.stdout or ""
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: `if_truncated`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: `auto_expand_when_empty`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode: `off`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Scope Priority Mode: `metric_first`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Emission Mode: `all_eligible`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode: `per_scope`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode: `fallback_only`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode: `per_scope`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode: `short`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode: `off`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Visibility Mode: `always`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Scope Order Mode: `canonical`",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_list_key_mode\":\"if_truncated\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_fallback_mode\":\"auto_expand_when_empty\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_truncation_indicator_mode\":\"off\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_scope_priority_mode\":\"metric_first\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_emission_mode\":\"all_eligible\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_mode\":\"per_scope\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_fallback_source_marker_activation_mode\":\"fallback_only\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_selected_scope_marker_mode\":\"per_scope\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode\":\"short\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_suppression_mode\":\"off\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_summary_visibility_mode\":\"always\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_scope_order_mode\":\"canonical\"",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_family_malformed_tokens\":[\"badtoken\",\"unknown=Nope\",\"both=\"]",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_metric_malformed_tokens\":[\"error=\",\"noeq\"]",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_fb_used\":true",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_metric_fb_source\":true",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertIn(
                "\"count_override_family_fb_source\":true",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertLess(
                table_export_diag_compact_sparse_fallback_text.find("\"count_override_family_fb_source\":"),
                table_export_diag_compact_sparse_fallback_text.find("\"count_override_metric_fb_source\":"),
            )
            self.assertNotIn(
                "\"count_override_fallback_used\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_metric_fallback_source\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_family_fallback_source\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_selected_source_used\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_family_selected_source\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_metric_selected_source\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_truncated\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_family_malformed_tokens_truncated\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_family_malformed_tokens_omitted_count\":",
                table_export_diag_compact_sparse_fallback_text,
            )
            self.assertNotIn(
                "\"count_override_metric_malformed_tokens_omitted_count\":",
                table_export_diag_compact_sparse_fallback_text,
            )

            table_export_empty_proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-table-style",
                    "minimal",
                    "--markdown-family-projects-count-table-empty-mode",
                    "table_empty",
                    "--markdown-family-projects-count-top-n",
                    "0",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(table_export_empty_proc.returncode, 0, msg=table_export_empty_proc.stderr)
            table_export_empty_text = str(table_export_empty_proc.stdout or "")
            self.assertIn("Family Projects Count Export Mode: `table`", table_export_empty_text)
            self.assertIn("Family Projects Count Table Style: `minimal`", table_export_empty_text)
            self.assertIn("Family Projects Count Table Empty Mode: `table_empty`", table_export_empty_text)
            self.assertIn("- Family Projects Counts Table:", table_export_empty_text)
            self.assertIn("| Family | All |", table_export_empty_text)
            self.assertIn("| (none) | 0 |", table_export_empty_text)
            self.assertNotIn("Family Projects Counts: `none`", table_export_empty_text)

    def test_export_minimal_profile_limit_and_flags(self) -> None:
        module = self._load_export_script_module()
        payload = {
            "status": "ok",
            "project_id": "alpha",
            "profile_key": "nightly",
            "preset": "quick",
            "warning_policy_profile": "quiet",
            "warning_policy_checksum": "f" * 64,
            "warning_policy_config_source": "explicit",
            "warning_policy_config_path": "/tmp/policy.json",
            "exit_code_policy": "off",
            "exit_code": 0,
            "exit_triggered": False,
            "max_warning_severity": "warning",
            "warning_count": 3,
            "warning_codes": ["a", "b", "c"],
            "signal_summary": {"signals_count": 9, "candidate_unscanned_count": 2},
            "_audit": {
                "exported_at": "2026-04-18T00:00:00+00:00",
                "exit_code": 0,
                "effective_exit_code": 0,
                "policy_drift": {
                    "baseline_path": None,
                    "baseline_missing": False,
                    "require_baseline": False,
                    "changed": True,
                    "guardrail_triggered": False,
                    "drift_exit_code": 7,
                    "missing_baseline_exit_code": 8,
                    "changed_fields": ["warning_policy_checksum"],
                    "guardrail_violations": [],
                },
            },
        }
        minimal = module._build_minimal_export_payload(
            payload,
            warning_code_limit=2,
            omit_signal_summary=False,
            omit_policy_drift_differences=True,
        )
        self.assertEqual(str(minimal.get("_export_profile") or ""), "minimal")
        self.assertEqual(int(minimal.get("warning_code_count") or 0), 3)
        self.assertEqual(int(minimal.get("warning_code_limit") or 0), 2)
        self.assertTrue(bool(minimal.get("warning_codes_truncated")))
        self.assertEqual(list(minimal.get("warning_codes") or []), ["a", "b"])
        self.assertEqual(
            dict(minimal.get("signal_summary") or {}),
            {"signals_count": 9, "candidate_unscanned_count": 2},
        )
        drift = dict((minimal.get("_audit") or {}).get("policy_drift") or {})
        self.assertNotIn("changed_fields", drift)
        self.assertNotIn("guardrail_violations", drift)
        minimal_export = dict((minimal.get("_audit") or {}).get("minimal_export") or {})
        self.assertTrue(bool(minimal_export))
        self.assertEqual(int(minimal_export.get("warning_code_limit") or 0), 2)
        self.assertTrue(bool(minimal_export.get("warning_codes_truncated")))
        self.assertFalse(bool(minimal_export.get("omit_signal_summary")))
        self.assertTrue(bool(minimal_export.get("omit_policy_drift_differences")))

    def test_export_policy_core_projection_ignores_warning_noise(self) -> None:
        module = self._load_export_script_module()
        base_payload = {
            "warning_policy_profile": "quiet",
            "warning_policy_checksum": "f" * 64,
            "warning_policy_config_source": "explicit",
            "warning_policy_config_path": "/tmp/policy.json",
            "exit_code_policy": "off",
            "max_warning_severity": "none",
            "warning_codes": ["candidate_scan_clipped"],
            "warning_policy_resolution": {"profile": {"source": "profile.default"}},
        }
        noisy_payload = dict(base_payload)
        noisy_payload["warning_codes"] = ["source_counts_capped"]
        noisy_payload["max_warning_severity"] = "warning"
        noisy_payload["warning_policy_config_path"] = "/tmp/another-policy.json"

        base_full = module._extract_policy_projection(base_payload, profile="full")
        noisy_full = module._extract_policy_projection(noisy_payload, profile="full")
        self.assertNotEqual(base_full, noisy_full)

        base_core = module._extract_policy_projection(base_payload, profile="policy_core")
        noisy_core = module._extract_policy_projection(noisy_payload, profile="policy_core")
        self.assertEqual(base_core, noisy_core)

    def test_export_minimal_profile_zero_limit_and_omit_signal_summary(self) -> None:
        module = self._load_export_script_module()
        payload = {
            "status": "ok",
            "project_id": "alpha",
            "warning_codes": ["a", "b"],
            "signal_summary": {"signals_count": 2, "candidate_unscanned_count": 1},
            "_audit": {
                "exported_at": "2026-04-18T00:00:00+00:00",
                "exit_code": 0,
                "effective_exit_code": 0,
                "policy_drift": {},
            },
        }
        minimal = module._build_minimal_export_payload(
            payload,
            warning_code_limit=0,
            omit_signal_summary=True,
            omit_policy_drift_differences=False,
        )
        self.assertEqual(int(minimal.get("warning_code_count") or 0), 2)
        self.assertEqual(minimal.get("warning_code_limit"), 0)
        self.assertTrue(bool(minimal.get("warning_codes_truncated")))
        self.assertEqual(list(minimal.get("warning_codes") or []), [])
        self.assertNotIn("signal_summary", minimal)
        minimal_export = dict((minimal.get("_audit") or {}).get("minimal_export") or {})
        self.assertTrue(bool(minimal_export.get("omit_signal_summary")))
        self.assertFalse(bool(minimal_export.get("omit_policy_drift_differences")))

    def test_bridge_marker_precedence_export_modes_and_signature(self) -> None:
        repo = self._repo_root()
        bridge_script = repo / "scripts" / "build_backfill_warning_bridge.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="1" * 64,
                exported_at=now - timedelta(minutes=10),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            base_cmd = [
                sys.executable,
                str(bridge_script),
                "--input-dir",
                str(tmp),
                "--since-hours",
                "48",
                "--projection-profile",
                "policy_core",
                "--format",
                "markdown",
                "--markdown-include-family-projects",
                "--markdown-family-projects-mode",
                "counts_only",
                "--markdown-family-projects-source",
                "all_current",
                "--markdown-family-projects-count-export-mode",
                "table",
                "--markdown-family-projects-count-label-override-diagnostics-json",
                "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                "compact",
                "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                "count_override_",
                "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                "counts_plus_tokens",
                "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                "strict_minimal",
                "--markdown-family-projects-count-table-family-label-override",
                "policy_only=Policy Lane,badtoken",
                "--markdown-family-projects-count-table-metric-label-override",
                "warn=Warning,error=,noeq",
            ]

            full_proc = subprocess.run(
                base_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(full_proc.returncode, 0, msg=full_proc.stderr)
            full_text = str(full_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Precedence Export Mode: `full`",
                full_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_precedence_export_mode\":\"full\"",
                full_text,
            )
            self.assertRegex(
                full_text,
                r"\"count_override_compact_token_marker_profile_signature\":\"[0-9a-f]{64}\"",
            )
            self.assertIn(
                "\"count_override_compact_token_marker_key_naming_mode_source\":",
                full_text,
            )
            self.assertNotIn(
                "\"count_override_compact_token_marker_precedence_summary\":",
                full_text,
            )

            summary_only_proc = subprocess.run(
                base_cmd
                + [
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-precedence-export-mode",
                    "summary_only",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(summary_only_proc.returncode, 0, msg=summary_only_proc.stderr)
            summary_only_text = str(summary_only_proc.stdout or "")
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Precedence Export Mode: `summary_only`",
                summary_only_text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_precedence_export_mode\":\"summary_only\"",
                summary_only_text,
            )
            self.assertRegex(
                summary_only_text,
                r"\"count_override_compact_token_marker_profile_signature\":\"[0-9a-f]{64}\"",
            )
            self.assertIn(
                "\"count_override_compact_token_marker_precedence_summary\":\"",
                summary_only_text,
            )
            self.assertNotIn(
                "\"count_override_compact_token_marker_key_naming_mode_source\":",
                summary_only_text,
            )

    def test_summarize_bridge_marker_precedence_summary_only_passthrough(self) -> None:
        repo = self._repo_root()
        summarize_script = repo / "scripts" / "summarize_backfill_warning_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="2" * 64,
                exported_at=now - timedelta(minutes=5),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_minimal",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-precedence-export-mode",
                    "summary_only",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            bridge_markdown = str(payload.get("bridge_markdown") or "")
            self.assertIn(
                "Family Projects Count Label Override Diagnostics JSON Compact Token Marker Precedence Export Mode: `summary_only`",
                bridge_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_precedence_export_mode\":\"summary_only\"",
                bridge_markdown,
            )
            self.assertRegex(
                bridge_markdown,
                r"\"count_override_compact_token_marker_profile_signature\":\"[0-9a-f]{64}\"",
            )
            self.assertIn(
                "\"count_override_compact_token_marker_precedence_summary\":\"",
                bridge_markdown,
            )
            self.assertNotIn(
                "\"count_override_compact_token_marker_key_naming_mode_source\":",
                bridge_markdown,
            )

    def test_bridge_marker_profile_signature_drift_strict_mode(self) -> None:
        repo = self._repo_root()
        bridge_script = repo / "scripts" / "build_backfill_warning_bridge.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="3" * 64,
                exported_at=now - timedelta(minutes=10),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_minimal",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected",
                    "0000000000000000000000000000000000000000000000000000000000000000",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode",
                    "strict",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            text = str(proc.stdout or "")
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_expected\":\"0000000000000000000000000000000000000000000000000000000000000000\"",
                text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_match_mode\":\"strict\"",
                text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_match\":false",
                text,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_drift_detected\":true",
                text,
            )
            self.assertIn(
                "\"count_override_fail_ci_recommended\":true",
                text,
            )

    def test_summarize_bridge_marker_profile_signature_drift_passthrough(self) -> None:
        repo = self._repo_root()
        summarize_script = repo / "scripts" / "summarize_backfill_warning_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="4" * 64,
                exported_at=now - timedelta(minutes=5),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_minimal",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected",
                    "0000000000000000000000000000000000000000000000000000000000000000",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            bridge_markdown = str(payload.get("bridge_markdown") or "")
            bridge_markdown_telemetry = dict(payload.get("bridge_markdown_telemetry") or {})
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_match_mode\":\"strict\"",
                bridge_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_match\":false",
                bridge_markdown,
            )
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_drift_detected\":true",
                bridge_markdown,
            )
            self.assertTrue(
                bool(bridge_markdown_telemetry.get("marker_profile_signature_drift_detected"))
            )
            self.assertTrue(
                bool(
                    bridge_markdown_telemetry.get(
                        "marker_profile_signature_drift_exit_eligible"
                    )
                )
            )

    def test_bridge_marker_profile_signature_drift_exit_code(self) -> None:
        repo = self._repo_root()
        bridge_script = repo / "scripts" / "build_backfill_warning_bridge.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="5" * 64,
                exported_at=now - timedelta(minutes=10),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(bridge_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--projection-profile",
                    "policy_core",
                    "--format",
                    "markdown",
                    "--markdown-include-family-projects",
                    "--markdown-family-projects-mode",
                    "counts_only",
                    "--markdown-family-projects-source",
                    "all_current",
                    "--markdown-family-projects-count-export-mode",
                    "table",
                    "--markdown-family-projects-count-label-override-diagnostics-json",
                    "--markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_minimal",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected",
                    "0000000000000000000000000000000000000000000000000000000000000000",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode",
                    "strict",
                    "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code",
                    "27",
                    "--markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken",
                    "--markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 27, msg=proc.stderr)
            text = str(proc.stdout or "")
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_drift_detected\":true",
                text,
            )

    def test_summarize_bridge_marker_profile_signature_drift_exit_code(self) -> None:
        repo = self._repo_root()
        summarize_script = repo / "scripts" / "summarize_backfill_warning_audits.py"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            self._write_artifact(
                path=tmp / "alpha_now.json",
                project_id="alpha",
                profile="strict",
                checksum="6" * 64,
                exported_at=now - timedelta(minutes=5),
                warning_codes=["candidate_scan_clipped"],
                drift_changed=False,
                drift_guardrail_triggered=False,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(summarize_script),
                    "--input-dir",
                    str(tmp),
                    "--since-hours",
                    "48",
                    "--rollup-mode",
                    "dashboard",
                    "--include-bridge",
                    "--bridge-projection-profile",
                    "policy_core",
                    "--bridge-include-markdown",
                    "--bridge-markdown-include-family-projects",
                    "--bridge-markdown-family-projects-mode",
                    "counts_only",
                    "--bridge-markdown-family-projects-source",
                    "all_current",
                    "--bridge-markdown-family-projects-count-export-mode",
                    "table",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
                    "compact",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
                    "count_override_",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
                    "counts_plus_tokens",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
                    "strict_minimal",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected",
                    "0000000000000000000000000000000000000000000000000000000000000000",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode",
                    "strict",
                    "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code",
                    "31",
                    "--bridge-markdown-family-projects-count-table-family-label-override",
                    "policy_only=Policy Lane,badtoken",
                    "--bridge-markdown-family-projects-count-table-metric-label-override",
                    "warn=Warning,error=,noeq",
                    "--json-compact",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(repo),
            )
            self.assertEqual(proc.returncode, 31, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            bridge_markdown = str(payload.get("bridge_markdown") or "")
            bridge_markdown_telemetry = dict(payload.get("bridge_markdown_telemetry") or {})
            self.assertIn(
                "\"count_override_compact_token_marker_profile_signature_drift_detected\":true",
                bridge_markdown,
            )
            self.assertTrue(
                bool(
                    bridge_markdown_telemetry.get(
                        "marker_profile_signature_drift_exit_eligible"
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
