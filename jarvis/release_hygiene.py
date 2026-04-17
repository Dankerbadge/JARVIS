from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"(^|/)\.jarvis(/|$)"),
    re.compile(r"(^|/)worktrees(/|$)"),
    re.compile(r"(^|/)keys(/|$)"),
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"(^|/)jarvis\.db$"),
    re.compile(r"(^|/).+\.db$"),
    re.compile(r"(^|/).+\.pem$"),
    re.compile(r"(^|/).+\.key$"),
    re.compile(r"(^|/).+id_rsa(\.pub)?$"),
    re.compile(r"(^|/).+id_ed25519(\.pub)?$"),
    re.compile(r"(^|/).+\.zip$"),
    re.compile(r"(^|/)provider_credentials(/|$)"),
]

SECRET_PATTERNS = [
    re.compile(r"BEGIN OPENSSH PRIVATE KEY"),
    re.compile(r"BEGIN RSA PRIVATE KEY"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"ghp_[A-Za-z0-9]+"),
    re.compile(r"glpat-[A-Za-z0-9_-]+"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
    re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9_\-.=]+"),
    re.compile(r"\"token\"\s*:\s*\"[^\"]{12,}\""),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

SECRET_SCAN_IGNORE_PREFIXES = ("tests/",)
SECRET_SCAN_IGNORE_FILES = {
    "jarvis/release_hygiene.py",
    "scripts/verify_release_clean.py",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path) -> list[Path]:
    files = [item for item in root.rglob("*") if item.is_file()]
    files.sort(key=lambda item: item.as_posix())
    return files


def _safe_read_text(path: Path, *, max_bytes: int = 512_000) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _path_is_forbidden(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    return any(pattern.search(normalized) for pattern in FORBIDDEN_PATH_PATTERNS)


def build_manifest(root: Path) -> dict[str, Any]:
    files = _iter_files(root)
    items: list[dict[str, Any]] = []
    total_bytes = 0
    for file_path in files:
        rel = file_path.relative_to(root).as_posix()
        size = file_path.stat().st_size
        total_bytes += size
        items.append(
            {
                "path": rel,
                "bytes": size,
                "sha256": _sha256_file(file_path),
            }
        )
    return {
        "generated_at": _utc_now_iso(),
        "root": str(root),
        "file_count": len(items),
        "total_bytes": total_bytes,
        "files": items,
    }


def scan_release_root(root: Path) -> dict[str, Any]:
    forbidden_paths: list[dict[str, Any]] = []
    secret_hits: list[dict[str, Any]] = []

    for file_path in _iter_files(root):
        rel = file_path.relative_to(root).as_posix()
        if _path_is_forbidden(rel):
            forbidden_paths.append({"path": rel, "reason": "forbidden_path_pattern"})

        if rel.startswith(SECRET_SCAN_IGNORE_PREFIXES) or rel in SECRET_SCAN_IGNORE_FILES:
            continue

        text = _safe_read_text(file_path)
        if text is None:
            continue
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                secret_hits.append(
                    {
                        "path": rel,
                        "line": line,
                        "pattern": pattern.pattern,
                    }
                )

    return {
        "generated_at": _utc_now_iso(),
        "root": str(root),
        "forbidden_paths": forbidden_paths,
        "secret_hits": secret_hits,
        "ok": not forbidden_paths and not secret_hits,
    }


def verify_release_clean(root: Path, *, strict: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = build_manifest(root)
    report = scan_release_root(root)
    if strict and not report["ok"]:
        raise ValueError("release hygiene verification failed")
    return manifest, report


def dumps_pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)
