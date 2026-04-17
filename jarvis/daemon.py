from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .approval_inbox import ApprovalInbox
from .connectors.base import BaseConnector
from .models import PlanArtifact
from .reactors import BaseReactor
from .runtime import JarvisRuntime


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectorCursorStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_cursors (
                connector_name TEXT PRIMARY KEY,
                cursor_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def get(self, connector_name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT cursor_json FROM connector_cursors WHERE connector_name = ?",
            (connector_name,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["cursor_json"])

    def set(self, connector_name: str, cursor: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO connector_cursors (connector_name, cursor_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(connector_name)
            DO UPDATE SET cursor_json = excluded.cursor_json, updated_at = excluded.updated_at
            """,
            (connector_name, json.dumps(cursor, sort_keys=True), _utc_now_iso()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class EventDaemon:
    def __init__(
        self,
        *,
        runtime: JarvisRuntime,
        connectors: list[BaseConnector],
        reactors: list[BaseReactor],
    ) -> None:
        self.runtime = runtime
        self.connectors = connectors
        self.reactors = reactors
        self.cursor_store = ConnectorCursorStore(runtime.db_path)
        self.approval_inbox = ApprovalInbox(runtime.security)

    def _propose_plans(
        self,
        *,
        event: Any,
        ingestion_outcome: dict[str, Any],
    ) -> list[PlanArtifact]:
        plans: list[PlanArtifact] = []
        for reactor in self.reactors:
            plans.extend(reactor.propose_plans(self.runtime, event, ingestion_outcome))
        return plans

    def _derive_outcome_status(self, execution_results: list[dict[str, Any]]) -> str:
        statuses = [result.get("status") for result in execution_results]
        if any(status in {"failed", "blocked"} for status in statuses):
            return "failure"
        if any(status == "awaiting_approval" for status in statuses):
            return "partial"
        if statuses and all(status == "ok" for status in statuses):
            return "success"
        return "partial"

    def _collect_touched_paths(
        self,
        plan: Any,
        execution_results: list[dict[str, Any]],
    ) -> list[str]:
        touched: set[str] = set()
        for step in plan.steps:
            payload = step.payload or {}
            relative_path = payload.get("relative_path")
            if isinstance(relative_path, str) and relative_path:
                touched.add(relative_path)
            for key in ("implicated_paths", "failed_paths", "ranked_paths"):
                values = payload.get(key, [])
                if isinstance(values, list):
                    touched.update(str(item) for item in values if str(item))
            report = payload.get("root_cause_report")
            if isinstance(report, dict):
                for candidate in report.get("candidates", []):
                    path = candidate.get("path")
                    if path:
                        touched.add(str(path))
        for result in execution_results:
            output = result.get("output", {})
            if not isinstance(output, dict):
                continue
            file_value = output.get("file")
            if isinstance(file_value, str) and file_value:
                touched.add(file_value)
            proposals = output.get("proposals", [])
            if isinstance(proposals, list):
                for proposal in proposals:
                    if isinstance(proposal, dict):
                        path = proposal.get("file")
                        if path:
                            touched.add(str(path))
        return sorted(touched)

    def _record_plan_outcome(
        self,
        *,
        plan_id: str,
        execution_results: list[dict[str, Any]],
    ) -> None:
        plan = self.runtime.plan_repo.get_plan(plan_id)
        first_payload = plan.steps[0].payload if plan.steps else {}
        repo_id = str(
            first_payload.get("repo_id")
            or first_payload.get("repo_path")
            or str(self.runtime.repo_path)
        )
        branch = str(first_payload.get("branch") or "unknown")
        failure_family = first_payload.get("failure_family")
        if not failure_family:
            report = first_payload.get("root_cause_report")
            if isinstance(report, dict):
                failure_family = report.get("failure_family")
        status = self._derive_outcome_status(execution_results)
        touched_paths = self._collect_touched_paths(plan, execution_results)
        self.runtime.plan_repo.record_outcome(
            plan_id=plan_id,
            repo_id=repo_id,
            branch=branch,
            status=status,
            touched_paths=touched_paths,
            failure_family=str(failure_family) if failure_family else None,
            summary=plan.reasoning_summary,
        )

    def run_once(self, *, dry_run: bool = False) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "events_processed": 0,
            "plans_proposed": 0,
            "plans_executed": 0,
            "connector_runs": [],
            "executions": [],
            "pending_approvals": [],
            "market_outcome_learning": [],
            "cognition": None,
            "presence_heartbeat": None,
            "openclaw_gateway": None,
            "daily_digest": None,
        }
        summary["openclaw_gateway"] = self.runtime.pump_openclaw_gateway(max_messages=120)
        for connector in self.connectors:
            previous_cursor = self.cursor_store.get(connector.name)
            poll_result = connector.poll(previous_cursor)
            if poll_result.cursor is not None:
                self.cursor_store.set(connector.name, poll_result.cursor)
            connector_summary = {
                "connector": connector.name,
                "events": len(poll_result.events),
            }
            summary["connector_runs"].append(connector_summary)

            for event in poll_result.events:
                summary["events_processed"] += 1
                ingestion_outcome = self.runtime.ingest_envelope(event)
                if event.source_type == "market.handoff_outcome":
                    mapped = self.runtime.record_market_handoff_outcome(event)
                    if mapped:
                        summary["market_outcome_learning"].append(mapped)
                plans = self._propose_plans(event=event, ingestion_outcome=ingestion_outcome)
                for plan in plans:
                    plan_id = self.runtime.plan_repo.save_plan(plan)
                    summary["plans_proposed"] += 1
                    execution = self.runtime.run(plan_id, dry_run=dry_run, approvals={})
                    summary["plans_executed"] += 1
                    self._record_plan_outcome(plan_id=plan_id, execution_results=execution)
                    summary["executions"].append(
                        {"plan_id": plan_id, "results": execution, "event_id": event.event_id}
                    )

        summary["pending_approvals"] = self.approval_inbox.list(status="pending")
        summary["cognition"] = self.runtime.run_cognition_cycle()
        summary["presence_heartbeat"] = self.runtime.run_presence_heartbeat()
        summary["daily_digest"] = self.runtime.maybe_export_daily_digest()
        return summary

    def run_forever(
        self,
        *,
        interval_seconds: float = 5.0,
        dry_run: bool = False,
        max_loops: int | None = None,
    ) -> list[dict[str, Any]]:
        loops = 0
        summaries: list[dict[str, Any]] = []
        self.runtime.start_openclaw_gateway_loop()
        try:
            while True:
                summaries.append(self.run_once(dry_run=dry_run))
                loops += 1
                if max_loops is not None and loops >= max_loops:
                    return summaries
                time.sleep(interval_seconds)
        finally:
            self.runtime.stop_openclaw_gateway_loop()

    def close(self) -> None:
        self.cursor_store.close()
