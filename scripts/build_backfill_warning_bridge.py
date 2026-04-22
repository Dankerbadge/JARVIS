#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backfill_warning_bridge_alerts import evaluate_bridge_alerts, resolve_bridge_alert_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build chat/inbox-ready bridge payload from backfill warning-audit artifacts.",
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
        help="Optional single project filter.",
    )
    parser.add_argument(
        "--since-hours",
        type=int,
        default=24,
        help="Only include runs with exported_at within the last N hours.",
    )
    parser.add_argument(
        "--projection-profile",
        type=str,
        default="policy_core",
        choices=("full", "policy_core"),
        help="Drift projection profile used for per-project delta comparisons.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit 0 with an empty bridge payload when no artifacts match.",
    )
    parser.add_argument(
        "--json-compact",
        action="store_true",
        help="Emit compact JSON.",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="json",
        choices=("json", "markdown"),
        help="Output format: machine-readable JSON or operator-facing markdown briefing.",
    )
    parser.add_argument(
        "--markdown-max-projects",
        type=int,
        default=None,
        help="Optional max number of project rows to include in markdown output.",
    )
    parser.add_argument(
        "--markdown-alert-compact",
        action="store_true",
        help="Emit compact alert summary lines (suppresses verbose per-rule/per-scope lines).",
    )
    parser.add_argument(
        "--markdown-triggered-rule-detail-max",
        type=int,
        default=None,
        help="Optional max number of triggered rule detail lines to emit in markdown output.",
    )
    parser.add_argument(
        "--markdown-hide-suppression-section",
        action="store_true",
        help="Hide suppression-focused markdown lines for ultra-compact digests.",
    )
    parser.add_argument(
        "--markdown-include-family-projects",
        action="store_true",
        help="Include triggered family project listings (warn/error/all) in markdown alerts.",
    )
    parser.add_argument(
        "--markdown-family-projects-include-counts",
        action="store_true",
        help="Include per-family warn/error/all project counts in markdown alerts.",
    )
    parser.add_argument(
        "--markdown-family-projects-hide-empty-families",
        action="store_true",
        help="Hide family rows with zero all-project count in markdown family listings.",
    )
    parser.add_argument(
        "--markdown-family-projects-mode",
        type=str,
        default="full",
        choices=("full", "counts_only"),
        help="Family project markdown rendering mode.",
    )
    parser.add_argument(
        "--markdown-family-projects-source",
        type=str,
        default="triggered",
        choices=("triggered", "all_current", "triggered_or_current"),
        help=(
            "Family project listing source: triggered rule surfaces, all current project states, "
            "or a union of both."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-severity",
        type=str,
        default="all",
        choices=("all", "warn_only", "error_only"),
        help="Family project listing severity filter.",
    )
    parser.add_argument(
        "--markdown-family-projects-max-items",
        type=int,
        default=None,
        help=(
            "Optional max project ids per family/severity list in markdown output. "
            "When capped, output includes (+N more)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-order",
        type=str,
        default="alphabetical",
        choices=("alphabetical", "severity_then_project"),
        help=(
            "Project ordering mode for family listings in markdown output. "
            "severity_then_project orders all-lists as error then warn."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-order",
        type=str,
        default="by_family",
        choices=("by_family", "by_total_desc"),
        help="Ordering mode for family project count summaries.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-render-mode",
        type=str,
        default="full_fields",
        choices=("full_fields", "nonzero_buckets"),
        help="Rendering mode for family project count rows.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-visibility-mode",
        type=str,
        default="all_rows",
        choices=("all_rows", "nonzero_all"),
        help="Visibility mode for family project count rows.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-export-mode",
        type=str,
        default="inline",
        choices=("inline", "table"),
        help="Export mode for family project count output in markdown.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-style",
        type=str,
        default="full",
        choices=("full", "minimal"),
        help="Table style for family project count output when export mode is table.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-empty-mode",
        type=str,
        default="inline_none",
        choices=("inline_none", "table_empty"),
        help="Empty-row behavior for family project count table export.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-family-label-mode",
        type=str,
        default="raw",
        choices=("raw", "title"),
        help="Family label style for family project count table rows.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-header-label-mode",
        type=str,
        default="title",
        choices=("raw", "title"),
        help="Header label style for family project count table output.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-family-label-override",
        action="append",
        default=None,
        help=(
            "Optional family label override in family=label format for table rows. "
            "Can be repeated or comma-separated."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-metric-label-mode",
        type=str,
        default="title",
        choices=("raw", "title"),
        help="Metric header-label style for family project count table output.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-metric-label-override",
        action="append",
        default=None,
        help=(
            "Optional metric label override in metric=label format for table headers. "
            "Supported metrics: warn,error,all. Can be repeated or comma-separated."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-row-order-mode",
        type=str,
        default="count_order",
        choices=("count_order", "canonical", "sorted"),
        help=(
            "Row ordering mode for family project count table output. "
            "count_order follows count-order/top-n selection order."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-table-include-schema-signature",
        action="store_true",
        help="Include table schema signature trace line for family project count table output.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-inline-family-label-mode",
        type=str,
        default="raw",
        choices=("raw", "title"),
        help="Family label style for inline family project count summaries.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-inline-bucket-label-mode",
        type=str,
        default="raw",
        choices=("raw", "title"),
        help="Bucket label style for inline family project count summaries.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics",
        action="store_true",
        help="Include diagnostics line for malformed family/metric table label override tokens.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-severity",
        type=str,
        default="off",
        choices=("off", "note", "warn"),
        help="Severity mode for malformed family/metric table label override diagnostics.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json",
        action="store_true",
        help="Include machine-readable JSON line for label override diagnostics resolution.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-mode",
        type=str,
        default="full",
        choices=("full", "compact"),
        help=(
            "JSON detail mode for label override diagnostics payloads. "
            "compact emits a single flat counters/status payload."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode",
        type=str,
        default="bridge_",
        choices=("bridge_", "count_override_"),
        help=(
            "Key-prefix mode for compact label override diagnostics JSON payload keys."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-profile",
        type=str,
        default="compact_min",
        choices=("compact_min", "compact_full"),
        help=(
            "Compact diagnostics JSON profile preset. "
            "compact_min emits status/counter keys; compact_full adds flat context keys."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode",
        type=str,
        default="counts_only",
        choices=(
            "none",
            "counts_only",
            "counts_plus_tokens",
            "counts_plus_tokens_if_truncated",
        ),
        help=(
            "Compact diagnostics JSON include mode for malformed-token visibility."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sort-mode",
        type=str,
        default="input_order",
        choices=("input_order", "lexicographic"),
        help="Compact diagnostics malformed-token sorting mode.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-max-per-scope",
        type=int,
        default=0,
        help=(
            "Compact diagnostics max malformed tokens per scope (family/metric). "
            "0 keeps all tokens."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix",
        type=str,
        default="+{omitted} more",
        help=(
            "Compact diagnostics overflow suffix appended when malformed-token lists are truncated. "
            "Supports {omitted} placeholder."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix-mode",
        type=str,
        default="include",
        choices=("include", "suppress"),
        help="Compact diagnostics overflow-suffix emission mode when malformed-token lists are truncated.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode",
        type=str,
        default="always",
        choices=("always", "if_truncated_only", "off"),
        help="Compact diagnostics omitted-count key visibility mode for malformed-token scopes.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode",
        type=str,
        default="off",
        choices=("off", "require_nonempty_tokens"),
        help="Compact diagnostics malformed-token list guard mode for sparse/empty token scopes.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode",
        type=str,
        default="always",
        choices=("always", "if_nonempty", "if_truncated"),
        help="Compact diagnostics malformed-token list key emission mode.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode",
        type=str,
        default="selected_only",
        choices=("selected_only", "auto_expand_when_empty"),
        help="Compact diagnostics malformed-token scope fallback mode when selected scopes emit no token keys.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode",
        type=str,
        default="off",
        choices=("off", "summary_only", "per_scope"),
        help=(
            "Compact diagnostics token-list truncation indicator mode for strict sinks without token payload expansion."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode",
        type=str,
        default="family_first",
        choices=("family_first", "metric_first"),
        help="Compact diagnostics token-scope priority mode used by auto-expand fallback ordering.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode",
        type=str,
        default="first_success_only",
        choices=("first_success_only", "all_eligible"),
        help=(
            "Compact diagnostics fallback emission mode for auto-expand scope fallback "
            "(first emitted scope only or all eligible scopes)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode",
        type=str,
        default="off",
        choices=("off", "summary", "per_scope"),
        help=(
            "Compact diagnostics fallback-source marker mode for strict sinks "
            "(summary or per-scope markers)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode",
        type=str,
        default="always",
        choices=("always", "fallback_only"),
        help=(
            "Compact diagnostics fallback-source marker activation mode "
            "(always emit marker keys, or only when fallback contributes source scopes)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode",
        type=str,
        default="off",
        choices=("off", "summary", "per_scope"),
        help=(
            "Compact diagnostics selected-scope marker mode for traceability when "
            "selected scopes satisfy token emission and fallback is bypassed."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode",
        type=str,
        default="default",
        choices=("default", "short"),
        help=(
            "Compact diagnostics marker key naming mode "
            "(default verbose names or short aliases for strict sinks)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode",
        type=str,
        default="off",
        choices=("off", "omit_when_no_token_payload"),
        help=(
            "Compact diagnostics marker suppression mode "
            "(optionally omit marker metadata when token payload keys are absent)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode",
        type=str,
        default="always",
        choices=("always", "if_true_only"),
        help=(
            "Compact diagnostics marker summary visibility mode for boolean marker keys "
            "(always include booleans or only include when true)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode",
        type=str,
        default="priority",
        choices=("canonical", "priority"),
        help=(
            "Compact diagnostics marker per-scope key order mode "
            "(canonical family/metric order or active scope-priority order)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-list-visibility-mode",
        type=str,
        default="always",
        choices=("always", "if_nonempty"),
        help=(
            "Compact diagnostics marker summary-list visibility mode "
            "(always emit summary list markers, or only when non-empty)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode",
        type=str,
        default="inherit",
        choices=("inherit", "markers"),
        help=(
            "Compact diagnostics marker key-prefix mode "
            "(inherit base compact key prefix or isolate marker keys under a marker_ branch)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode",
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
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode",
        type=str,
        default="insertion",
        choices=("insertion", "lexicographic"),
        help=(
            "Compact diagnostics summary-list marker ordering mode "
            "(insertion order or lexicographic)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode",
        type=str,
        default="all",
        choices=("all", "fallback_only", "selected_only", "none"),
        help=(
            "Compact diagnostics summary-list marker family visibility mode "
            "(control fallback vs selected summary list markers)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode",
        type=str,
        default="all",
        choices=("all", "fallback_only", "selected_only", "truncation_only", "none"),
        help=(
            "Compact diagnostics per-scope marker family visibility mode "
            "(control fallback/selected/truncation per-scope marker keys)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-boolean-family-visibility-mode",
        type=str,
        default="all",
        choices=("all", "fallback_only", "selected_only", "truncation_only", "none"),
        help=(
            "Compact diagnostics summary-boolean marker family visibility mode "
            "(control fallback/selected/truncation summary booleans)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode",
        type=str,
        default="off",
        choices=("off", "strict_minimal", "strict_verbose", "strict_debug"),
        help=(
            "Compact diagnostics marker profile shortcut mode "
            "(apply coherent marker visibility/order defaults for strict sinks)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected",
        type=str,
        default="",
        help=(
            "Optional expected marker profile signature (64 hex chars) for compact diagnostics drift checks."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode",
        type=str,
        default="off",
        choices=("off", "warn", "strict"),
        help=(
            "Compact diagnostics marker profile signature match mode "
            "(off reports metadata only, warn surfaces drift, strict also marks fail_ci_recommended)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code",
        type=int,
        default=0,
        help=(
            "Optional non-zero exit code when strict marker-profile signature drift is detected "
            "in markdown diagnostics output. 0 disables."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-precedence-export-mode",
        type=str,
        default="full",
        choices=("full", "summary_only"),
        help=(
            "Compact diagnostics marker precedence export mode "
            "(full per-control source keys or condensed source-count summary)."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode",
        type=str,
        default="both",
        choices=("family_only", "metric_only", "both"),
        help="Compact diagnostics malformed-token scope mode.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-dedup-mode",
        type=str,
        default="off",
        choices=("off", "on"),
        help="Compact diagnostics malformed-token dedup mode before sort/truncation.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-normalization-mode",
        type=str,
        default="preserve",
        choices=("preserve", "lower"),
        help="Compact diagnostics malformed-token normalization mode.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-mode",
        type=str,
        default="off",
        choices=("off", "ascii_safe"),
        help="Compact diagnostics malformed-token sanitization mode.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-replacement-char",
        type=str,
        default="_",
        help=(
            "Compact diagnostics replacement characters used for non-ASCII/non-printable chars "
            "when sanitization mode is ascii_safe."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length",
        type=int,
        default=1,
        help=(
            "Compact diagnostics minimum malformed-token length after normalization/sanitization. "
            "Tokens shorter than this are dropped."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-label-override-ci-policy-mode",
        type=str,
        default="off",
        choices=("off", "strict"),
        help=(
            "Optional CI recommendation mode for malformed family/metric label override tokens. "
            "strict emits fail_ci_recommended=True when malformed tokens are present."
        ),
    )
    parser.add_argument(
        "--markdown-family-projects-count-min-all",
        type=int,
        default=0,
        help="Optional minimum all-count required for a family to appear in count summaries.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-threshold-mode",
        type=str,
        default="off",
        choices=("off", "all_min"),
        help="Count threshold mode for family project count summaries.",
    )
    parser.add_argument(
        "--markdown-family-projects-count-top-n",
        type=int,
        default=None,
        help="Optional max number of family rows to include in count summaries (counts-only mode).",
    )
    parser.add_argument(
        "--bridge-alert-policy-drift-count-threshold",
        type=int,
        default=None,
        help="Optional alert threshold for projects_with_policy_drift (count).",
    )
    parser.add_argument(
        "--bridge-alert-policy-drift-rate-threshold",
        type=float,
        default=None,
        help="Optional alert threshold for projects_with_policy_drift/projects_with_previous in [0,1].",
    )
    parser.add_argument(
        "--bridge-alert-guardrail-count-threshold",
        type=int,
        default=None,
        help="Optional alert threshold for projects_with_guardrail_triggered (count).",
    )
    parser.add_argument(
        "--bridge-alert-guardrail-rate-threshold",
        type=float,
        default=None,
        help="Optional alert threshold for projects_with_guardrail_triggered/project_count in [0,1].",
    )
    parser.add_argument(
        "--bridge-alert-policy-drift-severity",
        type=str,
        default="error",
        choices=("warn", "error"),
        help="Severity tier for policy-drift threshold rules (warn does not trigger non-zero exit).",
    )
    parser.add_argument(
        "--bridge-alert-guardrail-severity",
        type=str,
        default="error",
        choices=("warn", "error"),
        help="Severity tier for guardrail threshold rules (warn does not trigger non-zero exit).",
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
        help="Exit code used when bridge alert thresholds trigger and script otherwise succeeds.",
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
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _extract_projection(payload: dict, *, profile: str) -> dict:
    warning_codes = payload.get("warning_codes")
    if isinstance(warning_codes, list):
        normalized_codes = sorted({str(item) for item in warning_codes if str(item).strip()})
    else:
        normalized_codes = []

    resolution = payload.get("warning_policy_resolution")
    if isinstance(resolution, dict):
        profile_resolution = resolution.get("profile")
        if isinstance(profile_resolution, dict):
            profile_source = str(profile_resolution.get("source") or "")
        else:
            profile_source = ""
    else:
        profile_source = ""

    full_projection = {
        "warning_policy_profile": str(payload.get("warning_policy_profile") or ""),
        "warning_policy_profile_source": profile_source,
        "warning_policy_checksum": str(payload.get("warning_policy_checksum") or ""),
        "warning_policy_config_source": str(payload.get("warning_policy_config_source") or ""),
        "warning_policy_config_path": str(payload.get("warning_policy_config_path") or ""),
        "exit_code_policy": str(payload.get("exit_code_policy") or ""),
        "max_warning_severity": str(payload.get("max_warning_severity") or ""),
        "warning_codes": normalized_codes,
    }
    if str(profile) == "policy_core":
        return {
            "warning_policy_profile": full_projection.get("warning_policy_profile"),
            "warning_policy_profile_source": full_projection.get("warning_policy_profile_source"),
            "warning_policy_checksum": full_projection.get("warning_policy_checksum"),
            "warning_policy_config_source": full_projection.get("warning_policy_config_source"),
        }
    return full_projection


def _diff_dicts(before: dict, after: dict) -> list[dict]:
    diffs: list[dict] = []
    for key in sorted(set(before.keys()) | set(after.keys())):
        b = before.get(key)
        a = after.get(key)
        if b == a:
            continue
        diffs.append({"field": key, "before": b, "after": a})
    return diffs


def _iter_filtered_rows(
    *,
    input_dir: Path,
    project_id: str | None,
    since_hours: int,
) -> list[tuple[Path, dict, datetime]]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=max(0, int(since_hours)))
    rows: list[tuple[Path, dict, datetime]] = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            payload = _load_json(path)
        except Exception:
            continue
        if project_id is not None and str(payload.get("project_id") or "") != str(project_id):
            continue
        exported_at = _parse_exported_at(payload)
        if exported_at is None:
            continue
        if exported_at < window_start:
            continue
        rows.append((path, payload, exported_at))
    return rows


def _summarize_project_row(path: Path, payload: dict, exported_at: datetime) -> dict:
    audit = payload.get("_audit")
    if isinstance(audit, dict):
        drift = audit.get("policy_drift")
    else:
        drift = None
    if isinstance(drift, dict):
        guardrail_triggered = bool(drift.get("guardrail_triggered"))
    else:
        guardrail_triggered = False
    return {
        "path": str(path.resolve()),
        "exported_at": exported_at.isoformat(),
        "status": str(payload.get("status") or "unknown"),
        "warning_count": max(0, int(payload.get("warning_count") or 0)),
        "warning_policy_profile": str(payload.get("warning_policy_profile") or ""),
        "warning_policy_checksum": str(payload.get("warning_policy_checksum") or ""),
        "warning_policy_config_source": str(payload.get("warning_policy_config_source") or ""),
        "guardrail_triggered": guardrail_triggered,
    }


def _build_empty_bridge_payload(*, projection_profile: str, since_hours: int) -> dict:
    return {
        "kind": "backfill_warning_bridge",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": int(max(0, int(since_hours))),
        "projection_profile": str(projection_profile),
        "total_runs": 0,
        "project_count": 0,
        "summary": {
            "projects_with_previous": 0,
            "projects_with_policy_drift": 0,
            "projects_with_guardrail_triggered": 0,
            "status_counts_latest": {},
        },
        "projects": [],
    }


def build_bridge_payload(
    rows: list[tuple[Path, dict, datetime]],
    *,
    projection_profile: str,
    since_hours: int,
) -> dict:
    grouped: dict[str, list[tuple[Path, dict, datetime]]] = defaultdict(list)
    for path, payload, exported_at in rows:
        project = str(payload.get("project_id") or "unknown")
        grouped[project].append((path, payload, exported_at))

    status_counts_latest: Counter[str] = Counter()
    projects_with_previous = 0
    projects_with_policy_drift = 0
    projects_with_guardrail_triggered = 0

    projects: list[dict] = []
    for project_id in sorted(grouped.keys()):
        ordered = sorted(grouped[project_id], key=lambda row: row[2])
        latest_path, latest_payload, latest_exported_at = ordered[-1]
        latest_summary = _summarize_project_row(latest_path, latest_payload, latest_exported_at)
        status_counts_latest[str(latest_summary.get("status") or "unknown")] += 1
        if bool(latest_summary.get("guardrail_triggered")):
            projects_with_guardrail_triggered += 1

        delta = {
            "has_previous": False,
            "projection_profile": str(projection_profile),
            "policy_drift_changed": False,
            "changed_fields": [],
            "warning_count_delta": 0,
            "status_changed": False,
            "guardrail_triggered_changed": False,
        }

        if len(ordered) >= 2:
            projects_with_previous += 1
            prev_path, prev_payload, prev_exported_at = ordered[-2]
            prev_projection = _extract_projection(prev_payload, profile=projection_profile)
            latest_projection = _extract_projection(latest_payload, profile=projection_profile)
            diffs = _diff_dicts(prev_projection, latest_projection)
            changed_fields = sorted(
                {
                    str(item.get("field") or "")
                    for item in diffs
                    if isinstance(item, dict) and str(item.get("field") or "").strip()
                }
            )
            policy_drift_changed = bool(diffs)
            if policy_drift_changed:
                projects_with_policy_drift += 1

            prev_summary = _summarize_project_row(prev_path, prev_payload, prev_exported_at)
            delta = {
                "has_previous": True,
                "projection_profile": str(projection_profile),
                "policy_drift_changed": policy_drift_changed,
                "changed_fields": changed_fields,
                "warning_count_delta": int(latest_summary["warning_count"]) - int(prev_summary["warning_count"]),
                "status_changed": str(prev_summary.get("status") or "") != str(latest_summary.get("status") or ""),
                "guardrail_triggered_changed": bool(prev_summary.get("guardrail_triggered"))
                != bool(latest_summary.get("guardrail_triggered")),
                "previous_path": str(prev_summary.get("path") or ""),
                "previous_exported_at": str(prev_summary.get("exported_at") or ""),
            }

        projects.append(
            {
                "project_id": project_id,
                "run_count_window": int(len(ordered)),
                "latest": latest_summary,
                "delta_from_previous": delta,
            }
        )

    return {
        "kind": "backfill_warning_bridge",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": int(max(0, int(since_hours))),
        "projection_profile": str(projection_profile),
        "total_runs": int(len(rows)),
        "project_count": int(len(projects)),
        "summary": {
            "projects_with_previous": int(projects_with_previous),
            "projects_with_policy_drift": int(projects_with_policy_drift),
            "projects_with_guardrail_triggered": int(projects_with_guardrail_triggered),
            "status_counts_latest": dict(sorted(status_counts_latest.items())),
        },
        "projects": projects,
    }


def _render_markdown_bridge(
    payload: dict,
    *,
    markdown_max_projects: int | None,
    markdown_alert_compact: bool = False,
    markdown_triggered_rule_detail_max: int | None = None,
    markdown_hide_suppression_section: bool = False,
    markdown_include_family_projects: bool = False,
    markdown_family_projects_include_counts: bool = False,
    markdown_family_projects_hide_empty_families: bool = False,
    markdown_family_projects_mode: str = "full",
    markdown_family_projects_source: str = "triggered",
    markdown_family_projects_severity: str = "all",
    markdown_family_projects_max_items: int | None = None,
    markdown_family_projects_order: str = "alphabetical",
    markdown_family_projects_count_order: str = "by_family",
    markdown_family_projects_count_render_mode: str = "full_fields",
    markdown_family_projects_count_visibility_mode: str = "all_rows",
    markdown_family_projects_count_export_mode: str = "inline",
    markdown_family_projects_count_table_style: str = "full",
    markdown_family_projects_count_table_empty_mode: str = "inline_none",
    markdown_family_projects_count_table_family_label_mode: str = "raw",
    markdown_family_projects_count_table_header_label_mode: str = "title",
    markdown_family_projects_count_table_family_label_overrides: list[str] | None = None,
    markdown_family_projects_count_table_metric_label_mode: str = "title",
    markdown_family_projects_count_table_metric_label_overrides: list[str] | None = None,
    markdown_family_projects_count_table_row_order_mode: str = "count_order",
    markdown_family_projects_count_table_include_schema_signature: bool = False,
    markdown_family_projects_count_inline_family_label_mode: str = "raw",
    markdown_family_projects_count_inline_bucket_label_mode: str = "raw",
    markdown_family_projects_count_label_override_diagnostics: bool = False,
    markdown_family_projects_count_label_override_diagnostics_severity: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json: bool = False,
    markdown_family_projects_count_label_override_diagnostics_json_mode: str = "full",
    markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode: str = "bridge_",
    markdown_family_projects_count_label_override_diagnostics_json_compact_profile: str = "compact_min",
    markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode: str = "counts_only",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode: str = "input_order",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope: int = 0,
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix: str = "+{omitted} more",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode: str = "include",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode: str = "always",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode: str = "always",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode: str = "selected_only",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode: str = "family_first",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode: str = "first_success_only",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode: str = "always",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode: str = "default",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode: str = "always",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode: str = "priority",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode: str = "always",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode: str = "inherit",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode: str = "all",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode: str = "insertion",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode: str = "all",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode: str = "all",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode: str = "all",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected: str = "",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode: str = "full",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode: str = "both",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode: str = "preserve",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode: str = "off",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char: str = "_",
    markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length: int = 1,
    markdown_family_projects_count_label_override_ci_policy_mode: str = "off",
    markdown_family_projects_count_min_all: int = 0,
    markdown_family_projects_count_threshold_mode: str = "off",
    markdown_family_projects_count_top_n: int | None = None,
    markdown_runtime_telemetry: dict[str, object] | None = None,
) -> str:
    if markdown_runtime_telemetry is not None:
        markdown_runtime_telemetry.setdefault(
            "marker_profile_signature_drift_detected", False
        )
        markdown_runtime_telemetry.setdefault(
            "marker_profile_signature_strict_mode", False
        )
        markdown_runtime_telemetry.setdefault(
            "marker_profile_signature_drift_exit_eligible", False
        )

    def _rule_family(rule_name: str) -> str:
        if rule_name.startswith("policy_drift_"):
            return "policy_only"
        if rule_name.startswith("guardrail_"):
            return "guardrail_only"
        return "both"

    def _normalize_str_list(raw: object) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if str(item).strip()]

    def _render_list(raw: object) -> str:
        values = _normalize_str_list(raw)
        if not values:
            return "none"
        return ", ".join(values)

    def _render_scope_map(raw: object) -> str:
        if not isinstance(raw, dict):
            return "none"
        entries: list[str] = []
        for key, value in sorted(raw.items()):
            project_id = str(key or "").strip()
            scope = str(value or "").strip()
            if not project_id or not scope:
                continue
            entries.append(f"{project_id}@{scope}")
        if not entries:
            return "none"
        return ", ".join(entries)

    def _render_family_rule_map(raw_rule_names: object) -> str:
        grouped: dict[str, list[str]] = {
            "policy_only": [],
            "guardrail_only": [],
            "both": [],
        }
        for name in _normalize_str_list(raw_rule_names):
            grouped[_rule_family(name)].append(name)
        parts: list[str] = []
        for family in ("policy_only", "guardrail_only", "both"):
            names = sorted(set(grouped[family]))
            rendered = ", ".join(names) if names else "none"
            parts.append(f"{family}=[{rendered}]")
        return "; ".join(parts)

    def _format_family_label_for_table(family: str, *, mode: str) -> str:
        if mode != "title":
            return family
        normalized = str(family or "").strip().replace("_", " ")
        return normalized.title() if normalized else family

    def _format_family_header_for_table(*, mode: str) -> str:
        base = "family"
        if mode == "raw":
            return base
        return base.title()

    def _parse_label_overrides(
        *,
        raw: object,
        valid_keys: set[str],
    ) -> tuple[dict[str, str], list[str]]:
        normalized: dict[str, str] = {}
        malformed: list[str] = []
        if raw is None:
            return normalized, malformed
        if isinstance(raw, list):
            chunks = [str(item or "") for item in raw]
        else:
            chunks = [str(raw or "")]
        for chunk in chunks:
            for part in chunk.split(","):
                token = str(part or "").strip()
                if not token:
                    continue
                if "=" not in token:
                    malformed.append(token)
                    continue
                key_raw, label_raw = token.split("=", 1)
                key = str(key_raw or "").strip().lower()
                label = str(label_raw or "").strip()
                if key not in valid_keys or not label:
                    malformed.append(token)
                    continue
                normalized[key] = label
        return normalized, malformed

    def _normalize_table_family_label_overrides(raw: object) -> tuple[dict[str, str], list[str]]:
        return _parse_label_overrides(
            raw=raw,
            valid_keys={"policy_only", "guardrail_only", "both"},
        )

    def _format_family_label_for_inline(family: str, *, mode: str) -> str:
        if mode != "title":
            return family
        normalized = str(family or "").strip().replace("_", " ")
        return normalized.title() if normalized else family

    def _format_metric_label_for_table(metric: str, *, mode: str) -> str:
        key = str(metric or "").strip().lower()
        if key not in {"warn", "error", "all"}:
            return metric
        if mode == "raw":
            return key
        return key.title()

    def _normalize_table_metric_label_overrides(raw: object) -> tuple[dict[str, str], list[str]]:
        return _parse_label_overrides(
            raw=raw,
            valid_keys={"warn", "error", "all"},
        )

    def _format_bucket_label_for_inline(bucket: str, *, mode: str) -> str:
        key = str(bucket or "").strip().lower()
        if key not in {"warn", "error", "all"}:
            return bucket
        if mode == "title":
            return key.title()
        return key

    def _order_table_count_rows(
        rows: list[tuple[str, int, int, int]],
        *,
        mode: str,
    ) -> list[tuple[str, int, int, int]]:
        if mode == "count_order":
            return list(rows)
        if mode == "sorted":
            return sorted(rows, key=lambda row: row[0])
        canonical_rank = {
            "policy_only": 0,
            "guardrail_only": 1,
            "both": 2,
        }
        return sorted(rows, key=lambda row: (canonical_rank.get(row[0], 99), row[0]))

    def _prepare_compact_tokens(
        raw_tokens: list[str],
        *,
        sort_mode: str,
        max_per_scope: int,
        overflow_suffix: str,
        overflow_suffix_mode: str,
        dedup_mode: str,
        normalization_mode: str,
        sanitization_mode: str,
        sanitization_replacement_char: str,
        min_length: int,
    ) -> tuple[list[str], int]:
        replacement_token = str(sanitization_replacement_char or "")
        if sanitization_mode == "ascii_safe":
            replacement_token = "".join(
                ch for ch in replacement_token if 32 <= ord(ch) <= 126
            )
            if not replacement_token:
                replacement_token = "_"
        min_token_length = max(0, int(min_length))

        def _normalize_sanitize_token(raw_token: object) -> str:
            token_value = str(raw_token or "")
            if normalization_mode == "lower":
                token_value = token_value.lower()
            if sanitization_mode == "ascii_safe":
                normalized_value = unicodedata.normalize("NFKD", token_value)
                sanitized_chars: list[str] = []
                for ch in normalized_value:
                    if 32 <= ord(ch) <= 126:
                        sanitized_chars.append(ch)
                    elif ch in ("\t", "\n", "\r"):
                        sanitized_chars.append(" ")
                    elif replacement_token:
                        sanitized_chars.append(replacement_token)
                token_value = "".join(sanitized_chars)
            token_value = "".join(
                ch if 32 <= ord(ch) <= 126 else " "
                for ch in token_value
            )
            token_value = " ".join(token_value.split())
            return token_value.strip()

        tokens: list[str] = []
        for raw_token in raw_tokens:
            normalized_token = _normalize_sanitize_token(raw_token)
            if normalized_token:
                if min_token_length > 0 and len(normalized_token) < min_token_length:
                    continue
                tokens.append(normalized_token)
        if dedup_mode == "on":
            deduped_tokens: list[str] = []
            seen_tokens: set[str] = set()
            for token in tokens:
                if token in seen_tokens:
                    continue
                seen_tokens.add(token)
                deduped_tokens.append(token)
            tokens = deduped_tokens
        if sort_mode == "lexicographic":
            tokens = sorted(tokens)
        limit = max(0, int(max_per_scope))
        if limit <= 0 or len(tokens) <= limit:
            return tokens, 0
        omitted = int(len(tokens) - limit)
        trimmed = list(tokens[:limit])
        suffix_mode_effective = str(overflow_suffix_mode or "include").strip().lower()
        if suffix_mode_effective not in ("include", "suppress"):
            suffix_mode_effective = "include"
        suffix_template = str(overflow_suffix or "")
        if suffix_mode_effective == "include" and suffix_template:
            suffix_text = suffix_template.replace("{omitted}", str(omitted))
            suffix_text = _normalize_sanitize_token(suffix_text)
            if suffix_text.strip():
                trimmed.append(suffix_text)
        return trimmed, omitted

    def _default_family_severity(alerts_payload: dict, family: str) -> str:
        severities_raw = alerts_payload.get("severities")
        if not isinstance(severities_raw, dict):
            return "error"
        if family == "policy_only":
            value = str(severities_raw.get("policy_drift") or "error").strip().lower()
        elif family == "guardrail_only":
            value = str(severities_raw.get("guardrail") or "error").strip().lower()
        else:
            value = "error"
        if value not in ("warn", "error"):
            return "error"
        return value

    def _resolve_family_project_severity(
        *,
        alerts_payload: dict,
        project_id: str,
        family: str,
    ) -> str:
        default_severity = _default_family_severity(alerts_payload, family)
        overrides_raw = alerts_payload.get("project_severity_overrides_resolved")
        if not isinstance(overrides_raw, dict):
            return default_severity
        project_override = overrides_raw.get(project_id)
        if not isinstance(project_override, dict):
            return default_severity
        severity = str(project_override.get("severity") or "").strip().lower()
        scope = str(project_override.get("scope") or "both").strip().lower()
        if severity not in ("warn", "error"):
            return default_severity
        if scope not in ("both", "policy_only", "guardrail_only"):
            scope = "both"
        if scope not in ("both", family):
            return default_severity
        return severity

    def _collect_family_projects_from_triggered_rules(rules_raw: object) -> dict[str, dict[str, set[str]]]:
        family_projects: dict[str, dict[str, set[str]]] = {
            "policy_only": {"warn": set(), "error": set(), "all": set()},
            "guardrail_only": {"warn": set(), "error": set(), "all": set()},
            "both": {"warn": set(), "error": set(), "all": set()},
        }
        if isinstance(rules_raw, list):
            for rule_item in rules_raw:
                if not isinstance(rule_item, dict):
                    continue
                if not bool(rule_item.get("triggered")):
                    continue
                family = _rule_family(str(rule_item.get("name") or ""))
                projects_by_severity = rule_item.get("projects_by_severity")
                if not isinstance(projects_by_severity, dict):
                    continue
                for severity in ("warn", "error"):
                    projects_raw = projects_by_severity.get(severity)
                    if not isinstance(projects_raw, list):
                        continue
                    for project_id in projects_raw:
                        normalized = str(project_id or "").strip()
                        if not normalized:
                            continue
                        family_projects[family][severity].add(normalized)
                        family_projects[family]["all"].add(normalized)
        return family_projects

    def _collect_family_projects_from_current_state(
        *,
        payload_data: dict,
        alerts_payload: dict,
    ) -> dict[str, dict[str, set[str]]]:
        family_projects: dict[str, dict[str, set[str]]] = {
            "policy_only": {"warn": set(), "error": set(), "all": set()},
            "guardrail_only": {"warn": set(), "error": set(), "all": set()},
            "both": {"warn": set(), "error": set(), "all": set()},
        }
        projects_raw = payload_data.get("projects")
        if not isinstance(projects_raw, list):
            return family_projects
        for row in projects_raw:
            if not isinstance(row, dict):
                continue
            project_id = str(row.get("project_id") or "").strip()
            if not project_id:
                continue
            delta = row.get("delta_from_previous")
            latest = row.get("latest")
            policy_changed = bool(isinstance(delta, dict) and bool(delta.get("policy_drift_changed")))
            guardrail_triggered = bool(isinstance(latest, dict) and bool(latest.get("guardrail_triggered")))
            if policy_changed:
                policy_severity = _resolve_family_project_severity(
                    alerts_payload=alerts_payload,
                    project_id=project_id,
                    family="policy_only",
                )
                family_projects["policy_only"][policy_severity].add(project_id)
                family_projects["policy_only"]["all"].add(project_id)
            if guardrail_triggered:
                guardrail_severity = _resolve_family_project_severity(
                    alerts_payload=alerts_payload,
                    project_id=project_id,
                    family="guardrail_only",
                )
                family_projects["guardrail_only"][guardrail_severity].add(project_id)
                family_projects["guardrail_only"]["all"].add(project_id)
            if policy_changed and guardrail_triggered:
                policy_severity = _resolve_family_project_severity(
                    alerts_payload=alerts_payload,
                    project_id=project_id,
                    family="policy_only",
                )
                guardrail_severity = _resolve_family_project_severity(
                    alerts_payload=alerts_payload,
                    project_id=project_id,
                    family="guardrail_only",
                )
                combined_severity = (
                    "error"
                    if "error" in {policy_severity, guardrail_severity}
                    else "warn"
                )
                family_projects["both"][combined_severity].add(project_id)
                family_projects["both"]["all"].add(project_id)
        return family_projects

    def _merge_family_project_maps(
        left: dict[str, dict[str, set[str]]],
        right: dict[str, dict[str, set[str]]],
    ) -> dict[str, dict[str, set[str]]]:
        merged: dict[str, dict[str, set[str]]] = {
            "policy_only": {"warn": set(), "error": set(), "all": set()},
            "guardrail_only": {"warn": set(), "error": set(), "all": set()},
            "both": {"warn": set(), "error": set(), "all": set()},
        }
        for family in ("policy_only", "guardrail_only", "both"):
            for bucket in ("warn", "error", "all"):
                merged[family][bucket] = set(left[family][bucket]) | set(right[family][bucket])
        return merged

    def _render_family_projects_map(
        *,
        source_mode: str,
        severity_filter: str,
        max_items: int | None,
        order_mode: str,
        hide_empty_families: bool,
        count_order_mode: str,
        count_render_mode: str,
        count_visibility_mode: str,
        count_inline_family_label_mode: str,
        count_inline_bucket_label_mode: str,
        count_min_all: int,
        count_threshold_mode: str,
        count_top_n: int | None,
        payload_data: dict,
        alerts_payload: dict,
        rules_raw: object,
    ) -> tuple[str, str, dict[str, int], list[tuple[str, int, int, int]]]:
        def _render_project_group(projects: list[str]) -> str:
            if max_items is None:
                return ", ".join(projects) if projects else "none"
            cap = max(0, int(max_items))
            if len(projects) <= cap:
                return ", ".join(projects) if projects else "none"
            shown = projects[:cap]
            omitted = len(projects) - len(shown)
            shown_text = ", ".join(shown) if shown else "none"
            return f"{shown_text} (+{omitted} more)"

        if source_mode == "all_current":
            family_projects = _collect_family_projects_from_current_state(
                payload_data=payload_data,
                alerts_payload=alerts_payload,
            )
        elif source_mode == "triggered_or_current":
            family_projects = _merge_family_project_maps(
                _collect_family_projects_from_triggered_rules(rules_raw),
                _collect_family_projects_from_current_state(
                    payload_data=payload_data,
                    alerts_payload=alerts_payload,
                ),
            )
        else:
            family_projects = _collect_family_projects_from_triggered_rules(rules_raw)
        if severity_filter == "warn_only":
            for family in ("policy_only", "guardrail_only", "both"):
                family_projects[family]["error"] = set()
                family_projects[family]["all"] = set(family_projects[family]["warn"])
        elif severity_filter == "error_only":
            for family in ("policy_only", "guardrail_only", "both"):
                family_projects[family]["warn"] = set()
                family_projects[family]["all"] = set(family_projects[family]["error"])
        segments: list[str] = []
        normalized_order_mode = (
            "severity_then_project"
            if str(order_mode or "").strip().lower() == "severity_then_project"
            else "alphabetical"
        )
        for family in ("policy_only", "guardrail_only", "both"):
            warn_projects = sorted(family_projects[family]["warn"])
            error_projects = sorted(family_projects[family]["error"])
            all_projects: list[str]
            if normalized_order_mode == "severity_then_project":
                all_candidates = (
                    list(error_projects)
                    + list(warn_projects)
                    + sorted(
                        family_projects[family]["all"]
                        - family_projects[family]["warn"]
                        - family_projects[family]["error"]
                    )
                )
                seen: set[str] = set()
                all_projects = []
                for project_id in all_candidates:
                    if project_id in seen:
                        continue
                    seen.add(project_id)
                    all_projects.append(project_id)
            else:
                all_projects = sorted(family_projects[family]["all"])
            if hide_empty_families and not all_projects:
                continue
            warn_text = _render_project_group(warn_projects)
            error_text = _render_project_group(error_projects)
            all_text = _render_project_group(all_projects)
            segments.append(
                f"{family}:warn=[{warn_text}] error=[{error_text}] all=[{all_text}]"
            )
        counts_segments: list[str] = []
        count_rows: list[tuple[str, int, int, int]] = []
        normalized_count_threshold_mode = (
            "all_min"
            if str(count_threshold_mode or "").strip().lower() == "all_min"
            else "off"
        )
        normalized_count_min_all = max(0, int(count_min_all))
        normalized_count_visibility_mode = (
            "nonzero_all"
            if str(count_visibility_mode or "").strip().lower() == "nonzero_all"
            else "all_rows"
        )
        for family in ("policy_only", "guardrail_only", "both"):
            all_count = len(family_projects[family]["all"])
            if normalized_count_visibility_mode == "nonzero_all" and all_count <= 0:
                continue
            if normalized_count_threshold_mode == "all_min" and all_count < normalized_count_min_all:
                continue
            count_rows.append(
                (
                    family,
                    len(family_projects[family]["warn"]),
                    len(family_projects[family]["error"]),
                    all_count,
                )
            )
        normalized_count_order_mode = (
            "by_total_desc"
            if str(count_order_mode or "").strip().lower() == "by_total_desc"
            else "by_family"
        )
        normalized_count_render_mode = (
            "nonzero_buckets"
            if str(count_render_mode or "").strip().lower() == "nonzero_buckets"
            else "full_fields"
        )
        normalized_inline_family_label_mode = (
            "title"
            if str(count_inline_family_label_mode or "").strip().lower() == "title"
            else "raw"
        )
        normalized_inline_bucket_label_mode = (
            "title"
            if str(count_inline_bucket_label_mode or "").strip().lower() == "title"
            else "raw"
        )
        if normalized_count_order_mode == "by_total_desc":
            count_rows.sort(key=lambda row: (-row[3], -row[2], -row[1], row[0]))
        total_count_rows = len(count_rows)
        if count_top_n is not None:
            normalized_count_top_n = max(0, int(count_top_n))
            count_rows = count_rows[:normalized_count_top_n]
        shown_count_rows = len(count_rows)
        for family, warn_count, error_count, all_count in count_rows:
            family_label = _format_family_label_for_inline(
                family,
                mode=normalized_inline_family_label_mode,
            )
            row_parts: list[str] = []
            if normalized_count_render_mode == "nonzero_buckets":
                if warn_count > 0:
                    row_parts.append(
                        f"{_format_bucket_label_for_inline('warn', mode=normalized_inline_bucket_label_mode)}={warn_count}"
                    )
                if error_count > 0:
                    row_parts.append(
                        f"{_format_bucket_label_for_inline('error', mode=normalized_inline_bucket_label_mode)}={error_count}"
                    )
                row_parts.append(
                    f"{_format_bucket_label_for_inline('all', mode=normalized_inline_bucket_label_mode)}={all_count}"
                )
            else:
                row_parts.extend(
                    [
                        f"{_format_bucket_label_for_inline('warn', mode=normalized_inline_bucket_label_mode)}={warn_count}",
                        f"{_format_bucket_label_for_inline('error', mode=normalized_inline_bucket_label_mode)}={error_count}",
                        f"{_format_bucket_label_for_inline('all', mode=normalized_inline_bucket_label_mode)}={all_count}",
                    ]
                )
            counts_segments.append(f"{family_label}:{' '.join(row_parts)}")
        rendered_segments = "; ".join(segments) if segments else "none"
        rendered_counts = "; ".join(counts_segments) if counts_segments else "none"
        return (
            rendered_segments,
            rendered_counts,
            {
                "total_rows": int(total_count_rows),
                "shown_rows": int(shown_count_rows),
                "omitted_rows": int(max(0, total_count_rows - shown_count_rows)),
            },
            list(count_rows),
        )

    generated_at = str(payload.get("generated_at") or "")
    window_hours = int(payload.get("window_hours") or 0)
    projection_profile = str(payload.get("projection_profile") or "")
    total_runs = int(payload.get("total_runs") or 0)
    project_count = int(payload.get("project_count") or 0)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        projects_with_previous = int(summary.get("projects_with_previous") or 0)
        projects_with_policy_drift = int(summary.get("projects_with_policy_drift") or 0)
        projects_with_guardrail_triggered = int(summary.get("projects_with_guardrail_triggered") or 0)
        status_counts_latest = dict(summary.get("status_counts_latest") or {})
    else:
        projects_with_previous = 0
        projects_with_policy_drift = 0
        projects_with_guardrail_triggered = 0
        status_counts_latest = {}

    projects_raw = payload.get("projects")
    if isinstance(projects_raw, list):
        projects = [item for item in projects_raw if isinstance(item, dict)]
    else:
        projects = []

    project_limit = None
    if markdown_max_projects is not None:
        project_limit = max(0, int(markdown_max_projects))
    if project_limit is not None:
        shown_projects = projects[:project_limit]
    else:
        shown_projects = projects

    detail_limit = None
    if markdown_triggered_rule_detail_max is not None:
        detail_limit = max(0, int(markdown_triggered_rule_detail_max))

    lines: list[str] = []
    lines.append("# Backfill Warning Bridge Briefing")
    lines.append("")
    lines.append(f"- Generated (UTC): `{generated_at}`")
    lines.append(f"- Window: `{window_hours}h`")
    lines.append(f"- Projection Profile: `{projection_profile}`")
    lines.append(f"- Runs: `{total_runs}`")
    lines.append(f"- Projects: `{project_count}`")
    lines.append(f"- Projects With Previous Run: `{projects_with_previous}`")
    lines.append(f"- Projects With Policy Drift: `{projects_with_policy_drift}`")
    lines.append(f"- Projects With Guardrail Triggered: `{projects_with_guardrail_triggered}`")
    if status_counts_latest:
        normalized_status_counts = ", ".join(
            f"{str(key)}={int(value)}" for key, value in sorted(status_counts_latest.items())
        )
    else:
        normalized_status_counts = "none"
    lines.append(f"- Latest Status Counts: `{normalized_status_counts}`")
    alerts = payload.get("alerts")
    if isinstance(alerts, dict):
        alert_enabled = bool(alerts.get("enabled"))
        alert_triggered = bool(alerts.get("triggered"))
        alert_exit_triggered = bool(alerts.get("exit_triggered"))
        max_triggered_severity = str(alerts.get("max_triggered_severity") or "none")
        triggered_rules_text = _render_list(alerts.get("triggered_rules"))
        lines.append(f"- Alerting Enabled: `{alert_enabled}`")
        lines.append(f"- Alerts Triggered: `{alert_triggered}`")
        lines.append(f"- Alert Exit Triggered: `{alert_exit_triggered}`")
        lines.append(f"- Max Triggered Severity: `{max_triggered_severity}`")
        lines.append(f"- Triggered Rules: `{triggered_rules_text}`")
        lines.append(
            f"- Triggered Rules By Family: `{_render_family_rule_map(alerts.get('triggered_rules'))}`"
        )
        if markdown_include_family_projects:
            source_mode_raw = str(markdown_family_projects_source or "").strip().lower()
            if source_mode_raw == "all_current":
                source_mode = "all_current"
            elif source_mode_raw == "triggered_or_current":
                source_mode = "triggered_or_current"
            else:
                source_mode = "triggered"
            family_projects_mode = (
                "counts_only"
                if str(markdown_family_projects_mode or "").strip().lower() == "counts_only"
                else "full"
            )
            severity_filter = str(markdown_family_projects_severity or "all").strip().lower()
            if severity_filter not in ("all", "warn_only", "error_only"):
                severity_filter = "all"
            if source_mode == "all_current":
                line_label = "Family Projects (all_current)"
            elif source_mode == "triggered_or_current":
                line_label = "Family Projects (triggered_or_current)"
            else:
                line_label = "Triggered Family Projects"
            (
                family_projects_text,
                family_projects_counts_text,
                family_projects_count_meta,
                family_projects_count_rows,
            ) = _render_family_projects_map(
                source_mode=source_mode,
                severity_filter=severity_filter,
                max_items=markdown_family_projects_max_items,
                order_mode=markdown_family_projects_order,
                hide_empty_families=markdown_family_projects_hide_empty_families,
                count_order_mode=markdown_family_projects_count_order,
                count_render_mode=markdown_family_projects_count_render_mode,
                count_visibility_mode=markdown_family_projects_count_visibility_mode,
                count_inline_family_label_mode=markdown_family_projects_count_inline_family_label_mode,
                count_inline_bucket_label_mode=markdown_family_projects_count_inline_bucket_label_mode,
                count_min_all=markdown_family_projects_count_min_all,
                count_threshold_mode=markdown_family_projects_count_threshold_mode,
                count_top_n=(
                    markdown_family_projects_count_top_n
                    if family_projects_mode == "counts_only"
                    else None
                ),
                payload_data=payload,
                alerts_payload=alerts,
                rules_raw=alerts.get("rules"),
            )
            if family_projects_mode == "full":
                lines.append(f"- {line_label}: `{family_projects_text}`")
            lines.append(f"- Family Projects Mode: `{family_projects_mode}`")
            lines.append(f"- Family Projects Source: `{source_mode}`")
            lines.append(f"- Family Projects Severity Filter: `{severity_filter}`")
            order_mode = (
                "severity_then_project"
                if str(markdown_family_projects_order or "").strip().lower() == "severity_then_project"
                else "alphabetical"
            )
            lines.append(f"- Family Projects Order: `{order_mode}`")
            count_order_mode = (
                "by_total_desc"
                if str(markdown_family_projects_count_order or "").strip().lower() == "by_total_desc"
                else "by_family"
            )
            lines.append(f"- Family Projects Count Order: `{count_order_mode}`")
            count_render_mode = (
                "nonzero_buckets"
                if str(markdown_family_projects_count_render_mode or "").strip().lower() == "nonzero_buckets"
                else "full_fields"
            )
            lines.append(f"- Family Projects Count Render Mode: `{count_render_mode}`")
            count_visibility_mode = (
                "nonzero_all"
                if str(markdown_family_projects_count_visibility_mode or "").strip().lower() == "nonzero_all"
                else "all_rows"
            )
            lines.append(f"- Family Projects Count Visibility Mode: `{count_visibility_mode}`")
            count_inline_family_label_mode = (
                "title"
                if str(markdown_family_projects_count_inline_family_label_mode or "").strip().lower()
                == "title"
                else "raw"
            )
            lines.append(
                "- Family Projects Count Inline Family Label Mode: "
                f"`{count_inline_family_label_mode}`"
            )
            count_inline_bucket_label_mode = (
                "title"
                if str(markdown_family_projects_count_inline_bucket_label_mode or "").strip().lower()
                == "title"
                else "raw"
            )
            lines.append(
                "- Family Projects Count Inline Bucket Label Mode: "
                f"`{count_inline_bucket_label_mode}`"
            )
            count_export_mode = (
                "table"
                if str(markdown_family_projects_count_export_mode or "").strip().lower() == "table"
                else "inline"
            )
            lines.append(f"- Family Projects Count Export Mode: `{count_export_mode}`")
            count_table_style = (
                "minimal"
                if str(markdown_family_projects_count_table_style or "").strip().lower() == "minimal"
                else "full"
            )
            lines.append(f"- Family Projects Count Table Style: `{count_table_style}`")
            count_table_empty_mode = (
                "table_empty"
                if str(markdown_family_projects_count_table_empty_mode or "").strip().lower() == "table_empty"
                else "inline_none"
            )
            lines.append(f"- Family Projects Count Table Empty Mode: `{count_table_empty_mode}`")
            count_table_family_label_mode = (
                "title"
                if str(markdown_family_projects_count_table_family_label_mode or "").strip().lower()
                == "title"
                else "raw"
            )
            lines.append(
                "- Family Projects Count Table Family Label Mode: "
                f"`{count_table_family_label_mode}`"
            )
            count_table_header_label_mode = (
                "raw"
                if str(markdown_family_projects_count_table_header_label_mode or "").strip().lower()
                == "raw"
                else "title"
            )
            lines.append(
                "- Family Projects Count Table Header Label Mode: "
                f"`{count_table_header_label_mode}`"
            )
            count_table_family_label_overrides, family_override_malformed = _normalize_table_family_label_overrides(
                markdown_family_projects_count_table_family_label_overrides
            )
            if count_table_family_label_overrides:
                override_parts = ", ".join(
                    f"{family}={count_table_family_label_overrides[family]}"
                    for family in sorted(count_table_family_label_overrides.keys())
                )
            else:
                override_parts = "none"
            lines.append(
                "- Family Projects Count Table Family Label Overrides: "
                f"`{override_parts}`"
            )
            count_table_metric_label_mode = (
                "raw"
                if str(markdown_family_projects_count_table_metric_label_mode or "").strip().lower()
                == "raw"
                else "title"
            )
            lines.append(
                "- Family Projects Count Table Metric Label Mode: "
                f"`{count_table_metric_label_mode}`"
            )
            count_table_metric_label_overrides, metric_override_malformed = _normalize_table_metric_label_overrides(
                markdown_family_projects_count_table_metric_label_overrides
            )
            if count_table_metric_label_overrides:
                metric_override_parts = ", ".join(
                    f"{metric}={count_table_metric_label_overrides[metric]}"
                    for metric in sorted(count_table_metric_label_overrides.keys())
                )
            else:
                metric_override_parts = "none"
            lines.append(
                "- Family Projects Count Table Metric Label Overrides: "
                f"`{metric_override_parts}`"
            )
            count_label_override_diagnostics = bool(
                markdown_family_projects_count_label_override_diagnostics
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics: "
                f"`{count_label_override_diagnostics}`"
            )
            resolved_family_override_count = int(len(count_table_family_label_overrides))
            resolved_metric_override_count = int(len(count_table_metric_label_overrides))
            family_malformed_override_count = int(len(family_override_malformed))
            metric_malformed_override_count = int(len(metric_override_malformed))
            malformed_override_count = int(
                family_malformed_override_count + metric_malformed_override_count
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics Counters: "
                f"`resolved_family_count={resolved_family_override_count} "
                f"resolved_metric_count={resolved_metric_override_count} "
                f"family_malformed_count={family_malformed_override_count} "
                f"metric_malformed_count={metric_malformed_override_count}`"
            )
            count_label_override_diagnostics_severity_mode = str(
                markdown_family_projects_count_label_override_diagnostics_severity or "off"
            ).strip().lower()
            if count_label_override_diagnostics_severity_mode not in ("off", "note", "warn"):
                count_label_override_diagnostics_severity_mode = "off"
            lines.append(
                "- Family Projects Count Label Override Diagnostics Severity Mode: "
                f"`{count_label_override_diagnostics_severity_mode}`"
            )
            count_label_override_diagnostics_triggered = (
                malformed_override_count > 0 and count_label_override_diagnostics_severity_mode != "off"
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics Triggered: "
                f"`{count_label_override_diagnostics_triggered}`"
            )
            count_label_override_diagnostics_effective_severity = (
                count_label_override_diagnostics_severity_mode
                if count_label_override_diagnostics_triggered
                else "none"
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics Severity: "
                f"`{count_label_override_diagnostics_effective_severity}`"
            )
            count_label_override_diagnostics_json = bool(
                markdown_family_projects_count_label_override_diagnostics_json
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON: "
                f"`{count_label_override_diagnostics_json}`"
            )
            count_label_override_diagnostics_json_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_mode or "full"
            ).strip().lower()
            if count_label_override_diagnostics_json_mode not in ("full", "compact"):
                count_label_override_diagnostics_json_mode = "full"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Mode: "
                f"`{count_label_override_diagnostics_json_mode}`"
            )
            count_label_override_diagnostics_json_key_prefix_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode
                or "bridge_"
            ).strip().lower()
            if count_label_override_diagnostics_json_key_prefix_mode not in (
                "bridge_",
                "count_override_",
            ):
                count_label_override_diagnostics_json_key_prefix_mode = "bridge_"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Key Prefix Mode: "
                f"`{count_label_override_diagnostics_json_key_prefix_mode}`"
            )
            count_label_override_diagnostics_json_compact_profile = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_profile
                or "compact_min"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_profile not in (
                "compact_min",
                "compact_full",
            ):
                count_label_override_diagnostics_json_compact_profile = "compact_min"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Profile: "
                f"`{count_label_override_diagnostics_json_compact_profile}`"
            )
            count_label_override_diagnostics_json_compact_include_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode
                or "counts_only"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_include_mode not in (
                "none",
                "counts_only",
                "counts_plus_tokens",
                "counts_plus_tokens_if_truncated",
            ):
                count_label_override_diagnostics_json_compact_include_mode = "counts_only"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Include Mode: "
                f"`{count_label_override_diagnostics_json_compact_include_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_sort_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode
                or "input_order"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_sort_mode not in (
                "input_order",
                "lexicographic",
            ):
                count_label_override_diagnostics_json_compact_token_sort_mode = "input_order"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_sort_mode}`"
            )
            try:
                count_label_override_diagnostics_json_compact_token_max_per_scope = max(
                    0,
                    int(
                        markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope
                    ),
                )
            except (TypeError, ValueError):
                count_label_override_diagnostics_json_compact_token_max_per_scope = 0
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope: "
                f"`{count_label_override_diagnostics_json_compact_token_max_per_scope}`"
            )
            count_label_override_diagnostics_json_compact_token_overflow_suffix = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix
                if markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix
                is not None
                else ""
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix: "
                f"`{count_label_override_diagnostics_json_compact_token_overflow_suffix}`"
            )
            count_label_override_diagnostics_json_compact_token_overflow_suffix_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode
                or "include"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_overflow_suffix_mode not in (
                "include",
                "suppress",
            ):
                count_label_override_diagnostics_json_compact_token_overflow_suffix_mode = "include"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_overflow_suffix_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
                or "always"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode not in (
                "always",
                "if_truncated_only",
                "off",
            ):
                count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode = "always"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_list_guard_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_list_guard_mode not in (
                "off",
                "require_nonempty_tokens",
            ):
                count_label_override_diagnostics_json_compact_token_list_guard_mode = "off"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_list_guard_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_list_key_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode
                or "always"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_list_key_mode not in (
                "always",
                "if_nonempty",
                "if_truncated",
            ):
                count_label_override_diagnostics_json_compact_token_list_key_mode = "always"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_list_key_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_scope_fallback_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode
                or "selected_only"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_scope_fallback_mode not in (
                "selected_only",
                "auto_expand_when_empty",
            ):
                count_label_override_diagnostics_json_compact_token_scope_fallback_mode = "selected_only"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_scope_fallback_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_truncation_indicator_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_truncation_indicator_mode not in (
                "off",
                "summary_only",
                "per_scope",
            ):
                count_label_override_diagnostics_json_compact_token_truncation_indicator_mode = "off"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_truncation_indicator_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_scope_priority_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode
                or "family_first"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_scope_priority_mode not in (
                "family_first",
                "metric_first",
            ):
                count_label_override_diagnostics_json_compact_token_scope_priority_mode = "family_first"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Scope Priority Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_scope_priority_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_fallback_emission_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode
                or "first_success_only"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_fallback_emission_mode not in (
                "first_success_only",
                "all_eligible",
            ):
                count_label_override_diagnostics_json_compact_token_fallback_emission_mode = (
                    "first_success_only"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Emission Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_fallback_emission_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode not in (
                "off",
                "summary",
                "per_scope",
            ):
                count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode = "off"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode
                or "always"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode not in (
                "always",
                "fallback_only",
            ):
                count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode = (
                    "always"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode not in (
                "off",
                "summary",
                "per_scope",
            ):
                count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode = "off"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_profile_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_profile_mode not in (
                "off",
                "strict_minimal",
                "strict_verbose",
                "strict_debug",
            ):
                count_label_override_diagnostics_json_compact_token_marker_profile_mode = "off"
            marker_profile_defaults: dict[str, dict[str, str]] = {
                "strict_minimal": {
                    "marker_key_naming_mode": "short",
                    "marker_suppression_mode": "omit_when_no_token_payload",
                    "marker_summary_visibility_mode": "if_true_only",
                    "marker_scope_order_mode": "canonical",
                    "marker_list_visibility_mode": "if_nonempty",
                    "marker_key_prefix_mode": "markers",
                    "marker_boolean_type_visibility_mode": "selected_only",
                    "marker_summary_list_order_mode": "lexicographic",
                    "marker_summary_family_visibility_mode": "selected_only",
                    "marker_per_scope_family_visibility_mode": "none",
                    "marker_summary_boolean_family_visibility_mode": "selected_only",
                },
                "strict_verbose": {
                    "marker_key_naming_mode": "default",
                    "marker_suppression_mode": "off",
                    "marker_summary_visibility_mode": "always",
                    "marker_scope_order_mode": "canonical",
                    "marker_list_visibility_mode": "always",
                    "marker_key_prefix_mode": "inherit",
                    "marker_boolean_type_visibility_mode": "all",
                    "marker_summary_list_order_mode": "lexicographic",
                    "marker_summary_family_visibility_mode": "all",
                    "marker_per_scope_family_visibility_mode": "all",
                    "marker_summary_boolean_family_visibility_mode": "all",
                },
                "strict_debug": {
                    "marker_key_naming_mode": "default",
                    "marker_suppression_mode": "off",
                    "marker_summary_visibility_mode": "always",
                    "marker_scope_order_mode": "canonical",
                    "marker_list_visibility_mode": "always",
                    "marker_key_prefix_mode": "markers",
                    "marker_boolean_type_visibility_mode": "all",
                    "marker_summary_list_order_mode": "lexicographic",
                    "marker_summary_family_visibility_mode": "all",
                    "marker_per_scope_family_visibility_mode": "all",
                    "marker_summary_boolean_family_visibility_mode": "all",
                },
            }
            selected_profile_defaults = dict(
                marker_profile_defaults.get(
                    count_label_override_diagnostics_json_compact_token_marker_profile_mode,
                    {},
                )
            )
            marker_profile_mode_source = (
                "explicit_input"
                if count_label_override_diagnostics_json_compact_token_marker_profile_mode != "off"
                else "baseline_default"
            )
            marker_control_precedence: dict[str, str] = {}

            def _apply_marker_profile_default(
                raw_value: object,
                default_value: str,
                profile_key: str,
            ) -> object:
                raw_effective = str(
                    raw_value if raw_value is not None else default_value
                ).strip().lower()
                default_effective = str(default_value).strip().lower()
                if raw_effective != default_effective:
                    marker_control_precedence[profile_key] = "explicit_input"
                    return raw_value
                profile_value = selected_profile_defaults.get(profile_key)
                if profile_value is None:
                    marker_control_precedence[profile_key] = "baseline_default"
                    return raw_value
                marker_control_precedence[profile_key] = "profile_default"
                return profile_value

            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode,
                "default",
                "marker_key_naming_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode,
                "off",
                "marker_suppression_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode,
                "always",
                "marker_summary_visibility_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode,
                "priority",
                "marker_scope_order_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode,
                "always",
                "marker_list_visibility_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode,
                "inherit",
                "marker_key_prefix_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode,
                "all",
                "marker_boolean_type_visibility_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode,
                "insertion",
                "marker_summary_list_order_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode,
                "all",
                "marker_summary_family_visibility_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode,
                "all",
                "marker_per_scope_family_visibility_mode",
            )
            markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode = _apply_marker_profile_default(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode,
                "all",
                "marker_summary_boolean_family_visibility_mode",
            )

            def _marker_profile_source_for(profile_key: str) -> str:
                return str(marker_control_precedence.get(profile_key) or "baseline_default")
            marker_profile_precedence_keys: list[tuple[str, str]] = [
                ("key_naming_mode", "marker_key_naming_mode"),
                ("suppression_mode", "marker_suppression_mode"),
                ("summary_visibility_mode", "marker_summary_visibility_mode"),
                ("scope_order_mode", "marker_scope_order_mode"),
                ("list_visibility_mode", "marker_list_visibility_mode"),
                ("key_prefix_mode", "marker_key_prefix_mode"),
                ("boolean_type_visibility_mode", "marker_boolean_type_visibility_mode"),
                ("summary_list_order_mode", "marker_summary_list_order_mode"),
                ("summary_family_visibility_mode", "marker_summary_family_visibility_mode"),
                ("per_scope_family_visibility_mode", "marker_per_scope_family_visibility_mode"),
                (
                    "summary_boolean_family_visibility_mode",
                    "marker_summary_boolean_family_visibility_mode",
                ),
            ]
            count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                or "full"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode not in (
                "full",
                "summary_only",
            ):
                count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode = (
                    "full"
                )
            marker_profile_precedence_summary_counts = Counter(
                _marker_profile_source_for(profile_key)
                for _, profile_key in marker_profile_precedence_keys
            )
            marker_profile_precedence_summary_text = " ".join(
                [
                    f"explicit_input={int(marker_profile_precedence_summary_counts.get('explicit_input') or 0)}",
                    f"profile_default={int(marker_profile_precedence_summary_counts.get('profile_default') or 0)}",
                    f"baseline_default={int(marker_profile_precedence_summary_counts.get('baseline_default') or 0)}",
                ]
            )
            marker_profile_precedence_detail_text = " ".join(
                f"{label}={_marker_profile_source_for(profile_key)}"
                for label, profile_key in marker_profile_precedence_keys
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_profile_mode}`"
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode Source: "
                f"`{marker_profile_mode_source}`"
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Precedence Export Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode}`"
            )
            if (
                count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                == "summary_only"
            ):
                lines.append(
                    "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Precedence Summary: "
                    f"`{marker_profile_precedence_summary_text}`"
                )
            else:
                lines.append(
                    "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Precedence: "
                    f"`{marker_profile_precedence_detail_text}`"
                )
            count_label_override_diagnostics_json_compact_token_marker_key_naming_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
                or "default"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_key_naming_mode not in (
                "default",
                "short",
            ):
                count_label_override_diagnostics_json_compact_token_marker_key_naming_mode = (
                    "default"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_key_naming_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_suppression_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_suppression_mode not in (
                "off",
                "omit_when_no_token_payload",
            ):
                count_label_override_diagnostics_json_compact_token_marker_suppression_mode = (
                    "off"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_suppression_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
                or "always"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode not in (
                "always",
                "if_true_only",
            ):
                count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode = (
                    "always"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Visibility Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_scope_order_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
                or "priority"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_scope_order_mode not in (
                "canonical",
                "priority",
            ):
                count_label_override_diagnostics_json_compact_token_marker_scope_order_mode = (
                    "priority"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Scope Order Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_scope_order_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
                or "always"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode not in (
                "always",
                "if_nonempty",
            ):
                count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode = (
                    "always"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker List Visibility Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
                or "inherit"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode not in (
                "inherit",
                "markers",
            ):
                count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode = (
                    "inherit"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Prefix Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                or "all"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode not in (
                "all",
                "fallback_only",
                "selected_only",
                "truncation_only",
                "fallback_selected",
                "fallback_truncation",
                "selected_truncation",
                "none",
            ):
                count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode = (
                    "all"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Boolean Type Visibility Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
                or "insertion"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode not in (
                "insertion",
                "lexicographic",
            ):
                count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode = (
                    "insertion"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary List Order Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                or "all"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode not in (
                "all",
                "fallback_only",
                "selected_only",
                "none",
            ):
                count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode = (
                    "all"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Family Visibility Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                or "all"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode not in (
                "all",
                "fallback_only",
                "selected_only",
                "truncation_only",
                "none",
            ):
                count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode = (
                    "all"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Per Scope Family Visibility Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                or "all"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode not in (
                "all",
                "fallback_only",
                "selected_only",
                "truncation_only",
                "none",
            ):
                count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode = (
                    "all"
                )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Boolean Family Visibility Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode}`"
            )
            marker_profile_signature_payload = {
                "marker_profile_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_profile_mode
                ),
                "marker_key_naming_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
                ),
                "marker_suppression_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_suppression_mode
                ),
                "marker_summary_visibility_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
                ),
                "marker_scope_order_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
                ),
                "marker_list_visibility_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
                ),
                "marker_key_prefix_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
                ),
                "marker_boolean_type_visibility_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                ),
                "marker_summary_list_order_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
                ),
                "marker_summary_family_visibility_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                ),
                "marker_per_scope_family_visibility_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                ),
                "marker_summary_boolean_family_visibility_mode": str(
                    count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                ),
            }
            marker_profile_signature = hashlib.sha256(
                json.dumps(
                    marker_profile_signature_payload,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Signature: "
                f"`{marker_profile_signature}`"
            )
            marker_profile_signature_expected = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected
                or ""
            ).strip().lower()
            marker_profile_signature_expected_valid = bool(
                marker_profile_signature_expected
                and len(marker_profile_signature_expected) == 64
                and all(
                    ch in "0123456789abcdef"
                    for ch in marker_profile_signature_expected
                )
            )
            marker_profile_signature_match_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode
                or "off"
            ).strip().lower()
            if marker_profile_signature_match_mode not in ("off", "warn", "strict"):
                marker_profile_signature_match_mode = "off"
            marker_profile_signature_match = (
                bool(marker_profile_signature_expected_valid)
                and marker_profile_signature == marker_profile_signature_expected
            )
            marker_profile_signature_drift_detected = bool(
                marker_profile_signature_expected
                and (not marker_profile_signature_match)
            )
            if markdown_runtime_telemetry is not None:
                markdown_runtime_telemetry["marker_profile_signature"] = str(
                    marker_profile_signature
                )
                markdown_runtime_telemetry[
                    "marker_profile_signature_expected"
                ] = str(marker_profile_signature_expected)
                markdown_runtime_telemetry[
                    "marker_profile_signature_expected_valid"
                ] = bool(marker_profile_signature_expected_valid)
                markdown_runtime_telemetry[
                    "marker_profile_signature_match_mode"
                ] = str(marker_profile_signature_match_mode)
                markdown_runtime_telemetry[
                    "marker_profile_signature_match"
                ] = bool(marker_profile_signature_match)
                if marker_profile_signature_match_mode == "strict":
                    markdown_runtime_telemetry[
                        "marker_profile_signature_strict_mode"
                    ] = True
                if marker_profile_signature_drift_detected:
                    markdown_runtime_telemetry[
                        "marker_profile_signature_drift_detected"
                    ] = True
                if (
                    marker_profile_signature_drift_detected
                    and marker_profile_signature_match_mode == "strict"
                ):
                    markdown_runtime_telemetry[
                        "marker_profile_signature_drift_exit_eligible"
                    ] = True
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Signature Expected: "
                f"`{marker_profile_signature_expected or 'none'}`"
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Signature Expected Valid: "
                f"`{marker_profile_signature_expected_valid}`"
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Signature Match Mode: "
                f"`{marker_profile_signature_match_mode}`"
            )
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Signature Match: "
                f"`{marker_profile_signature_match}`"
            )
            if marker_profile_signature_drift_detected and marker_profile_signature_match_mode in (
                "warn",
                "strict",
            ):
                lines.append(
                    "- Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Signature Drift Detected: "
                    "`true`"
                )
            count_label_override_diagnostics_json_compact_token_scope_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode
                or "both"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_scope_mode not in (
                "family_only",
                "metric_only",
                "both",
            ):
                count_label_override_diagnostics_json_compact_token_scope_mode = "both"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_scope_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_dedup_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_dedup_mode not in ("off", "on"):
                count_label_override_diagnostics_json_compact_token_dedup_mode = "off"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_dedup_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_normalization_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode
                or "preserve"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_normalization_mode not in (
                "preserve",
                "lower",
            ):
                count_label_override_diagnostics_json_compact_token_normalization_mode = "preserve"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Normalization Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_normalization_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_sanitization_mode = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode
                or "off"
            ).strip().lower()
            if count_label_override_diagnostics_json_compact_token_sanitization_mode not in (
                "off",
                "ascii_safe",
            ):
                count_label_override_diagnostics_json_compact_token_sanitization_mode = "off"
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Mode: "
                f"`{count_label_override_diagnostics_json_compact_token_sanitization_mode}`"
            )
            count_label_override_diagnostics_json_compact_token_sanitization_replacement_char = str(
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char
                if markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char
                is not None
                else ""
            )
            if count_label_override_diagnostics_json_compact_token_sanitization_mode == "ascii_safe":
                replacement_effective = "".join(
                    ch
                    for ch in count_label_override_diagnostics_json_compact_token_sanitization_replacement_char
                    if 32 <= ord(ch) <= 126
                )
                if not replacement_effective:
                    replacement_effective = "_"
            else:
                replacement_effective = count_label_override_diagnostics_json_compact_token_sanitization_replacement_char
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char: "
                f"`{replacement_effective}`"
            )
            try:
                count_label_override_diagnostics_json_compact_token_min_length = max(
                    0,
                    int(markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length),
                )
            except (TypeError, ValueError):
                count_label_override_diagnostics_json_compact_token_min_length = 1
            lines.append(
                "- Family Projects Count Label Override Diagnostics JSON Compact Token Min Length: "
                f"`{count_label_override_diagnostics_json_compact_token_min_length}`"
            )
            count_label_override_ci_policy_mode = str(
                markdown_family_projects_count_label_override_ci_policy_mode or "off"
            ).strip().lower()
            if count_label_override_ci_policy_mode not in ("off", "strict"):
                count_label_override_ci_policy_mode = "off"
            lines.append(
                "- Family Projects Count Label Override CI Policy Mode: "
                f"`{count_label_override_ci_policy_mode}`"
            )
            count_label_override_fail_ci_recommended = bool(
                count_label_override_ci_policy_mode == "strict" and malformed_override_count > 0
            )
            if (
                marker_profile_signature_drift_detected
                and marker_profile_signature_match_mode == "strict"
            ):
                count_label_override_fail_ci_recommended = True
            lines.append(
                "- Family Projects Count Label Override Fail CI Recommended: "
                f"`{count_label_override_fail_ci_recommended}`"
            )
            if count_label_override_diagnostics:
                family_malformed_text = (
                    ", ".join(family_override_malformed)
                    if family_override_malformed
                    else "none"
                )
                metric_malformed_text = (
                    ", ".join(metric_override_malformed)
                    if metric_override_malformed
                    else "none"
                )
                lines.append(
                    "- Family Projects Count Label Override Diagnostics Detail: "
                    f"`family_malformed=[{family_malformed_text}] metric_malformed=[{metric_malformed_text}]`"
                )
            count_table_row_order_mode = str(
                markdown_family_projects_count_table_row_order_mode or "count_order"
            ).strip().lower()
            if count_table_row_order_mode not in ("count_order", "canonical", "sorted"):
                count_table_row_order_mode = "count_order"
            lines.append(
                "- Family Projects Count Table Row Order Mode: "
                f"`{count_table_row_order_mode}`"
            )
            count_table_include_schema_signature = bool(
                markdown_family_projects_count_table_include_schema_signature
            )
            lines.append(
                "- Family Projects Count Table Include Schema Signature: "
                f"`{count_table_include_schema_signature}`"
            )
            if count_label_override_diagnostics_json:
                if count_label_override_diagnostics_json_mode == "compact":
                    prefix = str(count_label_override_diagnostics_json_key_prefix_mode)
                    diagnostics_json_payload = {
                        f"{prefix}diagnostics_enabled": bool(count_label_override_diagnostics),
                        f"{prefix}severity_mode": str(count_label_override_diagnostics_severity_mode),
                        f"{prefix}triggered": bool(count_label_override_diagnostics_triggered),
                        f"{prefix}severity": str(count_label_override_diagnostics_effective_severity),
                        f"{prefix}ci_policy_mode": str(count_label_override_ci_policy_mode),
                        f"{prefix}fail_ci_recommended": bool(count_label_override_fail_ci_recommended),
                        f"{prefix}compact_profile": str(
                            count_label_override_diagnostics_json_compact_profile
                        ),
                        f"{prefix}compact_include_mode": str(
                            count_label_override_diagnostics_json_compact_include_mode
                        ),
                        f"{prefix}compact_token_sort_mode": str(
                            count_label_override_diagnostics_json_compact_token_sort_mode
                        ),
                        f"{prefix}compact_token_max_per_scope": int(
                            count_label_override_diagnostics_json_compact_token_max_per_scope
                        ),
                        f"{prefix}compact_token_overflow_suffix": str(
                            count_label_override_diagnostics_json_compact_token_overflow_suffix
                        ),
                        f"{prefix}compact_token_overflow_suffix_mode": str(
                            count_label_override_diagnostics_json_compact_token_overflow_suffix_mode
                        ),
                        f"{prefix}compact_token_omitted_count_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
                        ),
                        f"{prefix}compact_token_list_guard_mode": str(
                            count_label_override_diagnostics_json_compact_token_list_guard_mode
                        ),
                        f"{prefix}compact_token_list_key_mode": str(
                            count_label_override_diagnostics_json_compact_token_list_key_mode
                        ),
                        f"{prefix}compact_token_scope_fallback_mode": str(
                            count_label_override_diagnostics_json_compact_token_scope_fallback_mode
                        ),
                        f"{prefix}compact_token_truncation_indicator_mode": str(
                            count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                        ),
                        f"{prefix}compact_token_scope_priority_mode": str(
                            count_label_override_diagnostics_json_compact_token_scope_priority_mode
                        ),
                        f"{prefix}compact_token_fallback_emission_mode": str(
                            count_label_override_diagnostics_json_compact_token_fallback_emission_mode
                        ),
                        f"{prefix}compact_token_fallback_source_marker_mode": str(
                            count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                        ),
                        f"{prefix}compact_token_fallback_source_marker_activation_mode": str(
                            count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode
                        ),
                        f"{prefix}compact_token_selected_scope_marker_mode": str(
                            count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                        ),
                        f"{prefix}compact_token_marker_key_naming_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
                        ),
                        f"{prefix}compact_token_marker_suppression_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_suppression_mode
                        ),
                        f"{prefix}compact_token_marker_summary_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
                        ),
                        f"{prefix}compact_token_marker_scope_order_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
                        ),
                        f"{prefix}compact_token_marker_list_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
                        ),
                        f"{prefix}compact_token_marker_key_prefix_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
                        ),
                        f"{prefix}compact_token_marker_boolean_type_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                        ),
                        f"{prefix}compact_token_marker_summary_list_order_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
                        ),
                        f"{prefix}compact_token_marker_summary_family_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                        ),
                        f"{prefix}compact_token_marker_per_scope_family_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                        ),
                        f"{prefix}compact_token_marker_summary_boolean_family_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                        ),
                        f"{prefix}compact_token_marker_profile_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_profile_mode
                        ),
                        f"{prefix}compact_token_marker_precedence_export_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                        ),
                        f"{prefix}compact_token_marker_profile_signature": str(
                            marker_profile_signature
                        ),
                        f"{prefix}compact_token_marker_profile_signature_expected": str(
                            marker_profile_signature_expected
                        ),
                        f"{prefix}compact_token_marker_profile_signature_expected_valid": bool(
                            marker_profile_signature_expected_valid
                        ),
                        f"{prefix}compact_token_marker_profile_signature_match_mode": str(
                            marker_profile_signature_match_mode
                        ),
                        f"{prefix}compact_token_marker_profile_signature_match": bool(
                            marker_profile_signature_match
                        ),
                        f"{prefix}compact_token_marker_profile_signature_drift_detected": bool(
                            marker_profile_signature_drift_detected
                        ),
                        f"{prefix}compact_token_marker_profile_mode_source": str(
                            marker_profile_mode_source
                        ),
                        f"{prefix}compact_token_marker_key_naming_mode_source": str(
                            _marker_profile_source_for("marker_key_naming_mode")
                        ),
                        f"{prefix}compact_token_marker_suppression_mode_source": str(
                            _marker_profile_source_for("marker_suppression_mode")
                        ),
                        f"{prefix}compact_token_marker_summary_visibility_mode_source": str(
                            _marker_profile_source_for("marker_summary_visibility_mode")
                        ),
                        f"{prefix}compact_token_marker_scope_order_mode_source": str(
                            _marker_profile_source_for("marker_scope_order_mode")
                        ),
                        f"{prefix}compact_token_marker_list_visibility_mode_source": str(
                            _marker_profile_source_for("marker_list_visibility_mode")
                        ),
                        f"{prefix}compact_token_marker_key_prefix_mode_source": str(
                            _marker_profile_source_for("marker_key_prefix_mode")
                        ),
                        f"{prefix}compact_token_marker_boolean_type_visibility_mode_source": str(
                            _marker_profile_source_for("marker_boolean_type_visibility_mode")
                        ),
                        f"{prefix}compact_token_marker_summary_list_order_mode_source": str(
                            _marker_profile_source_for("marker_summary_list_order_mode")
                        ),
                        f"{prefix}compact_token_marker_summary_family_visibility_mode_source": str(
                            _marker_profile_source_for("marker_summary_family_visibility_mode")
                        ),
                        f"{prefix}compact_token_marker_per_scope_family_visibility_mode_source": str(
                            _marker_profile_source_for("marker_per_scope_family_visibility_mode")
                        ),
                        f"{prefix}compact_token_marker_summary_boolean_family_visibility_mode_source": str(
                            _marker_profile_source_for("marker_summary_boolean_family_visibility_mode")
                        ),
                        f"{prefix}compact_token_scope_mode": str(
                            count_label_override_diagnostics_json_compact_token_scope_mode
                        ),
                        f"{prefix}compact_token_dedup_mode": str(
                            count_label_override_diagnostics_json_compact_token_dedup_mode
                        ),
                        f"{prefix}compact_token_normalization_mode": str(
                            count_label_override_diagnostics_json_compact_token_normalization_mode
                        ),
                        f"{prefix}compact_token_sanitization_mode": str(
                            count_label_override_diagnostics_json_compact_token_sanitization_mode
                        ),
                        f"{prefix}compact_token_sanitization_replacement_char": str(
                            replacement_effective
                        ),
                        f"{prefix}compact_token_min_length": int(
                            count_label_override_diagnostics_json_compact_token_min_length
                        ),
                    }
                    if (
                        count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                        == "summary_only"
                    ):
                        for source_key in (
                            "compact_token_marker_key_naming_mode_source",
                            "compact_token_marker_suppression_mode_source",
                            "compact_token_marker_summary_visibility_mode_source",
                            "compact_token_marker_scope_order_mode_source",
                            "compact_token_marker_list_visibility_mode_source",
                            "compact_token_marker_key_prefix_mode_source",
                            "compact_token_marker_boolean_type_visibility_mode_source",
                            "compact_token_marker_summary_list_order_mode_source",
                            "compact_token_marker_summary_family_visibility_mode_source",
                            "compact_token_marker_per_scope_family_visibility_mode_source",
                            "compact_token_marker_summary_boolean_family_visibility_mode_source",
                        ):
                            diagnostics_json_payload.pop(f"{prefix}{source_key}", None)
                        diagnostics_json_payload[
                            f"{prefix}compact_token_marker_precedence_summary"
                        ] = str(marker_profile_precedence_summary_text)
                    if count_label_override_diagnostics_json_compact_include_mode in (
                        "counts_only",
                        "counts_plus_tokens",
                        "counts_plus_tokens_if_truncated",
                    ):
                        diagnostics_json_payload.update(
                            {
                                f"{prefix}resolved_family_count": int(resolved_family_override_count),
                                f"{prefix}resolved_metric_count": int(resolved_metric_override_count),
                                f"{prefix}family_malformed_count": int(family_malformed_override_count),
                                f"{prefix}metric_malformed_count": int(metric_malformed_override_count),
                            }
                        )
                    if count_label_override_diagnostics_json_compact_include_mode in (
                        "counts_plus_tokens",
                        "counts_plus_tokens_if_truncated",
                    ):
                        emit_tokens_only_if_truncated = (
                            count_label_override_diagnostics_json_compact_include_mode
                            == "counts_plus_tokens_if_truncated"
                        )
                        if (
                            count_label_override_diagnostics_json_compact_token_scope_priority_mode
                            == "metric_first"
                        ):
                            ordered_scope_names = ["metric", "family"]
                        else:
                            ordered_scope_names = ["family", "metric"]
                        if count_label_override_diagnostics_json_compact_token_scope_mode == "family_only":
                            selected_scope_names = ["family"]
                        elif count_label_override_diagnostics_json_compact_token_scope_mode == "metric_only":
                            selected_scope_names = ["metric"]
                        else:
                            selected_scope_names = ["family", "metric"]
                        selected_scope_names = [
                            scope_name
                            for scope_name in ordered_scope_names
                            if scope_name in selected_scope_names
                        ]
                        if (
                            count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
                            == "canonical"
                        ):
                            marker_scope_names = ["family", "metric"]
                        else:
                            marker_scope_names = list(ordered_scope_names)
                        scope_truncation_markers: dict[str, bool] = {}
                        marker_key_prefix = (
                            f"{prefix}marker_"
                            if (
                                count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
                                == "markers"
                            )
                            else str(prefix)
                        )
                        marker_boolean_type_visibility_allowed: dict[str, bool] = {
                            "fallback": bool(
                                count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                                in (
                                    "all",
                                    "fallback_only",
                                    "fallback_selected",
                                    "fallback_truncation",
                                )
                            ),
                            "selected": bool(
                                count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                                in (
                                    "all",
                                    "selected_only",
                                    "fallback_selected",
                                    "selected_truncation",
                                )
                            ),
                            "truncation": bool(
                                count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                                in (
                                    "all",
                                    "truncation_only",
                                    "fallback_truncation",
                                    "selected_truncation",
                                )
                            ),
                        }
                        marker_summary_family_visibility_allowed: dict[str, bool] = {
                            "fallback": bool(
                                count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                                in ("all", "fallback_only")
                            ),
                            "selected": bool(
                                count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                                in ("all", "selected_only")
                            ),
                        }
                        marker_per_scope_family_visibility_allowed: dict[str, bool] = {
                            "fallback": bool(
                                count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                                in ("all", "fallback_only")
                            ),
                            "selected": bool(
                                count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                                in ("all", "selected_only")
                            ),
                            "truncation": bool(
                                count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                                in ("all", "truncation_only")
                            ),
                        }
                        marker_summary_boolean_family_visibility_allowed: dict[str, bool] = {
                            "fallback": bool(
                                count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                                in ("all", "fallback_only")
                            ),
                            "selected": bool(
                                count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                                in ("all", "selected_only")
                            ),
                            "truncation": bool(
                                count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                                in ("all", "truncation_only")
                            ),
                        }

                        def _marker_key_for(
                            marker_kind: str,
                            *,
                            scope_name: str | None = None,
                        ) -> str:
                            use_short_marker_keys = bool(
                                count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
                                == "short"
                            )
                            if marker_kind == "fallback_used":
                                return "fb_used" if use_short_marker_keys else "fallback_used"
                            if marker_kind == "fallback_source_scopes":
                                return "fb_scopes" if use_short_marker_keys else "fallback_source_scopes"
                            if marker_kind == "selected_source_used":
                                return "sel_used" if use_short_marker_keys else "selected_source_used"
                            if marker_kind == "selected_source_scopes":
                                return "sel_scopes" if use_short_marker_keys else "selected_source_scopes"
                            if marker_kind == "summary_malformed_tokens_truncated":
                                return (
                                    "tokens_trunc"
                                    if use_short_marker_keys
                                    else "malformed_tokens_truncated"
                                )
                            if marker_kind == "scope_fallback_source" and scope_name:
                                return (
                                    f"{scope_name}_fb_source"
                                    if use_short_marker_keys
                                    else f"{scope_name}_fallback_source"
                                )
                            if marker_kind == "scope_selected_source" and scope_name:
                                return (
                                    f"{scope_name}_sel_source"
                                    if use_short_marker_keys
                                    else f"{scope_name}_selected_source"
                                )
                            if marker_kind == "scope_malformed_tokens_truncated" and scope_name:
                                return (
                                    f"{scope_name}_tokens_trunc"
                                    if use_short_marker_keys
                                    else f"{scope_name}_malformed_tokens_truncated"
                                )
                            return marker_kind

                        def _emit_marker_bool(
                            marker_key_suffix: str,
                            value: bool,
                            *,
                            marker_boolean_type: str,
                            marker_scope_family: str | None = None,
                            marker_summary_boolean_family: str | None = None,
                        ) -> None:
                            if marker_scope_family is not None and not bool(
                                marker_per_scope_family_visibility_allowed.get(
                                    str(marker_scope_family), False
                                )
                            ):
                                return
                            if marker_summary_boolean_family is not None and not bool(
                                marker_summary_boolean_family_visibility_allowed.get(
                                    str(marker_summary_boolean_family), False
                                )
                            ):
                                return
                            if not bool(
                                marker_boolean_type_visibility_allowed.get(
                                    str(marker_boolean_type), False
                                )
                            ):
                                return
                            if (
                                count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
                                == "if_true_only"
                                and not bool(value)
                            ):
                                return
                            diagnostics_json_payload[
                                f"{marker_key_prefix}{marker_key_suffix}"
                            ] = bool(value)

                        def _emit_marker_list(
                            marker_key_suffix: str,
                            values: list[str],
                            *,
                            marker_summary_family: str,
                        ) -> None:
                            if not bool(
                                marker_summary_family_visibility_allowed.get(
                                    str(marker_summary_family), False
                                )
                            ):
                                return
                            list_values = list(values)
                            if (
                                count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
                                == "lexicographic"
                            ):
                                list_values = sorted(str(value) for value in list_values)
                            if (
                                count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
                                == "if_nonempty"
                                and not list_values
                            ):
                                return
                            diagnostics_json_payload[
                                f"{marker_key_prefix}{marker_key_suffix}"
                            ] = list_values

                        def _collect_scope_token_payload(
                            scope_name: str,
                            *,
                            force_list_key_mode: str | None = None,
                            include_truncation_indicator: bool = False,
                        ) -> tuple[dict[str, object], bool, bool, int]:
                            source_tokens = (
                                family_override_malformed
                                if scope_name == "family"
                                else metric_override_malformed
                            )
                            scope_compact_tokens, scope_compact_tokens_omitted = _prepare_compact_tokens(
                                source_tokens,
                                sort_mode=count_label_override_diagnostics_json_compact_token_sort_mode,
                                max_per_scope=count_label_override_diagnostics_json_compact_token_max_per_scope,
                                overflow_suffix=count_label_override_diagnostics_json_compact_token_overflow_suffix,
                                overflow_suffix_mode=count_label_override_diagnostics_json_compact_token_overflow_suffix_mode,
                                dedup_mode=count_label_override_diagnostics_json_compact_token_dedup_mode,
                                normalization_mode=count_label_override_diagnostics_json_compact_token_normalization_mode,
                                sanitization_mode=count_label_override_diagnostics_json_compact_token_sanitization_mode,
                                sanitization_replacement_char=replacement_effective,
                                min_length=count_label_override_diagnostics_json_compact_token_min_length,
                            )
                            scope_should_emit = (not emit_tokens_only_if_truncated) or (
                                scope_compact_tokens_omitted > 0
                            )
                            if (
                                scope_should_emit
                                and count_label_override_diagnostics_json_compact_token_list_guard_mode
                                == "require_nonempty_tokens"
                                and not scope_compact_tokens
                            ):
                                scope_should_emit = False
                            if not scope_should_emit:
                                return {}, False, False, int(scope_compact_tokens_omitted)

                            scope_payload: dict[str, object] = {}
                            list_key_mode_effective = str(
                                force_list_key_mode
                                if force_list_key_mode is not None
                                else count_label_override_diagnostics_json_compact_token_list_key_mode
                            ).strip().lower()
                            if list_key_mode_effective not in (
                                "always",
                                "if_nonempty",
                                "if_truncated",
                            ):
                                list_key_mode_effective = (
                                    count_label_override_diagnostics_json_compact_token_list_key_mode
                                )
                            scope_list_key_emitted = False
                            if list_key_mode_effective == "always":
                                scope_list_key_emitted = True
                            elif list_key_mode_effective == "if_nonempty":
                                scope_list_key_emitted = bool(scope_compact_tokens)
                            elif list_key_mode_effective == "if_truncated":
                                scope_list_key_emitted = bool(scope_compact_tokens_omitted > 0)
                            if scope_list_key_emitted:
                                scope_payload[f"{prefix}{scope_name}_malformed_tokens"] = list(
                                    scope_compact_tokens
                                )

                            scope_emit_omitted_count = False
                            if (
                                count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
                                == "always"
                            ):
                                scope_emit_omitted_count = True
                            elif (
                                count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
                                == "if_truncated_only"
                                and scope_compact_tokens_omitted > 0
                            ):
                                scope_emit_omitted_count = True
                            if scope_emit_omitted_count:
                                scope_payload[
                                    f"{prefix}{scope_name}_malformed_tokens_omitted_count"
                                ] = int(scope_compact_tokens_omitted)
                            if include_truncation_indicator:
                                scope_truncation_markers[scope_name] = bool(
                                    scope_compact_tokens_omitted > 0
                                )
                            return (
                                scope_payload,
                                scope_list_key_emitted,
                                bool(scope_payload),
                                int(scope_compact_tokens_omitted),
                            )

                        any_scope_list_key_emitted = False
                        any_scope_truncated = False
                        selected_source_scopes_emitted: list[str] = []
                        fallback_source_scopes_emitted: list[str] = []
                        for selected_scope_name in selected_scope_names:
                            (
                                scope_payload,
                                scope_list_key_emitted,
                                scope_payload_emitted,
                                scope_compact_tokens_omitted,
                            ) = _collect_scope_token_payload(
                                selected_scope_name,
                                include_truncation_indicator=(
                                    count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                                    == "per_scope"
                                ),
                            )
                            if scope_payload_emitted:
                                diagnostics_json_payload.update(scope_payload)
                            if scope_list_key_emitted:
                                any_scope_list_key_emitted = True
                                if selected_scope_name not in selected_source_scopes_emitted:
                                    selected_source_scopes_emitted.append(selected_scope_name)
                            if scope_compact_tokens_omitted > 0:
                                any_scope_truncated = True

                        if (
                            count_label_override_diagnostics_json_compact_token_scope_fallback_mode
                            == "auto_expand_when_empty"
                            and not any_scope_list_key_emitted
                        ):
                            fallback_scope_names = [
                                scope_name
                                for scope_name in ordered_scope_names
                                if scope_name not in selected_scope_names
                            ]
                            if not fallback_scope_names:
                                fallback_scope_names = list(ordered_scope_names)
                            for fallback_scope_name in fallback_scope_names:
                                (
                                    scope_payload,
                                    scope_list_key_emitted,
                                    scope_payload_emitted,
                                    scope_compact_tokens_omitted,
                                ) = _collect_scope_token_payload(
                                    fallback_scope_name,
                                    force_list_key_mode="if_nonempty",
                                    include_truncation_indicator=(
                                        count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                                        == "per_scope"
                                    ),
                                )
                                if scope_payload_emitted:
                                    diagnostics_json_payload.update(scope_payload)
                                    if fallback_scope_name not in fallback_source_scopes_emitted:
                                        fallback_source_scopes_emitted.append(fallback_scope_name)
                                if scope_compact_tokens_omitted > 0:
                                    any_scope_truncated = True
                                if scope_list_key_emitted:
                                    any_scope_list_key_emitted = True
                                    if (
                                        count_label_override_diagnostics_json_compact_token_fallback_emission_mode
                                        == "first_success_only"
                                    ):
                                        break
                        fallback_used = bool(fallback_source_scopes_emitted)
                        marker_payload_allowed = bool(
                            count_label_override_diagnostics_json_compact_token_marker_suppression_mode
                            == "off"
                            or any_scope_list_key_emitted
                        )
                        if (
                            count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                            == "per_scope"
                            and marker_payload_allowed
                        ):
                            for scope_name in marker_scope_names:
                                if scope_name not in scope_truncation_markers:
                                    continue
                                _emit_marker_bool(
                                    _marker_key_for(
                                        "scope_malformed_tokens_truncated",
                                        scope_name=scope_name,
                                    ),
                                    bool(scope_truncation_markers.get(scope_name)),
                                    marker_boolean_type="truncation",
                                    marker_scope_family="truncation",
                                )
                        if (
                            count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                            != "off"
                            and marker_payload_allowed
                        ):
                            fallback_marker_should_emit = bool(
                                count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode
                                == "always"
                                or fallback_used
                            )
                            if fallback_marker_should_emit:
                                _emit_marker_bool(
                                    _marker_key_for("fallback_used"),
                                    bool(fallback_used),
                                    marker_boolean_type="fallback",
                                    marker_summary_boolean_family="fallback",
                                )
                                if (
                                    count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                                    == "summary"
                                ):
                                    _emit_marker_list(
                                        _marker_key_for("fallback_source_scopes"),
                                        fallback_source_scopes_emitted,
                                        marker_summary_family="fallback",
                                    )
                                elif (
                                    count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                                    == "per_scope"
                                ):
                                    for scope_name in marker_scope_names:
                                        _emit_marker_bool(
                                            _marker_key_for(
                                                "scope_fallback_source",
                                                scope_name=scope_name,
                                            ),
                                            bool(scope_name in fallback_source_scopes_emitted),
                                            marker_boolean_type="fallback",
                                            marker_scope_family="fallback",
                                        )
                        if (
                            count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                            != "off"
                            and selected_source_scopes_emitted
                            and marker_payload_allowed
                        ):
                            _emit_marker_bool(
                                _marker_key_for("selected_source_used"),
                                True,
                                marker_boolean_type="selected",
                                marker_summary_boolean_family="selected",
                            )
                            if (
                                count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                                == "summary"
                            ):
                                _emit_marker_list(
                                    _marker_key_for("selected_source_scopes"),
                                    selected_source_scopes_emitted,
                                    marker_summary_family="selected",
                                )
                            elif (
                                count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                                == "per_scope"
                            ):
                                for scope_name in marker_scope_names:
                                    _emit_marker_bool(
                                        _marker_key_for(
                                            "scope_selected_source",
                                            scope_name=scope_name,
                                        ),
                                        bool(scope_name in selected_source_scopes_emitted),
                                        marker_boolean_type="selected",
                                        marker_scope_family="selected",
                                    )
                        if (
                            count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                            == "summary_only"
                            and marker_payload_allowed
                        ):
                            _emit_marker_bool(
                                _marker_key_for("summary_malformed_tokens_truncated"),
                                bool(any_scope_truncated),
                                marker_boolean_type="truncation",
                                marker_summary_boolean_family="truncation",
                            )
                    if count_label_override_diagnostics_json_compact_profile == "compact_full":
                        diagnostics_json_payload.update(
                            {
                                f"{prefix}json_mode": str(count_label_override_diagnostics_json_mode),
                                f"{prefix}json_key_prefix_mode": str(
                                    count_label_override_diagnostics_json_key_prefix_mode
                                ),
                                f"{prefix}inline_family_label_mode": str(count_inline_family_label_mode),
                                f"{prefix}inline_bucket_label_mode": str(count_inline_bucket_label_mode),
                                f"{prefix}table_family_label_mode": str(count_table_family_label_mode),
                                f"{prefix}table_header_label_mode": str(count_table_header_label_mode),
                                f"{prefix}table_metric_label_mode": str(count_table_metric_label_mode),
                                f"{prefix}table_row_order_mode": str(count_table_row_order_mode),
                                f"{prefix}table_include_schema_signature": bool(
                                    count_table_include_schema_signature
                                ),
                            }
                        )
                else:
                    diagnostics_json_payload = {
                        "diagnostics_enabled": bool(count_label_override_diagnostics),
                        "severity_mode": str(count_label_override_diagnostics_severity_mode),
                        "triggered": bool(count_label_override_diagnostics_triggered),
                        "severity": str(count_label_override_diagnostics_effective_severity),
                        "ci_policy_mode": str(count_label_override_ci_policy_mode),
                        "fail_ci_recommended": bool(count_label_override_fail_ci_recommended),
                        "json_mode": str(count_label_override_diagnostics_json_mode),
                        "json_key_prefix_mode": str(count_label_override_diagnostics_json_key_prefix_mode),
                        "json_compact_profile": str(
                            count_label_override_diagnostics_json_compact_profile
                        ),
                        "json_compact_include_mode": str(
                            count_label_override_diagnostics_json_compact_include_mode
                        ),
                        "json_compact_token_sort_mode": str(
                            count_label_override_diagnostics_json_compact_token_sort_mode
                        ),
                        "json_compact_token_max_per_scope": int(
                            count_label_override_diagnostics_json_compact_token_max_per_scope
                        ),
                        "json_compact_token_overflow_suffix": str(
                            count_label_override_diagnostics_json_compact_token_overflow_suffix
                        ),
                        "json_compact_token_overflow_suffix_mode": str(
                            count_label_override_diagnostics_json_compact_token_overflow_suffix_mode
                        ),
                        "json_compact_token_omitted_count_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
                        ),
                        "json_compact_token_list_guard_mode": str(
                            count_label_override_diagnostics_json_compact_token_list_guard_mode
                        ),
                        "json_compact_token_list_key_mode": str(
                            count_label_override_diagnostics_json_compact_token_list_key_mode
                        ),
                        "json_compact_token_scope_fallback_mode": str(
                            count_label_override_diagnostics_json_compact_token_scope_fallback_mode
                        ),
                        "json_compact_token_truncation_indicator_mode": str(
                            count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                        ),
                        "json_compact_token_scope_priority_mode": str(
                            count_label_override_diagnostics_json_compact_token_scope_priority_mode
                        ),
                        "json_compact_token_fallback_emission_mode": str(
                            count_label_override_diagnostics_json_compact_token_fallback_emission_mode
                        ),
                        "json_compact_token_fallback_source_marker_mode": str(
                            count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                        ),
                        "json_compact_token_fallback_source_marker_activation_mode": str(
                            count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode
                        ),
                        "json_compact_token_selected_scope_marker_mode": str(
                            count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                        ),
                        "json_compact_token_marker_key_naming_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
                        ),
                        "json_compact_token_marker_suppression_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_suppression_mode
                        ),
                        "json_compact_token_marker_summary_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
                        ),
                        "json_compact_token_marker_scope_order_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
                        ),
                        "json_compact_token_marker_list_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
                        ),
                        "json_compact_token_marker_key_prefix_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
                        ),
                        "json_compact_token_marker_boolean_type_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                        ),
                        "json_compact_token_marker_summary_list_order_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
                        ),
                        "json_compact_token_marker_summary_family_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                        ),
                        "json_compact_token_marker_per_scope_family_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                        ),
                        "json_compact_token_marker_summary_boolean_family_visibility_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                        ),
                        "json_compact_token_marker_profile_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_profile_mode
                        ),
                        "json_compact_token_marker_precedence_export_mode": str(
                            count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                        ),
                        "json_compact_token_marker_profile_signature": str(
                            marker_profile_signature
                        ),
                        "json_compact_token_marker_profile_signature_expected": str(
                            marker_profile_signature_expected
                        ),
                        "json_compact_token_marker_profile_signature_expected_valid": bool(
                            marker_profile_signature_expected_valid
                        ),
                        "json_compact_token_marker_profile_signature_match_mode": str(
                            marker_profile_signature_match_mode
                        ),
                        "json_compact_token_marker_profile_signature_match": bool(
                            marker_profile_signature_match
                        ),
                        "json_compact_token_marker_profile_signature_drift_detected": bool(
                            marker_profile_signature_drift_detected
                        ),
                        "json_compact_token_marker_profile_mode_source": str(
                            marker_profile_mode_source
                        ),
                        "json_compact_token_marker_key_naming_mode_source": str(
                            _marker_profile_source_for("marker_key_naming_mode")
                        ),
                        "json_compact_token_marker_suppression_mode_source": str(
                            _marker_profile_source_for("marker_suppression_mode")
                        ),
                        "json_compact_token_marker_summary_visibility_mode_source": str(
                            _marker_profile_source_for("marker_summary_visibility_mode")
                        ),
                        "json_compact_token_marker_scope_order_mode_source": str(
                            _marker_profile_source_for("marker_scope_order_mode")
                        ),
                        "json_compact_token_marker_list_visibility_mode_source": str(
                            _marker_profile_source_for("marker_list_visibility_mode")
                        ),
                        "json_compact_token_marker_key_prefix_mode_source": str(
                            _marker_profile_source_for("marker_key_prefix_mode")
                        ),
                        "json_compact_token_marker_boolean_type_visibility_mode_source": str(
                            _marker_profile_source_for("marker_boolean_type_visibility_mode")
                        ),
                        "json_compact_token_marker_summary_list_order_mode_source": str(
                            _marker_profile_source_for("marker_summary_list_order_mode")
                        ),
                        "json_compact_token_marker_summary_family_visibility_mode_source": str(
                            _marker_profile_source_for("marker_summary_family_visibility_mode")
                        ),
                        "json_compact_token_marker_per_scope_family_visibility_mode_source": str(
                            _marker_profile_source_for("marker_per_scope_family_visibility_mode")
                        ),
                        "json_compact_token_marker_summary_boolean_family_visibility_mode_source": str(
                            _marker_profile_source_for("marker_summary_boolean_family_visibility_mode")
                        ),
                        "json_compact_token_scope_mode": str(
                            count_label_override_diagnostics_json_compact_token_scope_mode
                        ),
                        "json_compact_token_dedup_mode": str(
                            count_label_override_diagnostics_json_compact_token_dedup_mode
                        ),
                        "json_compact_token_normalization_mode": str(
                            count_label_override_diagnostics_json_compact_token_normalization_mode
                        ),
                        "json_compact_token_sanitization_mode": str(
                            count_label_override_diagnostics_json_compact_token_sanitization_mode
                        ),
                        "json_compact_token_sanitization_replacement_char": str(
                            replacement_effective
                        ),
                        "json_compact_token_min_length": int(
                            count_label_override_diagnostics_json_compact_token_min_length
                        ),
                        "counters": {
                            "resolved_family_count": int(resolved_family_override_count),
                            "resolved_metric_count": int(resolved_metric_override_count),
                            "family_malformed_count": int(family_malformed_override_count),
                            "metric_malformed_count": int(metric_malformed_override_count),
                        },
                        "family": {
                            "resolved": dict(sorted(count_table_family_label_overrides.items())),
                            "malformed": list(family_override_malformed),
                        },
                        "metric": {
                            "resolved": dict(sorted(count_table_metric_label_overrides.items())),
                            "malformed": list(metric_override_malformed),
                        },
                        "inline": {
                            "family_label_mode": str(count_inline_family_label_mode),
                            "bucket_label_mode": str(count_inline_bucket_label_mode),
                        },
                        "table": {
                            "family_label_mode": str(count_table_family_label_mode),
                            "header_label_mode": str(count_table_header_label_mode),
                            "metric_label_mode": str(count_table_metric_label_mode),
                            "row_order_mode": str(count_table_row_order_mode),
                            "include_schema_signature": bool(count_table_include_schema_signature),
                        },
                    }
                    if (
                        count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                        == "summary_only"
                    ):
                        for source_key in (
                            "json_compact_token_marker_key_naming_mode_source",
                            "json_compact_token_marker_suppression_mode_source",
                            "json_compact_token_marker_summary_visibility_mode_source",
                            "json_compact_token_marker_scope_order_mode_source",
                            "json_compact_token_marker_list_visibility_mode_source",
                            "json_compact_token_marker_key_prefix_mode_source",
                            "json_compact_token_marker_boolean_type_visibility_mode_source",
                            "json_compact_token_marker_summary_list_order_mode_source",
                            "json_compact_token_marker_summary_family_visibility_mode_source",
                            "json_compact_token_marker_per_scope_family_visibility_mode_source",
                            "json_compact_token_marker_summary_boolean_family_visibility_mode_source",
                        ):
                            diagnostics_json_payload.pop(source_key, None)
                        diagnostics_json_payload[
                            "json_compact_token_marker_precedence_summary"
                        ] = str(marker_profile_precedence_summary_text)
                diagnostics_json_text = json.dumps(
                    diagnostics_json_payload,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                lines.append(
                    "- Family Projects Count Label Override Diagnostics JSON Detail: "
                    f"`{diagnostics_json_text}`"
                )
            count_threshold_mode = (
                "all_min"
                if str(markdown_family_projects_count_threshold_mode or "").strip().lower() == "all_min"
                else "off"
            )
            lines.append(f"- Family Projects Count Threshold Mode: `{count_threshold_mode}`")
            lines.append(
                f"- Family Projects Count Min All: `{max(0, int(markdown_family_projects_count_min_all))}`"
            )
            if family_projects_mode == "counts_only" and markdown_family_projects_count_top_n is not None:
                lines.append(
                    f"- Family Projects Count Top N: `{max(0, int(markdown_family_projects_count_top_n))}`"
                )
                lines.append(
                    "- Family Projects Count Rows: "
                    f"`shown={int(family_projects_count_meta.get('shown_rows') or 0)} "
                    f"total={int(family_projects_count_meta.get('total_rows') or 0)} "
                    f"omitted={int(family_projects_count_meta.get('omitted_rows') or 0)}`"
                )
            lines.append(
                f"- Family Projects Empty Families: `{'hidden' if markdown_family_projects_hide_empty_families else 'shown'}`"
            )
            if markdown_family_projects_include_counts or family_projects_mode == "counts_only":
                if count_export_mode == "table":
                    if family_projects_count_rows or count_table_empty_mode == "table_empty":
                        lines.append("- Family Projects Counts Table:")
                        lines.append("")
                        include_warn = True
                        include_error = True
                        if count_table_style == "minimal":
                            include_warn = any(warn_count > 0 for _, warn_count, _, _ in family_projects_count_rows)
                            include_error = any(error_count > 0 for _, _, error_count, _ in family_projects_count_rows)
                        header_cells = [
                            _format_family_header_for_table(mode=count_table_header_label_mode)
                        ]
                        align_cells = ["---"]
                        if include_warn:
                            header_cells.append(
                                count_table_metric_label_overrides.get(
                                    "warn",
                                    _format_metric_label_for_table(
                                        "warn",
                                        mode=count_table_metric_label_mode,
                                    ),
                                )
                            )
                            align_cells.append("---:")
                        if include_error:
                            header_cells.append(
                                count_table_metric_label_overrides.get(
                                    "error",
                                    _format_metric_label_for_table(
                                        "error",
                                        mode=count_table_metric_label_mode,
                                    ),
                                )
                            )
                            align_cells.append("---:")
                        header_cells.append(
                            count_table_metric_label_overrides.get(
                                "all",
                                _format_metric_label_for_table(
                                    "all",
                                    mode=count_table_metric_label_mode,
                                ),
                            )
                        )
                        align_cells.append("---:")
                        table_column_keys = ["family"]
                        if include_warn:
                            table_column_keys.append("warn")
                        if include_error:
                            table_column_keys.append("error")
                        table_column_keys.append("all")
                        if count_table_include_schema_signature:
                            schema_signature = json.dumps(
                                {
                                    "columns": table_column_keys,
                                    "headers": list(header_cells),
                                },
                                separators=(",", ":"),
                                ensure_ascii=False,
                            )
                            lines.append(
                                "- Family Projects Counts Table Schema Signature: "
                                f"`{schema_signature}`"
                            )
                        lines.append(f"| {' | '.join(header_cells)} |")
                        lines.append(f"| {' | '.join(align_cells)} |")
                        table_count_rows = _order_table_count_rows(
                            family_projects_count_rows,
                            mode=count_table_row_order_mode,
                        )
                        if family_projects_count_rows:
                            for family, warn_count, error_count, all_count in table_count_rows:
                                row_family_label = count_table_family_label_overrides.get(
                                    family,
                                    _format_family_label_for_table(
                                        family,
                                        mode=count_table_family_label_mode,
                                    ),
                                )
                                row_cells = [row_family_label]
                                if include_warn:
                                    row_cells.append(str(warn_count))
                                if include_error:
                                    row_cells.append(str(error_count))
                                row_cells.append(str(all_count))
                                lines.append(f"| {' | '.join(row_cells)} |")
                        else:
                            row_cells = ["(none)"]
                            if include_warn:
                                row_cells.append("0")
                            if include_error:
                                row_cells.append("0")
                            row_cells.append("0")
                            lines.append(f"| {' | '.join(row_cells)} |")
                    else:
                        lines.append(f"- Family Projects Counts: `{family_projects_counts_text}`")
                else:
                    lines.append(f"- Family Projects Counts: `{family_projects_counts_text}`")
            if markdown_family_projects_max_items is not None and family_projects_mode == "full":
                lines.append(
                    f"- Family Projects Max Items: `{max(0, int(markdown_family_projects_max_items))}`"
                )
        if not markdown_hide_suppression_section:
            lines.append(
                f"- Suppressed Triggered Rules: `{_render_list(alerts.get('suppressed_triggered_rules'))}`"
            )
            lines.append(
                f"- Suppressed Triggered Rules By Family: "
                f"`{_render_family_rule_map(alerts.get('suppressed_triggered_rules'))}`"
            )
            suppressed_requested = _normalize_str_list(alerts.get("suppressed_rules_requested"))
            suppressed_applied = _normalize_str_list(alerts.get("suppressed_rules_applied"))
            suppressed_unused = _normalize_str_list(alerts.get("suppressed_rules_unused"))
            suppressed_triggered = _normalize_str_list(alerts.get("suppressed_triggered_rules"))
            scope_map = alerts.get("project_suppression_scopes")
            scope_applied = _normalize_str_list(alerts.get("project_suppression_scopes_applied"))
            scope_unused = _normalize_str_list(alerts.get("project_suppression_scopes_unused"))
            scope_count = len(scope_map) if isinstance(scope_map, dict) else 0
            lines.append(
                "- Suppression Digest Counts: "
                f"`requested={len(suppressed_requested)} applied={len(suppressed_applied)} "
                f"unused={len(suppressed_unused)} triggered_suppressed={len(suppressed_triggered)} "
                f"scopes={scope_count} scopes_applied={len(scope_applied)} scopes_unused={len(scope_unused)}`"
            )
            if not markdown_alert_compact:
                lines.append(
                    f"- Suppressed Rules Requested: `{_render_list(alerts.get('suppressed_rules_requested'))}`"
                )
                lines.append(
                    f"- Suppressed Rules Applied: `{_render_list(alerts.get('suppressed_rules_applied'))}`"
                )
                lines.append(
                    f"- Suppressed Rules Unused: `{_render_list(alerts.get('suppressed_rules_unused'))}`"
                )
                lines.append(
                    f"- Project Suppression Scopes: `{_render_scope_map(alerts.get('project_suppression_scopes'))}`"
                )
                lines.append(
                    f"- Project Suppression Scopes Applied: `{_render_list(alerts.get('project_suppression_scopes_applied'))}`"
                )
                lines.append(
                    f"- Project Suppression Scopes Unused: `{_render_list(alerts.get('project_suppression_scopes_unused'))}`"
                )
        if markdown_alert_compact:
            lines.append("- Alert Detail Mode: `compact`")
        if markdown_hide_suppression_section:
            lines.append("- Suppression Section: `hidden`")

        rules_raw = alerts.get("rules")
        if isinstance(rules_raw, list):
            triggered_rule_items = [
                rule_item
                for rule_item in rules_raw
                if isinstance(rule_item, dict) and bool(rule_item.get("triggered"))
            ]
            if not markdown_alert_compact:
                if detail_limit is not None:
                    shown_rule_items = triggered_rule_items[:detail_limit]
                else:
                    shown_rule_items = triggered_rule_items
                for rule_item in shown_rule_items:
                    rule_name = str(rule_item.get("name") or "unknown_rule")
                    rule_severity = str(rule_item.get("severity") or "none")
                    if markdown_hide_suppression_section:
                        lines.append(
                            f"- Triggered Rule Detail: `{rule_name}` severity=`{rule_severity}`"
                        )
                    else:
                        suppressed = bool(rule_item.get("suppressed"))
                        scope_matched = bool(rule_item.get("suppression_scope_matched"))
                        scope_projects_text = _render_list(rule_item.get("suppression_project_matches"))
                        lines.append(
                            f"- Triggered Rule Detail: `{rule_name}` severity=`{rule_severity}` "
                            f"suppressed=`{suppressed}` scope_matched=`{scope_matched}` "
                            f"scope_projects=`{scope_projects_text}`"
                        )
                if detail_limit is not None and len(triggered_rule_items) > len(shown_rule_items):
                    lines.append(
                        "- Triggered Rule Detail Truncated: "
                        f"`showing {len(shown_rule_items)} of {len(triggered_rule_items)} "
                        f"(--markdown-triggered-rule-detail-max={detail_limit})`"
                    )

    lines.append("")
    lines.append("## Project Deltas")
    if not shown_projects:
        lines.append("- No projects in window.")
    else:
        for idx, row in enumerate(shown_projects, start=1):
            project_id = str(row.get("project_id") or "unknown")
            latest = row.get("latest")
            if isinstance(latest, dict):
                latest_status = str(latest.get("status") or "unknown")
                latest_warning_count = int(latest.get("warning_count") or 0)
                latest_profile = str(latest.get("warning_policy_profile") or "")
                latest_checksum = str(latest.get("warning_policy_checksum") or "")
                latest_guardrail = bool(latest.get("guardrail_triggered"))
            else:
                latest_status = "unknown"
                latest_warning_count = 0
                latest_profile = ""
                latest_checksum = ""
                latest_guardrail = False

            delta = row.get("delta_from_previous")
            if isinstance(delta, dict):
                has_previous = bool(delta.get("has_previous"))
                drift_changed = bool(delta.get("policy_drift_changed"))
                changed_fields_raw = delta.get("changed_fields")
                if isinstance(changed_fields_raw, list):
                    changed_fields = [str(item) for item in changed_fields_raw if str(item).strip()]
                else:
                    changed_fields = []
                warning_count_delta = int(delta.get("warning_count_delta") or 0)
                status_changed = bool(delta.get("status_changed"))
                guardrail_changed = bool(delta.get("guardrail_triggered_changed"))
            else:
                has_previous = False
                drift_changed = False
                changed_fields = []
                warning_count_delta = 0
                status_changed = False
                guardrail_changed = False

            if changed_fields:
                changed_fields_text = ", ".join(changed_fields)
            else:
                changed_fields_text = "none"
            lines.append(
                f"{idx}. `{project_id}` latest=`{latest_status}` warnings=`{latest_warning_count}` "
                f"profile=`{latest_profile}` checksum=`{latest_checksum}`"
            )
            lines.append(
                f"   previous=`{has_previous}` drift_changed=`{drift_changed}` "
                f"fields=`{changed_fields_text}` warning_delta=`{warning_count_delta}`"
            )
            lines.append(
                f"   status_changed=`{status_changed}` guardrail_latest=`{latest_guardrail}` "
                f"guardrail_changed=`{guardrail_changed}`"
            )

    if project_limit is not None and len(projects) > len(shown_projects):
        lines.append("")
        lines.append(
            f"_Truncated project rows: showing {len(shown_projects)} of {len(projects)} "
            f"(--markdown-max-projects={project_limit})._"
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    alert_config = resolve_bridge_alert_config(args)
    input_dir = args.input_dir.expanduser().resolve()
    try:
        marker_profile_signature_drift_exit_code = int(
            args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_drift_exit_code
        )
    except (TypeError, ValueError):
        marker_profile_signature_drift_exit_code = 0
    marker_profile_signature_drift_exit_code = max(
        0, marker_profile_signature_drift_exit_code
    )

    if not input_dir.exists():
        if bool(args.allow_empty):
            payload = _build_empty_bridge_payload(
                projection_profile=str(args.projection_profile),
                since_hours=int(args.since_hours),
            )
            payload["alerts"] = evaluate_bridge_alerts(payload, alert_config=alert_config)
            effective_exit_code = 0
            if bool(payload.get("alerts", {}).get("exit_triggered")):
                effective_exit_code = int(alert_config.get("exit_code") or 12)
            if str(args.format) == "markdown":
                markdown_runtime_telemetry: dict[str, object] = {}
                markdown_output = _render_markdown_bridge(
                    payload,
                    markdown_max_projects=args.markdown_max_projects,
                    markdown_alert_compact=bool(args.markdown_alert_compact),
                    markdown_triggered_rule_detail_max=args.markdown_triggered_rule_detail_max,
                    markdown_hide_suppression_section=bool(args.markdown_hide_suppression_section),
                    markdown_include_family_projects=bool(args.markdown_include_family_projects),
                    markdown_family_projects_include_counts=bool(args.markdown_family_projects_include_counts),
                    markdown_family_projects_hide_empty_families=bool(args.markdown_family_projects_hide_empty_families),
                    markdown_family_projects_mode=str(args.markdown_family_projects_mode),
                    markdown_family_projects_source=str(args.markdown_family_projects_source),
                    markdown_family_projects_severity=str(args.markdown_family_projects_severity),
                    markdown_family_projects_max_items=args.markdown_family_projects_max_items,
                    markdown_family_projects_order=str(args.markdown_family_projects_order),
                    markdown_family_projects_count_order=str(args.markdown_family_projects_count_order),
                    markdown_family_projects_count_render_mode=str(
                        args.markdown_family_projects_count_render_mode
                    ),
                    markdown_family_projects_count_visibility_mode=str(
                        args.markdown_family_projects_count_visibility_mode
                    ),
                    markdown_family_projects_count_export_mode=str(
                        args.markdown_family_projects_count_export_mode
                    ),
                    markdown_family_projects_count_table_style=str(
                        args.markdown_family_projects_count_table_style
                    ),
                    markdown_family_projects_count_table_empty_mode=str(
                        args.markdown_family_projects_count_table_empty_mode
                    ),
                    markdown_family_projects_count_table_family_label_mode=str(
                        args.markdown_family_projects_count_table_family_label_mode
                    ),
                    markdown_family_projects_count_table_header_label_mode=str(
                        args.markdown_family_projects_count_table_header_label_mode
                    ),
                    markdown_family_projects_count_table_family_label_overrides=args.markdown_family_projects_count_table_family_label_override,
                    markdown_family_projects_count_table_metric_label_mode=str(
                        args.markdown_family_projects_count_table_metric_label_mode
                    ),
                    markdown_family_projects_count_table_metric_label_overrides=args.markdown_family_projects_count_table_metric_label_override,
                    markdown_family_projects_count_table_row_order_mode=str(
                        args.markdown_family_projects_count_table_row_order_mode
                    ),
                    markdown_family_projects_count_table_include_schema_signature=bool(
                        args.markdown_family_projects_count_table_include_schema_signature
                    ),
                    markdown_family_projects_count_inline_family_label_mode=str(
                        args.markdown_family_projects_count_inline_family_label_mode
                    ),
                    markdown_family_projects_count_inline_bucket_label_mode=str(
                        args.markdown_family_projects_count_inline_bucket_label_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics=bool(
                        args.markdown_family_projects_count_label_override_diagnostics
                    ),
                    markdown_family_projects_count_label_override_diagnostics_severity=str(
                        args.markdown_family_projects_count_label_override_diagnostics_severity
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json=bool(
                        args.markdown_family_projects_count_label_override_diagnostics_json
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_profile=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_profile
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope=int(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char=str(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char
                    ),
                    markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length=int(
                        args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length
                    ),
                    markdown_family_projects_count_label_override_ci_policy_mode=str(
                        args.markdown_family_projects_count_label_override_ci_policy_mode
                    ),
                    markdown_family_projects_count_min_all=args.markdown_family_projects_count_min_all,
                    markdown_family_projects_count_threshold_mode=str(
                        args.markdown_family_projects_count_threshold_mode
                    ),
                    markdown_family_projects_count_top_n=args.markdown_family_projects_count_top_n,
                    markdown_runtime_telemetry=markdown_runtime_telemetry,
                )
                print(markdown_output, end="")
                if (
                    marker_profile_signature_drift_exit_code > 0
                    and effective_exit_code == 0
                    and bool(
                        markdown_runtime_telemetry.get(
                            "marker_profile_signature_drift_exit_eligible"
                        )
                    )
                ):
                    effective_exit_code = marker_profile_signature_drift_exit_code
            elif bool(args.json_compact):
                print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
            else:
                print(json.dumps(payload, indent=2))
            return int(effective_exit_code)
        raise SystemExit(f"input_dir_not_found:{input_dir}")

    rows = _iter_filtered_rows(
        input_dir=input_dir,
        project_id=args.project_id,
        since_hours=int(args.since_hours),
    )
    if not rows and not bool(args.allow_empty):
        raise SystemExit("no_matching_audit_artifacts")

    payload = build_bridge_payload(
        rows,
        projection_profile=str(args.projection_profile),
        since_hours=int(args.since_hours),
    )
    payload["alerts"] = evaluate_bridge_alerts(payload, alert_config=alert_config)
    effective_exit_code = 0
    if bool(payload.get("alerts", {}).get("exit_triggered")):
        effective_exit_code = int(alert_config.get("exit_code") or 12)
    if str(args.format) == "markdown":
        markdown_runtime_telemetry: dict[str, object] = {}
        markdown_output = _render_markdown_bridge(
            payload,
                markdown_max_projects=args.markdown_max_projects,
                markdown_alert_compact=bool(args.markdown_alert_compact),
                markdown_triggered_rule_detail_max=args.markdown_triggered_rule_detail_max,
                markdown_hide_suppression_section=bool(args.markdown_hide_suppression_section),
                markdown_include_family_projects=bool(args.markdown_include_family_projects),
                markdown_family_projects_include_counts=bool(args.markdown_family_projects_include_counts),
                markdown_family_projects_hide_empty_families=bool(args.markdown_family_projects_hide_empty_families),
                markdown_family_projects_mode=str(args.markdown_family_projects_mode),
                markdown_family_projects_source=str(args.markdown_family_projects_source),
                markdown_family_projects_severity=str(args.markdown_family_projects_severity),
                markdown_family_projects_max_items=args.markdown_family_projects_max_items,
                markdown_family_projects_order=str(args.markdown_family_projects_order),
                markdown_family_projects_count_order=str(args.markdown_family_projects_count_order),
                markdown_family_projects_count_render_mode=str(
                    args.markdown_family_projects_count_render_mode
                ),
                markdown_family_projects_count_visibility_mode=str(
                    args.markdown_family_projects_count_visibility_mode
                ),
                markdown_family_projects_count_export_mode=str(
                    args.markdown_family_projects_count_export_mode
                ),
                markdown_family_projects_count_table_style=str(
                    args.markdown_family_projects_count_table_style
                ),
                markdown_family_projects_count_table_empty_mode=str(
                    args.markdown_family_projects_count_table_empty_mode
                ),
                markdown_family_projects_count_table_family_label_mode=str(
                    args.markdown_family_projects_count_table_family_label_mode
                ),
                markdown_family_projects_count_table_header_label_mode=str(
                    args.markdown_family_projects_count_table_header_label_mode
                ),
                markdown_family_projects_count_table_family_label_overrides=args.markdown_family_projects_count_table_family_label_override,
                markdown_family_projects_count_table_metric_label_mode=str(
                    args.markdown_family_projects_count_table_metric_label_mode
                ),
                markdown_family_projects_count_table_metric_label_overrides=args.markdown_family_projects_count_table_metric_label_override,
                markdown_family_projects_count_table_row_order_mode=str(
                    args.markdown_family_projects_count_table_row_order_mode
                ),
                markdown_family_projects_count_table_include_schema_signature=bool(
                    args.markdown_family_projects_count_table_include_schema_signature
                ),
                markdown_family_projects_count_inline_family_label_mode=str(
                    args.markdown_family_projects_count_inline_family_label_mode
                ),
                markdown_family_projects_count_inline_bucket_label_mode=str(
                    args.markdown_family_projects_count_inline_bucket_label_mode
                ),
                markdown_family_projects_count_label_override_diagnostics=bool(
                    args.markdown_family_projects_count_label_override_diagnostics
                ),
                markdown_family_projects_count_label_override_diagnostics_severity=str(
                    args.markdown_family_projects_count_label_override_diagnostics_severity
                ),
                markdown_family_projects_count_label_override_diagnostics_json=bool(
                    args.markdown_family_projects_count_label_override_diagnostics_json
                ),
                markdown_family_projects_count_label_override_diagnostics_json_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_key_prefix_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_profile=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_profile
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_include_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_sort_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope=int(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_max_per_scope
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_overflow_suffix_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_omitted_count_visibility_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_guard_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_list_key_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_fallback_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_truncation_indicator_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_priority_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_emission_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_fallback_source_marker_activation_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_selected_scope_marker_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_naming_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_suppression_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_visibility_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_scope_order_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_list_visibility_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_key_prefix_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_boolean_type_visibility_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_list_order_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_family_visibility_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_per_scope_family_visibility_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_summary_boolean_family_visibility_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_expected
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_profile_signature_match_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_marker_precedence_export_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_scope_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_dedup_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_normalization_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_mode
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char=str(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_sanitization_replacement_char
                ),
                markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length=int(
                    args.markdown_family_projects_count_label_override_diagnostics_json_compact_token_min_length
                ),
                markdown_family_projects_count_label_override_ci_policy_mode=str(
                    args.markdown_family_projects_count_label_override_ci_policy_mode
                ),
                markdown_family_projects_count_min_all=args.markdown_family_projects_count_min_all,
                markdown_family_projects_count_threshold_mode=str(
                    args.markdown_family_projects_count_threshold_mode
                ),
                markdown_family_projects_count_top_n=args.markdown_family_projects_count_top_n,
                markdown_runtime_telemetry=markdown_runtime_telemetry,
        )
        print(markdown_output, end="")
        if (
            marker_profile_signature_drift_exit_code > 0
            and effective_exit_code == 0
            and bool(
                markdown_runtime_telemetry.get(
                    "marker_profile_signature_drift_exit_eligible"
                )
            )
        ):
            effective_exit_code = marker_profile_signature_drift_exit_code
    elif bool(args.json_compact):
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2))
    return int(effective_exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
