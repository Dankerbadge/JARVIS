#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from jarvis.release_hygiene import dumps_pretty, verify_release_clean


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify release staging directory for forbidden artifacts and secrets.")
    parser.add_argument("--root", type=Path, required=True, help="Directory to scan")
    parser.add_argument("--manifest", type=Path, required=True, help="Path to write manifest JSON")
    parser.add_argument("--report", type=Path, required=True, help="Path to write scan report JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when forbidden artifacts or secret patterns are detected.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.exists() or not root.is_dir():
        print(f"Scan root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        manifest, report = verify_release_clean(root, strict=args.strict)
    except ValueError:
        manifest, report = verify_release_clean(root, strict=False)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(dumps_pretty(manifest) + "\n", encoding="utf-8")
        args.report.write_text(dumps_pretty(report) + "\n", encoding="utf-8")
        print(f"Release hygiene verification failed for {root}", file=sys.stderr)
        print(dumps_pretty(report), file=sys.stderr)
        return 1

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(dumps_pretty(manifest) + "\n", encoding="utf-8")
    args.report.write_text(dumps_pretty(report) + "\n", encoding="utf-8")
    print(f"Release hygiene verification passed for {root}")
    print(f"Manifest: {args.manifest}")
    print(f"Report:   {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
