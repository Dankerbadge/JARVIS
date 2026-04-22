#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prune backfill warning-audit artifacts by per-project retention and/or age.",
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
        "--keep-per-project",
        type=int,
        default=100,
        help="Retain this many newest artifacts per project.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=None,
        help="Optional age threshold; artifacts older than this may be pruned.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete selected files. Default is dry-run.",
    )
    parser.add_argument(
        "--json-compact",
        action="store_true",
        help="Emit compact JSON.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Return an empty summary when no artifacts are found.",
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
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iter_rows(input_dir: Path, project_filter: str | None) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            payload = _load_json(path)
        except Exception:
            continue
        project_id = str(payload.get("project_id") or "unknown")
        if project_filter is not None and project_id != str(project_filter):
            continue
        exported_at = _parse_exported_at(payload)
        rows.append(
            {
                "path": path.resolve(),
                "project_id": project_id,
                "exported_at": exported_at,
            }
        )
    return rows


def _row_sort_key(row: dict) -> tuple[str, str]:
    exported_at = row.get("exported_at")
    if isinstance(exported_at, datetime):
        return (exported_at.isoformat(), str(row.get("path") or ""))
    return ("", str(row.get("path") or ""))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.exists():
        if bool(args.allow_empty):
            payload = {
                "dry_run": not bool(args.execute),
                "total_candidates": 0,
                "selected_for_prune": 0,
                "pruned_count": 0,
                "kept_count": 0,
                "selected_paths": [],
            }
            if bool(args.json_compact):
                print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
            else:
                print(json.dumps(payload, indent=2))
            return 0
        raise SystemExit(f"input_dir_not_found:{input_dir}")

    keep_per_project = max(0, int(args.keep_per_project))
    max_age_hours = args.max_age_hours
    cutoff: datetime | None = None
    if max_age_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0, int(max_age_hours)))

    rows = _iter_rows(input_dir=input_dir, project_filter=args.project_id)
    if not rows and not bool(args.allow_empty):
        raise SystemExit("no_matching_audit_artifacts")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("project_id") or "unknown")].append(row)

    selected_paths: list[str] = []
    kept_paths: list[str] = []
    for project_id, project_rows in grouped.items():
        ordered = sorted(project_rows, key=_row_sort_key, reverse=True)
        for idx, row in enumerate(ordered):
            path = str(row.get("path") or "")
            exported_at = row.get("exported_at")
            selected = False
            if idx >= keep_per_project:
                selected = True
            if cutoff is not None and isinstance(exported_at, datetime) and exported_at < cutoff:
                selected = True
            if selected:
                selected_paths.append(path)
            else:
                kept_paths.append(path)

    selected_paths = sorted(set(selected_paths))
    pruned_count = 0
    if bool(args.execute):
        for path_text in selected_paths:
            path = Path(path_text)
            if not path.exists():
                continue
            path.unlink()
            pruned_count += 1

    payload = {
        "dry_run": not bool(args.execute),
        "project_filter": args.project_id,
        "keep_per_project": keep_per_project,
        "max_age_hours": max_age_hours,
        "cutoff": cutoff.isoformat() if cutoff is not None else None,
        "total_candidates": int(len(rows)),
        "selected_for_prune": int(len(selected_paths)),
        "pruned_count": int(pruned_count),
        "kept_count": int(len(kept_paths)),
        "selected_paths": selected_paths,
    }
    if bool(args.json_compact):
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
