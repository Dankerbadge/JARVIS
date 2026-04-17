from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import EventEnvelope
from .base import BaseConnector, ConnectorPollResult


class JsonCIReportConnector(BaseConnector):
    """Reads JSON CI reports and emits failure events with path implication signals."""

    def __init__(
        self,
        reports_path: str | Path,
        *,
        name: str = "ci_reports",
        project: str = "zenith",
        zenith_owned_prefixes: tuple[str, ...] = ("jarvis/", "ui/", "zenith/"),
    ) -> None:
        self.reports_path = Path(reports_path).resolve()
        self.name = name
        self.project = project
        self.zenith_owned_prefixes = zenith_owned_prefixes
        if not self.reports_path.exists():
            raise FileNotFoundError(f"CI reports path not found: {self.reports_path}")

    def _iter_report_files(self) -> list[Path]:
        if self.reports_path.is_file():
            return [self.reports_path]
        files = [path for path in self.reports_path.rglob("*.json") if path.is_file()]
        files.sort(key=lambda path: path.stat().st_mtime_ns)
        return files

    def _status(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("status")
            or payload.get("conclusion")
            or payload.get("result")
            or ""
        ).lower()

    def _implicated_paths(self, payload: dict[str, Any]) -> list[str]:
        candidates = (
            payload.get("implicated_paths")
            or payload.get("failed_paths")
            or payload.get("paths")
            or payload.get("changed_files")
            or payload.get("files")
            or []
        )
        if not isinstance(candidates, list):
            return []
        paths = [str(item) for item in candidates if str(item).strip()]
        return sorted(set(paths))

    def _failed_tests(self, payload: dict[str, Any]) -> list[str]:
        candidates = payload.get("failed_tests") or payload.get("tests") or []
        if not isinstance(candidates, list):
            return []
        return sorted(set(str(item) for item in candidates if str(item).strip()))

    def _is_failure(self, payload: dict[str, Any]) -> bool:
        return self._status(payload) in {"failed", "failure", "error", "errored"}

    def poll(self, cursor: dict[str, Any] | None) -> ConnectorPollResult:
        previous_seen = dict((cursor or {}).get("seen", {}))
        events: list[EventEnvelope] = []
        next_seen = dict(previous_seen)

        for report_file in self._iter_report_files():
            report_key = str(report_file)
            mtime_ns = report_file.stat().st_mtime_ns
            if previous_seen.get(report_key) == mtime_ns:
                continue
            next_seen[report_key] = mtime_ns

            try:
                payload = json.loads(report_file.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            if not isinstance(payload, dict) or not self._is_failure(payload):
                continue

            paths = self._implicated_paths(payload)
            protected_ui_changed = any(path.startswith("ui/") for path in paths)
            zenith_owned = any(
                path.startswith(prefix) for path in paths for prefix in self.zenith_owned_prefixes
            )
            branch = str(payload.get("branch") or payload.get("git_branch") or "unknown")
            head_sha = str(payload.get("head_sha") or payload.get("sha") or "unknown")
            repo_id = str(payload.get("repo_id") or payload.get("repo_path") or "unknown")

            report_id = str(payload.get("report_id") or report_file.stem)
            summary_text = str(
                payload.get("summary")
                or payload.get("error_summary")
                or payload.get("message")
                or ""
            )
            failed_tests = self._failed_tests(payload)
            event = EventEnvelope(
                source="ci",
                source_type="ci.failure",
                payload={
                    "project": str(payload.get("project") or self.project),
                    "repo_id": repo_id,
                    "branch": branch,
                    "head_sha": head_sha,
                    "report_id": report_id,
                    "provider": str(payload.get("provider") or ""),
                    "pipeline_id": str(payload.get("pipeline_id") or ""),
                    "status": self._status(payload),
                    "job_name": str(payload.get("job_name") or payload.get("job") or ""),
                    "error_summary": summary_text,
                    "summary": summary_text,
                    "stacktrace": str(payload.get("stacktrace") or payload.get("error_excerpt") or ""),
                    "error_excerpt": str(payload.get("error_excerpt") or ""),
                    "implicated_paths": paths,
                    "failed_paths": paths,
                    "failed_tests": failed_tests,
                    "protected_ui_changed": protected_ui_changed,
                    "zenith_owned": zenith_owned,
                    "report_file": str(report_file),
                    "url": str(payload.get("url") or ""),
                },
                auth_context="connector_ci_read",
            )
            events.append(event)

        # Keep cursor bounded.
        if len(next_seen) > 500:
            keys = sorted(next_seen.keys(), key=lambda key: next_seen[key], reverse=True)[:500]
            next_seen = {key: next_seen[key] for key in keys}

        return ConnectorPollResult(events=events, cursor={"seen": next_seen})
