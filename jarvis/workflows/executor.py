from __future__ import annotations

from pathlib import Path
from typing import Any

from ..approval_packet import OutcomeSummary, RankedCandidate
from ..execution_service import ApprovalExecutionService
from ..models import PlanArtifact, PlanStep
from ..reasoning.schema import TraceStatus
from ..reasoning.tracer import ReasoningTracer
from ..security import ActionClass, SecurityManager
from .models import StepState
from .plan_repository import PlanRepository


class Executor:
    def __init__(
        self,
        *,
        repo_path: Path,
        security: SecurityManager,
        plan_repo: PlanRepository,
        tools: dict[str, Any],
        execution_service: ApprovalExecutionService | None = None,
        reasoning_tracer: ReasoningTracer | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.security = security
        self.plan_repo = plan_repo
        self.tools = tools
        self.execution_service = execution_service
        self.reasoning_tracer = reasoning_tracer

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
            step_trace_id: str | None = None
            if self.reasoning_tracer:
                step_trace_id = self.reasoning_tracer.open_step_trace(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    action_class=action_class.value,
                    proposed_action=step.proposed_action,
                    payload=step.payload,
                    dry_run=dry_run,
                )
                candidate_id = self.reasoning_tracer.add_candidate(
                    trace_id=step_trace_id,
                    candidate_kind="workflow_step_action",
                    candidate_ref=step.proposed_action,
                    rationale="workflow_selected_action",
                    confidence=1.0,
                    metadata={
                        "step_id": step.step_id,
                        "action_class": action_class.value,
                    },
                )
                self.reasoning_tracer.select_candidate(
                    trace_id=step_trace_id,
                    candidate_id=candidate_id,
                    selected_reason="step_execution_path",
                    metadata={"dry_run": bool(dry_run)},
                )
            self.plan_repo.record_step_attempt(
                plan_id=plan_id,
                step_id=step.step_id,
                step_state=StepState.RUNNING,
                details={
                    "action_class": action_class.value,
                    "requires_approval": bool(step.requires_approval),
                    "dry_run": bool(dry_run),
                },
            )
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
                    self.plan_repo.record_step_attempt(
                        plan_id=plan_id,
                        step_id=step.step_id,
                        step_state=StepState.BLOCKED,
                        details={
                            "reason": "awaiting_approval",
                            "approval_id": pending_id,
                            "action_class": action_class.value,
                        },
                    )
                    if self.reasoning_tracer and step_trace_id:
                        self.reasoning_tracer.record_event(
                            trace_id=step_trace_id,
                            event_type="approval.pending",
                            payload={
                                "approval_id": pending_id,
                                "action_class": action_class.value,
                            },
                        )
                        self.reasoning_tracer.finalize_trace(
                            trace_id=step_trace_id,
                            status=TraceStatus.BLOCKED,
                            summary="Protected action requires approval.",
                            payload={"approval_id": pending_id},
                        )
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
                self.plan_repo.record_step_attempt(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    step_state=StepState.APPROVED,
                    details={
                        "approval_id": approval_id,
                        "action_class": action_class.value,
                    },
                )
                if self.reasoning_tracer and step_trace_id:
                    self.reasoning_tracer.record_event(
                        trace_id=step_trace_id,
                        event_type="approval.applied",
                        payload={
                            "approval_id": approval_id,
                            "action_class": action_class.value,
                        },
                    )
            try:
                self.security.enforce(
                    action_class,
                    requires_approval=step.requires_approval,
                    approval_id=approval_id,
                )
            except PermissionError as exc:
                result = {"step_id": step.step_id, "status": "blocked", "reason": str(exc)}
                self.plan_repo.record_step_attempt(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    step_state=StepState.BLOCKED,
                    details={
                        "reason": str(exc),
                        "action_class": action_class.value,
                    },
                )
                if self.reasoning_tracer and step_trace_id:
                    self.reasoning_tracer.record_event(
                        trace_id=step_trace_id,
                        event_type="security.blocked",
                        payload={"reason": str(exc)},
                    )
                    self.reasoning_tracer.finalize_trace(
                        trace_id=step_trace_id,
                        status=TraceStatus.BLOCKED,
                        summary=str(exc),
                        payload={"reason": str(exc)},
                    )
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
                self.plan_repo.record_step_attempt(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    step_state=StepState.FAILED,
                    details={
                        "reason": f"Tool not registered: {step.proposed_action}",
                        "action_class": action_class.value,
                    },
                )
                if self.reasoning_tracer and step_trace_id:
                    self.reasoning_tracer.record_event(
                        trace_id=step_trace_id,
                        event_type="execution.missing_tool",
                        payload={"tool": step.proposed_action},
                    )
                    self.reasoning_tracer.finalize_trace(
                        trace_id=step_trace_id,
                        status=TraceStatus.FAILED,
                        summary=f"Tool not registered: {step.proposed_action}",
                        payload={"tool": step.proposed_action},
                    )
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

            try:
                output = tool(step.payload, dry_run)
            except Exception as exc:
                self.plan_repo.record_step_attempt(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    step_state=StepState.FAILED,
                    details={
                        "reason": str(exc),
                        "action_class": action_class.value,
                        "phase": "tool_execution",
                    },
                )
                compensation_payload: dict[str, Any] | None = None
                if prepare_id:
                    compensation_strategy = (
                        "rollback_marker"
                        if str(step.rollback).strip().lower() != "none"
                        else "no_rollback_hint"
                    )
                    compensation_payload = {
                        "prepare_id": prepare_id,
                        "rollback_hint": step.rollback,
                        "action_class": action_class.value,
                        "dry_run": bool(dry_run),
                        "failure_reason": str(exc),
                        "strategy": compensation_strategy,
                    }
                    compensation_id = self.plan_repo.record_step_compensation(
                        plan_id=plan_id,
                        step_id=step.step_id,
                        reason=str(exc),
                        strategy=compensation_strategy,
                        details=compensation_payload,
                    )
                    self.plan_repo.record_step_attempt(
                        plan_id=plan_id,
                        step_id=step.step_id,
                        step_state=StepState.COMPENSATED,
                        details={
                            "compensation_id": compensation_id,
                            "reason": str(exc),
                            "strategy": compensation_strategy,
                            "action_class": action_class.value,
                        },
                    )
                if self.reasoning_tracer and step_trace_id:
                    self.reasoning_tracer.record_event(
                        trace_id=step_trace_id,
                        event_type="execution.exception",
                        payload={"reason": str(exc)},
                    )
                    if compensation_payload is not None:
                        self.reasoning_tracer.record_event(
                            trace_id=step_trace_id,
                            event_type="compensation.applied",
                            payload=dict(compensation_payload),
                        )
                        self.reasoning_tracer.finalize_trace(
                            trace_id=step_trace_id,
                            status=TraceStatus.COMPENSATED,
                            summary=f"Step compensated after failure: {exc}",
                            payload=dict(compensation_payload),
                        )
                    else:
                        self.reasoning_tracer.finalize_trace(
                            trace_id=step_trace_id,
                            status=TraceStatus.FAILED,
                            summary=str(exc),
                            payload={"reason": str(exc)},
                        )
                if compensation_payload is not None:
                    self.security.audit(
                        action=step.proposed_action,
                        status="compensated",
                        details={
                            "reason": str(exc),
                            "compensation": dict(compensation_payload),
                        },
                        plan_id=plan_id,
                        step_id=step.step_id,
                        action_class=action_class,
                    )
                raise
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
            self.plan_repo.record_step_attempt(
                plan_id=plan_id,
                step_id=step.step_id,
                step_state=StepState.SUCCEEDED,
                details={
                    "action_class": action_class.value,
                    "tool": step.proposed_action,
                    "dry_run": bool(dry_run),
                },
            )
            if self.reasoning_tracer and step_trace_id:
                self.reasoning_tracer.record_event(
                    trace_id=step_trace_id,
                    event_type="execution.succeeded",
                    payload={
                        "tool": step.proposed_action,
                        "dry_run": bool(dry_run),
                    },
                )
                self.reasoning_tracer.finalize_trace(
                    trace_id=step_trace_id,
                    status=TraceStatus.SUCCEEDED,
                    summary=f"Executed {step.proposed_action}",
                    payload={"tool": step.proposed_action},
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
