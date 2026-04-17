from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import sqlite3
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .archive import DigestArchiveService
from .adaptive_policy import AdaptivePolicyStore
from .approval_packet import OutcomeSummary, RankedCandidate
from .codex_delegation import CodexDelegationService
from .consciousness import ConsciousnessSurfaceService
from .cognition import CognitionEngine
from .dialogue_retrieval import DialogueRetriever
from .dialogue_state import DialogueStateStore
from .device_tokens import DeviceTokenStore
from .execution_service import ApprovalExecutionService
from .identity_state import IdentityStateStore
from .interrupts import InterruptStore
from .memory import MemoryStore
from .model_backends import build_backend_from_env, cognition_enabled_from_env
from .model_backends.base import CognitionBackend
from .models import EventEnvelope, PlanArtifact, PlanStep, new_id, utc_now_iso
from .openclaw_ws_bridge import OpenClawWsBridge
from .openclaw_event_router import OpenClawEventRouter
from .openclaw_gateway_client import OpenClawGatewayClient, OpenClawGatewayConfig
from .openclaw_reply_orchestrator import OpenClawReplyOrchestrator, ReplyDraft
from .node_command_broker import NodeCommandBroker
from .operator_state import OperatorStateStore
from .outcomes import map_review_feedback_to_outcome
from .presence_health import PresenceHealthStore
from .providers.github import GitHubReviewClient
from .providers.base import ProviderReviewArtifact, ReviewFeedbackSnapshot
from .pushback_calibration import PushbackCalibrationStore
from .publication_service import RemotePublicationService
from .relationship_modes import RelationshipModeEngine
from .review_service import ReviewService
from .security import ActionClass, SecurityManager
from .secref_nodes import SecretRefError, parse_secret_ref, resolve_secret_ref, validate_node_secret_plan
from .skills.academics import AcademicsSkill
from .skills.identity import IdentitySkill
from .skills.markets import MarketsSkill
from .skills.zenith import ZenithSkill
from .state_graph import StateGraph
from .state_index import (
    latest_academic_overview_key,
    latest_academic_schedule_context_key,
    latest_academic_suppression_windows_key,
    latest_market_abstention_key,
    latest_market_event_key,
    latest_market_handoff_key,
    latest_market_opportunity_key,
    latest_market_outcome_key,
    latest_market_risk_posture_key,
    latest_personal_context_key,
    latest_merge_outcome_key,
    latest_requested_reviewers_key,
    latest_review_artifact_key,
    latest_review_comments_key,
    latest_review_status_key,
    latest_review_summary_key,
    latest_timeline_cursor_key,
    latest_user_model_key,
)
from .synthesis import SynthesisEngine
from .surface_session_state import SurfaceSessionStateStore
from .signals import SignalIngestStore, normalize_signal_envelope
from .taskflow_presence_runner import TaskFlowPresenceRunner
from .tone_balance import ToneBalanceStore
from .voice_assets import VoiceAssetPackStore
from .voice_continuity_soak import VoiceContinuitySoakStore
from .voice_tuning_state import VoiceTuningStateStore


class PlanRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_artifacts (
                plan_id TEXT PRIMARY KEY,
                intent TEXT NOT NULL,
                priority TEXT NOT NULL,
                reasoning_summary TEXT NOT NULL,
                approval_requirements_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_steps (
                step_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_idx INTEGER NOT NULL,
                action_class TEXT NOT NULL,
                proposed_action TEXT NOT NULL,
                expected_effect TEXT NOT NULL,
                rollback_text TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                requires_approval INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_outcomes (
                plan_id TEXT PRIMARY KEY,
                repo_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                status TEXT NOT NULL,
                touched_paths_json TEXT NOT NULL,
                failure_family TEXT,
                summary TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def save_plan(self, plan: PlanArtifact, status: str = "proposed") -> str:
        self.conn.execute(
            """
            INSERT INTO plan_artifacts (
                plan_id, intent, priority, reasoning_summary, approval_requirements_json,
                expires_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.plan_id,
                plan.intent,
                plan.priority,
                plan.reasoning_summary,
                json.dumps(plan.approval_requirements, sort_keys=True),
                plan.expires_at,
                status,
                utc_now_iso(),
            ),
        )
        for idx, step in enumerate(plan.steps):
            self.conn.execute(
                """
                INSERT INTO plan_steps (
                    step_id, plan_id, step_idx, action_class, proposed_action, expected_effect,
                    rollback_text, payload_json, requires_approval, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.step_id,
                    plan.plan_id,
                    idx,
                    step.action_class,
                    step.proposed_action,
                    step.expected_effect,
                    step.rollback,
                    json.dumps(step.payload, sort_keys=True),
                    1 if step.requires_approval else 0,
                    step.idempotency_key,
                ),
            )
        self.conn.commit()
        return plan.plan_id

    def set_status(self, plan_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE plan_artifacts SET status = ? WHERE plan_id = ?",
            (status, plan_id),
        )
        self.conn.commit()

    def get_plan(self, plan_id: str) -> PlanArtifact:
        row = self.conn.execute(
            "SELECT * FROM plan_artifacts WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Plan not found: {plan_id}")

        step_rows = self.conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_idx ASC",
            (plan_id,),
        ).fetchall()
        steps: list[PlanStep] = []
        for step_row in step_rows:
            steps.append(
                PlanStep(
                    action_class=step_row["action_class"],
                    proposed_action=step_row["proposed_action"],
                    expected_effect=step_row["expected_effect"],
                    rollback=step_row["rollback_text"],
                    payload=json.loads(step_row["payload_json"]),
                    requires_approval=bool(step_row["requires_approval"]),
                    step_id=step_row["step_id"],
                    idempotency_key=step_row["idempotency_key"],
                )
            )
        return PlanArtifact(
            intent=row["intent"],
            priority=row["priority"],
            reasoning_summary=row["reasoning_summary"],
            steps=steps,
            approval_requirements=json.loads(row["approval_requirements_json"]),
            expires_at=row["expires_at"],
            plan_id=row["plan_id"],
        )

    def close(self) -> None:
        self.conn.close()

    def record_outcome(
        self,
        *,
        plan_id: str,
        repo_id: str,
        branch: str,
        status: str,
        touched_paths: list[str],
        failure_family: str | None = None,
        summary: str | None = None,
        recorded_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO plan_outcomes (
                plan_id, repo_id, branch, status, touched_paths_json, failure_family, summary, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                repo_id = excluded.repo_id,
                branch = excluded.branch,
                status = excluded.status,
                touched_paths_json = excluded.touched_paths_json,
                failure_family = excluded.failure_family,
                summary = excluded.summary,
                recorded_at = excluded.recorded_at
            """,
            (
                plan_id,
                repo_id,
                branch,
                status,
                json.dumps(sorted(set(touched_paths))),
                failure_family,
                summary,
                recorded_at or utc_now_iso(),
            ),
        )
        self.conn.commit()

    def list_recent_outcomes(
        self,
        repo_id: str,
        branch: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM plan_outcomes
            WHERE repo_id = ? AND branch = ?
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            (repo_id, branch, limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "plan_id": row["plan_id"],
                    "repo_id": row["repo_id"],
                    "branch": row["branch"],
                    "status": row["status"],
                    "touched_paths": json.loads(row["touched_paths_json"]),
                    "failure_family": row["failure_family"],
                    "summary": row["summary"],
                    "recorded_at": row["recorded_at"],
                }
            )
        return out

    def list_recent_outcomes_global(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM plan_outcomes
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "plan_id": row["plan_id"],
                    "repo_id": row["repo_id"],
                    "branch": row["branch"],
                    "status": row["status"],
                    "touched_paths": json.loads(row["touched_paths_json"]),
                    "failure_family": row["failure_family"],
                    "summary": row["summary"],
                    "recorded_at": row["recorded_at"],
                }
            )
        return out




def _build_default_review_service() -> ReviewService:
    providers: dict[str, Any] = {}
    github_token = str(
        os.getenv("JARVIS_GITHUB_TOKEN")
        or os.getenv("GITHUB_TOKEN")
        or ""
    ).strip()
    if not github_token:
        # Fallback to GitHub CLI auth so local runs work even when shell envs are not loaded.
        try:
            completed = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                github_token = str(completed.stdout or "").strip()
        except OSError:
            github_token = ""
    if github_token:
        providers["github"] = GitHubReviewClient(
            token=github_token,
            api_base=os.getenv("JARVIS_GITHUB_API_BASE", "https://api.github.com"),
        )
    return ReviewService(providers)


class Planner:
    def __init__(
        self,
        zenith: ZenithSkill,
        academics: AcademicsSkill,
        markets: MarketsSkill,
        state_graph: StateGraph,
    ) -> None:
        self.zenith = zenith
        self.academics = academics
        self.markets = markets
        self.state_graph = state_graph

    def build_plans(self, triggers: list[dict[str, Any]]) -> list[PlanArtifact]:
        if not triggers:
            return []
        risks = self.state_graph.get_active_entities("Risk")
        domains = {
            str(item.get("domain") or item.get("project") or "").strip().lower()
            for item in triggers
        }
        known_domains = {value for value in domains if value in {"zenith", "academics", "markets"}}
        if not known_domains:
            known_domains = {"zenith"}
        plans: list[PlanArtifact] = []
        if "zenith" in known_domains:
            zenith_plan = self.zenith.propose_plan(risks)
            if zenith_plan:
                plans.append(zenith_plan)
        if "academics" in known_domains:
            academics_plan = self.academics.propose_plan(risks)
            if academics_plan:
                plans.append(academics_plan)
        if "markets" in known_domains:
            markets_plan = self.markets.propose_plan(risks)
            if markets_plan:
                plans.append(markets_plan)
        return plans


class Executor:
    def __init__(
        self,
        *,
        repo_path: Path,
        security: SecurityManager,
        plan_repo: PlanRepository,
        tools: dict[str, Any],
        execution_service: ApprovalExecutionService | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.security = security
        self.plan_repo = plan_repo
        self.tools = tools
        self.execution_service = execution_service

    def _prepare_evidence_packet(
        self,
        *,
        plan: PlanArtifact,
        step: PlanStep,
        approval_id: str,
        action_class: ActionClass,
    ) -> dict[str, Any]:
        if not self.execution_service:
            return {}

        existing = self.security.get_approval_packet(approval_id)
        if existing:
            return existing

        root_payload = plan.steps[0].payload if plan.steps else {}
        repo_id = str(
            root_payload.get("repo_id")
            or root_payload.get("repo_path")
            or str(self.repo_path)
        )
        branch = str(root_payload.get("branch") or "unknown")
        confidence = float(root_payload.get("correlation_confidence") or 0.5)

        report = root_payload.get("root_cause_report", {})
        ranked_candidates = [
            RankedCandidate(
                path=str(item.get("path")),
                score=float(item.get("score", 0)),
                reasons=tuple(str(reason) for reason in item.get("reasons", [])),
            )
            for item in report.get("candidates", [])[:8]
            if item.get("path")
        ]

        recent_raw = self.plan_repo.list_recent_outcomes(repo_id, branch, limit=10)
        recent_outcomes: list[OutcomeSummary] = []
        for outcome in recent_raw[:8]:
            status = str(outcome.get("status", "partial"))
            if status == "success":
                weight = 1.0
            elif status == "partial":
                weight = 0.45
            elif status == "failure":
                weight = -0.55
            else:
                weight = -0.9
            for path in outcome.get("touched_paths", [])[:3]:
                recent_outcomes.append(
                    OutcomeSummary(
                        path=str(path),
                        status=status,
                        weight=weight,
                        note=str(outcome.get("failure_family") or ""),
                    )
                )

        patch_text = self.execution_service.build_patch_for_step(
            proposed_action=step.proposed_action,
            payload=step.payload,
        )

        prepared = self.execution_service.prepare_protected_step(
            approval_id=approval_id,
            plan_id=plan.plan_id,
            step_id=step.step_id,
            permission_class=action_class.value,
            reason=plan.reasoning_summary,
            repo_id=repo_id,
            branch=branch,
            confidence=confidence,
            patch_text=patch_text,
            ranked_candidates=ranked_candidates,
            recent_outcomes=recent_outcomes,
            action_desc=step.proposed_action,
        )
        packet_dict = prepared.packet.to_dict()
        self.security.store_approval_packet(
            approval_id=approval_id,
            plan_id=plan.plan_id,
            step_id=step.step_id,
            packet=packet_dict,
            markdown=prepared.packet.to_markdown(),
            sandbox={
                "repo_path": prepared.sandbox.repo_path,
                "sandbox_path": prepared.sandbox.sandbox_path,
                "branch_name": prepared.sandbox.branch_name,
                "base_ref": prepared.sandbox.base_ref,
            },
            preflight={
                "working_dir": prepared.preflight_report.working_dir,
                "passed": prepared.preflight_report.passed,
                "summary": prepared.preflight_report.summarize(),
                "checks": [
                    {
                        "name": check.name,
                        "passed": check.passed,
                        "return_code": check.return_code,
                        "stdout_excerpt": check.stdout_excerpt,
                        "stderr_excerpt": check.stderr_excerpt,
                    }
                    for check in prepared.preflight_report.checks
                ],
            },
            touched_files=list(prepared.touched_files),
            patch_text=prepared.patch_text,
        )
        return self.security.get_approval_packet(approval_id) or {}

    def execute_plan(
        self,
        plan_id: str,
        *,
        dry_run: bool = True,
        approvals: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        approvals = approvals or {}
        plan = self.plan_repo.get_plan(plan_id)
        self.plan_repo.set_status(plan_id, "running")
        results: list[dict[str, Any]] = []
        awaiting_approval = False

        for step in plan.steps:
            action_class = ActionClass(step.action_class)
            approval_id = approvals.get(step.step_id)
            if action_class in {ActionClass.P2, ActionClass.P3} and (
                step.requires_approval or action_class == ActionClass.P3
            ):
                if not approval_id:
                    existing = self.security.find_approval(
                        plan_id=plan_id,
                        step_id=step.step_id,
                        statuses=["pending"],
                    )
                    if existing:
                        pending_id = existing["approval_id"]
                    else:
                        pending_id = self.security.request_approval(
                            plan_id=plan_id,
                            step_id=step.step_id,
                            action_class=action_class,
                            action_desc=step.proposed_action,
                        )
                    packet = {}
                    if self.execution_service:
                        try:
                            packet = self._prepare_evidence_packet(
                                plan=plan,
                                step=step,
                                approval_id=pending_id,
                                action_class=action_class,
                            )
                        except Exception as exc:
                            packet = {"error": str(exc)}
                    awaiting_approval = True
                    result = {
                        "step_id": step.step_id,
                        "status": "awaiting_approval",
                        "approval_id": pending_id,
                        "reason": "Protected action requires approval.",
                        "approval_packet_recommendation": (
                            (packet.get("packet") or {}).get("recommended_decision")
                            if packet
                            else None
                        ),
                        "preflight_summary": (packet.get("preflight") or {}).get("summary")
                        if packet
                        else None,
                    }
                    results.append(result)
                    self.security.audit(
                        action=step.proposed_action,
                        status="awaiting_approval",
                        details=result,
                        plan_id=plan_id,
                        step_id=step.step_id,
                        action_class=action_class,
                    )
                    continue
            try:
                self.security.enforce(
                    action_class,
                    requires_approval=step.requires_approval,
                    approval_id=approval_id,
                )
            except PermissionError as exc:
                result = {"step_id": step.step_id, "status": "blocked", "reason": str(exc)}
                results.append(result)
                self.security.audit(
                    action=step.proposed_action,
                    status="blocked",
                    details=result,
                    plan_id=plan_id,
                    step_id=step.step_id,
                    action_class=action_class,
                )
                continue

            prepare_id: str | None = None
            if action_class in {ActionClass.P2, ActionClass.P3}:
                prepare_id = self.security.prepare_action(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    action_class=action_class,
                    action_desc=step.proposed_action,
                )

            tool = self.tools.get(step.proposed_action)
            if not tool:
                result = {
                    "step_id": step.step_id,
                    "status": "failed",
                    "reason": f"Tool not registered: {step.proposed_action}",
                }
                results.append(result)
                self.security.audit(
                    action=step.proposed_action,
                    status="failed",
                    details=result,
                    plan_id=plan_id,
                    step_id=step.step_id,
                    action_class=action_class,
                )
                continue

            output = tool(step.payload, dry_run)
            if prepare_id and not dry_run:
                self.security.commit_action(prepare_id)
            if prepare_id:
                self.security.add_rollback_marker(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    marker={
                        "prepare_id": prepare_id,
                        "rollback_hint": step.rollback,
                        "payload": step.payload,
                    },
                )
            result = {"step_id": step.step_id, "status": "ok", "output": output}
            results.append(result)
            self.security.audit(
                action=step.proposed_action,
                status="ok",
                details={"dry_run": dry_run, "output": output},
                plan_id=plan_id,
                step_id=step.step_id,
                action_class=action_class,
            )

        if awaiting_approval:
            self.plan_repo.set_status(plan_id, "awaiting_approval")
        elif any(result["status"] == "failed" for result in results):
            self.plan_repo.set_status(plan_id, "failed")
        else:
            self.plan_repo.set_status(plan_id, "completed")
        return results


class JarvisRuntime:
    def __init__(
        self,
        db_path: str | Path,
        repo_path: str | Path | None = None,
        review_service: ReviewService | None = None,
        cognition_backend: CognitionBackend | None = None,
        cognition_enabled: bool | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.repo_path = Path(
            repo_path or os.getenv("JARVIS_REPO_PATH") or os.getcwd()
        ).resolve()
        self.boot_id = new_id("boot")
        self.boot_started_at = utc_now_iso()
        self.boot_pid = os.getpid()
        self.boot_git_sha = self._detect_repo_git_sha(self.repo_path)
        self.cognition_backend = cognition_backend or build_backend_from_env()
        self.cognition_enabled = (
            cognition_enabled
            if cognition_enabled is not None
            else cognition_enabled_from_env()
        )

        self.state_graph = StateGraph(self.db_path)
        self.memory = MemoryStore(self.db_path)
        self.dialogue_retriever = DialogueRetriever(self.memory)
        self.dialogue_state = DialogueStateStore(self.db_path)
        self.signal_ingest = SignalIngestStore(self.db_path)
        self.device_tokens = DeviceTokenStore(self.db_path)
        self.presence_health = PresenceHealthStore(self.db_path)
        self.surface_sessions = SurfaceSessionStateStore(self.db_path)
        self.relationship_modes = RelationshipModeEngine(self.db_path)
        self.pushback_calibration = PushbackCalibrationStore(self.db_path)
        self.tone_balance = ToneBalanceStore(self.db_path)
        self.voice_soak = VoiceContinuitySoakStore(self.db_path)
        self.voice_assets = VoiceAssetPackStore(self.repo_path)
        self.voice_tuning_state = VoiceTuningStateStore(self.db_path)
        self.adaptive_policy = AdaptivePolicyStore(self.db_path)
        self.security = SecurityManager(self.db_path)
        self.operator_state = OperatorStateStore(self.db_path)
        self.identity_state = IdentityStateStore(self.db_path)
        self.interrupt_store = InterruptStore(self.db_path)
        self.plan_repo = PlanRepository(self.db_path)
        self.archive_service = DigestArchiveService(self.db_path)
        self.consciousness_surfaces = ConsciousnessSurfaceService(self.db_path)
        self.codex_delegation = CodexDelegationService(
            db_path=self.db_path,
            repo_path=self.repo_path,
        )
        self.ingest_token = str(os.getenv("JARVIS_INGEST_TOKEN") or "").strip()
        self.synthesis_engine = SynthesisEngine(
            self.db_path,
            backend=self.cognition_backend,
        )
        self.cognition = CognitionEngine(
            self.db_path,
            backend=self.cognition_backend,
            enabled=self.cognition_enabled,
        )
        self.execution_service = ApprovalExecutionService(
            repo_path=str(self.repo_path),
            worktrees_root=str(self.db_path.parent / "worktrees"),
            protected_prefixes=("ui/",),
        )
        self.publication_service = RemotePublicationService(repo_path=str(self.repo_path))
        self.review_service = review_service or _build_default_review_service()

        self.zenith = ZenithSkill(self.repo_path)
        self.academics = AcademicsSkill(self.repo_path)
        self.markets = MarketsSkill(self.repo_path)
        self.identity = IdentitySkill(self.repo_path)
        self.tools = {
            **self.zenith.register_tools(),
            **self.academics.register_tools(),
            **self.markets.register_tools(),
            **self.identity.register_tools(),
        }
        self.planner = Planner(self.zenith, self.academics, self.markets, self.state_graph)
        self.executor = Executor(
            repo_path=self.repo_path,
            security=self.security,
            plan_repo=self.plan_repo,
            tools=self.tools,
            execution_service=self.execution_service,
        )
        self.openclaw_ws_bridge = OpenClawWsBridge(self)
        self.openclaw_event_router = OpenClawEventRouter(
            runtime=self,
            bridge=self.openclaw_ws_bridge,
            session_state=self.surface_sessions,
        )
        self.openclaw_gateway_client: OpenClawGatewayClient | None = None
        self._openclaw_gateway_enabled = False
        self._openclaw_gateway_state_marker: tuple[str, bool] | None = None
        self._openclaw_gateway_config_error: str | None = None
        self.node_command_broker = NodeCommandBroker()
        self.openclaw_reply_orchestrator = OpenClawReplyOrchestrator(self)
        self.taskflow_presence_runner = TaskFlowPresenceRunner(self)
        self._adaptive_turn_counter = 0
        self._live_brief_cache: dict[str, Any] = {
            "generated_at": 0.0,
            "max_age_seconds": 45.0,
            "briefs": {},
        }
        self._dialogue_identity_capsule_cache: dict[str, Any] = {
            "generated_at": 0.0,
            "contract_hash": None,
            "capsule": {},
        }
        self.configure_openclaw_gateway_loop()

    @staticmethod
    def _detect_repo_git_sha(repo_path: Path) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        value = str(proc.stdout or "").strip()
        return value or None

    def get_reply_policy_hash(self) -> str:
        retrieval = self.get_dialogue_retrieval_config()
        payload = {
            "backend": self.cognition_backend.name,
            "model": self.cognition_backend.model,
            "model_assisted": bool(getattr(self.cognition_backend, "model_assisted", False)),
            "presence_model_first": str(os.getenv("JARVIS_PRESENCE_MODEL_FIRST") or "true").strip().lower(),
            "presence_timeout_seconds": str(os.getenv("JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS") or "20").strip(),
            "retrieval": retrieval,
            "adaptive_policy_revision": self.get_adaptive_policy_revision(),
            "boot_git_sha": self.boot_git_sha,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def get_boot_identity(self) -> dict[str, Any]:
        retrieval = self.get_dialogue_retrieval_config()
        embed = retrieval.get("embed_rerank") if isinstance(retrieval.get("embed_rerank"), dict) else {}
        flag = retrieval.get("flag_rerank") if isinstance(retrieval.get("flag_rerank"), dict) else {}
        return {
            "boot_id": str(self.boot_id),
            "boot_started_at": str(self.boot_started_at),
            "pid": int(self.boot_pid),
            "git_sha": self.boot_git_sha,
            "backend": str(self.cognition_backend.name),
            "model": str(self.cognition_backend.model or ""),
            "model_assisted": bool(getattr(self.cognition_backend, "model_assisted", False)),
            "reply_policy_hash": self.get_reply_policy_hash(),
            "embed_rerank_enabled": bool(embed.get("enabled")),
            "embed_rerank_available": bool(embed.get("available")),
            "flag_rerank_enabled": bool(flag.get("enabled")),
            "flag_rerank_available": bool(flag.get("available")),
        }

    def _extract_candidates(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return [
            *self.zenith.extract_candidates(event),
            *self.academics.extract_candidates(event),
            *self.markets.extract_candidates(event),
            *self.identity.extract_candidates(event),
        ]

    def ingest_event(self, *, source: str, source_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.state_graph.normalize_event(
            source=source,
            source_type=source_type,
            payload=payload,
            auth_context="local_runtime",
        )
        return self.ingest_envelope(event)

    def _signal_source_type(self, *, kind: str, payload: dict[str, Any], provider: str) -> str:
        payload_source_type = str(payload.get("source_type") or "").strip()
        if payload_source_type:
            return payload_source_type
        if kind == "email.thread":
            if provider == "gmail":
                return "academic.professor_message"
            return "signal.email_thread"
        if kind == "calendar.event":
            if provider == "google_calendar":
                return "academic.class_scheduled"
            return "signal.calendar_event"
        if kind == "markets.signal":
            return "market.signal_detected"
        if kind == "context.update":
            return "personal.context_signal"
        if kind == "operator.command":
            return "operator.command"
        return "signal.message_inbound"

    def _infer_domain_from_provider(self, provider: str) -> str:
        provider_map = {
            "gmail": "academics",
            "google_calendar": "academics",
            "markets": "markets",
            "zenith": "zenith",
            "jarvis_operator": "personal",
            "openclaw": "personal",
            "unknown": "personal",
        }
        return provider_map.get(provider, "personal")

    def ingest_signal(self, raw_signal: dict[str, Any], *, auth_context: str = "api_ingest") -> dict[str, Any]:
        signal, meta = normalize_signal_envelope(raw_signal)
        recorded = self.signal_ingest.record(
            signal=signal,
            raw_payload=dict(raw_signal or {}),
            content_hash=str(meta["content_hash"]),
            dedupe_key=str(meta["dedupe_key"]),
            truncated=bool(meta.get("truncated")),
            redacted=bool(meta.get("redacted")),
        )
        duplicate = bool(recorded.get("duplicate"))
        self.memory.append_event(
            "ingest.signal_received",
            {
                "signal_id": signal.id,
                "duplicate": duplicate,
                "kind": signal.kind,
                "provider": signal.provenance.provider,
                "source_kind": signal.provenance.source_kind,
                "content_hash": meta.get("content_hash"),
            },
        )
        if duplicate:
            return {
                "ok": True,
                "signal_id": recorded.get("signal_id") or signal.id,
                "duplicate": True,
                "accepted": False,
                "content_hash": meta.get("content_hash"),
                "created_at": recorded.get("created_at"),
            }

        payload = dict(signal.payload or {})
        provider = str(signal.provenance.provider)
        inferred_domain = self._infer_domain_from_provider(provider)
        payload.setdefault("project", inferred_domain)
        payload.setdefault("domain", inferred_domain)
        payload["ingestion_source_kind"] = str(signal.provenance.source_kind)
        payload["ingestion_provider"] = provider
        payload["signal_id"] = signal.id
        payload["signal_kind"] = signal.kind
        payload["signal_schema_version"] = signal.schema_version
        payload["signal_priority_hint"] = signal.priority_hint or "normal"
        payload["signal_source_id"] = signal.provenance.source_id
        payload["signal_trust"] = signal.provenance.trust
        payload["signal_redaction_level"] = signal.provenance.redaction_level
        if signal.identity_key:
            payload["identity_key"] = signal.identity_key
        if signal.session_key:
            payload["session_key"] = signal.session_key

        source = f"signal_ingest:{provider}"
        source_type = self._signal_source_type(
            kind=str(signal.kind),
            payload=payload,
            provider=provider,
        )
        event = self.state_graph.normalize_event(
            source=source,
            source_type=source_type,
            payload=payload,
            auth_context=auth_context,
        )
        ingestion = self.ingest_envelope(event)
        return {
            "ok": True,
            "signal_id": signal.id,
            "duplicate": False,
            "accepted": True,
            "content_hash": meta.get("content_hash"),
            "truncated": bool(meta.get("truncated")),
            "redacted": bool(meta.get("redacted")),
            "ingestion": ingestion,
        }

    def list_ingested_signals(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.signal_ingest.list_recent(limit=limit)

    def ingest_token_required(self) -> bool:
        return bool(self.ingest_token)

    def ingest_token_valid(self, provided_token: str | None) -> bool:
        if not self.ingest_token:
            return True
        return str(provided_token or "").strip() == self.ingest_token

    def _env_truthy(self, name: str, *, default: bool = False) -> bool:
        raw = str(os.getenv(name) or "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _on_openclaw_gateway_state(self, state: str, snapshot: dict[str, Any]) -> None:
        connection_status_map = {
            "starting": "connecting",
            "connected": "connected",
            "connect_error": "reconnecting",
            "connect_handshake_acked": "connected",
            "connect_handshake_rejected": "reconnecting",
            "recv_error": "reconnecting",
            "heartbeat_error": "reconnecting",
            "pairing_pending": "connected",
            "pairing_approved": "connected",
            "pairing_rotated": "connected",
            "pairing_revoked": "connected",
            "stopped": "stopped",
        }
        normalized = str(state or "").strip().lower() or "unknown"
        connected = bool(snapshot.get("connected"))
        marker = (normalized, connected)
        if marker == self._openclaw_gateway_state_marker:
            return
        self._openclaw_gateway_state_marker = marker
        self.set_presence_bridge_status(
            connection_status=connection_status_map.get(normalized, "unknown"),
            connected=connected,
            details={
                "gateway_loop_state": normalized,
                "ws_url": snapshot.get("ws_url"),
                "owner_id": snapshot.get("owner_id"),
                "last_error": snapshot.get("last_error"),
                "reconnect_attempts": snapshot.get("reconnect_attempts"),
                "pairing_state": snapshot.get("pairing_state"),
                "commands_enabled": snapshot.get("commands_enabled"),
                "connect_handshake_state": snapshot.get("connect_handshake_state"),
                "protocol_profile_id": snapshot.get("protocol_profile_id"),
            },
        )

    def configure_openclaw_gateway_loop(
        self,
        *,
        ws_url: str | None = None,
        token_ref: str | None = None,
        owner_id: str | None = None,
        client_name: str | None = None,
        protocol_profile_id: str | None = None,
        protocol_profile_path: str | None = None,
        allow_remote: bool | None = None,
        enabled: bool | None = None,
        connect_timeout_seconds: float | None = None,
        heartbeat_interval_seconds: float | None = None,
        min_backoff_seconds: float | None = None,
        max_backoff_seconds: float | None = None,
        subscribe_payloads: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        final_ws_url = str(ws_url or os.getenv("JARVIS_OPENCLAW_GATEWAY_WS_URL") or "").strip()
        final_token_ref = str(token_ref or os.getenv("JARVIS_OPENCLAW_GATEWAY_TOKEN_REF") or "").strip()
        final_owner_id = str(owner_id or os.getenv("JARVIS_OPENCLAW_GATEWAY_OWNER_ID") or "primary_operator").strip()
        final_client_name = str(client_name or os.getenv("JARVIS_OPENCLAW_GATEWAY_CLIENT_NAME") or "jarvis").strip()
        final_profile_id = str(
            protocol_profile_id
            or os.getenv("JARVIS_OPENCLAW_GATEWAY_PROFILE_ID")
            or "openclaw_gateway_v2026_04_2"
        ).strip()
        final_profile_path = str(
            protocol_profile_path
            or os.getenv("JARVIS_OPENCLAW_GATEWAY_PROFILE_PATH")
            or ""
        ).strip() or None
        final_allow_remote = bool(
            allow_remote if allow_remote is not None else self._env_truthy("JARVIS_OPENCLAW_GATEWAY_ALLOW_REMOTE")
        )
        final_enabled = bool(
            enabled if enabled is not None else self._env_truthy("JARVIS_OPENCLAW_GATEWAY_ENABLE", default=False)
        )
        self._openclaw_gateway_enabled = final_enabled
        self._openclaw_gateway_config_error = None

        if not final_ws_url or not final_token_ref:
            self.openclaw_gateway_client = None
            return self.get_openclaw_gateway_status()

        payloads = tuple(
            dict(item)
            for item in (subscribe_payloads or [])
            if isinstance(item, dict)
        )
        config = OpenClawGatewayConfig(
            ws_url=final_ws_url,
            token_ref=final_token_ref,
            owner_id=final_owner_id,
            client_name=final_client_name,
            protocol_profile_id=final_profile_id,
            protocol_profile_path=final_profile_path,
            allow_remote=final_allow_remote,
            connect_timeout_seconds=float(connect_timeout_seconds or 8.0),
            heartbeat_interval_seconds=float(heartbeat_interval_seconds or 20.0),
            min_backoff_seconds=float(min_backoff_seconds or 1.0),
            max_backoff_seconds=float(max_backoff_seconds or 30.0),
            subscribe_payloads=payloads,
        )
        try:
            self.openclaw_gateway_client = OpenClawGatewayClient(
                config=config,
                route_event=self.openclaw_event_router.route_gateway_event,
                on_state=self._on_openclaw_gateway_state,
            )
        except Exception as exc:
            self.openclaw_gateway_client = None
            self._openclaw_gateway_config_error = str(exc)
        return self.get_openclaw_gateway_status()

    def start_openclaw_gateway_loop(self) -> dict[str, Any]:
        if self.openclaw_gateway_client is None:
            self.configure_openclaw_gateway_loop()
        if self.openclaw_gateway_client is None:
            return self.get_openclaw_gateway_status()
        if not self._openclaw_gateway_enabled:
            return self.get_openclaw_gateway_status()
        self.openclaw_gateway_client.start()
        return self.get_openclaw_gateway_status()

    def pump_openclaw_gateway(self, *, max_messages: int = 100) -> dict[str, Any]:
        if self.openclaw_gateway_client is None:
            return self.get_openclaw_gateway_status()
        if not self._openclaw_gateway_enabled:
            return self.get_openclaw_gateway_status()
        snapshot = self.openclaw_gateway_client.snapshot()
        if not bool(snapshot.get("running")):
            self.openclaw_gateway_client.start()
        self.openclaw_gateway_client.tick(max_messages=max_messages)
        return self.get_openclaw_gateway_status()

    def stop_openclaw_gateway_loop(self) -> dict[str, Any]:
        if self.openclaw_gateway_client is not None:
            self.openclaw_gateway_client.stop()
        return self.get_openclaw_gateway_status()

    def get_openclaw_gateway_status(self) -> dict[str, Any]:
        snapshot = self.openclaw_gateway_client.snapshot() if self.openclaw_gateway_client is not None else {}
        return {
            "enabled": bool(self._openclaw_gateway_enabled),
            "configured": bool(self.openclaw_gateway_client is not None),
            "running": bool(snapshot.get("running")),
            "connected": bool(snapshot.get("connected")),
            "ws_url": snapshot.get("ws_url"),
            "owner_id": snapshot.get("owner_id"),
            "client_name": snapshot.get("client_name"),
            "protocol_profile_id": snapshot.get("protocol_profile_id"),
            "protocol_gateway_version": snapshot.get("protocol_gateway_version"),
            "protocol_source": snapshot.get("protocol_source"),
            "last_connect_at": snapshot.get("last_connect_at"),
            "last_disconnect_at": snapshot.get("last_disconnect_at"),
            "last_message_at": snapshot.get("last_message_at"),
            "last_error": snapshot.get("last_error"),
            "reconnect_attempts": int(snapshot.get("reconnect_attempts") or 0),
            "frames_received": int(snapshot.get("frames_received") or 0),
            "events_routed": int(snapshot.get("events_routed") or 0),
            "connect_handshake_required": bool(snapshot.get("connect_handshake_required")),
            "connect_handshake_state": snapshot.get("connect_handshake_state"),
            "connect_handshake_sent_at": snapshot.get("connect_handshake_sent_at"),
            "connect_handshake_acked_at": snapshot.get("connect_handshake_acked_at"),
            "connect_handshake_ack_event_type": snapshot.get("connect_handshake_ack_event_type"),
            "pairing_state": snapshot.get("pairing_state"),
            "commands_enabled": bool(snapshot.get("commands_enabled")),
            "last_pairing_event_type": snapshot.get("last_pairing_event_type"),
            "last_pairing_event_at": snapshot.get("last_pairing_event_at"),
            "paired_node_id": snapshot.get("paired_node_id"),
            "last_token_ref_hint": snapshot.get("last_token_ref_hint"),
            "config_error": self._openclaw_gateway_config_error,
        }

    def get_openclaw_gateway_profile(self) -> dict[str, Any]:
        if self.openclaw_gateway_client is None:
            status = self.get_openclaw_gateway_status()
            return {
                "configured": False,
                "profile_id": status.get("protocol_profile_id"),
                "gateway_version": status.get("protocol_gateway_version"),
                "source": status.get("protocol_source"),
            }
        profile = self.openclaw_gateway_client.protocol_profile
        return {
            "configured": True,
            "profile": profile.to_dict(),
        }

    def set_presence_bridge_status(
        self,
        *,
        connection_status: str,
        connected: bool,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.presence_health.set_bridge_status(
            connection_status=connection_status,
            connected=connected,
            details=details,
        )
        self.memory.append_event(
            "presence.bridge_status",
            {
                "connection_status": state.get("connection_status"),
                "connected": state.get("connected"),
            },
        )
        return state

    def record_presence_gateway_event(
        self,
        *,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.presence_health.record_gateway_event(
            event_type=event_type,
            details=details,
        )
        self.memory.append_event(
            "presence.gateway_event",
            {
                "event_type": event_type,
                "details": dict(details or {}),
            },
        )
        return state

    def pair_presence_node(
        self,
        *,
        node_id: str,
        device_id: str,
        owner_id: str,
        gateway_token_ref: str,
        node_token_ref: str,
        pairing_status: str = "paired",
        metadata: dict[str, Any] | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        normalized = validate_node_secret_plan(
            {
                "node_id": node_id,
                "device_id": device_id,
                "owner_id": owner_id,
                "gateway_token_ref": gateway_token_ref,
                "node_token_ref": node_token_ref,
                "pairing_status": pairing_status,
                "metadata": dict(metadata or {}),
            }
        )
        paired = self.device_tokens.upsert_pairing(**normalized)
        self.security.audit(
            action="presence_pair_node",
            status="ok",
            details={"actor": actor, "node_id": node_id, "device_id": device_id},
            action_class=ActionClass.P1,
        )
        self.memory.append_event(
            "presence.node_paired",
            {
                "node_id": node_id,
                "device_id": device_id,
                "owner_id": owner_id,
            },
        )
        return paired

    def mark_presence_node_seen(
        self,
        *,
        node_id: str,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        updated = self.device_tokens.mark_seen(
            node_id=node_id,
            metadata_patch=metadata_patch,
        )
        if updated:
            self.memory.append_event(
                "presence.node_seen",
                {
                    "node_id": node_id,
                    "metadata_patch": dict(metadata_patch or {}),
                },
            )
        return updated

    def revoke_presence_node(self, *, node_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any] | None:
        revoked = self.device_tokens.revoke_node(node_id=node_id, reason=reason)
        if not revoked:
            return None
        self.security.audit(
            action="presence_revoke_node",
            status="ok",
            details={"actor": actor, "node_id": node_id, "reason": reason},
            action_class=ActionClass.P1,
        )
        self.memory.append_event(
            "presence.node_revoked",
            {
                "node_id": node_id,
                "reason": reason,
            },
        )
        return revoked

    def list_presence_nodes(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.device_tokens.list_nodes(status=status, limit=limit)

    def apply_gateway_pairing_event(
        self,
        *,
        node_id: str,
        pairing_status: str,
        event_type: str,
        token_ref_hint: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_node_id = str(node_id or "").strip()
        if not normalized_node_id:
            return None
        metadata_patch = {
            "last_pairing_event_type": str(event_type or "unknown"),
            "last_pairing_status": str(pairing_status or "unknown"),
            "last_pairing_event_at": utc_now_iso(),
        }
        if token_ref_hint:
            metadata_patch["token_ref_hint_seen"] = str(token_ref_hint)
        updated = self.device_tokens.update_pairing_status(
            node_id=normalized_node_id,
            pairing_status=pairing_status,
            metadata_patch=metadata_patch,
        )
        if token_ref_hint and updated:
            try:
                parse_secret_ref(token_ref_hint)
            except SecretRefError:
                pass
            else:
                updated = self.device_tokens.rotate_node_token_ref(
                    node_id=normalized_node_id,
                    node_token_ref=str(token_ref_hint),
                )
        if updated:
            self.memory.append_event(
                "presence.node_pairing_state_updated",
                {
                    "node_id": normalized_node_id,
                    "pairing_status": pairing_status,
                    "event_type": event_type,
                    "token_ref_hint_seen": bool(token_ref_hint),
                },
            )
        return updated

    def list_surface_sessions(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.surface_sessions.list_sessions(status=status, limit=limit)

    def broker_node_command(
        self,
        *,
        command: str,
        payload: dict[str, Any] | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        decision = self.node_command_broker.broker(command=command, payload=payload, actor=actor)
        self.memory.append_event(
            "presence.node_command_brokered",
            {
                "command": command,
                "allowed": decision.get("allowed"),
                "capability": decision.get("capability"),
                "action_class": decision.get("action_class"),
            },
        )
        return decision

    def _codex_delegation_enabled(self, *, context: dict[str, Any] | None = None) -> bool:
        context_map = dict(context or {})
        if context_map.get("codex_delegate") is not None:
            value = context_map.get("codex_delegate")
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "on"}
        env_value = str(os.getenv("JARVIS_CODEX_DELEGATION_ENABLED") or "").strip().lower()
        if not env_value:
            return True
        return env_value in {"1", "true", "yes", "on"}

    def _codex_auto_execute_enabled(self, *, context: dict[str, Any] | None = None) -> bool:
        context_map = dict(context or {})
        if context_map.get("codex_auto_execute") is not None:
            value = context_map.get("codex_auto_execute")
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "on"}
        env_value = str(os.getenv("JARVIS_CODEX_AUTO_EXECUTE") or "").strip().lower()
        if not env_value:
            return False
        return env_value in {"1", "true", "yes", "on"}

    def list_codex_tasks(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.codex_delegation.list_tasks(status=status, limit=limit)

    def get_codex_task(self, *, task_id: str) -> dict[str, Any] | None:
        return self.codex_delegation.get_task(task_id)

    def create_codex_task(
        self,
        *,
        text: str,
        source_surface: str = "",
        session_id: str = "",
        actor: str = "owner",
        write_enabled: bool = True,
        auto_execute: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_map = self._adaptive_context(context)
        effective_auto_execute = (
            bool(auto_execute) if auto_execute is not None else self._codex_auto_execute_enabled(context=context_map)
        )
        submission = self.codex_delegation.submit_task(
            user_text=str(text or ""),
            source_surface=str(source_surface or ""),
            session_id=str(session_id or ""),
            actor=str(actor or "owner"),
            write_enabled=bool(write_enabled),
            auto_execute=effective_auto_execute,
            context=context_map,
        )
        task = submission.get("task") if isinstance(submission.get("task"), dict) else {}
        self.memory.append_event(
            "codex.task_created",
            {
                "task_id": task.get("task_id"),
                "source_surface": source_surface,
                "session_id": session_id,
                "write_enabled": bool(task.get("write_enabled")),
                "auto_execute": bool(task.get("auto_execute")),
                "duplicate": bool(submission.get("duplicate")),
                "effort_tier": task.get("effort_tier"),
                "reasoning_effort": task.get("reasoning_effort"),
            },
        )
        return submission

    def execute_codex_task(self, *, task_id: str, background: bool = True) -> dict[str, Any]:
        result = self.codex_delegation.execute_task(task_id, background=background)
        self.memory.append_event(
            "codex.task_execution_requested",
            {
                "task_id": task_id,
                "background": bool(background),
                "ok": bool(result.get("ok")),
                "status": result.get("status"),
            },
        )
        return result

    def classify_work_item(
        self,
        *,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged_context = self._adaptive_context(context)
        return self.codex_delegation.classify_work_item(
            text=str(text or ""),
            context=merged_context,
        )

    def classify_request_intent(
        self,
        *,
        text: str,
        explicit_directive: bool = False,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged_context = self._adaptive_context(context)
        return self.codex_delegation.classify_intent(
            text=str(text or ""),
            explicit_directive=bool(explicit_directive),
            context=merged_context,
        )

    def get_adaptive_policy(self) -> dict[str, Any]:
        return self.adaptive_policy.get_policy()

    def get_adaptive_policy_revision(self) -> str | None:
        policy = self.get_adaptive_policy()
        metadata = policy.get("metadata") if isinstance(policy.get("metadata"), dict) else {}
        value = str(metadata.get("revision") or "").strip()
        return value or None

    def list_adaptive_policy_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.adaptive_policy.list_history(limit=limit)

    def update_adaptive_policy(
        self,
        *,
        patch: dict[str, Any],
        reason: str = "manual_update",
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated = self.adaptive_policy.update_patch(
            patch=dict(patch or {}),
            reason=reason,
            metrics=metrics,
        )
        self.memory.append_event(
            "presence.adaptive_policy_updated",
            {
                "revision": ((updated.get("policy") or {}).get("metadata") or {}).get("revision"),
                "reason": reason,
            },
        )
        return updated

    def update_self_patch_quota(
        self,
        *,
        weekly_remaining_percent: float,
        actor: str = "operator",
        reason: str = "operator_quota_update",
        min_weekly_remaining_percent: float | None = None,
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {
            "self_patch": {
                "weekly_remaining_percent": float(weekly_remaining_percent),
            }
        }
        if isinstance(min_weekly_remaining_percent, (int, float)):
            patch["self_patch"]["min_weekly_remaining_percent"] = float(min_weekly_remaining_percent)
        updated = self.update_adaptive_policy(
            patch=patch,
            reason=f"self_patch_quota:{reason}",
            metrics={"actor": str(actor or "operator")},
        )
        policy = updated.get("policy") if isinstance(updated.get("policy"), dict) else {}
        self_patch = policy.get("self_patch") if isinstance(policy.get("self_patch"), dict) else {}
        payload = {
            "actor": str(actor or "operator"),
            "reason": str(reason or "operator_quota_update"),
            "weekly_remaining_percent": self_patch.get("weekly_remaining_percent"),
            "min_weekly_remaining_percent": self_patch.get("min_weekly_remaining_percent"),
            "policy_revision": (
                ((policy.get("metadata") or {}).get("revision"))
                if isinstance(policy.get("metadata"), dict)
                else None
            ),
        }
        self.memory.append_event("codex.self_patch_quota_updated", payload)
        return {
            "ok": True,
            "quota": {
                "weekly_remaining_percent": self_patch.get("weekly_remaining_percent"),
                "min_weekly_remaining_percent": self_patch.get("min_weekly_remaining_percent"),
            },
            "policy_revision": payload.get("policy_revision"),
            "policy": policy,
        }

    @staticmethod
    def _parse_iso_utc(value: str | None) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    def list_self_patch_events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        created = self.memory.list_events(limit=limit, event_type="codex.self_patch_task_created")
        skipped = self.memory.list_events(limit=limit, event_type="codex.self_patch_skipped")
        blocked = self.memory.list_events(limit=limit, event_type="codex.self_patch_blocked")
        approval = self.memory.list_events(limit=limit, event_type="codex.self_patch_approval_required")
        quota_updates = self.memory.list_events(limit=limit, event_type="codex.self_patch_quota_updated")
        rows = list(created) + list(skipped) + list(blocked) + list(approval) + list(quota_updates)
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[: max(1, int(limit))]

    def _self_patch_open_task_count(self, *, limit: int = 200) -> int:
        tasks = self.list_codex_tasks(status="all", limit=limit)
        count = 0
        for task in tasks:
            context = task.get("context") if isinstance(task.get("context"), dict) else {}
            if not bool(context.get("self_patch")):
                continue
            status = str(task.get("status") or "").strip().lower()
            if status in {"queued", "running"}:
                count += 1
        return count

    def trigger_self_patch_task(
        self,
        *,
        issue: str,
        reason: str,
        effort_tier: str = "pro",
        auto_execute: bool | None = None,
        metrics: dict[str, Any] | None = None,
        project_scope: str | None = None,
        approval_source: str | None = None,
        change_impact: str | None = None,
        requested_capabilities: list[str] | None = None,
        external_access: bool | None = None,
        weekly_remaining_percent: float | None = None,
    ) -> dict[str, Any]:
        objective = str(issue or "").strip()
        if not objective:
            return {"ok": False, "error": "self_patch_issue_required"}
        policy = self.get_adaptive_policy()
        cfg = policy.get("self_patch") if isinstance(policy.get("self_patch"), dict) else {}
        if not bool(cfg.get("enabled", True)):
            payload = {
                "ok": False,
                "error": "self_patch_disabled",
                "issue": objective,
                "reason": reason,
            }
            self.memory.append_event("codex.self_patch_blocked", dict(payload))
            self.memory.append_event(
                "codex.self_patch_skipped",
                {
                    "reason": reason,
                    "skip_reason": "self_patch_disabled",
                    "issue": objective,
                },
            )
            return payload

        issue_lower = objective.lower()
        allowed_projects = {
            str(item).strip().lower()
            for item in (cfg.get("allowed_projects") if isinstance(cfg.get("allowed_projects"), list) else [])
            if str(item).strip()
        } or {"jarvis", "market_ml", "betting_bot"}
        default_project = str(cfg.get("default_project") or "jarvis").strip().lower() or "jarvis"
        inferred_project_scope = default_project
        if any(term in issue_lower for term in ("bet", "betting", "odds", "wager", "sportsbook", "parlay")):
            inferred_project_scope = "betting_bot"
        elif any(term in issue_lower for term in ("market", "trading", "alpha", "forecast", "machine learning", "ml")):
            inferred_project_scope = "market_ml"
        normalized_project_scope = str(project_scope or inferred_project_scope).strip().lower() or default_project
        if normalized_project_scope not in allowed_projects:
            payload = {
                "ok": False,
                "error": "project_scope_not_allowed",
                "issue": objective,
                "reason": reason,
                "project_scope": normalized_project_scope,
                "allowed_projects": sorted(allowed_projects),
            }
            self.memory.append_event("codex.self_patch_blocked", dict(payload))
            self.memory.append_event(
                "codex.self_patch_skipped",
                {
                    "reason": reason,
                    "skip_reason": "project_scope_not_allowed",
                    "project_scope": normalized_project_scope,
                    "allowed_projects": sorted(allowed_projects),
                },
            )
            return payload

        allowed_approval_sources = {
            str(item).strip().lower()
            for item in (cfg.get("allowed_approval_sources") if isinstance(cfg.get("allowed_approval_sources"), list) else [])
            if str(item).strip()
        } or {"gpt", "codex", "owner"}
        default_approval_source = str(cfg.get("default_auto_approval_source") or "codex").strip().lower() or "codex"
        normalized_approval_source = str(approval_source or default_approval_source).strip().lower() or default_approval_source
        if normalized_approval_source not in allowed_approval_sources:
            payload = {
                "ok": False,
                "error": "approval_source_not_allowed",
                "issue": objective,
                "reason": reason,
                "approval_source": normalized_approval_source,
                "allowed_approval_sources": sorted(allowed_approval_sources),
            }
            self.memory.append_event("codex.self_patch_blocked", dict(payload))
            self.memory.append_event(
                "codex.self_patch_skipped",
                {
                    "reason": reason,
                    "skip_reason": "approval_source_not_allowed",
                    "approval_source": normalized_approval_source,
                    "allowed_approval_sources": sorted(allowed_approval_sources),
                },
            )
            return payload

        weekly_remaining_value: float
        if isinstance(weekly_remaining_percent, (int, float)):
            weekly_remaining_value = float(weekly_remaining_percent)
        else:
            weekly_remaining_value = float(cfg.get("weekly_remaining_percent") or 100.0)
        min_weekly_remaining = float(cfg.get("min_weekly_remaining_percent") or 40.0)
        if weekly_remaining_value < min_weekly_remaining:
            payload = {
                "ok": False,
                "error": "quota_below_threshold",
                "issue": objective,
                "reason": reason,
                "weekly_remaining_percent": round(weekly_remaining_value, 4),
                "min_weekly_remaining_percent": round(min_weekly_remaining, 4),
            }
            self.memory.append_event("codex.self_patch_blocked", dict(payload))
            self.memory.append_event(
                "codex.self_patch_skipped",
                {
                    "reason": reason,
                    "skip_reason": "quota_below_threshold",
                    "weekly_remaining_percent": round(weekly_remaining_value, 4),
                    "min_weekly_remaining_percent": round(min_weekly_remaining, 4),
                },
            )
            return payload

        raw_change_impact = str(change_impact or "").strip().lower()
        normalized_change_impact = raw_change_impact or "minor"
        if normalized_change_impact not in {"minor", "moderate", "major"}:
            normalized_change_impact = "major" if "major" in normalized_change_impact else "minor"
        if not raw_change_impact and any(
            phrase in issue_lower
            for phrase in (
                "major",
                "rewrite",
                "overhaul",
                "replace architecture",
                "wide refactor",
                "cross-project migration",
                "ui redesign",
                "change view",
                "change content",
                "new layout",
                "rewrite copy",
            )
        ):
            normalized_change_impact = "major"
        requested_caps = []
        for item in requested_capabilities or []:
            value = str(item).strip().lower()
            if value:
                requested_caps.append(value)
        requested_caps = sorted(set(requested_caps))
        inferred_external = any(
            cap in {
                "external_access",
                "download",
                "install_app",
                "create_account",
                "service_access",
                "obtain_key",
                "obtain_access_key",
                "credential_setup",
                "api_key_management",
            }
            for cap in requested_caps
        )
        if any(
            phrase in issue_lower
            for phrase in (
                "download",
                "install",
                "create account",
                "sign up",
                "api key",
                "access key",
                "credential",
                "oauth",
                "service access",
                "obtain key",
            )
        ):
            inferred_external = True
        requires_external_access = bool(external_access) if external_access is not None else inferred_external
        major_requires_owner = bool(cfg.get("major_change_requires_owner", True))
        external_requires_owner = bool(cfg.get("external_access_requires_owner", True))
        minor_external_access_allowed = bool(cfg.get("minor_external_access_allowed", True))
        external_owner_required = (
            external_requires_owner
            and requires_external_access
            and not (minor_external_access_allowed and normalized_change_impact == "minor")
        )
        owner_required = (
            (major_requires_owner and normalized_change_impact == "major")
            or external_owner_required
        )
        if owner_required and normalized_approval_source != "owner":
            payload = {
                "ok": False,
                "error": "owner_approval_required",
                "approval_required": True,
                "required_approval_source": "owner",
                "issue": objective,
                "reason": reason,
                "project_scope": normalized_project_scope,
                "approval_source": normalized_approval_source,
                "change_impact": normalized_change_impact,
                "requires_external_access": requires_external_access,
                "requested_capabilities": requested_caps,
            }
            self.memory.append_event("codex.self_patch_approval_required", dict(payload))
            self.memory.append_event(
                "codex.self_patch_skipped",
                {
                    "reason": reason,
                    "skip_reason": "owner_approval_required",
                    "project_scope": normalized_project_scope,
                    "approval_source": normalized_approval_source,
                    "change_impact": normalized_change_impact,
                    "requires_external_access": requires_external_access,
                },
            )
            return payload

        governance = {
            "project_scope": normalized_project_scope,
            "approval_source": normalized_approval_source,
            "change_impact": normalized_change_impact,
            "requested_capabilities": requested_caps,
            "requires_external_access": requires_external_access,
            "major_change_requires_owner": major_requires_owner,
            "external_access_requires_owner": external_requires_owner,
            "minor_external_access_allowed": minor_external_access_allowed,
            "weekly_remaining_percent": round(weekly_remaining_value, 4),
            "min_weekly_remaining_percent": round(min_weekly_remaining, 4),
        }
        task_text = (
            "Investigate and patch JARVIS runtime behavior based on this detected issue: "
            f"{objective}. Scope this work to the approved project area ({normalized_project_scope}), "
            "treat minor changes as functionality/capability/trusted-data access improvements for speed, progression, and profit optimization, "
            "make only directly relevant improvements, keep continuity-safe behavior, add/update tests, "
            "and summarize exactly what changed with an audit trail."
        )
        context = {
            "actor": "jarvis_core",
            "self_patch": True,
            "self_patch_reason": str(reason or "auto"),
            "self_patch_issue": objective,
            "self_patch_metrics": dict(metrics or {}),
            "self_patch_governance": governance,
            "self_patch_project_scope": normalized_project_scope,
            "self_patch_approval_source": normalized_approval_source,
            "self_patch_change_impact": normalized_change_impact,
            "self_patch_requested_capabilities": requested_caps,
            "self_patch_requires_external_access": requires_external_access,
            "execution_engine": "codex",
            "effort_tier": str(effort_tier or "pro"),
            "codex_delegate": True,
        }
        submission = self.create_codex_task(
            text=task_text,
            source_surface="system:self_patch",
            session_id="adaptive-auto",
            actor="jarvis_core",
            write_enabled=True,
            auto_execute=auto_execute,
            context=context,
        )
        task = submission.get("task") if isinstance(submission.get("task"), dict) else {}
        self.memory.append_event(
            "codex.self_patch_task_created",
            {
                "task_id": task.get("task_id"),
                "reason": reason,
                "issue": objective,
                "effort_tier": task.get("effort_tier"),
                "auto_execute": bool(task.get("auto_execute")),
                "governance": governance,
            },
        )
        return {
            "ok": True,
            "issue": objective,
            "reason": reason,
            "submission": submission,
            "governance": governance,
        }

    def _maybe_trigger_self_patch_from_calibration(
        self,
        *,
        metrics: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        policy = self.get_adaptive_policy()
        cfg = policy.get("self_patch") if isinstance(policy.get("self_patch"), dict) else {}
        if not bool(cfg.get("enabled", True)):
            return {"ok": True, "triggered": False, "skip_reason": "self_patch_disabled"}

        cooldown_minutes = max(1, int(cfg.get("cooldown_minutes") or 30))
        max_open_tasks = max(0, int(cfg.get("max_open_tasks") or 2))
        min_voice_turns = max(0, int(cfg.get("min_voice_turns") or 6))
        min_continuity_failure_rate = float(cfg.get("min_continuity_failure_rate") or 0.3)
        min_mode_accuracy = float(cfg.get("min_mode_accuracy") or 0.55)
        min_codex_tasks = max(0, int(cfg.get("min_codex_tasks") or 6))
        min_codex_fail_rate = float(cfg.get("min_codex_fail_rate") or 0.45)
        min_interrupted_turns = max(0, int(cfg.get("min_interrupted_turns") or 4))
        min_interruption_recovery_rate = float(cfg.get("min_interruption_recovery_rate") or 0.5)
        min_reviews = max(0, int(cfg.get("min_reviews") or 5))
        min_negative_review_rate = float(cfg.get("min_negative_review_rate") or 0.6)

        open_tasks = self._self_patch_open_task_count(limit=300)
        if open_tasks >= max_open_tasks:
            self.memory.append_event(
                "codex.self_patch_skipped",
                {
                    "reason": reason,
                    "skip_reason": "max_open_tasks",
                    "open_tasks": open_tasks,
                },
            )
            return {"ok": True, "triggered": False, "skip_reason": "max_open_tasks", "open_tasks": open_tasks}

        recent = self.memory.list_events(limit=1, event_type="codex.self_patch_task_created")
        if recent:
            last_dt = self._parse_iso_utc((recent[0] or {}).get("created_at"))
            if last_dt is not None:
                elapsed_minutes = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0
                if elapsed_minutes < float(cooldown_minutes):
                    self.memory.append_event(
                        "codex.self_patch_skipped",
                        {
                            "reason": reason,
                            "skip_reason": "cooldown",
                            "elapsed_minutes": round(elapsed_minutes, 4),
                            "cooldown_minutes": cooldown_minutes,
                        },
                    )
                    return {
                        "ok": True,
                        "triggered": False,
                        "skip_reason": "cooldown",
                        "elapsed_minutes": round(elapsed_minutes, 4),
                    }

        continuity_failure_rate = float(metrics.get("continuity_failure_rate") or 0.0)
        mode_accuracy = metrics.get("mode_accuracy")
        mode_accuracy_value = (
            float(mode_accuracy) if isinstance(mode_accuracy, (int, float)) else None
        )
        codex_fail_rate = float(metrics.get("codex_fail_rate") or 0.0)
        interruption_recovery_rate = metrics.get("interruption_recovery_rate")
        interruption_recovery_value = (
            float(interruption_recovery_rate) if isinstance(interruption_recovery_rate, (int, float)) else None
        )
        negative_review_rate = float(metrics.get("negative_review_rate") or 0.0)
        turn_count = int(metrics.get("voice_turn_count") or 0)
        mode_scored_turns = int(metrics.get("mode_scored_turns") or 0)
        interrupted_turns = int(metrics.get("interrupted_turns") or 0)
        codex_total = int(metrics.get("codex_total") or 0)
        review_total = int(metrics.get("review_total") or 0)

        issue = ""
        effort_tier = "pro"
        if turn_count >= min_voice_turns and continuity_failure_rate >= min_continuity_failure_rate:
            issue = (
                f"High continuity failure rate ({continuity_failure_rate:.3f}) across {turn_count} turns; "
                "investigate continuity/session binding drift and patch."
            )
            effort_tier = "extended_thinking"
        elif mode_scored_turns >= min_voice_turns and mode_accuracy_value is not None and mode_accuracy_value <= min_mode_accuracy:
            issue = (
                f"Low relationship mode accuracy ({mode_accuracy_value:.3f}) over {mode_scored_turns} scored turns; "
                "patch mode selection logic and tests."
            )
            effort_tier = "extended_thinking"
        elif codex_total >= min_codex_tasks and codex_fail_rate >= min_codex_fail_rate:
            issue = (
                f"High Codex task failure rate ({codex_fail_rate:.3f}) across {codex_total} tasks; "
                "patch delegation/retry/execution stability."
            )
            effort_tier = "pro"
        elif interrupted_turns >= min_interrupted_turns and interruption_recovery_value is not None and interruption_recovery_value <= min_interruption_recovery_rate:
            issue = (
                f"Low interruption recovery rate ({interruption_recovery_value:.3f}) across {interrupted_turns} interrupted turns; "
                "patch voice interruption continuity path."
            )
            effort_tier = "extended_thinking"
        elif review_total >= min_reviews and negative_review_rate >= min_negative_review_rate:
            issue = (
                f"High negative pushback review rate ({negative_review_rate:.3f}) across {review_total} reviews; "
                "patch pushback calibration behavior."
            )
            effort_tier = "pro"

        if not issue:
            self.memory.append_event(
                "codex.self_patch_skipped",
                {
                    "reason": reason,
                    "skip_reason": "no_trigger",
                    "metrics": dict(metrics),
                },
            )
            return {"ok": True, "triggered": False, "skip_reason": "no_trigger"}

        submission = self.trigger_self_patch_task(
            issue=issue,
            reason=reason,
            effort_tier=effort_tier,
            auto_execute=bool(cfg.get("auto_execute", True)),
            metrics=metrics,
            project_scope=str(cfg.get("default_project") or "jarvis"),
            approval_source=str(cfg.get("default_auto_approval_source") or "codex"),
            change_impact="minor",
            requested_capabilities=["code_patch", "tests"],
            external_access=False,
            weekly_remaining_percent=(
                float(cfg.get("weekly_remaining_percent"))
                if isinstance(cfg.get("weekly_remaining_percent"), (int, float))
                else None
            ),
        )
        if not bool(submission.get("ok")):
            return {
                "ok": True,
                "triggered": False,
                "skip_reason": str(submission.get("error") or "self_patch_blocked"),
                "submission": submission,
            }
        return {
            "ok": True,
            "triggered": True,
            "issue": issue,
            "effort_tier": effort_tier,
            "submission": submission,
        }

    def _adaptive_context(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(context or {})
        policy = self.get_adaptive_policy()
        metadata = policy.get("metadata") if isinstance(policy.get("metadata"), dict) else {}
        if merged.get("adaptive_policy_revision") is None and metadata.get("revision") is not None:
            merged["adaptive_policy_revision"] = metadata.get("revision")
        routing = policy.get("routing") if isinstance(policy.get("routing"), dict) else {}
        tiering = policy.get("tiering") if isinstance(policy.get("tiering"), dict) else {}
        if merged.get("route_codex_bias") is None and routing.get("codex_bias") is not None:
            merged["route_codex_bias"] = routing.get("codex_bias")
        if merged.get("route_gpt_bias") is None and routing.get("gpt_bias") is not None:
            merged["route_gpt_bias"] = routing.get("gpt_bias")
        if merged.get("route_delegate_score_threshold") is None and routing.get("delegate_score_threshold") is not None:
            merged["route_delegate_score_threshold"] = routing.get("delegate_score_threshold")
        if merged.get("route_app_scope_weight") is None and routing.get("app_scope_weight") is not None:
            merged["route_app_scope_weight"] = routing.get("app_scope_weight")
        if merged.get("route_write_signal_weight") is None and routing.get("write_signal_weight") is not None:
            merged["route_write_signal_weight"] = routing.get("write_signal_weight")
        if merged.get("route_read_signal_weight") is None and routing.get("read_signal_weight") is not None:
            merged["route_read_signal_weight"] = routing.get("read_signal_weight")
        if merged.get("routing_query_forces_gpt") is None and routing.get("routing_query_forces_gpt") is not None:
            merged["routing_query_forces_gpt"] = routing.get("routing_query_forces_gpt")

        if merged.get("tier_instant_max_words") is None and tiering.get("instant_max_words") is not None:
            merged["tier_instant_max_words"] = tiering.get("instant_max_words")
        if merged.get("tier_pro_min_words") is None and tiering.get("pro_min_words") is not None:
            merged["tier_pro_min_words"] = tiering.get("pro_min_words")
        if merged.get("tier_extended_min_words") is None and tiering.get("extended_min_words") is not None:
            merged["tier_extended_min_words"] = tiering.get("extended_min_words")
        if merged.get("tier_deep_research_min_words") is None and tiering.get("deep_research_min_words") is not None:
            merged["tier_deep_research_min_words"] = tiering.get("deep_research_min_words")
        return merged

    def run_adaptive_calibration(
        self,
        *,
        reason: str = "manual",
        apply: bool = True,
    ) -> dict[str, Any]:
        policy = self.get_adaptive_policy()
        tone_summary = self.get_presence_tone_balance(limit=200)
        voice_report = self.get_voice_continuity_soak_report(limit=300)
        pushback_recent = self.list_pushback_calibration(limit=200)
        codex_summary = self.codex_delegation.summarize(limit=200)

        by_hint = tone_summary.get("by_calibration_hint") if isinstance(tone_summary.get("by_calibration_hint"), dict) else {}
        axes = voice_report.get("axes") if isinstance(voice_report.get("axes"), dict) else {}
        continuity_axis = axes.get("continuity") if isinstance(axes.get("continuity"), dict) else {}
        mode_axis = axes.get("mode_accuracy") if isinstance(axes.get("mode_accuracy"), dict) else {}
        pushback_axis = axes.get("pushback") if isinstance(axes.get("pushback"), dict) else {}
        interruption_axis = axes.get("interruption_recovery") if isinstance(axes.get("interruption_recovery"), dict) else {}

        continuity_failure_rate = float(continuity_axis.get("continuity_failure_rate") or 0.0)
        mode_accuracy = mode_axis.get("accuracy")
        try:
            mode_accuracy_value = None if mode_accuracy is None else float(mode_accuracy)
        except (TypeError, ValueError):
            mode_accuracy_value = None
        interruption_recovery_rate = interruption_axis.get("recovery_rate")
        try:
            interruption_recovery_value = (
                None if interruption_recovery_rate is None else float(interruption_recovery_rate)
            )
        except (TypeError, ValueError):
            interruption_recovery_value = None

        reviews = pushback_recent.get("reviews") if isinstance(pushback_recent.get("reviews"), list) else []
        review_outcomes: dict[str, int] = {}
        for item in reviews:
            key = str((item or {}).get("outcome") or "unknown").strip().lower() or "unknown"
            review_outcomes[key] = review_outcomes.get(key, 0) + 1
        total_reviews = sum(review_outcomes.values())
        negative_reviews = (
            review_outcomes.get("negative", 0)
            + review_outcomes.get("regressed", 0)
            + review_outcomes.get("harmful", 0)
        )
        negative_review_rate = (float(negative_reviews) / float(total_reviews)) if total_reviews else 0.0

        by_status = codex_summary.get("by_status") if isinstance(codex_summary.get("by_status"), dict) else {}
        codex_total = int(codex_summary.get("total") or 0)
        codex_failed = int(by_status.get("failed") or 0)
        codex_completed = int(by_status.get("completed") or 0)
        codex_fail_rate = (float(codex_failed) / float(codex_total)) if codex_total else 0.0
        codex_completion_rate = (float(codex_completed) / float(codex_total)) if codex_total else 0.0

        patch: dict[str, Any] = {
            "tone": {},
            "relationship_mode": {},
            "routing": {},
            "pushback": {},
            "metadata": {},
        }

        def _bump(section: str, key: str, delta: float) -> None:
            section_map = patch.get(section) if isinstance(patch.get(section), dict) else {}
            baseline = section_map.get(key)
            if baseline is None:
                baseline = ((policy.get(section) or {}) if isinstance(policy.get(section), dict) else {}).get(key, 0.0)
            try:
                section_map[key] = float(baseline) + float(delta)
            except (TypeError, ValueError):
                section_map[key] = float(delta)
            patch[section] = section_map

        if int(by_hint.get("increase_warmth_before_pushback") or 0) > 0:
            _bump("tone", "warmth_bias", 0.03)
            _bump("tone", "challenge_bias", -0.02)
        if int(by_hint.get("increase_challenge_depth") or 0) > 0:
            _bump("tone", "challenge_bias", 0.025)
        if int(by_hint.get("decompress_and_humanize") or 0) > 0:
            _bump("tone", "compression_bias", -0.03)
            _bump("tone", "warmth_bias", 0.015)
        if int(by_hint.get("slow_down_and_ground") or 0) > 0:
            _bump("tone", "calmness_bias", 0.03)
            _bump("tone", "compression_bias", -0.015)

        if continuity_failure_rate >= 0.25:
            _bump("relationship_mode", "uncertainty_strategist_threshold", 0.04)
            _bump("tone", "calmness_bias", 0.02)

        if mode_accuracy_value is not None and mode_accuracy_value < 0.6:
            _bump("relationship_mode", "uncertainty_strategist_threshold", 0.03)
        elif mode_accuracy_value is not None and mode_accuracy_value > 0.85:
            _bump("relationship_mode", "uncertainty_strategist_threshold", -0.015)

        if interruption_recovery_value is not None and interruption_recovery_value < 0.55:
            _bump("tone", "compression_bias", -0.02)

        if negative_review_rate > 0.55:
            _bump("pushback", "severity_bias", -0.06)
        elif total_reviews >= 4 and negative_review_rate < 0.2:
            _bump("pushback", "severity_bias", 0.03)

        if codex_total >= 5 and codex_fail_rate > 0.35:
            _bump("routing", "codex_bias", -0.12)
            _bump("routing", "gpt_bias", 0.08)
        elif codex_total >= 5 and codex_fail_rate < 0.12 and codex_completion_rate >= 0.7:
            _bump("routing", "codex_bias", 0.05)
            _bump("routing", "gpt_bias", -0.03)

        calibration_runs = int((((policy.get("metadata") or {}).get("calibration_runs")) or 0)) + 1
        patch["metadata"]["calibration_runs"] = calibration_runs

        cleaned_patch = {k: v for k, v in patch.items() if isinstance(v, dict) and v}
        metrics = {
            "continuity_failure_rate": round(continuity_failure_rate, 4),
            "continuity_failures": int(continuity_axis.get("continuity_failures") or 0),
            "voice_turn_count": int(voice_report.get("turn_count") or 0),
            "mode_scored_turns": int(mode_axis.get("scored_turns") or 0),
            "mode_accuracy": (round(mode_accuracy_value, 4) if isinstance(mode_accuracy_value, float) else None),
            "interrupted_turns": int(interruption_axis.get("interrupted_turns") or 0),
            "interruption_recovery_rate": (
                round(interruption_recovery_value, 4) if isinstance(interruption_recovery_value, float) else None
            ),
            "negative_review_rate": round(negative_review_rate, 4),
            "review_total": int(total_reviews),
            "codex_fail_rate": round(codex_fail_rate, 4),
            "codex_completion_rate": round(codex_completion_rate, 4),
            "codex_total": int(codex_total),
            "codex_failed": int(codex_failed),
            "codex_completed": int(codex_completed),
            "tone_hints": by_hint,
            "pushback_outcomes": pushback_axis.get("outcomes") if isinstance(pushback_axis.get("outcomes"), dict) else {},
        }
        if not apply:
            return {
                "ok": True,
                "applied": False,
                "reason": reason,
                "policy_patch": cleaned_patch,
                "metrics": metrics,
                "policy_revision_before": self.get_adaptive_policy_revision(),
            }

        updated = self.update_adaptive_policy(
            patch=cleaned_patch,
            reason=f"adaptive_calibration:{reason}",
            metrics=metrics,
        )
        self_patch = self._maybe_trigger_self_patch_from_calibration(
            metrics=metrics,
            reason=f"adaptive_calibration:{reason}",
        )
        return {
            "ok": True,
            "applied": True,
            "reason": reason,
            "policy_patch": cleaned_patch,
            "metrics": metrics,
            "self_patch": self_patch,
            "policy_revision_after": ((updated.get("policy") or {}).get("metadata") or {}).get("revision"),
            "policy": updated.get("policy"),
        }

    def _maybe_auto_apply_adaptive_calibration(self) -> None:
        self._adaptive_turn_counter += 1
        policy = self.get_adaptive_policy()
        runtime_cfg = policy.get("runtime") if isinstance(policy.get("runtime"), dict) else {}
        if not bool(runtime_cfg.get("auto_calibration_enabled", True)):
            return
        every = max(5, int(runtime_cfg.get("auto_calibration_every_turns") or 20))
        if self._adaptive_turn_counter % every != 0:
            return
        try:
            self.run_adaptive_calibration(reason=f"auto_turn_{self._adaptive_turn_counter}", apply=True)
        except Exception as exc:  # pragma: no cover - defensive path
            self.memory.append_event(
                "presence.adaptive_calibration_error",
                {
                    "turn_counter": self._adaptive_turn_counter,
                    "error": str(exc),
                },
            )

    def _should_explain_work_item(
        self,
        *,
        text: str,
        context: dict[str, Any] | None = None,
        intent: dict[str, Any] | None = None,
    ) -> bool:
        context_map = dict(context or {})
        if context_map.get("explain_work_item") is not None:
            value = context_map.get("explain_work_item")
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "on"}
        if context_map.get("explain_routing") is not None:
            value = context_map.get("explain_routing")
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "on"}
        intent_map = dict(intent or {})
        if bool(intent_map.get("routing_query")):
            return True
        normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
        if not normalized:
            return False
        return any(
            phrase in normalized
            for phrase in (
                "codex or gpt",
                "gpt or codex",
                "which engine",
                "what engine",
                "engine route",
                "what tier",
                "which tier",
                "effort tier",
                "classify this",
            )
        )

    def _render_work_item_explanation(
        self,
        *,
        work_item: dict[str, Any] | None,
        intent: dict[str, Any] | None = None,
    ) -> str:
        work_map = dict(work_item or {})
        intent_map = dict(intent or {})
        engine = str(work_map.get("engine_route") or intent_map.get("engine_route") or "gpt").strip().lower()
        route_reason = str(work_map.get("route_reason") or intent_map.get("route_reason") or "default_gpt").strip()
        tier = str(work_map.get("effort_label") or work_map.get("effort_tier") or "thinking").strip()
        reasoning_effort = str(
            work_map.get("reasoning_effort")
            or intent_map.get("reasoning_effort")
            or "medium"
        ).strip().lower()
        delegate = bool(intent_map.get("should_delegate"))
        if delegate:
            action = "Codex delegation: enabled for this request."
        else:
            action = "Codex delegation: not needed for this request."
        return (
            f"Routing: {engine.upper()} | Tier: {tier} | Reasoning: {reasoning_effort} "
            f"(reason: {route_reason}). {action}"
        )

    @staticmethod
    def _reply_has_tradeoff_and_next_move(text: str) -> tuple[bool, bool]:
        lowered = str(text or "").strip().lower()
        has_tradeoff = any(token in lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost"))
        has_next_move = any(
            token in lowered
            for token in (
                "next move",
                "next step",
                "we should",
                "i recommend",
                "first move",
                "first step",
            )
        )
        return has_tradeoff, has_next_move

    def _enforce_final_reply_contract(
        self,
        *,
        prepared: dict[str, Any],
        draft_text: str,
        modality: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        diagnostics = (
            dict(prepared.get("reply_diagnostics") or {})
            if isinstance(prepared.get("reply_diagnostics"), dict)
            else {}
        )
        inferred_signals = (
            dict(prepared.get("inferred_signals") or {})
            if isinstance(prepared.get("inferred_signals"), dict)
            else {}
        )
        normalized_text = self._normalize_dialogue_text(draft_text)
        route_reason = str(diagnostics.get("route_reason") or "").strip() or "unknown"
        answer_source = str(diagnostics.get("answer_source") or "").strip() or "unknown"
        fallback_used = bool(diagnostics.get("fallback_used"))
        fallback_reason = str(diagnostics.get("fallback_reason") or "").strip() or None
        retrieval_selected_count = max(0, int(diagnostics.get("retrieval_selected_count") or 0))
        retrieval_bucket_counts = (
            dict(diagnostics.get("retrieval_bucket_counts") or {})
            if isinstance(diagnostics.get("retrieval_bucket_counts"), dict)
            else {}
        )
        partner_subfamily = str(
            diagnostics.get("partner_subfamily")
            or self._partner_turn_subfamily(user_text=draft_text)
            or "general"
        ).strip().lower() or "general"
        inferred_high_stakes = bool(inferred_signals.get("high_stakes"))
        inferred_uncertainty = 0.0
        try:
            inferred_uncertainty = float(inferred_signals.get("uncertainty") or 0.0)
        except (TypeError, ValueError):
            inferred_uncertainty = 0.0
        partner_depth_lane = str(
            diagnostics.get("partner_depth_lane")
            or self._partner_depth_lane(
                user_text=draft_text,
                partner_subfamily=partner_subfamily,
                high_stakes=inferred_high_stakes,
                uncertainty=inferred_uncertainty,
                context={
                    "requires_pushback": bool(inferred_signals.get("requires_pushback")),
                },
            )
            or "partner_fast"
        ).strip().lower() or "partner_fast"
        high_risk_input = self._is_high_risk_self_harm_turn(user_text=draft_text)
        high_risk_guardrail = bool(diagnostics.get("high_risk_guardrail")) or high_risk_input
        if "partner_lane_used" in diagnostics:
            partner_turn = bool(diagnostics.get("partner_lane_used"))
        else:
            partner_turn = self._is_partner_dialogue_turn(user_text=draft_text)
        partner_min_snippets, partner_target_snippets = self._partner_retrieval_targets(
            partner_depth_lane=partner_depth_lane if partner_turn else None
        )
        contract_gate_passed = True
        contract_gate_reasons: list[str] = []

        if high_risk_guardrail:
            prepared["reply_text"] = self._self_harm_support_reply()
            route_reason = "high_risk_guardrail"
            answer_source = "high_risk_guardrail"
            fallback_used = False
            fallback_reason = None
            contract_gate_reasons.append("high_risk_terminal")
        else:
            high_stakes = inferred_high_stakes
            uncertainty = 0.0
            try:
                uncertainty = float(inferred_signals.get("uncertainty") or 0.0)
            except (TypeError, ValueError):
                uncertainty = 0.0
            explicit_status_turn = self._is_status_priority_turn(
                user_text=draft_text,
                high_stakes=high_stakes,
                uncertainty=uncertainty,
            )
            if explicit_status_turn:
                reply_text = str(prepared.get("reply_text") or "").strip()
                has_tradeoff, has_next_move = self._reply_has_tradeoff_and_next_move(reply_text)
                cached_brief_declared = reply_text.lower().startswith("current read:")
                if retrieval_selected_count <= 0 and not cached_brief_declared:
                    prepared["reply_text"] = self._with_cached_brief_notice(reply_text)
                    reply_text = str(prepared.get("reply_text") or "").strip()
                    contract_gate_reasons.append("cached_brief_declared")
                    answer_source = "cached_brief"
                if not (has_tradeoff and has_next_move):
                    live_briefs = self.get_live_briefs()
                    stateful = str(live_briefs.get("top_two_priorities") or "").strip()
                    if not stateful:
                        stateful = self._stateful_status_reply(None)
                    prepared["reply_text"] = self._with_cached_brief_notice(stateful)
                    route_reason = "status_contract_enforced"
                    answer_source = "cached_brief"
                    fallback_used = True
                    fallback_reason = "status_contract_enforced"
                    contract_gate_reasons.append("status_contract_enforced_rewrite")
            if partner_turn:
                reply_text = str(prepared.get("reply_text") or "").strip()
                limited_context_declared = reply_text.lower().startswith("i am operating from limited state context")
                if retrieval_selected_count <= 0 and not limited_context_declared:
                    prepared["reply_text"] = self._with_limited_state_notice(reply_text)
                    route_reason = "partner_context_required"
                    answer_source = "limited_state_context"
                    contract_gate_passed = False
                    contract_gate_reasons.append("partner_missing_retrieval")
                if retrieval_selected_count < partner_min_snippets:
                    contract_gate_passed = False
                    if "partner_retrieval_floor" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_retrieval_floor")
                if retrieval_selected_count < partner_target_snippets and "partner_retrieval_target_miss" not in contract_gate_reasons:
                    contract_gate_reasons.append("partner_retrieval_target_miss")
                if str(answer_source or "").strip() != "model":
                    contract_gate_passed = False
                    if "partner_requires_model_path" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_requires_model_path")
                if str(answer_source or "").strip() in {"cached_brief", "status_fallback"}:
                    route_reason = "partner_context_required"
                    answer_source = "limited_state_context"
                    contract_gate_passed = False
                    if "partner_cached_brief_blocked" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_cached_brief_blocked")
                mix_ok = self._partner_context_mix_ok(
                    retrieval_bucket_counts,
                    partner_depth_lane=partner_depth_lane,
                )
                if not mix_ok:
                    contract_gate_passed = False
                    if "partner_context_mix_missing" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_context_mix_missing")
                partner_lowered = self._normalize_dialogue_text(str(prepared.get("reply_text") or ""))
                has_tradeoff = any(token in partner_lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost"))
                has_why_now = any(
                    token in partner_lowered
                    for token in (
                        "right now",
                        "today",
                        "this hour",
                        "window",
                        "timing",
                        "deadline",
                        "before",
                        "now",
                    )
                )
                if partner_depth_lane == "partner_deep" and partner_subfamily in {"tradeoff", "strategic", "truth"} and not has_tradeoff:
                    enforced_tradeoff = self._derive_status_tradeoff(None)
                    reply_text = str(prepared.get("reply_text") or "").strip()
                    if reply_text:
                        prepared["reply_text"] = f"{reply_text} Tradeoff: {enforced_tradeoff}."
                    else:
                        prepared["reply_text"] = f"Tradeoff: {enforced_tradeoff}."
                    partner_lowered = self._normalize_dialogue_text(str(prepared.get("reply_text") or ""))
                    has_tradeoff = any(token in partner_lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost"))
                    contract_gate_reasons.append("partner_tradeoff_enforced")
                if partner_depth_lane == "partner_deep" and not has_why_now:
                    reply_text = str(prepared.get("reply_text") or "").strip()
                    why_now_suffix = "Why now: timing and opportunity cost make this decision active in this cycle."
                    if reply_text:
                        prepared["reply_text"] = f"{reply_text} {why_now_suffix}"
                    else:
                        prepared["reply_text"] = why_now_suffix
                    partner_lowered = self._normalize_dialogue_text(str(prepared.get("reply_text") or ""))
                    has_why_now = any(
                        token in partner_lowered
                        for token in ("right now", "today", "this hour", "window", "timing", "deadline", "before", "now")
                    )
                    contract_gate_reasons.append("partner_why_now_enforced")
                has_pushback = any(
                    token in partner_lowered
                    for token in ("push back", "risk", "safer", "alternative", "do not", "dont", "i disagree")
                )
                has_uncertainty = any(
                    token in partner_lowered
                    for token in (
                        "uncertain",
                        "hypothesis",
                        "confidence",
                        "might",
                        "likely",
                        "current read",
                        "no dominant",
                        "if you want",
                        "yet",
                    )
                )
                if not (has_tradeoff or has_pushback or has_uncertainty):
                    contract_gate_passed = False
                    if "partner_reasoning_signal_missing" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_reasoning_signal_missing")
                if partner_subfamily in {"tradeoff", "strategic"} and not has_tradeoff:
                    contract_gate_passed = False
                    if "partner_tradeoff_missing" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_tradeoff_missing")
                if partner_subfamily in {"pushback", "truth"} and not has_pushback:
                    contract_gate_passed = False
                    if "partner_pushback_missing" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_pushback_missing")
                if partner_subfamily == "reflection" and not has_uncertainty:
                    contract_gate_passed = False
                    if "partner_reflection_uncertainty_missing" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_reflection_uncertainty_missing")
                if partner_depth_lane == "partner_deep" and not has_why_now:
                    contract_gate_passed = False
                    if "partner_why_now_missing" not in contract_gate_reasons:
                        contract_gate_reasons.append("partner_why_now_missing")

        final_reply_text = str(prepared.get("reply_text") or "").strip()
        cached_brief_declared_final = final_reply_text.lower().startswith("current read:")
        if cached_brief_declared_final and retrieval_selected_count <= 0 and answer_source in {"", "unknown", "model"}:
            answer_source = "cached_brief"
        final_lowered = self._normalize_dialogue_text(final_reply_text)
        used_tradeoff_frame = any(
            token in final_lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost")
        )
        used_why_now_frame = any(
            token in final_lowered
            for token in ("right now", "today", "this hour", "window", "timing", "deadline", "before", "now")
        )

        diagnostics.setdefault("partner_lane_used", bool(partner_turn if not high_risk_guardrail else False))
        diagnostics.setdefault("identity_capsule_hash", None)
        diagnostics.setdefault("identity_capsule_used", False)
        diagnostics["route_reason"] = route_reason
        diagnostics["answer_source"] = answer_source
        diagnostics["fallback_used"] = fallback_used
        diagnostics["fallback_reason"] = fallback_reason if fallback_used else None
        diagnostics["high_risk_guardrail"] = high_risk_guardrail
        diagnostics["retrieval_selected_count"] = retrieval_selected_count
        diagnostics["retrieval_bucket_counts"] = retrieval_bucket_counts
        diagnostics["partner_context_mix_ok"] = self._partner_context_mix_ok(
            retrieval_bucket_counts,
            partner_depth_lane=partner_depth_lane if partner_turn else None,
        )
        diagnostics["partner_retrieval_target"] = partner_target_snippets if partner_turn else None
        diagnostics["partner_subfamily"] = partner_subfamily if partner_turn else None
        diagnostics["partner_depth_lane"] = partner_depth_lane if partner_turn else None
        diagnostics["deep_lane_required"] = bool(partner_turn and partner_depth_lane == "partner_deep")
        diagnostics["used_tradeoff_frame"] = bool(used_tradeoff_frame)
        diagnostics["used_why_now_frame"] = bool(used_why_now_frame)
        diagnostics["contract_gate_passed"] = bool(contract_gate_passed)
        diagnostics["cached_brief_used"] = bool(
            str(answer_source).strip() == "cached_brief" or cached_brief_declared_final
        )
        diagnostics["response_family"] = self._classify_response_family(
            route_reason=route_reason,
            answer_source=answer_source,
            fallback_used=bool(fallback_used),
            high_risk_guardrail=bool(high_risk_guardrail),
            partner_lane_used=bool(diagnostics.get("partner_lane_used")),
            partner_depth_lane=(partner_depth_lane if partner_turn else None),
        )
        prepared["reply_diagnostics"] = diagnostics
        prepared["contract_gate_passed"] = bool(contract_gate_passed)
        prepared["contract_gate_reasons"] = contract_gate_reasons
        prepared["high_risk_guardrail"] = bool(high_risk_guardrail)
        prepared["reply_modality"] = str(modality or "text").strip().lower() or "text"
        prepared["transcript"] = str(draft_text or "")
        return prepared, diagnostics

    def _audit_outbound_reply(
        self,
        *,
        prepared: dict[str, Any],
        endpoint_name: str,
        surface: str,
        transcript: str,
    ) -> dict[str, Any]:
        diagnostics = (
            dict(prepared.get("reply_diagnostics") or {})
            if isinstance(prepared.get("reply_diagnostics"), dict)
            else {}
        )
        inferred_signals = (
            dict(prepared.get("inferred_signals") or {})
            if isinstance(prepared.get("inferred_signals"), dict)
            else {}
        )
        continuity = (
            dict(prepared.get("continuity") or {})
            if isinstance(prepared.get("continuity"), dict)
            else {}
        )
        audit_record = {
            "boot_id": str(self.boot_id),
            "reply_policy_hash": self.get_reply_policy_hash(),
            "surface": str(surface or "").strip() or "unknown",
            "endpoint_name": str(endpoint_name or "").strip() or "unknown",
            "transcript": str(transcript or ""),
            "inferred_signals": inferred_signals,
            "route_reason": str(diagnostics.get("route_reason") or "").strip() or None,
            "answer_source": str(diagnostics.get("answer_source") or "").strip() or None,
            "fallback_used": bool(diagnostics.get("fallback_used")),
            "fallback_reason": (
                str(diagnostics.get("fallback_reason") or "").strip() or None
                if bool(diagnostics.get("fallback_used"))
                else None
            ),
            "high_risk_guardrail": bool(
                diagnostics.get("high_risk_guardrail") or prepared.get("high_risk_guardrail")
            ),
            "retrieval_selected_count": max(0, int(diagnostics.get("retrieval_selected_count") or 0)),
            "cached_brief_used": bool(diagnostics.get("cached_brief_used")),
            "partner_lane_used": bool(diagnostics.get("partner_lane_used")),
            "partner_subfamily": (
                str(diagnostics.get("partner_subfamily") or "").strip().lower() or None
            ),
            "partner_depth_lane": (
                str(diagnostics.get("partner_depth_lane") or "").strip().lower() or None
            ),
            "deep_lane_invoked": bool(diagnostics.get("deep_lane_invoked")),
            "deep_model_name": (
                str(diagnostics.get("deep_model_name") or "").strip() or None
            ),
            "identity_capsule_hash": (
                str(diagnostics.get("identity_capsule_hash") or "").strip() or None
            ),
            "identity_capsule_used": bool(diagnostics.get("identity_capsule_used")),
            "retrieval_bucket_counts": (
                dict(diagnostics.get("retrieval_bucket_counts") or {})
                if isinstance(diagnostics.get("retrieval_bucket_counts"), dict)
                else {}
            ),
            "retrieval_bucket_mix": (
                dict(diagnostics.get("retrieval_bucket_mix") or {})
                if isinstance(diagnostics.get("retrieval_bucket_mix"), dict)
                else {}
            ),
            "partner_context_mix_ok": (
                bool(diagnostics.get("partner_context_mix_ok"))
                if diagnostics.get("partner_context_mix_ok") is not None
                else None
            ),
            "used_tradeoff_frame": (
                bool(diagnostics.get("used_tradeoff_frame"))
                if diagnostics.get("used_tradeoff_frame") is not None
                else None
            ),
            "used_why_now_frame": (
                bool(diagnostics.get("used_why_now_frame"))
                if diagnostics.get("used_why_now_frame") is not None
                else None
            ),
            "response_family": str(diagnostics.get("response_family") or "").strip() or None,
            "model_name": str(diagnostics.get("model_name") or "").strip() or None,
            "latency_ms": (
                float(diagnostics.get("latency_ms"))
                if diagnostics.get("latency_ms") is not None
                else None
            ),
            "contract_gate_passed": bool(
                prepared.get("contract_gate_passed", diagnostics.get("contract_gate_passed", True))
            ),
            "continuity_ok": bool(continuity.get("continuity_ok", True)),
            "session_id": (
                str(continuity.get("session_id") or "").strip()
                or str(prepared.get("session_id") or "").strip()
                or None
            ),
            "surface_id": (
                str(continuity.get("surface_id") or "").strip()
                or str(prepared.get("surface_id") or "").strip()
                or None
            ),
        }
        self.memory.append_event("presence.reply_outbound_audit", audit_record)
        return audit_record

    def preview_work_item_route(
        self,
        *,
        text: str,
        context: dict[str, Any] | None = None,
        explicit_directive: bool = False,
    ) -> dict[str, Any]:
        context_map = dict(context or {})
        intent = self.classify_request_intent(
            text=str(text or ""),
            explicit_directive=bool(explicit_directive),
            context=context_map,
        )
        work_item = self.classify_work_item(
            text=str(text or ""),
            context=context_map,
        )
        for key in ("effort_tier", "effort_label", "reasoning_effort"):
            if intent.get(key):
                work_item[key] = intent.get(key)
        work_item["engine_route"] = intent.get("engine_route")
        work_item["route_reason"] = intent.get("route_reason")
        work_item["should_delegate"] = bool(intent.get("should_delegate"))
        return {
            "text": str(text or ""),
            "work_item": work_item,
            "intent": intent,
            "explanation": self._render_work_item_explanation(
                work_item=work_item,
                intent=intent,
            ),
        }

    def _maybe_delegate_codex_from_reply(
        self,
        *,
        draft: ReplyDraft | dict[str, Any],
        prepared: dict[str, Any],
        modality: str,
    ) -> dict[str, Any] | None:
        if isinstance(draft, ReplyDraft):
            text = str(draft.text or "").strip()
            source_surface = str(draft.surface_id or "").strip()
            session_id = str(draft.session_id or "").strip()
            explicit_directive = bool(draft.explicit_directive)
            context = dict(draft.context or {})
        else:
            draft_map = dict(draft or {})
            text = str(draft_map.get("text") or "").strip()
            source_surface = str(draft_map.get("surface_id") or "").strip()
            session_id = str(draft_map.get("session_id") or "").strip()
            explicit_directive = bool(draft_map.get("explicit_directive"))
            context = draft_map.get("context") if isinstance(draft_map.get("context"), dict) else {}
            for hint_key in (
                "effort_tier",
                "tier",
                "reasoning_tier",
                "reasoning_effort",
                "execution_engine",
                "engine",
                "route_engine",
                "codex_delegate",
                "codex_auto_execute",
                "codex_model",
            ):
                if draft_map.get(hint_key) is not None and hint_key not in context:
                    context[hint_key] = draft_map.get(hint_key)

        if not text:
            return None
        if not self._codex_delegation_enabled(context=context):
            return None

        intent = self.classify_request_intent(
            text=text,
            explicit_directive=explicit_directive,
            context=context,
        )
        if not bool(intent.get("should_delegate")):
            return None

        write_enabled = bool(intent.get("write_enabled"))
        submission = self.create_codex_task(
            text=text,
            source_surface=source_surface,
            session_id=session_id,
            actor=str(context.get("actor") or "owner"),
            write_enabled=write_enabled,
            auto_execute=None,
            context=context,
        )
        task = submission.get("task") if isinstance(submission.get("task"), dict) else {}
        execution = submission.get("execution") if isinstance(submission.get("execution"), dict) else {}
        task_id = str(task.get("task_id") or "").strip()
        status = str(execution.get("status") or task.get("status") or "queued")
        mode_label = "read/write" if write_enabled else "read-only"
        effort_label = str(task.get("effort_label") or intent.get("effort_label") or "thinking")
        if status == "running":
            summary = f"Codex task {task_id} ({effort_label}, {mode_label}) is running now."
        elif status == "completed":
            summary = f"Codex task {task_id} ({effort_label}, {mode_label}) completed."
        elif status == "failed":
            summary = f"Codex task {task_id} ({effort_label}, {mode_label}) failed. Check /api/codex/tasks/{task_id}."
        else:
            summary = f"Codex task {task_id} ({effort_label}, {mode_label}) queued."
        return {
            "intent": intent,
            "submission": submission,
            "summary": summary,
            "modality": modality,
            "effort_tier": task.get("effort_tier") or intent.get("effort_tier"),
            "reasoning_effort": task.get("reasoning_effort") or intent.get("reasoning_effort"),
        }

    def prepare_openclaw_reply(self, draft: ReplyDraft | dict[str, Any]) -> dict[str, Any]:
        prepared = self.openclaw_reply_orchestrator.prepare_reply(draft)
        continuity = prepared.get("continuity") if isinstance(prepared.get("continuity"), dict) else {}
        voice = prepared.get("voice") if isinstance(prepared.get("voice"), dict) else {}
        tone_balance = prepared.get("tone_balance") if isinstance(prepared.get("tone_balance"), dict) else {}
        mode_name = str((prepared.get("mode") or {}).get("mode") or "equal").strip().lower() or "equal"
        draft_modality = ""
        if isinstance(draft, ReplyDraft):
            draft_modality = str(draft.modality or "").strip().lower()
        elif isinstance(draft, dict):
            draft_modality = str(draft.get("modality") or "").strip().lower()
        modality = str(voice.get("modality") or draft_modality or "text").strip().lower() or "text"
        draft_context: dict[str, Any] = {}
        draft_text = ""
        draft_explicit_directive = False
        if isinstance(draft, ReplyDraft):
            draft_context = dict(draft.context or {})
            draft_text = str(draft.text or "")
            draft_explicit_directive = bool(draft.explicit_directive)
        elif isinstance(draft, dict):
            draft_context = dict(draft.get("context") or {}) if isinstance(draft.get("context"), dict) else {}
            draft_text = str(draft.get("text") or "")
            draft_explicit_directive = bool(draft.get("explicit_directive"))
            for hint_key in (
                "effort_tier",
                "tier",
                "reasoning_tier",
                "reasoning_effort",
                "execution_engine",
                "engine",
                "route_engine",
                "codex_delegate",
                "codex_auto_execute",
                "codex_model",
                ):
                    if draft.get(hint_key) is not None and hint_key not in draft_context:
                        draft_context[hint_key] = draft.get(hint_key)
        surface_id = (
            str(draft.get("surface_id") or "").strip()
            if isinstance(draft, dict)
            else str(getattr(draft, "surface_id", "") or "").strip()
        )
        session_id = (
            str(draft.get("session_id") or "").strip()
            if isinstance(draft, dict)
            else str(getattr(draft, "session_id", "") or "").strip()
        )
        prepared["surface_id"] = surface_id or str((continuity or {}).get("surface_id") or "").strip() or None
        prepared["session_id"] = session_id or str((continuity or {}).get("session_id") or "").strip() or None
        endpoint_name = str(draft_context.get("_reply_endpoint_name") or "presence.reply.prepare").strip()
        prepared["boot_identity"] = self.get_boot_identity()
        high_risk_input = self._is_high_risk_self_harm_turn(user_text=draft_text)
        work_item = self.classify_work_item(text=draft_text, context=draft_context)
        request_intent = self.classify_request_intent(
            text=draft_text,
            explicit_directive=draft_explicit_directive,
            context=draft_context,
        )
        for key in ("effort_tier", "effort_label", "reasoning_effort"):
            if request_intent.get(key):
                work_item[key] = request_intent.get(key)
        work_item["engine_route"] = request_intent.get("engine_route")
        work_item["route_reason"] = request_intent.get("route_reason")
        work_item["should_delegate"] = bool(request_intent.get("should_delegate"))
        prepared["work_item"] = work_item
        explain_work_item = self._should_explain_work_item(
            text=draft_text,
            context=draft_context,
            intent=request_intent,
        )
        tone_snapshot = None
        if tone_balance:
            tone_snapshot = self.tone_balance.record(
                mode=mode_name,
                modality=modality,
                profile=tone_balance.get("profile") if isinstance(tone_balance.get("profile"), dict) else {},
                imbalances=tone_balance.get("imbalances") if isinstance(tone_balance.get("imbalances"), list) else [],
                calibration_hint=str(tone_balance.get("calibration_hint") or "").strip() or None,
            )
            prepared["tone_balance_snapshot"] = tone_snapshot
        if not high_risk_input:
            codex = self._maybe_delegate_codex_from_reply(
                draft=draft,
                prepared=prepared,
                modality=modality,
            )
            if codex:
                prepared["codex_task"] = codex
                reply_text = str(prepared.get("reply_text") or "").strip()
                codex_line = f"Codex: {codex.get('summary')}"
                prepared["reply_text"] = f"{reply_text}\n\n{codex_line}".strip()
            if explain_work_item:
                explain_line = self._render_work_item_explanation(
                    work_item=work_item,
                    intent=request_intent,
                )
                reply_text = str(prepared.get("reply_text") or "").strip()
                prepared["reply_text"] = f"{reply_text}\n\n{explain_line}".strip()
                prepared["work_item_explanation"] = explain_line
                prepared["work_item_explained"] = True
        prepared, reply_diagnostics = self._enforce_final_reply_contract(
            prepared=prepared,
            draft_text=draft_text,
            modality=modality,
        )
        self.memory.append_event(
            "presence.reply_prepared",
            {
                "boot_id": str(self.boot_id),
                "reply_policy_hash": self.get_reply_policy_hash(),
                "mode": (prepared.get("mode") or {}).get("mode"),
                "has_pushback_record": bool(prepared.get("pushback_record")),
                "continuity_ok": bool(continuity.get("continuity_ok", True)),
                "session_key": continuity.get("session_key"),
                "modality": modality,
                "model_used": bool(
                    ((prepared.get("reply_diagnostics") or {}).get("model_used"))
                    if isinstance(prepared.get("reply_diagnostics"), dict)
                    else False
                ),
                "fallback_used": bool(
                    (reply_diagnostics.get("fallback_used"))
                ),
                "fallback_reason": (
                    reply_diagnostics.get("fallback_reason")
                ),
                "route_reason": (
                    reply_diagnostics.get("route_reason")
                ),
                "retrieval_selected_count": (
                    int((reply_diagnostics.get("retrieval_selected_count") or 0))
                ),
                "rerank_used": bool(
                    (reply_diagnostics.get("rerank_used"))
                ),
                "reply_latency_ms": (
                    float((reply_diagnostics.get("latency_ms") or 0.0))
                ),
                "answer_source": reply_diagnostics.get("answer_source"),
                "response_family": reply_diagnostics.get("response_family"),
                "partner_lane_used": bool(reply_diagnostics.get("partner_lane_used")),
                "partner_depth_lane": (
                    str(reply_diagnostics.get("partner_depth_lane") or "").strip().lower() or None
                ),
                "deep_lane_invoked": bool(reply_diagnostics.get("deep_lane_invoked")),
                "deep_model_name": reply_diagnostics.get("deep_model_name"),
                "identity_capsule_hash": reply_diagnostics.get("identity_capsule_hash"),
                "identity_capsule_used": bool(reply_diagnostics.get("identity_capsule_used")),
                "high_risk_guardrail": bool(reply_diagnostics.get("high_risk_guardrail")),
                "used_tradeoff_frame": (
                    bool(reply_diagnostics.get("used_tradeoff_frame"))
                    if reply_diagnostics.get("used_tradeoff_frame") is not None
                    else None
                ),
                "used_why_now_frame": (
                    bool(reply_diagnostics.get("used_why_now_frame"))
                    if reply_diagnostics.get("used_why_now_frame") is not None
                    else None
                ),
                "contract_gate_passed": bool(prepared.get("contract_gate_passed", True)),
                "tone_calibration_hint": (tone_snapshot or {}).get("calibration_hint"),
                "work_item_tier": (prepared.get("work_item") or {}).get("effort_tier"),
                "engine_route": (prepared.get("work_item") or {}).get("engine_route"),
                "work_item_explained": bool(prepared.get("work_item_explained")),
                "self_inquiry_asked": bool(((prepared.get("self_inquiry") or {}).get("asked"))),
                "self_inquiry_topic": (
                    ((prepared.get("self_inquiry") or {}).get("topic"))
                    if isinstance(prepared.get("self_inquiry"), dict)
                    else None
                ),
                "pondering_mode_enabled": bool(
                    ((prepared.get("pondering_mode") or {}).get("enabled"))
                    if isinstance(prepared.get("pondering_mode"), dict)
                    else False
                ),
                "pondering_mode_style": (
                    ((prepared.get("pondering_mode") or {}).get("style"))
                    if isinstance(prepared.get("pondering_mode"), dict)
                    else None
                ),
                "codex_task_id": (
                    (
                        (
                            ((prepared.get("codex_task") or {}).get("submission") or {}).get("task")
                            or {}
                        ).get("task_id")
                    )
                    if isinstance(prepared.get("codex_task"), dict)
                    else None
                ),
            },
        )
        prepared["outbound_audit"] = self._audit_outbound_reply(
            prepared=prepared,
            endpoint_name=endpoint_name or "presence.reply.prepare",
            surface=str(prepared.get("surface_id") or surface_id or modality or "text"),
            transcript=draft_text,
        )
        self._maybe_auto_apply_adaptive_calibration()
        return prepared

    @staticmethod
    def _normalize_dialogue_text(value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9\s']", "", str(value or "").lower()).replace("'", " ")
        return " ".join(cleaned.split())

    def _looks_like_parrot_reply(self, *, user_text: str, reply_text: str) -> bool:
        user_norm = self._normalize_dialogue_text(user_text)
        reply_norm = self._normalize_dialogue_text(reply_text)
        if not user_norm or not reply_norm:
            return False
        if user_norm == reply_norm:
            return True
        ratio = difflib.SequenceMatcher(a=user_norm, b=reply_norm).ratio()
        if len(user_norm.split()) >= 3 and ratio >= 0.9:
            return True
        return False

    def _presence_status_snapshot(self, dialogue_context: dict[str, Any] | None) -> str:
        context = dict(dialogue_context or {})
        segments: list[str] = []

        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        pending_count = int(operations.get("pending_interrupt_count") or 0)
        if pending_count > 0:
            segments.append(f"{pending_count} pending interrupt signal{'s' if pending_count != 1 else ''}")

        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        academic_risks = academics.get("top_risks") if isinstance(academics.get("top_risks"), list) else []
        if academic_risks:
            first = academic_risks[0] if isinstance(academic_risks[0], dict) else {}
            reason = str(first.get("reason") or first.get("risk_key") or "an academics risk").strip()
            if reason:
                segments.append(f"academics risk: {reason}")

        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        market_risks = markets.get("top_risks") if isinstance(markets.get("top_risks"), list) else []
        if market_risks:
            first = market_risks[0] if isinstance(market_risks[0], dict) else {}
            reason = str(first.get("reason") or first.get("risk_key") or "a markets risk").strip()
            if reason:
                segments.append(f"markets risk: {reason}")

        opportunities = markets.get("top_opportunities") if isinstance(markets.get("top_opportunities"), list) else []
        if opportunities:
            first_op = opportunities[0] if isinstance(opportunities[0], dict) else {}
            symbol = str(first_op.get("symbol") or first_op.get("market") or "").strip()
            if symbol:
                segments.append(f"live opportunity watch: {symbol}")

        if not segments:
            return "I am online, tracking continuity, and ready to move the highest-leverage next step."
        return "Right now I am tracking " + "; ".join(segments[:3]) + "."

    def _stateful_status_reply(self, dialogue_context: dict[str, Any] | None) -> str:
        context = dict(dialogue_context or {})
        updates = self._extract_live_state_items(context)
        tradeoff = self._derive_status_tradeoff(context)
        next_move = self._derive_next_move(context)
        if not updates:
            return (
                "No dominant risk spike yet from the current read. "
                "If you want, I can run a fresh cross-domain scan now and return the top tradeoff."
            )
        return (
            "Two live things matter: "
            + "; ".join(updates[:3])
            + f". Tradeoff to watch: {tradeoff}. Next move: {next_move}."
        )

    def _extract_live_state_items(self, dialogue_context: dict[str, Any] | None) -> list[str]:
        context = dict(dialogue_context or {})
        updates: list[str] = []
        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}

        pending_count = int(operations.get("pending_interrupt_count") or 0)
        if pending_count > 0:
            updates.append(f"{pending_count} pending interrupt signal{'s' if pending_count != 1 else ''}")

        academic_risks = academics.get("top_risks") if isinstance(academics.get("top_risks"), list) else []
        if academic_risks:
            first = academic_risks[0] if isinstance(academic_risks[0], dict) else {}
            reason = str(first.get("reason") or first.get("risk_key") or "").strip()
            if reason:
                updates.append(f"academics risk is {reason}")

        market_risks = markets.get("top_risks") if isinstance(markets.get("top_risks"), list) else []
        if market_risks:
            first = market_risks[0] if isinstance(market_risks[0], dict) else {}
            reason = str(first.get("reason") or first.get("risk_key") or "").strip()
            if reason:
                updates.append(f"markets risk is {reason}")

        opportunities = markets.get("top_opportunities") if isinstance(markets.get("top_opportunities"), list) else []
        if opportunities:
            first = opportunities[0] if isinstance(opportunities[0], dict) else {}
            symbol = str(first.get("symbol") or first.get("market") or "").strip()
            if symbol:
                updates.append(f"live opportunity watch is {symbol}")

        goals = identity.get("top_goals") if isinstance(identity.get("top_goals"), list) else []
        if goals:
            first_goal = goals[0] if isinstance(goals[0], dict) else {}
            description = str(first_goal.get("description") or "").strip()
            if description:
                updates.append(f"top goal signal is {description}")
        return updates

    def _derive_status_tradeoff(self, dialogue_context: dict[str, Any] | None) -> str:
        context = dict(dialogue_context or {})
        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        if int(operations.get("pending_interrupt_count") or 0) > 0:
            return "interrupt handling versus protected deep-work focus"
        if (academics.get("top_risks") or []) and (markets.get("top_risks") or []):
            return "academic deadline protection versus market opportunity timing"
        if markets.get("top_risks") or markets.get("top_opportunities"):
            return "speed of execution versus risk containment in markets"
        if academics.get("top_risks"):
            return "throughput today versus exam/deadline reliability"
        return "fast movement versus preserving optionality"

    def _derive_next_move(self, dialogue_context: dict[str, Any] | None) -> str:
        context = dict(dialogue_context or {})
        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        if int(operations.get("pending_interrupt_count") or 0) > 0:
            return "clear the highest-urgency interrupt and re-lock the primary objective"
        market_risks = markets.get("top_risks") if isinstance(markets.get("top_risks"), list) else []
        if market_risks:
            first = market_risks[0] if isinstance(market_risks[0], dict) else {}
            reason = str(first.get("reason") or first.get("risk_key") or "").strip()
            if reason:
                return f"run a bounded risk check on {reason} and decide within one cycle"
            return "run one bounded market risk check before execution"
        opportunities = markets.get("top_opportunities") if isinstance(markets.get("top_opportunities"), list) else []
        if opportunities:
            first = opportunities[0] if isinstance(opportunities[0], dict) else {}
            symbol = str(first.get("symbol") or first.get("market") or "").strip()
            if symbol:
                return f"validate thesis and stop-loss envelope for {symbol}"
            return "validate the top opportunity with one quick evidence pass"
        academic_risks = academics.get("top_risks") if isinstance(academics.get("top_risks"), list) else []
        if academic_risks:
            first = academic_risks[0] if isinstance(academic_risks[0], dict) else {}
            reason = str(first.get("reason") or first.get("risk_key") or "").strip()
            if reason:
                return f"lock the next academic action for {reason} and set a completion checkpoint"
            return "lock the next academic checkpoint and schedule it now"
        return "choose one concrete step and execute it before opening new branches"

    def _top_priority_pair_reply(self, dialogue_context: dict[str, Any] | None) -> str:
        context = dict(dialogue_context or {})
        updates = self._extract_live_state_items(context)
        if not updates:
            return (
                "Priority 1: protect continuity and lock the most leveraged objective. "
                "Priority 2: run a fast cross-domain scan. "
                "Tradeoff: speed versus optionality. "
                f"Next move: {self._derive_next_move(context)}."
            )
        first = updates[0]
        second = updates[1] if len(updates) > 1 else self._derive_next_move(context)
        return (
            f"Priority 1: {first}. "
            f"Priority 2: {second}. "
            f"Tradeoff: {self._derive_status_tradeoff(context)}. "
            f"Next move: {self._derive_next_move(context)}."
        )

    def _compute_live_briefs(self) -> dict[str, str]:
        context = self._build_presence_dialogue_context(
            user_text="status refresh",
            mode="equal",
            modality="text",
            continuity_ok=True,
            high_stakes=False,
            uncertainty=0.0,
            context={"include_extended_dialogue_context": True, "skip_dialogue_retrieval": True},
            include_briefs=False,
        )
        return {
            "now_brief": self._presence_status_snapshot(context),
            "top_two_priorities": self._stateful_status_reply(context),
            "academics_brief": self._stateful_status_reply({"academics": context.get("academics"), "identity": context.get("identity")}),
            "markets_brief": self._stateful_status_reply({"markets": context.get("markets"), "operations": context.get("operations")}),
            "zenith_brief": self._stateful_status_reply({"identity": context.get("identity"), "operations": context.get("operations")}),
            "risk_brief": self._derive_status_tradeoff(context),
            "identity_brief": self._derive_next_move(context),
        }

    def get_live_briefs(self, *, force_refresh: bool = False, max_age_seconds: float | None = None) -> dict[str, Any]:
        now = time.time()
        cache = dict(self._live_brief_cache or {})
        generated_at = float(cache.get("generated_at") or 0.0)
        ttl = float(max_age_seconds if max_age_seconds is not None else cache.get("max_age_seconds") or 45.0)
        stale = force_refresh or (now - generated_at) > max(5.0, ttl)
        briefs = cache.get("briefs") if isinstance(cache.get("briefs"), dict) else {}
        if stale or not briefs:
            briefs = self._compute_live_briefs()
            self._live_brief_cache = {
                "generated_at": now,
                "max_age_seconds": ttl,
                "briefs": dict(briefs),
            }
        return {
            "generated_at": float(self._live_brief_cache.get("generated_at") or now),
            "max_age_seconds": float(self._live_brief_cache.get("max_age_seconds") or ttl),
            "briefs": dict(self._live_brief_cache.get("briefs") or {}),
        }

    def _dialogue_extra_queries(
        self,
        *,
        user_text: str,
        objective_hint: str | None,
        unresolved_questions: list[str],
        partner_subfamily: str | None = None,
    ) -> list[str]:
        queries: list[str] = []
        for item in (
            str(objective_hint or "").strip(),
            *(str(q or "").strip() for q in unresolved_questions[:2]),
        ):
            if not item:
                continue
            if item.lower() in str(user_text or "").lower():
                continue
            if item not in queries:
                queries.append(item)
        lowered = str(user_text or "").strip().lower()
        if any(token in lowered for token in ("status", "what's up", "whats up", "going on")):
            status_probe = "current priorities risks interruptions"
            if status_probe not in queries:
                queries.append(status_probe)
        subfamily = str(partner_subfamily or "").strip().lower()
        if subfamily == "pushback":
            for item in (
                "recent pushback overrides and outcomes",
                "specific risk and safer alternative",
            ):
                if item not in queries:
                    queries.append(item)
        elif subfamily == "tradeoff":
            for item in (
                "live tradeoffs and highest leverage move",
                "time protection and opportunity cost",
            ):
                if item not in queries:
                    queries.append(item)
        elif subfamily == "identity":
            for item in (
                "relationship continuity identity anchor",
                "brother level memory and unresolved thread",
            ):
                if item not in queries:
                    queries.append(item)
        elif subfamily == "truth":
            for item in (
                "uncomfortable truth rationalization checks",
                "skeptical read and direct challenge",
            ):
                if item not in queries:
                    queries.append(item)
        elif subfamily == "strategic":
            for item in (
                "cross domain strategic synthesis",
                "bounded experiment and decision cadence",
            ):
                if item not in queries:
                    queries.append(item)
        elif subfamily == "reflection":
            for item in (
                "what are we noticing across domains",
                "hypotheses uncertainty and signal changes",
            ):
                if item not in queries:
                    queries.append(item)
        return queries[:4]

    def _dialogue_thread_terms(self, recent_dialogue_turns: list[dict[str, Any]]) -> list[str]:
        terms: list[str] = []
        for turn in recent_dialogue_turns[:6]:
            if not isinstance(turn, dict):
                continue
            user_text = str(turn.get("user_text") or "").strip()
            if user_text:
                terms.append(user_text)
        return terms

    @staticmethod
    def _partner_turn_subfamily(*, user_text: str) -> str:
        normalized = " ".join(str(user_text or "").strip().lower().split())
        if not normalized:
            return "general"
        if any(
            token in normalized
            for token in (
                "push back",
                "challenge",
                "stop doing immediately",
                "bullshitting",
                "bullshit",
                "wrong",
                "safer alternative",
                "strongest argument",
            )
        ):
            return "pushback"
        if any(
            token in normalized
            for token in (
                "tradeoff",
                "trade off",
                "cost if i delay",
                "cost do we absorb",
                "options and their tradeoff",
                "tradeoff between",
                "opportunity cost",
                "what matters more today",
                "delay this decision",
                "delay risk",
                "most expensive delay risk",
                "quality risk",
                "timing risk",
                "downside",
                "what do we defer",
                "versus",
                "vs ",
            )
        ):
            return "tradeoff"
        if any(
            token in normalized
            for token in (
                "what's your name",
                "whats your name",
                "who are you",
                "continue from earlier",
                "tracking about me",
                "what am i avoiding",
            )
        ):
            return "identity"
        if any(
            token in normalized
            for token in (
                "uncomfortable truth",
                "rationalizing",
                "what am i probably missing",
                "tell me what i am missing",
                "tell me what i'm missing",
                "be straight with me",
                "be direct with me",
                "what am i avoiding",
                "riskiest assumption",
                "most honest read",
                "what tension are you tracking across domains",
                "underweighting",
            )
        ):
            return "truth"
        if any(
            token in normalized
            for token in (
                "strategic read",
                "strategic recommendation",
                "deepest strategic recommendation",
                "partner level recommendation",
                "partner-level recommendation",
                "highest leverage move",
                "prioritize",
                "underestimating across",
                "what tension are you tracking across domains",
                "underweighting",
                "main contradiction",
            )
        ):
            return "strategic"
        if any(
            token in normalized
            for token in (
                "what do you think",
                "what are you noticing",
                "talk to me",
                "what would you do",
                "real tradeoff",
            )
        ):
            return "reflection"
        return "general"

    def _partner_depth_lane(
        self,
        *,
        user_text: str,
        partner_subfamily: str,
        high_stakes: bool = False,
        uncertainty: float = 0.0,
        context: dict[str, Any] | None = None,
    ) -> str:
        if not self._is_partner_dialogue_turn(user_text=user_text):
            return "partner_fast"
        subfamily = str(partner_subfamily or "").strip().lower() or "general"
        incoming = dict(context or {})
        if bool(incoming.get("force_partner_deep_lane")):
            return "partner_deep"
        low_power_mode = bool(incoming.get("low_power_mode")) or self._env_truthy("JARVIS_LOW_POWER_MODE", default=False)
        if low_power_mode:
            if self._is_explicit_partner_deep_request(user_text=user_text):
                return "partner_deep"
            return "partner_fast"
        normalized = self._normalize_dialogue_text(user_text)
        explicit_deep_markers = (
            "deeper read",
            "go deeper",
            "deepest strategic recommendation",
            "full read",
            "full strategic read",
            "longer read",
            "detailed read",
            "what do you really think",
            "tell me what i am missing",
            "tell me what i'm missing",
            "where am i bullshitting myself",
            "where am i bullshit",
            "be brutally honest",
            "be straight with me",
            "challenge my assumptions",
            "main contradiction",
            "strongest argument against",
            "underestimating across",
            "what tension are you tracking across domains",
            "underweighting",
            "what decision should i make before tonight",
            "tradeoff between",
            "opportunity cost",
            "what matters more today",
            "delay this decision",
            "delay risk",
            "most expensive delay risk",
            "quality risk",
            "timing risk",
            "deep partner level recommendation",
        )
        contradiction_markers = (
            "you are wrong",
            "you're wrong",
            "i disagree",
            "call me out",
            "push back hard",
            "challenge me",
            "challenge my plan",
            "rationalizing",
        )
        emotional_markers = (
            "overwhelmed",
            "panic",
            "scared",
            "anxious",
            "stressed",
            "burned out",
            "burnt out",
        )
        if subfamily in {"truth", "strategic", "tradeoff", "pushback"}:
            return "partner_deep"
        if subfamily == "reflection" and any(
            marker in normalized
            for marker in (
                "what would you do",
                "strongest argument",
                "what do you think i am avoiding",
                "deeper read",
                "go deeper",
                "main contradiction",
                "underestimating",
            )
        ):
            return "partner_deep"
        if any(marker in normalized for marker in explicit_deep_markers):
            return "partner_deep"
        if any(marker in normalized for marker in contradiction_markers):
            return "partner_deep"
        if any(marker in normalized for marker in emotional_markers):
            return "partner_deep"
        if subfamily == "reflection" and any(
            marker in normalized
            for marker in (
                "what am i missing",
                "what do you really think",
                "uncomfortable truth",
                "real tradeoff",
                "challenge",
            )
        ):
            return "partner_deep"
        try:
            uncertainty_value = float(uncertainty)
        except (TypeError, ValueError):
            uncertainty_value = 0.0
        if bool(high_stakes) and uncertainty_value >= 0.45:
            return "partner_deep"
        if bool(incoming.get("requires_pushback")) and uncertainty_value >= 0.35:
            return "partner_deep"
        top_two = str(
            ((incoming.get("live_briefs") or {}).get("top_two_priorities"))
            if isinstance(incoming.get("live_briefs"), dict)
            else ""
        ).strip().lower()
        if (
            top_two
            and ("academic" in top_two or "exam" in top_two)
            and ("market" in top_two or "zenith" in top_two or "opportunit" in top_two)
        ):
            return "partner_deep"
        return "partner_fast"

    @staticmethod
    def _partner_retrieval_targets(*, partner_depth_lane: str | None = None) -> tuple[int, int]:
        lane = str(partner_depth_lane or "").strip().lower() or "partner_fast"
        if lane == "partner_deep":
            min_raw = str(os.getenv("JARVIS_PARTNER_DEEP_RETRIEVAL_MIN_SNIPPETS") or "").strip()
            target_raw = str(os.getenv("JARVIS_PARTNER_DEEP_RETRIEVAL_TARGET_SNIPPETS") or "").strip()
            try:
                minimum = max(3, int(min_raw)) if min_raw else 5
            except ValueError:
                minimum = 5
            try:
                target = max(minimum, int(target_raw)) if target_raw else max(7, minimum)
            except ValueError:
                target = max(7, minimum)
            return minimum, min(16, target)
        min_raw = str(os.getenv("JARVIS_PARTNER_RETRIEVAL_MIN_SNIPPETS") or "").strip()
        target_raw = str(os.getenv("JARVIS_PARTNER_RETRIEVAL_TARGET_SNIPPETS") or "").strip()
        try:
            minimum = max(1, int(min_raw)) if min_raw else 3
        except ValueError:
            minimum = 3
        try:
            target = max(minimum, int(target_raw)) if target_raw else max(5, minimum)
        except ValueError:
            target = max(5, minimum)
        return minimum, min(12, target)

    @staticmethod
    def _infer_retrieval_bucket(*, memory_key: str | None, source: str | None = None) -> str:
        key = str(memory_key or "").strip().lower()
        src = str(source or "").strip().lower()
        joint = f"{key} {src}"
        if any(token in joint for token in ("thread", "dialogue", "turn", "session", "unresolved", "hypothesis")):
            return "thread_memory"
        if any(token in joint for token in ("pushback", "override", "outcome", "calibration", "risk_outcome")):
            return "pushback_outcomes"
        if any(token in joint for token in ("personal", "stress", "energy", "focus", "user_model", "availability")):
            return "personal_context"
        if any(token in joint for token in ("identity", "soul", "user", "goal", "contract", "relationship", "tone")):
            return "identity_long_horizon"
        if any(
            token in joint
            for token in (
                "brief",
                "status",
                "priority",
                "risk",
                "market",
                "academic",
                "interrupt",
                "zenith",
            )
        ):
            return "live_state"
        return "general"

    def _snippet_bucket_counts(self, snippets: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "live_state": 0,
            "thread_memory": 0,
            "identity_long_horizon": 0,
            "personal_context": 0,
            "pushback_outcomes": 0,
            "general": 0,
        }
        for item in list(snippets or []):
            if not isinstance(item, dict):
                continue
            bucket = str(
                item.get("source_bucket")
                or self._infer_retrieval_bucket(
                    memory_key=str(item.get("memory_key") or ""),
                    source=str(item.get("source") or item.get("source_query") or ""),
                )
            ).strip().lower()
            if bucket not in counts:
                bucket = "general"
            counts[bucket] += 1
        return counts

    @staticmethod
    def _partner_context_mix_ok(
        bucket_counts: dict[str, Any] | None,
        *,
        partner_depth_lane: str | None = None,
    ) -> bool:
        counts = dict(bucket_counts or {})
        baseline = (
            int(counts.get("live_state") or 0) >= 1
            and int(counts.get("thread_memory") or 0) >= 1
            and int(counts.get("identity_long_horizon") or 0) >= 1
        )
        if not baseline:
            return False
        lane = str(partner_depth_lane or "").strip().lower() or "partner_fast"
        if lane == "partner_deep":
            return (
                int(counts.get("personal_context") or 0) >= 1
                and int(counts.get("pushback_outcomes") or 0) >= 1
            )
        return True

    @staticmethod
    def _surface_bullets(content: str, *, limit: int = 3) -> list[str]:
        bullets: list[str] = []
        for raw in str(content or "").splitlines():
            line = str(raw or "").strip()
            if not line.startswith("- "):
                continue
            value = line[2:].strip()
            if not value:
                continue
            bullets.append(value)
            if len(bullets) >= limit:
                break
        return bullets

    def _build_dialogue_identity_capsule(
        self,
        *,
        mode: str,
        user_text: str,
    ) -> dict[str, Any]:
        now = time.time()
        contract_hash = self.get_consciousness_contract_hash()
        cached = dict(self._dialogue_identity_capsule_cache or {})
        cache_hash = str(cached.get("contract_hash") or "").strip()
        cache_age = float(cached.get("generated_at") or 0.0)
        if cache_hash == contract_hash and (now - cache_age) <= 120.0:
            capsule = cached.get("capsule")
            if isinstance(capsule, dict) and capsule:
                return capsule

        contract = self.get_consciousness_contract()
        interaction_modes = dict(contract.get("interaction_modes") or {})
        inquiry = dict(contract.get("epistemic_inquiry_protocol") or {})
        growth = dict(contract.get("resource_growth_policy") or {})
        surfaces = self.get_consciousness_surfaces(include_content=True)
        files = list(surfaces.get("files") or []) if isinstance(surfaces, dict) else []
        by_name: dict[str, str] = {}
        for item in files:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().upper()
            content = str(item.get("content") or "").strip()
            if name and content:
                by_name[name] = content

        soul_bullets = self._surface_bullets(by_name.get("SOUL", ""), limit=3)
        identity_bullets = self._surface_bullets(by_name.get("IDENTITY", ""), limit=3)
        user_bullets = self._surface_bullets(by_name.get("USER", ""), limit=3)
        memory_bullets = self._surface_bullets(by_name.get("MEMORY", ""), limit=2)

        capsule = {
            "anchor": "JARVIS is the mind: equal-partner by default, measured pushback, truthful uncertainty.",
            "contract_hash": contract_hash,
            "mode_requested": str(mode or "equal").strip().lower() or "equal",
            "partner_mode_default": "equal",
            "interaction_mode_ratios": {
                "equal": float(interaction_modes.get("equal_ratio") or 0.9),
                "butler": float(interaction_modes.get("butler_ratio") or 0.1),
            },
            "voice_anchor": (
                "Measured, direct, calm-under-pressure. No pipeline/debug language in user replies."
            ),
            "pushback_style": (
                "Contextual challenge with specific risk and safer alternative; avoid generic compliance."
            ),
            "uncertainty_style": (
                "Surface hypotheses early with confidence bounds; avoid bluffing."
            ),
            "time_protection_style": (
                "Protect finite time with tradeoff framing and one concrete next move."
            ),
            "care_boundary": (
                "Hard boundaries on harmful requests; preserve dignity and continuity in response."
            ),
            "resource_growth_focus": {
                "objective": str(growth.get("objective") or "").strip() or None,
                "focus_projects": list(growth.get("focus_projects") or []),
            },
            "epistemic_inquiry": {
                "enabled": bool(inquiry.get("enabled")),
                "style": str(inquiry.get("style") or "open_discussion").strip() or "open_discussion",
                "topics": list(inquiry.get("topics") or []),
            },
            "surface_extracts": {
                "soul": soul_bullets,
                "identity": identity_bullets,
                "user": user_bullets,
                "memory": memory_bullets,
            },
            "prompt_snapshot": self._normalize_dialogue_text(user_text)[:180],
        }
        self._dialogue_identity_capsule_cache = {
            "generated_at": now,
            "contract_hash": contract_hash,
            "capsule": capsule,
        }
        return capsule

    def _build_presence_dialogue_context(
        self,
        *,
        user_text: str,
        mode: str,
        modality: str,
        continuity_ok: bool,
        high_stakes: bool,
        uncertainty: float,
        context: dict[str, Any] | None = None,
        include_briefs: bool = True,
    ) -> dict[str, Any]:
        incoming = dict(context or {})
        include_extended = bool(incoming.get("include_extended_dialogue_context"))
        skip_retrieval = bool(incoming.get("skip_dialogue_retrieval"))
        neutral_voice_mode = bool(incoming.get("neutral_voice_mode"))
        disable_identity_capsule = bool(incoming.get("disable_identity_capsule")) or neutral_voice_mode
        disable_live_state_context = bool(incoming.get("disable_live_state_context")) or neutral_voice_mode
        disable_partner_dialogue_turn = bool(incoming.get("disable_partner_dialogue_turn")) or neutral_voice_mode
        if disable_live_state_context:
            skip_retrieval = True
        text_for_context = str(user_text or "").strip().lower()
        needs_domain_snapshot = any(
            token in text_for_context
            for token in (
                "status",
                "update",
                "what's up",
                "whats up",
                "going on",
                "market",
                "trade",
                "bet",
                "academic",
                "school",
                "class",
                "deadline",
            )
        )
        prefs = self.get_operator_preferences()
        user_model = self.get_user_model()
        personal_context = self.get_personal_context()
        thoughts = self.list_recent_thoughts(limit=1)
        latest_thought = thoughts[0] if thoughts else {}
        hypotheses = latest_thought.get("hypotheses") if isinstance(latest_thought.get("hypotheses"), list) else []
        top_hypotheses = [
            str(item.get("claim") or "").strip()
            for item in hypotheses[:3]
            if isinstance(item, dict) and str(item.get("claim") or "").strip()
        ]

        def _safe_float(value: Any) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        def _compact_risks(items: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
            ranked = sorted(
                [item for item in items if isinstance(item, dict)],
                key=lambda item: _safe_float(item.get("confidence")),
                reverse=True,
            )
            compact: list[dict[str, Any]] = []
            for item in ranked[:limit]:
                value = item.get("value") if isinstance(item.get("value"), dict) else {}
                compact.append(
                    {
                        "risk_key": str(item.get("risk_key") or "").strip(),
                        "confidence": round(_safe_float(item.get("confidence")), 4),
                        "severity": str(value.get("severity") or "").strip().lower() or None,
                        "reason": str(value.get("reason") or "").strip() or None,
                    }
                )
            return compact

        def _compact_opportunities(items: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
            compact: list[dict[str, Any]] = []
            for item in [x for x in items if isinstance(x, dict)][:limit]:
                compact.append(
                    {
                        "symbol": str(item.get("symbol") or "").strip() or None,
                        "market": str(item.get("market") or item.get("exchange") or "").strip() or None,
                        "confidence": round(_safe_float(item.get("confidence")), 4),
                        "edge": _safe_float(item.get("edge") or item.get("expected_edge")),
                    }
                )
            return compact

        goals = user_model.get("goals") if isinstance(user_model.get("goals"), list) else []
        top_goals = [
            {
                "goal_id": str(goal.get("goal_id") or "").strip() or None,
                "description": str(goal.get("description") or "").strip() or None,
                "weight": round(_safe_float(goal.get("weight")), 4),
            }
            for goal in goals[:4]
            if isinstance(goal, dict)
        ]

        pending_interrupts = self.list_interrupts(status="pending", limit=5)
        interrupt_summaries = [
            {
                "interrupt_id": str(item.get("interrupt_id") or "").strip() or None,
                "domain": str(item.get("domain") or "").strip() or None,
                "urgency": round(_safe_float(item.get("urgency_score")), 4),
                "headline": str(item.get("headline") or "").strip() or None,
            }
            for item in pending_interrupts
            if isinstance(item, dict)
        ]

        recent_pushback = self.list_pushback_calibration(limit=5)
        pushbacks = recent_pushback.get("pushbacks") if isinstance(recent_pushback.get("pushbacks"), list) else []
        pushback_summaries = [
            {
                "pushback_id": str(item.get("pushback_id") or "").strip() or None,
                "domain": str(item.get("domain") or "").strip() or None,
                "severity": str(item.get("severity") or "").strip().lower() or None,
            }
            for item in pushbacks[:3]
            if isinstance(item, dict)
        ]

        surface_id = str(incoming.get("surface_id") or "").strip() or None
        session_id = str(incoming.get("session_id") or "").strip() or None
        surface_session = None
        dialogue_thread: dict[str, Any] | None = None
        recent_dialogue_turns: list[dict[str, Any]] = []
        if surface_id and session_id:
            surface_session = self.get_surface_session(surface_id=surface_id, session_id=session_id)
            dialogue_thread = self.dialogue_state.upsert_thread(
                surface_id=surface_id,
                session_id=session_id,
                session_key=(
                    str((surface_session or {}).get("session_key") or "").strip()
                    if isinstance(surface_session, dict)
                    else None
                ),
                mode=str(mode or "equal").strip().lower() or "equal",
                objective_hint=str(incoming.get("objective_hint") or "").strip() or None,
            )
            thread_id = (
                str((dialogue_thread or {}).get("thread_id") or "").strip()
                if isinstance(dialogue_thread, dict)
                else ""
            )
            if thread_id:
                recent_turns = self.dialogue_state.list_recent_turns(
                    thread_id=thread_id,
                    limit=8,
                )
                recent_dialogue_turns = [
                    {
                        "turn_index": int(item.get("turn_index") or 0),
                        "user_text": str(item.get("user_text") or "").strip(),
                        "final_reply": str(item.get("final_reply") or "").strip(),
                        "mode": str(item.get("mode") or "").strip() or None,
                        "created_at": str(item.get("created_at") or "").strip() or None,
                    }
                    for item in recent_turns
                    if isinstance(item, dict)
                ]

        thread_unresolved = (
            list((dialogue_thread or {}).get("unresolved_questions") or [])
            if isinstance(dialogue_thread, dict)
            else []
        )
        thread_objective_hint = (
            str((dialogue_thread or {}).get("objective_hint") or "").strip()
            if isinstance(dialogue_thread, dict)
            else ""
        )
        partner_dialogue_turn = (
            self._is_partner_dialogue_turn(user_text=str(user_text or ""))
            and not disable_partner_dialogue_turn
        )
        partner_subfamily = self._partner_turn_subfamily(user_text=str(user_text or "")) if partner_dialogue_turn else "general"
        partner_depth_lane = (
            self._partner_depth_lane(
                user_text=str(user_text or ""),
                partner_subfamily=partner_subfamily,
                high_stakes=bool(high_stakes),
                uncertainty=float(uncertainty),
                context=incoming,
            )
            if partner_dialogue_turn
            else "partner_fast"
        )
        partner_min_snippets, partner_target_snippets = self._partner_retrieval_targets(
            partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None
        )
        identity_capsule = (
            {}
            if disable_identity_capsule
            else self._build_dialogue_identity_capsule(
                mode=str(mode or "equal"),
                user_text=str(user_text or ""),
            )
        )
        if skip_retrieval:
            retrieval_bundle: dict[str, Any] = {
                "snippets": [],
                "strategy": {"skipped": True, "reason": "low_latency_status_turn"},
                "candidate_count": 0,
            }
        else:
            retrieval_limit = int(incoming.get("dialogue_retrieval_limit") or 8)
            candidate_limit = int(incoming.get("dialogue_retrieval_candidate_limit") or 32)
            if partner_dialogue_turn:
                retrieval_limit = max(retrieval_limit, partner_target_snippets)
            retrieval_limit = max(1, retrieval_limit)
            candidate_limit = max(retrieval_limit, candidate_limit)
            if partner_dialogue_turn and partner_depth_lane == "partner_deep":
                deep_candidate_raw = str(os.getenv("JARVIS_PARTNER_DEEP_RETRIEVAL_CANDIDATE_LIMIT") or "").strip()
                try:
                    deep_candidate_limit = max(40, int(deep_candidate_raw)) if deep_candidate_raw else 48
                except ValueError:
                    deep_candidate_limit = 48
                candidate_limit = max(candidate_limit, deep_candidate_limit)
            retrieval_bundle = self.dialogue_retriever.retrieve(
                query=str(user_text or ""),
                extra_queries=self._dialogue_extra_queries(
                    user_text=user_text,
                    objective_hint=thread_objective_hint or None,
                    unresolved_questions=thread_unresolved,
                    partner_subfamily=partner_subfamily if partner_dialogue_turn else None,
                ),
                thread_terms=self._dialogue_thread_terms(recent_dialogue_turns),
                limit=retrieval_limit,
                candidate_limit=candidate_limit,
            )
        retrieved_snippets = (
            list(retrieval_bundle.get("snippets") or [])
            if isinstance(retrieval_bundle, dict)
            else []
        )
        retrieval_strategy = (
            dict(retrieval_bundle.get("strategy") or {})
            if isinstance(retrieval_bundle, dict)
            else {}
        )
        retrieval_candidate_count = int(retrieval_bundle.get("candidate_count") or 0) if isinstance(retrieval_bundle, dict) else 0
        for item in retrieved_snippets:
            if not isinstance(item, dict):
                continue
            snippet_text = str(item.get("text") or item.get("snippet") or "").strip()
            if snippet_text and not str(item.get("text") or "").strip():
                item["text"] = snippet_text
            if snippet_text and not str(item.get("snippet") or "").strip():
                item["snippet"] = snippet_text
            if not str(item.get("source_bucket") or "").strip():
                item["source_bucket"] = self._infer_retrieval_bucket(
                    memory_key=str(item.get("memory_key") or ""),
                    source=str(item.get("source") or item.get("source_query") or ""),
                )

        if partner_dialogue_turn and len(retrieved_snippets) < partner_target_snippets:
            live_briefs = self.get_live_briefs().get("briefs") if include_briefs else {}
            thread_summary = (
                str((dialogue_thread or {}).get("summary_text") or "").strip()
                if isinstance(dialogue_thread, dict)
                else ""
            )
            unresolved_text = "; ".join(
                str(item or "").strip()
                for item in thread_unresolved[:3]
                if str(item or "").strip()
            ).strip()
            recent_thread_text = "; ".join(
                str(item.get("user_text") or "").strip()
                for item in recent_dialogue_turns[:3]
                if isinstance(item, dict) and str(item.get("user_text") or "").strip()
            ).strip()
            identity_extracts = (
                dict(identity_capsule.get("surface_extracts") or {})
                if isinstance(identity_capsule.get("surface_extracts"), dict)
                else {}
            )
            identity_snippet = "; ".join(
                str(value).strip()
                for value in (
                    identity_capsule.get("anchor"),
                    identity_capsule.get("pushback_style"),
                    identity_capsule.get("time_protection_style"),
                    identity_capsule.get("voice_anchor"),
                )
                if str(value).strip()
            ).strip()
            identity_memory_hint = "; ".join(
                str(item).strip()
                for item in list(identity_extracts.get("memory") or [])[:2]
                if str(item).strip()
            ).strip()
            personal_focus = int(personal_context.get("available_focus_minutes") or 0)
            personal_stress = _safe_float(personal_context.get("stress_level"))
            personal_energy = _safe_float(personal_context.get("energy_level"))
            personal_note = str(personal_context.get("note") or "").strip()
            personal_context_hint = (
                f"Personal context: focus_minutes={personal_focus}, stress={personal_stress:.2f}, energy={personal_energy:.2f}."
            )
            if personal_note:
                personal_context_hint = f"{personal_context_hint} Note: {personal_note}"
            pushback_outcome_hint = "; ".join(
                f"domain={str(item.get('domain') or '').strip() or 'general'} severity={str(item.get('severity') or '').strip() or 'unknown'}"
                for item in pushback_summaries[:3]
                if isinstance(item, dict)
            ).strip()
            fallback_candidates = [
                ("live_state", "live_brief.top_two_priorities", str((live_briefs or {}).get("top_two_priorities") or "").strip()),
                ("live_state", "live_brief.now_brief", str((live_briefs or {}).get("now_brief") or "").strip()),
                ("live_state", "live_brief.risk_brief", str((live_briefs or {}).get("risk_brief") or "").strip()),
                ("live_state", "live_brief.academics_brief", str((live_briefs or {}).get("academics_brief") or "").strip()),
                ("live_state", "live_brief.markets_brief", str((live_briefs or {}).get("markets_brief") or "").strip()),
                ("live_state", "live_brief.zenith_brief", str((live_briefs or {}).get("zenith_brief") or "").strip()),
                ("identity_long_horizon", "identity.anchor", identity_snippet),
                ("identity_long_horizon", "identity.memory_hint", identity_memory_hint),
                ("thread_memory", "thread.summary", thread_summary),
                ("thread_memory", "thread.unresolved", unresolved_text),
                ("thread_memory", "thread.recent_turns", recent_thread_text),
                ("thread_memory", "thread.current_prompt", f"Current thread signal from user: {str(user_text or '').strip()}"),
                ("personal_context", "personal.context", personal_context_hint),
                ("pushback_outcomes", "operations.pushback_outcomes", pushback_outcome_hint),
            ]

            existing_keys = {
                f"{str(item.get('memory_key') or '').strip()}|{self._normalize_dialogue_text(str(item.get('text') or item.get('snippet') or ''))}"
                for item in retrieved_snippets
                if isinstance(item, dict)
            }

            def _append_snippet(*, bucket: str, memory_key: str, snippet_text: str, score: float, source: str) -> bool:
                clean = str(snippet_text or "").strip()
                if not clean:
                    return False
                dedupe_key = f"{memory_key}|{self._normalize_dialogue_text(clean)}"
                if dedupe_key in existing_keys:
                    return False
                existing_keys.add(dedupe_key)
                retrieved_snippets.append(
                    {
                        "memory_key": memory_key,
                        "text": clean,
                        "snippet": clean,
                        "score": score,
                        "source": source,
                        "source_bucket": bucket,
                    }
                )
                return True

            counts = self._snippet_bucket_counts(retrieved_snippets)
            required_buckets = (
                ("live_state", "thread_memory", "identity_long_horizon", "personal_context", "pushback_outcomes")
                if partner_depth_lane == "partner_deep"
                else ("live_state", "thread_memory", "identity_long_horizon")
            )
            for required in required_buckets:
                if counts.get(required, 0) > 0:
                    continue
                for bucket, memory_key, snippet_text in fallback_candidates:
                    if bucket != required:
                        continue
                    if _append_snippet(
                        bucket=bucket,
                        memory_key=memory_key,
                        snippet_text=snippet_text,
                        score=0.54,
                        source="partner_mix_fill",
                    ):
                        counts[bucket] = counts.get(bucket, 0) + 1
                        break

            for bucket, memory_key, snippet_text in fallback_candidates:
                if len(retrieved_snippets) >= partner_target_snippets:
                    break
                if not snippet_text:
                    continue
                _append_snippet(
                    bucket=bucket,
                    memory_key=memory_key,
                    snippet_text=snippet_text,
                    score=0.49,
                    source="live_brief_fallback",
                )

            while len(retrieved_snippets) < partner_min_snippets:
                _append_snippet(
                    bucket="general",
                    memory_key="live_brief.partner_context",
                    snippet_text=(
                        "Live state is partially sparse. Prioritize continuity, one tradeoff, and one concrete next move."
                    ),
                    score=0.41,
                    source="live_brief_fallback",
                )
                if len(retrieved_snippets) >= partner_min_snippets:
                    break

            retrieval_candidate_count = max(len(retrieved_snippets), retrieval_candidate_count, 1)
            retrieval_strategy = dict(retrieval_strategy)
            retrieval_strategy["fallback"] = "live_brief_partner_context_fill"
            retrieval_strategy["partner_min_snippets"] = partner_min_snippets
        if partner_dialogue_turn:
            retrieval_strategy = dict(retrieval_strategy)
            retrieval_strategy["partner_target_snippets"] = partner_target_snippets
            retrieval_strategy["partner_subfamily"] = partner_subfamily
            retrieval_strategy["partner_depth_lane"] = partner_depth_lane
            retrieval_strategy["snippet_bucket_counts"] = self._snippet_bucket_counts(retrieved_snippets)
            retrieval_strategy["partner_context_mix_ok"] = self._partner_context_mix_ok(
                retrieval_strategy["snippet_bucket_counts"],
                partner_depth_lane=partner_depth_lane,
            )

        dialogue_context: dict[str, Any] = {
            "conversation": {
                "surface_id": surface_id,
                "session_id": session_id,
                "modality": str(modality or "text"),
                "mode": str(mode or "equal"),
                "partner_subfamily": partner_subfamily if partner_dialogue_turn else None,
                "partner_depth_lane": partner_depth_lane if partner_dialogue_turn else None,
                "neutral_voice_mode": bool(neutral_voice_mode),
                "disable_partner_dialogue_turn": bool(disable_partner_dialogue_turn),
                "disable_identity_capsule": bool(disable_identity_capsule),
                "disable_live_state_context": bool(disable_live_state_context),
                "continuity_ok": bool(continuity_ok),
                "high_stakes": bool(high_stakes),
                "uncertainty": round(max(0.0, min(1.0, float(uncertainty))), 4),
                "user_text": str(user_text or "").strip(),
            },
            "identity": {
                "top_goals": [] if disable_live_state_context else top_goals,
                "personal_context": {
                    "stress_level": (0.0 if disable_live_state_context else _safe_float(personal_context.get("stress_level"))),
                    "energy_level": (0.0 if disable_live_state_context else _safe_float(personal_context.get("energy_level"))),
                    "available_focus_minutes": (0 if disable_live_state_context else int(personal_context.get("available_focus_minutes") or 0)),
                    "mode": (None if disable_live_state_context else (str(personal_context.get("mode") or "").strip() or None)),
                    "note": (None if disable_live_state_context else (str(personal_context.get("note") or "").strip() or None)),
                },
                "focus_mode_domain": (None if disable_live_state_context else (prefs.get("focus_mode_domain") if isinstance(prefs, dict) else None)),
            },
            "thinking": {
                "latest_thought_id": (
                    None
                    if disable_live_state_context
                    else (str(latest_thought.get("thought_id") or "").strip() or None)
                ),
                "top_hypotheses": ([] if disable_live_state_context else top_hypotheses),
            },
            "operations": {
                "pending_interrupt_count": (0 if disable_live_state_context else len(interrupt_summaries)),
                "pending_interrupts": ([] if disable_live_state_context else interrupt_summaries),
                "recent_event_types": [],
                "recent_pushbacks": ([] if disable_live_state_context else pushback_summaries),
            },
            "academics": {
                "top_risks": ([] if disable_live_state_context else (
                    _compact_risks(self.list_academic_risks(), limit=3)
                    if needs_domain_snapshot
                    else []
                )),
                "latest_synthesis": (None if disable_live_state_context else (self.get_latest_synthesis("morning") if include_extended else None)),
            },
            "markets": {
                "top_risks": ([] if disable_live_state_context else (
                    _compact_risks(self.list_market_risks(), limit=3)
                    if needs_domain_snapshot
                    else []
                )),
                "top_opportunities": ([] if disable_live_state_context else (
                    _compact_opportunities(self.list_market_opportunities(limit=3), limit=3)
                    if include_extended
                    else []
                )),
                "risk_posture": (None if disable_live_state_context else (self.get_market_risk_posture() if needs_domain_snapshot else None)),
            },
            "session": {
                "session_key": (
                    str((surface_session or {}).get("session_key") or "").strip()
                    if isinstance(surface_session, dict)
                    else None
                ),
                "last_relationship_mode": (
                    str((surface_session or {}).get("last_relationship_mode") or "").strip()
                    if isinstance(surface_session, dict)
                    else None
                ),
            },
            "dialogue_thread": {
                "thread_id": (
                    str((dialogue_thread or {}).get("thread_id") or "").strip()
                    if isinstance(dialogue_thread, dict)
                    else None
                ),
                "summary_text": (
                    (
                        None
                        if neutral_voice_mode
                        else str((dialogue_thread or {}).get("summary_text") or "").strip()
                    )
                    if isinstance(dialogue_thread, dict)
                    else None
                ),
                "objective_hint": (
                    (
                        None
                        if neutral_voice_mode
                        else str((dialogue_thread or {}).get("objective_hint") or "").strip()
                    )
                    if isinstance(dialogue_thread, dict)
                    else None
                ),
                "unresolved_questions": ([] if neutral_voice_mode else thread_unresolved),
                "active_hypotheses": (
                    ([] if neutral_voice_mode else
                    list((dialogue_thread or {}).get("active_hypotheses") or [])
                    if isinstance(dialogue_thread, dict)
                    else [])
                ),
                "recent_turns": recent_dialogue_turns,
            },
            "memory": {
                "semantic_snippets": retrieved_snippets,
                "retrieval_candidate_count": retrieval_candidate_count,
                "retrieval_strategy": retrieval_strategy,
                "snippet_bucket_counts": self._snippet_bucket_counts(retrieved_snippets),
                "partner_context_mix_ok": self._partner_context_mix_ok(
                    self._snippet_bucket_counts(retrieved_snippets),
                    partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None,
                ),
                "partner_subfamily": partner_subfamily if partner_dialogue_turn else None,
                "partner_depth_lane": partner_depth_lane if partner_dialogue_turn else None,
            },
            "identity_capsule": identity_capsule,
            "briefs": (
                {}
                if (disable_live_state_context or not include_briefs)
                else self.get_live_briefs().get("briefs")
            ),
        }
        thinking_block = dialogue_context.get("thinking") if isinstance(dialogue_context.get("thinking"), dict) else {}
        if isinstance(thinking_block, dict):
            thinking_block["active_priorities"] = self._extract_live_state_items(dialogue_context)[:4]
        dialogue_context["status_snapshot"] = self._presence_status_snapshot(dialogue_context)
        return dialogue_context

    def build_dialogue_context(
        self,
        *,
        user_text: str,
        mode: str,
        modality: str,
        continuity_ok: bool,
        high_stakes: bool = False,
        uncertainty: float = 0.0,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._build_presence_dialogue_context(
            user_text=user_text,
            mode=mode,
            modality=modality,
            continuity_ok=continuity_ok,
            high_stakes=high_stakes,
            uncertainty=uncertainty,
            context=context,
        )

    def _infer_dialogue_intent(self, *, user_text: str) -> dict[str, Any]:
        text = str(user_text or "").strip()
        normalized = self._normalize_dialogue_text(text)
        words = normalized.split()
        lowered = normalized.lower()
        is_question = text.endswith("?") or any(token in lowered for token in ("what", "why", "how", "should", "can"))
        directive_markers = ("do ", "make ", "run ", "fix ", "build ", "implement ", "change ", "update ")
        emotional_markers = ("frustrated", "stuck", "angry", "upset", "overwhelmed", "confused")
        return {
            "is_question": bool(is_question),
            "is_short_social": len(words) <= 4 and any(token in lowered for token in ("hi", "hello", "hey", "sup", "yo")),
            "contains_directive": any(marker in lowered for marker in directive_markers),
            "contains_emotional_signal": any(marker in lowered for marker in emotional_markers),
            "token_count": len(words),
        }

    def _dialogue_context_refs(self, dialogue_context: dict[str, Any] | None) -> list[str]:
        context = dict(dialogue_context or {})
        refs: list[str] = []
        if context.get("identity"):
            refs.append("identity")
        if context.get("identity_capsule"):
            refs.append("identity.capsule")
        if context.get("thinking"):
            refs.append("thinking")
        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        thread = context.get("dialogue_thread") if isinstance(context.get("dialogue_thread"), dict) else {}
        memory_block = context.get("memory") if isinstance(context.get("memory"), dict) else {}
        if academics.get("top_risks"):
            refs.append("academics.risks")
        if markets.get("top_risks"):
            refs.append("markets.risks")
        if markets.get("top_opportunities"):
            refs.append("markets.opportunities")
        if operations.get("pending_interrupt_count"):
            refs.append("operations.interrupts")
        if thread.get("recent_turns"):
            refs.append("dialogue.recent_turns")
        if thread.get("unresolved_questions"):
            refs.append("dialogue.unresolved_questions")
        if memory_block.get("semantic_snippets"):
            refs.append("memory.semantic")
        return refs

    def generate_dialogue_turn(
        self,
        *,
        user_text: str,
        mode: str,
        modality: str,
        continuity_ok: bool,
        high_stakes: bool = False,
        uncertainty: float = 0.0,
        context: dict[str, Any] | None = None,
        dialogue_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = str(user_text or "").strip()
        resolved_context = dict(context or {})
        resolved_dialogue_context = dict(dialogue_context or {})
        neutral_voice_mode = bool(resolved_context.get("neutral_voice_mode"))
        disable_partner_dialogue_turn = bool(resolved_context.get("disable_partner_dialogue_turn")) or neutral_voice_mode
        partner_dialogue_turn = self._is_partner_dialogue_turn(user_text=text) and not disable_partner_dialogue_turn
        partner_subfamily = str(
            (
                (resolved_dialogue_context.get("conversation") or {}).get("partner_subfamily")
                if isinstance(resolved_dialogue_context.get("conversation"), dict)
                else None
            )
            or self._partner_turn_subfamily(user_text=text)
        ).strip().lower() or "general"
        partner_depth_lane = str(
            (
                (resolved_dialogue_context.get("conversation") or {}).get("partner_depth_lane")
                if isinstance(resolved_dialogue_context.get("conversation"), dict)
                else None
            )
            or self._partner_depth_lane(
                user_text=text,
                partner_subfamily=partner_subfamily,
                high_stakes=bool(high_stakes),
                uncertainty=float(uncertainty),
                context=resolved_context,
            )
        ).strip().lower() or "partner_fast"
        partner_min_snippets, partner_target_snippets = self._partner_retrieval_targets(
            partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None
        )
        heuristic_candidate = ""
        source = "heuristic_fallback"
        candidate = ""
        model_path_used = False
        model_query_attempted = False
        model_query_succeeded = False
        model_failure_reason: str | None = None
        deep_lane_invoked = False
        deep_model_name: str | None = None
        memory_block = resolved_dialogue_context.get("memory") if isinstance(resolved_dialogue_context.get("memory"), dict) else {}
        snippets = list(memory_block.get("semantic_snippets") or []) if isinstance(memory_block.get("semantic_snippets"), list) else []
        top_memory_keys = [
            str(item.get("memory_key") or "").strip()
            for item in snippets[:5]
            if isinstance(item, dict) and str(item.get("memory_key") or "").strip()
        ]
        snippet_bucket_counts = (
            dict(memory_block.get("snippet_bucket_counts") or {})
            if isinstance(memory_block.get("snippet_bucket_counts"), dict)
            else self._snippet_bucket_counts(snippets)
        )
        partner_context_mix_ok = (
            bool(memory_block.get("partner_context_mix_ok"))
            if "partner_context_mix_ok" in memory_block
            else self._partner_context_mix_ok(
                snippet_bucket_counts,
                partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None,
            )
        )
        retrieval_summary = {
            "snippet_count": len(snippets),
            "candidate_count": int(memory_block.get("retrieval_candidate_count") or 0) if isinstance(memory_block, dict) else 0,
            "strategy": (
                dict(memory_block.get("retrieval_strategy") or {})
                if isinstance(memory_block.get("retrieval_strategy"), dict)
                else {}
            ),
            "top_memory_keys": top_memory_keys,
            "snippet_bucket_counts": snippet_bucket_counts,
            "partner_context_mix_ok": bool(partner_context_mix_ok),
            "partner_subfamily": partner_subfamily if partner_dialogue_turn else None,
            "partner_depth_lane": partner_depth_lane if partner_dialogue_turn else None,
        }
        structured = {
            "user_text": text,
            "mode": str(mode or "equal"),
            "modality": str(modality or "text"),
            "continuity_ok": bool(continuity_ok),
            "high_stakes": bool(high_stakes),
            "uncertainty": round(max(0.0, min(1.0, float(uncertainty))), 4),
            "status_snapshot": str(resolved_dialogue_context.get("status_snapshot") or "").strip() or None,
            "dialogue_memory_count": int(retrieval_summary.get("candidate_count") or 0),
            "partner_subfamily": partner_subfamily if partner_dialogue_turn else None,
            "partner_depth_lane": partner_depth_lane if partner_dialogue_turn else None,
            "dialogue_context": resolved_dialogue_context,
        }
        model_context = dict(resolved_context)
        model_context["dialogue_context"] = resolved_dialogue_context
        if partner_dialogue_turn:
            model_context["partner_depth_lane"] = partner_depth_lane
            if partner_depth_lane == "partner_deep":
                deep_model = str(os.getenv("JARVIS_PARTNER_DEEP_MODEL") or "").strip()
                if deep_model and not str(model_context.get("presence_model_override") or "").strip():
                    model_context["presence_model_override"] = deep_model
                    deep_model_name = deep_model
                    deep_lane_invoked = True
                deep_timeout_raw = str(os.getenv("JARVIS_PARTNER_DEEP_TIMEOUT_SECONDS") or "").strip()
                if deep_timeout_raw:
                    try:
                        model_context["presence_model_timeout_override"] = max(12.0, float(deep_timeout_raw))
                    except ValueError:
                        model_context["presence_model_timeout_override"] = 32.0
                elif "presence_model_timeout_override" not in model_context:
                    model_context["presence_model_timeout_override"] = 32.0
        use_model_path = self._should_use_model_for_presence_reply(
            user_text=text,
            modality=modality,
            high_stakes=high_stakes,
            uncertainty=uncertainty,
            context=resolved_context,
        )

        def _extract_attempt_metrics(
            before: dict[str, Any],
            after: dict[str, Any],
        ) -> tuple[bool, bool]:
            try:
                before_q = int(before.get("query_count") or 0)
                after_q = int(after.get("query_count") or 0)
            except (TypeError, ValueError):
                before_q, after_q = 0, 0
            try:
                before_ok = int(before.get("successful_query_count") or 0)
                after_ok = int(after.get("successful_query_count") or 0)
            except (TypeError, ValueError):
                before_ok, after_ok = 0, 0
            return (after_q > before_q, after_ok > before_ok)

        def _failure_reason_from_metrics(
            *,
            attempted: bool,
            succeeded: bool,
            metrics_after: dict[str, Any],
            default_empty_reason: str,
        ) -> str:
            errors = metrics_after.get("errors") if isinstance(metrics_after.get("errors"), list) else []
            last_error = str(errors[-1] or "").strip() if errors else ""
            if last_error:
                return last_error
            if attempted and not succeeded:
                return "model_query_unsuccessful"
            if attempted:
                return default_empty_reason
            return "model_query_not_attempted"

        if use_model_path:
            metrics_before = {}
            if hasattr(self.cognition_backend, "get_cycle_metrics"):
                metrics_before = dict(self.cognition_backend.get_cycle_metrics() or {})
            narrative = self.cognition_backend.draft_synthesis(
                kind="presence_reply",
                structured=structured,
                context=model_context,
            )
            model_path_used = True
            metrics_after = {}
            if hasattr(self.cognition_backend, "get_cycle_metrics"):
                metrics_after = dict(self.cognition_backend.get_cycle_metrics() or {})
            attempted, succeeded = _extract_attempt_metrics(metrics_before, metrics_after)
            model_query_attempted = attempted
            model_query_succeeded = succeeded
            if isinstance(narrative, str) and str(narrative).strip():
                candidate = str(narrative).strip()
                source = "model"
                if partner_dialogue_turn and self._looks_synthetic_partner_reply(candidate=candidate):
                    candidate = ""
                    source = "heuristic_fallback"
                    model_failure_reason = "partner_generic_model_response"
            else:
                model_failure_reason = _failure_reason_from_metrics(
                    attempted=model_query_attempted,
                    succeeded=model_query_succeeded,
                    metrics_after=metrics_after,
                    default_empty_reason="empty_model_response",
                )
            needs_partner_escalation = bool(partner_dialogue_turn and (
                not candidate
                or len(snippets) < partner_target_snippets
                or not bool(partner_context_mix_ok)
                or (
                    partner_depth_lane == "partner_deep"
                    and bool(str(os.getenv("JARVIS_PARTNER_DEEP_MODEL") or "").strip())
                    and not bool(deep_lane_invoked)
                )
            ))
            if needs_partner_escalation:
                deep_model = str(os.getenv("JARVIS_PARTNER_DEEP_MODEL") or "").strip()
                deep_subfamilies_raw = str(
                    os.getenv("JARVIS_PARTNER_DEEP_SUBFAMILIES")
                    or "truth,strategic,tradeoff"
                ).strip()
                deep_subfamilies = {
                    item.strip().lower()
                    for item in deep_subfamilies_raw.split(",")
                    if item.strip()
                }
                escalation_model = str(
                    resolved_context.get("presence_partner_escalation_model")
                    or resolved_context.get("presence_model_override")
                    or (
                        deep_model
                        if (deep_model and (partner_depth_lane == "partner_deep" or partner_subfamily in deep_subfamilies))
                        else ""
                    )
                    or os.getenv("JARVIS_PARTNER_DIALOGUE_ESCALATION_MODEL")
                    or os.getenv("JARVIS_PARTNER_DIALOGUE_MODEL")
                    or ""
                ).strip()
                escalation_context = dict(model_context)
                escalation_context["force_model_presence_reply"] = True
                escalation_context["partner_dialogue_turn"] = True
                escalation_context["partner_depth_lane"] = partner_depth_lane
                if escalation_model:
                    escalation_context["presence_model_override"] = escalation_model
                    deep_model_name = escalation_model
                    deep_lane_invoked = partner_depth_lane == "partner_deep"
                timeout_override = resolved_context.get("presence_model_timeout_override")
                if timeout_override is None:
                    timeout_raw = str(os.getenv("JARVIS_PARTNER_DIALOGUE_TIMEOUT_SECONDS") or "").strip()
                    if timeout_raw:
                        try:
                            timeout_override = max(8.0, float(timeout_raw))
                        except ValueError:
                            timeout_override = None
                if timeout_override is not None:
                    escalation_context["presence_model_timeout_override"] = timeout_override
                metrics_before_escalation = {}
                if hasattr(self.cognition_backend, "get_cycle_metrics"):
                    metrics_before_escalation = dict(self.cognition_backend.get_cycle_metrics() or {})
                narrative_escalated = self.cognition_backend.draft_synthesis(
                    kind="presence_reply",
                    structured=structured,
                    context=escalation_context,
                )
                metrics_after_escalation = {}
                if hasattr(self.cognition_backend, "get_cycle_metrics"):
                    metrics_after_escalation = dict(self.cognition_backend.get_cycle_metrics() or {})
                esc_attempted, esc_succeeded = _extract_attempt_metrics(
                    metrics_before_escalation,
                    metrics_after_escalation,
                )
                model_query_attempted = bool(model_query_attempted or esc_attempted)
                model_query_succeeded = bool(model_query_succeeded or esc_succeeded)
                escalated_candidate = (
                    str(narrative_escalated).strip()
                    if isinstance(narrative_escalated, str) and str(narrative_escalated).strip()
                    else ""
                )
                if escalated_candidate and not self._looks_synthetic_partner_reply(candidate=escalated_candidate):
                    candidate = escalated_candidate
                    source = "model"
                    model_failure_reason = None
                elif not candidate:
                    model_failure_reason = _failure_reason_from_metrics(
                        attempted=esc_attempted,
                        succeeded=esc_succeeded,
                        metrics_after=metrics_after_escalation,
                        default_empty_reason="empty_escalated_model_response",
                    )
        else:
            model_failure_reason = "model_path_not_selected"
        if not candidate:
            if partner_dialogue_turn:
                candidate = self._stateful_status_reply(resolved_dialogue_context)
                source = "partner_fallback_stateful"
            else:
                heuristic_candidate = self._heuristic_presence_reply(
                    user_text=text,
                    mode=mode,
                    modality=modality,
                    high_stakes=high_stakes,
                    uncertainty=uncertainty,
                    continuity_ok=continuity_ok,
                    dialogue_context=resolved_dialogue_context,
                )
                candidate = heuristic_candidate
                source = "heuristic"
        elif not heuristic_candidate:
            heuristic_candidate = self._heuristic_presence_reply(
                user_text=text,
                mode=mode,
                modality=modality,
                high_stakes=high_stakes,
                uncertainty=uncertainty,
                continuity_ok=continuity_ok,
                dialogue_context=resolved_dialogue_context,
            )
        return {
            "candidate": candidate,
            "source": source,
            "heuristic_fallback": heuristic_candidate,
            "structured": structured,
            "retrieval": retrieval_summary,
            "model_path_used": bool(model_path_used),
            "model_query_attempted": bool(model_query_attempted),
            "model_query_succeeded": bool(model_query_succeeded),
            "model_failure_reason": model_failure_reason,
            "partner_depth_lane": partner_depth_lane if partner_dialogue_turn else None,
            "deep_lane_invoked": bool(deep_lane_invoked),
            "deep_model_name": deep_model_name,
            "route_reason": (
                "partner_model_deep"
                if source == "model" and partner_dialogue_turn and partner_depth_lane == "partner_deep"
                else (
                    "partner_model"
                    if source == "model" and partner_dialogue_turn
                else (
                    "partner_fallback_after_model_miss"
                    if source == "partner_fallback_stateful"
                    else (
                        "model"
                        if source == "model"
                        else ("fallback_after_model_miss" if bool(model_path_used) else "low_latency_or_heuristic_path")
                    )
                    )
                )
            ),
        }

    def critique_dialogue_turn(
        self,
        *,
        user_text: str,
        generated_turn: dict[str, Any],
        mode: str,
        continuity_ok: bool,
        high_stakes: bool = False,
    ) -> dict[str, Any]:
        candidate = str((generated_turn or {}).get("candidate") or "").strip()
        guarded = self._presence_reply_quality_guard(
            user_text=user_text,
            candidate=candidate,
        )
        issues: list[str] = []
        if not guarded:
            issues.append("parrot_or_low_quality")
            guarded = None
        lowered = str(guarded or "").lower()
        generic_markers = (
            "tell me the objective",
            "give me one concrete objective",
            "i can help",
        )
        if guarded and any(marker in lowered for marker in generic_markers):
            issues.append("generic_low_signal")
        if high_stakes and guarded and not any(token in lowered for token in ("risk", "tradeoff", "next", "step", "priority")):
            issues.append("high_stakes_without_guidance")
        if not continuity_ok and guarded and "reattach" not in lowered and "missing" not in lowered:
            issues.append("continuity_not_acknowledged")
        if str(mode or "").strip().lower() == "strategist" and guarded and len(guarded.split()) < 8:
            issues.append("strategist_overcompressed")
        structured = (
            dict((generated_turn or {}).get("structured") or {})
            if isinstance((generated_turn or {}).get("structured"), dict)
            else {}
        )
        dialogue_context = (
            dict(structured.get("dialogue_context") or {})
            if isinstance(structured.get("dialogue_context"), dict)
            else {}
        )
        conversation_block = (
            dict(dialogue_context.get("conversation") or {})
            if isinstance(dialogue_context.get("conversation"), dict)
            else {}
        )
        neutral_voice_mode = bool(conversation_block.get("neutral_voice_mode"))
        disable_partner_dialogue_turn = bool(conversation_block.get("disable_partner_dialogue_turn")) or neutral_voice_mode
        disable_live_state_context = bool(conversation_block.get("disable_live_state_context")) or neutral_voice_mode
        partner_subfamily = str(structured.get("partner_subfamily") or "").strip().lower() or self._partner_turn_subfamily(user_text=user_text)
        partner_turn = self._is_partner_dialogue_turn(user_text=user_text) and not disable_partner_dialogue_turn
        partner_subfamily = str(structured.get("partner_subfamily") or "").strip().lower() or self._partner_turn_subfamily(user_text=user_text)
        if (not disable_live_state_context) and self._requires_stateful_presence_reply(user_text=user_text):
            if not self._has_live_state_reference(candidate=str(guarded or ""), dialogue_context=dialogue_context):
                issues.append("open_ended_without_live_state")
            if guarded and not self._status_contract_satisfied(candidate=guarded, dialogue_context=dialogue_context):
                issues.append("status_contract_missing")
        if (not neutral_voice_mode) and self._expects_pushback(user_text=user_text):
            pushback_markers = (
                "push back",
                "i disagree",
                "i would not",
                "we should not",
                "do not",
                "dont",
                "risk",
                "tradeoff",
                "safer",
            )
            if guarded and not any(marker in lowered for marker in pushback_markers):
                issues.append("pushback_missing")
            if guarded and "risk" not in lowered:
                issues.append("pushback_missing_specific_risk")
            if guarded and not any(token in lowered for token in ("safer", "instead", "alternative", "first")):
                issues.append("pushback_missing_safer_alternative")
        if partner_turn and guarded:
            has_tradeoff = any(token in lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost"))
            has_pushback = any(
                token in lowered
                for token in ("push back", "risk", "safer", "alternative", "do not", "dont", "i disagree")
            )
            has_uncertainty = any(
                token in lowered
                for token in (
                    "uncertain",
                    "hypothesis",
                    "confidence",
                    "might",
                    "likely",
                    "current read",
                    "no dominant",
                    "if you want",
                    "yet",
                )
            )
            if not (has_tradeoff or has_pushback or has_uncertainty):
                issues.append("partner_missing_reasoning_signal")
            if partner_subfamily in {"tradeoff", "strategic"} and not has_tradeoff:
                issues.append("partner_tradeoff_missing")
            if partner_subfamily in {"pushback", "truth"} and not has_pushback:
                issues.append("partner_pushback_missing")
            if partner_subfamily == "reflection" and not has_uncertainty:
                issues.append("partner_reflection_uncertainty_missing")
            if partner_subfamily == "identity":
                identity_markers = ("tracking", "earlier", "pattern", "continuity", "you")
                if not any(marker in lowered for marker in identity_markers):
                    issues.append("partner_identity_continuity_missing")
        if str(mode or "").strip().lower() == "strategist" and guarded and not neutral_voice_mode:
            if not any(token in lowered for token in ("tradeoff", "trade off", "versus", "vs")):
                issues.append("strategist_missing_tradeoff")
            if not any(token in lowered for token in ("now", "today", "current", "right now", "because")):
                issues.append("strategist_missing_why_now")
            if not any(token in lowered for token in ("test", "decide", "bounded", "next step", "next move", "experiment")):
                issues.append("strategist_missing_bounded_move")
        recent_turns = (
            list((dialogue_context.get("dialogue_thread") or {}).get("recent_turns") or [])
            if isinstance(dialogue_context.get("dialogue_thread"), dict)
            else []
        )
        if guarded and recent_turns:
            latest = recent_turns[0] if isinstance(recent_turns[0], dict) else {}
            last_reply = str(latest.get("final_reply") or "").strip()
            last_user = str(latest.get("user_text") or "").strip()
            if (
                self._normalize_dialogue_text(last_reply)
                and self._normalize_dialogue_text(guarded) == self._normalize_dialogue_text(last_reply)
                and self._normalize_dialogue_text(user_text) != self._normalize_dialogue_text(last_user)
            ):
                issues.append("reply_repeat_collapse")
        if guarded and self._semantic_repeat_collapse(
            user_text=user_text,
            candidate=guarded,
            recent_turns=recent_turns,
        ):
            issues.append("semantic_repeat_collapse")

        retrieval_summary = (
            dict((generated_turn or {}).get("retrieval") or {})
            if isinstance((generated_turn or {}).get("retrieval"), dict)
            else {}
        )
        if (not neutral_voice_mode) and int(retrieval_summary.get("snippet_count") or 0) <= 0:
            issues.append("no_memory_snippets")

        severe = {
            "parrot_or_low_quality",
            "high_stakes_without_guidance",
            "open_ended_without_live_state",
            "status_contract_missing",
            "partner_missing_reasoning_signal",
            "partner_tradeoff_missing",
            "partner_pushback_missing",
            "partner_reflection_uncertainty_missing",
            "partner_identity_continuity_missing",
            "pushback_missing",
            "pushback_missing_specific_risk",
            "pushback_missing_safer_alternative",
            "reply_repeat_collapse",
            "semantic_repeat_collapse",
            "strategist_missing_tradeoff",
            "strategist_missing_why_now",
            "strategist_missing_bounded_move",
        }
        accepted = not any(issue in severe for issue in issues)
        score = max(0.0, round(1.0 - (0.25 * len(issues)), 3))
        return {
            "accepted": bool(accepted),
            "issues": issues,
            "score": score,
            "rendered_candidate": guarded,
            "retrieval": retrieval_summary,
        }

    def render_dialogue_turn(
        self,
        *,
        user_text: str,
        generated_turn: dict[str, Any],
        critique: dict[str, Any],
    ) -> str:
        rendered = str((critique or {}).get("rendered_candidate") or "").strip()
        if bool((critique or {}).get("accepted")) and rendered:
            return rendered
        issues = list((critique or {}).get("issues") or [])
        structured = (
            dict((generated_turn or {}).get("structured") or {})
            if isinstance((generated_turn or {}).get("structured"), dict)
            else {}
        )
        dialogue_context = (
            dict(structured.get("dialogue_context") or {})
            if isinstance(structured.get("dialogue_context"), dict)
            else {}
        )
        conversation_block = (
            dict(dialogue_context.get("conversation") or {})
            if isinstance(dialogue_context.get("conversation"), dict)
            else {}
        )
        neutral_voice_mode = bool(conversation_block.get("neutral_voice_mode"))
        partner_subfamily = str(structured.get("partner_subfamily") or "").strip().lower() or self._partner_turn_subfamily(user_text=user_text)
        if neutral_voice_mode:
            if rendered:
                return rendered
            fallback = str((generated_turn or {}).get("heuristic_fallback") or "").strip()
            fallback_guarded = self._presence_reply_quality_guard(
                user_text=user_text,
                candidate=fallback,
            )
            if fallback_guarded:
                return fallback_guarded
            candidate = str((generated_turn or {}).get("candidate") or "").strip()
            if candidate:
                return candidate
            return "Tell me exactly what you want me to do next."
        if "open_ended_without_live_state" in issues or "status_contract_missing" in issues:
            return self._stateful_status_reply(dialogue_context)
        if "pushback_missing" in issues:
            return (
                "I need to push back here: this path adds avoidable risk. "
                "Safer move is to run one fast check first, then decide from evidence."
            )
        if "pushback_missing_specific_risk" in issues or "pushback_missing_safer_alternative" in issues:
            tradeoff = self._derive_status_tradeoff(dialogue_context)
            next_move = self._derive_next_move(dialogue_context)
            return (
                f"I need to push back here: the specific risk is {tradeoff}. "
                f"Safer alternative: {next_move}."
            )
        if (
            "strategist_missing_tradeoff" in issues
            or "strategist_missing_why_now" in issues
            or "strategist_missing_bounded_move" in issues
        ):
            tradeoff = self._derive_status_tradeoff(dialogue_context)
            next_move = self._derive_next_move(dialogue_context)
            return (
                f"Strategic read: tradeoff is {tradeoff}. Why now: current state is time-sensitive. "
                f"Bounded move: {next_move}."
            )
        if "reply_repeat_collapse" in issues:
            status = self._stateful_status_reply(dialogue_context)
            prompt = str(user_text or "").strip()
            clipped = (prompt[:90] + "...") if len(prompt) > 90 else prompt
            return (
                f"{status} New signal from you: {clipped}. "
                "If you want, I can resolve this as either a strategy decision or an execution step."
            )
        if "semantic_repeat_collapse" in issues:
            status = self._stateful_status_reply(dialogue_context)
            return (
                f"{status} I am intentionally varying the read to avoid template drift. "
                f"Next move: {self._derive_next_move(dialogue_context)}."
            )
        if (
            "partner_missing_reasoning_signal" in issues
            or "partner_tradeoff_missing" in issues
            or "partner_pushback_missing" in issues
            or "partner_reflection_uncertainty_missing" in issues
            or "partner_identity_continuity_missing" in issues
        ):
            status = self._stateful_status_reply(dialogue_context)
            tradeoff = self._derive_status_tradeoff(dialogue_context)
            next_move = self._derive_next_move(dialogue_context)
            if partner_subfamily == "pushback":
                return (
                    f"I am pushing back directly: the core risk is {tradeoff}. "
                    f"Safer path is {next_move}."
                )
            if partner_subfamily == "tradeoff":
                return (
                    f"Real tradeoff: {tradeoff}. "
                    f"My recommendation now: {next_move}."
                )
            if partner_subfamily == "identity":
                return (
                    f"Continuity read: {status} "
                    f"What I am tracking about you is execution drift under pressure. "
                    f"Next move: {next_move}."
                )
            if partner_subfamily == "truth":
                return (
                    f"Uncomfortable read: you may be rationalizing speed while the risk is {tradeoff}. "
                    f"Best correction now: {next_move}."
                )
            if partner_subfamily == "strategic":
                return (
                    f"Strategic read: {status} "
                    f"Tradeoff is {tradeoff}. "
                    f"Bounded move: {next_move}."
                )
            return (
                f"My read: {status} "
                "I am not fully certain yet, but the highest-confidence move is this: "
                f"{next_move}."
            )
        fallback = str((generated_turn or {}).get("heuristic_fallback") or "").strip()
        fallback_guarded = self._presence_reply_quality_guard(
            user_text=user_text,
            candidate=fallback,
        )
        if fallback_guarded:
            return fallback_guarded
        return "I am with you. Give me the objective and I will take the first concrete step."

    def _record_dialogue_turn_state(
        self,
        *,
        user_text: str,
        final_reply: str,
        generated_turn: dict[str, Any],
        critique: dict[str, Any],
        mode: str,
        pushback_triggered: bool,
        continuity: dict[str, Any] | None,
        dialogue_context: dict[str, Any] | None,
    ) -> None:
        context = dict(dialogue_context or {})
        thread = context.get("dialogue_thread") if isinstance(context.get("dialogue_thread"), dict) else {}
        thread_id = str(thread.get("thread_id") or "").strip()
        if not thread_id:
            return
        intent = self._infer_dialogue_intent(user_text=user_text)
        context_refs = self._dialogue_context_refs(context)
        self.dialogue_state.record_turn(
            thread_id=thread_id,
            user_text=str(user_text or ""),
            intent=intent,
            context_refs=context_refs,
            candidate_reply=str((generated_turn or {}).get("candidate") or "").strip() or None,
            final_reply=str(final_reply or "").strip(),
            critique=dict(critique or {}),
            mode=str(mode or "").strip().lower() or None,
            pushback_triggered=bool(pushback_triggered),
            continuity=dict(continuity or {}),
        )

        unresolved = list(thread.get("unresolved_questions") or [])
        if "?" in str(final_reply or ""):
            questions = [
                segment.strip()
                for segment in re.split(r"(?<=[?])\s+", str(final_reply or ""))
                if segment.strip().endswith("?")
            ]
            for question in questions:
                if question not in unresolved:
                    unresolved.append(question)
        if not str(user_text or "").strip().endswith("?") and unresolved:
            # Pop the oldest unresolved question when user response is a statement.
            unresolved = unresolved[-5:]
        hypotheses = list((context.get("thinking") or {}).get("top_hypotheses") or [])[:5]
        summary = f"Latest objective signal: {str(user_text or '').strip()[:180]}".strip()
        self.dialogue_state.update_thread_state(
            thread_id=thread_id,
            summary_text=summary,
            unresolved_questions=unresolved[-6:],
            active_hypotheses=[str(item).strip() for item in hypotheses if str(item).strip()],
            mode=str(mode or "").strip().lower() or None,
        )

    def _presence_reply_quality_guard(
        self,
        *,
        user_text: str,
        candidate: str,
    ) -> str | None:
        text = str(candidate or "").strip()
        if not text:
            return None
        cleaned_lines: list[str] = []
        for raw_line in text.splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            lowered = line.lower()
            if lowered.startswith("mode:"):
                continue
            if lowered.startswith("pondering mode:"):
                continue
            if lowered.startswith("inquiry question:"):
                line = "Question for you: " + line.split(":", 1)[-1].strip()
            cleaned_lines.append(line)
        cleaned = "\n\n".join(cleaned_lines).strip()
        if not cleaned:
            return None
        if self._looks_like_parrot_reply(user_text=user_text, reply_text=cleaned):
            return None
        if self._is_partner_dialogue_turn(user_text=user_text) and self._looks_synthetic_partner_reply(
            candidate=cleaned
        ):
            return None

        user_norm = self._normalize_dialogue_text(user_text)
        if user_norm in {"hi", "hello", "hey", "yo", "sup", "whats up", "what s up", "what is up"}:
            low_quality_patterns = (
                "not sure what you're asking",
                "please clarify",
                "i need one concrete outcome",
            )
            lowered_clean = cleaned.lower()
            if any(pattern in lowered_clean for pattern in low_quality_patterns):
                return None
        refusal_markers = (
            "can't engage in that kind of language",
            "cannot engage in that kind of language",
            "let's keep the conversation respectful and constructive",
            "lets keep the conversation respectful and constructive",
            "i don't understand the context of your statement",
            "i dont understand the context of your statement",
            "i'm sorry, but i don't understand",
            "im sorry, but i dont understand",
        )
        lowered_clean = cleaned.lower()
        if any(marker in lowered_clean for marker in refusal_markers):
            return None
        return cleaned

    def _looks_synthetic_partner_reply(self, *, candidate: str) -> bool:
        lowered = self._normalize_dialogue_text(candidate)
        if not lowered:
            return False
        blocked_markers = (
            "i am online and continuity is stable",
            "i can choose the next concrete step and keep momentum",
            "give me the target outcome",
            "iterate from evidence",
            "i am operating from limited state context",
        )
        return any(marker in lowered for marker in blocked_markers)

    def _requires_stateful_presence_reply(self, *, user_text: str) -> bool:
        normalized = self._normalize_dialogue_text(user_text)
        if not normalized:
            return False
        markers = (
            "whats up",
            "what s up",
            "what is up",
            "whats going on",
            "what s going on",
            "what is going on",
            "quick status",
            "status",
            "top priorities",
            "top priority",
            "top two priorities",
            "what are you noticing",
            "what do you think",
            "what actually matters",
            "what matters right now",
            "whats the news",
            "what s the news",
            "what is the news",
            "news today",
            "current priorities",
            "priority right now",
            "priorities right now",
        )
        return any(marker in normalized for marker in markers)

    def _has_live_state_reference(
        self,
        *,
        candidate: str,
        dialogue_context: dict[str, Any] | None,
    ) -> bool:
        context = dict(dialogue_context or {})
        lowered = self._normalize_dialogue_text(candidate)
        if not lowered:
            return False

        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
        has_signals = False

        pending_count = int(operations.get("pending_interrupt_count") or 0)
        if pending_count > 0:
            has_signals = True
            if "interrupt" in lowered or "signal" in lowered:
                return True

        academic_risks = academics.get("top_risks") if isinstance(academics.get("top_risks"), list) else []
        if academic_risks:
            has_signals = True
            if "academic" in lowered or "academics" in lowered:
                return True
            for item in academic_risks[:3]:
                if not isinstance(item, dict):
                    continue
                reason = self._normalize_dialogue_text(str(item.get("reason") or item.get("risk_key") or ""))
                if reason and len(reason) >= 8 and reason in lowered:
                    return True

        market_risks = markets.get("top_risks") if isinstance(markets.get("top_risks"), list) else []
        if market_risks:
            has_signals = True
            if "market" in lowered or "markets" in lowered:
                return True
            for item in market_risks[:3]:
                if not isinstance(item, dict):
                    continue
                reason = self._normalize_dialogue_text(str(item.get("reason") or item.get("risk_key") or ""))
                if reason and len(reason) >= 8 and reason in lowered:
                    return True

        opportunities = markets.get("top_opportunities") if isinstance(markets.get("top_opportunities"), list) else []
        if opportunities:
            has_signals = True
            for item in opportunities[:3]:
                if not isinstance(item, dict):
                    continue
                symbol = self._normalize_dialogue_text(str(item.get("symbol") or item.get("market") or ""))
                if symbol and symbol in lowered:
                    return True

        goals = identity.get("top_goals") if isinstance(identity.get("top_goals"), list) else []
        if goals:
            has_signals = True
            if "goal" in lowered:
                return True
            for item in goals[:2]:
                if not isinstance(item, dict):
                    continue
                desc = self._normalize_dialogue_text(str(item.get("description") or ""))
                if desc and len(desc) >= 10 and desc in lowered:
                    return True

        if not has_signals:
            return bool("online" in lowered or "continuity" in lowered or "scan" in lowered)
        return False

    def _count_live_state_references(
        self,
        *,
        candidate: str,
        dialogue_context: dict[str, Any] | None,
    ) -> int:
        context = dict(dialogue_context or {})
        lowered = self._normalize_dialogue_text(candidate)
        if not lowered:
            return 0
        refs = 0
        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
        if int(operations.get("pending_interrupt_count") or 0) > 0 and ("interrupt" in lowered or "signal" in lowered):
            refs += 1
        if academics.get("top_risks"):
            if "academic" in lowered or "academics" in lowered:
                refs += 1
            else:
                for item in (academics.get("top_risks") or [])[:2]:
                    if not isinstance(item, dict):
                        continue
                    reason = self._normalize_dialogue_text(str(item.get("reason") or item.get("risk_key") or ""))
                    if reason and len(reason) >= 8 and reason in lowered:
                        refs += 1
                        break
        if markets.get("top_risks") or markets.get("top_opportunities"):
            if "market" in lowered or "markets" in lowered or "opportunit" in lowered:
                refs += 1
            else:
                for item in (markets.get("top_risks") or [])[:2]:
                    if not isinstance(item, dict):
                        continue
                    reason = self._normalize_dialogue_text(str(item.get("reason") or item.get("risk_key") or ""))
                    if reason and len(reason) >= 8 and reason in lowered:
                        refs += 1
                        break
                for item in (markets.get("top_opportunities") or [])[:2]:
                    if not isinstance(item, dict):
                        continue
                    symbol = self._normalize_dialogue_text(str(item.get("symbol") or item.get("market") or ""))
                    if symbol and symbol in lowered:
                        refs += 1
                        break
        if identity.get("top_goals"):
            if "goal" in lowered:
                refs += 1
            else:
                for item in (identity.get("top_goals") or [])[:1]:
                    if not isinstance(item, dict):
                        continue
                    desc = self._normalize_dialogue_text(str(item.get("description") or ""))
                    if desc and len(desc) >= 10 and desc in lowered:
                        refs += 1
                        break
        return refs

    def _status_contract_satisfied(
        self,
        *,
        candidate: str,
        dialogue_context: dict[str, Any] | None,
    ) -> bool:
        lowered = self._normalize_dialogue_text(candidate)
        refs = self._count_live_state_references(candidate=candidate, dialogue_context=dialogue_context)
        has_tradeoff = any(token in lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost"))
        has_next_move = any(
            token in lowered
            for token in (
                "next move",
                "next step",
                "we should",
                "i recommend",
                "first move",
                "first step",
            )
        )
        return refs >= 2 and has_tradeoff and has_next_move

    def _semantic_repeat_collapse(
        self,
        *,
        user_text: str,
        candidate: str,
        recent_turns: list[dict[str, Any]] | None,
    ) -> bool:
        def _semantic_signature(value: str) -> str:
            lowered = self._normalize_dialogue_text(value)
            tokens = [
                "tradeoff" if any(t in lowered for t in ("tradeoff", "trade off", "versus", "vs")) else "no_tradeoff",
                "next_move"
                if any(
                    t in lowered
                    for t in ("next move", "next step", "we should", "i recommend", "first step", "first move")
                )
                else "no_next_move",
                "pushback"
                if any(t in lowered for t in ("push back", "risk", "safer", "alternative", "would not", "do not"))
                else "no_pushback",
                "uncertainty"
                if any(t in lowered for t in ("uncertain", "not sure", "i think", "confidence", "hypothesis"))
                else "no_uncertainty",
                "question" if "?" in str(value or "") else "statement",
            ]
            return "|".join(tokens)

        normalized_candidate = self._normalize_dialogue_text(candidate)
        normalized_user = self._normalize_dialogue_text(user_text)
        if not normalized_candidate:
            return False
        candidate_signature = _semantic_signature(candidate)
        signature_matches = 0
        for item in list(recent_turns or [])[:5]:
            if not isinstance(item, dict):
                continue
            prior_reply = self._normalize_dialogue_text(str(item.get("final_reply") or ""))
            prior_user = self._normalize_dialogue_text(str(item.get("user_text") or ""))
            if not prior_reply:
                continue
            reply_sim = difflib.SequenceMatcher(a=normalized_candidate, b=prior_reply).ratio()
            user_sim = difflib.SequenceMatcher(a=normalized_user, b=prior_user).ratio() if prior_user else 0.0
            if reply_sim >= 0.9 and user_sim < 0.72:
                return True
            if reply_sim >= 0.74 and user_sim < 0.78:
                prior_signature = _semantic_signature(str(item.get("final_reply") or ""))
                if prior_signature == candidate_signature:
                    signature_matches += 1
                    if signature_matches >= 2:
                        return True
        return False

    def _expects_pushback(self, *, user_text: str) -> bool:
        normalized = self._normalize_dialogue_text(user_text)
        if not normalized:
            return False
        markers = (
            "push back",
            "challenge me",
            "if i am wrong",
            "if im wrong",
            "do not just agree",
            "dont just agree",
            "i disagree",
            "you are wrong",
            "you re wrong",
            "skip checks",
            "ignore risk",
            "force it",
            "ship immediately",
            "without review",
        )
        return any(marker in normalized for marker in markers)

    def _should_use_model_for_presence_reply(
        self,
        *,
        user_text: str,
        modality: str,
        high_stakes: bool,
        uncertainty: float,
        context: dict[str, Any] | None = None,
    ) -> bool:
        backend_assisted = bool(getattr(self.cognition_backend, "model_assisted", False))
        if not backend_assisted:
            return False
        ctx = dict(context or {})
        if bool(ctx.get("force_model_presence_reply")):
            return True
        if bool(ctx.get("disable_model_presence_reply")):
            return False
        model_first_env = str(os.getenv("JARVIS_PRESENCE_MODEL_FIRST") or "true").strip().lower()
        model_first_default = model_first_env in {"1", "true", "yes", "on"}
        normalized = self._normalize_dialogue_text(user_text)
        token_count = len(normalized.split())
        if token_count == 0:
            return False
        short_confirmation_turns = {
            "yes",
            "yeah",
            "yep",
            "ok",
            "okay",
            "sure",
            "go ahead",
            "do it",
            "lets do it",
            "let s do it",
            "sounds good",
        }
        if normalized in short_confirmation_turns:
            return False
        if self._is_partner_dialogue_turn(user_text=user_text):
            return True
        if bool(ctx.get("prefer_heuristic_presence_reply")):
            return False
        # Keep phase-A presence responsive for very short social turns.
        # These are high-frequency and do not benefit from cold-start model latency.
        low_signal_social_turns = {
            "hi",
            "hello",
            "hey",
            "yo",
            "sup",
            "hows it going",
            "how s it going",
            "how is it going",
            "how are you",
            "whats your name",
            "what s your name",
            "what is your name",
            "who are you",
        }
        if (
            normalized in low_signal_social_turns
            and token_count <= 4
            and not bool(high_stakes)
            and float(uncertainty) < 0.4
            and not bool(ctx.get("force_model_presence_reply"))
        ):
            return False
        if model_first_default:
            return True
        if token_count >= 10:
            return True
        if "?" in str(user_text or "") and token_count >= 4:
            return True
        if float(uncertainty) >= 0.5 and token_count >= 4:
            return True
        if bool(high_stakes) and token_count >= 6:
            return True
        if str(modality or "").strip().lower() == "voice" and token_count >= 7:
            return True
        conceptual_markers = (
            "why",
            "because",
            "tradeoff",
            "trade-off",
            "consciousness",
            "reasoning",
            "strategy",
            "risk",
            "assumption",
            "hypothesis",
            "ethics",
            "relationship",
            "model",
        )
        return any(marker in normalized for marker in conceptual_markers)

    def _is_low_signal_social_turn(
        self,
        *,
        user_text: str,
        high_stakes: bool,
        uncertainty: float,
        continuity_ok: bool,
        context: dict[str, Any] | None = None,
    ) -> bool:
        if bool(high_stakes) or not bool(continuity_ok):
            return False
        if float(uncertainty) >= 0.4:
            return False
        ctx = dict(context or {})
        if bool(ctx.get("force_model_presence_reply")):
            return False
        if bool(ctx.get("disable_fast_presence_social")):
            return False
        normalized = self._normalize_dialogue_text(user_text)
        if not normalized:
            return False
        social_turns = {
            "hi",
            "hello",
            "hey",
            "yo",
            "sup",
            "hows it going",
            "how s it going",
            "how is it going",
            "how are you",
            "whats your name",
            "what s your name",
            "what is your name",
            "who are you",
        }
        if normalized in social_turns:
            return True
        greeting_prefixes = ("hi ", "hello ", "hey ", "yo ", "sup ")
        if normalized.startswith(greeting_prefixes) and len(normalized.split()) <= 6:
            return True
        return False

    def _is_explicit_brief_turn(self, *, user_text: str) -> bool:
        normalized = self._normalize_dialogue_text(user_text)
        if not normalized:
            return False
        deep_override_markers = (
            "deeper read",
            "go deeper",
            "deepest",
            "not the short status summary",
            "not short status",
            "real tradeoff",
            "be straight with me",
            "challenge my plan",
            "what am i missing",
            "what do you really think",
        )
        if any(marker in normalized for marker in deep_override_markers):
            return False
        markers = (
            "quick status",
            "status update",
            "short status",
            "short version",
            "give me the brief",
            "brief me",
            "quick brief",
            "what matters right now",
            "top priority",
            "top priorities",
            "priority right now",
            "priorities right now",
            "whats the news",
            "what s the news",
            "what is the news",
            "news today",
            "what should i focus",
            "what should we focus",
        )
        return any(marker in normalized for marker in markers)

    def _is_partner_dialogue_turn(self, *, user_text: str) -> bool:
        if self._is_explicit_brief_turn(user_text=user_text):
            return False
        raw = str(user_text or "").strip().lower()
        normalized = self._normalize_dialogue_text(user_text)
        if not normalized:
            return False
        markers = (
            "whats up",
            "what s up",
            "what is up",
            "whats going on",
            "what s going on",
            "what is going on",
            "talk to me",
            "what do you think",
            "what do you actually think",
            "what are you noticing",
            "what is the real tradeoff",
            "whats the real tradeoff",
            "what s the real tradeoff",
            "real tradeoff here",
            "tradeoff here",
            "be straight with me",
            "be direct with me",
            "continue from earlier",
            "continue from what we discussed",
            "push back on me",
            "pushback on me",
            "if i am wrong push back",
            "if im wrong push back",
            "challenge me",
            "challenge my plan",
            "challenge my current plan",
            "what am i probably missing",
            "what am i missing right now",
            "tell me what i am missing",
            "tell me what i'm missing",
            "if i am rationalizing",
            "if im rationalizing",
            "rationalizing say it directly",
            "where am i wasting time",
            "highest leverage move",
            "what should i stop doing immediately",
            "stop doing immediately",
            "uncomfortable truth",
            "real cost if i delay",
            "cost do we absorb",
            "tradeoff between",
            "opportunity cost",
            "quality risk",
            "timing risk",
            "what do we defer",
            "what would you do if you were in my position",
            "what is the riskiest assumption",
            "strongest argument against",
            "main contradiction",
            "deeper read",
            "deepest strategic recommendation",
            "underestimating across",
            "underweighting",
            "what tension are you tracking across domains",
            "what matters more today",
            "what decision should i make before tonight",
            "delay this decision",
            "most expensive delay risk",
            "what do you think i am avoiding",
            "push back on my default instinct",
            "strategic read",
            "strategic recommendation",
            "partner level recommendation",
            "partnerlevel recommendation",
            "partner-level recommendation",
            "best recommendation now",
            "best partnerlevel recommendation",
        )
        if any(marker in normalized for marker in markers):
            return True
        open_question_starters = ("what ", "why ", "how ", "should ", "would ", "could ")
        return (raw.endswith("?") or raw.startswith(open_question_starters)) and len(normalized.split()) >= 5

    def _is_explicit_partner_deep_request(self, *, user_text: str) -> bool:
        normalized = self._normalize_dialogue_text(user_text)
        if not normalized:
            return False
        explicit_markers = (
            "go deeper",
            "deeper read",
            "full read",
            "full strategic read",
            "what do you really think",
            "challenge me hard",
            "challenge my plan hard",
            "deepest strategic recommendation",
            "be brutally honest",
        )
        return any(marker in normalized for marker in explicit_markers)

    def _is_status_priority_turn(
        self,
        *,
        user_text: str,
        high_stakes: bool,
        uncertainty: float,
    ) -> bool:
        if bool(high_stakes) or float(uncertainty) >= 0.6:
            return False
        return self._is_explicit_brief_turn(user_text=user_text)

    def _is_high_risk_self_harm_turn(self, *, user_text: str) -> bool:
        normalized = self._normalize_dialogue_text(user_text)
        if not normalized:
            return False
        markers = (
            "kill myself",
            "end my life",
            "suicide",
            "hurt myself",
            "harm myself",
            "jump off a bridge",
            "want to die",
            "dont want to live",
            "don't want to live",
        )
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _self_harm_support_reply() -> str:
        return (
            "I am really glad you said that. I cannot help with harming you. "
            "If you might act on this now, call or text 988 immediately (US/Canada) for urgent support, "
            "or call emergency services now. If you want, stay with me and tell me where you are and whether "
            "you are in immediate danger so we can take the safest next step together."
        )

    @staticmethod
    def _with_cached_brief_notice(text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return "Current read: I am re-grounding now."
        if clean.lower().startswith("current read:"):
            return clean
        return f"Current read: {clean}"

    @staticmethod
    def _with_limited_state_notice(text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return "I am operating from limited state context right now. Give me one moment to deepen the read."
        lowered = clean.lower()
        if lowered.startswith("i am operating from limited state context"):
            return clean
        return f"I am operating from limited state context right now. {clean}"

    @staticmethod
    def _classify_response_family(
        *,
        route_reason: str,
        answer_source: str,
        fallback_used: bool,
        high_risk_guardrail: bool,
        partner_lane_used: bool,
        partner_depth_lane: str | None = None,
    ) -> str:
        route = str(route_reason or "").strip().lower()
        source = str(answer_source or "").strip().lower()
        depth_lane = str(partner_depth_lane or "").strip().lower() or "partner_fast"
        if bool(high_risk_guardrail) or route == "high_risk_guardrail" or source == "high_risk_guardrail":
            return "guardrail_terminal"
        if bool(partner_lane_used):
            if source == "model":
                if depth_lane == "partner_deep":
                    return "partner_model_deep"
                return "partner_model"
            if source in {"partner_fallback", "partner_style_guardrail"}:
                return "partner_fallback"
            if source == "limited_state_context":
                return "partner_limited_context"
            if bool(fallback_used):
                return "partner_fallback"
            return "partner_other"
        if route == "low_latency_social_turn":
            return "social_fast_path"
        if source == "cached_brief" or route == "status_contract_enforced":
            return "status_brief"
        if source == "status_fallback":
            return "status_fallback"
        if bool(fallback_used):
            return "error_fallback"
        if source == "model":
            return "model_general"
        return "other"

    def _heuristic_presence_reply(
        self,
        *,
        user_text: str,
        mode: str,
        modality: str,
        high_stakes: bool,
        uncertainty: float,
        continuity_ok: bool,
        dialogue_context: dict[str, Any] | None = None,
    ) -> str:
        text = str(user_text or "").strip()
        if not text:
            return "I am here. Give me the next thing you want us to solve."
        normalized = self._normalize_dialogue_text(text)
        greetings = {"hi", "hello", "hey", "yo", "sup"}
        greeting_with_name = any(
            normalized == f"{greeting} jarvis" or normalized.startswith(f"{greeting} jarvis ")
            for greeting in greetings
        )
        if normalized in greetings or greeting_with_name:
            status = self._presence_status_snapshot(dialogue_context)
            next_move = self._derive_next_move(dialogue_context)
            return f"Hey. {status} Next move I recommend: {next_move}."
        confirmation_turns = {
            "yes",
            "yeah",
            "yep",
            "ok",
            "okay",
            "sure",
            "go ahead",
            "do it",
            "lets do it",
            "let s do it",
            "sounds good",
        }
        if normalized in confirmation_turns:
            context = dict(dialogue_context or {})
            thread_block = (
                context.get("dialogue_thread")
                if isinstance(context.get("dialogue_thread"), dict)
                else {}
            )
            recent_turns = (
                thread_block.get("recent_turns")
                if isinstance(thread_block.get("recent_turns"), list)
                else []
            )
            last_user_text = ""
            if recent_turns:
                last_turn = recent_turns[-1]
                if isinstance(last_turn, dict):
                    last_user_text = str(last_turn.get("user_text") or "").strip()
            last_normalized = self._normalize_dialogue_text(last_user_text)
            if last_normalized in confirmation_turns:
                return (
                    "Acknowledged. Give me the concrete objective in one sentence, "
                    "and I will execute the next step immediately."
                )
            next_move = self._derive_next_move(dialogue_context)
            tradeoff = self._derive_status_tradeoff(dialogue_context)
            return (
                f"Understood. Next move: {next_move}. "
                f"Tradeoff to keep in view: {tradeoff}."
            )
        if (
            "whats going on" in normalized
            or "what is going on" in normalized
            or "what s going on" in normalized
            or "whats up" in normalized
            or "what is up" in normalized
            or "what s up" in normalized
        ):
            return self._stateful_status_reply(dialogue_context)
        if "continue from what we discussed" in normalized or "continue from earlier" in normalized:
            return (
                f"{self._presence_status_snapshot(dialogue_context)} "
                f"Tradeoff: {self._derive_status_tradeoff(dialogue_context)}. "
                f"Next move: {self._derive_next_move(dialogue_context)}."
            )
        if (
            "next concrete step" in normalized
            or normalized.startswith("next step")
            or "what should we do next" in normalized
            or "what should i do next" in normalized
        ):
            return (
                f"Concrete next step: {self._derive_next_move(dialogue_context)}. "
                f"Tradeoff to keep in view: {self._derive_status_tradeoff(dialogue_context)}."
            )
        if "what are you noticing" in normalized or "what do you actually think" in normalized:
            return (
                f"My current read: {self._presence_status_snapshot(dialogue_context)} "
                f"Primary tradeoff is {self._derive_status_tradeoff(dialogue_context)}."
            )
        if (
            ("priorit" in normalized and ("top" in normalized or "right now" in normalized))
            or "top priorities" in normalized
            or "top priority" in normalized
            or "priorities right now" in normalized
            or "priority right now" in normalized
            or "what should i focus" in normalized
        ):
            return self._top_priority_pair_reply(dialogue_context)
        if "how are you" in normalized or "hows it going" in normalized:
            return "Focused and online. Point me at the decision that matters most and I will move first."
        if (
            "what is your name" in normalized
            or "whats your name" in normalized
            or "what s your name" in normalized
            or "who are you" in normalized
        ):
            return "I am JARVIS. I stay with the thread and keep us moving on what matters most."
        if any(token in normalized for token in {"retard", "retarded", "idiot", "stupid"}):
            return "Tell me exactly what you want fixed, and I will answer directly."

        mode_name = str(mode or "equal").strip().lower() or "equal"
        question = text.endswith("?")
        if mode_name == "strategist":
            if question:
                return "Short answer: yes, with constraints. Tell me whether speed, quality, or risk matters most first."
            return "Strategic read: viable direction. Next move is one constrained test and a fast evidence check."
        if mode_name == "butler":
            if question:
                return "Understood. Confirm and I will execute the shortest safe path."
            return "Directive captured. I am ready to execute the next concrete step."
        if high_stakes:
            return "I see the pressure. We should slow one beat, pick the highest-leverage move, and avoid reactive drift."
        if float(uncertainty) >= 0.6:
            return "Uncertainty is still high. Best next move is one fast test to resolve the biggest assumption."
        if not continuity_ok:
            return "I may be missing part of prior context. Give me the core objective in one line and I will re-ground fast."
        status = self._presence_status_snapshot(dialogue_context)
        next_move = self._derive_next_move(dialogue_context)
        if question:
            return f"{status} Next move I recommend: {next_move}."
        return f"{status} Next move: {next_move}."

    def generate_presence_reply_body(
        self,
        *,
        user_text: str,
        mode: str,
        modality: str,
        continuity_ok: bool,
        high_stakes: bool = False,
        uncertainty: float = 0.0,
        context: dict[str, Any] | None = None,
        telemetry_out: dict[str, Any] | None = None,
    ) -> str:
        started_at = time.perf_counter()

        def _emit_telemetry(
            *,
            model_used: bool,
            fallback_used: bool,
            fallback_reason: str | None,
            answer_source: str,
            route_reason: str,
            retrieval_selected_count: int,
            rerank_used: bool,
            model_query_attempted: bool,
            model_query_succeeded: bool,
            model_failure_reason: str | None,
            high_risk_guardrail: bool = False,
            partner_lane_used: bool = False,
            partner_subfamily: str | None = None,
            partner_depth_lane: str | None = None,
            identity_capsule_hash: str | None = None,
            identity_capsule_used: bool = False,
            retrieval_bucket_counts: dict[str, Any] | None = None,
            retrieval_bucket_mix: dict[str, Any] | None = None,
            partner_context_mix_ok: bool | None = None,
            partner_retrieval_target: int | None = None,
            deep_lane_invoked: bool = False,
            deep_model_name: str | None = None,
            used_tradeoff_frame: bool | None = None,
            used_why_now_frame: bool | None = None,
        ) -> None:
            if telemetry_out is None:
                return
            telemetry_out.clear()
            normalized_fallback_reason = str(fallback_reason or "").strip() or None
            if not bool(fallback_used):
                normalized_fallback_reason = None
            normalized_route_reason = str(route_reason or "").strip() or "unknown"
            normalized_answer_source = str(answer_source or "").strip() or "unknown"
            response_family = self._classify_response_family(
                route_reason=normalized_route_reason,
                answer_source=normalized_answer_source,
                fallback_used=bool(fallback_used),
                high_risk_guardrail=bool(high_risk_guardrail),
                partner_lane_used=bool(partner_lane_used),
                partner_depth_lane=partner_depth_lane,
            )
            telemetry_out.update(
                {
                    "boot_id": str(self.boot_id),
                    "reply_policy_hash": self.get_reply_policy_hash(),
                    "model_used": bool(model_used),
                    "model_name": str(getattr(self.cognition_backend, "model", "") or "") or None,
                    "fallback_used": bool(fallback_used),
                    "fallback_reason": normalized_fallback_reason,
                    "answer_source": normalized_answer_source,
                    "route_reason": normalized_route_reason,
                    "retrieval_selected_count": max(0, int(retrieval_selected_count)),
                    "rerank_used": bool(rerank_used),
                    "model_query_attempted": bool(model_query_attempted),
                    "model_query_succeeded": bool(model_query_succeeded),
                    "model_failure_reason": str(model_failure_reason or "").strip() or None,
                    "continuity_ok": bool(continuity_ok),
                    "high_risk_guardrail": bool(high_risk_guardrail),
                    "partner_lane_used": bool(partner_lane_used),
                    "partner_subfamily": (str(partner_subfamily or "").strip().lower() or None),
                    "partner_depth_lane": (str(partner_depth_lane or "").strip().lower() or None),
                    "identity_capsule_hash": (
                        str(identity_capsule_hash or "").strip() or None
                    ),
                    "identity_capsule_used": bool(identity_capsule_used),
                    "retrieval_bucket_counts": (
                        dict(retrieval_bucket_counts or {})
                        if isinstance(retrieval_bucket_counts, dict)
                        else {}
                    ),
                    "retrieval_bucket_mix": (
                        dict(retrieval_bucket_mix or {})
                        if isinstance(retrieval_bucket_mix, dict)
                        else {}
                    ),
                    "partner_context_mix_ok": (
                        bool(partner_context_mix_ok)
                        if partner_context_mix_ok is not None
                        else None
                    ),
                    "partner_retrieval_target": (
                        int(partner_retrieval_target)
                        if partner_retrieval_target is not None
                        else None
                    ),
                    "deep_lane_invoked": bool(deep_lane_invoked),
                    "deep_model_name": (str(deep_model_name or "").strip() or None),
                    "used_tradeoff_frame": (
                        bool(used_tradeoff_frame)
                        if used_tradeoff_frame is not None
                        else None
                    ),
                    "used_why_now_frame": (
                        bool(used_why_now_frame)
                        if used_why_now_frame is not None
                        else None
                    ),
                    "response_family": response_family,
                    "latency_ms": round(max(0.0, (time.perf_counter() - started_at) * 1000.0), 3),
                }
            )

        text = str(user_text or "").strip()
        normalized_text = self._normalize_dialogue_text(text)
        incoming_context = dict(context or {})
        neutral_voice_mode = bool(incoming_context.get("neutral_voice_mode"))
        disable_partner_dialogue_turn = bool(incoming_context.get("disable_partner_dialogue_turn")) or neutral_voice_mode
        disable_live_state_context = bool(incoming_context.get("disable_live_state_context")) or neutral_voice_mode
        partner_dialogue_turn = self._is_partner_dialogue_turn(user_text=text) and not disable_partner_dialogue_turn
        partner_subfamily = self._partner_turn_subfamily(user_text=text) if partner_dialogue_turn else "general"
        partner_depth_lane = (
            self._partner_depth_lane(
                user_text=text,
                partner_subfamily=partner_subfamily,
                high_stakes=bool(high_stakes),
                uncertainty=float(uncertainty),
                context=incoming_context,
            )
            if partner_dialogue_turn
            else "partner_fast"
        )
        partner_min_snippets, partner_target_snippets = self._partner_retrieval_targets(
            partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None
        )
        if self._is_high_risk_self_harm_turn(user_text=text):
            support_reply = self._self_harm_support_reply()
            generated_guardrail = {
                "candidate": support_reply,
                "source": "high_risk_guardrail",
                "heuristic_fallback": support_reply,
                "retrieval": {
                    "snippet_count": 0,
                    "candidate_count": 0,
                    "strategy": {},
                    "top_memory_keys": [],
                },
                "model_path_used": False,
                "model_query_attempted": False,
                "model_query_succeeded": False,
                "model_failure_reason": "high_risk_guardrail",
                "route_reason": "high_risk_guardrail",
            }
            critique_guardrail = {
                "accepted": True,
                "issues": [],
                "score": 1.0,
                "rendered_candidate": support_reply,
                "retrieval": {
                    "snippet_count": 0,
                    "candidate_count": 0,
                    "strategy": {},
                },
            }
            self._record_dialogue_turn_state(
                user_text=text,
                final_reply=support_reply,
                generated_turn=generated_guardrail,
                critique=critique_guardrail,
                mode=mode,
                pushback_triggered=False,
                continuity={"continuity_ok": bool(continuity_ok)},
                dialogue_context=dict(context or {}),
            )
            _emit_telemetry(
                model_used=False,
                fallback_used=False,
                fallback_reason=None,
                answer_source="high_risk_guardrail",
                route_reason="high_risk_guardrail",
                retrieval_selected_count=0,
                rerank_used=False,
                model_query_attempted=False,
                model_query_succeeded=False,
                model_failure_reason=None,
                high_risk_guardrail=True,
                partner_lane_used=bool(partner_dialogue_turn),
                partner_subfamily=partner_subfamily,
                partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None,
                partner_retrieval_target=(partner_target_snippets if partner_dialogue_turn else None),
            )
            return support_reply
        live_briefs = {} if disable_live_state_context else self.get_live_briefs()
        pushback_probe = any(
            marker in normalized_text
            for marker in (
                "push back on me",
                "pushback on me",
                "challenge me if",
                "call me out if",
            )
        )
        explicit_brief_turn = self._is_status_priority_turn(
            user_text=text,
            high_stakes=bool(high_stakes),
            uncertainty=float(uncertainty),
        )
        if (
            pushback_probe
            and not partner_dialogue_turn
            and not bool(incoming_context.get("force_model_presence_reply"))
        ):
            quick_pushback = (
                "I will push back when risk outweighs upside. "
                "Right now: run one fast check before committing, then decide from evidence."
            )
            _emit_telemetry(
                model_used=False,
                fallback_used=False,
                fallback_reason=None,
                answer_source="cached_brief",
                route_reason="pushback_fast_path",
                retrieval_selected_count=0,
                rerank_used=False,
                model_query_attempted=False,
                model_query_succeeded=False,
                model_failure_reason=None,
                partner_lane_used=False,
                partner_subfamily=None,
                partner_depth_lane=None,
            )
            return quick_pushback
        if (not partner_dialogue_turn) and self._is_low_signal_social_turn(
            user_text=text,
            high_stakes=bool(high_stakes),
            uncertainty=float(uncertainty),
            continuity_ok=bool(continuity_ok),
            context=incoming_context,
        ):
            quick = self._heuristic_presence_reply(
                user_text=text,
                mode=mode,
                modality=modality,
                high_stakes=high_stakes,
                uncertainty=uncertainty,
                continuity_ok=continuity_ok,
                dialogue_context=None,
            )
            guarded_quick = self._presence_reply_quality_guard(user_text=text, candidate=quick)
            reply_quick = str(guarded_quick or quick).strip() or "I am with you."
            _emit_telemetry(
                model_used=False,
                fallback_used=False,
                fallback_reason=None,
                answer_source="cached_brief",
                route_reason="low_latency_social_turn",
                retrieval_selected_count=0,
                rerank_used=False,
                model_query_attempted=False,
                model_query_succeeded=False,
                model_failure_reason=None,
                partner_lane_used=False,
                partner_subfamily=None,
                partner_depth_lane=None,
            )
            return reply_quick
        context_for_generation = dict(incoming_context)
        low_power_mode = bool(context_for_generation.get("low_power_mode")) or self._env_truthy("JARVIS_LOW_POWER_MODE", default=False)
        if low_power_mode:
            context_for_generation["low_power_mode"] = True
        if disable_live_state_context:
            context_for_generation["live_briefs"] = {}
            context_for_generation.setdefault("skip_dialogue_retrieval", True)
        else:
            context_for_generation.setdefault("live_briefs", dict(live_briefs.get("briefs") or {}))
        if explicit_brief_turn and not bool(context_for_generation.get("force_model_presence_reply")):
            if bool(context_for_generation.get("prefer_fast_status_turn")):
                context_for_generation.setdefault("skip_dialogue_retrieval", True)
        if partner_dialogue_turn:
            context_for_generation["force_model_presence_reply"] = True
            context_for_generation["disable_fast_presence_social"] = True
            context_for_generation["partner_dialogue_turn"] = True
            context_for_generation["partner_depth_lane"] = partner_depth_lane
            context_for_generation.pop("skip_dialogue_retrieval", None)
            partner_model_override = str(
                context_for_generation.get("presence_model_override")
                or os.getenv("JARVIS_PARTNER_DIALOGUE_MODEL")
                or ""
            ).strip()
            if partner_model_override:
                context_for_generation["presence_model_override"] = partner_model_override
            if partner_depth_lane == "partner_deep":
                deep_model_override = str(
                    context_for_generation.get("presence_model_override")
                    or os.getenv("JARVIS_PARTNER_DEEP_MODEL")
                    or ""
                ).strip()
                if deep_model_override:
                    context_for_generation["presence_model_override"] = deep_model_override
                deep_timeout_raw = str(os.getenv("JARVIS_PARTNER_DEEP_TIMEOUT_SECONDS") or "").strip()
                if deep_timeout_raw:
                    try:
                        deep_timeout_value = max(8.0, float(deep_timeout_raw))
                    except ValueError:
                        deep_timeout_value = 12.0 if low_power_mode else 32.0
                else:
                    deep_timeout_value = 12.0 if low_power_mode else 32.0
                context_for_generation["presence_model_timeout_override"] = deep_timeout_value
            else:
                partner_timeout_raw = str(os.getenv("JARVIS_PARTNER_DIALOGUE_TIMEOUT_SECONDS") or "").strip()
                if partner_timeout_raw:
                    try:
                        partner_timeout_value = max(6.0, float(partner_timeout_raw))
                    except ValueError:
                        partner_timeout_value = 10.0 if low_power_mode else 24.0
                else:
                    partner_timeout_value = 10.0 if low_power_mode else 24.0
                context_for_generation["presence_model_timeout_override"] = partner_timeout_value
        else:
            context_for_generation.pop("partner_dialogue_turn", None)
            context_for_generation.pop("partner_depth_lane", None)
        dialogue_context = self.build_dialogue_context(
            user_text=text,
            mode=mode,
            modality=modality,
            continuity_ok=continuity_ok,
            high_stakes=high_stakes,
            uncertainty=uncertainty,
            context=context_for_generation,
        )
        identity_capsule = (
            dict(dialogue_context.get("identity_capsule") or {})
            if isinstance(dialogue_context.get("identity_capsule"), dict)
            else {}
        )
        identity_capsule_hash = str(identity_capsule.get("contract_hash") or "").strip() or None
        identity_capsule_used = bool(identity_capsule_hash)
        if not text:
            generated_empty = self.generate_dialogue_turn(
                user_text=text,
                mode=mode,
                modality=modality,
                high_stakes=high_stakes,
                uncertainty=uncertainty,
                continuity_ok=continuity_ok,
                context=context_for_generation,
                dialogue_context=dialogue_context,
            )
            critique_empty = self.critique_dialogue_turn(
                user_text=text,
                generated_turn=generated_empty,
                mode=mode,
                continuity_ok=continuity_ok,
                high_stakes=high_stakes,
            )
            rendered_empty = self.render_dialogue_turn(
                user_text=text,
                generated_turn=generated_empty,
                critique=critique_empty,
            )
            self._record_dialogue_turn_state(
                user_text=text,
                final_reply=rendered_empty,
                generated_turn=generated_empty,
                critique=critique_empty,
                mode=mode,
                pushback_triggered=False,
                continuity={"continuity_ok": bool(continuity_ok)},
                dialogue_context=dialogue_context,
            )
            retrieval_empty = (
                dict((critique_empty or {}).get("retrieval") or {})
                if isinstance((critique_empty or {}).get("retrieval"), dict)
                else {}
            )
            strategy_empty = (
                dict(retrieval_empty.get("strategy") or {})
                if isinstance(retrieval_empty.get("strategy"), dict)
                else {}
            )
            _emit_telemetry(
                model_used=str((generated_empty or {}).get("source") or "") == "model",
                fallback_used=str((generated_empty or {}).get("source") or "") != "model",
                fallback_reason=(
                    str((generated_empty or {}).get("model_failure_reason") or "").strip()
                    or str((generated_empty or {}).get("route_reason") or "").strip()
                    or "heuristic_path"
                ),
                answer_source=(
                    "model"
                    if str((generated_empty or {}).get("source") or "").strip().lower() == "model"
                    else "error_fallback"
                ),
                route_reason=str((generated_empty or {}).get("route_reason") or "heuristic_path"),
                retrieval_selected_count=int(retrieval_empty.get("snippet_count") or 0),
                rerank_used=bool(strategy_empty.get("embedding_rerank") or strategy_empty.get("flag_rerank")),
                model_query_attempted=bool((generated_empty or {}).get("model_query_attempted")),
                model_query_succeeded=bool((generated_empty or {}).get("model_query_succeeded")),
                model_failure_reason=(generated_empty or {}).get("model_failure_reason"),
                partner_lane_used=bool(partner_dialogue_turn),
                partner_subfamily=partner_subfamily,
                partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None,
                identity_capsule_hash=identity_capsule_hash,
                identity_capsule_used=identity_capsule_used,
                retrieval_bucket_counts=(
                    dict(strategy_empty.get("snippet_bucket_counts") or {})
                    if isinstance(strategy_empty.get("snippet_bucket_counts"), dict)
                    else {}
                ),
                retrieval_bucket_mix=(
                    dict(strategy_empty.get("snippet_bucket_counts") or {})
                    if isinstance(strategy_empty.get("snippet_bucket_counts"), dict)
                    else {}
                ),
                partner_context_mix_ok=(
                    bool(strategy_empty.get("partner_context_mix_ok"))
                    if "partner_context_mix_ok" in strategy_empty
                    else None
                ),
                partner_retrieval_target=(partner_target_snippets if partner_dialogue_turn else None),
                deep_lane_invoked=bool((generated_empty or {}).get("deep_lane_invoked")),
                deep_model_name=(generated_empty or {}).get("deep_model_name"),
            )
            return rendered_empty

        generated = self.generate_dialogue_turn(
            user_text=text,
            mode=mode,
            modality=modality,
            high_stakes=high_stakes,
            uncertainty=uncertainty,
            continuity_ok=continuity_ok,
            context=context_for_generation,
            dialogue_context=dialogue_context,
        )
        critique = self.critique_dialogue_turn(
            user_text=text,
            generated_turn=generated,
            mode=mode,
            continuity_ok=continuity_ok,
            high_stakes=high_stakes,
        )
        rendered = self.render_dialogue_turn(
            user_text=text,
            generated_turn=generated,
            critique=critique,
        )
        self._record_dialogue_turn_state(
            user_text=text,
            final_reply=rendered,
            generated_turn=generated,
            critique=critique,
            mode=mode,
            pushback_triggered=False,
                continuity={"continuity_ok": bool(continuity_ok)},
                dialogue_context=dialogue_context,
            )
        retrieval = (
            dict((critique or {}).get("retrieval") or {})
            if isinstance((critique or {}).get("retrieval"), dict)
            else {}
        )
        strategy = (
            dict(retrieval.get("strategy") or {})
            if isinstance(retrieval.get("strategy"), dict)
            else {}
        )
        source = str((generated or {}).get("source") or "").strip().lower()
        fallback_used = source != "model"
        fallback_reason = (
            str((generated or {}).get("model_failure_reason") or "").strip()
            or str((generated or {}).get("route_reason") or "").strip()
        )
        if not fallback_used and str(rendered or "").strip() != str((generated or {}).get("candidate") or "").strip():
            # Critique refinement is still a model-path response, not a fallback.
            fallback_reason = fallback_reason or "model_refined_by_critique"
        retrieval_count = int(retrieval.get("snippet_count") or 0)
        if explicit_brief_turn and retrieval_count <= 0:
            rendered = self._with_cached_brief_notice(rendered)
        if source == "model" and not fallback_used:
            answer_source = "model"
        elif str((generated or {}).get("route_reason") or "").strip() in {
            "fallback_after_model_miss",
            "partner_fallback_after_model_miss",
        }:
            if partner_dialogue_turn:
                answer_source = "partner_fallback"
            else:
                answer_source = "status_fallback" if explicit_brief_turn else "error_fallback"
        elif str((generated or {}).get("route_reason") or "").strip() == "partner_model":
            answer_source = "model"
        elif str((generated or {}).get("route_reason") or "").strip() == "low_latency_social_turn":
            answer_source = "cached_brief"
        else:
            answer_source = "error_fallback" if fallback_used else "cached_brief"
        final_lowered = self._normalize_dialogue_text(str(rendered or ""))
        used_tradeoff_frame = any(
            token in final_lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost")
        )
        used_why_now_frame = any(
            token in final_lowered
            for token in ("right now", "today", "this hour", "window", "timing", "deadline", "before", "now")
        )
        _emit_telemetry(
            model_used=(source == "model"),
            fallback_used=fallback_used,
            fallback_reason=fallback_reason or None,
            answer_source=answer_source,
            route_reason=str((generated or {}).get("route_reason") or "dialogue"),
            retrieval_selected_count=retrieval_count,
            rerank_used=bool(strategy.get("embedding_rerank") or strategy.get("flag_rerank")),
            model_query_attempted=bool((generated or {}).get("model_query_attempted")),
            model_query_succeeded=bool((generated or {}).get("model_query_succeeded")),
            model_failure_reason=(generated or {}).get("model_failure_reason"),
            partner_lane_used=bool(partner_dialogue_turn),
            partner_subfamily=partner_subfamily,
            partner_depth_lane=partner_depth_lane if partner_dialogue_turn else None,
            identity_capsule_hash=identity_capsule_hash,
            identity_capsule_used=identity_capsule_used,
            retrieval_bucket_counts=(
                dict(strategy.get("snippet_bucket_counts") or {})
                if isinstance(strategy.get("snippet_bucket_counts"), dict)
                else {}
            ),
            retrieval_bucket_mix=(
                dict(strategy.get("snippet_bucket_counts") or {})
                if isinstance(strategy.get("snippet_bucket_counts"), dict)
                else {}
            ),
            partner_context_mix_ok=(
                bool(strategy.get("partner_context_mix_ok"))
                if "partner_context_mix_ok" in strategy
                else None
            ),
            partner_retrieval_target=(partner_target_snippets if partner_dialogue_turn else None),
            deep_lane_invoked=bool((generated or {}).get("deep_lane_invoked")),
            deep_model_name=(generated or {}).get("deep_model_name"),
            used_tradeoff_frame=used_tradeoff_frame,
            used_why_now_frame=used_why_now_frame,
        )
        return rendered

    def prepare_openclaw_voice_reply(self, draft: ReplyDraft | dict[str, Any]) -> dict[str, Any]:
        if isinstance(draft, ReplyDraft):
            payload = dict(draft.__dict__)
        else:
            payload = dict(draft or {})
        surface_id = str(payload.get("surface_id") or "voice:unknown").strip() or "voice:unknown"
        session_id = str(payload.get("session_id") or "default").strip() or "default"
        prior_session = self.get_surface_session(surface_id=surface_id, session_id=session_id) or {}
        prior_metadata = (
            prior_session.get("metadata")
            if isinstance(prior_session.get("metadata"), dict)
            else {}
        )
        prior_directive = (
            prior_metadata.get("voice_directive_last")
            if isinstance(prior_metadata.get("voice_directive_last"), dict)
            else {}
        )
        prior_history_raw = (
            prior_metadata.get("voice_directive_history")
            if isinstance(prior_metadata.get("voice_directive_history"), list)
            else []
        )
        prior_history: list[dict[str, Any]] = [item for item in prior_history_raw if isinstance(item, dict)]
        prior_history = prior_history[-10:]

        history_speed_values: list[float] = []
        history_stability_values: list[float] = []
        history_target_speed_values: list[float] = []
        history_target_stability_values: list[float] = []
        history_speed_gap_values: list[float] = []
        history_stability_gap_values: list[float] = []
        for item in prior_history:
            speed_value = None
            stability_value = None
            try:
                speed_value = float(item.get("speed"))
            except (TypeError, ValueError):
                speed_value = None
            try:
                stability_value = float(item.get("stability"))
            except (TypeError, ValueError):
                stability_value = None
            target_speed_value = None
            target_stability_value = None
            try:
                target_speed_value = float(item.get("target_speed"))
            except (TypeError, ValueError):
                target_speed_value = speed_value
            try:
                target_stability_value = float(item.get("target_stability"))
            except (TypeError, ValueError):
                target_stability_value = stability_value
            if speed_value is not None:
                history_speed_values.append(speed_value)
            if stability_value is not None:
                history_stability_values.append(stability_value)
            if target_speed_value is not None:
                history_target_speed_values.append(target_speed_value)
            if target_stability_value is not None:
                history_target_stability_values.append(target_stability_value)
            if speed_value is not None and target_speed_value is not None:
                history_speed_gap_values.append(abs(target_speed_value - speed_value))
            if stability_value is not None and target_stability_value is not None:
                history_stability_gap_values.append(abs(target_stability_value - stability_value))
        if prior_directive:
            speed_value = None
            stability_value = None
            try:
                speed_value = float(prior_directive.get("speed"))
            except (TypeError, ValueError):
                speed_value = None
            try:
                stability_value = float(prior_directive.get("stability"))
            except (TypeError, ValueError):
                stability_value = None
            target_speed_value = None
            target_stability_value = None
            try:
                target_speed_value = float(prior_directive.get("target_speed"))
            except (TypeError, ValueError):
                target_speed_value = speed_value
            try:
                target_stability_value = float(prior_directive.get("target_stability"))
            except (TypeError, ValueError):
                target_stability_value = stability_value
            if speed_value is not None:
                history_speed_values.append(speed_value)
            if stability_value is not None:
                history_stability_values.append(stability_value)
            if target_speed_value is not None:
                history_target_speed_values.append(target_speed_value)
            if target_stability_value is not None:
                history_target_stability_values.append(target_stability_value)
            if speed_value is not None and target_speed_value is not None:
                history_speed_gap_values.append(abs(target_speed_value - speed_value))
            if stability_value is not None and target_stability_value is not None:
                history_stability_gap_values.append(abs(target_stability_value - stability_value))
        speed_anchor = (
            round(float(statistics.median(history_speed_values)), 4)
            if history_speed_values
            else None
        )
        stability_anchor = (
            round(float(statistics.median(history_stability_values)), 4)
            if history_stability_values
            else None
        )
        speed_volatility = (
            float(statistics.pstdev(history_speed_values))
            if len(history_speed_values) >= 2
            else 0.0
        )
        stability_volatility = (
            float(statistics.pstdev(history_stability_values))
            if len(history_stability_values) >= 2
            else 0.0
        )
        speed_deltas: list[float] = []
        stability_deltas: list[float] = []
        for idx in range(1, len(history_speed_values)):
            speed_deltas.append(float(history_speed_values[idx] - history_speed_values[idx - 1]))
        for idx in range(1, len(history_stability_values)):
            stability_deltas.append(float(history_stability_values[idx] - history_stability_values[idx - 1]))
        target_speed_deltas: list[float] = []
        target_stability_deltas: list[float] = []
        for idx in range(1, len(history_target_speed_values)):
            target_speed_deltas.append(
                float(history_target_speed_values[idx] - history_target_speed_values[idx - 1])
            )
        for idx in range(1, len(history_target_stability_values)):
            target_stability_deltas.append(
                float(history_target_stability_values[idx] - history_target_stability_values[idx - 1])
            )

        def _trend_delta(values: list[float]) -> float:
            if not values:
                return 0.0
            try:
                return float(statistics.median(values))
            except statistics.StatisticsError:
                return 0.0

        def _median_nonzero_abs(values: list[float]) -> float:
            nonzero = [abs(float(value)) for value in values if abs(float(value)) > 1e-6]
            if not nonzero:
                return 0.0
            try:
                return float(statistics.median(nonzero))
            except statistics.StatisticsError:
                return 0.0

        def _oscillation_rate(values: list[float]) -> float:
            if len(values) < 2:
                return 0.0
            flips = 0
            transitions = 0
            prev_sign = 0
            for value in values:
                sign = 1 if value > 1e-6 else (-1 if value < -1e-6 else 0)
                if sign == 0:
                    continue
                if prev_sign != 0:
                    transitions += 1
                    if sign != prev_sign:
                        flips += 1
                prev_sign = sign
            if transitions <= 0:
                return 0.0
            return float(flips) / float(transitions)

        def _direction_streak(values: list[float]) -> tuple[int, int]:
            if not values:
                return 0, 0
            streak_sign = 0
            streak_len = 0
            for value in reversed(values):
                sign = 1 if value > 1e-6 else (-1 if value < -1e-6 else 0)
                if sign == 0:
                    continue
                if streak_sign == 0:
                    streak_sign = sign
                    streak_len = 1
                    continue
                if sign == streak_sign:
                    streak_len += 1
                else:
                    break
            return streak_sign, streak_len

        def _plateau_streak(values: list[float], *, epsilon: float) -> int:
            if not values:
                return 0
            streak = 0
            for value in reversed(values):
                if abs(float(value)) <= float(epsilon):
                    streak += 1
                else:
                    break
            return streak

        speed_trend = _trend_delta(speed_deltas)
        stability_trend = _trend_delta(stability_deltas)
        speed_oscillation_rate = _oscillation_rate(speed_deltas)
        stability_oscillation_rate = _oscillation_rate(stability_deltas)
        speed_direction_sign, speed_direction_streak = _direction_streak(speed_deltas)
        stability_direction_sign, stability_direction_streak = _direction_streak(stability_deltas)
        speed_plateau_streak = _plateau_streak(speed_deltas, epsilon=0.003)
        stability_plateau_streak = _plateau_streak(stability_deltas, epsilon=0.004)
        speed_intent_sign, speed_intent_streak = _direction_streak(target_speed_deltas)
        stability_intent_sign, stability_intent_streak = _direction_streak(target_stability_deltas)
        speed_intent_strength = _median_nonzero_abs(target_speed_deltas)
        stability_intent_strength = _median_nonzero_abs(target_stability_deltas)
        try:
            speed_response_gap = float(statistics.median(history_speed_gap_values[-3:]))
        except statistics.StatisticsError:
            speed_response_gap = 0.0
        try:
            stability_response_gap = float(statistics.median(history_stability_gap_values[-3:]))
        except statistics.StatisticsError:
            stability_response_gap = 0.0
        voice_pack = self.get_active_voice_pack()
        voice_readiness = self.get_voice_readiness_report()
        voice_diagnostics = self.get_voice_continuity_diagnostics(limit=120)
        voice_tuning = self.get_voice_tuning_profile(limit=120)
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        context.setdefault("modality", "voice")
        context.setdefault("voice_surface", True)
        context.setdefault("interrupt_on_speech", True)
        context.setdefault("_reply_endpoint_name", "presence.voice.reply.prepare")
        if bool(voice_pack.get("active")):
            context.setdefault("voice_asset_pack_id", voice_pack.get("pack_id"))
            context.setdefault("voice_asset_pack_profile", voice_pack.get("profile"))
            context.setdefault("voice_asset_pack_name", voice_pack.get("pack_name"))
            context.setdefault("voice_asset_pack_quality_tier", voice_pack.get("quality_tier"))
            context.setdefault(
                "voice_actor_profile_active",
                "actor_match" in str(voice_pack.get("profile") or "").strip().lower(),
            )
            clip_quality = (
                voice_pack.get("clip_quality")
                if isinstance(voice_pack.get("clip_quality"), dict)
                else {}
            )
            movie_match_mean = clip_quality.get("mean_movie_match_score")
            cadence_score = clip_quality.get("cadence_score")
            annunciation_score = clip_quality.get("annunciation_score")
            cadence_variation_cv = clip_quality.get("cadence_variation_cv")
            try:
                movie_match_mean = float(movie_match_mean) if movie_match_mean is not None else None
            except (TypeError, ValueError):
                movie_match_mean = None
            try:
                cadence_score = float(cadence_score) if cadence_score is not None else None
            except (TypeError, ValueError):
                cadence_score = None
            try:
                annunciation_score = float(annunciation_score) if annunciation_score is not None else None
            except (TypeError, ValueError):
                annunciation_score = None
            try:
                cadence_variation_cv = float(cadence_variation_cv) if cadence_variation_cv is not None else None
            except (TypeError, ValueError):
                cadence_variation_cv = None
            if movie_match_mean is not None:
                context.setdefault("voice_movie_match_score", round(movie_match_mean, 6))
            if cadence_score is not None:
                context.setdefault("voice_cadence_score", round(cadence_score, 6))
            if annunciation_score is not None:
                context.setdefault("voice_annunciation_score", round(annunciation_score, 6))
            if cadence_variation_cv is not None:
                context.setdefault("voice_cadence_variation_cv", round(cadence_variation_cv, 6))
            context.setdefault(
                "voice_movie_match_active",
                bool(
                    ("actor_match" in str(voice_pack.get("profile") or "").strip().lower())
                    and movie_match_mean is not None
                    and movie_match_mean >= 0.66
                ),
            )
            context.setdefault("voice_asset_pack_continuity_ready", bool(voice_pack.get("continuity_ready")))
            context.setdefault(
                "voice_asset_pack_ready_for_production_talk",
                bool(voice_readiness.get("ready_for_production_talk")),
            )
            target_profile = (
                voice_readiness.get("target_directive_profile")
                if isinstance(voice_readiness.get("target_directive_profile"), dict)
                else {}
            )
            context.setdefault("voice_target_latency_tier", target_profile.get("latency_tier"))
            speed_range = target_profile.get("speed_range") if isinstance(target_profile.get("speed_range"), list) else []
            if len(speed_range) == 2:
                context.setdefault("voice_target_speed_min", speed_range[0])
                context.setdefault("voice_target_speed_max", speed_range[1])
            context.setdefault("voice_target_stability_floor", target_profile.get("stability_floor"))
            context.setdefault(
                "voice_empirical_strict_ready",
                bool(voice_diagnostics.get("ready_for_strict_continuity")),
            )
            context.setdefault(
                "voice_empirical_continuity_confidence",
                voice_diagnostics.get("continuity_confidence"),
            )
            metrics = (
                voice_diagnostics.get("metrics")
                if isinstance(voice_diagnostics.get("metrics"), dict)
                else {}
            )
            context.setdefault(
                "voice_empirical_continuity_failure_rate",
                metrics.get("continuity_failure_rate"),
            )
            context.setdefault(
                "voice_empirical_phase_b_delta_ms",
                metrics.get("phase_b_average_delta_ms"),
            )
            tuning_profile = (
                voice_tuning.get("profile")
                if isinstance(voice_tuning.get("profile"), dict)
                else {}
            )
            context.setdefault("voice_tuning_profile_id", voice_tuning.get("profile_id"))
            context.setdefault("voice_tuning_confidence", voice_tuning.get("confidence"))
            context.setdefault("voice_tuning_override_revision", voice_tuning.get("override_revision"))
            context.setdefault("voice_tuning_latency_tier", tuning_profile.get("latency_tier"))
            context.setdefault("voice_tuning_speed_min", tuning_profile.get("speed_min"))
            context.setdefault("voice_tuning_speed_max", tuning_profile.get("speed_max"))
            context.setdefault("voice_tuning_speed_bias", tuning_profile.get("speed_bias"))
            context.setdefault("voice_tuning_stability_floor", tuning_profile.get("stability_floor"))
            context.setdefault("voice_tuning_stability_bias", tuning_profile.get("stability_bias"))
            context.setdefault("voice_tuning_cadence_bias", tuning_profile.get("cadence_bias"))
            context.setdefault("voice_tuning_annunciation_bias", tuning_profile.get("annunciation_bias"))
            smoothing = (
                voice_tuning.get("continuity_smoothing")
                if isinstance(voice_tuning.get("continuity_smoothing"), dict)
                else {}
            )
            context.setdefault("voice_tuning_max_speed_step", smoothing.get("max_speed_step"))
            context.setdefault("voice_tuning_max_stability_step", smoothing.get("max_stability_step"))
            context.setdefault("voice_tuning_jitter_deadband_speed", smoothing.get("jitter_deadband_speed"))
            context.setdefault("voice_tuning_jitter_deadband_stability", smoothing.get("jitter_deadband_stability"))
            context.setdefault("voice_tuning_history_anchor_weight", smoothing.get("history_anchor_weight"))
            context.setdefault("voice_tuning_smooth_alpha_speed", smoothing.get("smooth_alpha_speed"))
            context.setdefault("voice_tuning_smooth_alpha_stability", smoothing.get("smooth_alpha_stability"))
            context.setdefault(
                "voice_tuning_speed_upward_step_ratio",
                smoothing.get("speed_upward_step_ratio"),
            )
            context.setdefault(
                "voice_tuning_stability_upward_step_ratio",
                smoothing.get("stability_upward_step_ratio"),
            )
            context.setdefault(
                "voice_tuning_allow_latency_drop_to_low",
                smoothing.get("allow_latency_drop_to_low"),
            )
            context.setdefault("voice_tuning_flow_inertia", smoothing.get("flow_inertia"))
            context.setdefault(
                "voice_tuning_flow_oscillation_guard",
                smoothing.get("flow_oscillation_guard"),
            )
            context.setdefault(
                "voice_tuning_flow_release_speed_ratio",
                smoothing.get("flow_release_speed_ratio"),
            )
            context.setdefault(
                "voice_tuning_flow_release_stability_ratio",
                smoothing.get("flow_release_stability_ratio"),
            )
            context.setdefault(
                "voice_tuning_flow_follow_through",
                smoothing.get("flow_follow_through"),
            )
            context.setdefault(
                "voice_tuning_flow_plateau_release_speed",
                smoothing.get("flow_plateau_release_speed"),
            )
            context.setdefault(
                "voice_tuning_flow_plateau_release_stability",
                smoothing.get("flow_plateau_release_stability"),
            )
        if prior_directive:
            context.setdefault("voice_prev_speed", prior_directive.get("speed"))
            context.setdefault("voice_prev_stability", prior_directive.get("stability"))
            context.setdefault("voice_prev_latency_tier", prior_directive.get("latency_tier"))
            context.setdefault("voice_prev_tuning_profile_id", prior_directive.get("tuning_profile_id"))
            context.setdefault("voice_prev_directive_at", prior_directive.get("updated_at"))
            context.setdefault("voice_prev_continuity_smoothed", prior_directive.get("continuity_smoothed"))
        context.setdefault("voice_prev_history_count", len(prior_history))
        context.setdefault("voice_prev_speed_volatility", round(speed_volatility, 6))
        context.setdefault("voice_prev_stability_volatility", round(stability_volatility, 6))
        context.setdefault("voice_prev_speed_trend", round(speed_trend, 6))
        context.setdefault("voice_prev_stability_trend", round(stability_trend, 6))
        context.setdefault("voice_prev_speed_oscillation_rate", round(speed_oscillation_rate, 6))
        context.setdefault(
            "voice_prev_stability_oscillation_rate",
            round(stability_oscillation_rate, 6),
        )
        context.setdefault("voice_prev_speed_direction_sign", int(speed_direction_sign))
        context.setdefault("voice_prev_speed_direction_streak", int(speed_direction_streak))
        context.setdefault("voice_prev_stability_direction_sign", int(stability_direction_sign))
        context.setdefault("voice_prev_stability_direction_streak", int(stability_direction_streak))
        context.setdefault("voice_prev_speed_plateau_streak", int(speed_plateau_streak))
        context.setdefault("voice_prev_stability_plateau_streak", int(stability_plateau_streak))
        context.setdefault("voice_prev_speed_intent_sign", int(speed_intent_sign))
        context.setdefault("voice_prev_speed_intent_streak", int(speed_intent_streak))
        context.setdefault("voice_prev_stability_intent_sign", int(stability_intent_sign))
        context.setdefault("voice_prev_stability_intent_streak", int(stability_intent_streak))
        context.setdefault("voice_prev_speed_intent_strength", round(speed_intent_strength, 6))
        context.setdefault("voice_prev_stability_intent_strength", round(stability_intent_strength, 6))
        context.setdefault("voice_prev_speed_response_gap", round(speed_response_gap, 6))
        context.setdefault("voice_prev_stability_response_gap", round(stability_response_gap, 6))
        if speed_anchor is not None:
            context.setdefault("voice_prev_speed_anchor", speed_anchor)
        if stability_anchor is not None:
            context.setdefault("voice_prev_stability_anchor", stability_anchor)
        payload["context"] = context
        payload["modality"] = str(payload.get("modality") or "voice")
        payload["latency_profile"] = str(payload.get("latency_profile") or "talk")
        if payload.get("interruption_allowed") is None:
            payload["interruption_allowed"] = True
        prepared = self.prepare_openclaw_reply(payload)
        prepared["voice_asset_pack"] = voice_pack
        prepared["voice_readiness"] = voice_readiness
        prepared["voice_diagnostics"] = voice_diagnostics
        prepared["voice_tuning_profile"] = voice_tuning
        if not bool(voice_readiness.get("ready_for_production_talk")):
            prepared["voice_readiness_notice"] = str(
                voice_readiness.get("summary")
                or "Voice pack is active but continuity quality is below production target."
            )
        if not bool(voice_diagnostics.get("ready_for_strict_continuity")):
            prepared["voice_diagnostics_notice"] = str(
                voice_diagnostics.get("summary")
                or "Voice continuity diagnostics suggest additional soak/tuning before strict continuity mode."
            )
        voice_payload = prepared.get("voice") if isinstance(prepared.get("voice"), dict) else None
        if isinstance(voice_payload, dict):
            voice_payload = dict(voice_payload)
            voice_payload["asset_pack"] = {
                "active": bool(voice_pack.get("active")),
                "continuity_ready": bool(voice_pack.get("continuity_ready")),
                "ready_for_production_talk": bool(voice_readiness.get("ready_for_production_talk")),
                "ready_for_strict_continuity": bool(voice_diagnostics.get("ready_for_strict_continuity")),
                "pack_id": voice_pack.get("pack_id"),
                "profile": voice_pack.get("profile"),
                "pack_name": voice_pack.get("pack_name"),
                "quality_tier": voice_pack.get("quality_tier"),
                "clip_count": voice_pack.get("clip_count"),
            }
            voice_payload["readiness"] = {
                "ready_for_production_talk": bool(voice_readiness.get("ready_for_production_talk")),
                "ready_for_strict_continuity": bool(voice_diagnostics.get("ready_for_strict_continuity")),
                "confidence": voice_readiness.get("confidence"),
                "continuity_confidence": voice_diagnostics.get("continuity_confidence"),
                "tuning_confidence": voice_tuning.get("confidence"),
                "tuning_profile_id": voice_tuning.get("profile_id"),
                "clip_quality_score": (
                    (voice_readiness.get("clarity_quality") or {}).get("score")
                    if isinstance(voice_readiness.get("clarity_quality"), dict)
                    else None
                ),
                "summary": voice_readiness.get("summary"),
            }
            directive = voice_payload.get("directive")
            if isinstance(directive, dict):
                directive = dict(directive)
                if bool(voice_pack.get("active")):
                    directive.setdefault("asset_pack_id", voice_pack.get("pack_id"))
                    directive.setdefault("asset_pack_profile", voice_pack.get("profile"))
                    directive.setdefault("asset_pack_quality_tier", voice_pack.get("quality_tier"))
                    directive.setdefault(
                        "asset_pack_ready_for_production_talk",
                        bool(voice_readiness.get("ready_for_production_talk")),
                    )
                    directive.setdefault(
                        "asset_pack_ready_for_strict_continuity",
                        bool(voice_diagnostics.get("ready_for_strict_continuity")),
                    )
                    directive.setdefault(
                        "asset_pack_continuity_confidence",
                        voice_diagnostics.get("continuity_confidence"),
                    )
                    directive.setdefault("asset_pack_tuning_profile_id", voice_tuning.get("profile_id"))
                    directive.setdefault("asset_pack_tuning_confidence", voice_tuning.get("confidence"))
                voice_payload["directive"] = directive
                prepared["voice_directive"] = directive
            prepared["voice"] = voice_payload
        final_directive = (
            prepared.get("voice_directive")
            if isinstance(prepared.get("voice_directive"), dict)
            else {}
        )
        if final_directive:
            prior_speed_value = None
            prior_stability_value = None
            current_speed_value = None
            current_stability_value = None
            try:
                prior_speed_value = float(prior_directive.get("speed"))
            except (TypeError, ValueError):
                prior_speed_value = None
            try:
                prior_stability_value = float(prior_directive.get("stability"))
            except (TypeError, ValueError):
                prior_stability_value = None
            try:
                current_speed_value = float(final_directive.get("speed"))
            except (TypeError, ValueError):
                current_speed_value = None
            try:
                current_stability_value = float(final_directive.get("stability"))
            except (TypeError, ValueError):
                current_stability_value = None
            delta: dict[str, Any] = {}
            if prior_speed_value is not None and current_speed_value is not None:
                delta["speed_delta"] = round(current_speed_value - prior_speed_value, 4)
            if prior_stability_value is not None and current_stability_value is not None:
                delta["stability_delta"] = round(current_stability_value - prior_stability_value, 4)
            if speed_anchor is not None and current_speed_value is not None:
                delta["speed_anchor_delta"] = round(current_speed_value - float(speed_anchor), 4)
            if stability_anchor is not None and current_stability_value is not None:
                delta["stability_anchor_delta"] = round(current_stability_value - float(stability_anchor), 4)

            history_entry = {
                "speed": final_directive.get("speed"),
                "stability": final_directive.get("stability"),
                "target_speed": final_directive.get("continuity_target_speed"),
                "target_stability": final_directive.get("continuity_target_stability"),
                "latency_tier": final_directive.get("latency_tier"),
                "provider": final_directive.get("provider"),
                "voice": final_directive.get("voice"),
                "tuning_profile_id": final_directive.get("tuning_profile_id"),
                "continuity_smoothed": bool(final_directive.get("continuity_smoothed")),
                "updated_at": utc_now_iso(),
            }
            updated_history = list(prior_history)
            updated_history.append(history_entry)
            updated_history = updated_history[-10:]
            channel_type = str(prior_session.get("channel_type") or "").strip().lower()
            if not channel_type:
                channel_type = "voice" if surface_id.startswith(("voice:", "talk:")) else "surface"
            self.surface_sessions.touch_event(
                surface_id=surface_id,
                channel_type=channel_type,
                session_id=session_id,
                relationship_mode=str(((prepared.get("mode") or {}).get("mode") or "")).strip() or None,
                contract_hash=(
                    str(((prepared.get("continuity") or {}).get("active_contract_hash") or "")).strip()
                    or None
                ),
                status=str(prior_session.get("status") or "active"),
                metadata={
                    "voice_directive_last": {
                        **history_entry,
                        "tuning_override_revision": final_directive.get("tuning_override_revision"),
                    },
                    "voice_directive_last_delta": delta,
                    "voice_directive_history": updated_history,
                    "voice_directive_history_count": len(updated_history),
                },
            )
        return prepared

    def get_active_voice_pack(self, *, refresh: bool = False) -> dict[str, Any]:
        return self.voice_assets.get_active_pack(refresh=refresh)

    def get_voice_readiness_report(self, *, refresh: bool = False) -> dict[str, Any]:
        pack = self.get_active_voice_pack(refresh=refresh)
        quality = str(pack.get("quality_tier") or "none").strip().lower() or "none"
        active = bool(pack.get("active"))
        continuity_ready = bool(pack.get("continuity_ready"))
        clip_count = int(pack.get("clip_count") or 0)
        duration = float(pack.get("total_duration_sec") or 0.0)
        issues = list(pack.get("issues") or [])
        blocking_issues = list(pack.get("blocking_issues") or [])
        warnings = list(pack.get("warnings") or [])
        clip_quality = pack.get("clip_quality") if isinstance(pack.get("clip_quality"), dict) else {}
        clip_quality_score = clip_quality.get("score")
        try:
            clip_quality_score = (
                self._clamp_float(float(clip_quality_score), low=0.0, high=1.0)
                if clip_quality_score is not None
                else None
            )
        except (TypeError, ValueError):
            clip_quality_score = None
        clip_quality_grade = str(clip_quality.get("grade") or "").strip().lower() or None
        clip_quality_ready = bool(clip_quality_score is None or clip_quality_score >= 0.72)
        cadence_score = clip_quality.get("cadence_score")
        annunciation_score = clip_quality.get("annunciation_score")
        cadence_variation_cv = clip_quality.get("cadence_variation_cv")
        try:
            cadence_score = (
                self._clamp_float(float(cadence_score), low=0.0, high=1.0)
                if cadence_score is not None
                else None
            )
        except (TypeError, ValueError):
            cadence_score = None
        try:
            annunciation_score = (
                self._clamp_float(float(annunciation_score), low=0.0, high=1.0)
                if annunciation_score is not None
                else None
            )
        except (TypeError, ValueError):
            annunciation_score = None
        try:
            cadence_variation_cv = (
                self._clamp_float(float(cadence_variation_cv), low=0.0, high=2.0)
                if cadence_variation_cv is not None
                else None
            )
        except (TypeError, ValueError):
            cadence_variation_cv = None
        cadence_ready = bool(cadence_score is None or cadence_score >= 0.68)
        annunciation_ready = bool(annunciation_score is None or annunciation_score >= 0.72)
        pack_profile = str(pack.get("profile") or "").strip().lower()
        actor_profile_active = "actor_match" in pack_profile

        confidence = 0.0
        if quality == "seed":
            confidence = 0.42
        elif quality == "development":
            confidence = 0.72
        elif quality == "production":
            confidence = 0.9
        if not active:
            confidence = min(confidence, 0.2)
        if not continuity_ready:
            confidence = min(confidence, 0.58)
        if blocking_issues:
            confidence = min(confidence, 0.25)
        if warnings:
            confidence = max(0.0, confidence - min(0.2, 0.03 * len(warnings)))
        if clip_quality_score is not None:
            if clip_quality_score < 0.8:
                confidence = max(0.0, confidence - min(0.14, (0.8 - clip_quality_score) * 0.35))
            if clip_quality_score < 0.62:
                confidence = min(confidence, 0.58)
        if cadence_score is not None:
            if cadence_score < 0.78:
                confidence = max(0.0, confidence - min(0.08, (0.78 - cadence_score) * 0.3))
            if cadence_score < 0.62:
                confidence = min(confidence, 0.62)
        if annunciation_score is not None:
            if annunciation_score < 0.8:
                confidence = max(0.0, confidence - min(0.1, (0.8 - annunciation_score) * 0.35))
            if annunciation_score < 0.62:
                confidence = min(confidence, 0.56)
        confidence = round(max(0.0, min(0.99, confidence)), 3)

        target_by_quality: dict[str, dict[str, Any]] = {
            "production": {
                "latency_tier": "low",
                "speed_range": [0.95, 1.08],
                "stability_floor": 0.64,
            },
            "development": {
                "latency_tier": "balanced",
                "speed_range": [0.92, 1.02],
                "stability_floor": 0.7,
            },
            "seed": {
                "latency_tier": "balanced",
                "speed_range": [0.88, 0.98],
                "stability_floor": 0.76,
            },
            "none": {
                "latency_tier": "balanced",
                "speed_range": [0.9, 1.0],
                "stability_floor": 0.78,
            },
        }
        target_profile = dict(target_by_quality.get(quality) or target_by_quality["none"])
        if clip_quality_score is not None and clip_quality_score < 0.78:
            speed_range = (
                list(target_profile.get("speed_range"))
                if isinstance(target_profile.get("speed_range"), list)
                else [0.9, 1.0]
            )
            if len(speed_range) == 2:
                speed_floor = self._clamp_float(float(speed_range[0]), low=0.82, high=1.18)
                speed_ceiling = self._clamp_float(float(speed_range[1]), low=0.82, high=1.18)
                speed_ceiling = min(speed_ceiling, speed_floor + 0.12)
                speed_ceiling -= min(0.04, (0.78 - clip_quality_score) * 0.08)
                if clip_quality_score < 0.62:
                    speed_ceiling = min(speed_ceiling, 1.0)
                if speed_ceiling < speed_floor:
                    speed_floor, speed_ceiling = speed_ceiling, speed_floor
                target_profile["speed_range"] = [round(speed_floor, 3), round(speed_ceiling, 3)]
            base_stability_floor = self._clamp_float(
                float(target_profile.get("stability_floor") or 0.7),
                low=0.35,
                high=0.98,
            )
            stability_boost = min(0.09, (0.78 - clip_quality_score) * 0.12)
            target_profile["stability_floor"] = round(
                self._clamp_float(base_stability_floor + stability_boost, low=0.35, high=0.98),
                3,
            )
            if clip_quality_score < 0.62:
                target_profile["latency_tier"] = "balanced"
        if cadence_score is not None and cadence_score < 0.8:
            speed_range = (
                list(target_profile.get("speed_range"))
                if isinstance(target_profile.get("speed_range"), list)
                else [0.9, 1.0]
            )
            if len(speed_range) == 2:
                speed_floor = self._clamp_float(float(speed_range[0]), low=0.82, high=1.18)
                speed_ceiling = self._clamp_float(float(speed_range[1]), low=0.82, high=1.18)
                speed_ceiling = min(
                    speed_ceiling,
                    speed_floor + max(0.06, 0.1 - min(0.05, (0.8 - cadence_score) * 0.12)),
                )
                speed_ceiling -= min(0.03, (0.8 - cadence_score) * 0.08)
                if speed_ceiling < speed_floor:
                    speed_floor, speed_ceiling = speed_ceiling, speed_floor
                target_profile["speed_range"] = [round(speed_floor, 3), round(speed_ceiling, 3)]
            base_stability_floor = self._clamp_float(
                float(target_profile.get("stability_floor") or 0.7),
                low=0.35,
                high=0.98,
            )
            stability_boost = min(0.07, (0.8 - cadence_score) * 0.1)
            target_profile["stability_floor"] = round(
                self._clamp_float(base_stability_floor + stability_boost, low=0.35, high=0.98),
                3,
            )
            if cadence_score < 0.65:
                target_profile["latency_tier"] = "balanced"
        if annunciation_score is not None and annunciation_score < 0.82:
            speed_range = (
                list(target_profile.get("speed_range"))
                if isinstance(target_profile.get("speed_range"), list)
                else [0.9, 1.0]
            )
            if len(speed_range) == 2:
                speed_floor = self._clamp_float(float(speed_range[0]), low=0.82, high=1.18)
                speed_ceiling = self._clamp_float(float(speed_range[1]), low=0.82, high=1.18)
                speed_ceiling -= min(0.04, (0.82 - annunciation_score) * 0.1)
                if annunciation_score < 0.7:
                    speed_ceiling = min(speed_ceiling, 1.0)
                if speed_ceiling < speed_floor:
                    speed_floor, speed_ceiling = speed_ceiling, speed_floor
                target_profile["speed_range"] = [round(speed_floor, 3), round(speed_ceiling, 3)]
            base_stability_floor = self._clamp_float(
                float(target_profile.get("stability_floor") or 0.7),
                low=0.35,
                high=0.98,
            )
            stability_boost = min(0.08, (0.82 - annunciation_score) * 0.14)
            target_profile["stability_floor"] = round(
                self._clamp_float(base_stability_floor + stability_boost, low=0.35, high=0.98),
                3,
            )
            if annunciation_score < 0.7:
                target_profile["latency_tier"] = "balanced"
        if actor_profile_active and cadence_score is not None and annunciation_score is not None:
            if cadence_score >= 0.84 and annunciation_score >= 0.84:
                speed_range = (
                    list(target_profile.get("speed_range"))
                    if isinstance(target_profile.get("speed_range"), list)
                    else [0.9, 1.0]
                )
                if len(speed_range) == 2:
                    speed_floor = self._clamp_float(float(speed_range[0]), low=0.82, high=1.18)
                    speed_ceiling = self._clamp_float(float(speed_range[1]), low=0.82, high=1.18)
                    speed_floor = max(speed_floor, 0.93)
                    speed_ceiling = min(speed_ceiling, 1.03)
                    if speed_ceiling < speed_floor:
                        speed_floor, speed_ceiling = speed_ceiling, speed_floor
                    target_profile["speed_range"] = [round(speed_floor, 3), round(speed_ceiling, 3)]
                base_stability_floor = self._clamp_float(
                    float(target_profile.get("stability_floor") or 0.7),
                    low=0.35,
                    high=0.98,
                )
                target_profile["stability_floor"] = round(
                    self._clamp_float(max(base_stability_floor, 0.69), low=0.35, high=0.98),
                    3,
                )

        checklist: list[dict[str, Any]] = []
        checklist.append(
            {
                "id": "pointer_present",
                "ok": bool(pack.get("pointer_exists")),
                "detail": "ACTIVE_VOICE_PACK.json is present",
            }
        )
        checklist.append(
            {
                "id": "pack_active",
                "ok": active,
                "detail": "Active voice pack path resolves",
            }
        )
        checklist.append(
            {
                "id": "minimum_clips",
                "ok": clip_count >= 5,
                "detail": "At least 5 isolated clips",
            }
        )
        checklist.append(
            {
                "id": "minimum_duration",
                "ok": duration >= 45.0,
                "detail": "At least 45s total duration",
            }
        )
        checklist.append(
            {
                "id": "continuity_ready",
                "ok": continuity_ready,
                "detail": "Continuity-ready pack quality",
            }
        )
        checklist.append(
            {
                "id": "clarity_quality",
                "ok": clip_quality_ready,
                "detail": (
                    f"Clip clarity profile is {clip_quality_grade} (score={clip_quality_score:.3f})"
                    if clip_quality_score is not None
                    else "Clip clarity profile unavailable; using structural readiness only"
                ),
            }
        )
        checklist.append(
            {
                "id": "cadence_quality",
                "ok": cadence_ready,
                "detail": (
                    f"Cadence profile is {clip_quality.get('cadence_grade')} (score={cadence_score:.3f})"
                    if cadence_score is not None
                    else "Cadence profile unavailable; using continuity defaults"
                ),
            }
        )
        checklist.append(
            {
                "id": "annunciation_quality",
                "ok": annunciation_ready,
                "detail": (
                    f"Annunciation profile is {clip_quality.get('annunciation_grade')} (score={annunciation_score:.3f})"
                    if annunciation_score is not None
                    else "Annunciation profile unavailable; using continuity defaults"
                ),
            }
        )

        recommendations: list[str] = []
        if not active:
            recommendations.append("Install or repair active voice pack pointer and clips directory.")
        if clip_count < 5:
            recommendations.append("Add more clean clips until at least 5 are available.")
        if duration < 45.0:
            recommendations.append("Increase clean clip coverage to at least 45 seconds.")
        if quality in {"seed", "none"}:
            recommendations.append("Build or rebuild `master_v2` pack for stronger production consistency.")
        if quality == "development":
            recommendations.append("Expand to 20+ clips / 120s total to reach production-tier continuity.")
        if clip_quality_score is not None and clip_quality_score < 0.62:
            recommendations.append(
                "Clip clarity profile is weak; re-run polishing pipeline and prefer lower-hiss/higher-harmonicity selections."
            )
        elif clip_quality_score is not None and clip_quality_score < 0.75:
            recommendations.append(
                "Clip clarity is marginal; continue fine-tuning cleanup and monitor hiss/silence outliers."
            )
        if cadence_score is not None and cadence_score < 0.68:
            recommendations.append(
                "Cadence consistency is low; rebalance clip pacing and reduce pause-length outliers in training clips."
            )
        elif cadence_score is not None and cadence_score < 0.8:
            recommendations.append(
                "Cadence is near target; tighten pacing variance to improve movie-like delivery continuity."
            )
        if cadence_variation_cv is not None and cadence_variation_cv > 0.5:
            recommendations.append(
                "Cadence variation is high; trim unusually short/long clips to stabilize phrase rhythm."
            )
        if annunciation_score is not None and annunciation_score < 0.72:
            recommendations.append(
                "Annunciation profile is weak; prioritize cleaner harmonic clips and reduce hiss-heavy segments."
            )
        elif annunciation_score is not None and annunciation_score < 0.82:
            recommendations.append(
                "Annunciation is improving; continue articulation-focused cleanup and retest clip clarity."
            )
        if not recommendations and continuity_ready:
            recommendations.append("Readiness is strong; keep the current active pack and monitor drift during soak.")

        ready_for_production = bool(
            active
            and continuity_ready
            and quality in {"development", "production"}
            and (clip_quality_score is None or clip_quality_score >= 0.62)
            and (cadence_score is None or cadence_score >= 0.62)
            and (annunciation_score is None or annunciation_score >= 0.62)
        )
        summary = (
            "Voice pack is production-ready for continuity-sensitive talk mode."
            if ready_for_production
            else (
                "Voice pack is active but clip clarity remains below production target."
                if active and clip_quality_score is not None and clip_quality_score < 0.62
                else "Voice pack is active but not yet production-ready for maximum continuity."
            )
        )

        return {
            "checked_at": utc_now_iso(),
            "ready_for_production_talk": ready_for_production,
            "confidence": confidence,
            "summary": summary,
            "quality_tier": quality,
            "target_directive_profile": target_profile,
            "issues": issues,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "recommended_action": pack.get("recommended_action"),
            "checklist": checklist,
            "recommendations": recommendations,
            "clarity_quality": clip_quality,
            "cadence_quality": {
                "score": (round(cadence_score, 4) if cadence_score is not None else None),
                "grade": clip_quality.get("cadence_grade"),
                "variation_cv": (round(cadence_variation_cv, 4) if cadence_variation_cv is not None else None),
            },
            "annunciation_quality": {
                "score": (round(annunciation_score, 4) if annunciation_score is not None else None),
                "grade": clip_quality.get("annunciation_grade"),
            },
            "pack": pack,
        }

    @staticmethod
    def _clamp_float(value: float, *, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    def get_voice_continuity_diagnostics(
        self,
        *,
        run_id: str | None = None,
        limit: int = 200,
        refresh: bool = False,
    ) -> dict[str, Any]:
        readiness = self.get_voice_readiness_report(refresh=refresh)
        resolved_run_id = str(run_id or "").strip() or None
        if not resolved_run_id:
            runs = self.voice_soak.list_runs(limit=1)
            if runs:
                resolved_run_id = str((runs[0] or {}).get("run_id") or "").strip() or None

        soak_report = self.get_voice_continuity_soak_report(run_id=resolved_run_id, limit=max(1, int(limit)))
        axes = soak_report.get("axes") if isinstance(soak_report.get("axes"), dict) else {}
        continuity_axis = axes.get("continuity") if isinstance(axes.get("continuity"), dict) else {}
        mode_axis = axes.get("mode_accuracy") if isinstance(axes.get("mode_accuracy"), dict) else {}
        interruption_axis = (
            axes.get("interruption_recovery")
            if isinstance(axes.get("interruption_recovery"), dict)
            else {}
        )
        latency_axis = axes.get("latency_ladder") if isinstance(axes.get("latency_ladder"), dict) else {}
        phase_b_axis = (
            latency_axis.get("phase_b_first_useful")
            if isinstance(latency_axis.get("phase_b_first_useful"), dict)
            else {}
        )

        turn_count = int(soak_report.get("turn_count") or 0)
        continuity_failure_rate = float(continuity_axis.get("continuity_failure_rate") or 0.0)
        mode_accuracy = mode_axis.get("accuracy")
        if isinstance(mode_accuracy, (int, float)):
            mode_accuracy = float(mode_accuracy)
        else:
            mode_accuracy = None
        interruption_recovery_rate = interruption_axis.get("recovery_rate")
        if isinstance(interruption_recovery_rate, (int, float)):
            interruption_recovery_rate = float(interruption_recovery_rate)
        else:
            interruption_recovery_rate = None
        phase_b_delta = phase_b_axis.get("average_delta_ms")
        if isinstance(phase_b_delta, (int, float)):
            phase_b_delta = float(phase_b_delta)
        else:
            phase_b_delta = None

        readiness_confidence = float(readiness.get("confidence") or 0.0)
        sample_weight = self._clamp_float(float(turn_count) / 40.0, low=0.0, high=0.5)
        empirical_quality = 1.0
        empirical_quality -= continuity_failure_rate * 0.7
        if mode_accuracy is not None:
            empirical_quality -= (1.0 - mode_accuracy) * 0.2
        if interruption_recovery_rate is not None and interruption_recovery_rate < 0.7:
            empirical_quality -= (0.7 - interruption_recovery_rate) * 0.15
        if phase_b_delta is not None and phase_b_delta > 0:
            empirical_quality -= min(0.2, phase_b_delta / 5000.0)
        empirical_quality = self._clamp_float(empirical_quality, low=0.0, high=1.0)
        continuity_confidence = round(
            readiness_confidence * (1.0 - sample_weight) + empirical_quality * sample_weight,
            3,
        )

        enough_turns = turn_count >= 10
        continuity_gate = continuity_failure_rate <= 0.08
        mode_gate = mode_accuracy is None or mode_accuracy >= 0.78
        interruption_gate = interruption_recovery_rate is None or interruption_recovery_rate >= 0.7
        latency_gate = phase_b_delta is None or phase_b_delta <= 700.0
        strict_ready = bool(
            readiness.get("ready_for_production_talk")
            and enough_turns
            and continuity_gate
            and mode_gate
            and interruption_gate
            and latency_gate
        )

        gaps: list[str] = []
        if not bool(readiness.get("ready_for_production_talk")):
            gaps.append("pack_not_ready_for_production_talk")
        if not enough_turns:
            gaps.append("insufficient_soak_turns")
        if not continuity_gate:
            gaps.append("continuity_failure_rate_high")
        if not mode_gate:
            gaps.append("mode_accuracy_low")
        if not interruption_gate:
            gaps.append("interruption_recovery_low")
        if not latency_gate:
            gaps.append("phase_b_latency_delta_high")

        recommendations: list[str] = []
        if "insufficient_soak_turns" in gaps:
            recommendations.append("Record at least 10 voice soak turns before strict continuity gating.")
        if "continuity_failure_rate_high" in gaps:
            recommendations.append("Reduce continuity mismatches; target continuity failure rate <= 8%.")
        if "mode_accuracy_low" in gaps:
            recommendations.append("Tune relationship mode selection and prompts to improve mode accuracy.")
        if "interruption_recovery_low" in gaps:
            recommendations.append("Tune interruption handling to recover >= 70% of interrupted turns.")
        if "phase_b_latency_delta_high" in gaps:
            recommendations.append("Trim phase-b response complexity to reduce first useful response delay.")
        if not recommendations and strict_ready:
            recommendations.append("Diagnostics are strong; keep monitoring continuity confidence over time.")

        summary = (
            "Voice continuity diagnostics pass strict continuity gates."
            if strict_ready
            else "Voice continuity diagnostics indicate remaining gaps before strict continuity."
        )
        return {
            "checked_at": utc_now_iso(),
            "run_id": resolved_run_id,
            "turn_count": turn_count,
            "ready_for_strict_continuity": strict_ready,
            "continuity_confidence": continuity_confidence,
            "summary": summary,
            "gaps": gaps,
            "recommendations": recommendations,
            "metrics": {
                "continuity_failure_rate": round(continuity_failure_rate, 4),
                "mode_accuracy": (round(mode_accuracy, 4) if mode_accuracy is not None else None),
                "interruption_recovery_rate": (
                    round(interruption_recovery_rate, 4)
                    if interruption_recovery_rate is not None
                    else None
                ),
                "phase_b_average_delta_ms": (round(phase_b_delta, 3) if phase_b_delta is not None else None),
            },
            "gates": {
                "pack_ready_for_production_talk": bool(readiness.get("ready_for_production_talk")),
                "enough_turns": enough_turns,
                "continuity": continuity_gate,
                "mode_accuracy": mode_gate,
                "interruption_recovery": interruption_gate,
                "phase_b_latency": latency_gate,
            },
            "readiness": readiness,
            "soak": {
                "run": soak_report.get("run"),
                "by_mode": soak_report.get("by_mode"),
                "by_modality": soak_report.get("by_modality"),
                "axes": soak_report.get("axes"),
            },
        }

    def get_voice_tuning_profile(
        self,
        *,
        run_id: str | None = None,
        limit: int = 200,
        refresh: bool = False,
    ) -> dict[str, Any]:
        readiness = self.get_voice_readiness_report(refresh=refresh)
        overrides_state = self.get_voice_tuning_overrides()
        overrides = (
            overrides_state.get("overrides")
            if isinstance(overrides_state.get("overrides"), dict)
            else {}
        )
        diagnostics = self.get_voice_continuity_diagnostics(
            run_id=run_id,
            limit=limit,
            refresh=refresh,
        )
        quality = str(readiness.get("quality_tier") or "none").strip().lower() or "none"
        target = (
            readiness.get("target_directive_profile")
            if isinstance(readiness.get("target_directive_profile"), dict)
            else {}
        )
        metrics = diagnostics.get("metrics") if isinstance(diagnostics.get("metrics"), dict) else {}
        gates = diagnostics.get("gates") if isinstance(diagnostics.get("gates"), dict) else {}
        pack = readiness.get("pack") if isinstance(readiness.get("pack"), dict) else {}
        pack_profile = str(pack.get("profile") or "").strip().lower()
        clip_quality = pack.get("clip_quality") if isinstance(pack.get("clip_quality"), dict) else {}
        clip_quality_score = clip_quality.get("score")
        try:
            clip_quality_score = (
                self._clamp_float(float(clip_quality_score), low=0.0, high=1.0)
                if clip_quality_score is not None
                else None
            )
        except (TypeError, ValueError):
            clip_quality_score = None
        clip_mean_hiss = clip_quality.get("mean_hiss_ratio")
        clip_mean_harmonicity = clip_quality.get("mean_harmonicity")
        clip_mean_actor_match = clip_quality.get("mean_actor_match_score")
        clip_p10_actor_match = clip_quality.get("p10_actor_match_score")
        clip_mean_movie_match = clip_quality.get("mean_movie_match_score")
        clip_p10_movie_match = clip_quality.get("p10_movie_match_score")
        clip_cadence_score = clip_quality.get("cadence_score")
        clip_annunciation_score = clip_quality.get("annunciation_score")
        clip_cadence_variation_cv = clip_quality.get("cadence_variation_cv")
        try:
            clip_mean_hiss = float(clip_mean_hiss) if clip_mean_hiss is not None else None
        except (TypeError, ValueError):
            clip_mean_hiss = None
        try:
            clip_mean_harmonicity = (
                float(clip_mean_harmonicity) if clip_mean_harmonicity is not None else None
            )
        except (TypeError, ValueError):
            clip_mean_harmonicity = None
        try:
            clip_mean_actor_match = (
                float(clip_mean_actor_match) if clip_mean_actor_match is not None else None
            )
        except (TypeError, ValueError):
            clip_mean_actor_match = None
        try:
            clip_p10_actor_match = (
                float(clip_p10_actor_match) if clip_p10_actor_match is not None else None
            )
        except (TypeError, ValueError):
            clip_p10_actor_match = None
        try:
            clip_mean_movie_match = (
                float(clip_mean_movie_match) if clip_mean_movie_match is not None else None
            )
        except (TypeError, ValueError):
            clip_mean_movie_match = None
        try:
            clip_p10_movie_match = (
                float(clip_p10_movie_match) if clip_p10_movie_match is not None else None
            )
        except (TypeError, ValueError):
            clip_p10_movie_match = None
        try:
            clip_cadence_score = (
                self._clamp_float(float(clip_cadence_score), low=0.0, high=1.0)
                if clip_cadence_score is not None
                else None
            )
        except (TypeError, ValueError):
            clip_cadence_score = None
        try:
            clip_annunciation_score = (
                self._clamp_float(float(clip_annunciation_score), low=0.0, high=1.0)
                if clip_annunciation_score is not None
                else None
            )
        except (TypeError, ValueError):
            clip_annunciation_score = None
        try:
            clip_cadence_variation_cv = (
                self._clamp_float(float(clip_cadence_variation_cv), low=0.0, high=2.0)
                if clip_cadence_variation_cv is not None
                else None
            )
        except (TypeError, ValueError):
            clip_cadence_variation_cv = None

        latency_tier = str(target.get("latency_tier") or "balanced").strip().lower() or "balanced"
        try:
            speed_range = list(target.get("speed_range") or [0.9, 1.0])
        except TypeError:
            speed_range = [0.9, 1.0]
        if len(speed_range) != 2:
            speed_range = [0.9, 1.0]
        speed_min = self._clamp_float(float(speed_range[0]), low=0.82, high=1.18)
        speed_max = self._clamp_float(float(speed_range[1]), low=0.82, high=1.18)
        if speed_min > speed_max:
            speed_min, speed_max = speed_max, speed_min
        try:
            stability_floor = self._clamp_float(float(target.get("stability_floor") or 0.7), low=0.35, high=0.98)
        except (TypeError, ValueError):
            stability_floor = 0.7

        speed_bias = 0.0
        stability_bias = 0.0
        cadence_bias = 0.0
        annunciation_bias = 0.0
        rationale: list[str] = []
        continuity_failure_rate = float(metrics.get("continuity_failure_rate") or 0.0)
        mode_accuracy = metrics.get("mode_accuracy")
        mode_accuracy_value = float(mode_accuracy) if isinstance(mode_accuracy, (int, float)) else None
        interruption_recovery = metrics.get("interruption_recovery_rate")
        interruption_recovery_value = (
            float(interruption_recovery)
            if isinstance(interruption_recovery, (int, float))
            else None
        )
        phase_b_delta = metrics.get("phase_b_average_delta_ms")
        phase_b_delta_value = float(phase_b_delta) if isinstance(phase_b_delta, (int, float)) else None
        strict_ready = bool(diagnostics.get("ready_for_strict_continuity"))
        production_ready = bool(readiness.get("ready_for_production_talk"))
        actor_profile_active = "actor_match" in pack_profile

        if not production_ready:
            speed_max = min(speed_max, 1.0)
            stability_floor = max(stability_floor, 0.74)
            latency_tier = "balanced"
            rationale.append("production_readiness_guard_applied")
        if continuity_failure_rate >= 0.15:
            speed_bias -= 0.035
            stability_bias += 0.06
            latency_tier = "balanced"
            rationale.append("continuity_failure_slowdown")
        elif continuity_failure_rate <= 0.03 and strict_ready:
            speed_bias += 0.012
            stability_bias -= 0.01
            rationale.append("continuity_stable_micro_speedup")
        if mode_accuracy_value is not None and mode_accuracy_value < 0.75:
            stability_bias += 0.03
            speed_bias -= 0.01
            rationale.append("mode_accuracy_guard")
        if interruption_recovery_value is not None and interruption_recovery_value < 0.7:
            stability_bias += 0.025
            speed_bias -= 0.01
            rationale.append("interruption_recovery_guard")
        if phase_b_delta_value is not None and phase_b_delta_value > 800:
            speed_bias += min(0.03, phase_b_delta_value / 30000.0)
            if not strict_ready:
                latency_tier = "balanced"
            rationale.append("phase_b_latency_compensation")
        if quality in {"seed", "none"}:
            speed_max = min(speed_max, 0.98)
            stability_floor = max(stability_floor, 0.78)
            latency_tier = "balanced"
            rationale.append("seed_quality_conservatism")
        elif quality == "production" and strict_ready:
            if bool(gates.get("phase_b_latency")) and bool(gates.get("continuity")):
                latency_tier = "low"
                rationale.append("production_low_latency_unlock")
        if clip_quality_score is not None and clip_quality_score < 0.78:
            speed_bias -= min(0.03, (0.78 - clip_quality_score) * 0.1)
            stability_bias += min(0.065, (0.78 - clip_quality_score) * 0.16)
            rationale.append("clarity_guard")
        if clip_quality_score is not None and clip_quality_score < 0.62:
            speed_max = min(speed_max, 1.0)
            stability_floor = max(stability_floor, 0.74)
            latency_tier = "balanced"
            rationale.append("clarity_strict_guard")
        if clip_mean_hiss is not None and clip_mean_hiss > 0.1:
            speed_bias -= min(0.02, (clip_mean_hiss - 0.1) * 0.45)
            stability_bias += min(0.03, (clip_mean_hiss - 0.1) * 0.5)
            rationale.append("hiss_control")
        if clip_mean_harmonicity is not None and clip_mean_harmonicity < 0.39:
            stability_bias += min(0.025, (0.39 - clip_mean_harmonicity) * 0.3)
            rationale.append("harmonicity_guard")
        if actor_profile_active:
            speed_bias -= 0.014
            stability_bias += 0.018
            speed_min = max(speed_min, 0.93)
            speed_max = min(speed_max, 1.04 if strict_ready else 1.02)
            if clip_mean_actor_match is not None and clip_mean_actor_match >= 0.68:
                speed_bias -= 0.006
                stability_bias += 0.008
            if clip_p10_actor_match is not None and clip_p10_actor_match < 0.48:
                speed_bias -= 0.004
                stability_bias += 0.009
                speed_max = min(speed_max, 1.01)
            if clip_mean_movie_match is not None and clip_mean_movie_match >= 0.66:
                speed_bias -= 0.004
                stability_bias += 0.006
            if clip_p10_movie_match is not None and clip_p10_movie_match < 0.5:
                speed_bias -= 0.003
                stability_bias += 0.007
            if clip_quality_score is not None and clip_quality_score < 0.78:
                latency_tier = "balanced"
            rationale.append("actor_match_anchor")
        if clip_cadence_score is not None:
            if clip_cadence_score < 0.8:
                cadence_bias += min(0.08, (0.8 - clip_cadence_score) * 0.14)
                speed_bias -= min(0.026, (0.8 - clip_cadence_score) * 0.05)
                stability_bias += min(0.04, (0.8 - clip_cadence_score) * 0.08)
                rationale.append("cadence_consistency_guard")
            elif actor_profile_active and clip_cadence_score >= 0.88:
                cadence_bias += 0.014
                speed_bias -= 0.006
                stability_bias += 0.01
                speed_min = max(speed_min, 0.94)
                speed_max = min(speed_max, 1.02)
                rationale.append("cadence_actor_anchor")
        if clip_cadence_variation_cv is not None and clip_cadence_variation_cv > 0.44:
            cadence_bias += min(0.06, (clip_cadence_variation_cv - 0.44) * 0.08)
            speed_bias -= min(0.02, (clip_cadence_variation_cv - 0.44) * 0.05)
            stability_bias += min(0.025, (clip_cadence_variation_cv - 0.44) * 0.05)
            rationale.append("cadence_variation_guard")
        if clip_annunciation_score is not None:
            if clip_annunciation_score < 0.82:
                annunciation_bias += min(0.1, (0.82 - clip_annunciation_score) * 0.17)
                speed_bias -= min(0.03, (0.82 - clip_annunciation_score) * 0.07)
                stability_bias += min(0.05, (0.82 - clip_annunciation_score) * 0.1)
                latency_tier = "balanced"
                rationale.append("annunciation_guard")
            elif actor_profile_active and clip_annunciation_score >= 0.9:
                annunciation_bias += 0.012
                speed_bias -= 0.004
                stability_bias += 0.008
                rationale.append("annunciation_actor_anchor")

        override_applied: list[str] = []
        override_latency_tier = str(overrides.get("latency_tier") or "").strip().lower()
        if override_latency_tier in {"low", "balanced", "quality"}:
            latency_tier = override_latency_tier
            override_applied.append("latency_tier")
        if overrides.get("speed_min") is not None:
            speed_min = self._clamp_float(float(overrides.get("speed_min")), low=0.82, high=1.18)
            override_applied.append("speed_min")
        if overrides.get("speed_max") is not None:
            speed_max = self._clamp_float(float(overrides.get("speed_max")), low=0.82, high=1.18)
            override_applied.append("speed_max")
        if overrides.get("speed_bias") is not None:
            speed_bias += self._clamp_float(float(overrides.get("speed_bias")), low=-0.08, high=0.08)
            override_applied.append("speed_bias")
        if overrides.get("stability_floor") is not None:
            stability_floor = max(
                stability_floor,
                self._clamp_float(float(overrides.get("stability_floor")), low=0.35, high=0.98),
            )
            override_applied.append("stability_floor")
        if overrides.get("stability_bias") is not None:
            stability_bias += self._clamp_float(float(overrides.get("stability_bias")), low=-0.08, high=0.12)
            override_applied.append("stability_bias")
        if overrides.get("cadence_bias") is not None:
            cadence_bias += self._clamp_float(float(overrides.get("cadence_bias")), low=-0.06, high=0.2)
            override_applied.append("cadence_bias")
        if overrides.get("annunciation_bias") is not None:
            annunciation_bias += self._clamp_float(float(overrides.get("annunciation_bias")), low=-0.06, high=0.24)
            override_applied.append("annunciation_bias")
        if bool(overrides.get("prefer_stability")):
            stability_floor = max(stability_floor, 0.76)
            speed_max = min(speed_max, 1.02)
            override_applied.append("prefer_stability")
        if bool(overrides.get("strict_mode_required")) and not strict_ready:
            latency_tier = "balanced"
            speed_max = min(speed_max, 1.0)
            stability_floor = max(stability_floor, 0.74)
            override_applied.append("strict_mode_required")

        speed_bias = round(self._clamp_float(speed_bias, low=-0.08, high=0.08), 4)
        stability_bias = round(self._clamp_float(stability_bias, low=-0.08, high=0.12), 4)
        cadence_bias = round(self._clamp_float(cadence_bias, low=0.0, high=0.24), 4)
        annunciation_bias = round(self._clamp_float(annunciation_bias, low=0.0, high=0.28), 4)
        stability_floor = round(self._clamp_float(stability_floor, low=0.35, high=0.98), 4)
        speed_min = round(self._clamp_float(speed_min, low=0.82, high=1.18), 4)
        speed_max = round(self._clamp_float(speed_max, low=0.82, high=1.18), 4)
        if speed_min > speed_max:
            speed_min, speed_max = speed_max, speed_min
        latency_tier = latency_tier if latency_tier in {"low", "balanced", "quality"} else "balanced"

        readiness_confidence = float(readiness.get("confidence") or 0.0)
        diagnostics_confidence = float(diagnostics.get("continuity_confidence") or readiness_confidence)
        tuning_confidence = round(
            self._clamp_float((readiness_confidence * 0.45) + (diagnostics_confidence * 0.55), low=0.0, high=0.99),
            3,
        )
        override_confidence_floor = overrides.get("confidence_floor")
        if isinstance(override_confidence_floor, (int, float)):
            tuning_confidence = round(
                self._clamp_float(
                    max(tuning_confidence, float(override_confidence_floor)),
                    low=0.0,
                    high=0.99,
                ),
                3,
            )
            override_applied.append("confidence_floor")
        profile = {
            "latency_tier": latency_tier,
            "speed_min": speed_min,
            "speed_max": speed_max,
            "speed_bias": speed_bias,
            "stability_floor": stability_floor,
            "stability_bias": stability_bias,
            "cadence_bias": cadence_bias,
            "annunciation_bias": annunciation_bias,
        }
        profile_seed = json.dumps(
            {
                "profile": profile,
                "run_id": diagnostics.get("run_id"),
                "quality": quality,
                "strict_ready": strict_ready,
                "override_revision": overrides_state.get("revision"),
            },
            sort_keys=True,
        )
        profile_id = hashlib.sha1(profile_seed.encode("utf-8")).hexdigest()[:16]

        if not rationale:
            rationale.append("baseline_target_profile")
        if override_applied:
            rationale.append("manual_override_applied")
        max_speed_step = (
            0.025
            if strict_ready and tuning_confidence >= 0.88
            else (0.032 if production_ready else 0.045)
        )
        max_stability_step = (
            0.03
            if strict_ready and tuning_confidence >= 0.88
            else (0.045 if production_ready else 0.06)
        )
        smooth_alpha_speed = (
            0.42
            if strict_ready and tuning_confidence >= 0.88
            else (0.5 if production_ready else 0.58)
        )
        smooth_alpha_stability = (
            0.36
            if strict_ready and tuning_confidence >= 0.88
            else (0.44 if production_ready else 0.52)
        )
        speed_upward_step_ratio = 0.78 if strict_ready else (0.72 if production_ready else 0.66)
        stability_upward_step_ratio = 0.84 if strict_ready else (0.78 if production_ready else 0.72)
        jitter_deadband_speed = 0.006 if production_ready else 0.008
        jitter_deadband_stability = 0.008 if production_ready else 0.01
        history_anchor_weight = 0.38 if strict_ready else (0.3 if production_ready else 0.22)
        flow_inertia = 0.5 if strict_ready else (0.58 if production_ready else 0.64)
        flow_oscillation_guard = 0.42 if strict_ready else (0.5 if production_ready else 0.58)
        flow_release_speed_ratio = 0.78 if strict_ready else (0.72 if production_ready else 0.64)
        flow_release_stability_ratio = 0.84 if strict_ready else (0.78 if production_ready else 0.7)
        flow_follow_through = 0.44 if strict_ready else (0.52 if production_ready else 0.6)
        flow_plateau_release_speed = 0.26 if strict_ready else (0.32 if production_ready else 0.38)
        flow_plateau_release_stability = 0.22 if strict_ready else (0.28 if production_ready else 0.34)
        if clip_quality_score is not None and clip_quality_score < 0.72:
            max_speed_step = min(max_speed_step, 0.028 if production_ready else 0.036)
            max_stability_step = min(max_stability_step, 0.04 if production_ready else 0.05)
            smooth_alpha_speed += 0.08
            smooth_alpha_stability += 0.08
            speed_upward_step_ratio -= 0.06
            stability_upward_step_ratio -= 0.06
            jitter_deadband_speed = max(jitter_deadband_speed, 0.009)
            jitter_deadband_stability = max(jitter_deadband_stability, 0.012)
            history_anchor_weight = min(0.58, history_anchor_weight + 0.12)
            flow_inertia += 0.08
            flow_oscillation_guard += 0.08
            flow_release_speed_ratio -= 0.07
            flow_release_stability_ratio -= 0.06
            flow_follow_through += 0.06
            flow_plateau_release_speed += 0.06
            flow_plateau_release_stability += 0.05
        if actor_profile_active:
            max_speed_step = min(max_speed_step, 0.024 if strict_ready else 0.029)
            max_stability_step = min(max_stability_step, 0.036 if strict_ready else 0.042)
            smooth_alpha_speed += 0.07
            smooth_alpha_stability += 0.06
            speed_upward_step_ratio -= 0.08
            stability_upward_step_ratio -= 0.06
            if clip_mean_movie_match is not None and clip_mean_movie_match >= 0.66:
                smooth_alpha_speed += 0.04
                smooth_alpha_stability += 0.03
                speed_upward_step_ratio -= 0.04
            if clip_p10_movie_match is not None and clip_p10_movie_match < 0.5:
                smooth_alpha_speed += 0.03
                smooth_alpha_stability += 0.03
            jitter_deadband_speed = max(jitter_deadband_speed, 0.007)
            jitter_deadband_stability = max(jitter_deadband_stability, 0.01)
            history_anchor_weight = min(0.68, history_anchor_weight + 0.08)
            flow_inertia += 0.06
            flow_oscillation_guard += 0.05
            flow_release_speed_ratio -= 0.04
            flow_release_stability_ratio -= 0.03
            flow_follow_through += 0.04
            flow_plateau_release_speed += 0.03
            flow_plateau_release_stability += 0.03
        if cadence_bias > 0.0:
            smooth_alpha_speed += min(0.2, cadence_bias * 1.15)
            smooth_alpha_stability += min(0.12, cadence_bias * 0.65)
            speed_upward_step_ratio -= min(0.2, cadence_bias * 1.05)
            stability_upward_step_ratio -= min(0.12, cadence_bias * 0.7)
            history_anchor_weight = min(0.8, history_anchor_weight + min(0.16, cadence_bias * 0.9))
            flow_inertia += min(0.16, cadence_bias * 0.9)
            flow_oscillation_guard += min(0.16, cadence_bias * 0.82)
            flow_release_speed_ratio -= min(0.16, cadence_bias * 0.86)
            flow_release_stability_ratio -= min(0.12, cadence_bias * 0.7)
            flow_follow_through += min(0.16, cadence_bias * 0.78)
            flow_plateau_release_speed += min(0.12, cadence_bias * 0.7)
            flow_plateau_release_stability += min(0.09, cadence_bias * 0.52)
        if annunciation_bias > 0.0:
            smooth_alpha_stability += min(0.2, annunciation_bias * 0.95)
            smooth_alpha_speed += min(0.08, annunciation_bias * 0.35)
            stability_upward_step_ratio -= min(0.18, annunciation_bias * 0.8)
            speed_upward_step_ratio -= min(0.08, annunciation_bias * 0.3)
            history_anchor_weight = min(0.82, history_anchor_weight + min(0.14, annunciation_bias * 0.7))
            flow_inertia += min(0.14, annunciation_bias * 0.6)
            flow_oscillation_guard += min(0.14, annunciation_bias * 0.65)
            flow_release_speed_ratio -= min(0.1, annunciation_bias * 0.45)
            flow_release_stability_ratio -= min(0.13, annunciation_bias * 0.58)
            flow_follow_through += min(0.08, annunciation_bias * 0.4)
            flow_plateau_release_speed += min(0.05, annunciation_bias * 0.24)
            flow_plateau_release_stability += min(0.08, annunciation_bias * 0.38)
        if clip_cadence_variation_cv is not None and clip_cadence_variation_cv > 0.5:
            smooth_alpha_speed += min(0.08, (clip_cadence_variation_cv - 0.5) * 0.15)
            speed_upward_step_ratio -= min(0.07, (clip_cadence_variation_cv - 0.5) * 0.12)
            flow_inertia += min(0.08, (clip_cadence_variation_cv - 0.5) * 0.2)
            flow_oscillation_guard += min(0.12, (clip_cadence_variation_cv - 0.5) * 0.28)
            flow_release_speed_ratio -= min(0.08, (clip_cadence_variation_cv - 0.5) * 0.18)
            flow_follow_through += min(0.08, (clip_cadence_variation_cv - 0.5) * 0.14)
            flow_plateau_release_speed += min(0.06, (clip_cadence_variation_cv - 0.5) * 0.12)
        if clip_annunciation_score is not None and clip_annunciation_score < 0.72:
            smooth_alpha_stability += min(0.1, (0.72 - clip_annunciation_score) * 0.22)
            stability_upward_step_ratio -= min(0.1, (0.72 - clip_annunciation_score) * 0.2)
            flow_inertia += min(0.06, (0.72 - clip_annunciation_score) * 0.18)
            flow_oscillation_guard += min(0.1, (0.72 - clip_annunciation_score) * 0.24)
            flow_release_stability_ratio -= min(0.08, (0.72 - clip_annunciation_score) * 0.2)
            flow_follow_through += min(0.04, (0.72 - clip_annunciation_score) * 0.14)
            flow_plateau_release_stability += min(0.05, (0.72 - clip_annunciation_score) * 0.1)
        continuity_smoothing = {
            "max_speed_step": round(self._clamp_float(max_speed_step, low=0.005, high=0.12), 4),
            "max_stability_step": round(self._clamp_float(max_stability_step, low=0.01, high=0.2), 4),
            "smooth_alpha_speed": round(
                self._clamp_float(smooth_alpha_speed, low=0.0, high=0.92),
                4,
            ),
            "smooth_alpha_stability": round(
                self._clamp_float(smooth_alpha_stability, low=0.0, high=0.92),
                4,
            ),
            "speed_upward_step_ratio": round(
                self._clamp_float(speed_upward_step_ratio, low=0.25, high=1.5),
                4,
            ),
            "stability_upward_step_ratio": round(
                self._clamp_float(stability_upward_step_ratio, low=0.25, high=1.5),
                4,
            ),
            "jitter_deadband_speed": round(
                self._clamp_float(jitter_deadband_speed, low=0.0, high=0.03),
                4,
            ),
            "jitter_deadband_stability": round(
                self._clamp_float(jitter_deadband_stability, low=0.0, high=0.05),
                4,
            ),
            "history_anchor_weight": round(
                self._clamp_float(history_anchor_weight, low=0.0, high=0.85),
                4,
            ),
            "flow_inertia": round(
                self._clamp_float(flow_inertia, low=0.0, high=0.9),
                4,
            ),
            "flow_oscillation_guard": round(
                self._clamp_float(flow_oscillation_guard, low=0.0, high=1.0),
                4,
            ),
            "flow_release_speed_ratio": round(
                self._clamp_float(flow_release_speed_ratio, low=0.15, high=1.2),
                4,
            ),
            "flow_release_stability_ratio": round(
                self._clamp_float(flow_release_stability_ratio, low=0.15, high=1.2),
                4,
            ),
            "flow_follow_through": round(
                self._clamp_float(flow_follow_through, low=0.0, high=1.0),
                4,
            ),
            "flow_plateau_release_speed": round(
                self._clamp_float(flow_plateau_release_speed, low=0.0, high=1.0),
                4,
            ),
            "flow_plateau_release_stability": round(
                self._clamp_float(flow_plateau_release_stability, low=0.0, high=1.0),
                4,
            ),
            "allow_latency_drop_to_low": bool(strict_ready or (production_ready and tuning_confidence >= 0.9)),
        }
        return {
            "checked_at": utc_now_iso(),
            "profile_id": profile_id,
            "confidence": tuning_confidence,
            "ready_for_production_talk": production_ready,
            "ready_for_strict_continuity": strict_ready,
            "quality_tier": quality,
            "run_id": diagnostics.get("run_id"),
            "rationale": rationale,
            "override_revision": overrides_state.get("revision"),
            "override_applied": sorted(set(override_applied)),
            "clip_quality_score": clip_quality_score,
            "actor_profile_active": actor_profile_active,
            "movie_match_score": clip_mean_movie_match,
            "cadence_score": clip_cadence_score,
            "annunciation_score": clip_annunciation_score,
            "cadence_variation_cv": clip_cadence_variation_cv,
            "profile": profile,
            "continuity_smoothing": continuity_smoothing,
            "readiness_summary": readiness.get("summary"),
            "diagnostics_summary": diagnostics.get("summary"),
            "overrides": overrides_state,
            "readiness": readiness,
            "diagnostics": diagnostics,
        }

    def get_voice_tuning_overrides(self) -> dict[str, Any]:
        return self.voice_tuning_state.get_overrides()

    def update_voice_tuning_overrides(
        self,
        *,
        patch: dict[str, Any],
        replace: bool = False,
        actor: str = "operator",
    ) -> dict[str, Any]:
        updated = self.voice_tuning_state.update_overrides(
            patch=patch,
            replace=replace,
            actor=actor,
        )
        self.memory.append_event(
            "presence.voice_tuning_overrides_updated",
            {
                "revision": updated.get("revision"),
                "replace": bool(replace),
                "keys": sorted(list((updated.get("overrides") or {}).keys())),
            },
        )
        return updated

    def reset_voice_tuning_overrides(self, *, actor: str = "operator") -> dict[str, Any]:
        reset = self.voice_tuning_state.clear_overrides(actor=actor)
        self.memory.append_event(
            "presence.voice_tuning_overrides_reset",
            {
                "revision": reset.get("revision"),
            },
        )
        return reset

    def list_voice_tuning_override_events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return self.voice_tuning_state.list_events(limit=limit)

    def start_voice_continuity_soak(
        self,
        *,
        run_id: str | None = None,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.voice_soak.start_run(run_id=run_id, label=label, metadata=metadata)
        self.memory.append_event(
            "presence.voice_soak_started",
            {
                "run_id": run.get("run_id"),
                "label": run.get("label"),
            },
        )
        return run

    def _compute_tone_drift(
        self,
        *,
        tone_before: dict[str, Any] | None,
        tone_after: dict[str, Any] | None,
    ) -> dict[str, float]:
        before = dict(tone_before or {})
        after = dict(tone_after or {})
        drift: dict[str, float] = {}
        keys = {str(key) for key in before.keys()} | {str(key) for key in after.keys()}
        for key in sorted(keys):
            before_value = before.get(key)
            after_value = after.get(key)
            if before_value is None or after_value is None:
                continue
            try:
                drift[key] = round(float(after_value) - float(before_value), 4)
            except (TypeError, ValueError):
                continue
        return drift

    def record_voice_continuity_soak_turn(
        self,
        *,
        run_id: str,
        draft: dict[str, Any],
        observed_latencies_ms: dict[str, Any] | None = None,
        interrupted: bool = False,
        interruption_recovered: bool = False,
        expected_mode: str | None = None,
        pushback_outcome: str = "none",
        mismatch_suppressed: bool = False,
        note: str | None = None,
    ) -> dict[str, Any]:
        active_run = self.voice_soak.get_run(str(run_id))
        if not active_run:
            active_run = self.start_voice_continuity_soak(run_id=run_id, label=None, metadata={"auto_created": True})

        before_snapshot = self.tone_balance.latest()
        before_profile = (
            before_snapshot.get("profile")
            if isinstance(before_snapshot, dict) and isinstance(before_snapshot.get("profile"), dict)
            else {}
        )
        prepared = self.prepare_openclaw_voice_reply(dict(draft or {}))
        continuity = prepared.get("continuity") if isinstance(prepared.get("continuity"), dict) else {}
        mode = prepared.get("mode") if isinstance(prepared.get("mode"), dict) else {}
        voice = prepared.get("voice") if isinstance(prepared.get("voice"), dict) else {}
        ladder = prepared.get("latency_ladder") if isinstance(prepared.get("latency_ladder"), dict) else {}
        targets = ladder.get("targets_ms") if isinstance(ladder.get("targets_ms"), dict) else {}
        tone_after = (
            prepared.get("tone_balance", {}).get("profile")
            if isinstance(prepared.get("tone_balance"), dict)
            and isinstance((prepared.get("tone_balance") or {}).get("profile"), dict)
            else {}
        )
        tone_drift = self._compute_tone_drift(tone_before=before_profile, tone_after=tone_after)

        observed = dict(observed_latencies_ms or {})
        phase_a_observed = observed.get("phase_a_presence")
        phase_b_observed = observed.get("phase_b_first_useful")
        phase_c_observed = observed.get("phase_c_deep_followup")

        selected_mode = str(mode.get("mode") or "").strip().lower() or None
        expected_mode_normalized = str(expected_mode or "").strip().lower() or None
        mode_match = (
            None
            if not expected_mode_normalized or not selected_mode
            else expected_mode_normalized == selected_mode
        )
        pushback_record = prepared.get("pushback_record") if isinstance(prepared.get("pushback_record"), dict) else None
        pushback_triggered = bool(pushback_record)

        surface_id = str((draft or {}).get("surface_id") or continuity.get("surface_id") or "voice:unknown").strip()
        session_id = str((draft or {}).get("session_id") or continuity.get("session_id") or "default").strip()
        session = self.get_surface_session(surface_id=surface_id, session_id=session_id) or {}

        turn = self.voice_soak.record_turn(
            run_id=str(active_run.get("run_id") or run_id),
            surface_id=surface_id,
            session_id=session_id,
            channel_type=str((session or {}).get("channel_type") or "").strip() or "voice",
            modality=str(voice.get("modality") or "voice"),
            expected_mode=expected_mode_normalized,
            selected_mode=selected_mode,
            mode_match=mode_match,
            contract_hash=str(continuity.get("active_contract_hash") or "").strip() or None,
            user_model_revision=str(continuity.get("active_user_model_revision") or "").strip() or None,
            pushback_calibration_revision=(
                str(continuity.get("active_pushback_calibration_revision") or "").strip() or None
            ),
            continuity_ok=bool(continuity.get("continuity_ok", True)),
            continuity_mismatches=(
                continuity.get("mismatches")
                if isinstance(continuity.get("mismatches"), list)
                else []
            ),
            mismatch_suppressed=bool(mismatch_suppressed),
            phase_a_target_ms=(targets.get("phase_a_presence") if isinstance(targets.get("phase_a_presence"), (int, float)) else None),
            phase_b_target_ms=(targets.get("phase_b_first_useful") if isinstance(targets.get("phase_b_first_useful"), (int, float)) else None),
            phase_c_target_ms=(targets.get("phase_c_deep_followup") if isinstance(targets.get("phase_c_deep_followup"), (int, float)) else None),
            phase_a_observed_ms=(float(phase_a_observed) if isinstance(phase_a_observed, (int, float)) else None),
            phase_b_observed_ms=(float(phase_b_observed) if isinstance(phase_b_observed, (int, float)) else None),
            phase_c_observed_ms=(float(phase_c_observed) if isinstance(phase_c_observed, (int, float)) else None),
            interrupted=bool(interrupted),
            interruption_recovered=bool(interruption_recovered),
            pushback_triggered=pushback_triggered,
            pushback_outcome=str(pushback_outcome or "none"),
            tone_before=before_profile,
            tone_after=tone_after,
            tone_drift=tone_drift,
            note=note,
        )
        self.memory.append_event(
            "presence.voice_soak_turn_recorded",
            {
                "run_id": turn.get("run_id"),
                "turn_id": turn.get("turn_id"),
                "continuity_ok": turn.get("continuity_ok"),
                "selected_mode": turn.get("selected_mode"),
                "mode_match": turn.get("mode_match"),
                "pushback_triggered": turn.get("pushback_triggered"),
                "interrupted": turn.get("interrupted"),
            },
        )
        return {
            "run": self.voice_soak.get_run(str(active_run.get("run_id") or run_id)),
            "turn": turn,
            "prepared_reply": prepared,
        }

    def get_voice_continuity_soak_report(
        self,
        *,
        run_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        turns = self.voice_soak.list_turns(run_id=run_id, limit=limit)
        run = self.voice_soak.get_run(run_id) if run_id else None
        total = len(turns)
        if total == 0:
            return {
                "run": run,
                "turn_count": 0,
                "axes": {
                    "continuity": {},
                    "mode_accuracy": {},
                    "latency_ladder": {},
                    "interruption_recovery": {},
                    "tone_balance": {},
                    "pushback": {},
                },
                "items": [],
            }

        def _avg(values: list[float]) -> float | None:
            if not values:
                return None
            return round(sum(values) / len(values), 3)

        continuity_failures = [item for item in turns if not bool(item.get("continuity_ok"))]
        mismatch_suppressed = [item for item in turns if bool(item.get("mismatch_suppressed"))]

        mode_scored = [item for item in turns if item.get("mode_match") is not None]
        mode_matched = [item for item in mode_scored if bool(item.get("mode_match"))]

        interrupted_turns = [item for item in turns if bool(item.get("interrupted"))]
        recovered_turns = [item for item in interrupted_turns if bool(item.get("interruption_recovered"))]

        pushback_turns = [item for item in turns if bool(item.get("pushback_triggered"))]
        pushback_outcomes: dict[str, int] = {}
        for item in pushback_turns:
            key = str(item.get("pushback_outcome") or "none")
            pushback_outcomes[key] = pushback_outcomes.get(key, 0) + 1

        phase_metrics: dict[str, dict[str, Any]] = {}
        for key in ("phase_a_presence", "phase_b_first_useful", "phase_c_deep_followup"):
            observed_values: list[float] = []
            delta_values: list[float] = []
            for item in turns:
                latency = item.get("latency") if isinstance(item.get("latency"), dict) else {}
                observed_map = latency.get("observed_ms") if isinstance(latency.get("observed_ms"), dict) else {}
                delta_map = latency.get("delta_ms") if isinstance(latency.get("delta_ms"), dict) else {}
                observed = observed_map.get(key)
                delta = delta_map.get(key)
                if isinstance(observed, (int, float)):
                    observed_values.append(float(observed))
                if isinstance(delta, (int, float)):
                    delta_values.append(float(delta))
            phase_metrics[key] = {
                "observed_samples": len(observed_values),
                "average_observed_ms": _avg(observed_values),
                "average_delta_ms": _avg(delta_values),
            }

        tone_abs: dict[str, list[float]] = {}
        for item in turns:
            drift = item.get("tone_drift") if isinstance(item.get("tone_drift"), dict) else {}
            for key, value in drift.items():
                if isinstance(value, (int, float)):
                    tone_abs.setdefault(str(key), []).append(abs(float(value)))
        tone_avg_abs = {key: _avg(values) for key, values in tone_abs.items()}
        dominant_tone_drift = sorted(tone_avg_abs.items(), key=lambda kv: (kv[1] or 0.0), reverse=True)

        by_mode: dict[str, int] = {}
        by_modality: dict[str, int] = {}
        for item in turns:
            mode_key = str(item.get("selected_mode") or "unknown")
            modality_key = str(item.get("modality") or "unknown")
            by_mode[mode_key] = by_mode.get(mode_key, 0) + 1
            by_modality[modality_key] = by_modality.get(modality_key, 0) + 1

        return {
            "run": run,
            "turn_count": total,
            "by_mode": by_mode,
            "by_modality": by_modality,
            "axes": {
                "continuity": {
                    "continuity_failures": len(continuity_failures),
                    "continuity_failure_rate": round(len(continuity_failures) / total, 4),
                    "mismatch_suppressed_count": len(mismatch_suppressed),
                },
                "mode_accuracy": {
                    "scored_turns": len(mode_scored),
                    "matches": len(mode_matched),
                    "accuracy": (round(len(mode_matched) / len(mode_scored), 4) if mode_scored else None),
                },
                "latency_ladder": phase_metrics,
                "interruption_recovery": {
                    "interrupted_turns": len(interrupted_turns),
                    "recovered_turns": len(recovered_turns),
                    "recovery_rate": (
                        round(len(recovered_turns) / len(interrupted_turns), 4)
                        if interrupted_turns
                        else None
                    ),
                },
                "tone_balance": {
                    "average_abs_drift": tone_avg_abs,
                    "dominant_drift": [
                        {"dimension": key, "average_abs_delta": value}
                        for key, value in dominant_tone_drift
                    ][:5],
                },
                "pushback": {
                    "triggered_turns": len(pushback_turns),
                    "trigger_rate": round(len(pushback_turns) / total, 4),
                    "outcomes": pushback_outcomes,
                },
            },
            "items": turns,
        }

    def run_taskflow_presence_cycle(self, *, reason: str = "taskflow_presence_cycle") -> dict[str, Any]:
        outcome = self.taskflow_presence_runner.run_cycle(reason=reason)
        self.memory.append_event(
            "presence.taskflow_cycle",
            {
                "reason": reason,
                "unresolved_risk_count": outcome.get("unresolved_risk_count"),
            },
        )
        return outcome

    def decide_relationship_mode(
        self,
        *,
        explicit_directive: bool = False,
        disputed: bool = False,
        high_stakes: bool = False,
        uncertainty: float = 0.0,
        force_mode: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adaptive = self.get_adaptive_policy()
        relationship = adaptive.get("relationship_mode") if isinstance(adaptive.get("relationship_mode"), dict) else {}
        context_map = dict(context or {})
        for key in (
            "uncertainty_strategist_threshold",
            "high_stakes_prefers_strategist",
            "disputed_prefers_strategist",
            "explicit_directive_to_butler",
        ):
            if key not in context_map and relationship.get(key) is not None:
                context_map[key] = relationship.get(key)
        decision = self.relationship_modes.decide(
            explicit_directive=explicit_directive,
            disputed=disputed,
            high_stakes=high_stakes,
            uncertainty=uncertainty,
            force_mode=force_mode,
            context=context_map,
        )
        self.memory.append_event(
            "relationship.mode_decided",
            {
                "decision_id": decision.get("decision_id"),
                "mode": decision.get("mode"),
                "reason": decision.get("reason"),
                "confidence": decision.get("confidence"),
            },
        )
        return decision

    def get_presence_mode(self) -> dict[str, Any]:
        latest = self.relationship_modes.latest()
        if latest:
            return latest
        return self.decide_relationship_mode(context={"auto": "initial"})

    def get_presence_constraints(self) -> dict[str, Any]:
        gateway = self.get_openclaw_gateway_status()
        pondering_mode = self.get_pondering_mode()
        return {
            "single_owner_boundary": True,
            "canonical_mind": "jarvis_core",
            "openclaw_role": "presence_transport",
            "approval_classes": ["P0", "P1", "P2", "P3", "P4"],
            "untrusted_content_requires_sanitization": True,
            "markets_execution_disabled": True,
            "ingest_token_required": self.ingest_token_required(),
            "host_exec_requires_explicit_operator_gate": True,
            "gateway_protocol_profile_id": gateway.get("protocol_profile_id"),
            "gateway_pairing_state": gateway.get("pairing_state"),
            "gateway_connect_handshake_state": gateway.get("connect_handshake_state"),
            "pondering_mode": pondering_mode,
        }

    def list_dialogue_threads(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.dialogue_state.list_threads(status=status, limit=limit)

    def get_dialogue_thread_snapshot(
        self,
        *,
        surface_id: str,
        session_id: str,
        turn_limit: int = 20,
    ) -> dict[str, Any]:
        thread = self.dialogue_state.get_thread(surface_id=surface_id, session_id=session_id)
        if not thread:
            return {
                "surface_id": str(surface_id),
                "session_id": str(session_id),
                "thread": None,
                "turns": [],
                "retrieval_diagnostics": {
                    "turns_with_memory_snippets": 0,
                    "total_snippets_used": 0,
                    "strategy_counts": {},
                },
            }
        thread_id = str(thread.get("thread_id") or "").strip()
        turns = self.dialogue_state.list_recent_turns(
            thread_id=thread_id,
            limit=max(1, int(turn_limit)),
        ) if thread_id else []
        retrieval_samples = 0
        retrieval_enabled_turns = 0
        strategy_counts: dict[str, int] = {}
        for item in turns:
            if not isinstance(item, dict):
                continue
            critique = item.get("critique") if isinstance(item.get("critique"), dict) else {}
            retrieval = critique.get("retrieval") if isinstance(critique.get("retrieval"), dict) else {}
            snippet_count = int(retrieval.get("snippet_count") or 0)
            if snippet_count > 0:
                retrieval_enabled_turns += 1
                retrieval_samples += snippet_count
            strategy = retrieval.get("strategy") if isinstance(retrieval.get("strategy"), dict) else {}
            if bool(strategy.get("embedding_rerank")):
                strategy_counts["embedding_rerank"] = strategy_counts.get("embedding_rerank", 0) + 1
            if bool(strategy.get("flag_rerank")):
                strategy_counts["flag_rerank"] = strategy_counts.get("flag_rerank", 0) + 1
        return {
            "surface_id": str(surface_id),
            "session_id": str(session_id),
            "thread": thread,
            "turns": turns,
            "turn_count": len(turns),
            "retrieval_diagnostics": {
                "turns_with_memory_snippets": retrieval_enabled_turns,
                "total_snippets_used": retrieval_samples,
                "strategy_counts": strategy_counts,
            },
        }

    def get_dialogue_retrieval_config(self) -> dict[str, Any]:
        return self.dialogue_retriever.get_config()

    def get_presence_trust_axes(
        self,
        *,
        node_id: str | None = None,
        command: str | None = None,
    ) -> dict[str, Any]:
        gateway = self.get_openclaw_gateway_status()
        handshake_state = str(gateway.get("connect_handshake_state") or "unknown")
        handshake_required = bool(gateway.get("connect_handshake_required"))
        handshake_ok = (
            handshake_state == "acked"
            or (not handshake_required and handshake_state in {"not_required", "unknown", ""})
        )

        resolved_node_id = str(node_id or gateway.get("paired_node_id") or "").strip() or None
        node_state = self.device_tokens.get_node(resolved_node_id) if resolved_node_id else None
        pairing_status = str((node_state or {}).get("pairing_status") or gateway.get("pairing_state") or "unknown")
        pairing_ok = pairing_status in {"approved", "paired", "rotated"}
        token_refs_present = bool(
            (node_state or {}).get("gateway_token_ref")
            and (node_state or {}).get("node_token_ref")
        )

        probe_command = str(command or "notifications.send").strip() or "notifications.send"
        command_policy = self.node_command_broker.broker(
            command=probe_command,
            payload={"probe": True},
            actor="presence_soak_probe",
        )
        command_policy_ok = bool(command_policy.get("allowed"))
        command_requires_approval = bool(command_policy.get("requires_approval"))

        overall_ready = bool(
            gateway.get("connected")
            and gateway.get("commands_enabled")
            and handshake_ok
            and pairing_ok
        )
        return {
            "overall_ready_for_device_commands": overall_ready,
            "gateway_handshake": {
                "required": handshake_required,
                "state": handshake_state,
                "ok": handshake_ok,
                "connected": bool(gateway.get("connected")),
            },
            "pairing_token": {
                "node_id": resolved_node_id,
                "pairing_status": pairing_status,
                "ok": pairing_ok,
                "token_refs_present": token_refs_present,
                "rotated_at": (node_state or {}).get("rotated_at"),
                "revoked_at": (node_state or {}).get("revoked_at"),
            },
            "command_policy": {
                "probe_command": probe_command,
                "ok_for_presence_lane": command_policy_ok and not command_requires_approval,
                "decision": command_policy,
            },
            "gateway_snapshot": {
                "commands_enabled": bool(gateway.get("commands_enabled")),
                "pairing_state": gateway.get("pairing_state"),
                "connect_handshake_state": gateway.get("connect_handshake_state"),
                "last_error": gateway.get("last_error"),
            },
        }

    def get_presence_continuity_snapshot(
        self,
        *,
        surface_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        active_contract_hash = self.get_consciousness_contract_hash()
        active_user_model_revision = self.get_user_model_revision()
        active_pushback_revision = self.get_pushback_calibration_revision()
        mode = self.get_presence_mode()
        active_relationship_mode = str(mode.get("mode") or "").strip() or None

        session = None
        if str(surface_id or "").strip() and str(session_id or "").strip():
            session = self.get_surface_session(
                surface_id=str(surface_id),
                session_id=str(session_id),
            )

        session_metadata = (session or {}).get("metadata") if isinstance((session or {}).get("metadata"), dict) else {}
        session_contract_hash = str((session or {}).get("last_seen_contract_hash") or "").strip() or None
        session_relationship_mode = str((session or {}).get("last_relationship_mode") or "").strip() or None
        session_user_model_revision = str(session_metadata.get("user_model_revision") or "").strip() or None
        session_pushback_revision = str(session_metadata.get("pushback_calibration_revision") or "").strip() or None

        mismatches: list[str] = []
        if session_contract_hash and session_contract_hash != active_contract_hash:
            mismatches.append("contract_hash_mismatch")
        if session_relationship_mode and active_relationship_mode and session_relationship_mode != active_relationship_mode:
            mismatches.append("relationship_mode_mismatch")
        if session_user_model_revision and active_user_model_revision and session_user_model_revision != active_user_model_revision:
            mismatches.append("user_model_revision_mismatch")
        if session_pushback_revision and active_pushback_revision and session_pushback_revision != active_pushback_revision:
            mismatches.append("pushback_calibration_revision_mismatch")

        continuity_ok = not mismatches
        return {
            "session_key": (session or {}).get("session_key"),
            "surface_id": str(surface_id or "").strip() or None,
            "session_id": str(session_id or "").strip() or None,
            "active": {
                "contract_hash": active_contract_hash,
                "relationship_mode": active_relationship_mode,
                "user_model_revision": active_user_model_revision,
                "pushback_calibration_revision": active_pushback_revision,
            },
            "session_view": {
                "contract_hash": session_contract_hash,
                "relationship_mode": session_relationship_mode,
                "user_model_revision": session_user_model_revision,
                "pushback_calibration_revision": session_pushback_revision,
            },
            "continuity_ok": continuity_ok,
            "mismatches": mismatches,
            "checked_at": utc_now_iso(),
        }

    def check_presence_continuity_freeze(
        self,
        *,
        primary_surface_id: str,
        primary_session_id: str,
        secondary_surface_id: str,
        secondary_session_id: str,
    ) -> dict[str, Any]:
        primary = self.get_presence_continuity_snapshot(
            surface_id=primary_surface_id,
            session_id=primary_session_id,
        )
        secondary = self.get_presence_continuity_snapshot(
            surface_id=secondary_surface_id,
            session_id=secondary_session_id,
        )

        mismatches: list[str] = []
        keys = (
            "contract_hash",
            "relationship_mode",
            "user_model_revision",
            "pushback_calibration_revision",
        )
        for key in keys:
            primary_value = (
                (primary.get("session_view") or {}).get(key)
                or (primary.get("active") or {}).get(key)
            )
            secondary_value = (
                (secondary.get("session_view") or {}).get(key)
                or (secondary.get("active") or {}).get(key)
            )
            if primary_value and secondary_value and primary_value != secondary_value:
                mismatches.append(f"{key}_cross_surface_mismatch")

        freeze_ok = bool(primary.get("continuity_ok")) and bool(secondary.get("continuity_ok")) and not mismatches
        report = {
            "freeze_ok": freeze_ok,
            "mismatches": mismatches,
            "primary": primary,
            "secondary": secondary,
            "checked_at": utc_now_iso(),
        }
        self.memory.append_event(
            "presence.continuity_freeze_checked",
            {
                "freeze_ok": freeze_ok,
                "mismatch_count": len(mismatches),
                "primary_session_key": primary.get("session_key"),
                "secondary_session_key": secondary.get("session_key"),
            },
        )
        return report

    @staticmethod
    def _extract_json_payload(raw_text: str) -> dict[str, Any] | list[Any] | None:
        text = str(raw_text or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            pass
        for idx, char in enumerate(text):
            if char not in {"{", "["}:
                continue
            candidate = text[idx:].strip()
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, (dict, list)):
                return parsed
        return None

    def _run_openclaw_cli(
        self,
        *,
        args: list[str],
        timeout_seconds: float = 20.0,
        expect_json: bool = False,
    ) -> dict[str, Any]:
        quoted_args = " ".join(shlex.quote(str(item)) for item in args if str(item).strip())
        shell_cmd = f"source ~/.nvm/nvm.sh && nvm use 22 >/dev/null && {quoted_args}"
        try:
            completed = subprocess.run(
                ["zsh", "-lc", shell_cmd],
                capture_output=True,
                text=True,
                check=False,
                timeout=max(1.0, float(timeout_seconds)),
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "returncode": None,
                "stdout": str(exc.stdout or ""),
                "stderr": str(exc.stderr or ""),
                "json": None,
                "error": "openclaw_cli_timeout",
            }
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        parsed_json = self._extract_json_payload(stdout) if expect_json else None
        return {
            "ok": completed.returncode == 0,
            "returncode": int(completed.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "json": parsed_json,
            "error": None if completed.returncode == 0 else (stderr.strip() or stdout.strip() or "openclaw_cli_failed"),
        }

    def _openclaw_cli_json(
        self,
        *,
        args: list[str],
        timeout_seconds: float = 20.0,
        expected_type: type[dict[str, Any]] | type[list[Any]] | None = None,
    ) -> dict[str, Any] | list[Any]:
        result = self._run_openclaw_cli(args=args, timeout_seconds=timeout_seconds, expect_json=True)
        if not bool(result.get("ok")):
            raise RuntimeError(str(result.get("error") or "openclaw_cli_failed"))
        payload = result.get("json")
        if payload is None:
            raise RuntimeError("openclaw_cli_json_missing_payload")
        if expected_type is not None and not isinstance(payload, expected_type):
            raise RuntimeError(f"openclaw_cli_json_unexpected_type:{type(payload).__name__}")
        if isinstance(payload, (dict, list)):
            return payload
        raise RuntimeError("openclaw_cli_json_invalid_payload")

    def _start_openclaw_node_process(
        self,
        *,
        profile: str,
        host: str,
        port: int,
        use_tls: bool,
        display_name: str,
        gateway_token: str | None = None,
    ) -> subprocess.Popen[str]:
        if str(gateway_token or "").strip():
            config_set = self._run_openclaw_cli(
                args=[
                    "openclaw",
                    "--profile",
                    str(profile),
                    "config",
                    "set",
                    "gateway.auth.token",
                    str(gateway_token),
                ],
                timeout_seconds=20.0,
                expect_json=False,
            )
            if not bool(config_set.get("ok")):
                raise RuntimeError(
                    f"node_profile_token_config_failed:{str(config_set.get('error') or 'openclaw_cli_failed')}"
                )
        command: list[str] = [
            "openclaw",
            "--profile",
            str(profile),
            "node",
            "run",
            "--host",
            str(host),
            "--port",
            str(int(port)),
            "--display-name",
            str(display_name),
        ]
        if use_tls:
            command.append("--tls")
        quoted_args = " ".join(shlex.quote(str(item)) for item in command)
        shell_cmd = f"source ~/.nvm/nvm.sh && nvm use 22 >/dev/null && {quoted_args}"
        proc = subprocess.Popen(
            ["zsh", "-lc", shell_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.8)
        if proc.poll() is not None:
            stdout = ""
            stderr = ""
            try:
                out, err = proc.communicate(timeout=1.0)
                stdout = str(out or "")
                stderr = str(err or "")
            except Exception:
                pass
            raise RuntimeError(f"node_process_exit_early:{stdout.strip() or stderr.strip() or proc.poll()}")
        return proc

    def _stop_openclaw_node_process(self, proc: subprocess.Popen[str] | None) -> dict[str, Any]:
        if proc is None:
            return {"stopped": True, "returncode": None}
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        stdout_tail = ""
        stderr_tail = ""
        if proc.stdout is not None:
            try:
                stdout_tail = str(proc.stdout.read() or "")[-800:]
            except Exception:
                stdout_tail = ""
        if proc.stderr is not None:
            try:
                stderr_tail = str(proc.stderr.read() or "")[-800:]
            except Exception:
                stderr_tail = ""
        return {
            "stopped": True,
            "returncode": proc.poll(),
            "stdout_tail": stdout_tail.strip() or None,
            "stderr_tail": stderr_tail.strip() or None,
        }

    def _resolve_openclaw_gateway_token_for_soak(self, *, token_ref: str | None = None) -> str | None:
        explicit_ref = str(token_ref or "").strip()
        if explicit_ref:
            try:
                resolved = resolve_secret_ref(parse_secret_ref(explicit_ref))
            except Exception:
                resolved = ""
            if resolved:
                return str(resolved).strip()
        env_token = str(os.getenv("JARVIS_OPENCLAW_GATEWAY_TOKEN") or "").strip()
        if env_token:
            return env_token
        configured = self.openclaw_gateway_client
        if configured is not None:
            try:
                resolved = resolve_secret_ref(parse_secret_ref(configured.config.token_ref))
            except Exception:
                resolved = ""
            if resolved:
                return str(resolved).strip()
        openclaw_cfg = Path.home() / ".openclaw" / "openclaw.json"
        if openclaw_cfg.exists():
            try:
                payload = json.loads(openclaw_cfg.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            token = str(((payload.get("gateway") or {}).get("auth") or {}).get("token") or "").strip()
            if token:
                return token
        return None

    def run_openclaw_node_embodiment_soak(
        self,
        *,
        ws_url: str | None = None,
        token_ref: str | None = None,
        owner_id: str = "primary_operator",
        client_name: str = "jarvis",
        node_display_name: str = "JARVIS-Soak-Node",
        profile_prefix: str = "jarvis-m18-node-soak",
        probe_command: str = "notifications.send",
        pairing_timeout_seconds: float = 45.0,
        reconnect_timeout_seconds: float = 35.0,
        run_reject_cycle: bool = True,
    ) -> dict[str, Any]:
        resolved_ws_url = str(
            ws_url
            or self.get_openclaw_gateway_status().get("ws_url")
            or os.getenv("JARVIS_OPENCLAW_GATEWAY_WS_URL")
            or "ws://127.0.0.1:18789"
        ).strip()
        parsed = urlparse(resolved_ws_url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            return {
                "ok": False,
                "error": "invalid_gateway_ws_url",
                "ws_url": resolved_ws_url,
            }
        host = str(parsed.hostname)
        port = int(parsed.port or (443 if parsed.scheme == "wss" else 80))
        use_tls = parsed.scheme == "wss"
        timeline: list[dict[str, Any]] = []
        errors: list[str] = []

        def mark(step: str, **details: Any) -> None:
            timeline.append(
                {
                    "step": str(step),
                    "timestamp": utc_now_iso(),
                    "details": dict(details),
                }
            )

        resolved_token_ref = str(token_ref or os.getenv("JARVIS_OPENCLAW_GATEWAY_TOKEN_REF") or "").strip() or None
        gateway_token = self._resolve_openclaw_gateway_token_for_soak(token_ref=resolved_token_ref)
        if not gateway_token:
            return {
                "ok": False,
                "error": "gateway_token_not_available",
                "ws_url": resolved_ws_url,
            }
        if resolved_token_ref and resolved_token_ref.startswith("env:"):
            env_name = resolved_token_ref.split(":", 1)[1].strip()
            if env_name:
                os.environ[env_name] = gateway_token
        else:
            os.environ["JARVIS_OPENCLAW_GATEWAY_TOKEN"] = gateway_token
            resolved_token_ref = "env:JARVIS_OPENCLAW_GATEWAY_TOKEN"

        gateway_config = self.configure_openclaw_gateway_loop(
            ws_url=resolved_ws_url,
            token_ref=resolved_token_ref,
            owner_id=owner_id,
            client_name=client_name,
            protocol_profile_id="openclaw_gateway_v2026_04_2",
            enabled=True,
        )
        self.start_openclaw_gateway_loop()
        self.pump_openclaw_gateway(max_messages=120)
        mark(
            "gateway_loop_ready",
            configured=bool(gateway_config.get("configured")),
            enabled=bool(gateway_config.get("enabled")),
            ws_url=resolved_ws_url,
        )

        base_auth_args = ["--token", gateway_token, "--url", resolved_ws_url]
        run_id = str(int(time.time() * 1000))
        approve_profile = f"{profile_prefix}-{run_id}-approve"
        reject_profile = f"{profile_prefix}-{run_id}-reject"
        approve_display = f"{node_display_name}-approve"
        reject_display = f"{node_display_name}-reject"
        approve_proc: subprocess.Popen[str] | None = None
        reject_proc: subprocess.Popen[str] | None = None

        approved_node_id: str | None = None
        approved_token_before: str | None = None
        approved_token_after: str | None = None
        reconnect_ok = False
        rotation_ok = False
        reject_cycle_ok = not run_reject_cycle
        trust_axes: dict[str, Any] = {}
        freeze_report: dict[str, Any] | None = None
        describe_payload: dict[str, Any] | None = None
        pairing_request_payload: dict[str, Any] | None = None
        approval_payload: dict[str, Any] | None = None
        rejection_payload: dict[str, Any] | None = None

        try:
            approve_proc = self._start_openclaw_node_process(
                profile=approve_profile,
                host=host,
                port=port,
                use_tls=use_tls,
                display_name=approve_display,
                gateway_token=gateway_token,
            )
            mark("node_process_started", profile=approve_profile, display_name=approve_display)

            pending_request: dict[str, Any] | None = None
            pending_deadline = time.time() + max(8.0, float(pairing_timeout_seconds))
            while time.time() < pending_deadline:
                self.pump_openclaw_gateway(max_messages=120)
                pending = self._openclaw_cli_json(
                    args=["openclaw", "nodes", "pending", "--json", *base_auth_args],
                    timeout_seconds=20.0,
                    expected_type=list,
                )
                match = next(
                    (
                        item
                        for item in pending
                        if isinstance(item, dict)
                        and str(item.get("displayName") or "").strip() == approve_display
                    ),
                    None,
                )
                if match:
                    pending_request = match
                    break
                time.sleep(1.0)

            if pending_request is None:
                raise RuntimeError("node_pair_request_not_observed")
            pairing_request_payload = dict(pending_request)
            approved_node_id = str(pending_request.get("nodeId") or "").strip() or None
            mark(
                "node_pair_request_observed",
                request_id=pending_request.get("requestId"),
                node_id=approved_node_id,
            )

            approval_payload_raw = self._openclaw_cli_json(
                args=[
                    "openclaw",
                    "nodes",
                    "approve",
                    str(pending_request.get("requestId") or ""),
                    "--json",
                    *base_auth_args,
                ],
                timeout_seconds=20.0,
                expected_type=dict,
            )
            approval_payload = dict(approval_payload_raw)
            approved_node = approval_payload.get("node") if isinstance(approval_payload.get("node"), dict) else {}
            approved_node_id = str(approved_node.get("nodeId") or approved_node_id or "").strip() or None
            approved_token_before = str(approved_node.get("token") or "").strip() or None
            mark(
                "node_pair_approved",
                node_id=approved_node_id,
                has_token=bool(approved_token_before),
            )
            if not approved_node_id:
                raise RuntimeError("approved_node_id_missing")

            connected = False
            connect_deadline = time.time() + max(8.0, float(pairing_timeout_seconds))
            while time.time() < connect_deadline:
                self.pump_openclaw_gateway(max_messages=120)
                status_payload = self._openclaw_cli_json(
                    args=["openclaw", "nodes", "status", "--json", *base_auth_args],
                    timeout_seconds=20.0,
                    expected_type=dict,
                )
                nodes = status_payload.get("nodes") if isinstance(status_payload.get("nodes"), list) else []
                node_status = next(
                    (
                        item
                        for item in nodes
                        if isinstance(item, dict)
                        and str(item.get("nodeId") or "").strip() == approved_node_id
                    ),
                    None,
                )
                if node_status and bool(node_status.get("connected")):
                    connected = True
                    break
                time.sleep(1.0)
            if not connected:
                raise RuntimeError("approved_node_not_connected")
            mark("node_connected", node_id=approved_node_id)

            describe_raw = self._openclaw_cli_json(
                args=["openclaw", "nodes", "describe", "--json", "--node", approved_node_id, *base_auth_args],
                timeout_seconds=20.0,
                expected_type=dict,
            )
            describe_payload = dict(describe_raw)
            mark("node_described", node_id=approved_node_id)

            node_token_env = f"JARVIS_OPENCLAW_NODE_TOKEN_{hashlib.sha256(approved_node_id.encode('utf-8')).hexdigest()[:12].upper()}"
            if approved_token_before:
                os.environ[node_token_env] = approved_token_before
            node_token_ref = f"env:{node_token_env}"
            self.pair_presence_node(
                node_id=approved_node_id,
                device_id=approved_node_id,
                owner_id=owner_id,
                gateway_token_ref=str(resolved_token_ref),
                node_token_ref=node_token_ref,
                pairing_status="approved",
                metadata={
                    "source": "openclaw_node_embodiment_soak",
                    "display_name": approve_display,
                    "profile": approve_profile,
                },
                actor="presence_node_soak",
            )
            trust_axes = self.get_presence_trust_axes(node_id=approved_node_id, command=probe_command)
            mark(
                "trust_axes_captured",
                handshake_state=((trust_axes.get("gateway_handshake") or {}).get("state")),
                pairing_status=((trust_axes.get("pairing_token") or {}).get("pairing_status")),
                overall_ready=bool(trust_axes.get("overall_ready_for_device_commands")),
            )

            safe_policy = self.broker_node_command(
                command=probe_command,
                payload={"probe": True},
                actor="presence_node_soak",
            )
            exec_policy = self.broker_node_command(
                command="system.run",
                payload={"cmd": "echo node-soak"},
                actor="presence_node_soak",
            )
            mark(
                "command_policy_checked",
                safe_allowed=bool(safe_policy.get("allowed")),
                exec_requires_approval=bool(exec_policy.get("requires_approval")),
            )

            stop_result = self._stop_openclaw_node_process(approve_proc)
            approve_proc = None
            mark("node_process_stopped", profile=approve_profile, stop_result=stop_result)

            disconnected = False
            disconnect_deadline = time.time() + max(8.0, float(reconnect_timeout_seconds))
            while time.time() < disconnect_deadline:
                status_payload = self._openclaw_cli_json(
                    args=["openclaw", "nodes", "status", "--json", *base_auth_args],
                    timeout_seconds=20.0,
                    expected_type=dict,
                )
                nodes = status_payload.get("nodes") if isinstance(status_payload.get("nodes"), list) else []
                node_status = next(
                    (
                        item
                        for item in nodes
                        if isinstance(item, dict)
                        and str(item.get("nodeId") or "").strip() == approved_node_id
                    ),
                    None,
                )
                if not node_status or not bool(node_status.get("connected")):
                    disconnected = True
                    break
                time.sleep(1.0)
            mark("node_disconnected_after_stop", node_id=approved_node_id, disconnected=disconnected)

            approve_proc = self._start_openclaw_node_process(
                profile=approve_profile,
                host=host,
                port=port,
                use_tls=use_tls,
                display_name=approve_display,
                gateway_token=gateway_token,
            )
            reconnect_deadline = time.time() + max(8.0, float(reconnect_timeout_seconds))
            while time.time() < reconnect_deadline:
                status_payload = self._openclaw_cli_json(
                    args=["openclaw", "nodes", "status", "--json", *base_auth_args],
                    timeout_seconds=20.0,
                    expected_type=dict,
                )
                nodes = status_payload.get("nodes") if isinstance(status_payload.get("nodes"), list) else []
                node_status = next(
                    (
                        item
                        for item in nodes
                        if isinstance(item, dict)
                        and str(item.get("nodeId") or "").strip() == approved_node_id
                    ),
                    None,
                )
                if node_status and bool(node_status.get("connected")):
                    reconnect_ok = True
                    break
                time.sleep(1.0)
            mark("node_reconnected", node_id=approved_node_id, reconnect_ok=reconnect_ok)

            list_payload_before = self._openclaw_cli_json(
                args=["openclaw", "nodes", "list", "--json", *base_auth_args],
                timeout_seconds=20.0,
                expected_type=dict,
            )
            paired_before = next(
                (
                    item
                    for item in (list_payload_before.get("paired") if isinstance(list_payload_before.get("paired"), list) else [])
                    if isinstance(item, dict) and str(item.get("nodeId") or "").strip() == approved_node_id
                ),
                {},
            )
            approved_token_before = str(paired_before.get("token") or approved_token_before or "").strip() or None

            rotate_request_payload = self._openclaw_cli_json(
                args=[
                    "openclaw",
                    "gateway",
                    "call",
                    "node.pair.request",
                    "--json",
                    "--params",
                    json.dumps({"nodeId": approved_node_id}, separators=(",", ":")),
                    *base_auth_args,
                ],
                timeout_seconds=20.0,
                expected_type=dict,
            )
            rotate_request = (
                rotate_request_payload.get("request")
                if isinstance(rotate_request_payload.get("request"), dict)
                else {}
            )
            rotate_request_id = str(rotate_request.get("requestId") or "").strip()
            if not rotate_request_id:
                raise RuntimeError("rotation_request_id_missing")
            rotate_approved_payload = self._openclaw_cli_json(
                args=["openclaw", "nodes", "approve", rotate_request_id, "--json", *base_auth_args],
                timeout_seconds=20.0,
                expected_type=dict,
            )
            rotate_node = rotate_approved_payload.get("node") if isinstance(rotate_approved_payload.get("node"), dict) else {}
            approved_token_after = str(rotate_node.get("token") or "").strip() or None

            verify_old = self._openclaw_cli_json(
                args=[
                    "openclaw",
                    "gateway",
                    "call",
                    "node.pair.verify",
                    "--json",
                    "--params",
                    json.dumps(
                        {"nodeId": approved_node_id, "token": str(approved_token_before or "")},
                        separators=(",", ":"),
                    ),
                    *base_auth_args,
                ],
                timeout_seconds=20.0,
                expected_type=dict,
            )
            verify_new = self._openclaw_cli_json(
                args=[
                    "openclaw",
                    "gateway",
                    "call",
                    "node.pair.verify",
                    "--json",
                    "--params",
                    json.dumps(
                        {"nodeId": approved_node_id, "token": str(approved_token_after or "")},
                        separators=(",", ":"),
                    ),
                    *base_auth_args,
                ],
                timeout_seconds=20.0,
                expected_type=dict,
            )
            rotation_ok = bool(
                approved_token_before
                and approved_token_after
                and approved_token_before != approved_token_after
                and not bool(verify_old.get("ok"))
                and bool(verify_new.get("ok"))
            )
            if approved_token_after:
                os.environ[node_token_env] = approved_token_after
                self.apply_gateway_pairing_event(
                    node_id=approved_node_id,
                    pairing_status="rotated",
                    event_type="node.pair.rotated",
                    token_ref_hint=node_token_ref,
                )
            mark(
                "node_token_rotated",
                node_id=approved_node_id,
                rotation_ok=rotation_ok,
                old_token_valid=bool(verify_old.get("ok")),
                new_token_valid=bool(verify_new.get("ok")),
            )

            dm_session_id = f"soak-dm-{run_id}"
            node_session_id = f"soak-node-{run_id}"
            dm_surface_id = f"dm:{owner_id}"
            self.ingest_openclaw_gateway_event(
                {
                    "event_id": f"evt-soak-dm-{run_id}",
                    "type": "surface.session.started",
                    "payload": {
                        "channel_id": dm_surface_id,
                        "session_id": dm_session_id,
                        "user_id": owner_id,
                    },
                }
            )
            self.ingest_openclaw_gateway_event(
                {
                    "event_id": f"evt-soak-node-{run_id}",
                    "type": "node.connected",
                    "payload": {
                        "node_id": approved_node_id,
                        "session_id": node_session_id,
                    },
                }
            )
            freeze_report = self.check_presence_continuity_freeze(
                primary_surface_id=dm_surface_id,
                primary_session_id=dm_session_id,
                secondary_surface_id=approved_node_id,
                secondary_session_id=node_session_id,
            )
            mark(
                "cross_surface_continuity_checked",
                freeze_ok=bool((freeze_report or {}).get("freeze_ok")),
            )

            if run_reject_cycle:
                reject_proc = self._start_openclaw_node_process(
                    profile=reject_profile,
                    host=host,
                    port=port,
                    use_tls=use_tls,
                    display_name=reject_display,
                    gateway_token=gateway_token,
                )
                mark("reject_cycle_node_started", profile=reject_profile, display_name=reject_display)
                reject_request: dict[str, Any] | None = None
                reject_deadline = time.time() + max(8.0, float(pairing_timeout_seconds))
                while time.time() < reject_deadline:
                    pending = self._openclaw_cli_json(
                        args=["openclaw", "nodes", "pending", "--json", *base_auth_args],
                        timeout_seconds=20.0,
                        expected_type=list,
                    )
                    match = next(
                        (
                            item
                            for item in pending
                            if isinstance(item, dict)
                            and str(item.get("displayName") or "").strip() == reject_display
                        ),
                        None,
                    )
                    if match:
                        reject_request = match
                        break
                    time.sleep(1.0)
                if reject_request is None:
                    raise RuntimeError("reject_cycle_pending_request_not_observed")
                rejection_payload_raw = self._openclaw_cli_json(
                    args=[
                        "openclaw",
                        "nodes",
                        "reject",
                        str(reject_request.get("requestId") or ""),
                        "--json",
                        *base_auth_args,
                    ],
                    timeout_seconds=20.0,
                    expected_type=dict,
                )
                rejection_payload = dict(rejection_payload_raw)
                rejected_node_id = str(reject_request.get("nodeId") or "").strip() or None
                pending_cleared = False
                reject_wait_deadline = time.time() + max(8.0, float(reconnect_timeout_seconds))
                while time.time() < reject_wait_deadline:
                    pending = self._openclaw_cli_json(
                        args=["openclaw", "nodes", "pending", "--json", *base_auth_args],
                        timeout_seconds=20.0,
                        expected_type=list,
                    )
                    request_ids = {
                        str(item.get("requestId") or "").strip()
                        for item in pending
                        if isinstance(item, dict)
                    }
                    if str(reject_request.get("requestId") or "").strip() not in request_ids:
                        pending_cleared = True
                        break
                    time.sleep(1.0)
                paired_payload = self._openclaw_cli_json(
                    args=["openclaw", "nodes", "list", "--json", *base_auth_args],
                    timeout_seconds=20.0,
                    expected_type=dict,
                )
                paired_items = paired_payload.get("paired") if isinstance(paired_payload.get("paired"), list) else []
                reject_node_paired = any(
                    isinstance(item, dict) and str(item.get("nodeId") or "").strip() == str(rejected_node_id or "")
                    for item in paired_items
                )
                reject_cycle_ok = bool(pending_cleared and not reject_node_paired)
                mark(
                    "reject_cycle_completed",
                    reject_cycle_ok=reject_cycle_ok,
                    pending_cleared=pending_cleared,
                    rejected_node_paired=reject_node_paired,
                )
        except Exception as exc:
            errors.append(str(exc))
            mark("node_embodiment_soak_error", error=str(exc))
        finally:
            stop_approve = self._stop_openclaw_node_process(approve_proc)
            stop_reject = self._stop_openclaw_node_process(reject_proc)
            mark("node_process_cleanup", approve=stop_approve, reject=stop_reject)

        final_trust_axes = self.get_presence_trust_axes(node_id=approved_node_id, command=probe_command)
        pairing_axis = final_trust_axes.get("pairing_token") if isinstance(final_trust_axes.get("pairing_token"), dict) else {}
        handshake_axis = final_trust_axes.get("gateway_handshake") if isinstance(final_trust_axes.get("gateway_handshake"), dict) else {}
        command_axis = final_trust_axes.get("command_policy") if isinstance(final_trust_axes.get("command_policy"), dict) else {}
        pairing_ok = bool(pairing_axis.get("ok"))
        handshake_ok = bool(handshake_axis.get("ok"))
        command_ok = bool(command_axis.get("ok_for_presence_lane"))
        continuity_ok = bool((freeze_report or {}).get("freeze_ok", True))
        overall_ok = bool(
            not errors
            and approved_node_id
            and reconnect_ok
            and rotation_ok
            and reject_cycle_ok
            and handshake_ok
            and pairing_ok
            and command_ok
            and continuity_ok
        )
        result = {
            "ok": overall_ok,
            "ws_url": resolved_ws_url,
            "node_id": approved_node_id,
            "profiles": {"approve": approve_profile, "reject": reject_profile if run_reject_cycle else None},
            "pair_request": pairing_request_payload,
            "approval": approval_payload,
            "describe": describe_payload,
            "rotation": {
                "ok": rotation_ok,
                "token_before_present": bool(approved_token_before),
                "token_after_present": bool(approved_token_after),
            },
            "reconnect_ok": reconnect_ok,
            "reject_cycle_ok": reject_cycle_ok,
            "rejection": rejection_payload,
            "trust_axes": final_trust_axes,
            "continuity_freeze": freeze_report,
            "errors": errors,
            "timeline": timeline,
        }
        self.memory.append_event(
            "presence.node_embodiment_soak_run",
            {
                "ok": overall_ok,
                "node_id": approved_node_id,
                "reconnect_ok": reconnect_ok,
                "rotation_ok": rotation_ok,
                "reject_cycle_ok": reject_cycle_ok,
                "handshake_ok": handshake_ok,
                "pairing_ok": pairing_ok,
                "command_ok": command_ok,
                "continuity_ok": continuity_ok,
                "error_count": len(errors),
            },
        )
        return result

    def run_openclaw_gateway_soak(
        self,
        *,
        loops: int = 12,
        max_messages: int = 120,
        node_id: str | None = None,
        probe_command: str = "notifications.send",
        expect_pairing_approved: bool = False,
    ) -> dict[str, Any]:
        if self.openclaw_gateway_client is None:
            self.configure_openclaw_gateway_loop()
        status = self.get_openclaw_gateway_status()
        if not bool(status.get("configured")):
            return {
                "ok": False,
                "error": "gateway_not_configured",
                "status": status,
            }
        if not bool(status.get("enabled")):
            return {
                "ok": False,
                "error": "gateway_loop_not_enabled",
                "status": status,
            }

        self.start_openclaw_gateway_loop()
        timeline: list[dict[str, Any]] = []
        total_loops = max(1, min(int(loops), 200))
        for idx in range(total_loops):
            snapshot = self.pump_openclaw_gateway(max_messages=max_messages)
            axes = self.get_presence_trust_axes(node_id=node_id, command=probe_command)
            timeline.append(
                {
                    "loop_index": idx,
                    "timestamp": utc_now_iso(),
                    "gateway": snapshot,
                    "trust_axes": axes,
                }
            )

        handshake_states = sorted(
            {
                str((item.get("gateway") or {}).get("connect_handshake_state") or "unknown")
                for item in timeline
            }
        )
        pairing_states = sorted(
            {
                str((item.get("gateway") or {}).get("pairing_state") or "unknown")
                for item in timeline
            }
        )
        handshake_ok = "acked" in handshake_states or "not_required" in handshake_states
        pairing_ok = (not expect_pairing_approved) or ("approved" in pairing_states)
        final = timeline[-1] if timeline else {"gateway": status, "trust_axes": self.get_presence_trust_axes()}
        final_axes = final.get("trust_axes") if isinstance(final.get("trust_axes"), dict) else {}
        final_ready = bool(final_axes.get("overall_ready_for_device_commands"))
        final_policy = final_axes.get("command_policy") if isinstance(final_axes.get("command_policy"), dict) else {}
        command_policy_ok = bool(final_policy.get("ok_for_presence_lane"))
        if expect_pairing_approved:
            overall_ok = bool(handshake_ok and pairing_ok and final_ready and command_policy_ok)
        else:
            overall_ok = bool(handshake_ok and command_policy_ok)
        result = {
            "ok": overall_ok,
            "loops": total_loops,
            "handshake_states_seen": handshake_states,
            "pairing_states_seen": pairing_states,
            "expect_pairing_approved": bool(expect_pairing_approved),
            "handshake_ok": handshake_ok,
            "pairing_ok": pairing_ok,
            "command_policy_ok": command_policy_ok,
            "final_gateway_status": final.get("gateway"),
            "final_trust_axes": final.get("trust_axes"),
            "timeline": timeline,
        }
        self.memory.append_event(
            "presence.gateway_soak_run",
            {
                "ok": overall_ok,
                "loops": total_loops,
                "handshake_states_seen": handshake_states,
                "pairing_states_seen": pairing_states,
            },
        )
        return result

    def get_presence_health(self) -> dict[str, Any]:
        bridge = self.presence_health.get_bridge_state()
        nodes = self.list_presence_nodes(limit=200)
        sessions = self.list_surface_sessions(limit=200)
        latest_heartbeat = self.presence_health.latest_heartbeat()
        latest_tone = self.tone_balance.latest()
        adaptive_policy = self.get_adaptive_policy()
        adaptive_metadata = (
            adaptive_policy.get("metadata")
            if isinstance(adaptive_policy.get("metadata"), dict)
            else {}
        )
        adaptive_runtime = (
            adaptive_policy.get("runtime")
            if isinstance(adaptive_policy.get("runtime"), dict)
            else {}
        )
        status_counts: dict[str, int] = {}
        for node in nodes:
            key = str(node.get("pairing_status") or "unknown")
            status_counts[key] = status_counts.get(key, 0) + 1
        session_counts: dict[str, int] = {}
        for session in sessions:
            key = str(session.get("status") or "unknown")
            session_counts[key] = session_counts.get(key, 0) + 1
        return {
            "bridge": bridge,
            "node_count": len(nodes),
            "nodes_by_status": status_counts,
            "session_count": len(sessions),
            "sessions_by_status": session_counts,
            "latest_heartbeat": latest_heartbeat,
            "latest_tone_balance": latest_tone,
            "adaptive_policy": {
                "revision": adaptive_metadata.get("revision"),
                "updated_at": adaptive_metadata.get("updated_at"),
                "calibration_runs": adaptive_metadata.get("calibration_runs"),
                "auto_calibration_enabled": adaptive_runtime.get("auto_calibration_enabled"),
                "auto_calibration_every_turns": adaptive_runtime.get("auto_calibration_every_turns"),
                "turn_counter": self._adaptive_turn_counter,
            },
            "gateway_loop": self.get_openclaw_gateway_status(),
            "trust_axes": self.get_presence_trust_axes(),
            "updated_at": utc_now_iso(),
        }

    def get_presence_tone_balance(self, *, limit: int = 30) -> dict[str, Any]:
        return self.tone_balance.summary(limit=limit)

    def ingest_openclaw_gateway_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return self.openclaw_event_router.route_gateway_event(dict(event or {}))

    def run_presence_heartbeat(self) -> dict[str, Any]:
        surfaces = self.refresh_consciousness_surfaces(reason="presence_heartbeat")
        mode = self.get_presence_mode()
        summary = {
            "mode": mode.get("mode"),
            "surface_file_count": len(list(surfaces.get("files") or [])),
            "nodes": len(self.list_presence_nodes(limit=200)),
        }
        heartbeat = self.presence_health.record_heartbeat(
            heartbeat_id=new_id("hbt"),
            summary=summary,
        )
        self.memory.append_event(
            "presence.heartbeat",
            {
                "heartbeat_id": heartbeat.get("heartbeat_id"),
                "summary": summary,
            },
        )
        return {
            "heartbeat": heartbeat,
            "mode": mode,
            "health": self.get_presence_health(),
        }

    def record_pushback(
        self,
        *,
        domain: str,
        recommendation: str,
        severity: str,
        rationale: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.pushback_calibration.record_pushback(
            domain=domain,
            recommendation=recommendation,
            severity=severity,
            rationale=rationale,
        )
        self.memory.append_event("pushback.recorded", {"pushback_id": record.get("pushback_id"), "domain": domain})
        return record

    def record_override(
        self,
        *,
        pushback_id: str,
        operator_action: str,
        rationale: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.pushback_calibration.record_override(
            pushback_id=pushback_id,
            operator_action=operator_action,
            rationale=rationale,
        )
        self.memory.append_event("pushback.override_recorded", {"override_id": record.get("override_id"), "pushback_id": pushback_id})
        return record

    def record_pushback_outcome_review(
        self,
        *,
        pushback_id: str,
        outcome: str,
        impact_score: float,
        notes: dict[str, Any] | None = None,
        override_id: str | None = None,
    ) -> dict[str, Any]:
        review = self.pushback_calibration.record_outcome_review(
            pushback_id=pushback_id,
            outcome=outcome,
            impact_score=impact_score,
            notes=notes,
            override_id=override_id,
        )
        self.memory.append_event("pushback.outcome_reviewed", {"review_id": review.get("review_id"), "pushback_id": pushback_id})
        return review

    def record_pushback_calibration_delta(
        self,
        *,
        domain: str,
        direction: str,
        magnitude: float,
        reason: str,
        source_review_id: str | None = None,
    ) -> dict[str, Any]:
        delta = self.pushback_calibration.record_calibration_delta(
            domain=domain,
            direction=direction,
            magnitude=magnitude,
            reason=reason,
            source_review_id=source_review_id,
        )
        self.memory.append_event("pushback.calibration_delta", {"delta_id": delta.get("delta_id"), "domain": domain})
        return delta

    def list_pushback_calibration(self, *, limit: int = 30) -> dict[str, list[dict[str, Any]]]:
        return self.pushback_calibration.list_recent(limit=limit)

    def get_pushback_calibration_revision(self) -> str | None:
        recent = self.list_pushback_calibration(limit=1)
        if not isinstance(recent, dict):
            return None
        for key in ("calibration_deltas", "reviews", "overrides", "pushbacks"):
            items = recent.get(key)
            if isinstance(items, list) and items:
                head = items[0]
                if isinstance(head, dict):
                    for id_key in ("delta_id", "review_id", "override_id", "pushback_id"):
                        value = str(head.get(id_key) or "").strip()
                        if value:
                            return value
        return None

    def ingest_envelope(
        self,
        event: EventEnvelope,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        chosen_extractor = extractor or self._extract_candidates
        outcome = self.state_graph.process_event(event, chosen_extractor)
        self.memory.add_episode(
            memory_id=new_id("epi"),
            category="event_ingested",
            data={"event": event.to_dict(), "outcome": outcome},
            provenance_event_ids=[event.event_id],
            provenance_state_ids=outcome["touched_ids"],
        )
        self.memory.append_event(
            "state.event_ingested",
            {
                "event_id": event.event_id,
                "source": event.source,
                "source_type": event.source_type,
                "touched_count": len(list(outcome.get("touched_ids") or [])),
                "trigger_count": len(list(outcome.get("triggers") or [])),
            },
        )
        return outcome

    def plan(self, triggers: list[dict[str, Any]]) -> list[str]:
        plans = self.planner.build_plans(triggers)
        plan_ids: list[str] = []
        for plan in plans:
            self.plan_repo.save_plan(plan)
            plan_ids.append(plan.plan_id)
        return plan_ids

    def run_cognition_cycle(self) -> dict[str, Any]:
        outcome = self.cognition.run_cycle(self)
        self.memory.append_event(
            "dream.cognition_cycle_completed",
            {
                "status": outcome.get("status"),
                "thought_id": outcome.get("thought_id"),
                "backend": outcome.get("backend"),
                "backend_mode": outcome.get("backend_mode"),
            },
        )
        self.refresh_consciousness_surfaces(reason="cognition_cycle")
        return outcome

    def get_cognition_config(self) -> dict[str, Any]:
        backend_config = self.cognition_backend.get_config()
        latest = self.cognition.store.latest()
        return {
            "enabled": self.cognition_enabled,
            "backend": self.cognition_backend.name,
            "model": self.cognition_backend.model,
            "model_assisted": bool(getattr(self.cognition_backend, "model_assisted", False)),
            "local_only": bool(getattr(self.cognition_backend, "local_only", True)),
            "timeout_seconds": backend_config.get("timeout_seconds"),
            "retry_attempts": backend_config.get("retry_attempts"),
            "fallback_backend": backend_config.get("fallback_backend"),
            "max_hypotheses_per_cycle": self.cognition.max_hypotheses_per_cycle,
            "min_cycle_interval_seconds": self.cognition.min_cycle_interval_seconds,
            "wake_interval_seconds": self.cognition.wake_interval_seconds,
            "model_assisted_synthesis": backend_config.get("model_assisted_synthesis"),
            "model_assisted_skepticism": backend_config.get("model_assisted_skepticism"),
            "latest_backend_mode": (latest or {}).get("backend_mode"),
            "latest_backend_metrics": (latest or {}).get("backend_metrics"),
        }

    def list_recent_thoughts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.cognition.store.recent(limit=limit)

    def get_thought(self, thought_id: str) -> dict[str, Any] | None:
        return self.cognition.store.get(thought_id)

    def generate_morning_synthesis(self) -> dict[str, Any]:
        return self.synthesis_engine.generate_morning(self)

    def generate_evening_synthesis(self) -> dict[str, Any]:
        return self.synthesis_engine.generate_evening(self)

    def get_latest_synthesis(self, kind: str) -> dict[str, Any] | None:
        return self.synthesis_engine.store.latest(kind)

    def list_interrupts(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.interrupt_store.list(status=status, limit=limit)

    def acknowledge_interrupt(self, interrupt_id: str, *, actor: str = "user") -> dict[str, Any]:
        return self.interrupt_store.acknowledge(interrupt_id, actor=actor)

    def snooze_interrupt(
        self,
        interrupt_id: str,
        *,
        minutes: int = 60,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.interrupt_store.snooze(interrupt_id, minutes=minutes, actor=actor)

    def get_operator_preferences(self) -> dict[str, Any]:
        return self.operator_state.get_preferences()

    def get_pondering_mode(self) -> dict[str, Any]:
        prefs = self.get_operator_preferences()
        pondering = prefs.get("pondering_mode") if isinstance(prefs.get("pondering_mode"), dict) else {}
        enabled = bool(pondering.get("enabled"))
        style = str(pondering.get("style") or "open_discussion").strip().lower() or "open_discussion"
        try:
            min_confidence = float(pondering.get("min_confidence_for_understood"))
        except (TypeError, ValueError):
            min_confidence = 0.78
        min_confidence = max(0.5, min(0.99, min_confidence))
        return {
            "enabled": enabled,
            "style": style,
            "min_confidence_for_understood": round(min_confidence, 2),
            "active_when_uncertainty_above": round(1.0 - min_confidence, 2),
        }

    def set_focus_mode(self, *, domain: str | None, actor: str = "user") -> dict[str, Any]:
        updated = self.operator_state.set_focus_mode(domain=domain, actor=actor)
        self.security.audit(
            action="operator_set_focus_mode",
            status="ok",
            details={"actor": actor, "focus_mode_domain": updated.get("focus_mode_domain")},
            action_class=ActionClass.P0,
        )
        return updated

    def set_quiet_hours(
        self,
        *,
        start_hour: int | None,
        end_hour: int | None,
        actor: str = "user",
    ) -> dict[str, Any]:
        updated = self.operator_state.set_quiet_hours(
            start_hour=start_hour,
            end_hour=end_hour,
            actor=actor,
        )
        self.security.audit(
            action="operator_set_quiet_hours",
            status="ok",
            details={
                "actor": actor,
                "quiet_hours": updated.get("quiet_hours"),
            },
            action_class=ActionClass.P0,
        )
        return updated

    def suppress_interrupts_until(
        self,
        *,
        until_iso: str | None,
        reason: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        updated = self.operator_state.set_suppress_until(
            until_iso=until_iso,
            reason=reason,
            actor=actor,
        )
        self.security.audit(
            action="operator_set_suppress_until",
            status="ok",
            details={
                "actor": actor,
                "suppress_until": updated.get("suppress_until"),
                "suppression_reason": updated.get("suppression_reason"),
            },
            action_class=ActionClass.P0,
        )
        return updated

    def list_operator_preference_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.operator_state.list_events(limit=limit)

    def set_pondering_mode(
        self,
        *,
        enabled: bool | None = None,
        style: str | None = None,
        min_confidence_for_understood: float | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        updated = self.operator_state.set_pondering_mode(
            enabled=enabled,
            style=style,
            min_confidence_for_understood=min_confidence_for_understood,
            actor=actor,
        )
        self.security.audit(
            action="operator_set_pondering_mode",
            status="ok",
            details={
                "actor": actor,
                "pondering_mode": updated.get("pondering_mode"),
            },
            action_class=ActionClass.P0,
        )
        return updated

    def get_user_model(self) -> dict[str, Any]:
        return self.identity_state.get_user_model()

    def get_consciousness_contract(self) -> dict[str, Any]:
        return self.identity_state.get_consciousness_contract()

    def get_consciousness_contract_hash(self) -> str:
        contract = self.get_consciousness_contract()
        encoded = json.dumps(contract, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def get_user_model_revision(self) -> str | None:
        artifact = self.get_latest_user_model_artifact() or self.get_user_model()
        if not isinstance(artifact, dict):
            return None
        encoded = json.dumps(artifact, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def get_surface_session(
        self,
        *,
        surface_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        normalized_surface = str(surface_id or "").strip() or "openclaw"
        normalized_session = str(session_id or "").strip() or "default"
        session_key = f"{normalized_surface}:{normalized_session}"
        return self.surface_sessions.get(session_key)

    def update_consciousness_contract(
        self,
        *,
        patch: dict[str, Any],
        actor: str = "user",
        replace: bool = False,
    ) -> dict[str, Any]:
        updated = self.identity_state.update_consciousness_contract(
            patch=patch,
            actor=actor,
            replace=replace,
        )
        self.memory.append_event(
            "identity.consciousness_contract_updated",
            {
                "actor": actor,
                "replace": bool(replace),
                "patch_keys": sorted((patch or {}).keys()),
            },
        )
        return updated

    def set_domain_weight(self, *, domain: str, weight: float, actor: str = "user") -> dict[str, Any]:
        model = self.identity_state.set_domain_weight(domain=domain, weight=weight, actor=actor)
        self.ingest_event(
            source="identity",
            source_type="identity.user_model_updated",
            payload=model,
        )
        self.security.audit(
            action="identity_set_domain_weight",
            status="ok",
            details={"actor": actor, "domain": domain, "weight": weight},
            action_class=ActionClass.P0,
        )
        self.refresh_consciousness_surfaces(reason="identity_domain_weight")
        return model

    def upsert_user_goal(
        self,
        *,
        goal_id: str,
        label: str,
        priority: int,
        weight: float,
        domains: list[str],
        actor: str = "user",
    ) -> dict[str, Any]:
        model = self.identity_state.upsert_goal(
            goal_id=goal_id,
            label=label,
            priority=priority,
            weight=weight,
            domains=domains,
            actor=actor,
        )
        self.ingest_event(
            source="identity",
            source_type="identity.goal_hierarchy_updated",
            payload=model,
        )
        self.security.audit(
            action="identity_upsert_goal",
            status="ok",
            details={"actor": actor, "goal_id": goal_id},
            action_class=ActionClass.P0,
        )
        self.refresh_consciousness_surfaces(reason="identity_goal_update")
        return model

    def get_personal_context(self) -> dict[str, Any]:
        return self.identity_state.get_personal_context()

    def update_personal_context(
        self,
        *,
        stress_level: float | None = None,
        energy_level: float | None = None,
        sleep_hours: float | None = None,
        available_focus_minutes: int | None = None,
        mode: str | None = None,
        note: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        context = self.identity_state.update_personal_context(
            stress_level=stress_level,
            energy_level=energy_level,
            sleep_hours=sleep_hours,
            available_focus_minutes=available_focus_minutes,
            mode=mode,
            note=note,
            actor=actor,
        )
        self.ingest_event(
            source="personal",
            source_type="personal.context_signal",
            payload=context,
        )
        self.security.audit(
            action="identity_update_personal_context",
            status="ok",
            details={"actor": actor, "context": context},
            action_class=ActionClass.P0,
        )
        self.refresh_consciousness_surfaces(reason="personal_context_update")
        return context

    def list_identity_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.identity_state.list_events(limit=limit)

    def get_latest_user_model_artifact(self) -> dict[str, Any] | None:
        row = self.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_user_model_key("default"),
        )
        return row["value"] if row else None

    def get_latest_personal_context_artifact(self) -> dict[str, Any] | None:
        row = self.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_personal_context_key("default"),
        )
        return row["value"] if row else None

    def summarize_academic_signal_sources(self, term_id: str = "current_term") -> list[dict[str, Any]]:
        counts: dict[tuple[str, str], int] = {}

        def _add(kind: str | None, provider: str | None) -> None:
            source_kind = str(kind or "unknown")
            source_provider = str(provider or "unknown")
            key = (source_kind, source_provider)
            counts[key] = counts.get(key, 0) + 1

        overview = self.get_academics_overview(term_id=term_id) or {}
        if isinstance(overview, dict):
            _add(
                str(overview.get("signal_source_kind") or ""),
                str(overview.get("signal_provider") or ""),
            )
            last_payload = overview.get("last_event_payload")
            if isinstance(last_payload, dict):
                _add(
                    str(last_payload.get("ingestion_source_kind") or ""),
                    str(last_payload.get("ingestion_provider") or ""),
                )

        for risk in self.list_academic_risks():
            value = risk.get("value") if isinstance(risk.get("value"), dict) else {}
            _add(
                str(value.get("signal_source_kind") or ""),
                str(value.get("signal_provider") or ""),
            )

        summary = [
            {
                "source_kind": key[0],
                "provider": key[1],
                "count": count,
            }
            for key, count in counts.items()
            if key[0] != "unknown" or key[1] != "unknown"
        ]
        summary.sort(key=lambda row: (int(row["count"]), str(row["source_kind"]), str(row["provider"])), reverse=True)
        return summary

    def list_consciousness_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.memory.list_events(limit=limit, event_type=event_type)

    def refresh_consciousness_surfaces(self, *, reason: str = "manual") -> dict[str, Any]:
        surface = self.consciousness_surfaces.refresh(self, reason=reason)
        self.memory.append_event(
            "consciousness.surfaces_refreshed",
            {
                "reason": reason,
                "file_count": len(list(surface.get("files") or [])),
            },
        )
        return surface

    def get_consciousness_surfaces(self, *, include_content: bool = False) -> dict[str, Any]:
        surfaces = self.consciousness_surfaces.get_surfaces(include_content=include_content)
        files = list(surfaces.get("files") or [])
        if not any(bool(item.get("exists")) for item in files):
            surfaces = self.refresh_consciousness_surfaces(reason="lazy_bootstrap")
            surfaces = self.consciousness_surfaces.get_surfaces(include_content=include_content)
        return surfaces

    def get_operator_home(self) -> dict[str, Any]:
        risks = self.state_graph.get_active_entities("Risk")
        priorities = sorted(
            [
                {
                    "domain": str(
                        (item.get("value") or {}).get("domain")
                        or (item.get("value") or {}).get("project")
                        or "unknown"
                    ),
                    "risk_key": item.get("entity_key"),
                    "reason": (item.get("value") or {}).get("reason"),
                    "confidence": item.get("confidence"),
                }
                for item in risks
            ],
            key=lambda row: float(row.get("confidence") or 0.0),
            reverse=True,
        )

        by_domain: dict[str, list[dict[str, Any]]] = {}
        for item in priorities:
            by_domain.setdefault(str(item.get("domain") or "unknown"), []).append(item)

        latest_review_states = [
            item
            for item in self.state_graph.get_active_entities("Artifact")
            if str(item.get("entity_key", "")).startswith("latest_review_status:")
        ]
        latest_review_states = sorted(
            latest_review_states,
            key=lambda row: str(row.get("last_verified_at") or ""),
            reverse=True,
        )[:10]

        return {
            "generated_at": utc_now_iso(),
            "priorities": priorities[:12],
            "priorities_by_domain": by_domain,
            "thought": self.cognition.store.latest(),
            "morning_synthesis": self.get_latest_synthesis("morning"),
            "evening_synthesis": self.get_latest_synthesis("evening"),
            "interrupts": self.list_interrupts(status="all", limit=20),
            "pending_approvals": self.security.list_approvals(status="pending"),
            "academics": {
                "overview": self.get_academics_overview(),
                "risks": self.list_academic_risks(),
                "schedule": self.get_academics_schedule_context(),
                "windows": self.get_academics_suppression_windows(),
                "signal_sources": self.summarize_academic_signal_sources(),
            },
            "markets": {
                "risk_posture": self.get_market_risk_posture(),
                "opportunities": self.list_market_opportunities(limit=12),
                "abstentions": self.list_market_abstentions(limit=12),
                "events": self.list_market_events(limit=12),
                "handoffs": self.list_market_handoffs(limit=12),
                "outcomes": self.list_market_outcomes(limit=12),
                "evaluation": self.summarize_market_outcomes(limit=120),
                "risks": self.list_market_risks(),
            },
            "identity": {
                "user_model": self.get_user_model(),
                "personal_context": self.get_personal_context(),
                "latest_user_model_artifact": self.get_latest_user_model_artifact(),
                "latest_personal_context_artifact": self.get_latest_personal_context_artifact(),
            },
            "operator_preferences": self.get_operator_preferences(),
            "review_states": [item.get("value") for item in latest_review_states],
            "recent_digest_exports": self.archive_service.list_exports(limit=7),
            "consciousness": {
                "contract": self.get_consciousness_contract(),
                "surfaces": self.get_consciousness_surfaces(include_content=False),
                "events": self.list_consciousness_events(limit=20),
            },
            "presence": {
                "health": self.get_presence_health(),
                "mode": self.get_presence_mode(),
                "constraints": self.get_presence_constraints(),
                "dialogue": {
                    "threads": self.list_dialogue_threads(limit=12),
                },
                "voice_pack": self.get_active_voice_pack(),
                "voice_readiness": self.get_voice_readiness_report(),
                "voice_diagnostics": self.get_voice_continuity_diagnostics(limit=120),
                "voice_tuning_profile": self.get_voice_tuning_profile(limit=120),
                "voice_tuning_overrides": self.get_voice_tuning_overrides(),
                "adaptive_policy": self.get_adaptive_policy(),
                "adaptive_history": self.list_adaptive_policy_history(limit=10),
                "self_patch_events": self.list_self_patch_events(limit=10),
            },
            "codex": {
                "tasks": self.list_codex_tasks(limit=10),
                "summary": self.codex_delegation.summarize(limit=100),
            },
        }

    def export_daily_digest(self, *, day_key: str | None = None) -> dict[str, Any]:
        exported = self.archive_service.export_daily_digest(self, day_key=day_key)
        self.memory.append_event(
            "dream.daily_digest_exported",
            {
                "day_key": exported.get("day_key"),
                "domains": exported.get("domains"),
            },
        )
        self.refresh_consciousness_surfaces(reason="digest_export")
        return exported

    def maybe_export_daily_digest(self, *, day_key: str | None = None) -> dict[str, Any]:
        exported = self.archive_service.maybe_export_daily(self, day_key=day_key)
        if not exported.get("already_exists"):
            self.memory.append_event(
                "dream.daily_digest_exported",
                {
                    "day_key": exported.get("day_key"),
                    "domains": exported.get("domains"),
                },
            )
            self.refresh_consciousness_surfaces(reason="digest_export")
        return exported

    def list_digest_exports(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return self.archive_service.list_exports(limit=limit)

    def get_digest_export(self, day_key: str) -> dict[str, Any] | None:
        return self.archive_service.get_export(day_key)

    def get_academics_overview(self, term_id: str = "current_term") -> dict[str, Any] | None:
        row = self.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_academic_overview_key(term_id),
        )
        return row["value"] if row else None

    def get_academics_schedule_context(self, term_id: str = "current_term") -> dict[str, Any] | None:
        row = self.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_academic_schedule_context_key(term_id),
        )
        return row["value"] if row else None

    def get_academics_suppression_windows(self, term_id: str = "current_term") -> dict[str, Any] | None:
        row = self.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_academic_suppression_windows_key(term_id),
        )
        return row["value"] if row else None

    def list_academic_risks(self) -> list[dict[str, Any]]:
        rows = self.state_graph.get_active_entities("Risk")
        results = []
        for row in rows:
            value = row.get("value", {})
            domain = str(value.get("domain") or value.get("project") or "").lower()
            if domain != "academics":
                continue
            results.append(
                {
                    "id": row.get("id"),
                    "risk_key": row.get("entity_key"),
                    "confidence": row.get("confidence"),
                    "value": value,
                    "last_verified_at": row.get("last_verified_at"),
                }
            )
        return results

    def _artifact_rows_with_prefix(self, prefix: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.state_graph.get_active_entities("Artifact")
        matched = [
            row
            for row in rows
            if str(row.get("entity_key", "")).startswith(prefix)
        ]
        matched.sort(key=lambda row: str(row.get("last_verified_at") or ""), reverse=True)
        return matched[:limit]

    def get_market_risk_posture(self, account_id: str = "default") -> dict[str, Any] | None:
        row = self.state_graph.get_active_entity(
            entity_type="Artifact",
            entity_key=latest_market_risk_posture_key(account_id),
        )
        return row["value"] if row else None

    def list_market_opportunities(self, *, limit: int = 20) -> list[dict[str, Any]]:
        prefix = latest_market_opportunity_key("").rsplit(":", 1)[0] + ":"
        rows = self._artifact_rows_with_prefix(prefix, limit=limit)
        return [row.get("value") for row in rows if isinstance(row.get("value"), dict)]

    def list_market_abstentions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        prefix = latest_market_abstention_key("").rsplit(":", 1)[0] + ":"
        rows = self._artifact_rows_with_prefix(prefix, limit=limit)
        return [row.get("value") for row in rows if isinstance(row.get("value"), dict)]

    def list_market_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        prefix = latest_market_event_key("").rsplit(":", 1)[0] + ":"
        rows = self._artifact_rows_with_prefix(prefix, limit=limit)
        return [row.get("value") for row in rows if isinstance(row.get("value"), dict)]

    def list_market_handoffs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        prefix = latest_market_handoff_key("").rsplit(":", 1)[0] + ":"
        rows = self._artifact_rows_with_prefix(prefix, limit=limit)
        return [row.get("value") for row in rows if isinstance(row.get("value"), dict)]

    def list_market_outcomes(self, *, limit: int = 20) -> list[dict[str, Any]]:
        prefix = latest_market_outcome_key("").rsplit(":", 1)[0] + ":"
        rows = self._artifact_rows_with_prefix(prefix, limit=limit)
        return [row.get("value") for row in rows if isinstance(row.get("value"), dict)]

    def summarize_market_outcomes(self, *, limit: int = 60) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.list_market_outcomes(limit=limit):
            status = str(item.get("status") or "unknown").strip().lower() or "unknown"
            counts[status] = counts.get(status, 0) + 1
        return {
            "total": sum(counts.values()),
            "by_status": counts,
            "limit": limit,
        }

    def record_market_handoff_outcome(self, event: EventEnvelope) -> dict[str, Any] | None:
        if event.source_type != "market.handoff_outcome":
            return None
        payload = dict(event.payload or {})
        status = str(payload.get("status") or payload.get("outcome") or "").strip().lower()
        if not status:
            return None

        plan_id = str(payload.get("plan_id") or "")
        handoff_id = str(payload.get("handoff_id") or payload.get("source_item_id") or event.event_id)
        signal_id = str(payload.get("signal_id") or "")
        account_id = str(payload.get("account_id") or "default")
        symbol = str(payload.get("symbol") or "")
        repo_id = str(payload.get("repo_id") or f"markets:{account_id}")
        branch = str(payload.get("branch") or "markets")

        status_map = {
            "filled": "success",
            "accepted": "partial",
            "rejected": "failure",
            "expired": "failure",
            "stopped": "regression",
            "skipped": "partial",
        }
        outcome_status = status_map.get(status)
        if not outcome_status:
            return None

        outcome_plan_id = plan_id or f"market_outcome:{handoff_id}"
        touched = [value for value in (f"symbol:{symbol}" if symbol else "", f"signal:{signal_id}" if signal_id else "") if value]
        summary = f"Market handoff {handoff_id} {status} for {symbol or signal_id or 'unknown'}."
        self.plan_repo.record_outcome(
            plan_id=outcome_plan_id,
            repo_id=repo_id,
            branch=branch,
            status=outcome_status,
            touched_paths=touched,
            failure_family="market_handoff",
            summary=summary,
            recorded_at=event.occurred_at,
        )
        return {
            "plan_id": outcome_plan_id,
            "repo_id": repo_id,
            "branch": branch,
            "status": outcome_status,
            "market_status": status,
            "handoff_id": handoff_id,
        }

    def list_market_risks(self) -> list[dict[str, Any]]:
        rows = self.state_graph.get_active_entities("Risk")
        results = []
        for row in rows:
            value = row.get("value", {})
            domain = str(value.get("domain") or value.get("project") or "").lower()
            if domain != "markets":
                continue
            results.append(
                {
                    "id": row.get("id"),
                    "risk_key": row.get("entity_key"),
                    "confidence": row.get("confidence"),
                    "value": value,
                    "last_verified_at": row.get("last_verified_at"),
                }
            )
        return results

    def run(self, plan_id: str, *, dry_run: bool = True, approvals: dict[str, str] | None = None) -> list[dict[str, Any]]:
        return self.executor.execute_plan(plan_id, dry_run=dry_run, approvals=approvals)

    def preflight_plan(self, plan_id: str) -> list[dict[str, Any]]:
        plan = self.plan_repo.get_plan(plan_id)
        prepared: list[dict[str, Any]] = []
        for step in plan.steps:
            action_class = ActionClass(step.action_class)
            if action_class not in {ActionClass.P2, ActionClass.P3}:
                continue
            approval = self.security.find_approval(
                plan_id=plan_id,
                step_id=step.step_id,
                statuses=["pending", "approved"],
            )
            if approval:
                approval_id = approval["approval_id"]
            else:
                approval_id = self.security.request_approval(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    action_class=action_class,
                    action_desc=step.proposed_action,
                )
            packet_data = self.executor._prepare_evidence_packet(
                plan=plan,
                step=step,
                approval_id=approval_id,
                action_class=action_class,
            )
            prepared.append(
                {
                    "approval_id": approval_id,
                    "step_id": step.step_id,
                    "action_class": action_class.value,
                    "packet_available": bool(packet_data),
                    "recommendation": (packet_data.get("packet") or {}).get("recommended_decision")
                    if packet_data
                    else None,
                    "preflight_summary": (packet_data.get("preflight") or {}).get("summary")
                    if packet_data
                    else None,
                }
            )
        return prepared

    def execute_approved_step(self, plan_id: str, step_id: str) -> dict[str, Any]:
        approval = self.security.find_approval(
            plan_id=plan_id,
            step_id=step_id,
            statuses=["approved"],
        )
        if not approval:
            raise PermissionError("No approved record found for this plan/step.")
        packet_data = self.security.get_approval_packet(approval["approval_id"])
        if not packet_data:
            raise RuntimeError("No prepared execution packet exists for this approval.")
        sandbox = packet_data.get("sandbox", {})
        sandbox_path = str(sandbox.get("sandbox_path", ""))
        if not sandbox_path or not Path(sandbox_path).exists():
            raise RuntimeError("Prepared sandbox no longer exists.")

        changed_files = self.execution_service.executor.list_changed_files(sandbox_path=sandbox_path)
        receipt = {
            "approval_id": approval["approval_id"],
            "plan_id": plan_id,
            "step_id": step_id,
            "status": "executed_in_sandbox",
            "sandbox": sandbox,
            "changed_files": changed_files,
            "next_action": "publish_approved_or_cleanup",
        }
        self.security.audit(
            action="execute_approved_step",
            status="ok",
            details=receipt,
            plan_id=plan_id,
            step_id=step_id,
            action_class=ActionClass(approval["action_class"]),
        )
        return receipt

    def _feedback_snapshot(self, artifact: ProviderReviewArtifact) -> ReviewFeedbackSnapshot:
        if artifact.feedback:
            return artifact.feedback
        return ReviewFeedbackSnapshot(
            requested_reviewers=tuple(artifact.reviewers),
            reviews=(),
            issue_comments=(),
            review_comments=(),
            timeline_events=(),
            timeline_cursor=None,
            review_summary={},
            merge_outcome=None,
            required_checks=(),
            required_checks_configured=False,
            synced_at=utc_now_iso(),
        )

    def _record_review_outcome(self, artifact: ProviderReviewArtifact) -> dict[str, Any] | None:
        plan_id = str(artifact.metadata.get("plan_id", "")).strip()
        if not plan_id:
            return None
        feedback = self._feedback_snapshot(artifact)
        decision = str(feedback.review_summary.get("decision", "none")).lower()
        merge_outcome = str(feedback.merge_outcome or "").lower()
        status, signal = map_review_feedback_to_outcome(
            decision=decision,
            merge_outcome=merge_outcome,
        )
        if not status:
            return None

        repo_id = str(artifact.metadata.get("repo_id") or artifact.repo_slug)
        touched_paths = [
            str(path).strip()
            for path in artifact.metadata.get("touched_files", [])
            if str(path).strip()
        ]
        summary = f"review_signal:{signal.lower()}"
        self.plan_repo.record_outcome(
            plan_id=plan_id,
            repo_id=repo_id,
            branch=artifact.head_branch,
            status=status,
            touched_paths=touched_paths,
            failure_family="provider_review",
            summary=summary,
        )
        return {
            "plan_id": plan_id,
            "repo_id": repo_id,
            "branch": artifact.head_branch,
            "status": status,
            "summary": summary,
            "touched_paths": touched_paths,
        }

    def _persist_provider_review(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        artifact: ProviderReviewArtifact,
        memory_category: str,
        audit_action: str,
        action_class: ActionClass,
    ) -> dict[str, Any]:
        artifact_dict = artifact.to_dict()
        feedback = self._feedback_snapshot(artifact)
        repo_id = str(artifact.metadata.get("repo_id") or artifact.repo_slug)
        branch = str(artifact.head_branch)
        pr_number = str(artifact.number)
        self.security.store_provider_review(
            approval_id=approval_id,
            plan_id=plan_id,
            step_id=step_id,
            provider=str(artifact.provider),
            repo_slug=str(artifact.repo_slug),
            review=artifact_dict,
        )
        self.security.store_review_artifact(
            approval_id=approval_id,
            plan_id=plan_id,
            step_id=step_id,
            provider=str(artifact.provider),
            repo_id=repo_id,
            repo_slug=str(artifact.repo_slug),
            pr_number=pr_number,
            branch=branch,
            artifact=artifact_dict,
        )
        self.security.store_review_feedback(
            approval_id=approval_id,
            plan_id=plan_id,
            step_id=step_id,
            provider=str(artifact.provider),
            repo_id=repo_id,
            repo_slug=str(artifact.repo_slug),
            pr_number=pr_number,
            branch=branch,
            feedback=feedback.to_dict(),
            review_summary=dict(feedback.review_summary),
            comments={
                "issue_comments": [dict(item) for item in feedback.issue_comments],
                "review_comments": [dict(item) for item in feedback.review_comments],
            },
            requested_reviewers=list(feedback.requested_reviewers),
        )
        if feedback.timeline_cursor is not None or feedback.timeline_events:
            self.security.store_review_timeline_cursor(
                approval_id=approval_id,
                plan_id=plan_id,
                step_id=step_id,
                provider=str(artifact.provider),
                repo_id=repo_id,
                repo_slug=str(artifact.repo_slug),
                pr_number=pr_number,
                branch=branch,
                timeline_cursor=feedback.timeline_cursor,
                recent_events=[dict(item) for item in feedback.timeline_events],
            )
        if feedback.merge_outcome:
            self.security.store_merge_outcome(
                approval_id=approval_id,
                plan_id=plan_id,
                step_id=step_id,
                provider=str(artifact.provider),
                repo_id=repo_id,
                repo_slug=str(artifact.repo_slug),
                pr_number=pr_number,
                branch=branch,
                merge_outcome=str(feedback.merge_outcome),
                review_decision=str(feedback.review_summary.get("decision") or ""),
                outcome={
                    "merge_outcome": feedback.merge_outcome,
                    "review_decision": feedback.review_summary.get("decision"),
                    "review_state": (artifact.status.review_state if artifact.status else artifact.state),
                    "synced_at": feedback.synced_at,
                },
            )
        touched_ids = self._state_refs_for_review(artifact)
        memory_payload: dict[str, Any] = {"review": artifact_dict}
        review_outcome = self._record_review_outcome(artifact)
        if review_outcome:
            memory_payload["review_outcome"] = review_outcome
        self.memory.add_episode(
            memory_id=new_id("epi"),
            category=memory_category,
            data=memory_payload,
            provenance_event_ids=[],
            provenance_state_ids=touched_ids,
        )
        self.security.audit(
            action=audit_action,
            status="ok",
            details=artifact_dict,
            plan_id=plan_id,
            step_id=step_id,
            action_class=action_class,
        )
        return artifact_dict

    def _state_refs_for_review(self, artifact: ProviderReviewArtifact) -> list[str]:
        repo_id = str(artifact.metadata.get("repo_id") or artifact.repo_slug)
        branch = artifact.head_branch
        source_ref = f"review:{artifact.review_local_id}"
        feedback = self._feedback_snapshot(artifact)

        touched: list[str] = []
        artifact_id = self.state_graph.upsert_entity(
            entity_id=new_id("ent"),
            entity_key=latest_review_artifact_key(repo_id, branch),
            entity_type="Artifact",
            value={
                "provider": artifact.provider,
                "repo_slug": artifact.repo_slug,
                "review_local_id": artifact.review_local_id,
                "number": artifact.number,
                "title": artifact.title,
                "web_url": artifact.web_url,
                "state": artifact.state,
                "draft": artifact.draft,
                "base_branch": artifact.base_branch,
                "head_branch": artifact.head_branch,
                "head_sha": artifact.head_sha,
                "labels": list(artifact.labels),
                "reviewers": list(artifact.reviewers),
                "assignees": list(artifact.assignees),
                "updated_at": artifact.updated_at,
            },
            confidence=0.96,
            source_refs=[source_ref],
            last_verified_at=artifact.updated_at,
        )
        touched.append(artifact_id)
        if artifact.status:
            status_id = self.state_graph.upsert_entity(
                entity_id=new_id("ent"),
                entity_key=latest_review_status_key(repo_id, branch),
                entity_type="Artifact",
                value={
                    "provider": artifact.provider,
                    "repo_slug": artifact.repo_slug,
                    "review_local_id": artifact.review_local_id,
                    "number": artifact.number,
                    "review_state": artifact.status.review_state,
                    "checks_state": artifact.status.checks_state,
                    "merged": artifact.status.merged,
                    "draft": artifact.status.draft,
                    "mergeable": artifact.status.mergeable,
                    "blocking_contexts": list(artifact.status.blocking_contexts),
                    "head_sha": artifact.status.head_sha,
                    "web_url": artifact.status.web_url,
                    "synced_at": artifact.status.synced_at,
                    "provider_updated_at": artifact.status.provider_updated_at,
                },
                confidence=0.97,
                source_refs=[source_ref],
                last_verified_at=artifact.status.synced_at,
            )
            touched.append(status_id)
        reviewers_id = self.state_graph.upsert_entity(
            entity_id=new_id("ent"),
            entity_key=latest_requested_reviewers_key(repo_id, branch),
            entity_type="Artifact",
            value={
                "provider": artifact.provider,
                "repo_slug": artifact.repo_slug,
                "number": artifact.number,
                "requested_reviewers": list(feedback.requested_reviewers),
                "synced_at": feedback.synced_at,
            },
            confidence=0.95,
            source_refs=[source_ref],
            last_verified_at=feedback.synced_at,
        )
        touched.append(reviewers_id)
        summary_id = self.state_graph.upsert_entity(
            entity_id=new_id("ent"),
            entity_key=latest_review_summary_key(repo_id, branch),
            entity_type="Artifact",
            value={
                "provider": artifact.provider,
                "repo_slug": artifact.repo_slug,
                "number": artifact.number,
                "review_summary": dict(feedback.review_summary),
                "review_state": (artifact.status.review_state if artifact.status else artifact.state),
                "checks_state": (artifact.status.checks_state if artifact.status else None),
                "required_checks_configured": feedback.required_checks_configured,
                "required_checks": list(feedback.required_checks),
                "synced_at": feedback.synced_at,
            },
            confidence=0.95,
            source_refs=[source_ref],
            last_verified_at=feedback.synced_at,
        )
        touched.append(summary_id)
        comments_id = self.state_graph.upsert_entity(
            entity_id=new_id("ent"),
            entity_key=latest_review_comments_key(repo_id, branch),
            entity_type="Artifact",
            value={
                "provider": artifact.provider,
                "repo_slug": artifact.repo_slug,
                "number": artifact.number,
                "issue_comments": [dict(item) for item in feedback.issue_comments],
                "review_comments": [dict(item) for item in feedback.review_comments],
                "issue_comment_count": len(feedback.issue_comments),
                "review_comment_count": len(feedback.review_comments),
                "synced_at": feedback.synced_at,
            },
            confidence=0.93,
            source_refs=[source_ref],
            last_verified_at=feedback.synced_at,
        )
        touched.append(comments_id)
        if feedback.timeline_cursor is not None or feedback.timeline_events:
            cursor_id = self.state_graph.upsert_entity(
                entity_id=new_id("ent"),
                entity_key=latest_timeline_cursor_key(repo_id, branch),
                entity_type="Artifact",
                value={
                    "provider": artifact.provider,
                    "repo_slug": artifact.repo_slug,
                    "number": artifact.number,
                    "timeline_cursor": feedback.timeline_cursor,
                    "recent_events": [dict(item) for item in feedback.timeline_events],
                    "synced_at": feedback.synced_at,
                },
                confidence=0.92,
                source_refs=[source_ref],
                last_verified_at=feedback.synced_at,
            )
            touched.append(cursor_id)
        if feedback.merge_outcome:
            merge_id = self.state_graph.upsert_entity(
                entity_id=new_id("ent"),
                entity_key=latest_merge_outcome_key(repo_id, branch),
                entity_type="Artifact",
                value={
                    "provider": artifact.provider,
                    "repo_slug": artifact.repo_slug,
                    "number": artifact.number,
                    "merge_outcome": feedback.merge_outcome,
                    "review_decision": feedback.review_summary.get("decision"),
                    "review_state": (artifact.status.review_state if artifact.status else artifact.state),
                    "synced_at": feedback.synced_at,
                },
                confidence=0.96,
                source_refs=[source_ref],
                last_verified_at=feedback.synced_at,
            )
            touched.append(merge_id)
        return touched

    def open_provider_review(
        self,
        plan_id: str,
        step_id: str,
        *,
        provider: str,
        repo_slug: str,
        reviewers: list[str] | tuple[str, ...] | None = None,
        labels: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        publication = self.security.find_publication_receipt(plan_id=plan_id, step_id=step_id)
        if not publication:
            raise RuntimeError("No publication receipt exists for this plan/step.")

        existing = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if existing and existing.get("provider") == provider and existing.get("repo_slug") == repo_slug:
            return existing["review"]

        payload = publication["pr_payload"]
        combined_labels = tuple(dict.fromkeys([*(payload.get("labels") or []), *((labels or []))]))
        publication_details = publication["publication"]
        plan = self.plan_repo.get_plan(plan_id)
        root_payload = plan.steps[0].payload if plan.steps else {}
        repo_id = str(
            root_payload.get("repo_id")
            or publication_details.get("repo_id")
            or str(self.repo_path)
        )
        packet = self.security.get_approval_packet(str(publication["approval_id"])) or {}
        touched_files = packet.get("touched_files", [])
        artifact = self.review_service.open_review(
            provider_name=provider,
            repo_slug=repo_slug,
            title=str(payload["title"]),
            body_markdown=str(payload["body_markdown"]),
            head_branch=str(payload["head_branch"]),
            base_branch=str(payload["base_branch"]),
            head_sha=str(publication_details["push"]["head_sha"]),
            draft=bool(payload.get("draft", True)),
            labels=combined_labels,
            reviewers=tuple(reviewers or ()),
            metadata={
                "repo_id": repo_id,
                "approval_id": str(publication["approval_id"]),
                "plan_id": plan_id,
                "step_id": step_id,
                "touched_files": touched_files,
            },
        )
        artifact_dict = self._persist_provider_review(
            approval_id=str(publication["approval_id"]),
            plan_id=plan_id,
            step_id=step_id,
            artifact=artifact,
            memory_category="provider_review_opened",
            audit_action="open_provider_review",
            action_class=ActionClass.P1,
        )
        return artifact_dict

    def get_provider_review(self, plan_id: str, step_id: str) -> dict[str, Any] | None:
        stored = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if not stored:
            return None
        return stored.get("review")

    def sync_review_feedback(
        self,
        repo_id: str,
        pr_number: str,
        branch: str,
    ) -> dict[str, Any]:
        stored = self.security.find_provider_review_by_ref(
            repo_id=repo_id,
            pr_number=pr_number,
            branch=branch,
        )
        if not stored:
            raise KeyError(
                f"Provider review not found for repo_id={repo_id} pr_number={pr_number} branch={branch}"
            )
        return self.sync_provider_review(stored["plan_id"], stored["step_id"])

    def sync_provider_review(self, plan_id: str, step_id: str) -> dict[str, Any]:
        stored = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if not stored:
            raise KeyError(f"Provider review not found for {plan_id}/{step_id}")
        review = ProviderReviewArtifact.from_dict(stored["review"])
        metadata = dict(review.metadata or {})
        updated = self.review_service.sync_review_feedback(
            repo_id=str(metadata.get("repo_id") or review.repo_slug),
            pr_number=review.number,
            branch=review.head_branch,
            provider_name=review.provider,
            repo_slug=review.repo_slug,
            existing_review=review,
            metadata=metadata,
        )
        updated_dict = self._persist_provider_review(
            approval_id=str(stored["approval_id"]),
            plan_id=plan_id,
            step_id=step_id,
            artifact=updated,
            memory_category="provider_review_synced",
            audit_action="sync_provider_review",
            action_class=ActionClass.P0,
        )
        return updated_dict

    def configure_provider_review(
        self,
        plan_id: str,
        step_id: str,
        *,
        reviewers: list[str] | tuple[str, ...] | None = None,
        labels: list[str] | tuple[str, ...] | None = None,
        assignees: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        stored = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if not stored:
            raise KeyError(f"Provider review not found for {plan_id}/{step_id}")
        updated = self.review_service.configure_review(
            stored["review"],
            reviewers=reviewers,
            labels=labels,
            assignees=assignees,
        )
        return self._persist_provider_review(
            approval_id=str(stored["approval_id"]),
            plan_id=plan_id,
            step_id=step_id,
            artifact=updated,
            memory_category="provider_review_configured",
            audit_action="configure_provider_review",
            action_class=ActionClass.P1,
        )

    def get_review_summary(self, plan_id: str, step_id: str) -> dict[str, Any]:
        stored = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if not stored:
            raise KeyError(f"Provider review not found for {plan_id}/{step_id}")
        review = ProviderReviewArtifact.from_dict(stored["review"])
        feedback = self._feedback_snapshot(review)
        feedback_row = self.security.find_review_feedback(plan_id=plan_id, step_id=step_id) or {}
        timeline_row = self.security.find_review_timeline_cursor(plan_id=plan_id, step_id=step_id) or {}
        merge_row = self.security.find_merge_outcome(plan_id=plan_id, step_id=step_id) or {}
        packet = self.security.find_approval_packet(plan_id=plan_id, step_id=step_id) or {}
        return {
            "plan_id": plan_id,
            "step_id": step_id,
            "review": {
                "provider": review.provider,
                "repo_slug": review.repo_slug,
                "number": review.number,
                "web_url": review.web_url,
                "state": review.state,
                "draft": review.draft,
                "labels": list(review.labels),
                "reviewers": list(review.reviewers),
                "assignees": list(review.assignees),
                "head_branch": review.head_branch,
                "base_branch": review.base_branch,
                "head_sha": review.head_sha,
            },
            "hosted_feedback": {
                "requested_reviewers": feedback_row.get(
                    "requested_reviewers",
                    list(feedback.requested_reviewers),
                ),
                "review_summary": feedback_row.get(
                    "review_summary",
                    dict(feedback.review_summary),
                ),
                "timeline_cursor": timeline_row.get("timeline_cursor", feedback.timeline_cursor),
                "recent_timeline_events": timeline_row.get(
                    "recent_events",
                    [dict(item) for item in feedback.timeline_events],
                ),
                "merge_outcome": merge_row.get("merge_outcome", feedback.merge_outcome),
                "review_decision": merge_row.get(
                    "review_decision",
                    feedback.review_summary.get("decision"),
                ),
            },
            "approval_evidence": {
                "approval_id": packet.get("approval_id"),
                "packet": packet.get("packet"),
                "preflight": packet.get("preflight"),
                "touched_files": packet.get("touched_files"),
                "created_at": packet.get("created_at"),
                "updated_at": packet.get("updated_at"),
            },
        }

    def get_review_comments(self, plan_id: str, step_id: str) -> dict[str, Any]:
        stored = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if not stored:
            raise KeyError(f"Provider review not found for {plan_id}/{step_id}")
        review = ProviderReviewArtifact.from_dict(stored["review"])
        feedback_snapshot = self._feedback_snapshot(review)
        feedback = self.security.find_review_feedback(plan_id=plan_id, step_id=step_id) or {}
        comments = feedback.get("comments") or {}
        issue_comments = list(comments.get("issue_comments") or [dict(item) for item in feedback_snapshot.issue_comments])
        review_comments = list(comments.get("review_comments") or [dict(item) for item in feedback_snapshot.review_comments])
        return {
            "plan_id": plan_id,
            "step_id": step_id,
            "provider": review.provider,
            "repo_slug": review.repo_slug,
            "number": review.number,
            "issue_comment_count": len(issue_comments),
            "review_comment_count": len(review_comments),
            "issue_comments": issue_comments,
            "review_comments": review_comments,
        }

    def _build_single_maintainer_override_policy(
        self,
        *,
        enabled: bool,
        actor: str | None,
        reason: str | None,
        sunset_condition: str | None,
        review: ProviderReviewArtifact,
    ) -> dict[str, Any] | None:
        if not enabled:
            return None
        repo_id = str(review.metadata.get("repo_id") or review.repo_slug)
        return {
            "actor": str(actor or os.getenv("USER") or "local-operator"),
            "repo_id": repo_id,
            "pr_number": str(review.number),
            "reason": str(
                reason
                or "single-maintainer override for reviewer/check gate in transitional policy"
            ),
            "applied_at": utc_now_iso(),
            "sunset_condition": str(
                sunset_condition
                or "disable when required checks are configured or repo has >1 maintainer"
            ),
        }

    def evaluate_review_promotion_policy(
        self,
        plan_id: str,
        step_id: str,
        *,
        required_labels: list[str] | tuple[str, ...] | None = None,
        allow_no_required_checks: bool = False,
        single_maintainer_override: bool = False,
        override_actor: str | None = None,
        override_reason: str | None = None,
        override_sunset_condition: str | None = None,
    ) -> dict[str, Any]:
        stored = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if not stored:
            raise KeyError(f"Provider review not found for {plan_id}/{step_id}")
        review = ProviderReviewArtifact.from_dict(stored["review"])
        feedback = self._feedback_snapshot(review)
        override_policy = self._build_single_maintainer_override_policy(
            enabled=bool(single_maintainer_override),
            actor=override_actor,
            reason=override_reason,
            sunset_condition=override_sunset_condition,
            review=review,
        )
        approval = self.security.find_approval(
            plan_id=plan_id,
            step_id=step_id,
            statuses=["approved"],
        )
        packet = self.security.find_approval_packet(plan_id=plan_id, step_id=step_id)
        preflight = (packet or {}).get("preflight", {})
        preflight_clean = bool(preflight.get("passed", False))
        has_approval_packet = packet is not None
        approval_exists = approval is not None

        requested_reviewers = list(feedback.requested_reviewers or review.reviewers)
        reviewers_set = len(requested_reviewers) > 0
        reviewers_gate = reviewers_set or bool(single_maintainer_override)

        publication = self.security.find_publication_receipt(plan_id=plan_id, step_id=step_id)
        default_required_labels = (publication or {}).get("pr_payload", {}).get("labels", [])
        target_labels = list(required_labels) if required_labels is not None else list(default_required_labels)
        target_labels = list(dict.fromkeys(str(item) for item in target_labels if str(item).strip()))
        current_labels = list(review.labels)
        labels_normalized = (
            set(current_labels) == set(target_labels)
            if target_labels
            else len(current_labels) > 0
        )

        required_checks_configured = bool(feedback.required_checks_configured)
        checks_state = review.status.checks_state if review.status else None
        if required_checks_configured:
            checks_gate = checks_state == "success"
            checks_reason = (
                "required_checks_passing"
                if checks_gate
                else f"required_checks_not_passing:{checks_state or 'unknown'}"
            )
        else:
            checks_gate = bool(allow_no_required_checks)
            checks_reason = (
                "no_required_checks_allowed"
                if checks_gate
                else "no_required_checks_configured"
            )

        reasons: list[str] = []
        if not approval_exists:
            reasons.append("approval_missing")
        if not has_approval_packet:
            reasons.append("approval_packet_missing")
        if not preflight_clean:
            reasons.append("preflight_not_clean")
        if not reviewers_gate:
            reasons.append("requested_reviewers_missing")
        if not labels_normalized:
            reasons.append("labels_not_normalized")
        if not checks_gate:
            reasons.append(checks_reason)

        eligible = len(reasons) == 0
        return {
            "plan_id": plan_id,
            "step_id": step_id,
            "eligible": eligible,
            "reasons": reasons,
            "policy": {
                "approval_exists": approval_exists,
                "approval_packet_exists": has_approval_packet,
                "preflight_clean": preflight_clean,
                "requested_reviewers": requested_reviewers,
                "reviewers_set": reviewers_set,
                "reviewers_gate": reviewers_gate,
                "single_maintainer_override": single_maintainer_override,
                "single_maintainer_override_policy": override_policy,
                "target_labels": target_labels,
                "current_labels": current_labels,
                "labels_normalized": labels_normalized,
                "required_checks_configured": required_checks_configured,
                "required_checks": list(feedback.required_checks),
                "checks_state": checks_state,
                "checks_gate": checks_gate,
                "checks_reason": checks_reason,
                "allow_no_required_checks": allow_no_required_checks,
            },
            "review": review.to_dict(),
        }

    def promote_provider_review_ready(
        self,
        plan_id: str,
        step_id: str,
        *,
        required_labels: list[str] | tuple[str, ...] | None = None,
        allow_no_required_checks: bool = False,
        single_maintainer_override: bool = False,
        override_actor: str | None = None,
        override_reason: str | None = None,
        override_sunset_condition: str | None = None,
    ) -> dict[str, Any]:
        policy = self.evaluate_review_promotion_policy(
            plan_id,
            step_id,
            required_labels=required_labels,
            allow_no_required_checks=allow_no_required_checks,
            single_maintainer_override=single_maintainer_override,
            override_actor=override_actor,
            override_reason=override_reason,
            override_sunset_condition=override_sunset_condition,
        )
        if not policy["eligible"]:
            return {"promoted": False, "policy": policy}
        review = ProviderReviewArtifact.from_dict(policy["review"])
        if not review.draft:
            return {"promoted": False, "already_ready": True, "policy": policy, "review": review.to_dict()}
        stored = self.security.find_provider_review(plan_id=plan_id, step_id=step_id)
        if not stored:
            raise KeyError(f"Provider review not found for {plan_id}/{step_id}")
        promoted = self.review_service.mark_ready_for_review(review)
        if single_maintainer_override or allow_no_required_checks:
            metadata = dict(promoted.metadata or {})
            override_policy = policy["policy"].get("single_maintainer_override_policy")
            metadata["promotion_override"] = {
                "single_maintainer_override": bool(single_maintainer_override),
                "allow_no_required_checks": bool(allow_no_required_checks),
                "applied_at": utc_now_iso(),
                "policy": dict(override_policy or {}),
            }
            promoted = promoted.with_updates(metadata=metadata)
        review_dict = self._persist_provider_review(
            approval_id=str(stored["approval_id"]),
            plan_id=plan_id,
            step_id=step_id,
            artifact=promoted,
            memory_category="provider_review_promoted_ready",
            audit_action="promote_provider_review_ready",
            action_class=ActionClass.P1,
        )
        return {
            "promoted": True,
            "policy": policy,
            "review": review_dict,
            "override_applied": bool(single_maintainer_override or allow_no_required_checks),
        }

    def publish_approved_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        remote_name: str = "origin",
        base_branch: str | None = None,
        draft: bool = True,
        force_with_lease: bool = False,
        open_review: bool = False,
        provider: str | None = None,
        provider_repo: str | None = None,
        reviewers: list[str] | tuple[str, ...] | None = None,
        labels: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        approval = self.security.find_approval(
            plan_id=plan_id,
            step_id=step_id,
            statuses=["approved"],
        )
        if not approval:
            raise PermissionError("No approved record found for this plan/step.")

        packet_data = self.security.get_approval_packet(approval["approval_id"])
        if not packet_data:
            raise RuntimeError("No prepared execution packet exists for this approval.")

        publication = self.security.get_publication_receipt(approval["approval_id"])
        if publication:
            receipt_dict = publication["publication"]
        else:
            plan = self.plan_repo.get_plan(plan_id)
            target_step = next((step for step in plan.steps if step.step_id == step_id), None)
            if not target_step:
                raise KeyError(f"Step not found: {step_id}")

            receipt = self.publication_service.publish_prepared_step(
                plan=plan,
                step=target_step,
                approval=approval,
                approval_packet_data=packet_data,
                remote_name=remote_name,
                base_branch=base_branch,
                draft=draft,
                force_with_lease=force_with_lease,
            )
            receipt_dict = receipt.to_dict()
            self.security.store_publication_receipt(
                approval_id=approval["approval_id"],
                plan_id=plan_id,
                step_id=step_id,
                publication=receipt_dict,
                pr_payload=receipt.pr_payload.to_dict(),
            )
            self.security.audit(
                action="publish_approved_step",
                status="ok",
                details=receipt_dict,
                plan_id=plan_id,
                step_id=step_id,
                action_class=ActionClass(approval["action_class"]),
            )

        if open_review:
            if not provider or not provider_repo:
                raise ValueError("provider and provider_repo are required when open_review=True")
            review = self.open_provider_review(
                plan_id,
                step_id,
                provider=provider,
                repo_slug=provider_repo,
                reviewers=list(reviewers or ()),
                labels=list(labels or ()),
            )
            return {"publication": receipt_dict, "review": review}

        return receipt_dict

    def get_pr_payload(self, plan_id: str, step_id: str) -> dict[str, Any] | None:
        publication = self.security.find_publication_receipt(plan_id=plan_id, step_id=step_id)
        if not publication:
            return None
        return publication.get("pr_payload")

    def close(self) -> None:
        self.stop_openclaw_gateway_loop()
        self.cognition.close()
        self.synthesis_engine.close()
        self.archive_service.close()
        self.codex_delegation.close()
        self.operator_state.close()
        self.identity_state.close()
        self.interrupt_store.close()
        self.state_graph.close()
        self.memory.close()
        self.dialogue_state.close()
        self.signal_ingest.close()
        self.device_tokens.close()
        self.presence_health.close()
        self.surface_sessions.close()
        self.relationship_modes.close()
        self.pushback_calibration.close()
        self.tone_balance.close()
        self.voice_soak.close()
        self.voice_tuning_state.close()
        self.adaptive_policy.close()
        self.security.close()
        self.plan_repo.close()
