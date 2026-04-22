#!/usr/bin/env python3
from __future__ import annotations

import argparse


def normalize_optional_int(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"invalid_{field}:must_be_non_negative")
    return normalized


def normalize_optional_rate(value: float | None, *, field: str) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if normalized < 0.0 or normalized > 1.0:
        raise ValueError(f"invalid_{field}:must_be_between_0_and_1")
    return normalized


def _parse_project_severity_overrides(raw_values: list[str] | None) -> dict[str, dict[str, str]]:
    overrides: dict[str, dict[str, str]] = {}
    for raw_entry in list(raw_values or []):
        parts = [segment.strip() for segment in str(raw_entry or "").split(",") if segment.strip()]
        for part in parts:
            if "@" in part:
                severity_part_raw, scope_raw = part.rsplit("@", 1)
                scope = str(scope_raw or "").strip().lower()
            else:
                severity_part_raw = part
                scope = "both"
            if "=" in severity_part_raw:
                project_raw, severity_raw = severity_part_raw.split("=", 1)
            elif ":" in severity_part_raw:
                project_raw, severity_raw = severity_part_raw.split(":", 1)
            else:
                raise ValueError(
                    "invalid_bridge_alert_project_severity_override:expected_<project_id>=<warn|error>"
                )
            project_id = str(project_raw or "").strip()
            severity = str(severity_raw or "").strip().lower()
            if not project_id:
                raise ValueError(
                    "invalid_bridge_alert_project_severity_override:missing_project_id"
                )
            if severity not in ("warn", "error"):
                raise ValueError(
                    "invalid_bridge_alert_project_severity_override:severity_must_be_warn_or_error"
                )
            if scope not in ("both", "policy_only", "guardrail_only"):
                raise ValueError(
                    "invalid_bridge_alert_project_severity_override:scope_must_be_both_policy_only_or_guardrail_only"
                )
            overrides[project_id] = {"severity": severity, "scope": scope}
    return overrides


def _parse_suppressed_rule_names(raw_values: list[str] | None) -> list[str]:
    suppressed: set[str] = set()
    for raw_entry in list(raw_values or []):
        parts = [segment.strip() for segment in str(raw_entry or "").split(",") if segment.strip()]
        for part in parts:
            normalized = str(part or "").strip()
            if normalized:
                suppressed.add(normalized)
    return sorted(suppressed)


def _parse_project_suppression_scopes(raw_values: list[str] | None) -> dict[str, str]:
    scopes: dict[str, str] = {}
    for raw_entry in list(raw_values or []):
        parts = [segment.strip() for segment in str(raw_entry or "").split(",") if segment.strip()]
        for part in parts:
            if "@" in part:
                project_raw, scope_raw = part.rsplit("@", 1)
            elif "=" in part:
                project_raw, scope_raw = part.split("=", 1)
            elif ":" in part:
                project_raw, scope_raw = part.split(":", 1)
            else:
                raise ValueError(
                    "invalid_bridge_alert_project_suppress_scope:expected_<project_id>@<policy_only|guardrail_only|both>"
                )
            project_id = str(project_raw or "").strip()
            scope = str(scope_raw or "").strip().lower()
            if not project_id:
                raise ValueError("invalid_bridge_alert_project_suppress_scope:missing_project_id")
            if scope not in ("policy_only", "guardrail_only", "both"):
                raise ValueError(
                    "invalid_bridge_alert_project_suppress_scope:scope_must_be_policy_only_guardrail_only_or_both"
                )
            scopes[project_id] = scope
    return scopes


def resolve_bridge_alert_config(args: argparse.Namespace) -> dict[str, object]:
    return {
        "policy_drift_count_threshold": normalize_optional_int(
            getattr(args, "bridge_alert_policy_drift_count_threshold", None),
            field="bridge_alert_policy_drift_count_threshold",
        ),
        "policy_drift_rate_threshold": normalize_optional_rate(
            getattr(args, "bridge_alert_policy_drift_rate_threshold", None),
            field="bridge_alert_policy_drift_rate_threshold",
        ),
        "guardrail_count_threshold": normalize_optional_int(
            getattr(args, "bridge_alert_guardrail_count_threshold", None),
            field="bridge_alert_guardrail_count_threshold",
        ),
        "guardrail_rate_threshold": normalize_optional_rate(
            getattr(args, "bridge_alert_guardrail_rate_threshold", None),
            field="bridge_alert_guardrail_rate_threshold",
        ),
        "policy_drift_severity": str(getattr(args, "bridge_alert_policy_drift_severity", "error") or "error"),
        "guardrail_severity": str(getattr(args, "bridge_alert_guardrail_severity", "error") or "error"),
        "exit_code": max(1, int(getattr(args, "bridge_alert_exit_code", 12) or 12)),
        "project_severity_overrides": _parse_project_severity_overrides(
            list(getattr(args, "bridge_alert_project_severity_override", None) or [])
        ),
        "suppressed_rules": _parse_suppressed_rule_names(
            list(getattr(args, "bridge_alert_suppress_rule", None) or [])
        ),
        "project_suppression_scopes": _parse_project_suppression_scopes(
            list(getattr(args, "bridge_alert_project_suppress_scope", None) or [])
        ),
    }


def evaluate_bridge_alerts(payload: dict, *, alert_config: dict[str, object]) -> dict:
    summary = payload.get("summary")
    if isinstance(summary, dict):
        projects_with_previous = int(summary.get("projects_with_previous") or 0)
        projects_with_policy_drift = int(summary.get("projects_with_policy_drift") or 0)
        projects_with_guardrail_triggered = int(summary.get("projects_with_guardrail_triggered") or 0)
    else:
        projects_with_previous = 0
        projects_with_policy_drift = 0
        projects_with_guardrail_triggered = 0

    project_count = int(payload.get("project_count") or 0)
    total_runs = int(payload.get("total_runs") or 0)
    policy_drift_rate = (
        float(projects_with_policy_drift) / float(projects_with_previous)
        if projects_with_previous > 0
        else 0.0
    )
    guardrail_rate = (
        float(projects_with_guardrail_triggered) / float(project_count)
        if project_count > 0
        else 0.0
    )

    rules: list[dict] = []
    policy_drift_severity = str(alert_config.get("policy_drift_severity") or "error")
    guardrail_severity = str(alert_config.get("guardrail_severity") or "error")
    project_severity_overrides_raw = alert_config.get("project_severity_overrides")
    project_severity_overrides: dict[str, dict[str, str]] = {}
    if isinstance(project_severity_overrides_raw, dict):
        for key, value in project_severity_overrides_raw.items():
            project_id = str(key or "").strip()
            if not project_id:
                continue
            if isinstance(value, dict):
                severity = str(value.get("severity") or "").strip().lower()
                scope = str(value.get("scope") or "both").strip().lower()
            else:
                severity = str(value or "").strip().lower()
                scope = "both"
            if severity not in ("warn", "error"):
                continue
            if scope not in ("both", "policy_only", "guardrail_only"):
                scope = "both"
            project_severity_overrides[project_id] = {"severity": severity, "scope": scope}
    suppressed_rules_raw = alert_config.get("suppressed_rules")
    if isinstance(suppressed_rules_raw, list):
        suppressed_rules_requested = {
            str(item or "").strip() for item in suppressed_rules_raw if str(item or "").strip()
        }
    else:
        suppressed_rules_requested = set()
    project_suppression_scopes_raw = alert_config.get("project_suppression_scopes")
    project_suppression_scopes: dict[str, str] = {}
    if isinstance(project_suppression_scopes_raw, dict):
        for key, value in project_suppression_scopes_raw.items():
            project_id = str(key or "").strip()
            scope = str(value or "").strip().lower()
            if not project_id:
                continue
            if scope not in ("policy_only", "guardrail_only", "both"):
                continue
            project_suppression_scopes[project_id] = scope

    policy_drift_projects_by_severity: dict[str, set[str]] = {"warn": set(), "error": set()}
    guardrail_projects_by_severity: dict[str, set[str]] = {"warn": set(), "error": set()}
    project_ids_seen: set[str] = set()
    projects_raw = payload.get("projects")
    if isinstance(projects_raw, list):
        for row in projects_raw:
            if not isinstance(row, dict):
                continue
            project_id = str(row.get("project_id") or "").strip()
            if not project_id:
                continue
            project_ids_seen.add(project_id)

            delta = row.get("delta_from_previous")
            policy_drift_changed = bool(
                isinstance(delta, dict) and bool(delta.get("policy_drift_changed"))
            )
            latest = row.get("latest")
            guardrail_triggered = bool(
                isinstance(latest, dict) and bool(latest.get("guardrail_triggered"))
            )

            override_entry = project_severity_overrides.get(project_id)
            if isinstance(override_entry, dict):
                override_severity = str(override_entry.get("severity") or "")
                override_scope = str(override_entry.get("scope") or "both")
            else:
                override_severity = ""
                override_scope = "both"
            if policy_drift_changed:
                if override_severity in ("warn", "error") and override_scope in ("both", "policy_only"):
                    severity = override_severity
                else:
                    severity = policy_drift_severity
                policy_drift_projects_by_severity[severity].add(project_id)
            if guardrail_triggered:
                if override_severity in ("warn", "error") and override_scope in ("both", "guardrail_only"):
                    severity = override_severity
                else:
                    severity = guardrail_severity
                guardrail_projects_by_severity[severity].add(project_id)

    def severity_for_family(
        *, family_default: str, projects_by_severity: dict[str, set[str]], triggered: bool
    ) -> str:
        if not triggered:
            return family_default
        if projects_by_severity["error"]:
            return "error"
        if projects_by_severity["warn"]:
            return "warn"
        return family_default

    policy_drift_count_threshold = alert_config.get("policy_drift_count_threshold")
    if policy_drift_count_threshold is not None:
        threshold_value = int(policy_drift_count_threshold)
        triggered = bool(projects_with_policy_drift >= threshold_value)
        rule_severity = severity_for_family(
            family_default=policy_drift_severity,
            projects_by_severity=policy_drift_projects_by_severity,
            triggered=triggered,
        )
        rules.append(
            {
                "name": "policy_drift_count_threshold",
                "metric": "projects_with_policy_drift",
                "actual": projects_with_policy_drift,
                "threshold": threshold_value,
                "triggered": triggered,
                "severity": rule_severity,
                "projects_by_severity": {
                    "warn": sorted(policy_drift_projects_by_severity["warn"]),
                    "error": sorted(policy_drift_projects_by_severity["error"]),
                },
            }
        )

    policy_drift_rate_threshold = alert_config.get("policy_drift_rate_threshold")
    if policy_drift_rate_threshold is not None:
        threshold_value = float(policy_drift_rate_threshold)
        triggered = bool(policy_drift_rate >= threshold_value)
        rule_severity = severity_for_family(
            family_default=policy_drift_severity,
            projects_by_severity=policy_drift_projects_by_severity,
            triggered=triggered,
        )
        rules.append(
            {
                "name": "policy_drift_rate_threshold",
                "metric": "policy_drift_rate",
                "actual": policy_drift_rate,
                "threshold": threshold_value,
                "triggered": triggered,
                "severity": rule_severity,
                "projects_by_severity": {
                    "warn": sorted(policy_drift_projects_by_severity["warn"]),
                    "error": sorted(policy_drift_projects_by_severity["error"]),
                },
            }
        )

    guardrail_count_threshold = alert_config.get("guardrail_count_threshold")
    if guardrail_count_threshold is not None:
        threshold_value = int(guardrail_count_threshold)
        triggered = bool(projects_with_guardrail_triggered >= threshold_value)
        rule_severity = severity_for_family(
            family_default=guardrail_severity,
            projects_by_severity=guardrail_projects_by_severity,
            triggered=triggered,
        )
        rules.append(
            {
                "name": "guardrail_count_threshold",
                "metric": "projects_with_guardrail_triggered",
                "actual": projects_with_guardrail_triggered,
                "threshold": threshold_value,
                "triggered": triggered,
                "severity": rule_severity,
                "projects_by_severity": {
                    "warn": sorted(guardrail_projects_by_severity["warn"]),
                    "error": sorted(guardrail_projects_by_severity["error"]),
                },
            }
        )

    guardrail_rate_threshold = alert_config.get("guardrail_rate_threshold")
    if guardrail_rate_threshold is not None:
        threshold_value = float(guardrail_rate_threshold)
        triggered = bool(guardrail_rate >= threshold_value)
        rule_severity = severity_for_family(
            family_default=guardrail_severity,
            projects_by_severity=guardrail_projects_by_severity,
            triggered=triggered,
        )
        rules.append(
            {
                "name": "guardrail_rate_threshold",
                "metric": "guardrail_rate",
                "actual": guardrail_rate,
                "threshold": threshold_value,
                "triggered": triggered,
                "severity": rule_severity,
                "projects_by_severity": {
                    "warn": sorted(guardrail_projects_by_severity["warn"]),
                    "error": sorted(guardrail_projects_by_severity["error"]),
                },
            }
        )

    def _rule_family(rule_name: str) -> str:
        if rule_name.startswith("policy_drift_"):
            return "policy_only"
        if rule_name.startswith("guardrail_"):
            return "guardrail_only"
        return "both"

    project_suppression_scopes_applied: set[str] = set()
    for rule in rules:
        name = str(rule.get("name") or "")
        suppressed = False
        suppression_scope_matched = False
        suppression_project_matches: list[str] = []
        if name and name in suppressed_rules_requested:
            if not project_suppression_scopes:
                suppressed = True
            else:
                family = _rule_family(name)
                projects_by_severity = rule.get("projects_by_severity")
                if isinstance(projects_by_severity, dict):
                    candidates = set()
                    candidates.update(str(item) for item in list(projects_by_severity.get("warn") or []))
                    candidates.update(str(item) for item in list(projects_by_severity.get("error") or []))
                else:
                    candidates = set()
                eligible_projects = {
                    project_id
                    for project_id, scope in project_suppression_scopes.items()
                    if scope == "both" or scope == family
                }
                matched_projects = sorted(project_id for project_id in candidates if project_id in eligible_projects)
                suppression_project_matches = matched_projects
                if matched_projects:
                    suppressed = True
                    suppression_scope_matched = True
                    project_suppression_scopes_applied.update(matched_projects)
                else:
                    suppressed = False
                    suppression_scope_matched = False
        rule["suppressed"] = suppressed
        rule["suppression_scope_matched"] = suppression_scope_matched
        rule["suppression_project_matches"] = suppression_project_matches

    triggered_rules_raw = [str(rule.get("name") or "") for rule in rules if bool(rule.get("triggered"))]
    triggered_warn_rules_raw = [
        str(rule.get("name") or "")
        for rule in rules
        if bool(rule.get("triggered")) and str(rule.get("severity") or "") == "warn"
    ]
    triggered_error_rules_raw = [
        str(rule.get("name") or "")
        for rule in rules
        if bool(rule.get("triggered")) and str(rule.get("severity") or "") == "error"
    ]
    suppressed_rules_applied = {
        str(rule.get("name") or "")
        for rule in rules
        if bool(rule.get("suppressed")) and str(rule.get("name") or "").strip()
    }
    suppressed_rules_unused = sorted(suppressed_rules_requested - suppressed_rules_applied)
    suppressed_triggered_rules = [
        str(rule.get("name") or "")
        for rule in rules
        if bool(rule.get("triggered")) and bool(rule.get("suppressed"))
    ]
    triggered_rules = [
        str(rule.get("name") or "")
        for rule in rules
        if bool(rule.get("triggered")) and not bool(rule.get("suppressed"))
    ]
    triggered_warn_rules = [
        str(rule.get("name") or "")
        for rule in rules
        if bool(rule.get("triggered"))
        and not bool(rule.get("suppressed"))
        and str(rule.get("severity") or "") == "warn"
    ]
    triggered_error_rules = [
        str(rule.get("name") or "")
        for rule in rules
        if bool(rule.get("triggered"))
        and not bool(rule.get("suppressed"))
        and str(rule.get("severity") or "") == "error"
    ]
    if triggered_error_rules:
        max_triggered_severity = "error"
    elif triggered_warn_rules:
        max_triggered_severity = "warn"
    else:
        max_triggered_severity = "none"
    exit_triggered = bool(triggered_error_rules)
    return {
        "enabled": bool(rules),
        "triggered": bool(triggered_rules),
        "triggered_rules": triggered_rules,
        "triggered_warn_rules": triggered_warn_rules,
        "triggered_error_rules": triggered_error_rules,
        "triggered_rules_raw": triggered_rules_raw,
        "triggered_warn_rules_raw": triggered_warn_rules_raw,
        "triggered_error_rules_raw": triggered_error_rules_raw,
        "suppressed_triggered_rules": suppressed_triggered_rules,
        "suppressed_rules_requested": sorted(suppressed_rules_requested),
        "suppressed_rules_applied": sorted(suppressed_rules_applied),
        "suppressed_rules_unused": suppressed_rules_unused,
        "max_triggered_severity": max_triggered_severity,
        "exit_triggered": exit_triggered,
        "metrics": {
            "total_runs": total_runs,
            "project_count": project_count,
            "projects_with_previous": projects_with_previous,
            "projects_with_policy_drift": projects_with_policy_drift,
            "projects_with_policy_drift_warn": len(policy_drift_projects_by_severity["warn"]),
            "projects_with_policy_drift_error": len(policy_drift_projects_by_severity["error"]),
            "projects_with_guardrail_triggered": projects_with_guardrail_triggered,
            "projects_with_guardrail_triggered_warn": len(guardrail_projects_by_severity["warn"]),
            "projects_with_guardrail_triggered_error": len(guardrail_projects_by_severity["error"]),
            "policy_drift_rate": policy_drift_rate,
            "guardrail_rate": guardrail_rate,
        },
        "thresholds": {
            "policy_drift_count_threshold": alert_config.get("policy_drift_count_threshold"),
            "policy_drift_rate_threshold": alert_config.get("policy_drift_rate_threshold"),
            "guardrail_count_threshold": alert_config.get("guardrail_count_threshold"),
            "guardrail_rate_threshold": alert_config.get("guardrail_rate_threshold"),
        },
        "severities": {
            "policy_drift": policy_drift_severity,
            "guardrail": guardrail_severity,
        },
        "project_severity_overrides": {
            project_id: str(config.get("severity") or "")
            for project_id, config in sorted(project_severity_overrides.items())
        },
        "project_severity_override_scopes": {
            project_id: str(config.get("scope") or "both")
            for project_id, config in sorted(project_severity_overrides.items())
        },
        "project_severity_overrides_resolved": dict(sorted(project_severity_overrides.items())),
        "project_severity_overrides_applied": sorted(
            project_id for project_id in project_severity_overrides.keys() if project_id in project_ids_seen
        ),
        "project_severity_overrides_unused": sorted(
            project_id for project_id in project_severity_overrides.keys() if project_id not in project_ids_seen
        ),
        "project_suppression_scopes": dict(sorted(project_suppression_scopes.items())),
        "project_suppression_scopes_applied": sorted(project_suppression_scopes_applied),
        "project_suppression_scopes_unused": sorted(
            project_id for project_id in project_suppression_scopes.keys() if project_id not in project_suppression_scopes_applied
        ),
        "rules": rules,
        "exit_code_when_triggered": int(alert_config.get("exit_code") or 12),
    }
