#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backfill_warning_bridge_alerts import (
    evaluate_bridge_alerts,
    normalize_optional_int,
    normalize_optional_rate,
    resolve_bridge_alert_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize exported backfill warning-audit artifacts.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("output") / "backfill_warning_audit",
        help="Directory containing warning-audit JSON files.",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Optional project_id filter.",
    )
    parser.add_argument(
        "--since-hours",
        type=int,
        default=24,
        help="Only include runs with exported_at within the last N hours.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit 0 with an empty summary when no artifacts match.",
    )
    parser.add_argument(
        "--json-compact",
        action="store_true",
        help="Emit compact JSON.",
    )
    parser.add_argument(
        "--rollup-mode",
        type=str,
        default="full",
        choices=("full", "dashboard"),
        help="Summary payload mode: full diagnostics or compact multi-project dashboard rollup.",
    )
    parser.add_argument(
        "--dashboard-alert-guardrail-triggered-count-threshold",
        type=int,
        default=None,
        help="Optional dashboard alert threshold for total guardrail-triggered runs in window.",
    )
    parser.add_argument(
        "--dashboard-alert-guardrail-triggered-rate-threshold",
        type=float,
        default=None,
        help="Optional dashboard alert threshold for guardrail-triggered run ratio in [0,1].",
    )
    parser.add_argument(
        "--dashboard-alert-policy-drift-changed-count-threshold",
        type=int,
        default=None,
        help="Optional dashboard alert threshold for total policy-drift-changed runs in window.",
    )
    parser.add_argument(
        "--dashboard-alert-policy-drift-changed-rate-threshold",
        type=float,
        default=None,
        help="Optional dashboard alert threshold for policy-drift-changed run ratio in [0,1].",
    )
    parser.add_argument(
        "--dashboard-alert-project-guardrail-triggered-count-threshold",
        type=int,
        default=None,
        help="Optional dashboard alert threshold for max per-project guardrail-triggered run count.",
    )
    parser.add_argument(
        "--include-bridge",
        action="store_true",
        help="Include bridge payload in summary output for single-command ops bundles.",
    )
    parser.add_argument(
        "--bridge-projection-profile",
        type=str,
        default="policy_core",
        choices=("full", "policy_core"),
        help="Projection profile used when --include-bridge is enabled.",
    )
    parser.add_argument(
        "--bridge-include-markdown",
        action="store_true",
        help="Include a markdown bridge briefing string when --include-bridge is enabled.",
    )
    parser.add_argument(
        "--bridge-markdown-max-projects",
        type=int,
        default=None,
        help="Optional project row cap for --bridge-include-markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-alert-compact",
        action="store_true",
        help="Emit compact bridge markdown alert lines when --bridge-include-markdown is enabled.",
    )
    parser.add_argument(
        "--bridge-markdown-triggered-rule-detail-max",
        type=int,
        default=None,
        help="Optional max triggered rule detail rows in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-hide-suppression-section",
        action="store_true",
        help="Hide suppression-focused markdown lines in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-include-family-projects",
        action="store_true",
        help="Include triggered family project listings in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-include-counts",
        action="store_true",
        help="Include per-family warn/error/all project counts in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-hide-empty-families",
        action="store_true",
        help="Hide family rows with zero all-project count in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-mode",
        type=str,
        default="full",
        choices=("full", "counts_only"),
        help="Family project markdown rendering mode for bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-source",
        type=str,
        default="triggered",
        choices=("triggered", "all_current", "triggered_or_current"),
        help="Family project listing source for bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-severity",
        type=str,
        default="all",
        choices=("all", "warn_only", "error_only"),
        help="Family project listing severity filter for bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-max-items",
        type=int,
        default=None,
        help=(
            "Optional max project ids per family/severity list in bridge markdown output. "
            "When capped, output includes (+N more)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-order",
        type=str,
        default="alphabetical",
        choices=("alphabetical", "severity_then_project"),
        help=(
            "Project ordering mode for family listings in bridge markdown output. "
            "severity_then_project orders all-lists as error then warn."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-order",
        type=str,
        default="by_family",
        choices=("by_family", "by_total_desc"),
        help="Ordering mode for family project count summaries in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-render-mode",
        type=str,
        default="full_fields",
        choices=("full_fields", "nonzero_buckets"),
        help="Rendering mode for family project count rows in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-visibility-mode",
        type=str,
        default="all_rows",
        choices=("all_rows", "nonzero_all"),
        help="Visibility mode for family project count rows in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-export-mode",
        type=str,
        default="inline",
        choices=("inline", "table"),
        help="Export mode for family project count output in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-style",
        type=str,
        default="full",
        choices=("full", "minimal"),
        help="Table style for family project count output when export mode is table.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-empty-mode",
        type=str,
        default="inline_none",
        choices=("inline_none", "table_empty"),
        help="Empty-row behavior for family project count table export.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-family-label-mode",
        type=str,
        default="raw",
        choices=("raw", "title"),
        help="Family label style for family project count table rows.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-header-label-mode",
        type=str,
        default="title",
        choices=("raw", "title"),
        help="Header label style for family project count table output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-family-label-override",
        action="append",
        default=None,
        help=(
            "Optional family label override in family=label format for bridge markdown table rows. "
            "Can be repeated or comma-separated."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-metric-label-mode",
        type=str,
        default="title",
        choices=("raw", "title"),
        help="Metric header-label style for family project count table output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-metric-label-override",
        action="append",
        default=None,
        help=(
            "Optional metric label override in metric=label format for bridge markdown table headers. "
            "Supported metrics: warn,error,all. Can be repeated or comma-separated."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-row-order-mode",
        type=str,
        default="count_order",
        choices=("count_order", "canonical", "sorted"),
        help=(
            "Row ordering mode for bridge markdown family project count table output. "
            "count_order follows count-order/top-n selection order."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-table-include-schema-signature",
        action="store_true",
        help="Include table schema signature trace line for bridge markdown family project count tables.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-inline-family-label-mode",
        type=str,
        default="raw",
        choices=("raw", "title"),
        help="Family label style for inline bridge markdown family project count summaries.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-inline-bucket-label-mode",
        type=str,
        default="raw",
        choices=("raw", "title"),
        help="Bucket label style for inline bridge markdown family project count summaries.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics",
        action="store_true",
        help="Include diagnostics line for malformed family/metric table label override tokens.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-severity",
        type=str,
        default="off",
        choices=("off", "note", "warn"),
        help="Severity mode for malformed family/metric table label override diagnostics.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json",
        action="store_true",
        help="Include machine-readable JSON line for label override diagnostics resolution.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode",
        type=str,
        default="full",
        choices=("full", "compact"),
        help=(
            "JSON detail mode for label override diagnostics payloads. "
            "compact emits a single flat counters/status payload."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
        type=str,
        default="bridge_",
        choices=("bridge_", "count_override_"),
        help="Key-prefix mode for compact label override diagnostics JSON payload keys.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-profile",
        type=str,
        default="compact_min",
        choices=("compact_min", "compact_full"),
        help=(
            "Compact diagnostics JSON profile preset. "
            "compact_min emits status/counter keys; compact_full adds flat context keys."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
        type=str,
        default="counts_only",
        choices=(
            "none",
            "counts_only",
            "counts_plus_tokens",
            "counts_plus_tokens_if_truncated",
        ),
        help="Compact diagnostics JSON include mode for malformed-token visibility.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sort-mode",
        type=str,
        default="input_order",
        choices=("input_order", "lexicographic"),
        help="Compact diagnostics malformed-token sorting mode.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-max-per-scope",
        type=int,
        default=0,
        help=(
            "Compact diagnostics max malformed tokens per scope (family/metric). "
            "0 keeps all tokens."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix",
        type=str,
        default="+{omitted} more",
        help=(
            "Compact diagnostics overflow suffix appended when malformed-token lists are truncated. "
            "Supports {omitted} placeholder."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix-mode",
        type=str,
        default="include",
        choices=("include", "suppress"),
        help="Compact diagnostics overflow-suffix emission mode when malformed-token lists are truncated.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
        type=str,
        default="always",
        choices=("always", "if_truncated_only", "off"),
        help="Compact diagnostics omitted-count key visibility mode for malformed-token scopes.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
        type=str,
        default="off",
        choices=("off", "require_nonempty_tokens"),
        help="Compact diagnostics malformed-token list guard mode for sparse/empty token scopes.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
        type=str,
        default="always",
        choices=("always", "if_nonempty", "if_truncated"),
        help="Compact diagnostics malformed-token list key emission mode.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
        type=str,
        default="selected_only",
        choices=("selected_only", "auto_expand_when_empty"),
        help="Compact diagnostics malformed-token scope fallback mode when selected scopes emit no token keys.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
        type=str,
        default="off",
        choices=("off", "summary_only", "per_scope"),
        help=(
            "Compact diagnostics token-list truncation indicator mode for strict sinks without token payload expansion."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
        type=str,
        default="family_first",
        choices=("family_first", "metric_first"),
        help="Compact diagnostics token-scope priority mode used by auto-expand fallback ordering.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode",
        type=str,
        default="first_success_only",
        choices=("first_success_only", "all_eligible"),
        help=(
            "Compact diagnostics fallback emission mode for auto-expand scope fallback "
            "(first emitted scope only or all eligible scopes)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
        type=str,
        default="off",
        choices=("off", "summary", "per_scope"),
        help=(
            "Compact diagnostics fallback-source marker mode for strict sinks "
            "(summary or per-scope markers)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
        type=str,
        default="always",
        choices=("always", "fallback_only"),
        help=(
            "Compact diagnostics fallback-source marker activation mode "
            "(always emit marker keys, or only when fallback contributes source scopes)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
        type=str,
        default="off",
        choices=("off", "summary", "per_scope"),
        help=(
            "Compact diagnostics selected-scope marker mode for traceability when "
            "selected scopes satisfy token emission and fallback is bypassed."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
        type=str,
        default="default",
        choices=("default", "short"),
        help=(
            "Compact diagnostics marker key naming mode "
            "(default verbose names or short aliases for strict sinks)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
        type=str,
        default="off",
        choices=("off", "omit_when_no_token_payload"),
        help=(
            "Compact diagnostics marker suppression mode "
            "(optionally omit marker metadata when token payload keys are absent)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode",
        type=str,
        default="always",
        choices=("always", "if_true_only"),
        help=(
            "Compact diagnostics marker summary visibility mode for boolean marker keys "
            "(always include booleans or only include when true)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode",
        type=str,
        default="priority",
        choices=("canonical", "priority"),
        help=(
            "Compact diagnostics marker per-scope key order mode "
            "(canonical family/metric order or active scope-priority order)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-list-visibility-mode",
        type=str,
        default="always",
        choices=("always", "if_nonempty"),
        help=(
            "Compact diagnostics marker summary-list visibility mode "
            "(always emit summary list markers, or only when non-empty)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
        type=str,
        default="inherit",
        choices=("inherit", "markers"),
        help=(
            "Compact diagnostics marker key-prefix mode "
            "(inherit base compact key prefix or isolate marker keys under a marker_ branch)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
        type=str,
        default="all",
        choices=(
            "all",
            "fallback_only",
            "selected_only",
            "truncation_only",
            "fallback_selected",
            "fallback_truncation",
            "selected_truncation",
            "none",
        ),
        help=(
            "Compact diagnostics marker boolean-type visibility mode "
            "(control fallback/selected/truncation boolean marker families independently)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
        type=str,
        default="insertion",
        choices=("insertion", "lexicographic"),
        help=(
            "Compact diagnostics summary-list marker ordering mode "
            "(insertion order or lexicographic)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode",
        type=str,
        default="all",
        choices=("all", "fallback_only", "selected_only", "none"),
        help=(
            "Compact diagnostics summary-list marker family visibility mode "
            "(control fallback vs selected summary list markers)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode",
        type=str,
        default="all",
        choices=("all", "fallback_only", "selected_only", "truncation_only", "none"),
        help=(
            "Compact diagnostics per-scope marker family visibility mode "
            "(control fallback/selected/truncation per-scope marker keys)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-boolean-family-visibility-mode",
        type=str,
        default="all",
        choices=("all", "fallback_only", "selected_only", "truncation_only", "none"),
        help=(
            "Compact diagnostics summary-boolean marker family visibility mode "
            "(control fallback/selected/truncation summary booleans)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
        type=str,
        default="off",
        choices=("off", "strict_minimal", "strict_verbose", "strict_debug"),
        help=(
            "Compact diagnostics marker profile shortcut mode "
            "(apply coherent marker visibility/order defaults for strict sinks)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected",
        type=str,
        default="",
        help=(
            "Optional expected marker profile signature (64 hex chars) for compact diagnostics drift checks."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode",
        type=str,
        default="off",
        choices=("off", "warn", "strict"),
        help=(
            "Compact diagnostics marker profile signature match mode "
            "(off reports metadata only, warn surfaces drift, strict also marks fail_ci_recommended)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code",
        type=int,
        default=0,
        help=(
            "Optional non-zero exit code when strict marker-profile signature drift is detected "
            "in bundled bridge markdown output. 0 disables."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-precedence-export-mode",
        type=str,
        default="full",
        choices=("full", "summary_only"),
        help=(
            "Compact diagnostics marker precedence export mode "
            "(full per-control source keys or condensed source-count summary)."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
        type=str,
        default="both",
        choices=("family_only", "metric_only", "both"),
        help="Compact diagnostics malformed-token scope mode.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-dedup-mode",
        type=str,
        default="off",
        choices=("off", "on"),
        help="Compact diagnostics malformed-token dedup mode before sort/truncation.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-normalization-mode",
        type=str,
        default="preserve",
        choices=("preserve", "lower"),
        help="Compact diagnostics malformed-token normalization mode.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-mode",
        type=str,
        default="off",
        choices=("off", "ascii_safe"),
        help="Compact diagnostics malformed-token sanitization mode.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-replacement-char",
        type=str,
        default="_",
        help=(
            "Compact diagnostics replacement characters used for non-ASCII/non-printable chars "
            "when sanitization mode is ascii_safe."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
        type=int,
        default=1,
        help=(
            "Compact diagnostics minimum malformed-token length after normalization/sanitization. "
            "Tokens shorter than this are dropped."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-label-override-ci-policy-mode",
        type=str,
        default="off",
        choices=("off", "strict"),
        help=(
            "Optional CI recommendation mode for malformed family/metric label override tokens. "
            "strict emits fail_ci_recommended=True when malformed tokens are present."
        ),
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-min-all",
        type=int,
        default=0,
        help="Optional minimum all-count required for a family to appear in bridge markdown count summaries.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-threshold-mode",
        type=str,
        default="off",
        choices=("off", "all_min"),
        help="Count threshold mode for family project count summaries in bridge markdown output.",
    )
    parser.add_argument(
        "--bridge-markdown-family-projects-count-top-n",
        type=int,
        default=None,
        help="Optional max number of family rows in bridge markdown count summaries (counts-only mode).",
    )
    parser.add_argument(
        "--bridge-alert-policy-drift-count-threshold",
        type=int,
        default=None,
        help="Optional bridge alert threshold for projects_with_policy_drift (count).",
    )
    parser.add_argument(
        "--bridge-alert-policy-drift-rate-threshold",
        type=float,
        default=None,
        help="Optional bridge alert threshold for projects_with_policy_drift/projects_with_previous in [0,1].",
    )
    parser.add_argument(
        "--bridge-alert-guardrail-count-threshold",
        type=int,
        default=None,
        help="Optional bridge alert threshold for projects_with_guardrail_triggered (count).",
    )
    parser.add_argument(
        "--bridge-alert-guardrail-rate-threshold",
        type=float,
        default=None,
        help="Optional bridge alert threshold for projects_with_guardrail_triggered/project_count in [0,1].",
    )
    parser.add_argument(
        "--bridge-alert-policy-drift-severity",
        type=str,
        default="error",
        choices=("warn", "error"),
        help="Bridge severity tier for policy-drift rules.",
    )
    parser.add_argument(
        "--bridge-alert-guardrail-severity",
        type=str,
        default="error",
        choices=("warn", "error"),
        help="Bridge severity tier for guardrail rules.",
    )
    parser.add_argument(
        "--bridge-alert-project-severity-override",
        action="append",
        default=None,
        help=(
            "Optional per-project severity override in project_id=warn|error format. "
            "Optional scope suffix supported: @policy_only|@guardrail_only|@both. "
            "Can be repeated."
        ),
    )
    parser.add_argument(
        "--bridge-alert-suppress-rule",
        action="append",
        default=None,
        help=(
            "Optional rule suppression by rule name (for example policy_drift_count_threshold). "
            "Can be repeated or passed as comma-separated values."
        ),
    )
    parser.add_argument(
        "--bridge-alert-project-suppress-scope",
        action="append",
        default=None,
        help=(
            "Optional project-level suppression scope for rule matching in "
            "project_id@policy_only|guardrail_only|both format. Can be repeated."
        ),
    )
    parser.add_argument(
        "--bridge-alert-exit-code",
        type=int,
        default=12,
        help="Bridge exit-code value included in bundle alert metadata when error-tier rules trigger.",
    )
    return parser


def _load_json(path: Path) -> dict:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid_payload_type:{path}:expected_object")
    return parsed


def _parse_exported_at(payload: dict) -> datetime | None:
    audit = payload.get("_audit")
    if not isinstance(audit, dict):
        return None
    raw = str(audit.get("exported_at") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _iter_filtered_payloads(
    *,
    input_dir: Path,
    project_id: str | None,
    since_hours: int,
) -> list[tuple[Path, dict]]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=max(0, int(since_hours)))
    rows: list[tuple[Path, dict]] = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            payload = _load_json(path)
        except Exception:
            continue
        if project_id is not None:
            if str(payload.get("project_id") or "") != str(project_id):
                continue
        exported_at = _parse_exported_at(payload)
        if exported_at is not None and exported_at.tzinfo is None:
            exported_at = exported_at.replace(tzinfo=timezone.utc)
        if exported_at is not None and exported_at < window_start:
            continue
        rows.append((path, payload))
    return rows


def summarize(rows: list[tuple[Path, dict]]) -> dict:
    status_counter: Counter[str] = Counter()
    severity_counter: Counter[str] = Counter()
    checksum_counter: Counter[str] = Counter()
    warning_code_counter: Counter[str] = Counter()
    project_runs: dict[str, list[dict]] = defaultdict(list)

    drift_changed_count = 0
    drift_guardrail_triggered_count = 0
    total_warning_count = 0

    for path, payload in rows:
        status = str(payload.get("status") or "unknown")
        status_counter[status] += 1
        severity = str(payload.get("max_warning_severity") or "none")
        severity_counter[severity] += 1
        checksum = str(payload.get("warning_policy_checksum") or "")
        if checksum:
            checksum_counter[checksum] += 1
        warning_codes = payload.get("warning_codes")
        if isinstance(warning_codes, list):
            for code in warning_codes:
                normalized = str(code or "").strip()
                if normalized:
                    warning_code_counter[normalized] += 1
        warning_count = int(payload.get("warning_count") or 0)
        total_warning_count += max(0, warning_count)

        audit = payload.get("_audit")
        if isinstance(audit, dict):
            drift = audit.get("policy_drift")
            if isinstance(drift, dict):
                if bool(drift.get("changed")):
                    drift_changed_count += 1
                if bool(drift.get("guardrail_triggered")):
                    drift_guardrail_triggered_count += 1
        pid = str(payload.get("project_id") or "unknown")
        project_runs[pid].append(
            {
                "path": str(path.resolve()),
                "status": status,
                "warning_count": warning_count,
                "warning_policy_profile": str(payload.get("warning_policy_profile") or ""),
                "warning_policy_checksum": checksum,
                "warning_policy_config_source": str(payload.get("warning_policy_config_source") or ""),
                "exported_at": str((payload.get("_audit") or {}).get("exported_at") or ""),
            }
        )

    latest_per_project: dict[str, dict] = {}
    for pid, runs in project_runs.items():
        ordered = sorted(runs, key=lambda row: str(row.get("exported_at") or ""))
        latest_per_project[pid] = ordered[-1]

    return {
        "rollup_mode": "full",
        "total_runs": int(len(rows)),
        "status_counts": dict(sorted(status_counter.items())),
        "max_warning_severity_counts": dict(sorted(severity_counter.items())),
        "total_warning_count": int(total_warning_count),
        "warning_code_counts": dict(sorted(warning_code_counter.items())),
        "policy_checksum_unique_count": int(len(checksum_counter)),
        "policy_checksum_counts": dict(sorted(checksum_counter.items())),
        "policy_drift_changed_count": int(drift_changed_count),
        "policy_drift_guardrail_triggered_count": int(drift_guardrail_triggered_count),
        "latest_per_project": latest_per_project,
    }


def _dashboard_alerts_payload(
    *,
    total_runs: int,
    policy_drift_changed_count: int,
    guardrail_triggered_count: int,
    max_project_guardrail_triggered_count: int,
    thresholds: dict[str, int | float | None],
) -> dict:
    total = max(0, int(total_runs))
    drift_count = max(0, int(policy_drift_changed_count))
    guardrail_count = max(0, int(guardrail_triggered_count))
    max_project_guardrail = max(0, int(max_project_guardrail_triggered_count))
    drift_rate = (float(drift_count) / float(total)) if total > 0 else 0.0
    guardrail_rate = (float(guardrail_count) / float(total)) if total > 0 else 0.0

    rules: list[dict] = []

    count_threshold = thresholds.get("guardrail_triggered_count_threshold")
    if count_threshold is not None:
        threshold_value = int(count_threshold)
        rules.append(
            {
                "name": "guardrail_triggered_count_threshold",
                "metric": "guardrail_triggered_count",
                "actual": guardrail_count,
                "threshold": threshold_value,
                "triggered": bool(guardrail_count >= threshold_value),
            }
        )

    rate_threshold = thresholds.get("guardrail_triggered_rate_threshold")
    if rate_threshold is not None:
        threshold_value = float(rate_threshold)
        rules.append(
            {
                "name": "guardrail_triggered_rate_threshold",
                "metric": "guardrail_triggered_rate",
                "actual": guardrail_rate,
                "threshold": threshold_value,
                "triggered": bool(guardrail_rate >= threshold_value),
            }
        )

    drift_count_threshold = thresholds.get("policy_drift_changed_count_threshold")
    if drift_count_threshold is not None:
        threshold_value = int(drift_count_threshold)
        rules.append(
            {
                "name": "policy_drift_changed_count_threshold",
                "metric": "policy_drift_changed_count",
                "actual": drift_count,
                "threshold": threshold_value,
                "triggered": bool(drift_count >= threshold_value),
            }
        )

    drift_rate_threshold = thresholds.get("policy_drift_changed_rate_threshold")
    if drift_rate_threshold is not None:
        threshold_value = float(drift_rate_threshold)
        rules.append(
            {
                "name": "policy_drift_changed_rate_threshold",
                "metric": "policy_drift_changed_rate",
                "actual": drift_rate,
                "threshold": threshold_value,
                "triggered": bool(drift_rate >= threshold_value),
            }
        )

    project_guardrail_threshold = thresholds.get("project_guardrail_triggered_count_threshold")
    if project_guardrail_threshold is not None:
        threshold_value = int(project_guardrail_threshold)
        rules.append(
            {
                "name": "project_guardrail_triggered_count_threshold",
                "metric": "max_project_guardrail_triggered_count",
                "actual": max_project_guardrail,
                "threshold": threshold_value,
                "triggered": bool(max_project_guardrail >= threshold_value),
            }
        )

    triggered_rules = [str(rule.get("name") or "") for rule in rules if bool(rule.get("triggered"))]
    return {
        "enabled": bool(rules),
        "triggered": bool(triggered_rules),
        "triggered_rules": triggered_rules,
        "metrics": {
            "total_runs": total,
            "policy_drift_changed_count": drift_count,
            "policy_drift_changed_rate": drift_rate,
            "guardrail_triggered_count": guardrail_count,
            "guardrail_triggered_rate": guardrail_rate,
            "max_project_guardrail_triggered_count": max_project_guardrail,
        },
        "thresholds": {
            "guardrail_triggered_count_threshold": thresholds.get("guardrail_triggered_count_threshold"),
            "guardrail_triggered_rate_threshold": thresholds.get("guardrail_triggered_rate_threshold"),
            "policy_drift_changed_count_threshold": thresholds.get("policy_drift_changed_count_threshold"),
            "policy_drift_changed_rate_threshold": thresholds.get("policy_drift_changed_rate_threshold"),
            "project_guardrail_triggered_count_threshold": thresholds.get(
                "project_guardrail_triggered_count_threshold"
            ),
        },
        "rules": rules,
    }


def summarize_dashboard(rows: list[tuple[Path, dict]], *, alert_thresholds: dict[str, int | float | None]) -> dict:
    project_stats: dict[str, dict] = {}
    status_counter: Counter[str] = Counter()
    drift_changed_count = 0
    drift_guardrail_triggered_count = 0
    total_warning_count = 0

    for path, payload in rows:
        project_id = str(payload.get("project_id") or "unknown")
        status = str(payload.get("status") or "unknown")
        warning_count = max(0, int(payload.get("warning_count") or 0))
        exported_at = str((payload.get("_audit") or {}).get("exported_at") or "")
        checksum = str(payload.get("warning_policy_checksum") or "")
        profile = str(payload.get("warning_policy_profile") or "")
        config_source = str(payload.get("warning_policy_config_source") or "")

        status_counter[status] += 1
        total_warning_count += warning_count

        audit = payload.get("_audit")
        drift_changed = False
        drift_guardrail_triggered = False
        if isinstance(audit, dict):
            drift = audit.get("policy_drift")
            if isinstance(drift, dict):
                drift_changed = bool(drift.get("changed"))
                drift_guardrail_triggered = bool(drift.get("guardrail_triggered"))
        if drift_changed:
            drift_changed_count += 1
        if drift_guardrail_triggered:
            drift_guardrail_triggered_count += 1

        row = project_stats.get(project_id)
        if row is None:
            row = {
                "project_id": project_id,
                "run_count": 0,
                "total_warning_count": 0,
                "drift_changed_count": 0,
                "guardrail_triggered_count": 0,
                "latest_exported_at": "",
                "latest_status": "unknown",
                "latest_warning_count": 0,
                "latest_warning_policy_profile": "",
                "latest_warning_policy_checksum": "",
                "latest_warning_policy_config_source": "",
                "latest_path": "",
            }
            project_stats[project_id] = row

        row["run_count"] = int(row.get("run_count") or 0) + 1
        row["total_warning_count"] = int(row.get("total_warning_count") or 0) + warning_count
        if drift_changed:
            row["drift_changed_count"] = int(row.get("drift_changed_count") or 0) + 1
        if drift_guardrail_triggered:
            row["guardrail_triggered_count"] = int(row.get("guardrail_triggered_count") or 0) + 1

        latest_exported_at = str(row.get("latest_exported_at") or "")
        if exported_at >= latest_exported_at:
            row["latest_exported_at"] = exported_at
            row["latest_status"] = status
            row["latest_warning_count"] = warning_count
            row["latest_warning_policy_profile"] = profile
            row["latest_warning_policy_checksum"] = checksum
            row["latest_warning_policy_config_source"] = config_source
            row["latest_path"] = str(path.resolve())

    projects = [project_stats[key] for key in sorted(project_stats.keys())]
    max_project_guardrail_triggered_count = 0
    for row in projects:
        max_project_guardrail_triggered_count = max(
            max_project_guardrail_triggered_count,
            int(row.get("guardrail_triggered_count") or 0),
        )
    alerts = _dashboard_alerts_payload(
        total_runs=int(len(rows)),
        policy_drift_changed_count=int(drift_changed_count),
        guardrail_triggered_count=int(drift_guardrail_triggered_count),
        max_project_guardrail_triggered_count=int(max_project_guardrail_triggered_count),
        thresholds=alert_thresholds,
    )
    return {
        "rollup_mode": "dashboard",
        "total_runs": int(len(rows)),
        "project_count": int(len(projects)),
        "status_counts": dict(sorted(status_counter.items())),
        "policy_drift_changed_count": int(drift_changed_count),
        "policy_drift_guardrail_triggered_count": int(drift_guardrail_triggered_count),
        "total_warning_count": int(total_warning_count),
        "alerts": alerts,
        "projects": projects,
    }


def _empty_summary(rollup_mode: str, *, alert_thresholds: dict[str, int | float | None]) -> dict:
    if str(rollup_mode) == "dashboard":
        alerts = _dashboard_alerts_payload(
            total_runs=0,
            policy_drift_changed_count=0,
            guardrail_triggered_count=0,
            max_project_guardrail_triggered_count=0,
            thresholds=alert_thresholds,
        )
        return {
            "rollup_mode": "dashboard",
            "total_runs": 0,
            "project_count": 0,
            "status_counts": {},
            "policy_drift_changed_count": 0,
            "policy_drift_guardrail_triggered_count": 0,
            "total_warning_count": 0,
            "alerts": alerts,
            "projects": [],
        }
    return {
        "rollup_mode": "full",
        "total_runs": 0,
        "status_counts": {},
        "max_warning_severity_counts": {},
        "total_warning_count": 0,
        "warning_code_counts": {},
        "policy_checksum_unique_count": 0,
        "policy_checksum_counts": {},
        "policy_drift_changed_count": 0,
        "policy_drift_guardrail_triggered_count": 0,
        "latest_per_project": {},
    }


def _load_bridge_module():
    bridge_path = Path(__file__).resolve().with_name("build_backfill_warning_bridge.py")
    spec = importlib.util.spec_from_file_location("build_backfill_warning_bridge", bridge_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed_to_load_bridge_module:{bridge_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _build_bridge_bundle(
    *,
    input_dir: Path,
    args: argparse.Namespace,
) -> dict:
    bridge_module = _load_bridge_module()
    bridge_rows = bridge_module._iter_filtered_rows(
        input_dir=input_dir,
        project_id=args.project_id,
        since_hours=int(args.since_hours),
    )
    if bridge_rows:
        bridge_payload = bridge_module.build_bridge_payload(
            bridge_rows,
            projection_profile=str(args.bridge_projection_profile),
            since_hours=int(args.since_hours),
        )
    else:
        bridge_payload = bridge_module._build_empty_bridge_payload(
            projection_profile=str(args.bridge_projection_profile),
            since_hours=int(args.since_hours),
        )

    alert_config = resolve_bridge_alert_config(args)
    bridge_payload["alerts"] = evaluate_bridge_alerts(
        bridge_payload,
        alert_config=alert_config,
    )
    bundle: dict[str, object] = {"bridge": bridge_payload}
    if bool(args.bridge_include_markdown):
        bridge_markdown_telemetry: dict[str, object] = {}
        bundle["bridge_markdown"] = bridge_module._render_markdown_bridge(
            bridge_payload,
            markdown_max_projects=args.bridge_markdown_max_projects,
            markdown_alert_compact=bool(args.bridge_markdown_alert_compact),
            markdown_triggered_rule_detail_max=args.bridge_markdown_triggered_rule_detail_max,
            markdown_hide_suppression_section=bool(args.bridge_markdown_hide_suppression_section),
            markdown_include_family_projects=bool(args.bridge_markdown_include_family_projects),
            markdown_family_projects_include_counts=bool(args.bridge_markdown_family_projects_include_counts),
            markdown_family_projects_hide_empty_families=bool(args.bridge_markdown_family_projects_hide_empty_families),
            markdown_family_projects_mode=str(args.bridge_markdown_family_projects_mode),
            markdown_family_projects_source=str(args.bridge_markdown_family_projects_source),
            markdown_family_projects_severity=str(args.bridge_markdown_family_projects_severity),
            markdown_family_projects_max_items=args.bridge_markdown_family_projects_max_items,
            markdown_family_projects_order=str(args.bridge_markdown_family_projects_order),
            markdown_family_projects_count_order=str(args.bridge_markdown_family_projects_count_order),
            markdown_family_projects_count_render_mode=str(
                args.bridge_markdown_family_projects_count_render_mode
            ),
            markdown_family_projects_count_visibility_mode=str(
                args.bridge_markdown_family_projects_count_visibility_mode
            ),
            markdown_family_projects_count_export_mode=str(
                args.bridge_markdown_family_projects_count_export_mode
            ),
            markdown_family_projects_count_table_style=str(
                args.bridge_markdown_family_projects_count_table_style
            ),
            markdown_family_projects_count_table_empty_mode=str(
                args.bridge_markdown_family_projects_count_table_empty_mode
            ),
            markdown_family_projects_count_table_family_label_mode=str(
                args.bridge_markdown_family_projects_count_table_family_label_mode
            ),
            markdown_family_projects_count_table_header_label_mode=str(
                args.bridge_markdown_family_projects_count_table_header_label_mode
            ),
            markdown_family_projects_count_table_family_label_overrides=args.bridge_markdown_family_projects_count_table_family_label_override,
            markdown_family_projects_count_table_metric_label_mode=str(
                args.bridge_markdown_family_projects_count_table_metric_label_mode
            ),
            markdown_family_projects_count_table_metric_label_overrides=args.bridge_markdown_family_projects_count_table_metric_label_override,
            markdown_family_projects_count_table_row_order_mode=str(
                args.bridge_markdown_family_projects_count_table_row_order_mode
            ),
            markdown_family_projects_count_table_include_schema_signature=bool(
                args.bridge_markdown_family_projects_count_table_include_schema_signature
            ),
            markdown_family_projects_count_inline_family_label_mode=str(
                args.bridge_markdown_family_projects_count_inline_family_label_mode
            ),
            markdown_family_projects_count_inline_bucket_label_mode=str(
                args.bridge_markdown_family_projects_count_inline_bucket_label_mode
            ),
            markdown_family_projects_count_label_override_diagnostics=bool(
                args.bridge_markdown_family_projects_count_label_override_diagnostics
            ),
            markdown_family_projects_count_label_override_diagnostics_severity=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_severity
            ),
            markdown_family_projects_count_label_override_diagnostics_json=bool(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json
            ),
            markdown_family_projects_count_label_override_diagnostics_json_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_profile=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_profile
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope=int(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char=str(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char
            ),
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length=int(
                args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length
            ),
            markdown_family_projects_count_label_override_ci_policy_mode=str(
                args.bridge_markdown_family_projects_count_label_override_ci_policy_mode
            ),
            markdown_family_projects_count_min_all=args.bridge_markdown_family_projects_count_min_all,
            markdown_family_projects_count_threshold_mode=str(
                args.bridge_markdown_family_projects_count_threshold_mode
            ),
            markdown_family_projects_count_top_n=args.bridge_markdown_family_projects_count_top_n,
            markdown_runtime_telemetry=bridge_markdown_telemetry,
        )
        bundle["bridge_markdown_telemetry"] = bridge_markdown_telemetry
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        marker_profile_signature_drift_exit_code = int(
            args.bridge_markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_drift_exit_code
        )
    except (TypeError, ValueError):
        marker_profile_signature_drift_exit_code = 0
    marker_profile_signature_drift_exit_code = max(
        0, marker_profile_signature_drift_exit_code
    )

    def _strict_marker_profile_signature_drift_detected(
        bundle_payload: dict[str, object],
    ) -> bool:
        telemetry = bundle_payload.get("bridge_markdown_telemetry")
        return bool(
            isinstance(telemetry, dict)
            and bool(
                telemetry.get("marker_profile_signature_drift_exit_eligible")
            )
        )

    alert_thresholds = {
        "guardrail_triggered_count_threshold": normalize_optional_int(
            args.dashboard_alert_guardrail_triggered_count_threshold,
            field="dashboard_alert_guardrail_triggered_count_threshold",
        ),
        "guardrail_triggered_rate_threshold": normalize_optional_rate(
            args.dashboard_alert_guardrail_triggered_rate_threshold,
            field="dashboard_alert_guardrail_triggered_rate_threshold",
        ),
        "policy_drift_changed_count_threshold": normalize_optional_int(
            args.dashboard_alert_policy_drift_changed_count_threshold,
            field="dashboard_alert_policy_drift_changed_count_threshold",
        ),
        "policy_drift_changed_rate_threshold": normalize_optional_rate(
            args.dashboard_alert_policy_drift_changed_rate_threshold,
            field="dashboard_alert_policy_drift_changed_rate_threshold",
        ),
        "project_guardrail_triggered_count_threshold": normalize_optional_int(
            args.dashboard_alert_project_guardrail_triggered_count_threshold,
            field="dashboard_alert_project_guardrail_triggered_count_threshold",
        ),
    }

    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.exists():
        if bool(args.allow_empty):
            payload = _empty_summary(str(args.rollup_mode), alert_thresholds=alert_thresholds)
            if bool(args.include_bridge):
                payload.update(
                    _build_bridge_bundle(
                        input_dir=input_dir,
                        args=args,
                    )
                )
            if bool(args.json_compact):
                print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
            else:
                print(json.dumps(payload, indent=2))
            effective_exit_code = 0
            if (
                marker_profile_signature_drift_exit_code > 0
                and _strict_marker_profile_signature_drift_detected(payload)
            ):
                effective_exit_code = marker_profile_signature_drift_exit_code
            return int(effective_exit_code)
        raise SystemExit(f"input_dir_not_found:{input_dir}")

    rows = _iter_filtered_payloads(
        input_dir=input_dir,
        project_id=args.project_id,
        since_hours=int(args.since_hours),
    )
    if not rows and not bool(args.allow_empty):
        raise SystemExit("no_matching_audit_artifacts")
    if str(args.rollup_mode) == "dashboard":
        payload = summarize_dashboard(rows, alert_thresholds=alert_thresholds)
    else:
        payload = summarize(rows)
    if bool(args.include_bridge):
        payload.update(
            _build_bridge_bundle(
                input_dir=input_dir,
                args=args,
            )
        )
    effective_exit_code = 0
    if (
        marker_profile_signature_drift_exit_code > 0
        and _strict_marker_profile_signature_drift_detected(payload)
    ):
        effective_exit_code = marker_profile_signature_drift_exit_code
    if bool(args.json_compact):
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2))
    return int(effective_exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
