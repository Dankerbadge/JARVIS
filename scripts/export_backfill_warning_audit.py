#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _default_repo_path() -> Path:
    return Path.cwd()


def _default_db_path(repo_path: Path) -> Path:
    return repo_path / ".jarvis" / "jarvis.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run backfill warnings mode and export a timestamped JSON audit artifact.",
    )
    parser.add_argument("project_id", type=str)
    parser.add_argument("--profile-key", type=str, default="nightly")
    parser.add_argument("--preset", type=str, default="balanced")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--policy-config", type=Path, default=None)
    parser.add_argument("--repo-path", type=Path, default=_default_repo_path())
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "backfill_warning_audit",
    )
    parser.add_argument(
        "--filename-prefix",
        type=str,
        default=None,
        help="Optional filename prefix. Defaults to project id.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Also print the exported JSON payload to stdout (pretty).",
    )
    parser.add_argument(
        "--export-profile",
        type=str,
        default="full",
        choices=("full", "minimal"),
        help="Audit payload profile: full detail or minimized storage-friendly payload.",
    )
    parser.add_argument(
        "--minimal-warning-code-limit",
        type=int,
        default=None,
        help="When using --export-profile minimal, keep at most N warning codes.",
    )
    parser.add_argument(
        "--minimal-omit-signal-summary",
        action="store_true",
        help="When using --export-profile minimal, omit signal_summary block.",
    )
    parser.add_argument(
        "--minimal-omit-policy-drift-differences",
        action="store_true",
        help="When using --export-profile minimal, omit verbose policy_drift diff lists.",
    )
    parser.add_argument(
        "--baseline-audit",
        type=Path,
        default=None,
        help="Optional baseline audit JSON used for policy drift comparison.",
    )
    parser.add_argument(
        "--compare-with-latest",
        action="store_true",
        help="Use the latest existing audit file for this prefix as baseline when --baseline-audit is omitted.",
    )
    parser.add_argument(
        "--enforce-stable-policy-source",
        action="store_true",
        help="Fail (with drift exit code) when policy source fields drift vs baseline.",
    )
    parser.add_argument(
        "--enforce-stable-policy-checksum",
        action="store_true",
        help="Fail (with drift exit code) when warning_policy_checksum drifts vs baseline.",
    )
    parser.add_argument(
        "--enforce-stable-policy-core",
        action="store_true",
        help="Fail (with drift exit code) when policy-core fields drift vs baseline.",
    )
    parser.add_argument(
        "--drift-projection-profile",
        type=str,
        default="full",
        choices=("full", "policy_core"),
        help="Projection profile used for drift reporting (full surface or policy core).",
    )
    parser.add_argument(
        "--drift-exit-code",
        type=int,
        default=7,
        help="Exit code used when drift guardrails trigger and base command exits 0.",
    )
    parser.add_argument(
        "--require-baseline",
        action="store_true",
        help="Fail guardrails when no baseline artifact is available.",
    )
    parser.add_argument(
        "--missing-baseline-exit-code",
        type=int,
        default=8,
        help="Exit code used when --require-baseline is set and baseline is missing.",
    )
    return parser


def _timestamp_utc() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _decode_payload(stdout_text: str) -> dict:
    raw = stdout_text.strip()
    if not raw:
        raise ValueError("empty_backfill_warnings_payload")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json_payload:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload_type:expected_object")
    return payload


def _load_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid_payload_type:{path}:expected_object")
    return parsed


def _extract_policy_projection(payload: dict, *, profile: str = "full") -> dict:
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
    keys = sorted(set(before.keys()) | set(after.keys()))
    for key in keys:
        b = before.get(key)
        a = after.get(key)
        if b == a:
            continue
        diffs.append(
            {
                "field": key,
                "before": b,
                "after": a,
            }
        )
    return diffs


def _resolve_baseline_path(
    *,
    explicit_baseline: Path | None,
    compare_with_latest: bool,
    output_dir: Path,
    prefix: str,
) -> Path | None:
    if explicit_baseline is not None:
        resolved = explicit_baseline.expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"baseline_audit_not_found:{resolved}")
        return resolved
    if not compare_with_latest:
        return None
    candidates = sorted(output_dir.glob(f"{prefix}_*.json"))
    if not candidates:
        return None
    return candidates[-1].resolve()


def _build_minimal_export_payload(
    payload: dict,
    *,
    warning_code_limit: int | None,
    omit_signal_summary: bool,
    omit_policy_drift_differences: bool,
) -> dict:
    signal_summary = payload.get("signal_summary")
    if bool(omit_signal_summary):
        minimal_signal_summary = None
    elif isinstance(signal_summary, dict):
        minimal_signal_summary = {
            "signals_count": int(signal_summary.get("signals_count") or 0),
            "candidate_unscanned_count": int(signal_summary.get("candidate_unscanned_count") or 0),
        }
    else:
        minimal_signal_summary = None
    warning_codes_raw = list(payload.get("warning_codes") or [])
    warning_code_count = int(len(warning_codes_raw))
    if warning_code_limit is not None:
        warning_codes = warning_codes_raw[: max(0, int(warning_code_limit))]
    else:
        warning_codes = warning_codes_raw
    warning_code_limit_value = None if warning_code_limit is None else max(0, int(warning_code_limit))
    warning_codes_truncated = bool(
        warning_code_limit_value is not None and warning_code_count > int(warning_code_limit_value)
    )
    audit = payload.get("_audit")
    if isinstance(audit, dict):
        drift = audit.get("policy_drift")
        if isinstance(drift, dict):
            minimal_drift = {
                "baseline_path": drift.get("baseline_path"),
                "baseline_missing": bool(drift.get("baseline_missing")),
                "require_baseline": bool(drift.get("require_baseline")),
                "projection_profile": str(drift.get("projection_profile") or "full"),
                "changed": bool(drift.get("changed")),
                "policy_core_changed": bool(drift.get("policy_core_changed")),
                "guardrail_triggered": bool(drift.get("guardrail_triggered")),
                "drift_exit_code": int(drift.get("drift_exit_code") or 0),
                "missing_baseline_exit_code": int(drift.get("missing_baseline_exit_code") or 0),
            }
            if not bool(omit_policy_drift_differences):
                minimal_drift["changed_fields"] = list(drift.get("changed_fields") or [])
                minimal_drift["policy_core_changed_fields"] = list(drift.get("policy_core_changed_fields") or [])
                minimal_drift["guardrail_violations"] = list(drift.get("guardrail_violations") or [])
        else:
            minimal_drift = {}
        minimal_audit = {
            "exported_at": str(audit.get("exported_at") or ""),
            "exit_code": int(audit.get("exit_code") or 0),
            "effective_exit_code": int(audit.get("effective_exit_code") or 0),
            "policy_drift": minimal_drift,
            "minimal_export": {
                "warning_code_limit": warning_code_limit_value,
                "warning_codes_truncated": warning_codes_truncated,
                "omit_signal_summary": bool(omit_signal_summary),
                "omit_policy_drift_differences": bool(omit_policy_drift_differences),
            },
        }
    else:
        minimal_audit = {}
    minimal_payload = {
        "_export_profile": "minimal",
        "status": str(payload.get("status") or "unknown"),
        "project_id": str(payload.get("project_id") or ""),
        "profile_key": str(payload.get("profile_key") or ""),
        "preset": str(payload.get("preset") or ""),
        "warning_policy_profile": str(payload.get("warning_policy_profile") or ""),
        "warning_policy_checksum": str(payload.get("warning_policy_checksum") or ""),
        "warning_policy_config_source": str(payload.get("warning_policy_config_source") or ""),
        "warning_policy_config_path": str(payload.get("warning_policy_config_path") or ""),
        "exit_code_policy": str(payload.get("exit_code_policy") or ""),
        "exit_code": int(payload.get("exit_code") or 0),
        "exit_triggered": bool(payload.get("exit_triggered")),
        "max_warning_severity": str(payload.get("max_warning_severity") or "none"),
        "warning_count": int(payload.get("warning_count") or 0),
        "warning_code_count": warning_code_count,
        "warning_code_limit": warning_code_limit_value,
        "warning_codes_truncated": warning_codes_truncated,
        "warning_codes": warning_codes,
        "_audit": minimal_audit,
    }
    if minimal_signal_summary is not None:
        minimal_payload["signal_summary"] = minimal_signal_summary
    return minimal_payload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_path = args.repo_path.resolve()
    db_path = (args.db_path.resolve() if args.db_path is not None else _default_db_path(repo_path).resolve())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        sys.executable,
        "-m",
        "jarvis.cli",
        "plans",
        "backfill-project-signals",
        str(args.project_id),
        "--profile-key",
        str(args.profile_key),
        "--preset",
        str(args.preset),
        "--summary-only",
        "--output",
        "warnings",
        "--json-compact",
        "--repo-path",
        str(repo_path),
        "--db-path",
        str(db_path),
    ]
    if bool(args.execute):
        cmd.append("--execute")
    if args.policy_config is not None:
        cmd.extend(["--warning-policy-config", str(args.policy_config.resolve())])

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    payload = _decode_payload(proc.stdout)

    prefix = str(args.filename_prefix or args.project_id or "backfill").strip() or "backfill"
    baseline_path = _resolve_baseline_path(
        explicit_baseline=args.baseline_audit,
        compare_with_latest=bool(args.compare_with_latest),
        output_dir=output_dir,
        prefix=prefix,
    )
    baseline_projection: dict | None = None
    projection_profile = str(args.drift_projection_profile)
    current_projection = _extract_policy_projection(payload, profile=projection_profile)
    current_policy_core_projection = _extract_policy_projection(payload, profile="policy_core")
    baseline_policy_core_projection: dict | None = None
    policy_core_differences: list[dict] = []
    drift_differences: list[dict] = []
    if baseline_path is not None:
        baseline_payload = _load_json(baseline_path)
        baseline_projection = _extract_policy_projection(baseline_payload, profile=projection_profile)
        drift_differences = _diff_dicts(baseline_projection, current_projection)
        baseline_policy_core_projection = _extract_policy_projection(baseline_payload, profile="policy_core")
        policy_core_differences = _diff_dicts(baseline_policy_core_projection, current_policy_core_projection)
    drift_changed = bool(drift_differences)
    policy_core_changed = bool(policy_core_differences)
    changed_fields = sorted(
        {
            str(item.get("field") or "")
            for item in drift_differences
            if isinstance(item, dict) and str(item.get("field") or "").strip()
        }
    )
    policy_core_changed_fields = sorted(
        {
            str(item.get("field") or "")
            for item in policy_core_differences
            if isinstance(item, dict) and str(item.get("field") or "").strip()
        }
    )
    policy_source_fields = {
        "warning_policy_config_source",
        "warning_policy_profile_source",
    }
    source_changed = bool(any(field in policy_source_fields for field in changed_fields))
    checksum_changed = bool("warning_policy_checksum" in changed_fields)
    guardrail_violations: list[str] = []
    baseline_missing = bool(baseline_path is None)
    if bool(args.require_baseline) and baseline_missing:
        guardrail_violations.append("baseline_missing")
    if bool(args.enforce_stable_policy_source) and source_changed:
        guardrail_violations.append("policy_source_changed")
    if bool(args.enforce_stable_policy_checksum) and checksum_changed:
        guardrail_violations.append("policy_checksum_changed")
    if bool(args.enforce_stable_policy_core) and policy_core_changed:
        guardrail_violations.append("policy_core_changed")
    guardrail_triggered = bool(guardrail_violations)
    base_exit_code = int(proc.returncode)
    drift_exit_code = max(1, int(args.drift_exit_code))
    missing_baseline_exit_code = max(1, int(args.missing_baseline_exit_code))
    effective_exit_code = base_exit_code
    if effective_exit_code == 0 and guardrail_triggered:
        if "baseline_missing" in guardrail_violations:
            effective_exit_code = missing_baseline_exit_code
        else:
            effective_exit_code = drift_exit_code

    payload["_audit"] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "command": cmd,
        "exit_code": base_exit_code,
        "effective_exit_code": effective_exit_code,
        "policy_drift": {
            "baseline_path": str(baseline_path) if baseline_path is not None else None,
            "baseline_missing": baseline_missing,
            "require_baseline": bool(args.require_baseline),
            "compare_with_latest": bool(args.compare_with_latest),
            "projection_profile": projection_profile,
            "changed": drift_changed,
            "changed_fields": changed_fields,
            "differences": drift_differences,
            "baseline_projection": baseline_projection,
            "current_projection": current_projection,
            "policy_core_changed": policy_core_changed,
            "policy_core_changed_fields": policy_core_changed_fields,
            "policy_core_differences": policy_core_differences,
            "baseline_policy_core_projection": baseline_policy_core_projection,
            "current_policy_core_projection": current_policy_core_projection,
            "enforce_stable_policy_source": bool(args.enforce_stable_policy_source),
            "enforce_stable_policy_checksum": bool(args.enforce_stable_policy_checksum),
            "enforce_stable_policy_core": bool(args.enforce_stable_policy_core),
            "guardrail_violations": guardrail_violations,
            "guardrail_triggered": guardrail_triggered,
            "drift_exit_code": drift_exit_code,
            "missing_baseline_exit_code": missing_baseline_exit_code,
        },
    }
    payload["_export_profile"] = str(args.export_profile)

    export_payload = payload
    if str(args.export_profile) == "minimal":
        warning_code_limit = args.minimal_warning_code_limit
        if warning_code_limit is not None:
            warning_code_limit = max(0, int(warning_code_limit))
        export_payload = _build_minimal_export_payload(
            payload,
            warning_code_limit=warning_code_limit,
            omit_signal_summary=bool(args.minimal_omit_signal_summary),
            omit_policy_drift_differences=bool(args.minimal_omit_policy_drift_differences),
        )

    filename = f"{prefix}_{_timestamp_utc()}.json"
    out_path = output_dir / filename
    out_path.write_text(json.dumps(export_payload, indent=2), encoding="utf-8")

    summary = {
        "export_profile": str(args.export_profile),
        "status": str(payload.get("status") or "unknown"),
        "exit_code": base_exit_code,
        "effective_exit_code": effective_exit_code,
        "policy_drift_projection_profile": projection_profile,
        "policy_drift_changed": drift_changed,
        "policy_core_changed": policy_core_changed,
        "policy_drift_guardrail_triggered": guardrail_triggered,
        "warning_count": int(payload.get("warning_count") or 0),
        "path": str(out_path),
    }
    print(json.dumps(summary, indent=2))

    if bool(args.print_json):
        print(json.dumps(export_payload, indent=2))

    return int(effective_exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
