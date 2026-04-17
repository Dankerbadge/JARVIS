from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


class RepoChangeConnector(BaseConnector):
    """Detects file-system deltas in a repository directory."""

    def __init__(
        self,
        repo_path: str | Path,
        *,
        name: str = "repo_changes",
        include_suffixes: tuple[str, ...] | None = None,
        emit_on_initial_scan: bool = False,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.name = name
        self.include_suffixes = include_suffixes
        self.emit_on_initial_scan = emit_on_initial_scan
        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise FileNotFoundError(f"Repo path not found: {self.repo_path}")

    def _iter_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.repo_path.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if self.include_suffixes and path.suffix not in self.include_suffixes:
                continue
            files.append(path)
        return files

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha1()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _snapshot(self) -> dict[str, str]:
        snap: dict[str, str] = {}
        for file_path in self._iter_files():
            rel = str(file_path.relative_to(self.repo_path))
            snap[rel] = self._hash_file(file_path)
        return snap

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous = dict((cursor or {}).get("snapshot", {}))
        current = self._snapshot()
        next_cursor = {"snapshot": current}

        if not previous:
            if self.emit_on_initial_scan and current:
                event = EventEnvelope(
                    source="repo",
                    source_type="repo_change",
                    payload={
                        "repo_path": str(self.repo_path),
                        "added": sorted(current.keys()),
                        "modified": [],
                        "deleted": [],
                        "changed_count": len(current),
                        "protected_ui_changed": any(path.startswith("ui/") for path in current),
                    },
                    auth_context="connector_repo_read",
                )
                return ConnectorPollResult(events=[event], cursor=next_cursor)
            return ConnectorPollResult(events=[], cursor=next_cursor)

        previous_paths = set(previous)
        current_paths = set(current)
        added = sorted(current_paths - previous_paths)
        deleted = sorted(previous_paths - current_paths)
        modified = sorted(
            path for path in (current_paths & previous_paths) if current[path] != previous[path]
        )
        changed = added + modified + deleted
        if not changed:
            return ConnectorPollResult(events=[], cursor=next_cursor)

        protected_ui_changed = any(path.startswith("ui/") for path in changed)
        event = EventEnvelope(
            source="repo",
            source_type="repo_change",
            payload={
                "repo_path": str(self.repo_path),
                "added": added,
                "modified": modified,
                "deleted": deleted,
                "changed_count": len(changed),
                "protected_ui_changed": protected_ui_changed,
            },
            auth_context="connector_repo_read",
        )
        return ConnectorPollResult(events=[event], cursor=next_cursor)

