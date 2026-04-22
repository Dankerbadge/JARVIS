#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid_payload_type:{path}:expected_object")
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
    if profile == "policy_core":
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two backfill warning-audit payloads and report warning-policy drift.",
    )
    parser.add_argument("before", type=Path, help="Baseline audit JSON path")
    parser.add_argument("after", type=Path, help="Candidate audit JSON path")
    parser.add_argument(
        "--allow-changes",
        action="store_true",
        help="Always exit 0 even when drift is detected",
    )
    parser.add_argument(
        "--changed-exit-code",
        type=int,
        default=1,
        help="Exit code used when drift is detected and --allow-changes is not set",
    )
    parser.add_argument(
        "--projection-profile",
        type=str,
        default="full",
        choices=("full", "policy_core"),
        help="Comparison projection profile (full surface or narrowed policy core contract).",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Emit compact drift summary fields instead of full differences payload.",
    )
    parser.add_argument(
        "--json-compact",
        action="store_true",
        help="Emit compact JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    before_payload = _load_json(args.before.resolve())
    after_payload = _load_json(args.after.resolve())
    before_projection = _extract_projection(before_payload, profile=str(args.projection_profile))
    after_projection = _extract_projection(after_payload, profile=str(args.projection_profile))
    diffs = _diff_dicts(before_projection, after_projection)
    changed = bool(diffs)
    changed_fields = [
        str(item.get("field") or "")
        for item in diffs
        if isinstance(item, dict) and str(item.get("field") or "").strip()
    ]

    output: dict
    if bool(args.summary_only):
        output = {
            "changed": changed,
            "before": str(args.before.resolve()),
            "after": str(args.after.resolve()),
            "projection_profile": str(args.projection_profile),
            "changed_field_count": int(len(changed_fields)),
            "changed_fields": changed_fields,
            "before_warning_policy_profile": str(before_projection.get("warning_policy_profile") or ""),
            "after_warning_policy_profile": str(after_projection.get("warning_policy_profile") or ""),
            "before_warning_policy_checksum": str(before_projection.get("warning_policy_checksum") or ""),
            "after_warning_policy_checksum": str(after_projection.get("warning_policy_checksum") or ""),
        }
    else:
        output = {
            "changed": changed,
            "before": str(args.before.resolve()),
            "after": str(args.after.resolve()),
            "projection_profile": str(args.projection_profile),
            "differences": diffs,
        }
    if bool(args.json_compact):
        print(json.dumps(output, separators=(",", ":"), ensure_ascii=False))
    else:
        print(json.dumps(output, indent=2))

    if changed and not bool(args.allow_changes):
        return max(1, int(args.changed_exit_code))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
