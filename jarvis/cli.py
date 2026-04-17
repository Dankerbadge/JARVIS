from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .approval_inbox import ApprovalInbox
from .connectors.academics import AcademicsFeedConnector
from .connectors.academics_calendar import AcademicCalendarConnector
from .connectors.academics_gmail import GmailAcademicsConnector
from .connectors.academics_google_calendar import GoogleCalendarConnector
from .connectors.academics_materials import AcademicMaterialsConnector
from .connectors.ci_reports import JsonCIReportConnector
from .connectors.git_native import GitNativeRepoConnector
from .connectors.markets_calendar import MarketsCalendarConnector
from .connectors.markets_outcomes import MarketsOutcomesConnector
from .connectors.markets_positions import MarketsPositionsConnector
from .connectors.markets_signals import MarketsSignalsConnector
from .connectors.personal_context import PersonalContextConnector
from .connectors.repo import RepoChangeConnector
from .daemon import EventDaemon
from .evaluation import compare_backends_on_snapshot
from .reactors import ZenithCorrelationReactor, ZenithGitDeltaReactor, ZenithRiskReactor
from .runtime import JarvisRuntime
from .security import ActionClass, SecurityManager
from .server import run_operator_server


def _build_demo_repo(repo_path: Path) -> None:
    (repo_path / "ui").mkdir(parents=True, exist_ok=True)
    (repo_path / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
    (repo_path / "service.py").write_text(
        "def render():\n    return 'TODO_ZENITH'\n",
        encoding="utf-8",
    )


def _default_db_path() -> Path:
    return Path.cwd() / ".jarvis" / "jarvis.db"


def _default_repo_path() -> Path:
    return Path(os.getenv("JARVIS_REPO_PATH", str(Path.cwd())))


def _int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_token(
    *,
    explicit_value: str | None,
    env_var_name: str | None,
) -> str | None:
    if explicit_value:
        return str(explicit_value).strip() or None
    env_name = str(env_var_name or "").strip()
    if not env_name:
        return None
    value = str(os.getenv(env_name) or "").strip()
    return value or None


def _resolve_secret(
    *,
    explicit_value: str | None,
    env_var_name: str | None,
) -> str | None:
    return _resolve_token(explicit_value=explicit_value, env_var_name=env_var_name)


def _configure_openclaw_gateway(runtime: JarvisRuntime, args: argparse.Namespace) -> dict[str, Any] | None:
    ws_url = str(getattr(args, "openclaw_gateway_ws_url", "") or "").strip() or None
    token_ref = _resolve_token(
        explicit_value=getattr(args, "openclaw_gateway_token_ref", None),
        env_var_name=getattr(args, "openclaw_gateway_token_ref_env", None),
    )
    owner_id = str(getattr(args, "openclaw_gateway_owner_id", "") or "").strip() or None
    client_name = str(getattr(args, "openclaw_gateway_client_name", "") or "").strip() or None
    profile_id = str(getattr(args, "openclaw_gateway_profile_id", "") or "").strip() or None
    profile_path = str(getattr(args, "openclaw_gateway_profile_path", "") or "").strip() or None
    enable_flag = bool(getattr(args, "openclaw_gateway_enable", False))
    allow_remote = bool(getattr(args, "openclaw_gateway_allow_remote", False))
    if not any([ws_url, token_ref, enable_flag, profile_id, profile_path]):
        return None
    return runtime.configure_openclaw_gateway_loop(
        ws_url=ws_url,
        token_ref=token_ref,
        owner_id=owner_id,
        client_name=client_name,
        protocol_profile_id=profile_id,
        protocol_profile_path=profile_path,
        allow_remote=allow_remote,
        enabled=enable_flag,
        connect_timeout_seconds=float(getattr(args, "openclaw_gateway_connect_timeout", 8.0)),
        heartbeat_interval_seconds=float(getattr(args, "openclaw_gateway_heartbeat", 20.0)),
    )


def run_demo(repo_path: Path, db_path: Path) -> dict:
    _build_demo_repo(repo_path)
    runtime = JarvisRuntime(db_path=db_path, repo_path=repo_path)
    try:
        ingestion = runtime.ingest_event(
            source="github",
            source_type="ci",
            payload={"project": "zenith", "status": "failed", "deadline_hours": 24},
        )
        plan_ids = runtime.plan(ingestion["triggers"])
        if not plan_ids:
            return {"ingestion": ingestion, "plan_ids": [], "execution": []}

        plan_id = plan_ids[0]
        plan = runtime.plan_repo.get_plan(plan_id)
        approvals = {}
        for step in plan.steps:
            if step.action_class == ActionClass.P2.value and step.requires_approval:
                approval_id = runtime.security.request_approval(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    action_class=ActionClass.P2,
                    action_desc=step.proposed_action,
                )
                runtime.security.approve(approval_id, approved_by="demo")
                approvals[step.step_id] = approval_id
        execution = runtime.run(plan_id, dry_run=True, approvals=approvals)
        return {"ingestion": ingestion, "plan_ids": plan_ids, "execution": execution}
    finally:
        runtime.close()


def build_daemon(repo_path: Path, db_path: Path) -> tuple[JarvisRuntime, EventDaemon]:
    runtime = JarvisRuntime(db_path=db_path, repo_path=repo_path)
    connectors = []
    try:
        connectors.append(GitNativeRepoConnector(repo_path=repo_path, emit_on_initial_scan=False))
    except Exception:
        # Fallback for non-git directories.
        connectors.append(RepoChangeConnector(repo_path=repo_path, emit_on_initial_scan=False))
    reactors = [ZenithCorrelationReactor(), ZenithGitDeltaReactor(), ZenithRiskReactor()]
    daemon = EventDaemon(runtime=runtime, connectors=connectors, reactors=reactors)
    return runtime, daemon


def build_daemon_with_optional_ci(
    repo_path: Path,
    db_path: Path,
    ci_reports_path: Path | None,
    academics_feed_path: Path | None,
    academics_calendar_path: Path | None,
    academics_materials_path: Path | None,
    google_calendar_id: str | None,
    google_api_token: str | None,
    google_refresh_token: str | None,
    google_client_id: str | None,
    google_client_secret: str | None,
    google_token_endpoint: str,
    gmail_query: str | None,
    gmail_max_results: int,
    personal_context_path: Path | None,
    markets_signals_path: Path | None,
    markets_positions_path: Path | None,
    markets_calendar_path: Path | None,
    markets_outcomes_path: Path | None,
) -> tuple[JarvisRuntime, EventDaemon]:
    runtime, daemon = build_daemon(repo_path, db_path)
    has_google_refresh = bool(
        str(google_refresh_token or "").strip()
        and str(google_client_id or "").strip()
        and str(google_client_secret or "").strip()
    )
    if ci_reports_path is None:
        pass
    else:
        daemon.connectors.append(JsonCIReportConnector(ci_reports_path))
    if academics_feed_path is not None:
        daemon.connectors.append(AcademicsFeedConnector(academics_feed_path))
    if academics_calendar_path is not None:
        daemon.connectors.append(AcademicCalendarConnector(academics_calendar_path))
    if academics_materials_path is not None:
        daemon.connectors.append(AcademicMaterialsConnector(academics_materials_path))
    if google_calendar_id:
        if not google_api_token and not has_google_refresh:
            raise ValueError("Google Calendar intake requires access token or refresh-token credentials.")
        daemon.connectors.append(
            GoogleCalendarConnector(
                calendar_id=google_calendar_id,
                token=google_api_token,
                refresh_token=google_refresh_token,
                client_id=google_client_id,
                client_secret=google_client_secret,
                token_endpoint=google_token_endpoint,
            )
        )
    if gmail_query:
        if not google_api_token and not has_google_refresh:
            raise ValueError("Gmail academics intake requires access token or refresh-token credentials.")
        daemon.connectors.append(
            GmailAcademicsConnector(
                token=google_api_token,
                refresh_token=google_refresh_token,
                client_id=google_client_id,
                client_secret=google_client_secret,
                token_endpoint=google_token_endpoint,
                query=gmail_query,
                max_results=gmail_max_results,
            )
        )
    if personal_context_path is not None:
        daemon.connectors.append(PersonalContextConnector(personal_context_path))
    if markets_signals_path is not None:
        daemon.connectors.append(MarketsSignalsConnector(markets_signals_path))
    if markets_positions_path is not None:
        daemon.connectors.append(MarketsPositionsConnector(markets_positions_path))
    if markets_calendar_path is not None:
        daemon.connectors.append(MarketsCalendarConnector(markets_calendar_path))
    if markets_outcomes_path is not None:
        daemon.connectors.append(MarketsOutcomesConnector(markets_outcomes_path))
    return runtime, daemon


def cmd_run_once(args: argparse.Namespace) -> None:
    google_api_token = _resolve_token(
        explicit_value=args.google_api_token,
        env_var_name=args.google_api_token_env,
    )
    google_refresh_token = _resolve_secret(
        explicit_value=args.google_refresh_token,
        env_var_name=args.google_refresh_token_env,
    )
    google_client_id = _resolve_secret(
        explicit_value=args.google_client_id,
        env_var_name=args.google_client_id_env,
    )
    google_client_secret = _resolve_secret(
        explicit_value=args.google_client_secret,
        env_var_name=args.google_client_secret_env,
    )
    runtime, daemon = build_daemon_with_optional_ci(
        args.repo_path.resolve(),
        args.db_path.resolve(),
        args.ci_reports_path.resolve() if args.ci_reports_path else None,
        args.academics_feed_path.resolve() if args.academics_feed_path else None,
        args.academics_calendar_path.resolve() if args.academics_calendar_path else None,
        args.academics_materials_path.resolve() if args.academics_materials_path else None,
        args.google_calendar_id,
        google_api_token,
        google_refresh_token,
        google_client_id,
        google_client_secret,
        args.google_token_endpoint,
        args.gmail_query,
        args.gmail_max_results,
        args.personal_context_path.resolve() if args.personal_context_path else None,
        args.markets_signals_path.resolve() if args.markets_signals_path else None,
        args.markets_positions_path.resolve() if args.markets_positions_path else None,
        args.markets_calendar_path.resolve() if args.markets_calendar_path else None,
        args.markets_outcomes_path.resolve() if args.markets_outcomes_path else None,
    )
    gateway_cfg = _configure_openclaw_gateway(runtime, args)
    if isinstance(gateway_cfg, dict) and gateway_cfg.get("enabled"):
        runtime.start_openclaw_gateway_loop()
    try:
        summary = daemon.run_once(dry_run=args.dry_run)
        print(json.dumps(summary, indent=2))
    finally:
        runtime.stop_openclaw_gateway_loop()
        daemon.close()
        runtime.close()


def cmd_watch(args: argparse.Namespace) -> None:
    google_api_token = _resolve_token(
        explicit_value=args.google_api_token,
        env_var_name=args.google_api_token_env,
    )
    google_refresh_token = _resolve_secret(
        explicit_value=args.google_refresh_token,
        env_var_name=args.google_refresh_token_env,
    )
    google_client_id = _resolve_secret(
        explicit_value=args.google_client_id,
        env_var_name=args.google_client_id_env,
    )
    google_client_secret = _resolve_secret(
        explicit_value=args.google_client_secret,
        env_var_name=args.google_client_secret_env,
    )
    runtime, daemon = build_daemon_with_optional_ci(
        args.repo_path.resolve(),
        args.db_path.resolve(),
        args.ci_reports_path.resolve() if args.ci_reports_path else None,
        args.academics_feed_path.resolve() if args.academics_feed_path else None,
        args.academics_calendar_path.resolve() if args.academics_calendar_path else None,
        args.academics_materials_path.resolve() if args.academics_materials_path else None,
        args.google_calendar_id,
        google_api_token,
        google_refresh_token,
        google_client_id,
        google_client_secret,
        args.google_token_endpoint,
        args.gmail_query,
        args.gmail_max_results,
        args.personal_context_path.resolve() if args.personal_context_path else None,
        args.markets_signals_path.resolve() if args.markets_signals_path else None,
        args.markets_positions_path.resolve() if args.markets_positions_path else None,
        args.markets_calendar_path.resolve() if args.markets_calendar_path else None,
        args.markets_outcomes_path.resolve() if args.markets_outcomes_path else None,
    )
    _configure_openclaw_gateway(runtime, args)
    try:
        summaries = daemon.run_forever(
            interval_seconds=args.interval,
            dry_run=args.dry_run,
            max_loops=args.max_loops,
        )
        for summary in summaries:
            print(json.dumps(summary, indent=2))
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped"}, indent=2))
    finally:
        daemon.close()
        runtime.close()


def cmd_approvals_list(args: argparse.Namespace) -> None:
    security = SecurityManager(args.db_path.resolve())
    inbox = ApprovalInbox(security)
    try:
        items = inbox.list(status=args.status)
        print(json.dumps({"count": len(items), "items": items}, indent=2))
    finally:
        security.close()


def cmd_approvals_show(args: argparse.Namespace) -> None:
    security = SecurityManager(args.db_path.resolve())
    inbox = ApprovalInbox(security)
    try:
        item = inbox.show(args.approval_id)
        if not item:
            print(json.dumps({"error": "approval_not_found", "approval_id": args.approval_id}, indent=2))
            return
        print(json.dumps(item, indent=2))
    finally:
        security.close()


def cmd_approvals_approve(args: argparse.Namespace) -> None:
    security = SecurityManager(args.db_path.resolve())
    inbox = ApprovalInbox(security)
    try:
        inbox.approve(args.approval_id, actor=args.actor)
        print(json.dumps({"approval_id": args.approval_id, "status": "approved"}, indent=2))
    finally:
        security.close()


def cmd_approvals_deny(args: argparse.Namespace) -> None:
    security = SecurityManager(args.db_path.resolve())
    inbox = ApprovalInbox(security)
    try:
        inbox.deny(args.approval_id, actor=args.actor)
        print(json.dumps({"approval_id": args.approval_id, "status": "denied"}, indent=2))
    finally:
        security.close()


def cmd_plans_preflight(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        prepared = runtime.preflight_plan(args.plan_id)
        print(json.dumps({"plan_id": args.plan_id, "prepared": prepared}, indent=2))
    finally:
        runtime.close()


def cmd_plans_execute_approved(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        receipt = runtime.execute_approved_step(args.plan_id, args.step_id)
        print(json.dumps(receipt, indent=2))
    finally:
        runtime.close()


def cmd_plans_publish_approved(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        receipt = runtime.publish_approved_step(
            args.plan_id,
            args.step_id,
            remote_name=args.remote_name,
            base_branch=args.base_branch,
            draft=not args.ready,
            force_with_lease=args.force_with_lease,
            open_review=args.open_review,
            provider=args.provider,
            provider_repo=args.provider_repo,
            reviewers=args.reviewer,
            labels=args.label,
        )
        print(json.dumps(receipt, indent=2))
    finally:
        runtime.close()


def cmd_plans_pr_payload(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        payload = runtime.get_pr_payload(args.plan_id, args.step_id)
        if not payload:
            print(json.dumps({"error": "pr_payload_not_found", "plan_id": args.plan_id, "step_id": args.step_id}, indent=2))
            return
        print(json.dumps(payload, indent=2))
    finally:
        runtime.close()


def cmd_plans_open_review(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        review = runtime.open_provider_review(
            args.plan_id,
            args.step_id,
            provider=args.provider,
            repo_slug=args.provider_repo,
            reviewers=args.reviewer,
            labels=args.label,
        )
        print(json.dumps(review, indent=2))
    finally:
        runtime.close()


def cmd_plans_review_artifact(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        review = runtime.get_provider_review(args.plan_id, args.step_id)
        if not review:
            print(json.dumps({"error": "provider_review_not_found", "plan_id": args.plan_id, "step_id": args.step_id}, indent=2))
            return
        print(json.dumps(review, indent=2))
    finally:
        runtime.close()


def cmd_plans_sync_review(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        review = runtime.sync_provider_review(args.plan_id, args.step_id)
        print(json.dumps(review, indent=2))
    finally:
        runtime.close()


def cmd_plans_sync_review_feedback(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        review = runtime.sync_review_feedback(args.repo_id, args.pr_number, args.branch)
        print(json.dumps(review, indent=2))
    finally:
        runtime.close()


def cmd_plans_configure_review(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        review = runtime.configure_provider_review(
            args.plan_id,
            args.step_id,
            reviewers=args.reviewer,
            labels=args.label,
            assignees=args.assignee,
        )
        print(json.dumps(review, indent=2))
    finally:
        runtime.close()


def cmd_plans_request_reviewers(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        review = runtime.configure_provider_review(
            args.plan_id,
            args.step_id,
            reviewers=args.reviewer,
            labels=None,
        )
        print(json.dumps(review, indent=2))
    finally:
        runtime.close()


def cmd_plans_set_labels(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        review = runtime.configure_provider_review(
            args.plan_id,
            args.step_id,
            reviewers=None,
            labels=args.label,
        )
        print(json.dumps(review, indent=2))
    finally:
        runtime.close()


def cmd_plans_review_summary(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        summary = runtime.get_review_summary(args.plan_id, args.step_id)
        print(json.dumps(summary, indent=2))
    finally:
        runtime.close()


def cmd_plans_review_comments(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        comments = runtime.get_review_comments(args.plan_id, args.step_id)
        print(json.dumps(comments, indent=2))
    finally:
        runtime.close()


def cmd_plans_evaluate_promotion(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        policy = runtime.evaluate_review_promotion_policy(
            args.plan_id,
            args.step_id,
            required_labels=args.required_label,
            allow_no_required_checks=args.allow_no_required_checks,
            single_maintainer_override=args.single_maintainer_override,
            override_actor=args.override_actor,
            override_reason=args.override_reason,
            override_sunset_condition=args.override_sunset_condition,
        )
        print(json.dumps(policy, indent=2))
    finally:
        runtime.close()


def cmd_plans_promote_ready(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        result = runtime.promote_provider_review_ready(
            args.plan_id,
            args.step_id,
            required_labels=args.required_label,
            allow_no_required_checks=args.allow_no_required_checks,
            single_maintainer_override=args.single_maintainer_override,
            override_actor=args.override_actor,
            override_reason=args.override_reason,
            override_sunset_condition=args.override_sunset_condition,
        )
        print(json.dumps(result, indent=2))
    finally:
        runtime.close()


def cmd_thoughts_recent(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        thoughts = runtime.list_recent_thoughts(limit=args.limit)
        print(json.dumps({"count": len(thoughts), "items": thoughts}, indent=2))
    finally:
        runtime.close()


def cmd_thoughts_show(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        thought = runtime.get_thought(args.thought_id)
        if not thought:
            print(json.dumps({"error": "thought_not_found", "thought_id": args.thought_id}, indent=2))
            return
        print(json.dumps(thought, indent=2))
    finally:
        runtime.close()


def cmd_thoughts_config(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        config = runtime.get_cognition_config()
        print(json.dumps(config, indent=2))
    finally:
        runtime.close()


def cmd_thoughts_evaluate(args: argparse.Namespace) -> None:
    result = compare_backends_on_snapshot(
        db_snapshot_path=args.snapshot_db_path.resolve(),
        repo_path=args.repo_path.resolve(),
        primary_backend=args.primary_backend,
        primary_model=args.primary_model or "",
        secondary_backend=args.secondary_backend,
        secondary_model=args.secondary_model or "",
        local_only=not args.allow_remote,
    )
    print(json.dumps(result, indent=2))


def cmd_synthesis_morning(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        artifact = runtime.generate_morning_synthesis() if args.generate else runtime.get_latest_synthesis("morning")
        print(json.dumps(artifact or {"error": "morning_synthesis_not_found"}, indent=2))
    finally:
        runtime.close()


def cmd_synthesis_evening(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        artifact = runtime.generate_evening_synthesis() if args.generate else runtime.get_latest_synthesis("evening")
        print(json.dumps(artifact or {"error": "evening_synthesis_not_found"}, indent=2))
    finally:
        runtime.close()


def cmd_interrupts_list(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        items = runtime.list_interrupts(status=args.status, limit=args.limit)
        print(json.dumps({"count": len(items), "items": items}, indent=2))
    finally:
        runtime.close()


def cmd_interrupts_ack(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        item = runtime.acknowledge_interrupt(args.interrupt_id, actor=args.actor)
        print(json.dumps(item, indent=2))
    finally:
        runtime.close()


def cmd_interrupts_snooze(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        item = runtime.snooze_interrupt(
            args.interrupt_id,
            minutes=args.minutes,
            actor=args.actor,
        )
        print(json.dumps(item, indent=2))
    finally:
        runtime.close()


def cmd_interrupts_suppress_until(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        updated = runtime.suppress_interrupts_until(
            until_iso=args.until_iso,
            reason=args.reason,
            actor=args.actor,
        )
        print(json.dumps(updated, indent=2))
    finally:
        runtime.close()


def cmd_interrupts_focus_mode(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        updated = runtime.set_focus_mode(domain=args.domain, actor=args.actor)
        print(json.dumps(updated, indent=2))
    finally:
        runtime.close()


def cmd_interrupts_quiet_hours(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        updated = runtime.set_quiet_hours(
            start_hour=args.start_hour,
            end_hour=args.end_hour,
            actor=args.actor,
        )
        print(json.dumps(updated, indent=2))
    finally:
        runtime.close()


def cmd_interrupts_preferences(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        payload = {
            "preferences": runtime.get_operator_preferences(),
            "events": runtime.list_operator_preference_events(limit=args.limit),
        }
        print(json.dumps(payload, indent=2))
    finally:
        runtime.close()


def cmd_academics_overview(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        overview = runtime.get_academics_overview(term_id=args.term_id)
        print(json.dumps(overview or {"error": "academics_overview_not_found"}, indent=2))
    finally:
        runtime.close()


def cmd_academics_risks(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        risks = runtime.list_academic_risks()
        print(json.dumps({"count": len(risks), "items": risks}, indent=2))
    finally:
        runtime.close()


def cmd_academics_schedule(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        schedule = runtime.get_academics_schedule_context(term_id=args.term_id)
        print(json.dumps(schedule or {"error": "academics_schedule_context_not_found"}, indent=2))
    finally:
        runtime.close()


def cmd_academics_windows(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        windows = runtime.get_academics_suppression_windows(term_id=args.term_id)
        print(json.dumps(windows or {"error": "academics_suppression_windows_not_found"}, indent=2))
    finally:
        runtime.close()


def cmd_markets_overview(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        payload = {
            "risk_posture": runtime.get_market_risk_posture(account_id=args.account_id),
            "opportunities": runtime.list_market_opportunities(limit=args.limit),
            "abstentions": runtime.list_market_abstentions(limit=args.limit),
            "events": runtime.list_market_events(limit=args.limit),
            "handoffs": runtime.list_market_handoffs(limit=args.limit),
            "outcomes": runtime.list_market_outcomes(limit=args.limit),
            "evaluation": runtime.summarize_market_outcomes(limit=max(args.limit, 60)),
            "risks": runtime.list_market_risks(),
        }
        print(json.dumps(payload, indent=2))
    finally:
        runtime.close()


def cmd_markets_opportunities(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        items = runtime.list_market_opportunities(limit=args.limit)
        print(json.dumps({"count": len(items), "items": items}, indent=2))
    finally:
        runtime.close()


def cmd_markets_abstentions(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        items = runtime.list_market_abstentions(limit=args.limit)
        print(json.dumps({"count": len(items), "items": items}, indent=2))
    finally:
        runtime.close()


def cmd_markets_posture(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        posture = runtime.get_market_risk_posture(account_id=args.account_id)
        print(json.dumps(posture or {"error": "market_risk_posture_not_found"}, indent=2))
    finally:
        runtime.close()


def cmd_markets_handoffs(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        items = runtime.list_market_handoffs(limit=args.limit)
        print(json.dumps({"count": len(items), "items": items}, indent=2))
    finally:
        runtime.close()


def cmd_markets_outcomes(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        items = runtime.list_market_outcomes(limit=args.limit)
        summary = runtime.summarize_market_outcomes(limit=max(args.limit, 60))
        print(json.dumps({"count": len(items), "items": items, "summary": summary}, indent=2))
    finally:
        runtime.close()


def cmd_identity_show(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        payload = {
            "user_model": runtime.get_user_model(),
            "personal_context": runtime.get_personal_context(),
            "latest_user_model_artifact": runtime.get_latest_user_model_artifact(),
            "latest_personal_context_artifact": runtime.get_latest_personal_context_artifact(),
            "events": runtime.list_identity_events(limit=args.limit),
        }
        print(json.dumps(payload, indent=2))
    finally:
        runtime.close()


def cmd_identity_set_domain_weight(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        model = runtime.set_domain_weight(
            domain=args.domain,
            weight=args.weight,
            actor=args.actor,
        )
        print(json.dumps(model, indent=2))
    finally:
        runtime.close()


def cmd_identity_set_goal(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        model = runtime.upsert_user_goal(
            goal_id=args.goal_id,
            label=args.label,
            priority=args.priority,
            weight=args.weight,
            domains=args.domain or [],
            actor=args.actor,
        )
        print(json.dumps(model, indent=2))
    finally:
        runtime.close()


def cmd_identity_update_context(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        context = runtime.update_personal_context(
            stress_level=args.stress_level,
            energy_level=args.energy_level,
            sleep_hours=args.sleep_hours,
            available_focus_minutes=args.focus_minutes,
            mode=args.mode,
            note=args.note,
            actor=args.actor,
        )
        print(json.dumps(context, indent=2))
    finally:
        runtime.close()


def cmd_archive_export(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        payload = runtime.export_daily_digest(day_key=args.day_key)
        print(json.dumps(payload, indent=2))
    finally:
        runtime.close()


def cmd_archive_list(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        items = runtime.list_digest_exports(limit=args.limit)
        print(json.dumps({"count": len(items), "items": items}, indent=2))
    finally:
        runtime.close()


def cmd_archive_show(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        item = runtime.get_digest_export(args.day_key)
        print(json.dumps(item or {"error": "digest_export_not_found", "day_key": args.day_key}, indent=2))
    finally:
        runtime.close()


def cmd_serve(args: argparse.Namespace) -> None:
    run_operator_server(
        repo_path=args.repo_path.resolve(),
        db_path=args.db_path.resolve(),
        host=args.host,
        port=args.port,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS bootstrap CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="Run end-to-end Zenith demo")
    demo.add_argument("--repo-path", type=Path, default=Path.cwd() / ".jarvis_demo_repo")
    demo.add_argument("--db-path", type=Path, default=_default_db_path())

    run_once = sub.add_parser("run-once", help="Poll connectors and execute one daemon cycle")
    run_once.add_argument("--repo-path", type=Path, default=_default_repo_path())
    run_once.add_argument("--db-path", type=Path, default=_default_db_path())
    run_once.add_argument("--ci-reports-path", type=Path, default=None)
    run_once.add_argument(
        "--academics-feed-path",
        type=Path,
        default=Path(os.getenv("JARVIS_ACADEMICS_FEED_PATH")) if os.getenv("JARVIS_ACADEMICS_FEED_PATH") else None,
    )
    run_once.add_argument(
        "--academics-calendar-path",
        type=Path,
        default=Path(os.getenv("JARVIS_ACADEMICS_CALENDAR_PATH")) if os.getenv("JARVIS_ACADEMICS_CALENDAR_PATH") else None,
    )
    run_once.add_argument(
        "--academics-materials-path",
        type=Path,
        default=Path(os.getenv("JARVIS_ACADEMICS_MATERIALS_PATH")) if os.getenv("JARVIS_ACADEMICS_MATERIALS_PATH") else None,
    )
    run_once.add_argument(
        "--google-calendar-id",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_CALENDAR_ID"),
    )
    run_once.add_argument(
        "--google-api-token",
        type=str,
        default=None,
        help="Google API bearer token (prefer env var usage).",
    )
    run_once.add_argument(
        "--google-api-token-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_API_TOKEN_ENV") or "JARVIS_GOOGLE_API_TOKEN",
        help="Env var name used to load Google API bearer token.",
    )
    run_once.add_argument(
        "--google-refresh-token",
        type=str,
        default=None,
        help="Google OAuth refresh token (optional, enables auto-refresh).",
    )
    run_once.add_argument(
        "--google-refresh-token-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_REFRESH_TOKEN_ENV") or "JARVIS_GOOGLE_REFRESH_TOKEN",
        help="Env var name used to load Google OAuth refresh token.",
    )
    run_once.add_argument(
        "--google-client-id",
        type=str,
        default=None,
        help="OAuth client_id for refresh-token exchange.",
    )
    run_once.add_argument(
        "--google-client-id-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_CLIENT_ID_ENV") or "JARVIS_GOOGLE_CLIENT_ID",
        help="Env var name used to load OAuth client_id.",
    )
    run_once.add_argument(
        "--google-client-secret",
        type=str,
        default=None,
        help="OAuth client_secret for refresh-token exchange.",
    )
    run_once.add_argument(
        "--google-client-secret-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_CLIENT_SECRET_ENV") or "JARVIS_GOOGLE_CLIENT_SECRET",
        help="Env var name used to load OAuth client_secret.",
    )
    run_once.add_argument(
        "--google-token-endpoint",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_TOKEN_ENDPOINT") or "https://oauth2.googleapis.com/token",
        help="OAuth token endpoint used for refresh-token exchange.",
    )
    run_once.add_argument(
        "--gmail-query",
        type=str,
        default=os.getenv("JARVIS_GMAIL_QUERY"),
        help="When set, enables Gmail academics intake using this Gmail search query.",
    )
    run_once.add_argument(
        "--gmail-max-results",
        type=int,
        default=_int_env("JARVIS_GMAIL_MAX_RESULTS", 50),
    )
    run_once.add_argument(
        "--personal-context-path",
        type=Path,
        default=Path(os.getenv("JARVIS_PERSONAL_CONTEXT_PATH")) if os.getenv("JARVIS_PERSONAL_CONTEXT_PATH") else None,
        help="Local JSON path with personal stress/energy/focus context snapshot.",
    )
    run_once.add_argument(
        "--markets-signals-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_SIGNALS_PATH")) if os.getenv("JARVIS_MARKETS_SIGNALS_PATH") else None,
        help="Local JSON path with markets signal feed snapshot.",
    )
    run_once.add_argument(
        "--markets-positions-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_POSITIONS_PATH")) if os.getenv("JARVIS_MARKETS_POSITIONS_PATH") else None,
        help="Local JSON path with markets positions/exposure snapshot.",
    )
    run_once.add_argument(
        "--markets-calendar-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_CALENDAR_PATH")) if os.getenv("JARVIS_MARKETS_CALENDAR_PATH") else None,
        help="Local JSON path with markets event/expiry calendar.",
    )
    run_once.add_argument(
        "--markets-outcomes-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_OUTCOMES_PATH")) if os.getenv("JARVIS_MARKETS_OUTCOMES_PATH") else None,
        help="Local JSON path with investing-bot handoff outcome receipts.",
    )
    run_once.add_argument(
        "--openclaw-gateway-ws-url",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_WS_URL"),
        help="OpenClaw Gateway websocket URL (ws:// or wss://).",
    )
    run_once.add_argument(
        "--openclaw-gateway-token-ref",
        type=str,
        default=None,
        help="SecretRef for gateway token (env:NAME or file:/abs/path).",
    )
    run_once.add_argument(
        "--openclaw-gateway-token-ref-env",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_TOKEN_REF_ENV") or "JARVIS_OPENCLAW_GATEWAY_TOKEN_REF",
        help="Env var name used to load OpenClaw gateway token SecretRef.",
    )
    run_once.add_argument(
        "--openclaw-gateway-owner-id",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_OWNER_ID") or "primary_operator",
    )
    run_once.add_argument(
        "--openclaw-gateway-client-name",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_CLIENT_NAME") or "jarvis",
    )
    run_once.add_argument(
        "--openclaw-gateway-profile-id",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_PROFILE_ID") or "openclaw_gateway_v2026_04_2",
    )
    run_once.add_argument(
        "--openclaw-gateway-profile-path",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_PROFILE_PATH"),
    )
    run_once.add_argument("--openclaw-gateway-enable", action="store_true")
    run_once.add_argument("--openclaw-gateway-allow-remote", action="store_true")
    run_once.add_argument("--openclaw-gateway-connect-timeout", type=float, default=8.0)
    run_once.add_argument("--openclaw-gateway-heartbeat", type=float, default=20.0)
    run_once.add_argument("--dry-run", action="store_true")

    watch = sub.add_parser("watch", help="Run the always-on daemon loop")
    watch.add_argument("--repo-path", type=Path, default=_default_repo_path())
    watch.add_argument("--db-path", type=Path, default=_default_db_path())
    watch.add_argument("--ci-reports-path", type=Path, default=None)
    watch.add_argument(
        "--academics-feed-path",
        type=Path,
        default=Path(os.getenv("JARVIS_ACADEMICS_FEED_PATH")) if os.getenv("JARVIS_ACADEMICS_FEED_PATH") else None,
    )
    watch.add_argument(
        "--academics-calendar-path",
        type=Path,
        default=Path(os.getenv("JARVIS_ACADEMICS_CALENDAR_PATH")) if os.getenv("JARVIS_ACADEMICS_CALENDAR_PATH") else None,
    )
    watch.add_argument(
        "--academics-materials-path",
        type=Path,
        default=Path(os.getenv("JARVIS_ACADEMICS_MATERIALS_PATH")) if os.getenv("JARVIS_ACADEMICS_MATERIALS_PATH") else None,
    )
    watch.add_argument(
        "--google-calendar-id",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_CALENDAR_ID"),
    )
    watch.add_argument(
        "--google-api-token",
        type=str,
        default=None,
        help="Google API bearer token (prefer env var usage).",
    )
    watch.add_argument(
        "--google-api-token-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_API_TOKEN_ENV") or "JARVIS_GOOGLE_API_TOKEN",
        help="Env var name used to load Google API bearer token.",
    )
    watch.add_argument(
        "--google-refresh-token",
        type=str,
        default=None,
        help="Google OAuth refresh token (optional, enables auto-refresh).",
    )
    watch.add_argument(
        "--google-refresh-token-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_REFRESH_TOKEN_ENV") or "JARVIS_GOOGLE_REFRESH_TOKEN",
        help="Env var name used to load Google OAuth refresh token.",
    )
    watch.add_argument(
        "--google-client-id",
        type=str,
        default=None,
        help="OAuth client_id for refresh-token exchange.",
    )
    watch.add_argument(
        "--google-client-id-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_CLIENT_ID_ENV") or "JARVIS_GOOGLE_CLIENT_ID",
        help="Env var name used to load OAuth client_id.",
    )
    watch.add_argument(
        "--google-client-secret",
        type=str,
        default=None,
        help="OAuth client_secret for refresh-token exchange.",
    )
    watch.add_argument(
        "--google-client-secret-env",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_CLIENT_SECRET_ENV") or "JARVIS_GOOGLE_CLIENT_SECRET",
        help="Env var name used to load OAuth client_secret.",
    )
    watch.add_argument(
        "--google-token-endpoint",
        type=str,
        default=os.getenv("JARVIS_GOOGLE_TOKEN_ENDPOINT") or "https://oauth2.googleapis.com/token",
        help="OAuth token endpoint used for refresh-token exchange.",
    )
    watch.add_argument(
        "--gmail-query",
        type=str,
        default=os.getenv("JARVIS_GMAIL_QUERY"),
        help="When set, enables Gmail academics intake using this Gmail search query.",
    )
    watch.add_argument(
        "--gmail-max-results",
        type=int,
        default=_int_env("JARVIS_GMAIL_MAX_RESULTS", 50),
    )
    watch.add_argument(
        "--personal-context-path",
        type=Path,
        default=Path(os.getenv("JARVIS_PERSONAL_CONTEXT_PATH")) if os.getenv("JARVIS_PERSONAL_CONTEXT_PATH") else None,
        help="Local JSON path with personal stress/energy/focus context snapshot.",
    )
    watch.add_argument(
        "--markets-signals-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_SIGNALS_PATH")) if os.getenv("JARVIS_MARKETS_SIGNALS_PATH") else None,
        help="Local JSON path with markets signal feed snapshot.",
    )
    watch.add_argument(
        "--markets-positions-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_POSITIONS_PATH")) if os.getenv("JARVIS_MARKETS_POSITIONS_PATH") else None,
        help="Local JSON path with markets positions/exposure snapshot.",
    )
    watch.add_argument(
        "--markets-calendar-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_CALENDAR_PATH")) if os.getenv("JARVIS_MARKETS_CALENDAR_PATH") else None,
        help="Local JSON path with markets event/expiry calendar.",
    )
    watch.add_argument(
        "--markets-outcomes-path",
        type=Path,
        default=Path(os.getenv("JARVIS_MARKETS_OUTCOMES_PATH")) if os.getenv("JARVIS_MARKETS_OUTCOMES_PATH") else None,
        help="Local JSON path with investing-bot handoff outcome receipts.",
    )
    watch.add_argument(
        "--openclaw-gateway-ws-url",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_WS_URL"),
        help="OpenClaw Gateway websocket URL (ws:// or wss://).",
    )
    watch.add_argument(
        "--openclaw-gateway-token-ref",
        type=str,
        default=None,
        help="SecretRef for gateway token (env:NAME or file:/abs/path).",
    )
    watch.add_argument(
        "--openclaw-gateway-token-ref-env",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_TOKEN_REF_ENV") or "JARVIS_OPENCLAW_GATEWAY_TOKEN_REF",
        help="Env var name used to load OpenClaw gateway token SecretRef.",
    )
    watch.add_argument(
        "--openclaw-gateway-owner-id",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_OWNER_ID") or "primary_operator",
    )
    watch.add_argument(
        "--openclaw-gateway-client-name",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_CLIENT_NAME") or "jarvis",
    )
    watch.add_argument(
        "--openclaw-gateway-profile-id",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_PROFILE_ID") or "openclaw_gateway_v2026_04_2",
    )
    watch.add_argument(
        "--openclaw-gateway-profile-path",
        type=str,
        default=os.getenv("JARVIS_OPENCLAW_GATEWAY_PROFILE_PATH"),
    )
    watch.add_argument("--openclaw-gateway-enable", action="store_true")
    watch.add_argument("--openclaw-gateway-allow-remote", action="store_true")
    watch.add_argument("--openclaw-gateway-connect-timeout", type=float, default=8.0)
    watch.add_argument("--openclaw-gateway-heartbeat", type=float, default=20.0)
    watch.add_argument("--dry-run", action="store_true")
    watch.add_argument("--interval", type=float, default=5.0)
    watch.add_argument("--max-loops", type=int, default=None)

    serve = sub.add_parser("serve", help="Run local operator API/dashboard server")
    serve.add_argument("--repo-path", type=Path, default=_default_repo_path())
    serve.add_argument("--db-path", type=Path, default=_default_db_path())
    serve.add_argument("--host", type=str, default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    thoughts = sub.add_parser("thoughts", help="Inspect persisted cognition thought artifacts")
    thoughts_sub = thoughts.add_subparsers(dest="thoughts_cmd", required=True)
    thoughts_recent = thoughts_sub.add_parser("recent", help="List recent thought artifacts")
    thoughts_recent.add_argument("--limit", type=int, default=20)
    thoughts_recent.add_argument("--repo-path", type=Path, default=_default_repo_path())
    thoughts_recent.add_argument("--db-path", type=Path, default=_default_db_path())
    thoughts_show = thoughts_sub.add_parser("show", help="Show one thought artifact")
    thoughts_show.add_argument("thought_id", type=str)
    thoughts_show.add_argument("--repo-path", type=Path, default=_default_repo_path())
    thoughts_show.add_argument("--db-path", type=Path, default=_default_db_path())
    thoughts_config = thoughts_sub.add_parser("config", help="Show resolved cognition backend configuration")
    thoughts_config.add_argument("--repo-path", type=Path, default=_default_repo_path())
    thoughts_config.add_argument("--db-path", type=Path, default=_default_db_path())
    thoughts_evaluate = thoughts_sub.add_parser(
        "evaluate",
        help="Compare cognition quality across two backends on the same DB snapshot",
    )
    thoughts_evaluate.add_argument(
        "--snapshot-db-path",
        type=Path,
        default=_default_db_path(),
    )
    thoughts_evaluate.add_argument("--repo-path", type=Path, default=_default_repo_path())
    thoughts_evaluate.add_argument("--primary-backend", type=str, default="heuristic")
    thoughts_evaluate.add_argument("--primary-model", type=str, default="")
    thoughts_evaluate.add_argument("--secondary-backend", type=str, default="ollama")
    thoughts_evaluate.add_argument("--secondary-model", type=str, default="")
    thoughts_evaluate.add_argument("--allow-remote", action="store_true")

    synthesis = sub.add_parser("synthesis", help="Generate or inspect daily synthesis artifacts")
    synthesis_sub = synthesis.add_subparsers(dest="synthesis_cmd", required=True)
    synthesis_morning = synthesis_sub.add_parser("morning", help="Morning synthesis")
    synthesis_morning.add_argument("--generate", action="store_true")
    synthesis_morning.add_argument("--repo-path", type=Path, default=_default_repo_path())
    synthesis_morning.add_argument("--db-path", type=Path, default=_default_db_path())
    synthesis_evening = synthesis_sub.add_parser("evening", help="Evening synthesis")
    synthesis_evening.add_argument("--generate", action="store_true")
    synthesis_evening.add_argument("--repo-path", type=Path, default=_default_repo_path())
    synthesis_evening.add_argument("--db-path", type=Path, default=_default_db_path())

    interrupts = sub.add_parser("interrupts", help="Interrupt decision inbox")
    interrupts_sub = interrupts.add_subparsers(dest="interrupts_cmd", required=True)
    interrupts_list = interrupts_sub.add_parser("list", help="List interrupt decisions")
    interrupts_list.add_argument("--status", type=str, default="all")
    interrupts_list.add_argument("--limit", type=int, default=50)
    interrupts_list.add_argument("--repo-path", type=Path, default=_default_repo_path())
    interrupts_list.add_argument("--db-path", type=Path, default=_default_db_path())
    interrupts_ack = interrupts_sub.add_parser("acknowledge", help="Acknowledge an interrupt decision")
    interrupts_ack.add_argument("interrupt_id", type=str)
    interrupts_ack.add_argument("--actor", type=str, default="user")
    interrupts_ack.add_argument("--repo-path", type=Path, default=_default_repo_path())
    interrupts_ack.add_argument("--db-path", type=Path, default=_default_db_path())
    interrupts_snooze = interrupts_sub.add_parser("snooze", help="Snooze an interrupt decision")
    interrupts_snooze.add_argument("interrupt_id", type=str)
    interrupts_snooze.add_argument("--minutes", type=int, default=60)
    interrupts_snooze.add_argument("--actor", type=str, default="user")
    interrupts_snooze.add_argument("--repo-path", type=Path, default=_default_repo_path())
    interrupts_snooze.add_argument("--db-path", type=Path, default=_default_db_path())
    interrupts_suppress = interrupts_sub.add_parser(
        "suppress-until",
        help="Set a manual interruption suppression-until timestamp (ISO8601)",
    )
    interrupts_suppress.add_argument("--until-iso", type=str, default=None)
    interrupts_suppress.add_argument("--reason", type=str, default="")
    interrupts_suppress.add_argument("--actor", type=str, default="user")
    interrupts_suppress.add_argument("--repo-path", type=Path, default=_default_repo_path())
    interrupts_suppress.add_argument("--db-path", type=Path, default=_default_db_path())
    interrupts_focus = interrupts_sub.add_parser(
        "focus-mode",
        help="Set active focus mode domain (academics|zenith|off)",
    )
    interrupts_focus.add_argument("--domain", type=str, default="off")
    interrupts_focus.add_argument("--actor", type=str, default="user")
    interrupts_focus.add_argument("--repo-path", type=Path, default=_default_repo_path())
    interrupts_focus.add_argument("--db-path", type=Path, default=_default_db_path())
    interrupts_quiet = interrupts_sub.add_parser(
        "quiet-hours",
        help="Set quiet hours using local-hour integers (0-23). Pass no args to clear.",
    )
    interrupts_quiet.add_argument("--start-hour", type=int, default=None)
    interrupts_quiet.add_argument("--end-hour", type=int, default=None)
    interrupts_quiet.add_argument("--actor", type=str, default="user")
    interrupts_quiet.add_argument("--repo-path", type=Path, default=_default_repo_path())
    interrupts_quiet.add_argument("--db-path", type=Path, default=_default_db_path())
    interrupts_prefs = interrupts_sub.add_parser(
        "preferences",
        help="Show interruption governance preferences and recent preference events",
    )
    interrupts_prefs.add_argument("--limit", type=int, default=30)
    interrupts_prefs.add_argument("--repo-path", type=Path, default=_default_repo_path())
    interrupts_prefs.add_argument("--db-path", type=Path, default=_default_db_path())

    academics = sub.add_parser("academics", help="Academics domain state surfaces")
    academics_sub = academics.add_subparsers(dest="academics_cmd", required=True)
    academics_overview = academics_sub.add_parser("overview", help="Show latest academics overview artifact")
    academics_overview.add_argument("--term-id", type=str, default="current_term")
    academics_overview.add_argument("--repo-path", type=Path, default=_default_repo_path())
    academics_overview.add_argument("--db-path", type=Path, default=_default_db_path())
    academics_risks = academics_sub.add_parser("risks", help="List active academics risks")
    academics_risks.add_argument("--repo-path", type=Path, default=_default_repo_path())
    academics_risks.add_argument("--db-path", type=Path, default=_default_db_path())
    academics_schedule = academics_sub.add_parser("schedule", help="Show latest academics schedule context")
    academics_schedule.add_argument("--term-id", type=str, default="current_term")
    academics_schedule.add_argument("--repo-path", type=Path, default=_default_repo_path())
    academics_schedule.add_argument("--db-path", type=Path, default=_default_db_path())
    academics_windows = academics_sub.add_parser(
        "windows",
        help="Show active suppression-window context for academics",
    )
    academics_windows.add_argument("--term-id", type=str, default="current_term")
    academics_windows.add_argument("--repo-path", type=Path, default=_default_repo_path())
    academics_windows.add_argument("--db-path", type=Path, default=_default_db_path())

    markets = sub.add_parser("markets", help="Markets domain state surfaces")
    markets_sub = markets.add_subparsers(dest="markets_cmd", required=True)
    markets_overview = markets_sub.add_parser("overview", help="Show latest markets opportunities, abstentions, events, handoffs, outcomes, and posture")
    markets_overview.add_argument("--account-id", type=str, default="default")
    markets_overview.add_argument("--limit", type=int, default=20)
    markets_overview.add_argument("--repo-path", type=Path, default=_default_repo_path())
    markets_overview.add_argument("--db-path", type=Path, default=_default_db_path())
    markets_opportunities = markets_sub.add_parser("opportunities", help="List market opportunity artifacts")
    markets_opportunities.add_argument("--limit", type=int, default=20)
    markets_opportunities.add_argument("--repo-path", type=Path, default=_default_repo_path())
    markets_opportunities.add_argument("--db-path", type=Path, default=_default_db_path())
    markets_abstentions = markets_sub.add_parser("abstentions", help="List market abstention artifacts")
    markets_abstentions.add_argument("--limit", type=int, default=20)
    markets_abstentions.add_argument("--repo-path", type=Path, default=_default_repo_path())
    markets_abstentions.add_argument("--db-path", type=Path, default=_default_db_path())
    markets_posture = markets_sub.add_parser("posture", help="Show latest market risk-posture artifact")
    markets_posture.add_argument("--account-id", type=str, default="default")
    markets_posture.add_argument("--repo-path", type=Path, default=_default_repo_path())
    markets_posture.add_argument("--db-path", type=Path, default=_default_db_path())
    markets_handoffs = markets_sub.add_parser("handoffs", help="List market handoff artifacts prepared for external bot evaluation")
    markets_handoffs.add_argument("--limit", type=int, default=20)
    markets_handoffs.add_argument("--repo-path", type=Path, default=_default_repo_path())
    markets_handoffs.add_argument("--db-path", type=Path, default=_default_db_path())
    markets_outcomes = markets_sub.add_parser("outcomes", help="List market handoff outcomes and aggregate status summary")
    markets_outcomes.add_argument("--limit", type=int, default=20)
    markets_outcomes.add_argument("--repo-path", type=Path, default=_default_repo_path())
    markets_outcomes.add_argument("--db-path", type=Path, default=_default_db_path())

    identity = sub.add_parser("identity", help="Identity model and personal-context controls")
    identity_sub = identity.add_subparsers(dest="identity_cmd", required=True)
    identity_show = identity_sub.add_parser("show", help="Show user model, personal context, and identity events")
    identity_show.add_argument("--limit", type=int, default=30)
    identity_show.add_argument("--repo-path", type=Path, default=_default_repo_path())
    identity_show.add_argument("--db-path", type=Path, default=_default_db_path())
    identity_weight = identity_sub.add_parser("set-domain-weight", help="Set domain weight in goal hierarchy")
    identity_weight.add_argument("--domain", type=str, required=True)
    identity_weight.add_argument("--weight", type=float, required=True)
    identity_weight.add_argument("--actor", type=str, default="user")
    identity_weight.add_argument("--repo-path", type=Path, default=_default_repo_path())
    identity_weight.add_argument("--db-path", type=Path, default=_default_db_path())
    identity_goal = identity_sub.add_parser("set-goal", help="Upsert one explicit goal entry")
    identity_goal.add_argument("--goal-id", type=str, required=True)
    identity_goal.add_argument("--label", type=str, required=True)
    identity_goal.add_argument("--priority", type=int, default=10)
    identity_goal.add_argument("--weight", type=float, default=1.0)
    identity_goal.add_argument("--domain", action="append", default=[])
    identity_goal.add_argument("--actor", type=str, default="user")
    identity_goal.add_argument("--repo-path", type=Path, default=_default_repo_path())
    identity_goal.add_argument("--db-path", type=Path, default=_default_db_path())
    identity_context = identity_sub.add_parser(
        "update-context",
        help="Update personal context signal (stress/energy/sleep/focus/mode)",
    )
    identity_context.add_argument("--stress-level", type=float, default=None)
    identity_context.add_argument("--energy-level", type=float, default=None)
    identity_context.add_argument("--sleep-hours", type=float, default=None)
    identity_context.add_argument("--focus-minutes", type=int, default=None)
    identity_context.add_argument("--mode", type=str, default=None)
    identity_context.add_argument("--note", type=str, default=None)
    identity_context.add_argument("--actor", type=str, default="user")
    identity_context.add_argument("--repo-path", type=Path, default=_default_repo_path())
    identity_context.add_argument("--db-path", type=Path, default=_default_db_path())

    archive = sub.add_parser("archive", help="Daily digest export/archive surfaces")
    archive_sub = archive.add_subparsers(dest="archive_cmd", required=True)
    archive_export = archive_sub.add_parser("export", help="Export digest for today or a specific day")
    archive_export.add_argument("--day-key", type=str, default=None)
    archive_export.add_argument("--repo-path", type=Path, default=_default_repo_path())
    archive_export.add_argument("--db-path", type=Path, default=_default_db_path())
    archive_list = archive_sub.add_parser("list", help="List indexed digest exports")
    archive_list.add_argument("--limit", type=int, default=30)
    archive_list.add_argument("--repo-path", type=Path, default=_default_repo_path())
    archive_list.add_argument("--db-path", type=Path, default=_default_db_path())
    archive_show = archive_sub.add_parser("show", help="Show one digest export metadata")
    archive_show.add_argument("day_key", type=str)
    archive_show.add_argument("--repo-path", type=Path, default=_default_repo_path())
    archive_show.add_argument("--db-path", type=Path, default=_default_db_path())

    approvals = sub.add_parser("approvals", help="Approval inbox commands")
    approvals_sub = approvals.add_subparsers(dest="approvals_cmd", required=True)

    approvals_list = approvals_sub.add_parser("list", help="List approvals")
    approvals_list.add_argument("--db-path", type=Path, default=_default_db_path())
    approvals_list.add_argument(
        "--status",
        type=str,
        default="pending",
        choices=["pending", "approved", "denied", "all"],
    )

    approvals_show = approvals_sub.add_parser("show", help="Show approval details and evidence packet")
    approvals_show.add_argument("approval_id", type=str)
    approvals_show.add_argument("--db-path", type=Path, default=_default_db_path())

    approvals_approve = approvals_sub.add_parser("approve", help="Approve an action")
    approvals_approve.add_argument("approval_id", type=str)
    approvals_approve.add_argument("--db-path", type=Path, default=_default_db_path())
    approvals_approve.add_argument("--actor", type=str, default="user")

    approvals_deny = approvals_sub.add_parser("deny", help="Deny an action")
    approvals_deny.add_argument("approval_id", type=str)
    approvals_deny.add_argument("--db-path", type=Path, default=_default_db_path())
    approvals_deny.add_argument("--actor", type=str, default="user")

    plans = sub.add_parser("plans", help="Plan preparation/execution commands")
    plans_sub = plans.add_subparsers(dest="plans_cmd", required=True)

    plans_preflight = plans_sub.add_parser("preflight", help="Prepare protected steps for approval")
    plans_preflight.add_argument("plan_id", type=str)
    plans_preflight.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_preflight.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_execute = plans_sub.add_parser(
        "execute-approved",
        help="Execute an approved protected step in prepared sandbox context",
    )
    plans_execute.add_argument("plan_id", type=str)
    plans_execute.add_argument("step_id", type=str)
    plans_execute.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_execute.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_publish = plans_sub.add_parser(
        "publish-approved",
        help="Commit the prepared sandbox, push a review branch, and generate PR payload",
    )
    plans_publish.add_argument("plan_id", type=str)
    plans_publish.add_argument("step_id", type=str)
    plans_publish.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_publish.add_argument("--db-path", type=Path, default=_default_db_path())
    plans_publish.add_argument("--remote-name", type=str, default="origin")
    plans_publish.add_argument("--base-branch", type=str, default=None)
    plans_publish.add_argument("--force-with-lease", action="store_true")
    plans_publish.add_argument("--ready", action="store_true", help="Mark generated PR payload as ready, not draft")
    plans_publish.add_argument("--open-review", action="store_true", help="Open a provider-native review after publishing")
    plans_publish.add_argument("--provider", type=str, default=None)
    plans_publish.add_argument("--provider-repo", type=str, default=None)
    plans_publish.add_argument("--reviewer", action="append", default=[])
    plans_publish.add_argument("--label", action="append", default=[])

    plans_pr = plans_sub.add_parser(
        "pr-payload",
        help="Show the generated PR payload for a published approved step",
    )
    plans_pr.add_argument("plan_id", type=str)
    plans_pr.add_argument("step_id", type=str)
    plans_pr.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_pr.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_open_review = plans_sub.add_parser(
        "open-review",
        help="Create a provider-native review artifact from a published approved step",
    )
    plans_open_review.add_argument("plan_id", type=str)
    plans_open_review.add_argument("step_id", type=str)
    plans_open_review.add_argument("--provider", type=str, required=True)
    plans_open_review.add_argument("--provider-repo", type=str, required=True)
    plans_open_review.add_argument("--reviewer", action="append", default=[])
    plans_open_review.add_argument("--label", action="append", default=[])
    plans_open_review.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_open_review.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_review_artifact = plans_sub.add_parser(
        "review-artifact",
        help="Show the stored provider review artifact for a plan step",
    )
    plans_review_artifact.add_argument("plan_id", type=str)
    plans_review_artifact.add_argument("step_id", type=str)
    plans_review_artifact.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_review_artifact.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_sync_review = plans_sub.add_parser(
        "sync-review",
        help="Refresh provider review state and checks back into runtime state",
    )
    plans_sync_review.add_argument("plan_id", type=str)
    plans_sync_review.add_argument("step_id", type=str)
    plans_sync_review.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_sync_review.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_sync_review_feedback = plans_sub.add_parser(
        "sync-review-feedback",
        help="Sync hosted review feedback using repo_id + pr_number + branch",
    )
    plans_sync_review_feedback.add_argument("repo_id", type=str)
    plans_sync_review_feedback.add_argument("pr_number", type=str)
    plans_sync_review_feedback.add_argument("branch", type=str)
    plans_sync_review_feedback.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_sync_review_feedback.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_configure_review = plans_sub.add_parser(
        "configure-review",
        help="Normalize requested reviewers and labels on an existing provider review",
    )
    plans_configure_review.add_argument("plan_id", type=str)
    plans_configure_review.add_argument("step_id", type=str)
    plans_configure_review.add_argument("--reviewer", action="append", default=None)
    plans_configure_review.add_argument("--label", action="append", default=None)
    plans_configure_review.add_argument("--assignee", action="append", default=None)
    plans_configure_review.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_configure_review.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_request_reviewers = plans_sub.add_parser(
        "request-reviewers",
        help="Set requested reviewers on an existing provider review",
    )
    plans_request_reviewers.add_argument("plan_id", type=str)
    plans_request_reviewers.add_argument("step_id", type=str)
    plans_request_reviewers.add_argument("--reviewer", action="append", default=[])
    plans_request_reviewers.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_request_reviewers.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_set_labels = plans_sub.add_parser(
        "set-labels",
        help="Set labels on an existing provider review",
    )
    plans_set_labels.add_argument("plan_id", type=str)
    plans_set_labels.add_argument("step_id", type=str)
    plans_set_labels.add_argument("--label", action="append", default=[])
    plans_set_labels.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_set_labels.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_review_summary = plans_sub.add_parser(
        "review-summary",
        help="Show hosted review summary with approval evidence for this plan step",
    )
    plans_review_summary.add_argument("plan_id", type=str)
    plans_review_summary.add_argument("step_id", type=str)
    plans_review_summary.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_review_summary.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_review_comments = plans_sub.add_parser(
        "review-comments",
        help="Show hosted issue/review comments for this plan step",
    )
    plans_review_comments.add_argument("plan_id", type=str)
    plans_review_comments.add_argument("step_id", type=str)
    plans_review_comments.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_review_comments.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_eval_promotion = plans_sub.add_parser(
        "evaluate-promotion",
        help="Evaluate draft-to-ready promotion policy for a provider review",
    )
    plans_eval_promotion.add_argument("plan_id", type=str)
    plans_eval_promotion.add_argument("step_id", type=str)
    plans_eval_promotion.add_argument("--required-label", action="append", default=None)
    plans_eval_promotion.add_argument("--allow-no-required-checks", action="store_true")
    plans_eval_promotion.add_argument("--single-maintainer-override", action="store_true")
    plans_eval_promotion.add_argument("--override-actor", type=str, default=None)
    plans_eval_promotion.add_argument("--override-reason", type=str, default=None)
    plans_eval_promotion.add_argument("--override-sunset-condition", type=str, default=None)
    plans_eval_promotion.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_eval_promotion.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_promote_ready = plans_sub.add_parser(
        "promote-ready",
        help="Promote a draft provider review to ready for review when policy gates pass",
    )
    plans_promote_ready.add_argument("plan_id", type=str)
    plans_promote_ready.add_argument("step_id", type=str)
    plans_promote_ready.add_argument("--required-label", action="append", default=None)
    plans_promote_ready.add_argument("--allow-no-required-checks", action="store_true")
    plans_promote_ready.add_argument("--single-maintainer-override", action="store_true")
    plans_promote_ready.add_argument("--override-actor", type=str, default=None)
    plans_promote_ready.add_argument("--override-reason", type=str, default=None)
    plans_promote_ready.add_argument("--override-sunset-condition", type=str, default=None)
    plans_promote_ready.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_promote_ready.add_argument("--db-path", type=Path, default=_default_db_path())

    args = parser.parse_args()

    if args.cmd == "demo":
        result = run_demo(repo_path=args.repo_path.resolve(), db_path=args.db_path.resolve())
        print(json.dumps(result, indent=2))
        return
    if args.cmd == "run-once":
        cmd_run_once(args)
        return
    if args.cmd == "watch":
        cmd_watch(args)
        return
    if args.cmd == "serve":
        cmd_serve(args)
        return
    if args.cmd == "thoughts" and args.thoughts_cmd == "recent":
        cmd_thoughts_recent(args)
        return
    if args.cmd == "thoughts" and args.thoughts_cmd == "show":
        cmd_thoughts_show(args)
        return
    if args.cmd == "thoughts" and args.thoughts_cmd == "config":
        cmd_thoughts_config(args)
        return
    if args.cmd == "thoughts" and args.thoughts_cmd == "evaluate":
        cmd_thoughts_evaluate(args)
        return
    if args.cmd == "synthesis" and args.synthesis_cmd == "morning":
        cmd_synthesis_morning(args)
        return
    if args.cmd == "synthesis" and args.synthesis_cmd == "evening":
        cmd_synthesis_evening(args)
        return
    if args.cmd == "interrupts" and args.interrupts_cmd == "list":
        cmd_interrupts_list(args)
        return
    if args.cmd == "interrupts" and args.interrupts_cmd == "acknowledge":
        cmd_interrupts_ack(args)
        return
    if args.cmd == "interrupts" and args.interrupts_cmd == "snooze":
        cmd_interrupts_snooze(args)
        return
    if args.cmd == "interrupts" and args.interrupts_cmd == "suppress-until":
        cmd_interrupts_suppress_until(args)
        return
    if args.cmd == "interrupts" and args.interrupts_cmd == "focus-mode":
        cmd_interrupts_focus_mode(args)
        return
    if args.cmd == "interrupts" and args.interrupts_cmd == "quiet-hours":
        cmd_interrupts_quiet_hours(args)
        return
    if args.cmd == "interrupts" and args.interrupts_cmd == "preferences":
        cmd_interrupts_preferences(args)
        return
    if args.cmd == "academics" and args.academics_cmd == "overview":
        cmd_academics_overview(args)
        return
    if args.cmd == "academics" and args.academics_cmd == "risks":
        cmd_academics_risks(args)
        return
    if args.cmd == "academics" and args.academics_cmd == "schedule":
        cmd_academics_schedule(args)
        return
    if args.cmd == "academics" and args.academics_cmd == "windows":
        cmd_academics_windows(args)
        return
    if args.cmd == "markets" and args.markets_cmd == "overview":
        cmd_markets_overview(args)
        return
    if args.cmd == "markets" and args.markets_cmd == "opportunities":
        cmd_markets_opportunities(args)
        return
    if args.cmd == "markets" and args.markets_cmd == "abstentions":
        cmd_markets_abstentions(args)
        return
    if args.cmd == "markets" and args.markets_cmd == "posture":
        cmd_markets_posture(args)
        return
    if args.cmd == "markets" and args.markets_cmd == "handoffs":
        cmd_markets_handoffs(args)
        return
    if args.cmd == "markets" and args.markets_cmd == "outcomes":
        cmd_markets_outcomes(args)
        return
    if args.cmd == "identity" and args.identity_cmd == "show":
        cmd_identity_show(args)
        return
    if args.cmd == "identity" and args.identity_cmd == "set-domain-weight":
        cmd_identity_set_domain_weight(args)
        return
    if args.cmd == "identity" and args.identity_cmd == "set-goal":
        cmd_identity_set_goal(args)
        return
    if args.cmd == "identity" and args.identity_cmd == "update-context":
        cmd_identity_update_context(args)
        return
    if args.cmd == "archive" and args.archive_cmd == "export":
        cmd_archive_export(args)
        return
    if args.cmd == "archive" and args.archive_cmd == "list":
        cmd_archive_list(args)
        return
    if args.cmd == "archive" and args.archive_cmd == "show":
        cmd_archive_show(args)
        return
    if args.cmd == "approvals" and args.approvals_cmd == "list":
        cmd_approvals_list(args)
        return
    if args.cmd == "approvals" and args.approvals_cmd == "show":
        cmd_approvals_show(args)
        return
    if args.cmd == "approvals" and args.approvals_cmd == "approve":
        cmd_approvals_approve(args)
        return
    if args.cmd == "approvals" and args.approvals_cmd == "deny":
        cmd_approvals_deny(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "preflight":
        cmd_plans_preflight(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "execute-approved":
        cmd_plans_execute_approved(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "publish-approved":
        cmd_plans_publish_approved(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "pr-payload":
        cmd_plans_pr_payload(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "open-review":
        cmd_plans_open_review(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "review-artifact":
        cmd_plans_review_artifact(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "sync-review":
        cmd_plans_sync_review(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "sync-review-feedback":
        cmd_plans_sync_review_feedback(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "configure-review":
        cmd_plans_configure_review(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "request-reviewers":
        cmd_plans_request_reviewers(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "set-labels":
        cmd_plans_set_labels(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "review-summary":
        cmd_plans_review_summary(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "review-comments":
        cmd_plans_review_comments(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "evaluate-promotion":
        cmd_plans_evaluate_promotion(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "promote-ready":
        cmd_plans_promote_ready(args)
        return

    raise ValueError("Unsupported command")


if __name__ == "__main__":
    main()
