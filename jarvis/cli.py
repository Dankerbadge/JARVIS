from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
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
from .improvement import FeedbackFeedPuller, FeedbackFileConnector, FrictionMiningStore, FrictionSourceAdapter
from .interrupts import InterruptDecision
from .models import new_id, utc_now_iso
from .reactors import ZenithCorrelationReactor, ZenithGitDeltaReactor, ZenithRiskReactor
from .runtime import JarvisRuntime
from .security import ActionClass, SecurityManager
from .server import run_operator_server


PROJECT_BACKFILL_PRESETS: dict[str, dict[str, Any]] = {
    "quick": {
        "limit": 50,
        "include_outcomes": True,
        "include_review_artifacts": False,
        "include_merge_outcomes": False,
        "skip_seen": True,
        "load_since_from_cursor_profile": True,
        "top_signal_types": 5,
        "include_raw_signals": False,
        "include_raw_ingestions": False,
    },
    "balanced": {
        "limit": 100,
        "include_outcomes": True,
        "include_review_artifacts": True,
        "include_merge_outcomes": True,
        "skip_seen": True,
        "load_since_from_cursor_profile": True,
        "top_signal_types": 5,
        "include_raw_signals": False,
        "include_raw_ingestions": False,
    },
    "deep": {
        "limit": 300,
        "include_outcomes": True,
        "include_review_artifacts": True,
        "include_merge_outcomes": True,
        "skip_seen": True,
        "load_since_from_cursor_profile": True,
        "top_signal_types": 10,
        "include_raw_signals": False,
        "include_raw_ingestions": False,
    },
}

WARNING_SEVERITY_ORDER: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "error": 2,
}

WARNING_POLICY_PROFILES: dict[str, dict[str, Any]] = {
    "default": {
        "min_warning_severity": "info",
        "exit_code_policy": "off",
        "warning_exit_code": 2,
        "error_exit_code": 3,
        "suppress_warning_codes": [],
    },
    "strict": {
        "min_warning_severity": "warning",
        "exit_code_policy": "warning",
        "warning_exit_code": 2,
        "error_exit_code": 3,
        "suppress_warning_codes": [],
    },
    "quiet": {
        "min_warning_severity": "warning",
        "exit_code_policy": "off",
        "warning_exit_code": 2,
        "error_exit_code": 3,
        "suppress_warning_codes": [
            "source_counts_capped",
            "signal_type_counts_capped",
        ],
    },
}

DEFAULT_FITNESS_APP_FIELDS_CSV = (
    "app_name,app,product,provider,source_context.app_identifier,source_context.app_name,source_context.app,source_context,source"
)
DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV = "quant_finance,kalshi_weather,fitness_apps,market_ml"
DEFAULT_EVIDENCE_LOOKUP_ID_FIELDS_CSV = (
    "id,record_id,review_id,ticket_id,source_context.id,source_context.record_id,source_context.review_id"
)
DEFAULT_EVIDENCE_LOOKUP_SUMMARY_FIELDS_CSV = (
    "summary,review,text,content,complaint,message,body,title,headline,subject"
)
DEFAULT_EVIDENCE_LOOKUP_TIMESTAMP_FIELDS_CSV = (
    "created_at,at,submission_date,date,timestamp,occurred_at,updated_at,source_context.created_at"
)
DEFAULT_EVIDENCE_LOOKUP_CONTEXT_FIELDS_CSV = (
    "url,rating,score,severity,app_version,platform,app_name,app,provider,"
    "source_context.app_identifier,source_context.app_label,source_context.app_name,source_context.platform"
)


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


def _parse_csv_items(raw_value: str | None) -> list[str]:
    raw = str(raw_value or "").strip()
    if not raw:
        return []
    normalized = raw.replace("\n", ",").replace("\t", ",").replace(";", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _coerce_warning_code_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _parse_csv_items(value)
    if isinstance(value, (list, tuple, set)):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
    return []


def _load_warning_policy_config(path_value: Any) -> tuple[dict[str, Any], str | None]:
    if path_value is None:
        return {}, None
    raw_path = str(path_value).strip()
    if not raw_path:
        return {}, None
    path = Path(raw_path).expanduser()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed_to_load_warning_policy_config:{path}:{exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"invalid_warning_policy_config_type:{path}:expected_json_object")
    resolved_path = str(path.resolve())
    return dict(loaded), resolved_path


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


def _resolve_project_backfill_options(args: argparse.Namespace) -> dict[str, Any]:
    preset_name = str(getattr(args, "preset", "balanced") or "balanced").strip().lower()
    preset = dict(PROJECT_BACKFILL_PRESETS.get(preset_name) or PROJECT_BACKFILL_PRESETS["balanced"])
    warning_policy_config_source: str | None = None
    warning_policy_config_input = getattr(args, "warning_policy_config", None)
    if warning_policy_config_input is not None and str(warning_policy_config_input).strip():
        warning_policy_config_source = "explicit"
    else:
        repo_path_value = getattr(args, "repo_path", None)
        if repo_path_value is not None:
            repo_path = Path(str(repo_path_value)).expanduser()
            candidates = [
                repo_path / ".jarvis" / "backfill.warning_policy.json",
                repo_path / ".jarvis" / "backfill_warning_policy.json",
            ]
            for candidate in candidates:
                if candidate.exists():
                    warning_policy_config_input = candidate
                    warning_policy_config_source = "repo_default"
                    break
    warning_policy_config, warning_policy_config_path = _load_warning_policy_config(
        warning_policy_config_input
    )
    warning_policy_resolution_fallbacks: list[dict[str, Any]] = []
    warning_policy_profile_source = "profile_default"
    warning_policy_profile = str(getattr(args, "warning_policy_profile", None) or "").strip().lower()
    if warning_policy_profile:
        warning_policy_profile_source = "explicit"
    else:
        config_profile = str(warning_policy_config.get("warning_policy_profile") or "").strip().lower()
        if config_profile:
            warning_policy_profile = config_profile
            warning_policy_profile_source = "config"
        else:
            env_profile = str(os.getenv("JARVIS_BACKFILL_WARNING_POLICY_PROFILE") or "").strip().lower()
            if env_profile:
                warning_policy_profile = env_profile
                warning_policy_profile_source = "env"
            else:
                warning_policy_profile = "default"
                warning_policy_profile_source = "profile_default"
    if warning_policy_profile not in WARNING_POLICY_PROFILES:
        warning_policy_resolution_fallbacks.append(
            {
                "field": "warning_policy_profile",
                "invalid_value": warning_policy_profile,
                "source": warning_policy_profile_source,
                "fallback_value": "default",
                "fallback_source": "profile_default",
            }
        )
        warning_policy_profile = "default"
        warning_policy_profile_source = "profile_default"
    warning_profile_defaults = dict(WARNING_POLICY_PROFILES[warning_policy_profile])

    def _pick(name: str) -> Any:
        value = getattr(args, name, None)
        if value is None:
            return preset.get(name)
        return value

    max_source_counts_value = _pick("max_source_counts")
    max_signal_type_counts_value = _pick("max_signal_type_counts")
    min_warning_severity_source = "profile_default"
    min_warning_severity_raw = getattr(args, "min_warning_severity", None)
    if min_warning_severity_raw is not None:
        min_warning_severity_source = "explicit"
    else:
        min_warning_severity_raw = warning_policy_config.get("min_warning_severity")
        if min_warning_severity_raw is not None:
            min_warning_severity_source = "config"
        else:
            env_min_warning_severity = str(os.getenv("JARVIS_BACKFILL_MIN_WARNING_SEVERITY") or "").strip().lower()
            if env_min_warning_severity:
                min_warning_severity_raw = env_min_warning_severity
                min_warning_severity_source = "env"
            else:
                min_warning_severity_raw = warning_profile_defaults.get("min_warning_severity")
                min_warning_severity_source = "profile_default"
    min_warning_severity = str(min_warning_severity_raw or "info").strip().lower()
    if min_warning_severity not in WARNING_SEVERITY_ORDER:
        warning_policy_resolution_fallbacks.append(
            {
                "field": "min_warning_severity",
                "invalid_value": min_warning_severity_raw,
                "source": min_warning_severity_source,
                "fallback_value": "info",
                "fallback_source": "profile_default",
            }
        )
        min_warning_severity = "info"
        min_warning_severity_source = "profile_default"
    exit_code_policy_source = "profile_default"
    exit_code_policy_raw = getattr(args, "exit_code_policy", None)
    if exit_code_policy_raw is not None:
        exit_code_policy_source = "explicit"
    else:
        exit_code_policy_raw = warning_policy_config.get("exit_code_policy")
        if exit_code_policy_raw is not None:
            exit_code_policy_source = "config"
        else:
            env_exit_code_policy = str(os.getenv("JARVIS_BACKFILL_EXIT_CODE_POLICY") or "").strip().lower()
            if env_exit_code_policy:
                exit_code_policy_raw = env_exit_code_policy
                exit_code_policy_source = "env"
            else:
                exit_code_policy_raw = warning_profile_defaults.get("exit_code_policy")
                exit_code_policy_source = "profile_default"
    exit_code_policy = str(exit_code_policy_raw or "off").strip().lower()
    if exit_code_policy not in {"off", "warning", "error"}:
        warning_policy_resolution_fallbacks.append(
            {
                "field": "exit_code_policy",
                "invalid_value": exit_code_policy_raw,
                "source": exit_code_policy_source,
                "fallback_value": "off",
                "fallback_source": "profile_default",
            }
        )
        exit_code_policy = "off"
        exit_code_policy_source = "profile_default"
    warning_exit_code_source = "profile_default"
    warning_exit_code_raw = getattr(args, "warning_exit_code", None)
    if warning_exit_code_raw is not None:
        warning_exit_code_source = "explicit"
    else:
        warning_exit_code_raw = warning_policy_config.get("warning_exit_code")
        if warning_exit_code_raw is not None:
            warning_exit_code_source = "config"
        else:
            env_warning_exit_code = str(os.getenv("JARVIS_BACKFILL_WARNING_EXIT_CODE") or "").strip()
            if env_warning_exit_code:
                warning_exit_code_raw = env_warning_exit_code
                warning_exit_code_source = "env"
            else:
                warning_exit_code_raw = warning_profile_defaults.get("warning_exit_code", 2)
                warning_exit_code_source = "profile_default"
    try:
        warning_exit_code = max(1, int(warning_exit_code_raw))
    except (TypeError, ValueError):
        warning_policy_resolution_fallbacks.append(
            {
                "field": "warning_exit_code",
                "invalid_value": warning_exit_code_raw,
                "source": warning_exit_code_source,
                "fallback_value": 2,
                "fallback_source": "profile_default",
            }
        )
        warning_exit_code = 2
        warning_exit_code_source = "profile_default"
    error_exit_code_source = "profile_default"
    error_exit_code_raw = getattr(args, "error_exit_code", None)
    if error_exit_code_raw is not None:
        error_exit_code_source = "explicit"
    else:
        error_exit_code_raw = warning_policy_config.get("error_exit_code")
        if error_exit_code_raw is not None:
            error_exit_code_source = "config"
        else:
            env_error_exit_code = str(os.getenv("JARVIS_BACKFILL_ERROR_EXIT_CODE") or "").strip()
            if env_error_exit_code:
                error_exit_code_raw = env_error_exit_code
                error_exit_code_source = "env"
            else:
                error_exit_code_raw = warning_profile_defaults.get("error_exit_code", 3)
                error_exit_code_source = "profile_default"
    try:
        error_exit_code = max(1, int(error_exit_code_raw))
    except (TypeError, ValueError):
        warning_policy_resolution_fallbacks.append(
            {
                "field": "error_exit_code",
                "invalid_value": error_exit_code_raw,
                "source": error_exit_code_source,
                "fallback_value": 3,
                "fallback_source": "profile_default",
            }
        )
        error_exit_code = 3
        error_exit_code_source = "profile_default"
    suppress_warning_codes_raw = list(getattr(args, "suppress_warning_code", None) or [])
    env_suppress_warning_codes_raw = _parse_csv_items(
        os.getenv("JARVIS_BACKFILL_SUPPRESS_WARNING_CODES")
    )
    profile_suppress_warning_codes_raw = list(warning_profile_defaults.get("suppress_warning_codes") or [])
    config_suppress_warning_codes_raw = _coerce_warning_code_items(
        warning_policy_config.get("suppress_warning_codes")
    )
    suppress_candidates = (
        profile_suppress_warning_codes_raw
        + config_suppress_warning_codes_raw
        + env_suppress_warning_codes_raw
        + suppress_warning_codes_raw
    )
    suppress_warning_codes = sorted(
        {
            str(item or "").strip().lower()
            for item in suppress_candidates
            if str(item or "").strip()
        }
    )
    suppress_warning_codes_sources = sorted(
        {
            source
            for source, values in (
                ("profile_default", profile_suppress_warning_codes_raw),
                ("config", config_suppress_warning_codes_raw),
                ("env", env_suppress_warning_codes_raw),
                ("explicit", suppress_warning_codes_raw),
            )
            if any(str(item or "").strip() for item in values)
        }
    )

    return {
        "preset": preset_name,
        "warning_policy_config_path": warning_policy_config_path,
        "warning_policy_config_source": warning_policy_config_source,
        "warning_policy_profile": warning_policy_profile,
        "warning_policy_resolution": {
            "profile": {
                "value": warning_policy_profile,
                "source": warning_policy_profile_source,
            },
            "config": {
                "path": warning_policy_config_path,
                "source": warning_policy_config_source,
            },
            "min_warning_severity": {
                "value": min_warning_severity,
                "source": min_warning_severity_source,
            },
            "exit_code_policy": {
                "value": exit_code_policy,
                "source": exit_code_policy_source,
            },
            "warning_exit_code": {
                "value": warning_exit_code,
                "source": warning_exit_code_source,
            },
            "error_exit_code": {
                "value": error_exit_code,
                "source": error_exit_code_source,
            },
            "suppress_warning_codes": {
                "value": suppress_warning_codes,
                "sources": suppress_warning_codes_sources,
            },
            "fallbacks": warning_policy_resolution_fallbacks,
            "has_fallbacks": bool(warning_policy_resolution_fallbacks),
        },
        "limit": int(_pick("limit") or preset.get("limit") or 100),
        "include_outcomes": bool(_pick("include_outcomes")),
        "include_review_artifacts": bool(_pick("include_review_artifacts")),
        "include_merge_outcomes": bool(_pick("include_merge_outcomes")),
        "skip_seen": bool(_pick("skip_seen")),
        "load_since_from_cursor_profile": bool(_pick("load_since_from_cursor_profile")),
        "top_signal_types": int(_pick("top_signal_types") or preset.get("top_signal_types") or 5),
        "max_source_counts": int(max_source_counts_value) if max_source_counts_value is not None else None,
        "max_signal_type_counts": (
            int(max_signal_type_counts_value)
            if max_signal_type_counts_value is not None
            else None
        ),
        "min_warning_severity": min_warning_severity,
        "exit_code_policy": exit_code_policy,
        "warning_exit_code": warning_exit_code,
        "error_exit_code": error_exit_code,
        "suppress_warning_codes": suppress_warning_codes,
        "include_raw_signals": bool(_pick("include_raw_signals")),
        "include_raw_ingestions": bool(_pick("include_raw_ingestions")),
    }


def _print_json_payload(payload: dict[str, Any], *, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        return
    print(json.dumps(payload, indent=2))


def _compute_policy_resolution_checksum(resolution: dict[str, Any]) -> str:
    canonical = json.dumps(
        resolution if isinstance(resolution, dict) else {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest


def _resolve_color_enabled(mode: str) -> bool:
    normalized = str(mode or "auto").strip().lower()
    if normalized == "always":
        return True
    if normalized == "never":
        return False
    stream = getattr(sys, "stdout", None)
    isatty_fn = getattr(stream, "isatty", None)
    if callable(isatty_fn):
        try:
            return bool(isatty_fn())
        except Exception:
            return False
    return False


def _ansi(text: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _render_project_backfill_pretty(payload: dict[str, Any], *, color_enabled: bool) -> str:
    result = dict(payload.get("result") or {})
    summary = (
        dict(result.get("summary") or {})
        if isinstance(result.get("summary"), dict)
        else {}
    )
    hints = list(payload.get("operator_hints") or [])

    lines: list[str] = []
    lines.append(_ansi("JARVIS Backfill Summary", "1;36", enabled=color_enabled))
    lines.append(
        " ".join(
            [
                f"project={str(payload.get('project_id') or '')}",
                f"profile={str(payload.get('profile_key') or '')}",
                f"preset={str(payload.get('preset') or '')}",
                f"warning_profile={str(payload.get('warning_policy_profile') or 'default')}",
                f"execute={bool(payload.get('execute'))}",
                f"dry_run={bool(result.get('dry_run'))}",
                f"cursor_persisted={bool(result.get('cursor_persisted'))}",
            ]
        )
    )
    warning_policy_config_path = payload.get("warning_policy_config_path")
    if warning_policy_config_path:
        warning_policy_config_source = str(payload.get("warning_policy_config_source") or "").strip()
        if warning_policy_config_source:
            lines.append(
                " ".join(
                    [
                        f"warning_policy_config={str(warning_policy_config_path)}",
                        f"source={warning_policy_config_source}",
                    ]
                )
            )
        else:
            lines.append(f"warning_policy_config={str(warning_policy_config_path)}")
    warning_policy_resolution = (
        dict(payload.get("warning_policy_resolution") or {})
        if isinstance(payload.get("warning_policy_resolution"), dict)
        else {}
    )
    if warning_policy_resolution:
        profile_resolution = (
            dict(warning_policy_resolution.get("profile") or {})
            if isinstance(warning_policy_resolution.get("profile"), dict)
            else {}
        )
        exit_policy_resolution = (
            dict(warning_policy_resolution.get("exit_code_policy") or {})
            if isinstance(warning_policy_resolution.get("exit_code_policy"), dict)
            else {}
        )
        profile_source = str(profile_resolution.get("source") or "").strip()
        exit_policy_source = str(exit_policy_resolution.get("source") or "").strip()
        if profile_source or exit_policy_source:
            lines.append(
                " ".join(
                    [
                        f"profile_source={profile_source or 'unknown'}",
                        f"exit_policy_source={exit_policy_source or 'unknown'}",
                    ]
                )
            )
    warning_policy_checksum = str(payload.get("warning_policy_checksum") or "").strip()
    if warning_policy_checksum:
        lines.append(f"warning_policy_checksum={warning_policy_checksum}")
    lines.append("")
    lines.append(_ansi("Counts", "1", enabled=color_enabled))
    lines.append(
        " ".join(
            [
                f"signals={int(summary.get('signals_count') or 0)}",
                f"would_ingest={int(summary.get('would_ingest_count') or 0)}",
                f"skipped_existing={int(summary.get('skipped_existing_count') or 0)}",
                f"persisted_markers={int(summary.get('persisted_marker_count') or 0)}",
            ]
        )
    )
    lines.append(
        " ".join(
            [
                f"candidate_pool={int(summary.get('candidate_pool_count') or 0)}",
                f"scan_limit={int(summary.get('candidate_scan_limit') or 0)}",
                f"scanned={int(summary.get('candidate_scanned_count') or 0)}",
                f"unscanned={int(summary.get('candidate_unscanned_count') or 0)}",
            ]
        )
    )

    source_counts = (
        dict(summary.get("source_counts") or {})
        if isinstance(summary.get("source_counts"), dict)
        else {}
    )
    if source_counts:
        parts = [
            f"{str(name)}={int(count)}"
            for name, count in sorted(
                source_counts.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )
        ]
        lines.append("")
        lines.append(_ansi("Source Counts", "1", enabled=color_enabled))
        lines.append(", ".join(parts))

    top_signal_types = list(summary.get("top_signal_types") or [])
    if top_signal_types:
        parts = [
            f"{str((row or {}).get('type') or 'unknown')}={int((row or {}).get('count') or 0)}"
            for row in top_signal_types
            if isinstance(row, dict)
        ]
        lines.append("")
        lines.append(_ansi("Top Signal Types", "1", enabled=color_enabled))
        lines.append(", ".join(parts))

    if hints:
        lines.append("")
        lines.append(_ansi(f"Warnings ({len(hints)})", "1;33", enabled=color_enabled))
        for item in hints:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "warning")
            message = str(item.get("message") or "").strip()
            lines.append(f"- [{code}] {message}")
            actions = list(item.get("recommended_actions") or [])
            if actions:
                lines.append(f"  suggestion: {str(actions[0])}")
    else:
        lines.append("")
        lines.append(_ansi("Warnings (0)", "1;32", enabled=color_enabled))

    return "\n".join(lines).rstrip() + "\n"


def _build_project_backfill_warnings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload.get("result") or {})
    summary = (
        dict(result.get("summary") or {})
        if isinstance(result.get("summary"), dict)
        else {}
    )
    warnings = list(payload.get("operator_hints") or [])
    warning_codes = [
        str(item.get("code") or "warning")
        for item in warnings
        if isinstance(item, dict)
    ]
    source_meta = (
        dict(summary.get("source_counts_metadata") or {})
        if isinstance(summary.get("source_counts_metadata"), dict)
        else {}
    )
    signal_type_meta = (
        dict(summary.get("signal_type_counts_metadata") or {})
        if isinstance(summary.get("signal_type_counts_metadata"), dict)
        else {}
    )
    suppression = (
        dict(payload.get("operator_hints_suppression") or {})
        if isinstance(payload.get("operator_hints_suppression"), dict)
        else {}
    )
    severity_filter = (
        dict(payload.get("operator_hints_severity_filter") or {})
        if isinstance(payload.get("operator_hints_severity_filter"), dict)
        else {}
    )
    warning_count = int(len(warning_codes))
    return {
        "status": "warning" if warning_count > 0 else "ok",
        "project_id": str(payload.get("project_id") or ""),
        "profile_key": str(payload.get("profile_key") or ""),
        "preset": str(payload.get("preset") or ""),
        "warning_policy_profile": str(payload.get("warning_policy_profile") or "default"),
        "warning_policy_config_path": (
            str(payload.get("warning_policy_config_path") or "")
            if payload.get("warning_policy_config_path") is not None
            else None
        ),
        "warning_policy_config_source": (
            str(payload.get("warning_policy_config_source") or "")
            if payload.get("warning_policy_config_source") is not None
            else None
        ),
        "warning_policy_resolution": (
            dict(payload.get("warning_policy_resolution") or {})
            if isinstance(payload.get("warning_policy_resolution"), dict)
            else {}
        ),
        "warning_policy_checksum": str(payload.get("warning_policy_checksum") or ""),
        "execute": bool(payload.get("execute")),
        "dry_run": bool(result.get("dry_run")),
        "cursor_persisted": bool(result.get("cursor_persisted")),
        "exit_code_policy": str(payload.get("exit_code_policy") or "off"),
        "exit_code": int(payload.get("exit_code") or 0),
        "exit_triggered": bool(payload.get("exit_triggered")),
        "max_warning_severity": str(payload.get("max_warning_severity") or "none"),
        "has_warnings": bool(warning_count > 0),
        "warning_count": warning_count,
        "warning_codes": warning_codes,
        "warnings": warnings,
        "warning_suppression": suppression,
        "warning_severity_filter": severity_filter,
        "signal_summary": {
            "signals_count": int(summary.get("signals_count") or 0),
            "candidate_unscanned_count": int(summary.get("candidate_unscanned_count") or 0),
            "source_counts_omitted_keys": int(source_meta.get("omitted_keys") or 0),
            "signal_type_counts_omitted_keys": int(signal_type_meta.get("omitted_keys") or 0),
        },
    }


def _build_project_backfill_policy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload.get("result") or {})
    summary = (
        dict(result.get("summary") or {})
        if isinstance(result.get("summary"), dict)
        else {}
    )
    warnings = list(payload.get("operator_hints") or [])
    warning_codes = [
        str(item.get("code") or "warning")
        for item in warnings
        if isinstance(item, dict)
    ]
    warning_count = int(len(warning_codes))
    return {
        "status": "warning" if warning_count > 0 else "ok",
        "project_id": str(payload.get("project_id") or ""),
        "profile_key": str(payload.get("profile_key") or ""),
        "preset": str(payload.get("preset") or ""),
        "warning_policy_profile": str(payload.get("warning_policy_profile") or "default"),
        "warning_policy_config_path": (
            str(payload.get("warning_policy_config_path") or "")
            if payload.get("warning_policy_config_path") is not None
            else None
        ),
        "warning_policy_config_source": (
            str(payload.get("warning_policy_config_source") or "")
            if payload.get("warning_policy_config_source") is not None
            else None
        ),
        "warning_policy_resolution": (
            dict(payload.get("warning_policy_resolution") or {})
            if isinstance(payload.get("warning_policy_resolution"), dict)
            else {}
        ),
        "warning_policy_checksum": str(payload.get("warning_policy_checksum") or ""),
        "exit_code_policy": str(payload.get("exit_code_policy") or "off"),
        "exit_code": int(payload.get("exit_code") or 0),
        "exit_triggered": bool(payload.get("exit_triggered")),
        "max_warning_severity": str(payload.get("max_warning_severity") or "none"),
        "warning_count": warning_count,
        "warning_codes": warning_codes,
        "signal_summary": {
            "signals_count": int(summary.get("signals_count") or 0),
            "candidate_unscanned_count": int(summary.get("candidate_unscanned_count") or 0),
        },
    }


def _next_project_backfill_preset(preset: str) -> str | None:
    order = ("quick", "balanced", "deep")
    normalized = str(preset or "").strip().lower()
    if normalized not in order:
        return None
    idx = order.index(normalized)
    if idx >= len(order) - 1:
        return None
    return str(order[idx + 1])


def _build_project_backfill_operator_hints(
    *,
    preset: str,
    resolved_options: dict[str, Any],
    run_result: dict[str, Any],
) -> list[dict[str, Any]]:
    summary = (
        dict(run_result.get("summary") or {})
        if isinstance(run_result.get("summary"), dict)
        else {}
    )
    hints: list[dict[str, Any]] = []

    unscanned_total = int(summary.get("candidate_unscanned_count") or 0)
    if unscanned_total > 0:
        unscanned_by_source = (
            dict(summary.get("candidate_unscanned_by_source") or {})
            if isinstance(summary.get("candidate_unscanned_by_source"), dict)
            else {}
        )
        recommended_actions: list[str] = []
        next_preset = _next_project_backfill_preset(preset)
        if next_preset:
            recommended_actions.append(
                f"Rerun with --preset {next_preset} for a wider scan window."
            )
        recommended_actions.append(
            "Increase --limit to raise the candidate scan budget (scan limit is limit*3)."
        )
        recommended_actions.append(
            "Narrow included sources if one source dominates clipping."
        )
        hints.append(
            {
                "code": "candidate_scan_clipped",
                "severity": "warning",
                "message": (
                    f"Candidate scan clipped {unscanned_total} deduped item(s) "
                    f"after scan cap."
                ),
                "details": {
                    "candidate_pool_count": int(summary.get("candidate_pool_count") or 0),
                    "candidate_scan_limit": int(summary.get("candidate_scan_limit") or 0),
                    "candidate_scanned_count": int(summary.get("candidate_scanned_count") or 0),
                    "candidate_unscanned_count": int(unscanned_total),
                    "unscanned_by_source": {
                        str(name): int(count)
                        for name, count in sorted(
                            unscanned_by_source.items(),
                            key=lambda item: (-int(item[1]), str(item[0])),
                        )
                    },
                },
                "recommended_actions": recommended_actions,
            }
        )

    source_meta = (
        dict(summary.get("source_counts_metadata") or {})
        if isinstance(summary.get("source_counts_metadata"), dict)
        else {}
    )
    source_omitted = int(source_meta.get("omitted_keys") or 0)
    if source_omitted > 0:
        hints.append(
            {
                "code": "source_counts_capped",
                "severity": "warning",
                "message": (
                    f"source_counts omitted {source_omitted} key(s) due to cap."
                ),
                "details": {
                    "cap": source_meta.get("cap"),
                    "total_keys": int(source_meta.get("total_keys") or 0),
                    "returned_keys": int(source_meta.get("returned_keys") or 0),
                    "omitted_keys": int(source_omitted),
                },
                "recommended_actions": [
                    "Increase --max-source-counts or remove it to inspect full source cardinality."
                ],
            }
        )

    signal_type_meta = (
        dict(summary.get("signal_type_counts_metadata") or {})
        if isinstance(summary.get("signal_type_counts_metadata"), dict)
        else {}
    )
    signal_type_omitted = int(signal_type_meta.get("omitted_keys") or 0)
    if signal_type_omitted > 0:
        hints.append(
            {
                "code": "signal_type_counts_capped",
                "severity": "warning",
                "message": (
                    f"signal_type_counts omitted {signal_type_omitted} key(s) due to cap."
                ),
                "details": {
                    "cap": signal_type_meta.get("cap"),
                    "total_keys": int(signal_type_meta.get("total_keys") or 0),
                    "returned_keys": int(signal_type_meta.get("returned_keys") or 0),
                    "omitted_keys": int(signal_type_omitted),
                },
                "recommended_actions": [
                    "Increase --max-signal-type-counts or remove it to inspect full signal-type cardinality."
                ],
            }
        )

    if not hints:
        return []
    hints_context = {
        "preset": str(preset or ""),
        "limit": int(resolved_options.get("limit") or 0),
        "dry_run": bool(run_result.get("dry_run")),
    }
    return [
        {
            **hint,
            "context": hints_context,
        }
        for hint in hints
    ]


def _apply_project_backfill_warning_suppression(
    *,
    hints: list[dict[str, Any]],
    suppress_warning_codes: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested = sorted(
        {
            str(item or "").strip().lower()
            for item in list(suppress_warning_codes or [])
            if str(item or "").strip()
        }
    )
    if not requested:
        return list(hints), {
            "requested_codes": [],
            "applied_codes": [],
            "suppressed_count": 0,
            "remaining_count": int(len(hints)),
            "total_count": int(len(hints)),
        }
    requested_set = set(requested)
    filtered: list[dict[str, Any]] = []
    applied_codes: list[str] = []
    for hint in hints:
        if not isinstance(hint, dict):
            filtered.append(hint)
            continue
        code = str(hint.get("code") or "").strip().lower()
        if code and code in requested_set:
            applied_codes.append(code)
            continue
        filtered.append(hint)
    total_count = int(len(hints))
    remaining_count = int(len(filtered))
    suppressed_count = int(max(0, total_count - remaining_count))
    return filtered, {
        "requested_codes": requested,
        "applied_codes": sorted(set(applied_codes)),
        "suppressed_count": suppressed_count,
        "remaining_count": remaining_count,
        "total_count": total_count,
    }


def _apply_project_backfill_warning_severity_filter(
    *,
    hints: list[dict[str, Any]],
    min_warning_severity: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested = str(min_warning_severity or "info").strip().lower()
    if requested not in WARNING_SEVERITY_ORDER:
        requested = "info"
    threshold_rank = int(WARNING_SEVERITY_ORDER.get(requested, 0))
    filtered: list[dict[str, Any]] = []
    for hint in hints:
        if not isinstance(hint, dict):
            filtered.append(hint)
            continue
        severity = str(hint.get("severity") or "warning").strip().lower()
        severity_rank = int(WARNING_SEVERITY_ORDER.get(severity, WARNING_SEVERITY_ORDER["warning"]))
        if severity_rank < threshold_rank:
            continue
        filtered.append(hint)
    total_count = int(len(hints))
    remaining_count = int(len(filtered))
    filtered_out_count = int(max(0, total_count - remaining_count))
    return filtered, {
        "requested_min_severity": requested,
        "filtered_out_count": filtered_out_count,
        "remaining_count": remaining_count,
        "total_count": total_count,
    }


def _max_project_backfill_warning_severity(hints: list[dict[str, Any]]) -> str:
    max_rank = -1
    max_label = "none"
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        severity = str(hint.get("severity") or "warning").strip().lower()
        rank = int(WARNING_SEVERITY_ORDER.get(severity, WARNING_SEVERITY_ORDER["warning"]))
        if rank > max_rank:
            max_rank = rank
            max_label = severity
    return max_label


def _compute_project_backfill_exit_code(
    *,
    hints: list[dict[str, Any]],
    exit_code_policy: str,
    warning_exit_code: int,
    error_exit_code: int,
) -> tuple[int, str]:
    policy = str(exit_code_policy or "off").strip().lower()
    if policy not in {"off", "warning", "error"}:
        policy = "off"
    max_severity = _max_project_backfill_warning_severity(hints)
    max_rank = int(WARNING_SEVERITY_ORDER.get(max_severity, -1))
    if policy == "off":
        return 0, max_severity
    if policy == "error":
        if max_rank >= WARNING_SEVERITY_ORDER["error"]:
            return max(1, int(error_exit_code)), max_severity
        return 0, max_severity
    if max_rank >= WARNING_SEVERITY_ORDER["error"]:
        return max(1, int(error_exit_code)), max_severity
    if max_rank >= WARNING_SEVERITY_ORDER["warning"]:
        return max(1, int(warning_exit_code)), max_severity
    return 0, max_severity


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
            enforce_critical_drift_gate=bool(getattr(args, "enforce_critical_drift_gate", True)),
            critical_drift_gate_limit=max(1, int(getattr(args, "critical_drift_gate_limit", 100) or 100)),
        )
        print(json.dumps(policy, indent=2))
    finally:
        runtime.close()


def _build_gate_status_payload(
    runtime: JarvisRuntime,
    *,
    plan_id: str,
    step_id: str,
    required_labels: list[str] | None,
    allow_no_required_checks: bool,
    single_maintainer_override: bool,
    override_actor: str | None,
    override_reason: str | None,
    override_sunset_condition: str | None,
    enforce_critical_drift_gate: bool,
    critical_drift_gate_limit: int,
) -> dict[str, Any]:
    policy = runtime.evaluate_review_promotion_policy(
        plan_id,
        step_id,
        required_labels=required_labels,
        allow_no_required_checks=allow_no_required_checks,
        single_maintainer_override=single_maintainer_override,
        override_actor=override_actor,
        override_reason=override_reason,
        override_sunset_condition=override_sunset_condition,
        enforce_critical_drift_gate=enforce_critical_drift_gate,
        critical_drift_gate_limit=max(1, int(critical_drift_gate_limit)),
    )
    policy_block = dict(policy.get("policy") or {})
    gate_status = dict(policy_block.get("critical_drift_gate_status") or {})
    blocking_interrupt_ids = [
        str(item).strip()
        for item in list(gate_status.get("blocking_interrupt_ids") or [])
        if str(item).strip()
    ]
    acknowledge_commands = [
        str(item).strip()
        for item in list(gate_status.get("acknowledge_commands") or gate_status.get("blocking_acknowledge_commands") or [])
        if str(item).strip()
    ]

    alert_by_id: dict[str, dict[str, Any]] = {}
    for row in list(policy_block.get("critical_drift_alerts") or []):
        if not isinstance(row, dict):
            continue
        interrupt_id = str(row.get("interrupt_id") or "").strip()
        if not interrupt_id:
            continue
        alert_by_id[interrupt_id] = {
            "interrupt_id": interrupt_id,
            "status": str(row.get("status") or ""),
            "domain": str(row.get("domain") or ""),
            "drift_severity": str(row.get("drift_severity") or ""),
            "reason": str(row.get("reason") or ""),
            "created_at": row.get("created_at"),
            "acknowledge_command": row.get("acknowledge_command"),
        }

    blocking_alerts = [
        alert_by_id[interrupt_id]
        for interrupt_id in blocking_interrupt_ids
        if interrupt_id in alert_by_id
    ]
    blocking_interrupt_statuses = {
        str(interrupt_id): str((alert_by_id.get(interrupt_id) or {}).get("status") or "")
        for interrupt_id in blocking_interrupt_ids
        if str(interrupt_id).strip()
    }
    gate_mode = str(gate_status.get("mode") or "disabled")
    blocked = bool(gate_status.get("blocked"))
    unlock_ready = bool(
        gate_mode == "enabled"
        and (
            (not blocking_interrupt_ids)
            or all(
                str(blocking_interrupt_statuses.get(interrupt_id) or "").strip().lower() == "acknowledged"
                for interrupt_id in blocking_interrupt_ids
            )
        )
    )
    recheck_command = f"python3 -m jarvis.cli plans promote-ready {str(plan_id)} {str(step_id)}"

    next_action = (
        "Acknowledge each blocking interrupt and rerun plans promote-ready."
        if blocked
        else (
            "Critical drift gate clear; run plans promote-ready when other checks are satisfied."
            if unlock_ready
            else "No blocking critical drift alerts."
        )
    )
    return {
        "plan_id": str(plan_id),
        "step_id": str(step_id),
        "gate_mode": gate_mode,
        "blocked": blocked,
        "unlock_ready": unlock_ready,
        "recheck_command": recheck_command,
        "blocking_interrupt_count": len(blocking_interrupt_ids),
        "blocking_interrupt_ids": blocking_interrupt_ids,
        "blocking_interrupt_statuses": blocking_interrupt_statuses,
        "acknowledge_commands": acknowledge_commands,
        "blocking_alerts": blocking_alerts,
        "next_action": next_action,
        "critical_drift_gate_status": gate_status,
    }


def _render_gate_status_payload_text(payload: dict[str, Any]) -> str:
    lines = [
        f"plan_id: {payload['plan_id']}",
        f"step_id: {payload['step_id']}",
        f"gate_mode: {payload['gate_mode']}",
        f"blocked: {'yes' if bool(payload['blocked']) else 'no'}",
        f"unlock_ready: {'yes' if bool(payload.get('unlock_ready')) else 'no'}",
        f"blocking_interrupt_count: {int(payload['blocking_interrupt_count'])}",
    ]
    if list(payload.get("blocking_interrupt_ids") or []):
        lines.append(
            "blocking_interrupt_ids: "
            + ", ".join(str(item) for item in list(payload.get("blocking_interrupt_ids") or []))
        )
    else:
        lines.append("blocking_interrupt_ids: none")
    lines.append("acknowledge_commands:")
    commands = list(payload.get("acknowledge_commands") or [])
    if commands:
        lines.extend(f"- {str(command)}" for command in commands)
    else:
        lines.append("- none")
    recheck_command = str(payload.get("recheck_command") or "").strip()
    if recheck_command:
        lines.append(f"recheck_command: {recheck_command}")
    lines.append(f"next_action: {payload['next_action']}")
    return "\n".join(lines)


def _render_gate_status_all_ci_summary(payload: dict[str, Any]) -> str:
    blocked_steps = list(payload.get("blocked_steps") or [])
    unlock_ready_steps = list(payload.get("unlock_ready_steps") or [])
    unlock_ready_commands = list(payload.get("unlock_ready_commands") or [])
    normalized_unlock_ready_commands = [
        str(command).strip() for command in unlock_ready_commands if str(command).strip()
    ]
    first_unlock_ready_command = (
        str(payload.get("first_unlock_ready_command") or "").strip()
        or (normalized_unlock_ready_commands[0] if normalized_unlock_ready_commands else "none")
    )
    acknowledge_commands = list(payload.get("acknowledge_commands") or [])
    normalized_ack_commands = [str(command).strip() for command in acknowledge_commands if str(command).strip()]
    errors = list(payload.get("errors") or [])
    next_action = str(payload.get("next_action") or "").strip()

    lines: list[str] = [
        "# plans gate-status-all summary",
        "",
        "## Counts",
        f"- scanned_review_count: {int(payload.get('scanned_review_count') or 0)}",
        f"- evaluated_step_count: {int(payload.get('evaluated_step_count') or 0)}",
        f"- visible_step_count: {int(payload.get('visible_step_count') or 0)}",
        f"- blocked_step_count: {int(payload.get('blocked_step_count') or 0)}",
        f"- unlock_ready_step_count: {int(payload.get('unlock_ready_step_count') or 0)}",
        f"- first_unlock_ready_command: {first_unlock_ready_command}",
        f"- error_count: {int(payload.get('error_count') or 0)}",
        f"- exit_reason: {str(payload.get('exit_reason') or '')}",
        f"- exit_code: {int(payload.get('exit_code') or 0)}",
        "",
        "## Blocked Steps",
    ]
    if blocked_steps:
        for row in blocked_steps:
            interrupt_ids = list(row.get("blocking_interrupt_ids") or [])
            interrupt_text = ", ".join(str(item) for item in interrupt_ids) if interrupt_ids else "none"
            lines.append(
                f"- {str(row.get('plan_id') or '')}/{str(row.get('step_id') or '')} (interrupt_ids: {interrupt_text})"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Unlock-Ready Steps")
    if unlock_ready_steps:
        for row in unlock_ready_steps:
            command = str(row.get("recheck_command") or "").strip() or "none"
            lines.append(
                f"- {str(row.get('plan_id') or '')}/{str(row.get('step_id') or '')} "
                f"(recheck_command: `{command}`)"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Acknowledge Commands")
    if normalized_ack_commands:
        lines.extend(f"- `{command}`" for command in normalized_ack_commands)
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Errors")
    if errors:
        lines.extend(
            f"- {str(item.get('plan_id') or '')}/{str(item.get('step_id') or '')}: {str(item.get('error') or '')}"
            for item in errors
        )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Next Action")
    lines.append(next_action or "none")
    return "\n".join(lines).rstrip() + "\n"


def _build_gate_status_all_ci_json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    unlock_ready_commands = [
        str(command).strip() for command in list(payload.get("unlock_ready_commands") or []) if str(command).strip()
    ]
    first_unlock_ready_command = str(payload.get("first_unlock_ready_command") or "").strip()
    if not first_unlock_ready_command and unlock_ready_commands:
        first_unlock_ready_command = unlock_ready_commands[0]
    if not first_unlock_ready_command:
        first_unlock_ready_command = "none"
    return {
        "only_blocked": bool(payload.get("only_blocked")),
        "only_unlock_ready": bool(payload.get("only_unlock_ready")),
        "scanned_review_count": int(payload.get("scanned_review_count") or 0),
        "evaluated_step_count": int(payload.get("evaluated_step_count") or 0),
        "visible_step_count": int(payload.get("visible_step_count") or 0),
        "blocked_step_count": int(payload.get("blocked_step_count") or 0),
        "unlock_ready_step_count": int(payload.get("unlock_ready_step_count") or 0),
        "error_count": int(payload.get("error_count") or 0),
        "exit_reason": str(payload.get("exit_reason") or ""),
        "exit_code": int(payload.get("exit_code") or 0),
        "exit_triggered": bool(payload.get("exit_triggered")),
        "blocked_exit_triggered": bool(payload.get("blocked_exit_triggered")),
        "error_exit_triggered": bool(payload.get("error_exit_triggered")),
        "zero_scanned_exit_triggered": bool(payload.get("zero_scanned_exit_triggered")),
        "zero_evaluated_exit_triggered": bool(payload.get("zero_evaluated_exit_triggered")),
        "zero_unlock_ready_exit_triggered": bool(payload.get("zero_unlock_ready_exit_triggered")),
        "empty_ack_commands_exit_triggered": bool(payload.get("empty_ack_commands_exit_triggered")),
        "blocked_steps": [
            {
                "plan_id": str(item.get("plan_id") or ""),
                "step_id": str(item.get("step_id") or ""),
                "unlock_ready": bool(item.get("unlock_ready")),
                "blocking_interrupt_ids": list(item.get("blocking_interrupt_ids") or []),
            }
            for item in list(payload.get("blocked_steps") or [])
        ],
        "unlock_ready_steps": [
            {
                "plan_id": str(item.get("plan_id") or ""),
                "step_id": str(item.get("step_id") or ""),
                "recheck_command": str(item.get("recheck_command") or ""),
            }
            for item in list(payload.get("unlock_ready_steps") or [])
        ],
        "unlock_ready_commands": unlock_ready_commands,
        "first_unlock_ready_command": first_unlock_ready_command,
        "acknowledge_commands": list(payload.get("acknowledge_commands") or []),
        "errors": [
            {
                "plan_id": str(item.get("plan_id") or ""),
                "step_id": str(item.get("step_id") or ""),
                "error": str(item.get("error") or ""),
            }
            for item in list(payload.get("errors") or [])
        ],
        "next_action": str(payload.get("next_action") or ""),
    }


def cmd_plans_gate_status(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        payload = _build_gate_status_payload(
            runtime,
            plan_id=str(args.plan_id),
            step_id=str(args.step_id),
            required_labels=args.required_label,
            allow_no_required_checks=bool(args.allow_no_required_checks),
            single_maintainer_override=bool(args.single_maintainer_override),
            override_actor=args.override_actor,
            override_reason=args.override_reason,
            override_sunset_condition=args.override_sunset_condition,
            enforce_critical_drift_gate=bool(getattr(args, "enforce_critical_drift_gate", True)),
            critical_drift_gate_limit=max(1, int(getattr(args, "critical_drift_gate_limit", 100) or 100)),
        )
        output_mode = str(getattr(args, "output", "json") or "json").strip().lower()
        if output_mode == "text":
            print(_render_gate_status_payload_text(payload))
            return
        print(json.dumps(payload, indent=2))
    finally:
        runtime.close()


def cmd_plans_gate_status_all(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        only_blocked = bool(getattr(args, "only_blocked", False))
        only_unlock_ready = bool(getattr(args, "only_unlock_ready", False))
        fail_on_blocked = bool(getattr(args, "fail_on_blocked", False))
        fail_on_errors = bool(getattr(args, "fail_on_errors", False))
        fail_on_zero_scanned = bool(getattr(args, "fail_on_zero_scanned", False))
        fail_on_zero_evaluated = bool(getattr(args, "fail_on_zero_evaluated", False))
        fail_on_zero_unlock_ready = bool(getattr(args, "fail_on_zero_unlock_ready", False))
        fail_on_empty_ack_commands = bool(getattr(args, "fail_on_empty_ack_commands", False))
        blocked_exit_code = max(1, int(getattr(args, "blocked_exit_code", 2) or 2))
        error_exit_code = max(1, int(getattr(args, "error_exit_code", 3) or 3))
        zero_scanned_exit_code = max(1, int(getattr(args, "zero_scanned_exit_code", 5) or 5))
        zero_evaluated_exit_code = max(1, int(getattr(args, "zero_evaluated_exit_code", 4) or 4))
        zero_unlock_ready_exit_code = max(1, int(getattr(args, "zero_unlock_ready_exit_code", 8) or 8))
        empty_ack_commands_exit_code = max(1, int(getattr(args, "empty_ack_commands_exit_code", 6) or 6))
        emit_ci_summary_path_value = getattr(args, "emit_ci_summary_path", None)
        emit_ci_json_path = (
            Path(str(getattr(args, "emit_ci_json_path", ""))).expanduser().resolve()
            if getattr(args, "emit_ci_json_path", None) is not None
            else None
        )
        ci_summary_path_source = "none"
        if emit_ci_summary_path_value is not None:
            emit_ci_summary_path = Path(str(emit_ci_summary_path_value)).expanduser().resolve()
            ci_summary_path_source = "cli"
        else:
            github_step_summary_raw = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary_raw:
                emit_ci_summary_path = Path(github_step_summary_raw).expanduser().resolve()
                ci_summary_path_source = "env"
            else:
                emit_ci_summary_path = None
        review_refs = runtime.list_provider_review_refs(
            provider=(str(args.provider).strip() or None) if getattr(args, "provider", None) is not None else None,
            repo_slug=(str(args.repo_slug).strip() or None) if getattr(args, "repo_slug", None) is not None else None,
            limit=max(1, int(getattr(args, "limit", 25) or 25)),
        )
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for ref in review_refs:
            plan_id = str((ref or {}).get("plan_id") or "").strip()
            step_id = str((ref or {}).get("step_id") or "").strip()
            if not plan_id or not step_id:
                continue
            try:
                row_payload = _build_gate_status_payload(
                    runtime,
                    plan_id=plan_id,
                    step_id=step_id,
                    required_labels=args.required_label,
                    allow_no_required_checks=bool(args.allow_no_required_checks),
                    single_maintainer_override=bool(args.single_maintainer_override),
                    override_actor=args.override_actor,
                    override_reason=args.override_reason,
                    override_sunset_condition=args.override_sunset_condition,
                    enforce_critical_drift_gate=bool(getattr(args, "enforce_critical_drift_gate", True)),
                    critical_drift_gate_limit=max(1, int(getattr(args, "critical_drift_gate_limit", 100) or 100)),
                )
                row_payload["review_ref"] = {
                    "provider": str(ref.get("provider") or ""),
                    "repo_slug": str(ref.get("repo_slug") or ""),
                    "repo_id": str(ref.get("repo_id") or ""),
                    "number": str(ref.get("number") or ""),
                    "head_branch": str(ref.get("head_branch") or ""),
                    "state": str(ref.get("state") or ""),
                    "draft": bool(ref.get("draft", False)),
                    "updated_at": ref.get("updated_at"),
                }
                rows.append(row_payload)
            except Exception as exc:
                errors.append(
                    {
                        "plan_id": plan_id,
                        "step_id": step_id,
                        "error": str(exc),
                    }
                )
        blocked_rows = [row for row in rows if bool(row.get("blocked"))]
        unlock_ready_rows = [row for row in rows if bool(row.get("unlock_ready"))]
        deduped_ack_commands: list[str] = []
        seen_commands: set[str] = set()
        for row in blocked_rows:
            for command in list(row.get("acknowledge_commands") or []):
                normalized = str(command).strip()
                if not normalized or normalized in seen_commands:
                    continue
                seen_commands.add(normalized)
                deduped_ack_commands.append(normalized)
        visible_rows = list(rows)
        if only_blocked:
            visible_rows = [row for row in visible_rows if bool(row.get("blocked"))]
        if only_unlock_ready:
            visible_rows = [row for row in visible_rows if bool(row.get("unlock_ready"))]
        unlock_ready_commands: list[str] = []
        seen_unlock_ready_commands: set[str] = set()
        for row in unlock_ready_rows:
            recheck_command = str(row.get("recheck_command") or "").strip()
            if not recheck_command or recheck_command in seen_unlock_ready_commands:
                continue
            seen_unlock_ready_commands.add(recheck_command)
            unlock_ready_commands.append(recheck_command)
        first_unlock_ready_command = unlock_ready_commands[0] if unlock_ready_commands else "none"
        blocked_exit_triggered = fail_on_blocked and bool(blocked_rows)
        error_exit_triggered = fail_on_errors and bool(errors)
        zero_scanned_exit_triggered = fail_on_zero_scanned and len(review_refs) == 0
        zero_evaluated_exit_triggered = fail_on_zero_evaluated and len(review_refs) > 0 and len(rows) == 0
        zero_unlock_ready_exit_triggered = fail_on_zero_unlock_ready and len(unlock_ready_rows) == 0
        empty_ack_commands_exit_triggered = (
            fail_on_empty_ack_commands and len(blocked_rows) > 0 and len(deduped_ack_commands) == 0
        )
        if error_exit_triggered:
            exit_code = error_exit_code
            exit_reason = "errors_present"
        elif zero_scanned_exit_triggered:
            exit_code = zero_scanned_exit_code
            exit_reason = "zero_scanned_reviews"
        elif zero_evaluated_exit_triggered:
            exit_code = zero_evaluated_exit_code
            exit_reason = "zero_evaluated_steps"
        elif zero_unlock_ready_exit_triggered:
            exit_code = zero_unlock_ready_exit_code
            exit_reason = "zero_unlock_ready_steps"
        elif empty_ack_commands_exit_triggered:
            exit_code = empty_ack_commands_exit_code
            exit_reason = "empty_ack_commands_missing"
        elif blocked_exit_triggered:
            exit_code = blocked_exit_code
            exit_reason = "blocked_steps_present"
        else:
            exit_code = 0
            exit_reason = "none"
        next_action = (
            "Acknowledge blocker queue commands, then rerun plans promote-ready for affected steps."
            if blocked_rows
            else (
                "Critical drift gate clear for unlock-ready steps; run plans promote-ready for release candidates."
                if unlock_ready_rows
                else "No blocking critical drift alerts across scanned review steps."
            )
        )
        payload = {
            "only_blocked": only_blocked,
            "only_unlock_ready": only_unlock_ready,
            "fail_on_blocked": fail_on_blocked,
            "fail_on_errors": fail_on_errors,
            "fail_on_zero_scanned": fail_on_zero_scanned,
            "fail_on_zero_evaluated": fail_on_zero_evaluated,
            "fail_on_zero_unlock_ready": fail_on_zero_unlock_ready,
            "fail_on_empty_ack_commands": fail_on_empty_ack_commands,
            "blocked_exit_code": int(blocked_exit_code),
            "error_exit_code": int(error_exit_code),
            "zero_scanned_exit_code": int(zero_scanned_exit_code),
            "zero_evaluated_exit_code": int(zero_evaluated_exit_code),
            "zero_unlock_ready_exit_code": int(zero_unlock_ready_exit_code),
            "empty_ack_commands_exit_code": int(empty_ack_commands_exit_code),
            "scanned_review_count": len(review_refs),
            "evaluated_step_count": len(rows),
            "visible_step_count": len(visible_rows),
            "non_blocking_step_count": max(0, len(rows) - len(blocked_rows)),
            "blocked_step_count": len(blocked_rows),
            "unlock_ready_step_count": len(unlock_ready_rows),
            "error_count": len(errors),
            "blocked_steps": [
                {
                    "plan_id": str(item.get("plan_id") or ""),
                    "step_id": str(item.get("step_id") or ""),
                    "unlock_ready": bool(item.get("unlock_ready")),
                    "blocking_interrupt_ids": list(item.get("blocking_interrupt_ids") or []),
                    "acknowledge_commands": list(item.get("acknowledge_commands") or []),
                }
                for item in blocked_rows
            ],
            "unlock_ready_steps": [
                {
                    "plan_id": str(item.get("plan_id") or ""),
                    "step_id": str(item.get("step_id") or ""),
                    "recheck_command": str(item.get("recheck_command") or ""),
                }
                for item in unlock_ready_rows
            ],
            "unlock_ready_commands": unlock_ready_commands,
            "first_unlock_ready_command": first_unlock_ready_command,
            "acknowledge_commands": deduped_ack_commands,
            "errors": errors,
            "next_action": next_action,
            "gate_rows": visible_rows,
            "exit_code": int(exit_code),
            "exit_triggered": bool(int(exit_code) != 0),
            "blocked_exit_triggered": bool(blocked_exit_triggered),
            "error_exit_triggered": bool(error_exit_triggered),
            "zero_scanned_exit_triggered": bool(zero_scanned_exit_triggered),
            "zero_evaluated_exit_triggered": bool(zero_evaluated_exit_triggered),
            "zero_unlock_ready_exit_triggered": bool(zero_unlock_ready_exit_triggered),
            "empty_ack_commands_exit_triggered": bool(empty_ack_commands_exit_triggered),
            "exit_reason": str(exit_reason),
        }
        if emit_ci_summary_path is not None:
            emit_ci_summary_path.parent.mkdir(parents=True, exist_ok=True)
            emit_ci_summary_path.write_text(
                _render_gate_status_all_ci_summary(payload),
                encoding="utf-8",
            )
            payload["ci_summary_path"] = str(emit_ci_summary_path)
            payload["ci_summary_path_source"] = ci_summary_path_source
        if emit_ci_json_path is not None:
            ci_json_payload = _build_gate_status_all_ci_json_payload(payload)
            emit_ci_json_path.parent.mkdir(parents=True, exist_ok=True)
            emit_ci_json_path.write_text(json.dumps(ci_json_payload, indent=2), encoding="utf-8")
            payload["ci_json_path"] = str(emit_ci_json_path)

        if bool(getattr(args, "emit_github_output", False)):
            artifact_path = (
                str(payload.get("ci_json_path") or "").strip()
                or (str(emit_ci_json_path) if emit_ci_json_path is not None else "")
            ) or "none"
            blocked_step_count = _coerce_int(payload.get("blocked_step_count"), default=0)
            unlock_ready_step_count = _coerce_int(payload.get("unlock_ready_step_count"), default=0)
            first_unlock_ready_command = (
                str(payload.get("first_unlock_ready_command") or "none").replace("\n", " ").strip() or "none"
            )
            acknowledge_commands = [
                str(command).strip()
                for command in list(payload.get("acknowledge_commands") or [])
                if str(command).strip()
            ]
            acknowledge_command_count = len(acknowledge_commands)
            first_acknowledge_command = (
                acknowledge_commands[0].replace("\n", " ").strip() if acknowledge_commands else "none"
            ) or "none"
            error_count = _coerce_int(payload.get("error_count"), default=0)
            exit_reason = str(payload.get("exit_reason") or "none").strip() or "none"
            exit_code_out = _coerce_int(payload.get("exit_code"), default=0)

            output_lines = [
                f"artifact_path={artifact_path}",
                f"blocked_step_count={blocked_step_count}",
                f"unlock_ready_step_count={unlock_ready_step_count}",
                f"first_unlock_ready_command={first_unlock_ready_command}",
                f"acknowledge_command_count={acknowledge_command_count}",
                f"first_acknowledge_command={first_acknowledge_command}",
                f"error_count={error_count}",
                f"exit_reason={exit_reason}",
                f"exit_code={exit_code_out}",
            ]

            github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
            if github_output:
                with Path(github_output).open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(output_lines) + "\n")

            summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
            if summary_heading_raw:
                github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
                if github_step_summary:
                    summary_path = Path(github_step_summary).expanduser()
                    summary_lines = [
                        f"## {summary_heading_raw}",
                        "",
                        f"- blocked_step_count: `{blocked_step_count}`",
                        f"- unlock_ready_step_count: `{unlock_ready_step_count}`",
                        f"- first_unlock_ready_command: `{first_unlock_ready_command}`",
                        f"- acknowledge_command_count: `{acknowledge_command_count}`",
                        f"- first_acknowledge_command: `{first_acknowledge_command}`",
                        f"- error_count: `{error_count}`",
                        f"- exit_reason: `{exit_reason}`",
                        f"- exit_code: `{exit_code_out}`",
                        "",
                    ]
                    with summary_path.open("a", encoding="utf-8") as handle:
                        handle.write("\n".join(summary_lines) + "\n")
        output_mode = str(getattr(args, "output", "json") or "json").strip().lower()
        if output_mode == "text":
            lines = [
                f"only_blocked: {'yes' if only_blocked else 'no'}",
                f"only_unlock_ready: {'yes' if only_unlock_ready else 'no'}",
                f"fail_on_blocked: {'yes' if fail_on_blocked else 'no'}",
                f"fail_on_errors: {'yes' if fail_on_errors else 'no'}",
                f"fail_on_zero_scanned: {'yes' if fail_on_zero_scanned else 'no'}",
                f"fail_on_zero_evaluated: {'yes' if fail_on_zero_evaluated else 'no'}",
                f"fail_on_zero_unlock_ready: {'yes' if fail_on_zero_unlock_ready else 'no'}",
                f"fail_on_empty_ack_commands: {'yes' if fail_on_empty_ack_commands else 'no'}",
                f"blocked_exit_code: {int(blocked_exit_code)}",
                f"error_exit_code: {int(error_exit_code)}",
                f"zero_scanned_exit_code: {int(zero_scanned_exit_code)}",
                f"zero_evaluated_exit_code: {int(zero_evaluated_exit_code)}",
                f"zero_unlock_ready_exit_code: {int(zero_unlock_ready_exit_code)}",
                f"empty_ack_commands_exit_code: {int(empty_ack_commands_exit_code)}",
                f"scanned_review_count: {int(payload['scanned_review_count'])}",
                f"evaluated_step_count: {int(payload['evaluated_step_count'])}",
                f"visible_step_count: {int(payload['visible_step_count'])}",
                f"blocked_step_count: {int(payload['blocked_step_count'])}",
                f"unlock_ready_step_count: {int(payload['unlock_ready_step_count'])}",
                f"first_unlock_ready_command: {first_unlock_ready_command}",
                f"error_count: {int(payload['error_count'])}",
            ]
            lines.append("blocker_queue:")
            if blocked_rows:
                for row in blocked_rows:
                    ids = list(row.get("blocking_interrupt_ids") or [])
                    ids_text = ", ".join(str(item) for item in ids) if ids else "none"
                    lines.append(
                        f"- {str(row.get('plan_id') or '')}/{str(row.get('step_id') or '')} interrupt_ids={ids_text}"
                    )
            else:
                lines.append("- none")
            lines.append("unlock_ready_steps:")
            if unlock_ready_rows:
                for row in unlock_ready_rows:
                    recheck_command = str(row.get("recheck_command") or "").strip() or "none"
                    lines.append(
                        f"- {str(row.get('plan_id') or '')}/{str(row.get('step_id') or '')} "
                        f"recheck_command={recheck_command}"
                    )
            else:
                lines.append("- none")
            lines.append("acknowledge_commands:")
            if deduped_ack_commands:
                lines.extend(f"- {command}" for command in deduped_ack_commands)
            else:
                lines.append("- none")
            if errors:
                lines.append("errors:")
                lines.extend(
                    f"- {str(item.get('plan_id') or '')}/{str(item.get('step_id') or '')}: {str(item.get('error') or '')}"
                    for item in errors
                )
            lines.append(f"exit_code: {int(payload['exit_code'])}")
            lines.append(f"exit_reason: {str(payload['exit_reason'])}")
            if str(payload.get("ci_summary_path") or "").strip():
                lines.append(f"ci_summary_path: {str(payload.get('ci_summary_path') or '')}")
            if str(payload.get("ci_json_path") or "").strip():
                lines.append(f"ci_json_path: {str(payload.get('ci_json_path') or '')}")
            lines.append(f"next_action: {next_action}")
            print("\n".join(lines))
        else:
            print(json.dumps(payload, indent=2))
        if int(exit_code) != 0:
            raise SystemExit(int(exit_code))
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
            enforce_critical_drift_gate=bool(getattr(args, "enforce_critical_drift_gate", True)),
            critical_drift_gate_limit=max(1, int(getattr(args, "critical_drift_gate_limit", 100) or 100)),
        )
        print(json.dumps(result, indent=2))
    finally:
        runtime.close()


def cmd_plans_backfill_project_signals(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    resolved = _resolve_project_backfill_options(args)
    try:
        run_result = runtime.run_project_backfill_with_cursor_profile_summary(
            project_id=str(args.project_id),
            profile_key=str(args.profile_key or "default"),
            actor=str(args.actor or "operator"),
            limit=max(1, int(resolved.get("limit") or 100)),
            include_outcomes=bool(resolved.get("include_outcomes")),
            include_review_artifacts=bool(resolved.get("include_review_artifacts")),
            include_merge_outcomes=bool(resolved.get("include_merge_outcomes")),
            skip_seen=bool(resolved.get("skip_seen")),
            since_updated_at=args.since_updated_at,
            since_outcomes_at=args.since_outcomes_at,
            since_review_artifacts_at=args.since_review_artifacts_at,
            since_merge_outcomes_at=args.since_merge_outcomes_at,
            load_since_from_cursor_profile=bool(resolved.get("load_since_from_cursor_profile")),
            dry_run=not bool(args.execute),
            top_signal_types=max(1, int(resolved.get("top_signal_types") or 5)),
            max_source_counts=resolved.get("max_source_counts"),
            max_signal_type_counts=resolved.get("max_signal_type_counts"),
            include_raw_signals=bool(resolved.get("include_raw_signals")),
            include_raw_ingestions=bool(resolved.get("include_raw_ingestions")),
        )
        operator_hints = _build_project_backfill_operator_hints(
            preset=str(resolved.get("preset") or "balanced"),
            resolved_options=resolved,
            run_result=run_result,
        )
        operator_hints, suppression_meta = _apply_project_backfill_warning_suppression(
            hints=operator_hints,
            suppress_warning_codes=list(resolved.get("suppress_warning_codes") or []),
        )
        operator_hints, severity_meta = _apply_project_backfill_warning_severity_filter(
            hints=operator_hints,
            min_warning_severity=str(resolved.get("min_warning_severity") or "info"),
        )
        exit_code, max_warning_severity = _compute_project_backfill_exit_code(
            hints=operator_hints,
            exit_code_policy=str(resolved.get("exit_code_policy") or "off"),
            warning_exit_code=int(resolved.get("warning_exit_code") or 2),
            error_exit_code=int(resolved.get("error_exit_code") or 3),
        )
        warning_policy_resolution = (
            dict(resolved.get("warning_policy_resolution") or {})
            if isinstance(resolved.get("warning_policy_resolution"), dict)
            else {}
        )
        warning_policy_checksum = _compute_policy_resolution_checksum(warning_policy_resolution)
        base_payload = {
            "project_id": str(args.project_id),
            "profile_key": str(args.profile_key or "default"),
            "actor": str(args.actor or "operator"),
            "execute": bool(args.execute),
            "preset": str(resolved.get("preset") or "balanced"),
            "warning_policy_profile": str(resolved.get("warning_policy_profile") or "default"),
            "warning_policy_config_path": resolved.get("warning_policy_config_path"),
            "warning_policy_config_source": resolved.get("warning_policy_config_source"),
            "warning_policy_resolution": warning_policy_resolution,
            "warning_policy_checksum": warning_policy_checksum,
            "resolved_options": resolved,
            "exit_code_policy": str(resolved.get("exit_code_policy") or "off"),
            "max_warning_severity": str(max_warning_severity),
            "exit_code": int(exit_code),
            "exit_triggered": bool(int(exit_code) != 0),
            "operator_hints_total_count": int(suppression_meta.get("total_count") or len(operator_hints)),
            "operator_hints_count": int(len(operator_hints)),
            "operator_hints_suppressed_count": int(suppression_meta.get("suppressed_count") or 0),
            "operator_hints_suppression": suppression_meta,
            "operator_hints_filtered_by_severity_count": int(severity_meta.get("filtered_out_count") or 0),
            "operator_hints_severity_filter": severity_meta,
            "operator_hints": operator_hints,
            "result": run_result,
        }
        output_mode = str(getattr(args, "output", "json") or "json").strip().lower()
        color_enabled = _resolve_color_enabled(str(getattr(args, "color", "auto") or "auto"))
        output_payload = base_payload
        if bool(getattr(args, "summary_only", False)):
            result_block = {
                "dry_run": bool(run_result.get("dry_run")),
                "cursor_persisted": bool(run_result.get("cursor_persisted")),
                "summary": (
                    dict(run_result.get("summary") or {})
                    if isinstance(run_result.get("summary"), dict)
                    else {}
                ),
            }
            output_payload = {
                **{k: v for k, v in base_payload.items() if k != "result"},
                "result": result_block,
            }

        if output_mode == "warnings":
            warnings_payload = _build_project_backfill_warnings_payload(output_payload)
            _print_json_payload(
                warnings_payload,
                compact=bool(getattr(args, "json_compact", False)),
            )
        elif output_mode == "policy":
            policy_payload = _build_project_backfill_policy_payload(output_payload)
            _print_json_payload(
                policy_payload,
                compact=bool(getattr(args, "json_compact", False)),
            )
        elif output_mode == "pretty":
            print(_render_project_backfill_pretty(output_payload, color_enabled=color_enabled), end="")
        else:
            _print_json_payload(
                output_payload,
                compact=bool(getattr(args, "json_compact", False)),
            )
        if int(exit_code) != 0:
            raise SystemExit(int(exit_code))
    finally:
        runtime.close()


def cmd_improvement_cycle_from_file(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        run = runtime.run_friction_hypothesis_cycle_from_file(
            domain=str(args.domain),
            source=str(args.source),
            input_path=str(args.input_path),
            input_format=args.input_format,
            default_segment=str(args.default_segment or "general"),
            default_severity=args.default_severity,
            default_frustration_score=args.default_frustration_score,
            status=str(args.status or "open"),
            metadata={"invoked_by": "jarvis.cli.improvement.cycle-from-file"},
            min_cluster_count=max(1, int(args.min_cluster_count)),
            proposal_limit=max(1, int(args.proposal_limit)),
            auto_register=bool(args.auto_register),
            owner=str(args.owner or "operator"),
        )
        report = runtime.build_hypothesis_inbox_report(
            domain=str(args.domain),
            cluster_min_count=max(1, int(args.min_cluster_count)),
            cluster_limit=max(1, int(args.report_cluster_limit)),
            hypothesis_limit=max(1, int(args.report_hypothesis_limit)),
            experiment_limit=max(1, int(args.report_experiment_limit)),
            queue_limit=max(1, int(args.report_queue_limit)),
        )
        payload = {
            "domain": str(args.domain),
            "source": str(args.source),
            "auto_register": bool(args.auto_register),
            "cycle": run,
            "report": report,
        }
        report_path = args.report_path.resolve() if args.report_path is not None else None
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            payload["report_path"] = str(report_path)

        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )
    finally:
        runtime.close()


def cmd_improvement_run_experiment_artifact(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        result = runtime.run_hypothesis_experiment_from_artifact(
            hypothesis_id=str(args.hypothesis_id),
            artifact_path=str(args.artifact_path),
            environment=args.environment,
            source_trace_id=args.source_trace_id,
            notes=args.notes,
        )
        payload = {
            "hypothesis_id": str(args.hypothesis_id),
            "result": result,
        }
        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )
    finally:
        runtime.close()


def cmd_improvement_seed_hypotheses(args: argparse.Namespace) -> None:
    template_path = args.template_path.resolve()
    loaded = json.loads(template_path.read_text(encoding="utf-8"))
    if isinstance(loaded, list):
        rows = list(loaded)
    elif isinstance(loaded, dict):
        rows = list(loaded.get("hypotheses") or loaded.get("items") or [])
    else:
        raise ValueError("invalid_hypothesis_template:expected_json_object_or_array")

    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        created: list[dict[str, Any]] = []
        existing: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        domain_cache: dict[str, list[dict[str, Any]]] = {}
        lookup_limit = max(1, int(getattr(args, "lookup_limit", 400) or 400))
        allow_invalid_rows = bool(getattr(args, "allow_invalid_rows", False))
        default_owner = str(getattr(args, "owner", "operator") or "operator").strip() or "operator"

        def _domain_hypotheses(domain: str) -> list[dict[str, Any]]:
            normalized = str(domain or "").strip().lower()
            if normalized not in domain_cache:
                domain_cache[normalized] = [
                    item
                    for item in runtime.list_hypotheses(
                        domain=normalized,
                        status=None,
                        limit=lookup_limit,
                    )
                    if isinstance(item, dict)
                ]
            return domain_cache[normalized]

        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                entry = {"index": index, "error": "template_row_not_object"}
                if allow_invalid_rows:
                    skipped.append(entry)
                else:
                    errors.append(entry)
                continue

            domain = str(row.get("domain") or "").strip().lower()
            title = str(row.get("title") or "").strip()
            statement = str(row.get("statement") or "").strip()
            proposed_change = str(row.get("proposed_change") or "").strip()
            friction_key_raw = row.get("friction_key")
            friction_key = (
                str(friction_key_raw).strip()
                if friction_key_raw is not None and str(friction_key_raw).strip()
                else None
            )
            normalized_friction_key = _normalize_friction_key(friction_key)

            if not domain or not title or not statement or not proposed_change:
                entry = {
                    "index": index,
                    "error": "missing_required_fields",
                    "domain": domain,
                    "title": title,
                }
                if allow_invalid_rows:
                    skipped.append(entry)
                else:
                    errors.append(entry)
                continue

            candidates = _domain_hypotheses(domain)
            matched: dict[str, Any] | None = None
            if normalized_friction_key:
                matched = next(
                    (
                        item
                        for item in candidates
                        if _normalize_friction_key(item.get("friction_key")) == normalized_friction_key
                    ),
                    None,
                )
            if matched is None:
                normalized_title = title.lower()
                matched = next(
                    (
                        item
                        for item in candidates
                        if str(item.get("title") or "").strip().lower() == normalized_title
                    ),
                    None,
                )

            if isinstance(matched, dict):
                existing.append(
                    {
                        "index": index,
                        "hypothesis_id": matched.get("hypothesis_id"),
                        "domain": matched.get("domain"),
                        "title": matched.get("title"),
                        "friction_key": matched.get("friction_key"),
                        "reason": "existing_match",
                    }
                )
                continue

            metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
            metadata["seed_template_path"] = str(template_path)
            metadata["seed_index"] = int(index)
            owner = str(row.get("owner") or default_owner).strip() or default_owner
            risk_level = str(row.get("risk_level") or "medium").strip().lower() or "medium"
            success_criteria = (
                dict(row.get("success_criteria") or {})
                if isinstance(row.get("success_criteria"), dict)
                else None
            )
            friction_ids = (
                [str(item).strip() for item in list(row.get("friction_ids") or []) if str(item).strip()]
                if isinstance(row.get("friction_ids"), list)
                else None
            )

            hypothesis = runtime.register_hypothesis(
                domain=domain,
                title=title,
                statement=statement,
                proposed_change=proposed_change,
                success_criteria=success_criteria,
                friction_key=friction_key,
                friction_ids=friction_ids,
                risk_level=risk_level,
                owner=owner,
                metadata=metadata,
            )
            created.append(
                {
                    "index": index,
                    "hypothesis_id": hypothesis.get("hypothesis_id"),
                    "domain": hypothesis.get("domain"),
                    "title": hypothesis.get("title"),
                    "friction_key": hypothesis.get("friction_key"),
                    "risk_level": hypothesis.get("risk_level"),
                }
            )
            candidates.append(dict(hypothesis))

        payload = {
            "generated_at": utc_now_iso(),
            "template_path": str(template_path),
            "requested_count": len(rows),
            "created_count": len(created),
            "existing_count": len(existing),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "created": created,
            "existing": existing,
            "skipped": skipped,
            "errors": errors,
            "status": "ok" if not errors else "warning",
        }

        output_path = args.output_path.resolve() if args.output_path is not None else None
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            payload["output_path"] = str(output_path)

        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )
        if errors and bool(getattr(args, "strict", False)):
            raise SystemExit(2)
    finally:
        runtime.close()


def _resolve_pipeline_path(raw_path: Any, *, config_path: Path) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    else:
        path = path.resolve()
    return path


def _normalize_friction_key(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        return ""
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value


def _resolve_record_path_value(payload: Any, path: Any) -> Any:
    if path is None:
        return payload
    if isinstance(path, (list, tuple)):
        parts = [str(item).strip() for item in path if str(item).strip()]
    else:
        parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    value: Any = payload
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
            continue
        if isinstance(value, list):
            try:
                idx = int(part)
            except (TypeError, ValueError):
                return None
            if idx < 0 or idx >= len(value):
                return None
            value = value[idx]
            continue
        return None
    return value


def _coerce_scalar_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value).strip()
    return text or None


def _collect_record_id_candidates(
    *,
    record: dict[str, Any],
    id_fields: list[str],
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for field in id_fields:
        value = _coerce_scalar_text(_resolve_record_path_value(record, field))
        if value is None:
            continue
        key = f"{field}::{value}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append((value, field))
    return candidates


def _resolve_first_record_text(
    *,
    record: dict[str, Any],
    field_paths: list[str],
) -> tuple[str | None, str | None]:
    for field in field_paths:
        value = _coerce_scalar_text(_resolve_record_path_value(record, field))
        if value is not None:
            return value, field
    return None, None


def _extract_evidence_record_ids_from_payload(payload: Any) -> list[str]:
    out: list[str] = []

    def _append(value: Any) -> None:
        text = str(value or "").strip()
        if not text or text in out:
            return
        out.append(text)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for item in list(node.get("seed_evidence_record_ids") or []):
                _append(item)
            raw_refs = node.get("evidence_lookup_refs")
            if isinstance(raw_refs, list):
                for raw in raw_refs:
                    if isinstance(raw, dict):
                        record_id = str(raw.get("record_id") or "").strip()
                        if record_id:
                            _append(record_id)
                        lookup_key = str(raw.get("lookup_key") or "").strip()
                        if lookup_key.startswith("record_id:"):
                            _append(lookup_key.split(":", 1)[1])
                    else:
                        text = str(raw or "").strip()
                        if text.startswith("record_id:"):
                            _append(text.split(":", 1)[1])
            for value in node.values():
                _walk(value)
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return out


def _collect_evidence_lookup_input_sources(
    *,
    config_path: Path | None,
    input_paths: list[Path],
    input_format: str | None,
) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}

    def _upsert(
        *,
        path: Path,
        source_kind: str,
        source_index: int | None,
        domain: str | None = None,
        source: str | None = None,
        file_format: str | None = None,
    ) -> None:
        key = str(path)
        current = deduped.get(key)
        if current is None:
            current = {
                "input_path": str(path),
                "input_format": str(file_format or "").strip().lower() or None,
                "domain": str(domain or "").strip().lower() or None,
                "source": str(source or "").strip().lower() or None,
                "origins": [],
            }
            deduped[key] = current
        elif not current.get("input_format") and file_format:
            current["input_format"] = str(file_format).strip().lower() or None
        if not current.get("domain") and domain:
            current["domain"] = str(domain).strip().lower() or None
        if not current.get("source") and source:
            current["source"] = str(source).strip().lower() or None
        current_origins = [dict(item) for item in list(current.get("origins") or []) if isinstance(item, dict)]
        current_origins.append(
            {
                "kind": source_kind,
                "index": source_index,
            }
        )
        current["origins"] = current_origins

    for index, raw_path in enumerate(input_paths):
        resolved_path = Path(str(raw_path)).expanduser().resolve()
        _upsert(
            path=resolved_path,
            source_kind="cli_input_path",
            source_index=index,
            file_format=input_format,
        )

    if config_path is not None:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid_pipeline_config:expected_json_object")
        defaults = dict(loaded.get("defaults") or {}) if isinstance(loaded.get("defaults"), dict) else {}
        feedback_jobs = list(loaded.get("feedback_jobs") or [])
        for index, raw_job in enumerate(feedback_jobs):
            if not isinstance(raw_job, dict):
                continue
            input_path_raw = raw_job.get("input_path")
            if input_path_raw is None or not str(input_path_raw).strip():
                continue
            resolved_input_path = _resolve_pipeline_path(input_path_raw, config_path=config_path)
            _upsert(
                path=resolved_input_path,
                source_kind="config_feedback_job",
                source_index=index,
                domain=str(raw_job.get("domain") or defaults.get("domain") or "").strip().lower() or None,
                source=str(raw_job.get("source") or defaults.get("source") or "").strip().lower() or None,
                file_format=raw_job.get("input_format"),
            )

    rows = [dict(item) for item in deduped.values()]
    rows.sort(key=lambda row: str(row.get("input_path") or ""))
    return rows


def _parse_timestamp_value(raw_value: Any) -> datetime | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        parsed_dt = raw_value
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
        return parsed_dt.astimezone(timezone.utc)
    if isinstance(raw_value, (int, float)):
        numeric = float(raw_value)
        if numeric > 10_000_000_000:
            numeric = numeric / 1000.0
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(raw_value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{10,16}", raw):
        try:
            numeric = float(raw)
            if numeric > 10_000_000_000:
                numeric = numeric / 1000.0
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    iso_candidate = raw
    if iso_candidate.endswith("Z"):
        iso_candidate = f"{iso_candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_app_identifier(raw_value: Any) -> str:
    text = str(raw_value or "").strip().lower()
    if not text:
        return "unknown_app"
    text = re.sub(r"[\s/]+", "_", text)
    text = re.sub(r"[^a-z0-9_\-]+", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown_app"


def _extract_app_identity_from_record(
    record: dict[str, Any],
    *,
    app_fields: list[str],
) -> tuple[str, str | None, str | None]:
    for field in app_fields:
        value = _resolve_record_path_value(record, field)
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple, set)):
            continue
        label = str(value).strip()
        if not label:
            continue
        return _normalize_app_identifier(label), label, field

    source_context_raw = record.get("source_context")
    if isinstance(source_context_raw, dict):
        for key in ("app_identifier", "app_name", "app", "product", "provider", "source", "name"):
            value = source_context_raw.get(key)
            if value is None:
                continue
            label = str(value).strip()
            if not label:
                continue
            return _normalize_app_identifier(label), label, f"source_context.{key}"
    elif source_context_raw is not None:
        label = str(source_context_raw).strip()
        if label:
            return _normalize_app_identifier(label), label, "source_context"
    return "unknown_app", None, None


def _inject_app_context_on_record(
    record: dict[str, Any],
    *,
    app_identifier: str,
    app_label: str | None,
    app_field: str | None,
) -> dict[str, Any]:
    enriched = dict(record)
    source_context_raw = enriched.get("source_context")
    source_context = dict(source_context_raw) if isinstance(source_context_raw, dict) else {}
    if not str(source_context.get("app_identifier") or "").strip():
        source_context["app_identifier"] = app_identifier
    if app_label and not str(source_context.get("app_label") or "").strip():
        source_context["app_label"] = app_label
    if app_field and not str(source_context.get("app_field") or "").strip():
        source_context["app_field"] = app_field
    enriched["source_context"] = source_context
    return enriched


def _resolve_signal_app_identifier(signal: dict[str, Any]) -> str:
    metadata = dict(signal.get("metadata") or {}) if isinstance(signal.get("metadata"), dict) else {}
    source_context = dict(metadata.get("source_context") or {}) if isinstance(metadata.get("source_context"), dict) else {}
    for key in ("app_identifier", "app_id", "app_name", "app", "product", "provider", "app_label"):
        value = source_context.get(key)
        normalized = _normalize_app_identifier(value)
        if normalized != "unknown_app":
            return normalized

    evidence = dict(signal.get("evidence") or {}) if isinstance(signal.get("evidence"), dict) else {}
    for key in ("app_identifier", "app_id", "app_name", "app", "product", "provider"):
        value = evidence.get(key)
        normalized = _normalize_app_identifier(value)
        if normalized != "unknown_app":
            return normalized
    return "unknown_app"


def _rank_app_counter(
    counter: Counter[str],
    *,
    total: int,
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cap = max(1, int(limit))
    denominator = max(1, int(total))
    for app_identifier, count in counter.most_common(cap):
        rows.append(
            {
                "app_identifier": app_identifier,
                "count": int(count),
                "share": round(float(count) / float(denominator), 4),
            }
        )
    return rows


def _collect_cluster_app_counters(signals: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    counters: dict[str, Counter[str]] = {}
    for signal in signals:
        canonical_key = str(signal.get("canonical_key") or "").strip()
        if not canonical_key:
            continue
        app_identifier = _resolve_signal_app_identifier(signal)
        counters.setdefault(canonical_key, Counter())[app_identifier] += 1
    return counters


def _resolve_signal_record_id(signal: dict[str, Any]) -> tuple[str | None, str | None]:
    evidence = dict(signal.get("evidence") or {}) if isinstance(signal.get("evidence"), dict) else {}
    for key in ("id", "record_id", "review_id", "ticket_id"):
        value = evidence.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text, key
    return None, None


def _build_signal_evidence_sample(signal: dict[str, Any]) -> dict[str, Any]:
    evidence = dict(signal.get("evidence") or {}) if isinstance(signal.get("evidence"), dict) else {}
    metadata = dict(signal.get("metadata") or {}) if isinstance(signal.get("metadata"), dict) else {}
    source_context = dict(metadata.get("source_context") or {}) if isinstance(metadata.get("source_context"), dict) else {}
    record_id, record_id_field = _resolve_signal_record_id(signal)
    app_label = (
        str(source_context.get("app_label") or source_context.get("app_name") or evidence.get("app_name") or "").strip()
        or None
    )
    summary_text = str(signal.get("summary") or "").strip()
    if not summary_text:
        summary_text = str(signal.get("normalized_summary") or "").strip()
    return {
        "record_id": record_id,
        "record_id_field": record_id_field,
        "signal_id": str(signal.get("friction_id") or "").strip() or None,
        "app_identifier": _resolve_signal_app_identifier(signal),
        "app_label": app_label,
        "source": str(signal.get("source") or "").strip() or None,
        "segment": str(signal.get("segment") or "").strip() or None,
        "summary": summary_text or None,
        "severity": int(signal.get("severity") or 0),
        "frustration_score": round(float(signal.get("frustration_score") or 0.0), 4),
        "created_at": evidence.get("created_at") or signal.get("created_at"),
        "updated_at": signal.get("updated_at"),
        "rating": evidence.get("rating"),
        "app_version": evidence.get("app_version"),
        "platform": evidence.get("platform"),
        "url": evidence.get("url"),
    }


def _collect_cluster_evidence_samples(
    *,
    signals: list[dict[str, Any]],
    canonical_key: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not canonical_key:
        return []
    selected = [
        dict(signal)
        for signal in signals
        if str(signal.get("canonical_key") or "").strip() == canonical_key
    ]
    selected.sort(
        key=lambda signal: (
            -float(signal.get("severity") or 0.0),
            -float(signal.get("frustration_score") or 0.0),
            str(signal.get("updated_at") or ""),
        )
    )
    evidence_rows: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()
    cap = max(1, int(limit))
    for signal in selected:
        sample = _build_signal_evidence_sample(signal)
        dedupe_key = (
            str(sample.get("record_id") or "").strip()
            or str(sample.get("signal_id") or "").strip()
        )
        if dedupe_key and dedupe_key in seen_record_ids:
            continue
        if dedupe_key:
            seen_record_ids.add(dedupe_key)
        evidence_rows.append(sample)
        if len(evidence_rows) >= cap:
            break
    return evidence_rows


def _summarize_displeasures_for_records(
    *,
    records: list[dict[str, Any]],
    domain: str,
    source: str,
    min_cluster_count: int,
    cluster_limit: int,
) -> dict[str, Any]:
    store = FrictionMiningStore(":memory:")
    adapter = FrictionSourceAdapter()
    try:
        ingest = adapter.ingest_feedback_batch(
            store=store,
            domain=domain,
            source=source,
            records=records,
            default_segment="general",
            default_severity=3.0,
            default_frustration_score=None,
            status="open",
            metadata={"invoked_by": "jarvis.cli.improvement.fitness-leaderboard"},
        )
        summary = store.summarize_common_displeasures(
            domain=domain,
            min_count=max(1, int(min_cluster_count)),
            limit=max(1, int(cluster_limit)),
        )
        signal_limit = max(
            int(ingest.get("ingested_count") or 0),
            max(200, int(cluster_limit) * 40),
        )
        signals = store.list_signals(
            domain=domain,
            limit=max(1, int(signal_limit)),
        )
        return {
            "ingest": ingest,
            "summary": summary,
            "signals": signals,
        }
    finally:
        store.close()


def cmd_improvement_fitness_leaderboard(args: argparse.Namespace) -> None:
    connector = FeedbackFileConnector()
    loaded = connector.load_records(
        path=args.input_path,
        file_format=args.input_format,
    )
    raw_records = [
        dict(item)
        for item in list(loaded.get("records") or [])
        if isinstance(item, dict)
    ]

    app_fields = _parse_csv_items(getattr(args, "app_fields", None))
    if not app_fields:
        app_fields = _parse_csv_items(DEFAULT_FITNESS_APP_FIELDS_CSV)
    top_apps_per_cluster = max(1, int(getattr(args, "top_apps_per_cluster", 3) or 3))
    min_cross_app_count = max(1, int(getattr(args, "min_cross_app_count", 2) or 2))
    own_app_aliases = sorted(
        {
            _normalize_app_identifier(item)
            for item in _parse_csv_items(getattr(args, "own_app_aliases", None))
            if _normalize_app_identifier(item) != "unknown_app"
        }
    )
    own_app_alias_set = set(own_app_aliases)

    records: list[dict[str, Any]] = []
    app_field_hits: Counter[str] = Counter()
    app_known_count = 0
    app_unknown_count = 0
    for raw_row in raw_records:
        app_identifier, app_label, app_field = _extract_app_identity_from_record(
            raw_row,
            app_fields=app_fields,
        )
        if app_identifier == "unknown_app":
            app_unknown_count += 1
        else:
            app_known_count += 1
        if app_field:
            app_field_hits[app_field] += 1
        records.append(
            _inject_app_context_on_record(
                raw_row,
                app_identifier=app_identifier,
                app_label=app_label,
                app_field=app_field,
            )
        )

    timestamp_fields = _parse_csv_items(getattr(args, "timestamp_fields", None))
    if not timestamp_fields:
        timestamp_fields = ["created_at", "at", "submission_date", "date", "timestamp", "occurred_at"]

    as_of_raw = getattr(args, "as_of", None)
    as_of_dt = _parse_timestamp_value(as_of_raw) if as_of_raw is not None else datetime.now(timezone.utc)
    if as_of_dt is None:
        raise ValueError(f"invalid_as_of_timestamp:{as_of_raw}")

    lookback_days = max(1, int(getattr(args, "lookback_days", 7) or 7))
    current_window_start = as_of_dt - timedelta(days=lookback_days)
    previous_window_start = current_window_start - timedelta(days=lookback_days)

    current_records: list[dict[str, Any]] = []
    previous_records: list[dict[str, Any]] = []
    untimed_count = 0
    older_count = 0
    future_count = 0
    include_untimed_current = bool(getattr(args, "include_untimed_current", False))

    for row in records:
        row_dt: datetime | None = None
        for field in timestamp_fields:
            value = _resolve_record_path_value(row, field)
            row_dt = _parse_timestamp_value(value)
            if row_dt is not None:
                break
        if row_dt is None:
            untimed_count += 1
            if include_untimed_current:
                current_records.append(dict(row))
            continue
        if row_dt > as_of_dt:
            future_count += 1
            continue
        if row_dt >= current_window_start:
            current_records.append(dict(row))
            continue
        if row_dt >= previous_window_start:
            previous_records.append(dict(row))
            continue
        older_count += 1

    domain = str(getattr(args, "domain", "fitness_apps") or "fitness_apps").strip().lower() or "fitness_apps"
    source = str(getattr(args, "source", "market_reviews") or "market_reviews").strip().lower() or "market_reviews"
    min_cluster_count = max(1, int(getattr(args, "min_cluster_count", 1) or 1))
    cluster_limit = max(1, int(getattr(args, "cluster_limit", 20) or 20))
    leaderboard_limit = max(1, int(getattr(args, "leaderboard_limit", 12) or 12))
    cooling_limit = max(1, int(getattr(args, "cooling_limit", 10) or 10))
    trend_threshold = max(0.0, float(getattr(args, "trend_threshold", 0.25) or 0.25))
    evidence_sample_limit = max(1, int(getattr(args, "evidence_sample_limit", 3) or 3))

    current_summary_payload = _summarize_displeasures_for_records(
        records=current_records,
        domain=domain,
        source=source,
        min_cluster_count=min_cluster_count,
        cluster_limit=cluster_limit,
    )
    previous_summary_payload = _summarize_displeasures_for_records(
        records=previous_records,
        domain=domain,
        source=source,
        min_cluster_count=min_cluster_count,
        cluster_limit=cluster_limit,
    )

    current_clusters = list((current_summary_payload.get("summary") or {}).get("clusters") or [])
    previous_clusters = list((previous_summary_payload.get("summary") or {}).get("clusters") or [])
    current_signals = [dict(item) for item in list(current_summary_payload.get("signals") or []) if isinstance(item, dict)]
    previous_signals = [dict(item) for item in list(previous_summary_payload.get("signals") or []) if isinstance(item, dict)]
    current_cluster_app_counters = _collect_cluster_app_counters(current_signals)
    previous_cluster_app_counters = _collect_cluster_app_counters(previous_signals)
    current_window_app_counter: Counter[str] = Counter()
    previous_window_app_counter: Counter[str] = Counter()
    for signal in current_signals:
        current_window_app_counter[_resolve_signal_app_identifier(signal)] += 1
    for signal in previous_signals:
        previous_window_app_counter[_resolve_signal_app_identifier(signal)] += 1

    previous_by_key = {
        str(item.get("canonical_key") or ""): item
        for item in previous_clusters
        if isinstance(item, dict) and str(item.get("canonical_key") or "")
    }
    current_keys = {
        str(item.get("canonical_key") or "")
        for item in current_clusters
        if isinstance(item, dict) and str(item.get("canonical_key") or "")
    }

    leaderboard: list[dict[str, Any]] = []
    for item in current_clusters:
        if not isinstance(item, dict):
            continue
        canonical_key = str(item.get("canonical_key") or "").strip()
        if not canonical_key:
            continue
        previous_item = dict(previous_by_key.get(canonical_key) or {})

        signal_count_current = int(item.get("signal_count") or 0)
        signal_count_previous = int(previous_item.get("signal_count") or 0)
        impact_current = float(item.get("impact_score") or 0.0)
        impact_previous = float(previous_item.get("impact_score") or 0.0)
        severity_current = float(item.get("avg_severity") or 0.0)
        severity_previous = float(previous_item.get("avg_severity") or 0.0)
        frustration_current = float(item.get("avg_frustration_score") or 0.0)
        frustration_previous = float(previous_item.get("avg_frustration_score") or 0.0)

        impact_delta = round(impact_current - impact_previous, 4)
        signal_delta = signal_count_current - signal_count_previous
        severity_delta = round(severity_current - severity_previous, 4)
        frustration_delta = round(frustration_current - frustration_previous, 4)
        current_app_counter = Counter(current_cluster_app_counters.get(canonical_key) or {})
        previous_app_counter = Counter(previous_cluster_app_counters.get(canonical_key) or {})
        cross_app_count_current = len(current_app_counter)
        cross_app_count_previous = len(previous_app_counter)
        own_app_signal_count_current = sum(int(current_app_counter.get(alias) or 0) for alias in own_app_alias_set)
        market_recurrence_score = round(impact_current * max(1, cross_app_count_current), 4)

        if signal_count_previous == 0 and signal_count_current > 0:
            trend = "new"
        elif impact_delta >= trend_threshold:
            trend = "rising"
        elif impact_delta <= -trend_threshold:
            trend = "cooling"
        else:
            trend = "flat"

        leaderboard.append(
            {
                "canonical_key": canonical_key,
                "friction_key": _normalize_friction_key(canonical_key),
                "trend": trend,
                "signal_count_current": signal_count_current,
                "signal_count_previous": signal_count_previous,
                "signal_count_delta": signal_delta,
                "impact_score_current": round(impact_current, 4),
                "impact_score_previous": round(impact_previous, 4),
                "impact_score_delta": impact_delta,
                "avg_severity_current": round(severity_current, 4),
                "avg_severity_previous": round(severity_previous, 4),
                "avg_severity_delta": severity_delta,
                "avg_frustration_score_current": round(frustration_current, 4),
                "avg_frustration_score_previous": round(frustration_previous, 4),
                "avg_frustration_score_delta": frustration_delta,
                "cross_app_count_current": int(cross_app_count_current),
                "cross_app_count_previous": int(cross_app_count_previous),
                "top_apps_current": _rank_app_counter(
                    current_app_counter,
                    total=max(1, signal_count_current),
                    limit=top_apps_per_cluster,
                ),
                "top_apps_previous": _rank_app_counter(
                    previous_app_counter,
                    total=max(1, signal_count_previous),
                    limit=top_apps_per_cluster,
                )
                if signal_count_previous > 0
                else [],
                "own_app_signal_count_current": int(own_app_signal_count_current),
                "market_recurrence_score": float(market_recurrence_score),
                "example_summary": str(item.get("example_summary") or ""),
                "top_tags": list(item.get("top_tags") or []),
                "top_sources": list(item.get("top_sources") or []),
                "evidence_samples_current": _collect_cluster_evidence_samples(
                    signals=current_signals,
                    canonical_key=canonical_key,
                    limit=evidence_sample_limit,
                ),
                "evidence_samples_previous": _collect_cluster_evidence_samples(
                    signals=previous_signals,
                    canonical_key=canonical_key,
                    limit=evidence_sample_limit,
                ),
                "last_seen_at": item.get("latest_seen_at"),
            }
        )

    trend_priority = {"new": 0, "rising": 1, "flat": 2, "cooling": 3}
    leaderboard.sort(
        key=lambda row: (
            int(trend_priority.get(str(row.get("trend") or "flat"), 9)),
            -float(row.get("market_recurrence_score") or 0.0),
            -float(row.get("impact_score_current") or 0.0),
            -int(row.get("signal_count_current") or 0),
            str(row.get("canonical_key") or ""),
        )
    )
    for index, row in enumerate(leaderboard):
        row["rank"] = index + 1

    cooling_clusters: list[dict[str, Any]] = []
    for item in previous_clusters:
        if not isinstance(item, dict):
            continue
        canonical_key = str(item.get("canonical_key") or "").strip()
        if not canonical_key or canonical_key in current_keys:
            continue
        previous_app_counter = Counter(previous_cluster_app_counters.get(canonical_key) or {})
        signal_count_previous = int(item.get("signal_count") or 0)
        cooling_clusters.append(
            {
                "canonical_key": canonical_key,
                "friction_key": _normalize_friction_key(canonical_key),
                "trend": "cooling",
                "signal_count_previous": signal_count_previous,
                "impact_score_previous": round(float(item.get("impact_score") or 0.0), 4),
                "avg_severity_previous": round(float(item.get("avg_severity") or 0.0), 4),
                "avg_frustration_score_previous": round(float(item.get("avg_frustration_score") or 0.0), 4),
                "cross_app_count_previous": int(len(previous_app_counter)),
                "top_apps_previous": _rank_app_counter(
                    previous_app_counter,
                    total=max(1, signal_count_previous),
                    limit=top_apps_per_cluster,
                )
                if signal_count_previous > 0
                else [],
                "example_summary": str(item.get("example_summary") or ""),
                "top_tags": list(item.get("top_tags") or []),
                "evidence_samples_previous": _collect_cluster_evidence_samples(
                    signals=previous_signals,
                    canonical_key=canonical_key,
                    limit=evidence_sample_limit,
                ),
                "last_seen_at": item.get("latest_seen_at"),
            }
        )
    cooling_clusters.sort(
        key=lambda row: (
            -float(row.get("impact_score_previous") or 0.0),
            -int(row.get("signal_count_previous") or 0),
            str(row.get("canonical_key") or ""),
        )
    )

    top_entries = leaderboard[:leaderboard_limit]
    top_cooling = cooling_clusters[:cooling_limit]
    shared_market_displeasures = [
        dict(row)
        for row in leaderboard
        if int(row.get("cross_app_count_current") or 0) >= min_cross_app_count
    ]
    top_shared_market_displeasures = shared_market_displeasures[:leaderboard_limit]

    white_space_candidates: list[dict[str, Any]] = []
    if own_app_alias_set:
        for row in shared_market_displeasures:
            trend = str(row.get("trend") or "")
            if trend not in {"new", "rising"}:
                continue
            own_count = int(row.get("own_app_signal_count_current") or 0)
            if own_count > 0:
                continue
            top_competitor_apps = [
                dict(app_row)
                for app_row in list(row.get("top_apps_current") or [])
                if isinstance(app_row, dict)
                and str(app_row.get("app_identifier") or "") not in own_app_alias_set
                and str(app_row.get("app_identifier") or "") != "unknown_app"
            ]
            white_space_candidates.append(
                {
                    "canonical_key": row.get("canonical_key"),
                    "friction_key": row.get("friction_key"),
                    "trend": trend,
                    "impact_score_current": row.get("impact_score_current"),
                    "impact_score_delta": row.get("impact_score_delta"),
                    "signal_count_current": int(row.get("signal_count_current") or 0),
                    "signal_count_previous": int(row.get("signal_count_previous") or 0),
                    "market_recurrence_score": row.get("market_recurrence_score"),
                    "cross_app_count_current": row.get("cross_app_count_current"),
                    "top_competitor_apps": top_competitor_apps[:top_apps_per_cluster],
                    "suggested_test": (
                        "Run controlled prototype validation for this friction against your own experience flow, "
                        "then compare retention/activation guardrails against the current baseline."
                    ),
                }
            )
    white_space_candidates.sort(
        key=lambda row: (
            -float(row.get("market_recurrence_score") or 0.0),
            -float(row.get("impact_score_current") or 0.0),
            str(row.get("friction_key") or ""),
        )
    )
    top_white_space_candidates = white_space_candidates[:leaderboard_limit]
    known_app_share = round(
        float(app_known_count) / float(max(1, app_known_count + app_unknown_count)),
        4,
    )

    suggested_actions: list[str] = []
    if known_app_share < 0.8:
        suggested_actions.append(
            "Improve app identity mapping (for example include app_name/app/source_context fields) "
            f"before relying on shared-market ranking (known_app_share={known_app_share})."
        )
    for entry in top_white_space_candidates[:2]:
        suggested_actions.append(
            "Prioritize whitespace validation for "
            f"'{entry.get('friction_key')}' (trend={entry.get('trend')}, "
            f"cross_app_count={entry.get('cross_app_count_current')})."
        )
    rising_or_new = [entry for entry in top_entries if str(entry.get("trend") or "") in {"new", "rising"}]
    for entry in rising_or_new[:3]:
        suggested_actions.append(
            "Test a controlled hypothesis for "
            f"'{entry.get('friction_key')}' (trend={entry.get('trend')}, "
            f"impact_delta={entry.get('impact_score_delta')}, cross_app_count={entry.get('cross_app_count_current')})."
        )
    if not suggested_actions and top_entries:
        suggested_actions.append("Monitor the top frustration clusters and re-run this leaderboard after next data pull.")
    if top_cooling:
        suggested_actions.append("Keep observing cooling clusters to confirm sustained improvement before deprioritizing.")
    if not top_entries:
        suggested_actions.append("No active current-window fitness frustration clusters; continue ingesting market review exports.")

    payload = {
        "generated_at": utc_now_iso(),
        "input_path": str(loaded.get("input_path") or ""),
        "input_format": str(loaded.get("input_format") or ""),
        "record_count": int(loaded.get("record_count") or 0),
        "domain": domain,
        "source": source,
        "as_of": as_of_dt.isoformat(),
        "lookback_days": lookback_days,
        "app_fields": list(app_fields),
        "top_apps_per_cluster": int(top_apps_per_cluster),
        "min_cross_app_count": int(min_cross_app_count),
        "own_app_aliases": own_app_aliases,
        "window": {
            "current": {
                "start": current_window_start.isoformat(),
                "end": as_of_dt.isoformat(),
            },
            "previous": {
                "start": previous_window_start.isoformat(),
                "end": current_window_start.isoformat(),
            },
        },
        "timestamp_fields": list(timestamp_fields),
        "trend_threshold": trend_threshold,
        "evidence_sample_limit": int(evidence_sample_limit),
        "include_untimed_current": include_untimed_current,
        "app_resolution": {
            "known_app_records": int(app_known_count),
            "unknown_app_records": int(app_unknown_count),
            "known_app_share": known_app_share,
            "field_hits": [
                {"field": field, "count": int(count)}
                for field, count in app_field_hits.most_common()
            ],
        },
        "counts": {
            "current_window_records": len(current_records),
            "previous_window_records": len(previous_records),
            "untimed_records": untimed_count,
            "future_records": future_count,
            "older_records": older_count,
        },
        "leaderboard_count": len(top_entries),
        "leaderboard": top_entries,
        "shared_market_displeasures_count": len(shared_market_displeasures),
        "shared_market_displeasures": top_shared_market_displeasures,
        "white_space_candidates_count": len(white_space_candidates),
        "white_space_candidates": top_white_space_candidates,
        "cooling_clusters_count": len(top_cooling),
        "cooling_clusters": top_cooling,
        "top_apps_current_window": _rank_app_counter(
            current_window_app_counter,
            total=max(1, len(current_signals)),
            limit=max(5, top_apps_per_cluster * 2),
        )
        if current_signals
        else [],
        "top_apps_previous_window": _rank_app_counter(
            previous_window_app_counter,
            total=max(1, len(previous_signals)),
            limit=max(5, top_apps_per_cluster * 2),
        )
        if previous_signals
        else [],
        "suggested_actions": suggested_actions,
        "status": "ok" if top_entries else "warning",
    }

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )

    if not top_entries and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def cmd_improvement_evidence_lookup(args: argparse.Namespace) -> None:
    requested_record_ids: list[str] = []
    for item in _parse_csv_items(getattr(args, "record_ids", None)):
        normalized = str(item).strip()
        if normalized and normalized not in requested_record_ids:
            requested_record_ids.append(normalized)

    operator_report_path = (
        args.operator_report_path.resolve()
        if getattr(args, "operator_report_path", None) is not None
        else None
    )
    operator_report_extracted_ids: list[str] = []
    if operator_report_path is not None:
        loaded_report = json.loads(operator_report_path.read_text(encoding="utf-8"))
        operator_report_extracted_ids = _extract_evidence_record_ids_from_payload(loaded_report)
        for record_id in operator_report_extracted_ids:
            if record_id not in requested_record_ids:
                requested_record_ids.append(record_id)
    if not requested_record_ids:
        raise ValueError("missing_record_ids")

    config_path = args.config_path.resolve() if getattr(args, "config_path", None) is not None else None
    input_paths = [Path(str(item)).expanduser() for item in list(getattr(args, "input_paths", None) or [])]
    explicit_input_format = str(getattr(args, "input_format", None) or "").strip().lower() or None
    sources = _collect_evidence_lookup_input_sources(
        config_path=config_path,
        input_paths=input_paths,
        input_format=explicit_input_format,
    )
    if not sources:
        raise ValueError("missing_input_sources")

    id_fields = _parse_csv_items(getattr(args, "id_fields", None))
    if not id_fields:
        id_fields = _parse_csv_items(DEFAULT_EVIDENCE_LOOKUP_ID_FIELDS_CSV)
    summary_fields = _parse_csv_items(getattr(args, "summary_fields", None))
    if not summary_fields:
        summary_fields = _parse_csv_items(DEFAULT_EVIDENCE_LOOKUP_SUMMARY_FIELDS_CSV)
    timestamp_fields = _parse_csv_items(getattr(args, "timestamp_fields", None))
    if not timestamp_fields:
        timestamp_fields = _parse_csv_items(DEFAULT_EVIDENCE_LOOKUP_TIMESTAMP_FIELDS_CSV)
    context_fields = _parse_csv_items(getattr(args, "context_fields", None))
    if not context_fields:
        context_fields = _parse_csv_items(DEFAULT_EVIDENCE_LOOKUP_CONTEXT_FIELDS_CSV)

    snippet_max_chars = max(80, int(getattr(args, "snippet_max_chars", 280) or 280))
    limit_per_id = max(1, int(getattr(args, "limit_per_id", 5) or 5))
    include_record = bool(getattr(args, "include_record", False))
    allow_missing_inputs = bool(getattr(args, "allow_missing_inputs", False))

    requested_id_set = set(requested_record_ids)
    connector = FeedbackFileConnector()
    matches_by_record_id: dict[str, list[dict[str, Any]]] = {record_id: [] for record_id in requested_record_ids}
    source_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for source_index, source in enumerate(sources):
        input_path = Path(str(source.get("input_path") or "")).expanduser().resolve()
        source_format = str(source.get("input_format") or "").strip().lower() or None
        domain = str(source.get("domain") or "").strip().lower() or None
        source_name = str(source.get("source") or "").strip().lower() or None
        source_entry = {
            "index": int(source_index),
            "input_path": str(input_path),
            "input_format": source_format,
            "domain": domain,
            "source": source_name,
            "origins": [dict(item) for item in list(source.get("origins") or []) if isinstance(item, dict)],
            "record_count": 0,
            "matched_count": 0,
            "matched_record_ids": [],
            "status": "ok",
        }
        if not input_path.exists():
            source_entry["status"] = "skipped_missing_input" if allow_missing_inputs else "error_missing_input"
            if not allow_missing_inputs:
                errors.append(
                    {
                        "source_index": int(source_index),
                        "input_path": str(input_path),
                        "error": "input_path_not_found",
                    }
                )
            source_rows.append(source_entry)
            continue
        try:
            loaded = connector.load_records(path=input_path, file_format=source_format)
        except Exception as exc:
            source_entry["status"] = "error_load_failed"
            source_entry["error"] = str(exc)
            errors.append(
                {
                    "source_index": int(source_index),
                    "input_path": str(input_path),
                    "error": str(exc),
                }
            )
            source_rows.append(source_entry)
            continue

        source_entry["input_format"] = str(loaded.get("input_format") or source_entry.get("input_format") or "")
        records = [dict(item) for item in list(loaded.get("records") or []) if isinstance(item, dict)]
        source_entry["record_count"] = len(records)
        matched_record_ids_for_source: set[str] = set()
        for record_index, record in enumerate(records):
            id_candidates = _collect_record_id_candidates(
                record=record,
                id_fields=id_fields,
            )
            matching_candidates: list[tuple[str, str]] = []
            seen_matching_record_ids: set[str] = set()
            for record_id, matched_field in id_candidates:
                if record_id not in requested_id_set or record_id in seen_matching_record_ids:
                    continue
                seen_matching_record_ids.add(record_id)
                matching_candidates.append((record_id, matched_field))
            if not matching_candidates:
                continue
            summary_text, summary_field = _resolve_first_record_text(
                record=record,
                field_paths=summary_fields,
            )
            timestamp_value, timestamp_field = _resolve_first_record_text(
                record=record,
                field_paths=timestamp_fields,
            )
            context: dict[str, Any] = {}
            for field in context_fields:
                value = _resolve_record_path_value(record, field)
                if value is None:
                    continue
                if isinstance(value, (dict, list, tuple, set)):
                    continue
                if field in context:
                    continue
                context[field] = value

            snippet = str(summary_text or "").strip()
            if len(snippet) > snippet_max_chars:
                snippet = f"{snippet[:snippet_max_chars].rstrip()}..."

            for record_id, matched_field in matching_candidates:
                match_row = {
                    "record_id": record_id,
                    "lookup_key": f"record_id:{record_id}",
                    "matched_field": matched_field,
                    "record_index": int(record_index),
                    "input_path": str(input_path),
                    "input_format": str(loaded.get("input_format") or ""),
                    "domain": domain,
                    "source": source_name,
                    "summary": summary_text,
                    "summary_field": summary_field,
                    "snippet": snippet or None,
                    "timestamp": timestamp_value,
                    "timestamp_field": timestamp_field,
                    "context": context,
                }
                if include_record:
                    match_row["record"] = dict(record)
                matches_by_record_id.setdefault(record_id, []).append(match_row)
                matched_record_ids_for_source.add(record_id)
        source_entry["matched_count"] = len(matched_record_ids_for_source)
        source_entry["matched_record_ids"] = sorted(matched_record_ids_for_source)
        source_rows.append(source_entry)

    matches: list[dict[str, Any]] = []
    missing_record_ids: list[str] = []
    for record_id in requested_record_ids:
        rows = [dict(item) for item in list(matches_by_record_id.get(record_id) or []) if isinstance(item, dict)]
        total_matches = len(rows)
        if total_matches == 0:
            missing_record_ids.append(record_id)
        matches.append(
            {
                "record_id": record_id,
                "lookup_key": f"record_id:{record_id}",
                "status": "resolved" if total_matches > 0 else "missing",
                "match_count": int(total_matches),
                "truncated_count": max(0, total_matches - limit_per_id),
                "matches": rows[:limit_per_id],
            }
        )

    status = "ok" if not errors and not missing_record_ids else "warning"
    payload = {
        "generated_at": utc_now_iso(),
        "status": status,
        "record_ids": list(requested_record_ids),
        "requested_count": len(requested_record_ids),
        "resolved_record_id_count": len(requested_record_ids) - len(missing_record_ids),
        "missing_record_ids": missing_record_ids,
        "missing_count": len(missing_record_ids),
        "operator_report_path": str(operator_report_path) if operator_report_path is not None else None,
        "operator_report_extracted_record_ids": operator_report_extracted_ids,
        "config_path": str(config_path) if config_path is not None else None,
        "input_source_count": len(source_rows),
        "input_sources": source_rows,
        "id_fields": list(id_fields),
        "summary_fields": list(summary_fields),
        "timestamp_fields": list(timestamp_fields),
        "context_fields": list(context_fields),
        "snippet_max_chars": int(snippet_max_chars),
        "limit_per_id": int(limit_per_id),
        "matches": matches,
        "error_count": len(errors),
        "errors": errors,
    }

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)) and (errors or missing_record_ids):
        raise SystemExit(2)


def cmd_improvement_seed_from_leaderboard(args: argparse.Namespace) -> None:
    leaderboard_path = args.leaderboard_path.resolve()
    loaded = json.loads(leaderboard_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_leaderboard_report:expected_json_object")

    requested_entry_source = (
        str(getattr(args, "entry_source", "leaderboard") or "leaderboard").strip().lower() or "leaderboard"
    )
    if requested_entry_source == "entries":
        requested_entry_source = "leaderboard"
    fallback_entry_source_raw = str(getattr(args, "fallback_entry_source", "leaderboard") or "").strip().lower()
    fallback_entry_source: str | None = None
    if fallback_entry_source_raw and fallback_entry_source_raw not in {"none", "off", "null"}:
        fallback_entry_source = "leaderboard" if fallback_entry_source_raw == "entries" else fallback_entry_source_raw

    allowed_entry_sources = {"leaderboard", "shared_market_displeasures", "white_space_candidates"}
    if requested_entry_source not in allowed_entry_sources:
        raise ValueError(
            "invalid_entry_source:"
            f"{requested_entry_source}:expected_one_of:{','.join(sorted(allowed_entry_sources))}"
        )
    if fallback_entry_source is not None and fallback_entry_source not in allowed_entry_sources:
        raise ValueError(
            "invalid_fallback_entry_source:"
            f"{fallback_entry_source}:expected_one_of:{','.join(sorted(allowed_entry_sources))},none"
        )

    def _load_entries_for_source(source_name: str) -> list[dict[str, Any]]:
        if source_name == "leaderboard":
            raw_rows = loaded.get("leaderboard")
            if raw_rows is None:
                raw_rows = loaded.get("entries")
        else:
            raw_rows = loaded.get(source_name)
        return [dict(item) for item in list(raw_rows or []) if isinstance(item, dict)]

    available_entry_source_counts = {
        source_name: len(_load_entries_for_source(source_name))
        for source_name in sorted(allowed_entry_sources)
    }
    entries = _load_entries_for_source(requested_entry_source)
    entry_source = requested_entry_source
    fallback_triggered = False
    fallback_reason: str | None = None
    if not entries and fallback_entry_source is not None and fallback_entry_source != requested_entry_source:
        fallback_entries = _load_entries_for_source(fallback_entry_source)
        if fallback_entries:
            entries = fallback_entries
            entry_source = fallback_entry_source
            fallback_triggered = True
            fallback_reason = f"entry_source_empty:{requested_entry_source}"
        else:
            fallback_reason = f"entry_source_empty_and_fallback_empty:{requested_entry_source}:{fallback_entry_source}"

    domain = str(getattr(args, "domain", None) or loaded.get("domain") or "fitness_apps").strip().lower() or "fitness_apps"
    source = str(getattr(args, "source", None) or loaded.get("source") or "fitness_leaderboard").strip().lower() or "fitness_leaderboard"
    owner = str(getattr(args, "owner", "operator") or "operator").strip() or "operator"
    lookup_limit = max(1, int(getattr(args, "lookup_limit", 400) or 400))
    limit = max(1, int(getattr(args, "limit", 8) or 8))
    min_impact_score = float(getattr(args, "min_impact_score", 0.0) or 0.0)
    min_impact_delta = float(getattr(args, "min_impact_delta", 0.0) or 0.0)
    min_cross_app_count = max(0, int(getattr(args, "min_cross_app_count", 0) or 0))
    min_signal_count_current = max(0, int(getattr(args, "min_signal_count_current", 0) or 0))
    include_trends = {item.lower() for item in _parse_csv_items(getattr(args, "trends", None))}
    if not include_trends:
        include_trends = {"new", "rising"}

    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        existing_candidates = [
            item
            for item in runtime.list_hypotheses(domain=domain, status=None, limit=lookup_limit)
            if isinstance(item, dict)
        ]

        existing_by_key: dict[str, dict[str, Any]] = {}
        existing_title_keys: dict[str, dict[str, Any]] = {}
        for hypothesis in existing_candidates:
            friction_key = _normalize_friction_key(hypothesis.get("friction_key"))
            if friction_key and friction_key not in existing_by_key:
                existing_by_key[friction_key] = hypothesis
            title_key = str(hypothesis.get("title") or "").strip().lower()
            if title_key and title_key not in existing_title_keys:
                existing_title_keys[title_key] = hypothesis

        created: list[dict[str, Any]] = []
        existing: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        selected_count = 0

        for index, entry in enumerate(entries):
            if len(created) >= limit:
                break

            trend = str(entry.get("trend") or "").strip().lower() or "flat"
            if include_trends and trend not in include_trends:
                skipped.append(
                    {
                        "index": index,
                        "reason": "trend_filtered",
                        "trend": trend,
                        "canonical_key": entry.get("canonical_key"),
                    }
                )
                continue

            impact_score_current = float(entry.get("impact_score_current") or 0.0)
            impact_score_delta = float(entry.get("impact_score_delta") or 0.0)
            if impact_score_current < min_impact_score:
                skipped.append(
                    {
                        "index": index,
                        "reason": "impact_score_below_min",
                        "impact_score_current": impact_score_current,
                        "min_impact_score": min_impact_score,
                        "canonical_key": entry.get("canonical_key"),
                    }
                )
                continue
            if trend != "new" and impact_score_delta < min_impact_delta:
                skipped.append(
                    {
                        "index": index,
                        "reason": "impact_delta_below_min",
                        "impact_score_delta": impact_score_delta,
                        "min_impact_delta": min_impact_delta,
                        "canonical_key": entry.get("canonical_key"),
                    }
                )
                continue

            canonical_key = str(entry.get("canonical_key") or "").strip()
            friction_key = _normalize_friction_key(entry.get("friction_key") or canonical_key)
            if not friction_key:
                skipped.append(
                    {
                        "index": index,
                        "reason": "missing_friction_key",
                        "canonical_key": canonical_key,
                    }
                )
                continue

            try:
                signal_count_current = max(0, int(entry.get("signal_count_current") or 0))
            except (TypeError, ValueError):
                signal_count_current = 0
            if signal_count_current < min_signal_count_current:
                skipped.append(
                    {
                        "index": index,
                        "reason": "signal_count_current_below_min",
                        "signal_count_current": signal_count_current,
                        "min_signal_count_current": min_signal_count_current,
                        "canonical_key": entry.get("canonical_key"),
                    }
                )
                continue
            try:
                signal_count_previous = max(0, int(entry.get("signal_count_previous") or 0))
            except (TypeError, ValueError):
                signal_count_previous = 0

            selected_count += 1
            title = f"{domain}: reduce '{friction_key}' frustration"
            existing_match = existing_by_key.get(friction_key) or existing_title_keys.get(title.strip().lower())
            if isinstance(existing_match, dict):
                existing.append(
                    {
                        "index": index,
                        "reason": "existing_match",
                        "hypothesis_id": existing_match.get("hypothesis_id"),
                        "domain": existing_match.get("domain"),
                        "title": existing_match.get("title"),
                        "friction_key": existing_match.get("friction_key"),
                    }
                )
                continue

            top_tag_rows = [dict(item) for item in list(entry.get("top_tags") or []) if isinstance(item, dict)]
            top_tag_names = [
                str(item.get("tag") or "").strip()
                for item in top_tag_rows
                if str(item.get("tag") or "").strip()
            ]
            top_app_rows = [dict(item) for item in list(entry.get("top_apps_current") or []) if isinstance(item, dict)]
            if not top_app_rows:
                top_app_rows = [
                    dict(item)
                    for item in list(entry.get("top_competitor_apps") or [])
                    if isinstance(item, dict)
                ]
            top_app_names = [
                _normalize_app_identifier(item.get("app_identifier"))
                for item in top_app_rows
                if _normalize_app_identifier(item.get("app_identifier")) != "unknown_app"
            ]
            cross_app_count_current = max(
                int(entry.get("cross_app_count_current") or 0),
                len(set(top_app_names)),
            )
            if cross_app_count_current < min_cross_app_count:
                skipped.append(
                    {
                        "index": index,
                        "reason": "cross_app_count_below_min",
                        "cross_app_count_current": cross_app_count_current,
                        "min_cross_app_count": min_cross_app_count,
                        "canonical_key": entry.get("canonical_key"),
                    }
                )
                continue
            summary_hint = str(entry.get("example_summary") or "").strip()
            evidence_samples_current = [
                dict(item)
                for item in list(entry.get("evidence_samples_current") or [])
                if isinstance(item, dict)
            ]
            evidence_samples_previous = [
                dict(item)
                for item in list(entry.get("evidence_samples_previous") or [])
                if isinstance(item, dict)
            ]
            seed_evidence_record_ids: list[str] = []
            for sample in [*evidence_samples_current, *evidence_samples_previous]:
                record_id = str(sample.get("record_id") or "").strip()
                if not record_id or record_id in seed_evidence_record_ids:
                    continue
                seed_evidence_record_ids.append(record_id)

            statement = (
                f"Recent {source} feedback indicates '{canonical_key or friction_key}' is {trend}, "
                f"with current impact score {round(impact_score_current, 4)} "
                f"and delta {round(impact_score_delta, 4)} versus the previous window "
                f"(cross-app count={cross_app_count_current})."
            )
            proposed_change = (
                "Design and test a controlled intervention targeted to this friction, "
                "then validate retention/activation guardrails before broader rollout."
            )
            if top_tag_names:
                proposed_change += f" Prioritize tactics related to tags: {', '.join(top_tag_names[:5])}."
            if top_app_names:
                proposed_change += f" Benchmark against competitor app signals from: {', '.join(top_app_names[:5])}."

            metadata = {
                "seed_source": "fitness_leaderboard",
                "seed_leaderboard_path": str(leaderboard_path),
                "seed_generated_at": str(loaded.get("generated_at") or ""),
                "seed_as_of": str(loaded.get("as_of") or ""),
                "seed_trend": trend,
                "seed_rank": entry.get("rank"),
                "seed_impact_score_current": impact_score_current,
                "seed_impact_score_delta": impact_score_delta,
                "seed_signal_count_current": int(signal_count_current),
                "seed_signal_count_previous": int(signal_count_previous),
                "seed_canonical_key": canonical_key,
                "seed_example_summary": summary_hint,
                "seed_top_tags": top_tag_names,
                "seed_entry_source": entry_source,
                "seed_cross_app_count_current": int(cross_app_count_current),
                "seed_market_recurrence_score": float(entry.get("market_recurrence_score") or 0.0),
                "seed_top_apps_current": sorted(set(top_app_names)),
                "seed_evidence_record_ids": seed_evidence_record_ids,
            }
            risk_level = "high" if trend == "new" and impact_score_current >= 6.0 else "medium"
            if trend in {"new", "rising"} and cross_app_count_current >= 3 and impact_score_current >= 4.0:
                risk_level = "high"
            if entry_source == "white_space_candidates" and trend in {"new", "rising"} and impact_score_current >= 3.0:
                risk_level = "high"

            try:
                hypothesis = runtime.register_hypothesis(
                    domain=domain,
                    title=title,
                    statement=statement,
                    proposed_change=proposed_change,
                    friction_key=friction_key,
                    risk_level=risk_level,
                    owner=owner,
                    metadata=metadata,
                )
            except Exception as exc:
                errors.append(
                    {
                        "index": index,
                        "error": str(exc),
                        "friction_key": friction_key,
                        "canonical_key": canonical_key,
                    }
                )
                continue

            created_entry = {
                "index": index,
                "hypothesis_id": hypothesis.get("hypothesis_id"),
                "domain": hypothesis.get("domain"),
                "title": hypothesis.get("title"),
                "friction_key": hypothesis.get("friction_key"),
                "risk_level": hypothesis.get("risk_level"),
                "trend": trend,
                "impact_score_current": round(impact_score_current, 4),
                "impact_score_delta": round(impact_score_delta, 4),
                "seed_evidence_record_ids": list(seed_evidence_record_ids),
            }
            created.append(created_entry)
            existing_by_key[friction_key] = hypothesis
            existing_title_keys[title.strip().lower()] = hypothesis

        payload = {
            "generated_at": utc_now_iso(),
            "leaderboard_path": str(leaderboard_path),
            "leaderboard_generated_at": loaded.get("generated_at"),
            "domain": domain,
            "source": source,
            "owner": owner,
            "requested_entry_source": requested_entry_source,
            "entry_source": entry_source,
            "available_entry_sources": sorted(allowed_entry_sources),
            "available_entry_source_counts": available_entry_source_counts,
            "entry_source_count": len(entries),
            "fallback_entry_source": fallback_entry_source,
            "fallback_triggered": fallback_triggered,
            "fallback_reason": fallback_reason,
            "trend_filters": sorted(include_trends),
            "min_impact_score": min_impact_score,
            "min_impact_delta": min_impact_delta,
            "min_cross_app_count": min_cross_app_count,
            "min_signal_count_current": min_signal_count_current,
            "requested_count": len(entries),
            "selected_count": selected_count,
            "created_count": len(created),
            "existing_count": len(existing),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "created": created,
            "existing": existing,
            "skipped": skipped,
            "errors": errors,
            "status": "ok" if not errors else "warning",
        }

        output_path = args.output_path.resolve() if args.output_path is not None else None
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            payload["output_path"] = str(output_path)

        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )

        if errors and bool(getattr(args, "strict", False)):
            raise SystemExit(2)
    finally:
        runtime.close()


def _resolve_path_from_base(raw_path: Any, *, base_dir: Path | None = None) -> Path:
    candidate = Path(str(raw_path)).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    anchor = base_dir.resolve() if isinstance(base_dir, Path) else Path.cwd()
    return (anchor / candidate).resolve()


def _default_controlled_environment_for_domain(domain: str) -> str:
    normalized = str(domain or "").strip().lower()
    if normalized in {"fitness", "fitness_apps"}:
        return "controlled_rollout"
    if normalized in {
        "quant_finance",
        "quantitative_finance",
        "kalshi_weather",
        "weather_betting",
        "kalshi",
        "market_ml",
        "market_machine_learning",
    }:
        return "controlled_backtest"
    return "sandbox"


def _normalize_guardrail_rows(raw_rows: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_row in list(raw_rows or []):
        if not isinstance(raw_row, dict):
            continue
        metric = str(raw_row.get("metric") or "").strip()
        if not metric:
            continue
        op = str(raw_row.get("op") or "<=").strip() or "<="
        threshold_raw = raw_row.get("value")
        if threshold_raw is None:
            threshold_raw = raw_row.get("threshold")
        threshold_value: float | None = None
        if threshold_raw is not None:
            try:
                threshold_value = float(threshold_raw)
            except (TypeError, ValueError):
                threshold_value = None
        rows.append(
            {
                "metric": metric,
                "op": op,
                "value": threshold_value,
            }
        )
    return rows


def _collect_benchmark_priority_targets(
    benchmark_payload: dict[str, Any],
    *,
    min_opportunity_score: float | None = None,
) -> list[dict[str, Any]]:
    summary_block = dict(benchmark_payload.get("summary") or {})
    recurring_rows = [row for row in list(benchmark_payload.get("recurring_pains") or []) if isinstance(row, dict)]
    recurring_by_key: dict[str, dict[str, Any]] = {}
    for row in recurring_rows:
        domain = str(row.get("domain") or "").strip().lower()
        friction_key = _normalize_friction_key(row.get("friction_key"))
        if not domain or not friction_key:
            continue
        recurring_by_key[f"{domain}:{friction_key}"] = row

    targets: list[dict[str, Any]] = []
    priority_rows = [row for row in list(benchmark_payload.get("priority_board") or []) if isinstance(row, dict)]
    for index, row in enumerate(priority_rows):
        domain = str(row.get("domain") or "").strip().lower()
        friction_key = _normalize_friction_key(row.get("friction_key"))
        if not domain or not friction_key:
            continue
        opportunity_score = _coerce_float(row.get("opportunity_score"), default=0.0)
        if min_opportunity_score is not None and opportunity_score < float(min_opportunity_score):
            continue
        evidence_runtime_trend = (
            str(
                row.get("evidence_runtime_trend")
                if row.get("evidence_runtime_trend") is not None
                else summary_block.get("evidence_runtime_history_trend")
                or ""
            ).strip().lower()
            or None
        )
        evidence_runtime_priority_boost = _coerce_float(
            row.get("evidence_runtime_priority_boost")
            if row.get("evidence_runtime_priority_boost") is not None
            else summary_block.get("evidence_runtime_priority_boost"),
            default=0.0,
        )
        recurring_row = dict(recurring_by_key.get(f"{domain}:{friction_key}") or {})
        hypothesis_ids: list[str] = []
        for raw_ids in [row.get("hypothesis_ids"), recurring_row.get("hypothesis_ids")]:
            for item in list(raw_ids or []):
                hypothesis_id = str(item).strip()
                if not hypothesis_id or hypothesis_id in hypothesis_ids:
                    continue
                hypothesis_ids.append(hypothesis_id)
        targets.append(
            {
                "index": index,
                "priority_rank": index + 1,
                "domain": domain,
                "friction_key": friction_key,
                "opportunity_score": round(float(opportunity_score), 4),
                "pressure_score": round(float(opportunity_score), 4),
                "pressure_rank": index + 1,
                "trend": str(row.get("trend") or recurring_row.get("trend") or "").strip().lower() or None,
                "recurrence_score": max(
                    0,
                    _coerce_int(
                        row.get("recurrence_score")
                        if row.get("recurrence_score") is not None
                        else recurring_row.get("recurrence_score"),
                        default=0,
                    ),
                ),
                "evidence_runtime_trend": evidence_runtime_trend,
                "evidence_runtime_priority_boost": round(float(evidence_runtime_priority_boost), 4),
                "hypothesis_ids": hypothesis_ids,
            }
        )
    return targets


def cmd_improvement_draft_experiment_jobs(args: argparse.Namespace) -> None:
    seed_report_path = (
        _resolve_path_from_base(args.seed_report_path, base_dir=Path.cwd())
        if getattr(args, "seed_report_path", None) is not None
        else None
    )
    benchmark_report_path = (
        _resolve_path_from_base(args.benchmark_report_path, base_dir=Path.cwd())
        if getattr(args, "benchmark_report_path", None) is not None
        else None
    )
    pipeline_config_path = (
        _resolve_path_from_base(args.pipeline_config_path, base_dir=Path.cwd())
        if getattr(args, "pipeline_config_path", None) is not None
        else None
    )
    if pipeline_config_path is None and getattr(args, "write_config_path", None) is not None:
        raise ValueError("write_config_path_requires_pipeline_config_path")
    if pipeline_config_path is None and bool(getattr(args, "in_place", False)):
        raise ValueError("in_place_requires_pipeline_config_path")

    seed_report: dict[str, Any] = {}
    benchmark_report: dict[str, Any] = {}
    benchmark_priority_targets: list[dict[str, Any]] = []
    benchmark_targets_without_hypothesis_id: list[dict[str, Any]] = []
    seed_hypothesis_ids: list[str] = []
    seen_seed_ids: set[str] = set()
    source_by_hypothesis_id: dict[str, dict[str, Any]] = {}
    domain_from_seed: str | None = None
    domain_from_benchmark: str | None = None
    benchmark_min_opportunity = (
        float(getattr(args, "benchmark_min_opportunity"))
        if getattr(args, "benchmark_min_opportunity", None) is not None
        else None
    )
    evidence_runtime_history_path_raw = getattr(args, "evidence_runtime_history_path", None)
    evidence_runtime_history_window = max(
        1,
        _coerce_int(getattr(args, "evidence_runtime_history_window", 7), default=7),
    )
    evidence_pressure_enabled = _coerce_bool(
        getattr(args, "evidence_pressure_enable", True),
        default=True,
    )
    evidence_pressure_min_priority_boost = max(
        0.0,
        _coerce_float(getattr(args, "evidence_pressure_min_priority_boost", 0.35), default=0.35),
    )
    evidence_pressure_limit_increase = max(
        0,
        _coerce_int(getattr(args, "evidence_pressure_limit_increase", 2), default=2),
    )
    evidence_pressure_statuses = _coerce_status_preferences(
        getattr(args, "evidence_pressure_statuses", "queued,testing"),
    )
    if not evidence_pressure_statuses:
        evidence_pressure_statuses = ["queued", "testing"]
    if benchmark_report_path is not None:
        loaded_benchmark = json.loads(benchmark_report_path.read_text(encoding="utf-8"))
        if not isinstance(loaded_benchmark, dict):
            raise ValueError("invalid_benchmark_report:expected_json_object")
        benchmark_report = dict(loaded_benchmark)
        benchmark_priority_targets = _collect_benchmark_priority_targets(
            benchmark_report,
            min_opportunity_score=benchmark_min_opportunity,
        )
        benchmark_domains = {
            str(row.get("domain") or "").strip().lower()
            for row in benchmark_priority_targets
            if str(row.get("domain") or "").strip()
        }
        if len(benchmark_domains) == 1:
            domain_from_benchmark = next(iter(benchmark_domains))
        for row in benchmark_priority_targets:
            hypothesis_ids = [str(item) for item in list(row.get("hypothesis_ids") or []) if str(item)]
            if not hypothesis_ids:
                benchmark_targets_without_hypothesis_id.append(row)
                continue
            for hypothesis_id in hypothesis_ids:
                if hypothesis_id in seen_seed_ids:
                    continue
                seen_seed_ids.add(hypothesis_id)
                seed_hypothesis_ids.append(hypothesis_id)
                source_by_hypothesis_id[hypothesis_id] = {
                    "seed_index": int(row.get("index") or 0),
                    "seed_reason": "benchmark_priority",
                    "benchmark_priority_rank": int(row.get("priority_rank") or 0),
                    "benchmark_opportunity_score": _coerce_float(row.get("opportunity_score"), default=0.0),
                    "benchmark_pressure_rank": int(
                        row.get("pressure_rank")
                        if row.get("pressure_rank") is not None
                        else row.get("priority_rank")
                        or 0
                    ),
                    "benchmark_pressure_score": _coerce_float(
                        row.get("pressure_score")
                        if row.get("pressure_score") is not None
                        else row.get("opportunity_score"),
                        default=0.0,
                    ),
                    "benchmark_domain": row.get("domain"),
                    "benchmark_friction_key": row.get("friction_key"),
                    "benchmark_trend": row.get("trend"),
                    "benchmark_recurrence_score": int(row.get("recurrence_score") or 0),
                    "benchmark_evidence_runtime_trend": (
                        str(row.get("evidence_runtime_trend") or "").strip().lower() or None
                    ),
                    "benchmark_evidence_runtime_priority_boost": _coerce_float(
                        row.get("evidence_runtime_priority_boost"),
                        default=0.0,
                    ),
                }
    if seed_report_path is not None:
        loaded_seed = json.loads(seed_report_path.read_text(encoding="utf-8"))
        if not isinstance(loaded_seed, dict):
            raise ValueError("invalid_seed_report:expected_json_object")
        seed_report = dict(loaded_seed)
        domain_from_seed = str(seed_report.get("domain") or "").strip().lower() or None
        if domain_from_seed in {"multi_domain", "all", "*"}:
            domain_from_seed = None
        seed_rows = [row for row in list(seed_report.get("created") or []) if isinstance(row, dict)]
        if bool(getattr(args, "include_existing", False)):
            seed_rows.extend([row for row in list(seed_report.get("existing") or []) if isinstance(row, dict)])

        for index, row in enumerate(seed_rows):
            hypothesis_id = str(row.get("hypothesis_id") or "").strip()
            if not hypothesis_id or hypothesis_id in seen_seed_ids:
                continue
            seen_seed_ids.add(hypothesis_id)
            seed_hypothesis_ids.append(hypothesis_id)
            source_by_hypothesis_id[hypothesis_id] = {
                "seed_index": int(index),
                "seed_reason": str(row.get("reason") or "seed_report_row"),
                "seed_evidence_record_ids": [
                    str(item).strip()
                    for item in list(row.get("seed_evidence_record_ids") or [])
                    if str(item).strip()
                ],
            }

    domain_filter_raw = str(getattr(args, "domain", None) or domain_from_seed or domain_from_benchmark or "").strip().lower()
    domain_filter = domain_filter_raw or None
    status_filters_requested = _coerce_status_preferences(getattr(args, "statuses", "queued"))
    if not status_filters_requested:
        status_filters_requested = ["queued"]
    limit_requested = max(1, int(getattr(args, "limit", 8) or 8))
    status_filters = list(status_filters_requested)
    limit = int(limit_requested)
    history_fallback_base_dir = (
        benchmark_report_path.parent
        if benchmark_report_path is not None
        else (
            seed_report_path.parent
            if seed_report_path is not None
            else Path.cwd()
        )
    )
    evidence_runtime_history_path = _resolve_evidence_runtime_history_path(
        raw_value=evidence_runtime_history_path_raw,
        fallback_base_dir=history_fallback_base_dir,
    )
    evidence_runtime_history = _summarize_evidence_runtime_history(
        history_path=evidence_runtime_history_path,
        window=evidence_runtime_history_window,
    )
    evidence_pressure_applied = False
    evidence_pressure_reason = "none"
    evidence_runtime_trend = str(evidence_runtime_history.get("trend") or "").strip().lower()
    evidence_runtime_priority_boost = max(
        0.0,
        _coerce_float(evidence_runtime_history.get("priority_boost"), default=0.0),
    )
    if (
        evidence_pressure_enabled
        and evidence_runtime_trend in {"worsening", "persistent"}
        and evidence_runtime_priority_boost >= evidence_pressure_min_priority_boost
    ):
        for pressure_status in evidence_pressure_statuses:
            if pressure_status not in status_filters:
                status_filters.append(pressure_status)
        limit = int(limit + evidence_pressure_limit_increase)
        evidence_pressure_applied = True
        evidence_pressure_reason = (
            "evidence_runtime_trend_"
            f"{evidence_runtime_trend}_priority_boost_{round(float(evidence_runtime_priority_boost), 4)}"
        )

    status_filter_set = set(status_filters)
    lookup_limit = max(limit, int(getattr(args, "lookup_limit", 400) or 400))
    default_sample_size = max(1, int(getattr(args, "default_sample_size", 100) or 100))
    overwrite_artifacts = bool(getattr(args, "overwrite_artifacts", False))

    pipeline_payload: dict[str, Any] = {}
    existing_experiment_jobs: list[dict[str, Any]] = []
    if pipeline_config_path is not None:
        loaded_pipeline = json.loads(pipeline_config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded_pipeline, dict):
            raise ValueError("invalid_pipeline_config:expected_json_object")
        pipeline_payload = dict(loaded_pipeline)
        existing_experiment_jobs = [
            dict(item)
            for item in list(pipeline_payload.get("experiment_jobs") or [])
            if isinstance(item, dict)
        ]

    base_dir = pipeline_config_path.parent if pipeline_config_path is not None else Path.cwd()
    artifacts_dir = _resolve_path_from_base(
        getattr(args, "artifacts_dir", "analysis/improvement/experiment_artifacts"),
        base_dir=base_dir,
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        errors: list[dict[str, Any]] = []

        listing_limit = max(lookup_limit, len(seed_hypothesis_ids) + 20)
        hypothesis_candidates = [
            item
            for item in runtime.list_hypotheses(
                domain=domain_filter,
                status=None,
                limit=listing_limit,
            )
            if isinstance(item, dict)
        ]
        hypothesis_by_id = {
            str(item.get("hypothesis_id") or "").strip(): item
            for item in hypothesis_candidates
            if str(item.get("hypothesis_id") or "").strip()
        }
        hypothesis_by_domain_friction: dict[str, dict[str, Any]] = {}
        for item in hypothesis_candidates:
            item_domain = str(item.get("domain") or "").strip().lower()
            item_friction_key = _normalize_friction_key(item.get("friction_key"))
            if item_domain and item_friction_key:
                hypothesis_by_domain_friction.setdefault(f"{item_domain}:{item_friction_key}", item)

        selected_hypotheses: list[dict[str, Any]] = []
        selected_hypothesis_ids: set[str] = set()

        def _append_selected_hypothesis(hypothesis: dict[str, Any]) -> bool:
            hypothesis_id = str(hypothesis.get("hypothesis_id") or "").strip()
            if not hypothesis_id or hypothesis_id in selected_hypothesis_ids:
                return False
            selected_hypotheses.append(dict(hypothesis))
            selected_hypothesis_ids.add(hypothesis_id)
            return True

        if seed_hypothesis_ids:
            for index, hypothesis_id in enumerate(seed_hypothesis_ids):
                if len(selected_hypotheses) >= limit:
                    break
                hypothesis = hypothesis_by_id.get(hypothesis_id)
                if not isinstance(hypothesis, dict):
                    fallback = runtime.hypothesis_lab.get_hypothesis(hypothesis_id)
                    if isinstance(fallback, dict):
                        hypothesis = fallback
                if not isinstance(hypothesis, dict):
                    errors.append(
                        {
                            "index": index,
                            "hypothesis_id": hypothesis_id,
                            "error": "seed_hypothesis_not_found",
                        }
                    )
                    continue
                status_value = str(hypothesis.get("status") or "").strip().lower()
                if status_filter_set and status_value not in status_filter_set:
                    continue
                _append_selected_hypothesis(dict(hypothesis))

        for row in benchmark_targets_without_hypothesis_id:
            if len(selected_hypotheses) >= limit:
                break
            row_domain = str(row.get("domain") or "").strip().lower()
            row_friction_key = _normalize_friction_key(row.get("friction_key"))
            if not row_domain or not row_friction_key:
                continue
            hypothesis = dict(hypothesis_by_domain_friction.get(f"{row_domain}:{row_friction_key}") or {})
            if not hypothesis:
                continue
            status_value = str(hypothesis.get("status") or "").strip().lower()
            if status_filter_set and status_value not in status_filter_set:
                continue
            if not _append_selected_hypothesis(hypothesis):
                continue
            hypothesis_id = str(hypothesis.get("hypothesis_id") or "").strip()
            source_by_hypothesis_id[hypothesis_id] = {
                "seed_index": int(row.get("index") or 0),
                "seed_reason": "benchmark_priority_domain_friction",
                "benchmark_priority_rank": int(row.get("priority_rank") or 0),
                "benchmark_opportunity_score": _coerce_float(row.get("opportunity_score"), default=0.0),
                "benchmark_pressure_rank": int(
                    row.get("pressure_rank")
                    if row.get("pressure_rank") is not None
                    else row.get("priority_rank")
                    or 0
                ),
                "benchmark_pressure_score": _coerce_float(
                    row.get("pressure_score")
                    if row.get("pressure_score") is not None
                    else row.get("opportunity_score"),
                    default=0.0,
                ),
                "benchmark_domain": row.get("domain"),
                "benchmark_friction_key": row.get("friction_key"),
                "benchmark_trend": row.get("trend"),
                "benchmark_recurrence_score": int(row.get("recurrence_score") or 0),
                "benchmark_evidence_runtime_trend": (
                    str(row.get("evidence_runtime_trend") or "").strip().lower() or None
                ),
                "benchmark_evidence_runtime_priority_boost": _coerce_float(
                    row.get("evidence_runtime_priority_boost"),
                    default=0.0,
                ),
            }

        if not selected_hypotheses:
            for hypothesis in hypothesis_candidates:
                status_value = str(hypothesis.get("status") or "").strip().lower()
                if status_filter_set and status_value not in status_filter_set:
                    continue
                if not _append_selected_hypothesis(dict(hypothesis)):
                    continue
                if len(selected_hypotheses) >= limit:
                    break

        existing_hypothesis_ids: set[str] = set()
        existing_domain_friction_pairs: set[str] = set()
        for existing_job in existing_experiment_jobs:
            existing_hypothesis_id = str(existing_job.get("hypothesis_id") or "").strip()
            if existing_hypothesis_id:
                existing_hypothesis_ids.add(existing_hypothesis_id)
            existing_domain = str(existing_job.get("domain") or "").strip().lower()
            existing_friction_key = _normalize_friction_key(existing_job.get("friction_key"))
            if existing_domain and existing_friction_key:
                existing_domain_friction_pairs.add(f"{existing_domain}:{existing_friction_key}")

        appended_jobs: list[dict[str, Any]] = []
        skipped_existing_jobs: list[dict[str, Any]] = []
        drafts: list[dict[str, Any]] = []
        artifact_created_count = 0
        artifact_existing_count = 0

        for index, hypothesis in enumerate(selected_hypotheses):
            hypothesis_id = str(hypothesis.get("hypothesis_id") or "").strip()
            if not hypothesis_id:
                errors.append(
                    {
                        "index": index,
                        "error": "missing_hypothesis_id",
                    }
                )
                continue
            domain = str(hypothesis.get("domain") or "").strip().lower() or "unknown"
            friction_key = _normalize_friction_key(hypothesis.get("friction_key"))
            if not friction_key:
                friction_key = _normalize_friction_key(hypothesis.get("title")) or f"hypothesis_{index + 1}"
            status_value = str(hypothesis.get("status") or "").strip().lower()
            hypothesis_metadata = (
                dict(hypothesis.get("metadata") or {})
                if isinstance(hypothesis.get("metadata"), dict)
                else {}
            )
            source_hint = dict(source_by_hypothesis_id.get(hypothesis_id) or {})
            source_reason = str(source_hint.get("seed_reason") or "queued_hypothesis")
            seed_evidence_record_ids: list[str] = []
            for raw_ids in (
                hypothesis_metadata.get("seed_evidence_record_ids"),
                source_hint.get("seed_evidence_record_ids"),
            ):
                for item in list(raw_ids or []):
                    value = str(item).strip()
                    if not value or value in seed_evidence_record_ids:
                        continue
                    seed_evidence_record_ids.append(value)

            success_criteria = (
                dict(hypothesis.get("success_criteria") or {})
                if isinstance(hypothesis.get("success_criteria"), dict)
                else {}
            )
            metric = str(success_criteria.get("metric") or "").strip() or "utility_score"
            direction = str(success_criteria.get("direction") or "increase").strip().lower() or "increase"
            try:
                min_effect = float(success_criteria.get("min_effect") or 0.0)
            except (TypeError, ValueError):
                min_effect = 0.0
            try:
                target_sample_size = int(success_criteria.get("min_sample_size"))
                if target_sample_size <= 0:
                    target_sample_size = default_sample_size
            except (TypeError, ValueError):
                target_sample_size = default_sample_size
            guardrails = _normalize_guardrail_rows(success_criteria.get("guardrails"))

            environment = (
                str(getattr(args, "environment", None)).strip()
                if getattr(args, "environment", None) is not None and str(getattr(args, "environment", None)).strip()
                else _default_controlled_environment_for_domain(domain)
            )

            safe_domain = re.sub(r"[^a-zA-Z0-9_-]+", "_", domain) or "domain"
            safe_friction = re.sub(r"[^a-zA-Z0-9_-]+", "_", friction_key) or "friction"
            safe_hypothesis_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", hypothesis_id) or f"hypothesis_{index + 1}"
            artifact_filename = f"{safe_domain}_{safe_friction}_{safe_hypothesis_id[-8:]}.json"
            artifact_path = (artifacts_dir / artifact_filename).resolve()

            baseline_metrics: dict[str, Any] = {metric: 0.0}
            candidate_metrics: dict[str, Any] = {metric: float(min_effect)}
            for guardrail in guardrails:
                guardrail_metric = str(guardrail.get("metric") or "").strip()
                if not guardrail_metric:
                    continue
                guardrail_value_raw = guardrail.get("value")
                try:
                    guardrail_value = float(guardrail_value_raw) if guardrail_value_raw is not None else 0.0
                except (TypeError, ValueError):
                    guardrail_value = 0.0
                baseline_metrics.setdefault(guardrail_metric, guardrail_value)
                candidate_metrics.setdefault(guardrail_metric, guardrail_value)

            artifact_payload: dict[str, Any] = {
                "environment": environment,
                "baseline_metrics": baseline_metrics,
                "candidate_metrics": candidate_metrics,
                "baseline": {"metrics": baseline_metrics},
                "candidate": {
                    "metrics": candidate_metrics,
                    "sample_size": int(target_sample_size),
                },
                "sample_size": int(target_sample_size),
                "metadata": {
                    "generated_by": "jarvis.cli.improvement.draft-experiment-jobs",
                    "template_state": "bootstrap_numeric_placeholders",
                    "hypothesis_id": hypothesis_id,
                    "domain": domain,
                    "friction_key": friction_key,
                    "success_criteria": success_criteria,
                    "target_sample_size": int(target_sample_size),
                    "guardrails": guardrails,
                    "seed_source_reason": source_reason,
                    "seed_evidence_record_ids": list(seed_evidence_record_ids),
                },
                "notes": (
                    "Drafted controlled experiment artifact with bootstrap numeric placeholders. "
                    "Replace baseline/candidate metrics with observed controlled-run values before promotion decisions."
                ),
            }

            artifact_status = "created"
            if artifact_path.exists() and not overwrite_artifacts:
                artifact_status = "existing"
                artifact_existing_count += 1
            else:
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                artifact_path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")
                artifact_created_count += 1

            if pipeline_config_path is not None:
                try:
                    job_artifact_path = Path(
                        os.path.relpath(
                            artifact_path,
                            start=pipeline_config_path.parent,
                        )
                    ).as_posix()
                except ValueError:
                    job_artifact_path = str(artifact_path)
            else:
                job_artifact_path = str(artifact_path)

            job_entry: dict[str, Any] = {
                "hypothesis_id": hypothesis_id,
                "domain": domain,
                "friction_key": friction_key,
                "artifact_path": job_artifact_path,
                "environment": environment,
                "notes": f"auto_draft:{source_reason}",
                "compare_history": True,
                "collect_debug": True,
                "auto_retest_lane": True,
                "seed_evidence_record_ids": list(seed_evidence_record_ids),
            }

            config_action = "not_requested"
            if pipeline_config_path is not None:
                domain_friction_key = f"{domain}:{friction_key}" if domain and friction_key else ""
                duplicate_reason: str | None = None
                if hypothesis_id in existing_hypothesis_ids:
                    duplicate_reason = "existing_hypothesis_id"
                elif domain_friction_key and domain_friction_key in existing_domain_friction_pairs:
                    duplicate_reason = "existing_domain_friction_key"

                if duplicate_reason is not None:
                    skipped_existing_jobs.append(
                        {
                            "index": index,
                            "hypothesis_id": hypothesis_id,
                            "domain": domain,
                            "friction_key": friction_key,
                            "reason": duplicate_reason,
                        }
                    )
                    config_action = f"skipped_{duplicate_reason}"
                else:
                    appended_jobs.append(dict(job_entry))
                    existing_hypothesis_ids.add(hypothesis_id)
                    if domain_friction_key:
                        existing_domain_friction_pairs.add(domain_friction_key)
                    config_action = "appended"

            drafts.append(
                {
                    "index": index,
                    "hypothesis_id": hypothesis_id,
                    "domain": domain,
                    "status": status_value,
                    "risk_level": hypothesis.get("risk_level"),
                    "friction_key": friction_key,
                    "artifact_path": str(artifact_path),
                    "artifact_status": artifact_status,
                    "job": job_entry,
                    "config_action": config_action,
                    "target_sample_size": int(target_sample_size),
                    "primary_metric": metric,
                    "direction": direction,
                    "min_effect": float(min_effect),
                    "guardrails": guardrails,
                    "seed_evidence_record_ids": list(seed_evidence_record_ids),
                    "source_hint": source_hint,
                }
            )

        config_output_path: Path | None = None
        if pipeline_config_path is not None:
            if bool(getattr(args, "in_place", False)):
                config_output_path = pipeline_config_path
            elif getattr(args, "write_config_path", None) is not None:
                config_output_path = _resolve_path_from_base(
                    args.write_config_path,
                    base_dir=pipeline_config_path.parent,
                )
            else:
                suffix = pipeline_config_path.suffix or ".json"
                config_output_path = pipeline_config_path.with_name(f"{pipeline_config_path.stem}.drafted{suffix}")

            merged_jobs = [dict(item) for item in existing_experiment_jobs]
            merged_jobs.extend([dict(item) for item in appended_jobs])
            updated_pipeline_payload = dict(pipeline_payload)
            updated_pipeline_payload["experiment_jobs"] = merged_jobs

            config_output_path.parent.mkdir(parents=True, exist_ok=True)
            config_output_path.write_text(json.dumps(updated_pipeline_payload, indent=2), encoding="utf-8")

        payload = {
            "generated_at": utc_now_iso(),
            "seed_report_path": str(seed_report_path) if seed_report_path is not None else None,
            "benchmark_report_path": str(benchmark_report_path) if benchmark_report_path is not None else None,
            "benchmark_target_count": len(benchmark_priority_targets),
            "benchmark_target_missing_hypothesis_count": len(benchmark_targets_without_hypothesis_id),
            "benchmark_min_opportunity": benchmark_min_opportunity,
            "pipeline_config_path": str(pipeline_config_path) if pipeline_config_path is not None else None,
            "config_output_path": str(config_output_path) if config_output_path is not None else None,
            "artifacts_dir": str(artifacts_dir),
            "domain_filter": domain_filter,
            "status_filters_requested": status_filters_requested,
            "status_filters": status_filters,
            "limit_requested": int(limit_requested),
            "limit": limit,
            "lookup_limit": lookup_limit,
            "evidence_lookup_runtime_history_path": str(evidence_runtime_history_path),
            "evidence_lookup_runtime_history_window": int(evidence_runtime_history_window),
            "evidence_lookup_runtime_history": evidence_runtime_history,
            "evidence_pressure_enabled": bool(evidence_pressure_enabled),
            "evidence_pressure_applied": bool(evidence_pressure_applied),
            "evidence_pressure_reason": evidence_pressure_reason,
            "evidence_pressure_min_priority_boost": float(evidence_pressure_min_priority_boost),
            "evidence_pressure_limit_increase": int(evidence_pressure_limit_increase),
            "evidence_pressure_statuses": evidence_pressure_statuses,
            "candidate_seed_count": len(seed_hypothesis_ids),
            "selected_hypotheses_count": len(selected_hypotheses),
            "drafted_count": len(drafts),
            "artifact_created_count": artifact_created_count,
            "artifact_existing_count": artifact_existing_count,
            "config_appended_count": len(appended_jobs),
            "config_skipped_existing_count": len(skipped_existing_jobs),
            "drafts": drafts,
            "appended_jobs": appended_jobs,
            "config_skipped_existing": skipped_existing_jobs,
            "errors": errors,
            "error_count": len(errors),
            "status": "ok" if drafts and not errors else "warning",
        }
        if not drafts and not errors:
            payload["note"] = "no_hypotheses_selected_for_drafting"

        output_path = args.output_path.resolve() if args.output_path is not None else None
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            payload["output_path"] = str(output_path)

        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )

        if (errors or not drafts) and bool(getattr(args, "strict", False)):
            raise SystemExit(2)
    finally:
        runtime.close()


def _coerce_status_preferences(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = _parse_csv_items(value)
    elif isinstance(value, (list, tuple, set)):
        values = [str(item).strip().lower() for item in value if str(item).strip()]
    else:
        values = [str(value).strip().lower()] if str(value).strip() else []

    out: list[str] = []
    for item in values:
        normalized = str(item).strip().lower()
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_record_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    out: list[str] = []
    for item in list(value or []):
        text = str(item).strip()
        if not text or text in out:
            continue
        out.append(text)
    return out


def _resolve_evidence_runtime_history_path(
    *,
    raw_value: Any,
    fallback_base_dir: Path,
    fallback_name: str = "evidence_lookup_runtime_history.jsonl",
    resolve_relative_to: Path | None = None,
) -> Path:
    if raw_value is None or not str(raw_value).strip():
        return (fallback_base_dir / fallback_name).resolve()
    base_dir = resolve_relative_to or Path.cwd()
    return _resolve_path_from_base(raw_value, base_dir=base_dir).resolve()


def _load_evidence_runtime_history_rows(history_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not history_path.exists():
        return rows
    for line_number, raw_line in enumerate(history_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = str(raw_line or "").strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        normalized = dict(row)
        normalized["_line_number"] = line_number
        rows.append(normalized)
    return rows


def _append_evidence_runtime_history_row(history_path: Path, row: dict[str, Any]) -> str | None:
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return None
    except Exception as exc:
        text = str(exc).strip()
        return text or "history_append_failed"


def _summarize_evidence_runtime_history(
    *,
    history_path: Path | None,
    window: int = 7,
) -> dict[str, Any]:
    effective_window = max(1, _coerce_int(window, default=7))
    if history_path is None:
        return {
            "status": "not_configured",
            "history_path": None,
            "history_exists": False,
            "window": int(effective_window),
            "row_count": 0,
            "recent_row_count": 0,
            "previous_row_count": 0,
            "recent_missing_total": 0,
            "previous_missing_total": 0,
            "recent_missing_avg": 0.0,
            "previous_missing_avg": 0.0,
            "recent_unresolved_runs": 0,
            "previous_unresolved_runs": 0,
            "recent_unresolved_rate": 0.0,
            "previous_unresolved_rate": 0.0,
            "missing_count_delta": 0.0,
            "unresolved_rate_delta": 0.0,
            "trend": "not_configured",
            "priority_boost": 0.0,
            "recurring_missing_record_ids": [],
            "recurring_missing_record_id_count": 0,
            "recommendation": "Runtime history path is not configured.",
        }

    resolved_history_path = history_path.resolve()
    rows = _load_evidence_runtime_history_rows(resolved_history_path)
    if not rows:
        return {
            "status": "missing_history",
            "history_path": str(resolved_history_path),
            "history_exists": bool(resolved_history_path.exists()),
            "window": int(effective_window),
            "row_count": 0,
            "recent_row_count": 0,
            "previous_row_count": 0,
            "recent_missing_total": 0,
            "previous_missing_total": 0,
            "recent_missing_avg": 0.0,
            "previous_missing_avg": 0.0,
            "recent_unresolved_runs": 0,
            "previous_unresolved_runs": 0,
            "recent_unresolved_rate": 0.0,
            "previous_unresolved_rate": 0.0,
            "missing_count_delta": 0.0,
            "unresolved_rate_delta": 0.0,
            "trend": "missing_history",
            "priority_boost": 0.0,
            "recurring_missing_record_ids": [],
            "recurring_missing_record_id_count": 0,
            "recommendation": "No evidence runtime history rows found yet; collect baseline samples first.",
        }

    recent_rows = rows[-effective_window:]
    previous_rows = rows[-(effective_window * 2) : -effective_window]

    def _missing_count_from_row(row: dict[str, Any]) -> int:
        explicit = row.get("missing_count")
        if explicit is not None:
            return max(0, _coerce_int(explicit, default=0))
        return len(_normalize_record_id_list(row.get("missing_record_ids")))

    def _is_unresolved_row(row: dict[str, Any]) -> bool:
        if _missing_count_from_row(row) > 0:
            return True
        lookup_status = str(row.get("lookup_status") or "").strip().lower()
        return lookup_status == "missing_report"

    recent_missing_total = sum(_missing_count_from_row(row) for row in recent_rows)
    previous_missing_total = sum(_missing_count_from_row(row) for row in previous_rows)
    recent_unresolved_runs = sum(1 for row in recent_rows if _is_unresolved_row(row))
    previous_unresolved_runs = sum(1 for row in previous_rows if _is_unresolved_row(row))

    recent_row_count = len(recent_rows)
    previous_row_count = len(previous_rows)
    recent_missing_avg = float(recent_missing_total) / float(max(1, recent_row_count))
    previous_missing_avg = float(previous_missing_total) / float(max(1, previous_row_count))
    recent_unresolved_rate = float(recent_unresolved_runs) / float(max(1, recent_row_count))
    previous_unresolved_rate = float(previous_unresolved_runs) / float(max(1, previous_row_count))
    missing_count_delta = recent_missing_avg - previous_missing_avg
    unresolved_rate_delta = recent_unresolved_rate - previous_unresolved_rate

    recurring_counter: Counter[str] = Counter()
    for row in recent_rows:
        for record_id in _normalize_record_id_list(row.get("missing_record_ids")):
            recurring_counter[record_id] += 1
    recurring_missing_record_ids = [record_id for record_id, _ in recurring_counter.most_common(5)]

    if recent_unresolved_runs <= 0 and recent_missing_total <= 0:
        trend = "clear"
    elif previous_row_count <= 0:
        trend = "insufficient_history"
    elif unresolved_rate_delta >= 0.15 or missing_count_delta >= 0.5:
        trend = "worsening"
    elif unresolved_rate_delta <= -0.15 or missing_count_delta <= -0.5:
        trend = "improving"
    else:
        trend = "persistent"

    priority_boost = 0.0
    if trend == "worsening":
        priority_boost = min(
            1.0,
            0.55 + (recent_unresolved_rate * 0.3) + min(0.2, max(0.0, missing_count_delta) * 0.15),
        )
    elif trend == "persistent":
        priority_boost = min(
            0.75,
            0.35 + (recent_unresolved_rate * 0.25) + min(0.15, recent_missing_avg * 0.05),
        )
    elif trend == "insufficient_history" and recent_unresolved_runs > 0:
        priority_boost = min(
            0.55,
            0.2 + (recent_unresolved_rate * 0.2) + min(0.1, recent_missing_avg * 0.05),
        )

    if trend == "worsening":
        recommendation = "Escalate evidence lookup remediation; unresolved record IDs are worsening."
    elif trend == "persistent":
        recommendation = "Prioritize recurring unresolved evidence IDs and close repeated source lookup gaps."
    elif trend == "insufficient_history" and recent_unresolved_runs > 0:
        recommendation = "Collect more runtime samples; unresolved evidence IDs already indicate emerging risk."
    elif trend == "improving":
        recommendation = "Evidence lookup trend is improving; keep remediation momentum."
    else:
        recommendation = "Evidence lookup runtime trend is clear; continue monitoring."

    return {
        "status": "ok",
        "history_path": str(resolved_history_path),
        "history_exists": True,
        "window": int(effective_window),
        "row_count": len(rows),
        "recent_row_count": recent_row_count,
        "previous_row_count": previous_row_count,
        "recent_missing_total": int(recent_missing_total),
        "previous_missing_total": int(previous_missing_total),
        "recent_missing_avg": round(float(recent_missing_avg), 4),
        "previous_missing_avg": round(float(previous_missing_avg), 4),
        "recent_unresolved_runs": int(recent_unresolved_runs),
        "previous_unresolved_runs": int(previous_unresolved_runs),
        "recent_unresolved_rate": round(float(recent_unresolved_rate), 4),
        "previous_unresolved_rate": round(float(previous_unresolved_rate), 4),
        "missing_count_delta": round(float(missing_count_delta), 4),
        "unresolved_rate_delta": round(float(unresolved_rate_delta), 4),
        "trend": trend,
        "priority_boost": round(float(priority_boost), 4),
        "recurring_missing_record_ids": recurring_missing_record_ids,
        "recurring_missing_record_id_count": len(recurring_missing_record_ids),
        "recommendation": recommendation,
    }


def _resolve_benchmark_stale_fallback_history_path(
    *,
    raw_value: Any,
    fallback_base_dir: Path,
) -> Path:
    return _resolve_evidence_runtime_history_path(
        raw_value=raw_value,
        fallback_base_dir=fallback_base_dir,
        fallback_name="benchmark_stale_fallback_history.jsonl",
    )


def _summarize_benchmark_stale_fallback_history(
    *,
    history_path: Path | None,
    window: int = 7,
) -> dict[str, Any]:
    effective_window = max(1, _coerce_int(window, default=7))
    if history_path is None:
        return {
            "status": "not_configured",
            "history_path": None,
            "history_exists": False,
            "window": int(effective_window),
            "row_count": 0,
            "recent_row_count": 0,
            "previous_row_count": 0,
            "recent_stale_runs": 0,
            "previous_stale_runs": 0,
            "recent_stale_rate": 0.0,
            "previous_stale_rate": 0.0,
            "stale_run_delta": 0,
            "stale_rate_delta": 0.0,
            "trend": "not_configured",
            "priority_boost": 0.0,
            "recurring_stale_reasons": [],
            "recurring_stale_reason_count": 0,
            "recommendation": "Benchmark stale fallback history path is not configured.",
        }

    resolved_history_path = history_path.resolve()
    rows = _load_evidence_runtime_history_rows(resolved_history_path)
    if not rows:
        return {
            "status": "missing_history",
            "history_path": str(resolved_history_path),
            "history_exists": bool(resolved_history_path.exists()),
            "window": int(effective_window),
            "row_count": 0,
            "recent_row_count": 0,
            "previous_row_count": 0,
            "recent_stale_runs": 0,
            "previous_stale_runs": 0,
            "recent_stale_rate": 0.0,
            "previous_stale_rate": 0.0,
            "stale_run_delta": 0,
            "stale_rate_delta": 0.0,
            "trend": "missing_history",
            "priority_boost": 0.0,
            "recurring_stale_reasons": [],
            "recurring_stale_reason_count": 0,
            "recommendation": "No benchmark stale fallback history rows found yet; collect baseline samples first.",
        }

    def _is_stale_fallback_row(row: dict[str, Any]) -> bool:
        if row.get("benchmark_stale_fallback") is not None:
            return _coerce_bool(row.get("benchmark_stale_fallback"), default=False)
        if row.get("stale_fallback") is not None:
            return _coerce_bool(row.get("stale_fallback"), default=False)
        return False

    recent_rows = rows[-effective_window:]
    previous_rows = rows[-(effective_window * 2) : -effective_window]
    recent_stale_runs = sum(1 for row in recent_rows if _is_stale_fallback_row(row))
    previous_stale_runs = sum(1 for row in previous_rows if _is_stale_fallback_row(row))
    recent_row_count = len(recent_rows)
    previous_row_count = len(previous_rows)
    recent_stale_rate = float(recent_stale_runs) / float(max(1, recent_row_count))
    previous_stale_rate = float(previous_stale_runs) / float(max(1, previous_row_count))
    stale_run_delta = int(recent_stale_runs - previous_stale_runs)
    stale_rate_delta = float(recent_stale_rate - previous_stale_rate)

    recurring_reason_counter: Counter[str] = Counter()
    for row in recent_rows:
        if not _is_stale_fallback_row(row):
            continue
        reason = str(row.get("benchmark_stale_reason") or row.get("stale_reason") or "").strip().lower()
        if reason and reason != "none":
            recurring_reason_counter[reason] += 1
    recurring_stale_reasons = [reason for reason, _ in recurring_reason_counter.most_common(5)]

    if recent_stale_runs <= 0:
        trend = "clear"
    elif previous_row_count <= 0:
        trend = "insufficient_history"
    elif stale_rate_delta >= 0.2 or stale_run_delta >= 1:
        trend = "worsening"
    elif stale_rate_delta <= -0.2 or stale_run_delta <= -1:
        trend = "improving"
    else:
        trend = "persistent"

    priority_boost = 0.0
    if trend == "worsening":
        priority_boost = min(1.0, 0.55 + (recent_stale_rate * 0.35) + min(0.1, float(recent_stale_runs) * 0.03))
    elif trend == "persistent":
        priority_boost = min(0.8, 0.35 + (recent_stale_rate * 0.3))
    elif trend == "insufficient_history" and recent_stale_runs > 0:
        priority_boost = min(0.55, 0.2 + (recent_stale_rate * 0.2))

    if trend == "worsening":
        recommendation = "Escalate benchmark stale fallback remediation; repeated stale skips are increasing."
    elif trend == "persistent":
        recommendation = "Stale benchmark fallback is recurring; prioritize automated benchmark freshness checks."
    elif trend == "insufficient_history" and recent_stale_runs > 0:
        recommendation = "Collect additional runtime samples; stale benchmark fallback is already present."
    elif trend == "improving":
        recommendation = "Benchmark stale fallback trend is improving; continue freshness remediation."
    else:
        recommendation = "Benchmark stale fallback trend is clear; continue monitoring."

    return {
        "status": "ok",
        "history_path": str(resolved_history_path),
        "history_exists": True,
        "window": int(effective_window),
        "row_count": len(rows),
        "recent_row_count": int(recent_row_count),
        "previous_row_count": int(previous_row_count),
        "recent_stale_runs": int(recent_stale_runs),
        "previous_stale_runs": int(previous_stale_runs),
        "recent_stale_rate": round(float(recent_stale_rate), 4),
        "previous_stale_rate": round(float(previous_stale_rate), 4),
        "stale_run_delta": int(stale_run_delta),
        "stale_rate_delta": round(float(stale_rate_delta), 4),
        "trend": trend,
        "priority_boost": round(float(priority_boost), 4),
        "recurring_stale_reasons": recurring_stale_reasons,
        "recurring_stale_reason_count": len(recurring_stale_reasons),
        "recommendation": recommendation,
    }


def _inspect_benchmark_report_recency(report_path: Path) -> dict[str, Any]:
    resolved_path = report_path.resolve()
    exists = bool(resolved_path.exists())
    generated_at_dt: datetime | None = None
    generated_at_source = "none"
    parse_error: str | None = None
    if exists:
        try:
            loaded = json.loads(resolved_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                candidate = loaded.get("generated_at")
                if candidate is not None and str(candidate).strip():
                    parsed = _parse_timestamp_value(candidate)
                    if parsed is not None:
                        generated_at_dt = parsed
                        generated_at_source = "payload_generated_at"
            else:
                parse_error = "invalid_benchmark_report:expected_json_object"
        except Exception as exc:
            parse_error = str(exc).strip() or "benchmark_report_parse_failed"
    if exists and generated_at_dt is None:
        try:
            generated_at_dt = datetime.fromtimestamp(resolved_path.stat().st_mtime, tz=timezone.utc)
            generated_at_source = "file_mtime"
        except Exception as exc:
            if parse_error is None:
                parse_error = str(exc).strip() or "benchmark_report_stat_failed"
    now_dt = datetime.now(timezone.utc)
    age_hours: float | None = None
    if generated_at_dt is not None:
        age_seconds = max(0.0, float((now_dt - generated_at_dt).total_seconds()))
        age_hours = round(age_seconds / 3600.0, 4)
    return {
        "path": str(resolved_path),
        "exists": exists,
        "generated_at": (
            generated_at_dt.astimezone(timezone.utc).isoformat()
            if generated_at_dt is not None
            else None
        ),
        "generated_at_source": generated_at_source,
        "age_hours": age_hours,
        "parse_error": parse_error,
    }


def _build_evidence_lookup_refs(seed_evidence_record_ids: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for record_id in _normalize_record_id_list(seed_evidence_record_ids):
        refs.append(
            {
                "lookup_type": "source_record_id",
                "record_id": record_id,
                "lookup_key": f"record_id:{record_id}",
            }
        )
    return refs


def _resolve_pipeline_hypothesis_id(
    *,
    runtime: JarvisRuntime,
    raw_job: dict[str, Any],
    defaults: dict[str, Any],
) -> tuple[str | None, dict[str, Any], str | None]:
    explicit_hypothesis_id = str(raw_job.get("hypothesis_id") or "").strip()
    if explicit_hypothesis_id:
        return (
            explicit_hypothesis_id,
            {
                "strategy": "explicit_hypothesis_id",
                "hypothesis_id": explicit_hypothesis_id,
            },
            None,
        )

    selector_domain = str(raw_job.get("domain") or defaults.get("domain") or "").strip().lower()
    selector_friction_key_raw = str(raw_job.get("friction_key") or "").strip()
    selector_friction_key = _normalize_friction_key(selector_friction_key_raw)
    selector_title_contains = str(raw_job.get("title_contains") or "").strip().lower()
    selector_status = str(raw_job.get("hypothesis_status") or "").strip().lower()

    if not selector_domain:
        return None, {}, "missing_hypothesis_selector_domain"
    if not selector_friction_key and not selector_title_contains:
        return None, {}, "missing_hypothesis_selector_fields"

    lookup_limit_raw = raw_job.get("hypothesis_lookup_limit")
    if lookup_limit_raw is None:
        lookup_limit_raw = defaults.get("hypothesis_lookup_limit", 200)
    try:
        lookup_limit = max(1, int(lookup_limit_raw))
    except (TypeError, ValueError):
        lookup_limit = 200

    candidates = runtime.list_hypotheses(
        domain=selector_domain,
        status=None,
        limit=lookup_limit,
    )
    filtered = [item for item in candidates if isinstance(item, dict)]
    if selector_friction_key:
        filtered = [
            item
            for item in filtered
            if _normalize_friction_key(item.get("friction_key")) == selector_friction_key
        ]
    if selector_title_contains:
        filtered = [
            item
            for item in filtered
            if selector_title_contains in str(item.get("title") or "").strip().lower()
        ]
    if selector_status:
        filtered = [
            item
            for item in filtered
            if str(item.get("status") or "").strip().lower() == selector_status
        ]

    preferred_statuses = _coerce_status_preferences(
        raw_job.get("preferred_statuses")
        if raw_job.get("preferred_statuses") is not None
        else defaults.get("preferred_statuses")
    )
    if preferred_statuses:
        status_rank = {status: idx for idx, status in enumerate(preferred_statuses)}
        fallback_rank = len(status_rank) + 1
        filtered = sorted(
            filtered,
            key=lambda item: status_rank.get(str(item.get("status") or "").strip().lower(), fallback_rank),
        )

    if not filtered:
        return (
            None,
            {
                "strategy": "selector",
                "domain": selector_domain,
                "friction_key": selector_friction_key or None,
                "title_contains": selector_title_contains or None,
                "hypothesis_status": selector_status or None,
                "candidate_count": 0,
                "preferred_statuses": preferred_statuses,
            },
            "hypothesis_selector_no_match",
        )

    selected = filtered[0]
    resolved_hypothesis_id = str(selected.get("hypothesis_id") or "").strip()
    if not resolved_hypothesis_id:
        return None, {}, "resolved_hypothesis_missing_id"
    return (
        resolved_hypothesis_id,
        {
            "strategy": "selector",
            "domain": selector_domain,
            "friction_key": selector_friction_key or None,
            "title_contains": selector_title_contains or None,
            "hypothesis_status": selector_status or None,
            "candidate_count": len(filtered),
            "preferred_statuses": preferred_statuses,
            "selected": {
                "hypothesis_id": resolved_hypothesis_id,
                "title": str(selected.get("title") or ""),
                "status": str(selected.get("status") or ""),
                "friction_key": selected.get("friction_key"),
            },
        },
        None,
    )


def cmd_improvement_pull_feeds(args: argparse.Namespace) -> None:
    config_path = args.config_path.resolve()
    puller = FeedbackFeedPuller()

    feed_names = _parse_csv_items(getattr(args, "feed_names", None))
    allow_missing = bool(getattr(args, "allow_missing", False))
    result = puller.pull_from_config(
        config_path=config_path,
        feed_names=feed_names or None,
        allow_missing=True,
        timeout_seconds=float(getattr(args, "timeout_seconds", 20.0) or 20.0),
    )

    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        **result,
        "allow_missing": allow_missing,
    }
    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if int(result.get("error_count") or 0) > 0 and (allow_missing is False or bool(getattr(args, "strict", False))):
        raise SystemExit(2)


def cmd_improvement_daily_pipeline(args: argparse.Namespace) -> None:
    config_path = args.config_path.resolve()
    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_pipeline_config:expected_json_object")

    defaults = dict(loaded.get("defaults") or {}) if isinstance(loaded.get("defaults"), dict) else {}
    feed_jobs = list(loaded.get("feed_jobs") or loaded.get("feeds") or [])
    feedback_jobs = list(loaded.get("feedback_jobs") or [])
    experiment_jobs = list(loaded.get("experiment_jobs") or [])
    allow_missing_inputs = bool(
        args.allow_missing_inputs
        or bool(defaults.get("allow_missing_inputs"))
    )

    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        feed_runs: list[dict[str, Any]] = []
        feedback_runs: list[dict[str, Any]] = []
        experiment_runs: list[dict[str, Any]] = []
        retest_runs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        feed_puller = FeedbackFeedPuller()

        for index, raw_job in enumerate(feed_jobs):
            if not isinstance(raw_job, dict):
                errors.append({"job_type": "feed_pull", "index": index, "error": "job_not_object"})
                continue
            enabled = raw_job.get("enabled")
            if enabled is False:
                feed_runs.append(
                    {
                        "index": index,
                        "name": str(raw_job.get("name") or f"feed_{index}"),
                        "status": "skipped_disabled",
                    }
                )
                continue

            try:
                result = feed_puller.pull_feed(
                    config_path=config_path,
                    feed=raw_job,
                    timeout_seconds=float(raw_job.get("timeout_seconds") or defaults.get("timeout_seconds") or 20.0),
                )
                feed_runs.append(
                    {
                        "index": index,
                        "status": "ok",
                        **dict(result),
                    }
                )
            except Exception as exc:
                if allow_missing_inputs:
                    feed_runs.append(
                        {
                            "index": index,
                            "name": str(raw_job.get("name") or f"feed_{index}"),
                            "status": "skipped_error",
                            "error": str(exc),
                        }
                    )
                    continue
                errors.append(
                    {
                        "job_type": "feed_pull",
                        "index": index,
                        "error": str(exc),
                        "name": str(raw_job.get("name") or f"feed_{index}"),
                    }
                )

        for index, raw_job in enumerate(feedback_jobs):
            if not isinstance(raw_job, dict):
                errors.append({"job_type": "feedback", "index": index, "error": "job_not_object"})
                continue
            domain = str(raw_job.get("domain") or defaults.get("domain") or "").strip().lower()
            source = str(raw_job.get("source") or defaults.get("source") or "").strip().lower()
            input_path_raw = raw_job.get("input_path")
            if not domain or not source or not input_path_raw:
                errors.append(
                    {
                        "job_type": "feedback",
                        "index": index,
                        "error": "missing_required_fields",
                        "domain": domain,
                        "source": source,
                    }
                )
                continue
            input_path = _resolve_pipeline_path(input_path_raw, config_path=config_path)
            if not input_path.exists():
                if allow_missing_inputs:
                    feedback_runs.append(
                        {
                            "index": index,
                            "domain": domain,
                            "source": source,
                            "input_path": str(input_path),
                            "status": "skipped_missing_input",
                        }
                    )
                    continue
                errors.append(
                    {
                        "job_type": "feedback",
                        "index": index,
                        "error": "input_path_not_found",
                        "input_path": str(input_path),
                    }
                )
                continue

            try:
                run = runtime.run_friction_hypothesis_cycle_from_file(
                    domain=domain,
                    source=source,
                    input_path=str(input_path),
                    input_format=raw_job.get("input_format"),
                    default_segment=str(raw_job.get("default_segment") or defaults.get("default_segment") or "general"),
                    default_severity=raw_job.get("default_severity", defaults.get("default_severity", 3.0)),
                    default_frustration_score=raw_job.get(
                        "default_frustration_score",
                        defaults.get("default_frustration_score"),
                    ),
                    status=str(raw_job.get("status") or defaults.get("status") or "open"),
                    metadata={
                        "invoked_by": "jarvis.cli.improvement.daily-pipeline",
                        "job_index": index,
                    },
                    min_cluster_count=max(
                        1,
                        int(raw_job.get("min_cluster_count") or defaults.get("min_cluster_count") or 2),
                    ),
                    proposal_limit=max(1, int(raw_job.get("proposal_limit") or defaults.get("proposal_limit") or 5)),
                    auto_register=bool(
                        raw_job.get("auto_register")
                        if raw_job.get("auto_register") is not None
                        else defaults.get("auto_register", True)
                    ),
                    owner=str(raw_job.get("owner") or defaults.get("owner") or "operator"),
                )
                report = runtime.build_hypothesis_inbox_report(
                    domain=domain,
                    cluster_min_count=max(
                        1,
                        int(raw_job.get("report_cluster_min_count") or defaults.get("report_cluster_min_count") or 1),
                    ),
                    cluster_limit=max(
                        1,
                        int(raw_job.get("report_cluster_limit") or defaults.get("report_cluster_limit") or 10),
                    ),
                    hypothesis_limit=max(
                        1,
                        int(raw_job.get("report_hypothesis_limit") or defaults.get("report_hypothesis_limit") or 30),
                    ),
                    experiment_limit=max(
                        1,
                        int(raw_job.get("report_experiment_limit") or defaults.get("report_experiment_limit") or 50),
                    ),
                    queue_limit=max(
                        1,
                        int(raw_job.get("report_queue_limit") or defaults.get("report_queue_limit") or 20),
                    ),
                )
                feedback_entry = {
                    "index": index,
                    "domain": domain,
                    "source": source,
                    "input_path": str(input_path),
                    "status": "ok",
                    "cycle": {
                        "proposal_count": int((run.get("cycle") or {}).get("proposal_count") or 0),
                        "created_count": int((run.get("cycle") or {}).get("created_count") or 0),
                        "skipped_existing_count": int((run.get("cycle") or {}).get("skipped_existing_count") or 0),
                        "ingested_count": int((run.get("ingest") or {}).get("ingested_count") or 0),
                        "skipped_count": int((run.get("ingest") or {}).get("skipped_count") or 0),
                    },
                    "report": report,
                }
                report_path_raw = raw_job.get("report_path")
                if report_path_raw:
                    report_path = _resolve_pipeline_path(report_path_raw, config_path=config_path)
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    feedback_entry["report_path"] = str(report_path)
                feedback_runs.append(feedback_entry)
            except Exception as exc:
                errors.append(
                    {
                        "job_type": "feedback",
                        "index": index,
                        "error": str(exc),
                        "domain": domain,
                        "source": source,
                        "input_path": str(input_path),
                    }
                )
                continue

        for index, raw_job in enumerate(experiment_jobs):
            if not isinstance(raw_job, dict):
                errors.append({"job_type": "experiment", "index": index, "error": "job_not_object"})
                continue
            hypothesis_id, resolution_details, hypothesis_error = _resolve_pipeline_hypothesis_id(
                runtime=runtime,
                raw_job=raw_job,
                defaults=defaults,
            )
            if hypothesis_error:
                errors.append(
                    {
                        "job_type": "experiment",
                        "index": index,
                        "error": hypothesis_error,
                        "resolution": resolution_details,
                    }
                )
                continue
            artifact_raw = raw_job.get("artifact_path")
            if not hypothesis_id or not artifact_raw:
                errors.append(
                    {
                        "job_type": "experiment",
                        "index": index,
                        "error": "missing_required_fields",
                        "hypothesis_id": hypothesis_id,
                    }
                )
                continue
            artifact_path = _resolve_pipeline_path(artifact_raw, config_path=config_path)
            if not artifact_path.exists():
                if allow_missing_inputs:
                    experiment_runs.append(
                        {
                            "index": index,
                            "hypothesis_id": hypothesis_id,
                            "artifact_path": str(artifact_path),
                            "status": "skipped_missing_artifact",
                        }
                    )
                    continue
                errors.append(
                    {
                        "job_type": "experiment",
                        "index": index,
                        "error": "artifact_path_not_found",
                        "artifact_path": str(artifact_path),
                    }
                )
                continue
            try:
                hypothesis_snapshot = runtime.hypothesis_lab.get_hypothesis(hypothesis_id)
                hypothesis_metadata = (
                    dict(hypothesis_snapshot.get("metadata") or {})
                    if isinstance(hypothesis_snapshot, dict) and isinstance(hypothesis_snapshot.get("metadata"), dict)
                    else {}
                )
                job_seed_evidence_record_ids = _normalize_record_id_list(raw_job.get("seed_evidence_record_ids"))
                result = runtime.run_hypothesis_experiment_from_artifact(
                    hypothesis_id=hypothesis_id,
                    artifact_path=str(artifact_path),
                    environment=(
                        str(raw_job.get("environment")).strip()
                        if raw_job.get("environment") is not None
                        else None
                    ),
                    source_trace_id=(
                        str(raw_job.get("source_trace_id")).strip()
                        if raw_job.get("source_trace_id") is not None
                        else None
                    ),
                    notes=(
                        str(raw_job.get("notes")).strip()
                        if raw_job.get("notes") is not None
                        else None
                    ),
                )
                evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
                run_id = str(result.get("run_id") or "").strip()
                artifact_block = dict(result.get("artifact") or {}) if isinstance(result.get("artifact"), dict) else {}
                artifact_metadata = (
                    dict(artifact_block.get("metadata") or {})
                    if isinstance(artifact_block.get("metadata"), dict)
                    else {}
                )
                seed_evidence_record_ids: list[str] = []
                for raw_ids in (
                    hypothesis_metadata.get("seed_evidence_record_ids"),
                    artifact_metadata.get("seed_evidence_record_ids"),
                    job_seed_evidence_record_ids,
                ):
                    for item in _normalize_record_id_list(raw_ids):
                        if item in seed_evidence_record_ids:
                            continue
                        seed_evidence_record_ids.append(item)
                debug_entry: dict[str, Any] = {}
                compare_entry: dict[str, Any] = {}
                retest_entry: dict[str, Any] = {}
                collect_debug = _coerce_bool(
                    raw_job.get("collect_debug")
                    if raw_job.get("collect_debug") is not None
                    else defaults.get("collect_experiment_debug"),
                    default=False,
                )
                include_decision_timeline = _coerce_bool(
                    raw_job.get("include_decision_timeline")
                    if raw_job.get("include_decision_timeline") is not None
                    else defaults.get("include_decision_timeline"),
                    default=True,
                )
                debug_report_path_raw = raw_job.get("debug_report_path")
                debug_output_dir_raw = (
                    raw_job.get("debug_output_dir")
                    if raw_job.get("debug_output_dir") is not None
                    else defaults.get("experiment_debug_output_dir")
                )
                should_emit_debug = bool(collect_debug or debug_report_path_raw or debug_output_dir_raw)
                if should_emit_debug and run_id:
                    try:
                        debug_report = runtime.debug_hypothesis_experiment(
                            run_id=run_id,
                            include_decision_timeline=include_decision_timeline,
                        )
                        failed_checks = list(debug_report.get("failed_checks") or [])
                        root_cause_hints = [
                            str(item).strip()
                            for item in list(debug_report.get("root_cause_hints") or [])
                            if str(item).strip()
                        ]
                        debug_entry.update(
                            {
                                "failed_checks_count": len(failed_checks),
                                "root_cause_hints": root_cause_hints,
                                "debug_found": bool(debug_report.get("found")),
                            }
                        )

                        debug_report_path: Path | None = None
                        if debug_report_path_raw:
                            debug_report_path = _resolve_pipeline_path(debug_report_path_raw, config_path=config_path)
                        elif debug_output_dir_raw:
                            debug_output_dir = _resolve_pipeline_path(debug_output_dir_raw, config_path=config_path)
                            safe_run_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", run_id) or f"experiment_{index}"
                            debug_report_path = (debug_output_dir / f"{safe_run_id}.json").resolve()

                        if debug_report_path is not None:
                            debug_report_path.parent.mkdir(parents=True, exist_ok=True)
                            debug_report_path.write_text(json.dumps(debug_report, indent=2), encoding="utf-8")
                            debug_entry["debug_report_path"] = str(debug_report_path)
                    except Exception as debug_exc:
                        debug_entry["debug_error"] = str(debug_exc)
                        errors.append(
                            {
                                "job_type": "experiment_debug",
                                "index": index,
                                "error": str(debug_exc),
                                "run_id": run_id,
                            }
                        )

                compare_history = _coerce_bool(
                    raw_job.get("compare_history")
                    if raw_job.get("compare_history") is not None
                    else defaults.get("compare_experiment_history"),
                    default=True,
                )
                if compare_history and run_id:
                    try:
                        compare_entry = runtime.compare_hypothesis_runs(
                            hypothesis_id=hypothesis_id,
                            current_run_id=run_id,
                            previous_run_id=(
                                str(raw_job.get("previous_run_id")).strip()
                                if raw_job.get("previous_run_id") is not None
                                else None
                            ),
                            limit=max(
                                2,
                                _coerce_int(
                                    raw_job.get("comparison_limit")
                                    if raw_job.get("comparison_limit") is not None
                                    else defaults.get("comparison_limit"),
                                    default=25,
                                ),
                            ),
                        )
                    except Exception as compare_exc:
                        errors.append(
                            {
                                "job_type": "experiment_compare",
                                "index": index,
                                "error": str(compare_exc),
                                "run_id": run_id,
                                "hypothesis_id": hypothesis_id,
                            }
                        )

                auto_retest_lane = _coerce_bool(
                    raw_job.get("auto_retest_lane")
                    if raw_job.get("auto_retest_lane") is not None
                    else defaults.get("auto_retest_lane"),
                    default=False,
                )
                verdict = str(evaluation.get("verdict") or "").strip().lower()
                if auto_retest_lane and run_id and verdict in {"blocked_guardrail", "insufficient_data"}:
                    try:
                        retest_entry = runtime.queue_hypothesis_retest_from_run(
                            run_id=run_id,
                            insufficient_sample_multiplier=_coerce_float(
                                raw_job.get("insufficient_sample_multiplier")
                                if raw_job.get("insufficient_sample_multiplier") is not None
                                else defaults.get("insufficient_sample_multiplier"),
                                default=1.5,
                            ),
                            guardrail_sample_multiplier=_coerce_float(
                                raw_job.get("guardrail_sample_multiplier")
                                if raw_job.get("guardrail_sample_multiplier") is not None
                                else defaults.get("guardrail_sample_multiplier"),
                                default=1.1,
                            ),
                            min_sample_increment=max(
                                0,
                                _coerce_int(
                                    raw_job.get("min_sample_increment")
                                    if raw_job.get("min_sample_increment") is not None
                                    else defaults.get("min_sample_increment"),
                                    default=50,
                                ),
                            ),
                            guardrail_safety_factor=_coerce_float(
                                raw_job.get("guardrail_safety_factor")
                                if raw_job.get("guardrail_safety_factor") is not None
                                else defaults.get("guardrail_safety_factor"),
                                default=0.9,
                            ),
                            notes=(
                                str(raw_job.get("retest_notes")).strip()
                                if raw_job.get("retest_notes") is not None
                                else None
                            ),
                        )
                        if retest_entry:
                            retest_entry = {
                                **dict(retest_entry),
                                "seed_evidence_record_ids": list(seed_evidence_record_ids),
                            }
                            retest_runs.append(
                                {
                                    "index": index,
                                    **dict(retest_entry),
                                    "seed_evidence_record_ids": list(seed_evidence_record_ids),
                                }
                            )
                    except Exception as retest_exc:
                        errors.append(
                            {
                                "job_type": "experiment_retest",
                                "index": index,
                                "error": str(retest_exc),
                                "run_id": run_id,
                                "hypothesis_id": hypothesis_id,
                                "verdict": verdict,
                            }
                        )

                experiment_runs.append(
                    {
                        "index": index,
                        "hypothesis_id": hypothesis_id,
                        "artifact_path": str(artifact_path),
                        "status": "ok",
                        "run_id": run_id,
                        "hypothesis_status": result.get("hypothesis_status"),
                        "verdict": evaluation.get("verdict"),
                        "resolution": resolution_details,
                        "seed_evidence_record_ids": list(seed_evidence_record_ids),
                        "side_by_side": compare_entry or None,
                        "retest": retest_entry or None,
                        **debug_entry,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "job_type": "experiment",
                        "index": index,
                        "error": str(exc),
                        "hypothesis_id": hypothesis_id,
                        "artifact_path": str(artifact_path),
                        "resolution": resolution_details,
                    }
                )
                continue

        payload = {
            "generated_at": utc_now_iso(),
            "config_path": str(config_path),
            "feed_jobs_count": len(feed_jobs),
            "feed_runs_count": len(feed_runs),
            "feedback_jobs_count": len(feedback_jobs),
            "feedback_runs_count": len(feedback_runs),
            "experiment_jobs_count": len(experiment_jobs),
            "experiment_runs_count": len(experiment_runs),
            "retest_runs_count": len(retest_runs),
            "error_count": len(errors),
            "feed_runs": feed_runs,
            "feedback_runs": feedback_runs,
            "experiment_runs": experiment_runs,
            "retest_runs": retest_runs,
            "errors": errors,
            "status": "ok" if not errors else "warning",
        }

        output_path = (
            args.output_path.resolve()
            if args.output_path is not None
            else (
                _resolve_pipeline_path(loaded.get("output_path"), config_path=config_path)
                if loaded.get("output_path")
                else None
            )
        )
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            payload["output_path"] = str(output_path)

        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )
        if errors and bool(getattr(args, "strict", False)):
            raise SystemExit(2)
    finally:
        runtime.close()


def cmd_improvement_execute_retests(args: argparse.Namespace) -> None:
    pipeline_report_path = args.pipeline_report_path.resolve()
    loaded = json.loads(pipeline_report_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_retest_report:expected_json_object")

    raw_retest_runs = list(loaded.get("retest_runs") or [])
    normalized_jobs: list[dict[str, Any]] = []
    for index, item in enumerate(raw_retest_runs):
        if not isinstance(item, dict):
            continue
        normalized_jobs.append(
            {
                "index": index,
                **dict(item),
                "seed_evidence_record_ids": _normalize_record_id_list(item.get("seed_evidence_record_ids")),
            }
        )
    if not normalized_jobs:
        for index, run in enumerate(list(loaded.get("experiment_runs") or [])):
            if not isinstance(run, dict):
                continue
            retest = run.get("retest")
            if not isinstance(retest, dict):
                continue
            if not bool(retest.get("queued")):
                continue
            merged_seed_evidence_ids: list[str] = []
            for raw_ids in (run.get("seed_evidence_record_ids"), retest.get("seed_evidence_record_ids")):
                for item in _normalize_record_id_list(raw_ids):
                    if item in merged_seed_evidence_ids:
                        continue
                    merged_seed_evidence_ids.append(item)
            normalized_jobs.append(
                {
                    "index": index,
                    **dict(retest),
                    "seed_evidence_record_ids": merged_seed_evidence_ids,
                }
            )

    max_runs = None
    if args.max_runs is not None:
        max_runs = max(0, int(args.max_runs))
    selected_jobs = normalized_jobs[:max_runs] if max_runs is not None else normalized_jobs
    allow_missing_jobs = bool(getattr(args, "allow_missing_jobs", False))

    artifact_dir: Path | None = None
    if args.artifact_dir is not None:
        raw_artifact_dir = Path(str(args.artifact_dir)).expanduser()
        if raw_artifact_dir.is_absolute():
            artifact_dir = raw_artifact_dir.resolve()
        else:
            artifact_dir = (pipeline_report_path.parent / raw_artifact_dir).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)

    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        runs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for index, job in enumerate(selected_jobs):
            hypothesis_id = str(job.get("hypothesis_id") or "").strip()
            trigger_run_id = str(job.get("run_id") or job.get("trigger_run_id") or "").strip()
            seed_evidence_record_ids = _normalize_record_id_list(job.get("seed_evidence_record_ids"))
            if not hypothesis_id:
                row = {
                    "index": index,
                    "error": "missing_hypothesis_id",
                    "job": job,
                }
                if allow_missing_jobs:
                    runs.append(
                        {
                            "index": index,
                            "status": "skipped_missing_hypothesis_id",
                            "job": job,
                        }
                    )
                    continue
                errors.append(row)
                continue
            if not trigger_run_id and not allow_missing_jobs:
                errors.append(
                    {
                        "index": index,
                        "error": "missing_trigger_run_id",
                        "hypothesis_id": hypothesis_id,
                        "job": job,
                    }
                )
                continue

            notes_prefix = str(args.notes_prefix or "").strip()
            notes = (
                f"{notes_prefix} | pipeline_report={pipeline_report_path.name} | job_index={index}"
                if notes_prefix
                else f"pipeline_report={pipeline_report_path.name} | job_index={index}"
            )
            try:
                execution = runtime.run_hypothesis_retest(
                    hypothesis_id=hypothesis_id,
                    trigger_run_id=(trigger_run_id or None),
                    environment=(
                        str(args.environment).strip()
                        if getattr(args, "environment", None) is not None
                        else None
                    ),
                    notes=notes,
                )
                artifact_payload = (
                    dict(execution.get("artifact_payload") or {})
                    if isinstance(execution.get("artifact_payload"), dict)
                    else {}
                )
                if seed_evidence_record_ids:
                    artifact_metadata = (
                        dict(artifact_payload.get("metadata") or {})
                        if isinstance(artifact_payload.get("metadata"), dict)
                        else {}
                    )
                    artifact_metadata["seed_evidence_record_ids"] = list(seed_evidence_record_ids)
                    artifact_payload["metadata"] = artifact_metadata
                artifact_path: Path | None = None
                if artifact_dir is not None:
                    safe_hypothesis = re.sub(r"[^a-zA-Z0-9_-]+", "_", hypothesis_id) or "hypothesis"
                    safe_trigger = re.sub(r"[^a-zA-Z0-9_-]+", "_", (trigger_run_id or "latest")) or "latest"
                    artifact_path = (artifact_dir / f"{safe_hypothesis}_{safe_trigger}_retest_artifact.json").resolve()
                    artifact_path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")

                result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
                evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
                runs.append(
                    {
                        "index": index,
                        "status": "ok",
                        "hypothesis_id": hypothesis_id,
                        "trigger_run_id": execution.get("trigger_run_id") or trigger_run_id or None,
                        "run_id": execution.get("run_id"),
                        "hypothesis_status": execution.get("hypothesis_status") or result.get("hypothesis_status"),
                        "verdict": evaluation.get("verdict"),
                        "side_by_side": execution.get("side_by_side"),
                        "retest_plan": execution.get("retest_plan"),
                        "seed_evidence_record_ids": list(seed_evidence_record_ids),
                        "artifact_path": str(artifact_path) if artifact_path is not None else None,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "index": index,
                        "error": str(exc),
                        "hypothesis_id": hypothesis_id,
                        "trigger_run_id": trigger_run_id or None,
                    }
                )

        payload = {
            "generated_at": utc_now_iso(),
            "pipeline_report_path": str(pipeline_report_path),
            "source_retest_runs_count": len(normalized_jobs),
            "selected_jobs_count": len(selected_jobs),
            "executed_count": len(runs),
            "error_count": len(errors),
            "runs": runs,
            "errors": errors,
            "status": "ok" if not errors else "warning",
        }
        output_path: Path | None = None
        if args.output_path is not None:
            raw_output = Path(str(args.output_path)).expanduser()
            if raw_output.is_absolute():
                output_path = raw_output.resolve()
            else:
                output_path = (pipeline_report_path.parent / raw_output).resolve()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            payload["output_path"] = str(output_path)

        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )
        if errors and bool(getattr(args, "strict", False)):
            raise SystemExit(2)
    finally:
        runtime.close()


def _invoke_cli_json_command(
    handler: Any,
    *,
    args: argparse.Namespace,
) -> dict[str, Any]:
    out = io.StringIO()
    with redirect_stdout(out):
        handler(args)
    raw = out.getvalue().strip()
    if not raw:
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        return {"payload": loaded}
    return loaded


def _resolve_path_near(base: Path, raw_path: Path | str | None, *, default_name: str) -> Path:
    if raw_path is None:
        return (base / default_name).resolve()
    candidate = Path(str(raw_path)).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base / candidate).resolve()


def _count_verdict(rows: list[dict[str, Any]], verdict: str) -> int:
    target = str(verdict or "").strip().lower()
    return sum(
        1
        for row in list(rows or [])
        if isinstance(row, dict) and str(row.get("verdict") or "").strip().lower() == target
    )


def _aggregate_stage_status(statuses: list[str]) -> str:
    normalized = [str(item or "").strip().lower() for item in list(statuses or [])]
    normalized = [item for item in normalized if item]
    if not normalized:
        return "skipped_not_requested"
    if any(item == "error" for item in normalized):
        return "error"
    if all(item == "skipped_not_requested" for item in normalized):
        return "skipped_not_requested"
    if any(item in {"warning", "skipped_leaderboard_error"} for item in normalized):
        return "warning"
    if all(item == "ok" for item in normalized):
        return "ok"
    return normalized[0]


def _normalize_seed_domain_name(raw_domain: Any) -> str:
    normalized = str(raw_domain or "").strip().lower()
    aliases = {
        "quantitative_finance": "quant_finance",
        "weather_betting": "kalshi_weather",
        "kalshi": "kalshi_weather",
        "fitness": "fitness_apps",
        "market_machine_learning": "market_ml",
    }
    return aliases.get(normalized, normalized)


def _load_operator_cycle_defaults_from_config(*, config_path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    defaults = loaded.get("defaults")
    return dict(defaults) if isinstance(defaults, dict) else {}


def _resolve_seed_min_signal_count_current(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(0, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 0, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_min_signal_count_current_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(0, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_min_signal_count_current")
    if global_default_raw is not None:
        try:
            return max(0, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 0, "builtin_default"


def _resolve_seed_min_cross_app_count(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(0, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 0, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_min_cross_app_count_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(0, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_min_cross_app_count")
    if global_default_raw is not None:
        try:
            return max(0, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 0, "builtin_default"


_ALLOWED_SEED_ENTRY_SOURCES = {
    "leaderboard",
    "shared_market_displeasures",
    "white_space_candidates",
}


def _normalize_seed_entry_source_value(raw_value: Any, *, allow_none: bool) -> str | None:
    value = str(raw_value or "").strip().lower()
    if not value:
        return None
    if value == "entries":
        value = "leaderboard"
    if allow_none and value in {"none", "off", "null"}:
        return None
    if value in _ALLOWED_SEED_ENTRY_SOURCES:
        return value
    return None


def _resolve_seed_entry_source(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[str, str]:
    cli_raw = str(cli_value or "").strip()
    if cli_raw:
        cli_normalized = _normalize_seed_entry_source_value(cli_raw, allow_none=False)
        if cli_normalized is not None:
            return cli_normalized, "cli_override"
        return "leaderboard", "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_entry_source_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            resolved = _normalize_seed_entry_source_value(raw_value, allow_none=False)
            if resolved is not None:
                return resolved, "config_by_domain"
            break

    global_default_raw = defaults.get("seed_entry_source")
    resolved_global = _normalize_seed_entry_source_value(global_default_raw, allow_none=False)
    if resolved_global is not None:
        return resolved_global, "config_global"

    return "leaderboard", "builtin_default"


def _resolve_seed_fallback_entry_source(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[str | None, str]:
    cli_raw = str(cli_value or "").strip()
    if cli_raw:
        cli_normalized = _normalize_seed_entry_source_value(cli_raw, allow_none=True)
        if cli_normalized is not None or cli_raw.lower() in {"none", "off", "null"}:
            return cli_normalized, "cli_override"
        return "leaderboard", "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_fallback_entry_source_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            resolved = _normalize_seed_entry_source_value(raw_value, allow_none=True)
            if resolved is not None or str(raw_value or "").strip().lower() in {"none", "off", "null"}:
                return resolved, "config_by_domain"
            break

    global_default_raw = defaults.get("seed_fallback_entry_source")
    if global_default_raw is not None:
        resolved_global = _normalize_seed_entry_source_value(global_default_raw, allow_none=True)
        if resolved_global is not None or str(global_default_raw or "").strip().lower() in {"none", "off", "null"}:
            return resolved_global, "config_global"

    return "leaderboard", "builtin_default"


def _normalize_seed_trends_value(raw_value: Any) -> str | None:
    parts: list[str] = []
    seen: set[str] = set()
    for item in _parse_csv_items(raw_value):
        normalized = str(item or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parts.append(normalized)
    if not parts:
        return None
    return ",".join(parts)


def _resolve_seed_trends(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[str, str]:
    cli_raw = str(cli_value or "").strip()
    if cli_raw:
        cli_normalized = _normalize_seed_trends_value(cli_raw)
        if cli_normalized is not None:
            return cli_normalized, "cli_override"
        return "new,rising", "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_trends_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            resolved = _normalize_seed_trends_value(raw_value)
            if resolved is not None:
                return resolved, "config_by_domain"
            break

    global_default_raw = defaults.get("seed_trends")
    resolved_global = _normalize_seed_trends_value(global_default_raw)
    if resolved_global is not None:
        return resolved_global, "config_global"

    return "new,rising", "builtin_default"


def _resolve_seed_min_impact_score(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[float, str]:
    if cli_value is not None:
        try:
            return float(cli_value), "cli_override"
        except (TypeError, ValueError):
            return 0.0, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_min_impact_score_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return float(raw_value), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_min_impact_score")
    if global_default_raw is not None:
        try:
            return float(global_default_raw), "config_global"
        except (TypeError, ValueError):
            pass

    return 0.0, "builtin_default"


def _resolve_seed_min_impact_delta(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[float, str]:
    if cli_value is not None:
        try:
            return float(cli_value), "cli_override"
        except (TypeError, ValueError):
            return 0.0, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_min_impact_delta_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return float(raw_value), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_min_impact_delta")
    if global_default_raw is not None:
        try:
            return float(global_default_raw), "config_global"
        except (TypeError, ValueError):
            pass

    return 0.0, "builtin_default"


def _resolve_seed_limit(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 8, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_limit_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(1, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_limit")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 8, "builtin_default"


def _resolve_seed_lookup_limit(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 400, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_lookup_limit_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(1, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_lookup_limit")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 400, "builtin_default"


def _resolve_seed_lookback_days(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 7, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_lookback_days_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(1, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_lookback_days")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 7, "builtin_default"


def _resolve_seed_leaderboard_limit(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 12, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_leaderboard_limit_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(1, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_leaderboard_limit")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 12, "builtin_default"


def _resolve_seed_trend_threshold(
    *,
    domain: str,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[float, str]:
    if cli_value is not None:
        try:
            return max(0.0, float(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 0.25, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain)
    by_domain_raw = defaults.get("seed_trend_threshold_by_domain")
    if isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(0.0, float(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("seed_trend_threshold")
    if global_default_raw is not None:
        try:
            return max(0.0, float(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 0.25, "builtin_default"


def _normalize_draft_statuses_value(raw_value: Any) -> str | None:
    values = _coerce_status_preferences(raw_value)
    if not values:
        return None
    return ",".join(values)


def _resolve_draft_statuses(
    *,
    domain: str | None,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[str, str]:
    if cli_value is not None:
        cli_normalized = _normalize_draft_statuses_value(cli_value)
        if cli_normalized is not None:
            return cli_normalized, "cli_override"
        return "queued", "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain or "")
    by_domain_raw = defaults.get("draft_statuses_by_domain")
    if normalized_domain and isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            resolved = _normalize_draft_statuses_value(raw_value)
            if resolved is not None:
                return resolved, "config_by_domain"
            break

    global_default_raw = defaults.get("draft_statuses")
    resolved_global = _normalize_draft_statuses_value(global_default_raw)
    if resolved_global is not None:
        return resolved_global, "config_global"

    return "queued", "builtin_default"


def _resolve_draft_limit(
    *,
    domain: str | None,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 8, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain or "")
    by_domain_raw = defaults.get("draft_limit_by_domain")
    if normalized_domain and isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(1, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("draft_limit")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 8, "builtin_default"


def _resolve_draft_lookup_limit(
    *,
    domain: str | None,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 400, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain or "")
    by_domain_raw = defaults.get("draft_lookup_limit_by_domain")
    if normalized_domain and isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(1, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("draft_lookup_limit")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 400, "builtin_default"


def _resolve_draft_environment(
    *,
    domain: str | None,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[str, str]:
    if cli_value is not None:
        cli_raw = str(cli_value).strip()
        if cli_raw:
            return cli_raw, "cli_override"
        return _default_controlled_environment_for_domain(domain or ""), "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain or "")
    by_domain_raw = defaults.get("draft_environment_by_domain")
    if normalized_domain and isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            resolved = str(raw_value or "").strip()
            if resolved:
                return resolved, "config_by_domain"
            break

    global_default_raw = str(defaults.get("draft_environment") or "").strip()
    if global_default_raw:
        return global_default_raw, "config_global"

    return _default_controlled_environment_for_domain(domain or ""), "builtin_default"


def _resolve_draft_default_sample_size(
    *,
    domain: str | None,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 100, "cli_override_invalid"

    normalized_domain = _normalize_seed_domain_name(domain or "")
    by_domain_raw = defaults.get("draft_default_sample_size_by_domain")
    if normalized_domain and isinstance(by_domain_raw, dict):
        for raw_domain, raw_value in by_domain_raw.items():
            domain_key = _normalize_seed_domain_name(raw_domain)
            if not domain_key or domain_key != normalized_domain:
                continue
            try:
                return max(1, int(raw_value)), "config_by_domain"
            except (TypeError, ValueError):
                break

    global_default_raw = defaults.get("draft_default_sample_size")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 100, "builtin_default"


def _resolve_benchmark_top_limit(
    *,
    cli_value: Any,
    defaults: dict[str, Any],
) -> tuple[int, str]:
    if cli_value is not None:
        try:
            return max(1, int(cli_value)), "cli_override"
        except (TypeError, ValueError):
            return 10, "cli_override_invalid"

    global_default_raw = defaults.get("benchmark_top_limit")
    if global_default_raw is not None:
        try:
            return max(1, int(global_default_raw)), "config_global"
        except (TypeError, ValueError):
            pass

    return 10, "builtin_default"


def _resolve_verify_matrix_path(
    *,
    cli_value: Any,
    defaults: dict[str, Any],
    config_path: Path,
) -> tuple[Path | None, str]:
    if cli_value is not None and str(cli_value).strip():
        return _resolve_path_from_base(cli_value, base_dir=Path.cwd()), "cli_override"

    config_raw = defaults.get("verify_matrix_path")
    if config_raw is not None and str(config_raw).strip():
        return _resolve_pipeline_path(config_raw, config_path=config_path), "config_global"

    return None, "builtin_default"


def _build_operator_cycle_recheck_command(
    *,
    config_path: Path,
    output_dir: Path,
    verify_matrix_path: Path | None,
    verify_matrix_alert_domain: str,
    verify_matrix_alert_max_items: int,
    verify_matrix_alert_urgency: Any,
    verify_matrix_alert_confidence: Any,
) -> str:
    command_parts = [
        "python3",
        "-m",
        "jarvis.cli",
        "improvement",
        "operator-cycle",
        "--config-path",
        str(config_path),
        "--output-dir",
        str(output_dir),
        "--verify-matrix-enable",
        "--verify-matrix-alert-enable",
    ]
    if verify_matrix_path is not None:
        command_parts.extend(["--verify-matrix-path", str(verify_matrix_path)])
    if str(verify_matrix_alert_domain or "").strip():
        command_parts.extend(["--verify-matrix-alert-domain", str(verify_matrix_alert_domain)])
    command_parts.extend(["--verify-matrix-alert-max-items", str(max(1, int(verify_matrix_alert_max_items or 1)))])
    if verify_matrix_alert_urgency is not None:
        try:
            command_parts.extend(["--verify-matrix-alert-urgency", str(float(verify_matrix_alert_urgency))])
        except (TypeError, ValueError):
            pass
    if verify_matrix_alert_confidence is not None:
        try:
            command_parts.extend(["--verify-matrix-alert-confidence", str(float(verify_matrix_alert_confidence))])
        except (TypeError, ValueError):
            pass
    return " ".join(shlex.quote(part) for part in command_parts)


def _build_operator_evidence_lookup_command(
    *,
    config_path: Path,
    record_ids: Any,
    output_path: Path | None = None,
) -> str:
    normalized_record_ids = _normalize_record_id_list(record_ids)
    if not normalized_record_ids:
        return "none"
    command_parts = [
        "python3",
        "-m",
        "jarvis.cli",
        "improvement",
        "evidence-lookup",
        "--config-path",
        str(config_path),
        "--record-ids",
        ",".join(normalized_record_ids),
    ]
    if output_path is not None:
        command_parts.extend(["--output-path", str(output_path)])
    return " ".join(shlex.quote(part) for part in command_parts)


def _build_operator_cycle_knowledge_bootstrap_command(
    *,
    config_path: Path,
    output_dir: Path,
    knowledge_domains: str,
    knowledge_snapshot_dir: Path | None,
    knowledge_query: str,
    knowledge_snapshot_label: str | None,
) -> str:
    command_parts = [
        "python3",
        "-m",
        "jarvis.cli",
        "improvement",
        "operator-cycle",
        "--config-path",
        str(config_path),
        "--output-dir",
        str(output_dir),
        "--knowledge-brief-enable",
        "--knowledge-delta-alert-enable",
    ]
    domains_value = str(knowledge_domains or "").strip()
    if domains_value:
        command_parts.extend(["--knowledge-delta-domains", domains_value])
    if knowledge_snapshot_dir is not None:
        command_parts.extend(["--knowledge-delta-snapshot-dir", str(knowledge_snapshot_dir)])
    query_value = str(knowledge_query or "").strip()
    if query_value:
        command_parts.extend(["--knowledge-brief-query", query_value])
    snapshot_label_value = str(knowledge_snapshot_label or "").strip()
    if snapshot_label_value:
        command_parts.extend(["--knowledge-brief-snapshot-label", snapshot_label_value])
    return " ".join(shlex.quote(part) for part in command_parts)


def _collect_knowledge_snapshot_inventory(snapshot_dir: Path) -> dict[str, Any]:
    resolved_snapshot_dir = snapshot_dir.resolve()
    latest_path = (resolved_snapshot_dir / "knowledge_brief_latest.json").resolve()
    index_path = (resolved_snapshot_dir / "knowledge_brief_index.jsonl").resolve()

    index_rows = _load_knowledge_snapshot_index_rows(index_path)
    indexed_existing_paths: set[str] = set()
    for row in index_rows:
        candidate = str(row.get("path") or "").strip()
        if not candidate:
            continue
        resolved_candidate = Path(candidate).expanduser().resolve()
        if resolved_candidate.exists():
            indexed_existing_paths.add(str(resolved_candidate))

    versioned_paths: set[str] = set()
    if resolved_snapshot_dir.exists():
        for candidate in resolved_snapshot_dir.glob("knowledge_brief_*.json"):
            resolved_candidate = candidate.resolve()
            if resolved_candidate.name == "knowledge_brief_latest.json":
                continue
            if resolved_candidate.exists():
                versioned_paths.add(str(resolved_candidate))

    return {
        "snapshot_dir": str(resolved_snapshot_dir),
        "latest_path": str(latest_path),
        "latest_exists": latest_path.exists(),
        "index_path": str(index_path),
        "index_entry_count": len(index_rows),
        "indexed_existing_snapshot_count": len(indexed_existing_paths),
        "versioned_snapshot_count": len(versioned_paths),
        "comparison_candidate_count": len(versioned_paths),
        "minimum_required_snapshot_count": 2,
        "bootstrap_ready": len(versioned_paths) >= 2,
    }


def _infer_feedback_seed_context_from_config(*, config_path: Path, domain: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(loaded, dict):
        return None
    feedback_jobs = list(loaded.get("feedback_jobs") or [])
    resolved_domain = str(domain or "").strip().lower()
    for raw_job in feedback_jobs:
        if not isinstance(raw_job, dict):
            continue
        job_domain = str(raw_job.get("domain") or "").strip().lower()
        if resolved_domain and job_domain != resolved_domain:
            continue
        input_path_raw = raw_job.get("input_path")
        if input_path_raw is None or not str(input_path_raw).strip():
            continue
        input_format = str(raw_job.get("input_format") or "").strip().lower() or None
        source = str(raw_job.get("source") or "").strip().lower() or None
        return {
            "domain": job_domain or resolved_domain or None,
            "source": source,
            "input_format": input_format,
            "input_path": _resolve_pipeline_path(input_path_raw, config_path=config_path),
        }
    return None


def _infer_feedback_input_path_from_config(*, config_path: Path, domain: str) -> Path | None:
    inferred = _infer_feedback_seed_context_from_config(config_path=config_path, domain=domain)
    if not isinstance(inferred, dict):
        return None
    input_path = inferred.get("input_path")
    return input_path if isinstance(input_path, Path) else None


def cmd_improvement_operator_cycle(args: argparse.Namespace) -> None:
    config_path = args.config_path.resolve()
    default_output_dir = (config_path.parent / "output" / "improvement" / "operator_cycle").resolve()
    if args.output_dir is None:
        output_dir = default_output_dir
    else:
        explicit_output_dir = Path(str(args.output_dir)).expanduser()
        if explicit_output_dir.is_absolute():
            output_dir = explicit_output_dir.resolve()
        else:
            output_dir = (Path.cwd() / explicit_output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pull_report_path = _resolve_path_near(output_dir, None, default_name="pull_feeds_report.json")
    daily_report_path = _resolve_path_near(output_dir, None, default_name="daily_pipeline_report.json")
    retest_report_path = _resolve_path_near(output_dir, None, default_name="retest_execution_report.json")
    inbox_summary_path = _resolve_path_near(
        output_dir,
        args.inbox_summary_path,
        default_name="operator_inbox_summary.json",
    )
    operator_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "operator_report_path", None),
        default_name="operator_cycle_report.json",
    )
    benchmark_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "benchmark_report_path", None),
        default_name="benchmark_frustrations_report.json",
    )
    verify_matrix_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "verify_matrix_report_path", None),
        default_name="verify_matrix_report.json",
    )
    verify_matrix_alert_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "verify_matrix_alert_report_path", None),
        default_name="verify_matrix_alert_report.json",
    )
    knowledge_delta_alert_report_path_value = (
        getattr(args, "knowledge_brief_delta_alert_report_path", None)
        if getattr(args, "knowledge_brief_delta_alert_report_path", None) is not None
        else getattr(args, "knowledge_delta_alert_report_path", None)
    )
    knowledge_brief_delta_alert_report_path = _resolve_path_near(
        output_dir,
        knowledge_delta_alert_report_path_value,
        default_name="knowledge_brief_delta_alert_report.json",
    )
    knowledge_brief_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "knowledge_brief_report_path", None),
        default_name="knowledge_brief_report.json",
    )
    retest_artifact_dir = _resolve_path_near(
        output_dir,
        args.retest_artifact_dir,
        default_name="retest_artifacts",
    )
    draft_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "draft_report_path", None),
        default_name="draft_experiment_jobs_report.json",
    )
    draft_artifacts_dir = _resolve_path_near(
        output_dir,
        getattr(args, "draft_artifacts_dir", None),
        default_name="drafted_experiment_artifacts",
    )
    seed_leaderboard_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "seed_leaderboard_report_path", None),
        default_name="fitness_frustration_leaderboard.json",
    )
    seed_report_output_path = _resolve_path_near(
        output_dir,
        getattr(args, "seed_report_path", None),
        default_name="fitness_leaderboard_seed_report.json",
    )
    evidence_lookup_report_path = _resolve_path_near(
        output_dir,
        getattr(args, "evidence_lookup_report_path", None),
        default_name="evidence_lookup_report.json",
    )

    stage_errors: list[dict[str, Any]] = []
    operator_cycle_defaults = _load_operator_cycle_defaults_from_config(config_path=config_path)
    raw_evidence_runtime_history_path = (
        getattr(args, "evidence_runtime_history_path", None)
        if getattr(args, "evidence_runtime_history_path", None) is not None
        else operator_cycle_defaults.get("evidence_runtime_history_path")
    )
    resolved_evidence_runtime_history_path: Path
    evidence_runtime_history_path_source: str
    if raw_evidence_runtime_history_path is not None and str(raw_evidence_runtime_history_path).strip():
        if getattr(args, "evidence_runtime_history_path", None) is not None:
            resolved_evidence_runtime_history_path = _resolve_path_from_base(
                raw_evidence_runtime_history_path,
                base_dir=Path.cwd(),
            ).resolve()
            evidence_runtime_history_path_source = "cli_override"
        else:
            resolved_evidence_runtime_history_path = _resolve_pipeline_path(
                raw_evidence_runtime_history_path,
                config_path=config_path,
            ).resolve()
            evidence_runtime_history_path_source = "config_global"
    else:
        resolved_evidence_runtime_history_path = (output_dir / "evidence_lookup_runtime_history.jsonl").resolve()
        evidence_runtime_history_path_source = "output_default"
    if getattr(args, "evidence_runtime_history_window", None) is not None:
        resolved_evidence_runtime_history_window = max(
            1,
            int(getattr(args, "evidence_runtime_history_window") or 1),
        )
        evidence_runtime_history_window_source = "cli_override"
    elif operator_cycle_defaults.get("evidence_runtime_history_window") is not None:
        resolved_evidence_runtime_history_window = max(
            1,
            _coerce_int(operator_cycle_defaults.get("evidence_runtime_history_window"), default=7),
        )
        evidence_runtime_history_window_source = "config_global"
    else:
        resolved_evidence_runtime_history_window = 7
        evidence_runtime_history_window_source = "builtin_default"
    if getattr(args, "benchmark_stale_runtime_history_window", None) is not None:
        resolved_benchmark_stale_runtime_history_window = max(
            1,
            int(getattr(args, "benchmark_stale_runtime_history_window") or 1),
        )
        benchmark_stale_runtime_history_window_source = "cli_override"
    elif operator_cycle_defaults.get("benchmark_stale_runtime_history_window") is not None:
        resolved_benchmark_stale_runtime_history_window = max(
            1,
            _coerce_int(operator_cycle_defaults.get("benchmark_stale_runtime_history_window"), default=7),
        )
        benchmark_stale_runtime_history_window_source = "config_global"
    else:
        resolved_benchmark_stale_runtime_history_window = 7
        benchmark_stale_runtime_history_window_source = "builtin_default"
    if getattr(args, "benchmark_stale_runtime_repeat_threshold", None) is not None:
        resolved_benchmark_stale_runtime_repeat_threshold = max(
            1,
            int(getattr(args, "benchmark_stale_runtime_repeat_threshold") or 1),
        )
        benchmark_stale_runtime_repeat_threshold_source = "cli_override"
    elif operator_cycle_defaults.get("benchmark_stale_runtime_repeat_threshold") is not None:
        resolved_benchmark_stale_runtime_repeat_threshold = max(
            1,
            _coerce_int(operator_cycle_defaults.get("benchmark_stale_runtime_repeat_threshold"), default=2),
        )
        benchmark_stale_runtime_repeat_threshold_source = "config_global"
    else:
        resolved_benchmark_stale_runtime_repeat_threshold = 2
        benchmark_stale_runtime_repeat_threshold_source = "builtin_default"
    if getattr(args, "benchmark_stale_runtime_rate_ceiling", None) is not None:
        resolved_benchmark_stale_runtime_rate_ceiling = min(
            1.0,
            max(0.0, _coerce_float(getattr(args, "benchmark_stale_runtime_rate_ceiling"), default=0.6)),
        )
        benchmark_stale_runtime_rate_ceiling_source = "cli_override"
    elif operator_cycle_defaults.get("benchmark_stale_runtime_rate_ceiling") is not None:
        resolved_benchmark_stale_runtime_rate_ceiling = min(
            1.0,
            max(0.0, _coerce_float(operator_cycle_defaults.get("benchmark_stale_runtime_rate_ceiling"), default=0.6)),
        )
        benchmark_stale_runtime_rate_ceiling_source = "config_global"
    else:
        resolved_benchmark_stale_runtime_rate_ceiling = 0.6
        benchmark_stale_runtime_rate_ceiling_source = "builtin_default"
    if getattr(args, "benchmark_stale_runtime_consecutive_runs", None) is not None:
        resolved_benchmark_stale_runtime_consecutive_runs = max(
            1,
            int(getattr(args, "benchmark_stale_runtime_consecutive_runs") or 1),
        )
        benchmark_stale_runtime_consecutive_runs_source = "cli_override"
    elif operator_cycle_defaults.get("benchmark_stale_runtime_consecutive_runs") is not None:
        resolved_benchmark_stale_runtime_consecutive_runs = max(
            1,
            _coerce_int(operator_cycle_defaults.get("benchmark_stale_runtime_consecutive_runs"), default=2),
        )
        benchmark_stale_runtime_consecutive_runs_source = "config_global"
    else:
        resolved_benchmark_stale_runtime_consecutive_runs = 2
        benchmark_stale_runtime_consecutive_runs_source = "builtin_default"

    pull_payload: dict[str, Any]
    try:
        pull_args = argparse.Namespace(
            config_path=config_path,
            feed_names=args.feed_names,
            allow_missing=bool(args.allow_missing_feeds),
            strict=False,
            timeout_seconds=float(args.feed_timeout_seconds),
            output_path=pull_report_path,
            json_compact=False,
        )
        pull_payload = _invoke_cli_json_command(
            cmd_improvement_pull_feeds,
            args=pull_args,
        )
    except Exception as exc:
        pull_payload = {
            "status": "error",
            "error": str(exc),
            "output_path": str(pull_report_path),
        }
        stage_errors.append({"stage": "pull_feeds", "error": str(exc)})

    seed_requested = bool(
        getattr(args, "seed_enable", False)
        or getattr(args, "seed_leaderboard_input_path", None) is not None
    )
    seed_domains_raw = (
        getattr(args, "seed_domains", None)
        if getattr(args, "seed_domains", None) is not None
        else operator_cycle_defaults.get("seed_domains")
    )
    seed_domains = [
        _normalize_seed_domain_name(str(item or "").strip().lower())
        for item in _parse_csv_items(seed_domains_raw)
        if str(item or "").strip()
    ]
    if not seed_domains:
        fallback_seed_domain = (
            str(getattr(args, "seed_domain", "fitness_apps") or "fitness_apps").strip().lower() or "fitness_apps"
        )
        seed_domains = [fallback_seed_domain]
    deduped_seed_domains: list[str] = []
    seen_seed_domains: set[str] = set()
    for domain_value in seed_domains:
        if domain_value in seen_seed_domains:
            continue
        seen_seed_domains.add(domain_value)
        deduped_seed_domains.append(domain_value)
    seed_domains = deduped_seed_domains

    seed_source_override = str(getattr(args, "seed_source", None) or "").strip().lower() or None
    seed_hypothesis_source_override = (
        str(getattr(args, "seed_hypothesis_source", None) or "").strip().lower() or None
    )

    leaderboard_payload: dict[str, Any]
    seed_payload: dict[str, Any]
    seed_domain_runs: list[dict[str, Any]] = []
    seed_report_paths_for_draft: list[Path] = []
    seed_combined_report_path: Path | None = None
    if seed_requested:
        for domain_index, seed_domain in enumerate(seed_domains):
            domain_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", seed_domain) or f"domain_{domain_index + 1}"
            resolved_seed_lookback_days, seed_lookback_days_source = _resolve_seed_lookback_days(
                domain=seed_domain,
                cli_value=getattr(args, "seed_lookback_days", None),
                defaults=operator_cycle_defaults,
            )
            resolved_seed_leaderboard_limit, seed_leaderboard_limit_source = _resolve_seed_leaderboard_limit(
                domain=seed_domain,
                cli_value=getattr(args, "seed_leaderboard_limit", None),
                defaults=operator_cycle_defaults,
            )
            resolved_seed_trend_threshold, seed_trend_threshold_source = _resolve_seed_trend_threshold(
                domain=seed_domain,
                cli_value=getattr(args, "seed_trend_threshold", None),
                defaults=operator_cycle_defaults,
            )
            resolved_seed_limit, seed_limit_source = _resolve_seed_limit(
                domain=seed_domain,
                cli_value=getattr(args, "seed_limit", None),
                defaults=operator_cycle_defaults,
            )
            resolved_seed_lookup_limit, seed_lookup_limit_source = _resolve_seed_lookup_limit(
                domain=seed_domain,
                cli_value=getattr(args, "seed_lookup_limit", None),
                defaults=operator_cycle_defaults,
            )
            if resolved_seed_lookup_limit < resolved_seed_limit:
                resolved_seed_lookup_limit = int(resolved_seed_limit)
                seed_lookup_limit_source = f"{seed_lookup_limit_source}_raised_to_limit"
            resolved_trends, trends_source = _resolve_seed_trends(
                domain=seed_domain,
                cli_value=getattr(args, "seed_trends", None),
                defaults=operator_cycle_defaults,
            )
            resolved_min_impact_score, min_impact_score_source = _resolve_seed_min_impact_score(
                domain=seed_domain,
                cli_value=getattr(args, "seed_min_impact_score", None),
                defaults=operator_cycle_defaults,
            )
            resolved_min_impact_delta, min_impact_delta_source = _resolve_seed_min_impact_delta(
                domain=seed_domain,
                cli_value=getattr(args, "seed_min_impact_delta", None),
                defaults=operator_cycle_defaults,
            )
            resolved_entry_source, entry_source_setting_source = _resolve_seed_entry_source(
                domain=seed_domain,
                cli_value=getattr(args, "seed_entry_source", None),
                defaults=operator_cycle_defaults,
            )
            resolved_fallback_entry_source, fallback_entry_source_setting_source = _resolve_seed_fallback_entry_source(
                domain=seed_domain,
                cli_value=getattr(args, "seed_fallback_entry_source", None),
                defaults=operator_cycle_defaults,
            )
            resolved_min_cross_app_count, min_cross_app_count_source = _resolve_seed_min_cross_app_count(
                domain=seed_domain,
                cli_value=getattr(args, "seed_min_cross_app_count", None),
                defaults=operator_cycle_defaults,
            )
            resolved_min_cross_app_count_for_leaderboard = int(resolved_min_cross_app_count)
            if min_cross_app_count_source == "builtin_default":
                # Preserve legacy behavior when no override/default is provided:
                # shared-market ranking in leaderboard defaults to 2, while
                # seed-from-leaderboard filtering defaults to 0.
                resolved_min_cross_app_count_for_leaderboard = 2
            resolved_min_signal_count_current, min_signal_count_source = _resolve_seed_min_signal_count_current(
                domain=seed_domain,
                cli_value=getattr(args, "seed_min_signal_count_current", None),
                defaults=operator_cycle_defaults,
            )
            if domain_index == 0:
                domain_leaderboard_report_path = seed_leaderboard_report_path
                domain_seed_report_path = seed_report_output_path
            else:
                domain_seed_output_dir = (output_dir / "seeding" / domain_slug).resolve()
                domain_seed_output_dir.mkdir(parents=True, exist_ok=True)
                domain_leaderboard_report_path = _resolve_path_near(
                    domain_seed_output_dir,
                    None,
                    default_name=f"{domain_slug}_frustration_leaderboard.json",
                )
                domain_seed_report_path = _resolve_path_near(
                    domain_seed_output_dir,
                    None,
                    default_name=f"{domain_slug}_leaderboard_seed_report.json",
                )

            domain_input_path: Path | None = None
            domain_input_format = (
                str(getattr(args, "seed_leaderboard_input_format", None)).strip().lower()
                if getattr(args, "seed_leaderboard_input_format", None) is not None
                else None
            )
            domain_seed_source = seed_source_override
            domain_seed_hypothesis_source = seed_hypothesis_source_override or "fitness_leaderboard"

            try:
                leaderboard_input_raw = getattr(args, "seed_leaderboard_input_path", None)
                if leaderboard_input_raw is not None and str(leaderboard_input_raw).strip():
                    explicit_leaderboard_input = Path(str(leaderboard_input_raw)).expanduser()
                    if explicit_leaderboard_input.is_absolute():
                        domain_input_path = explicit_leaderboard_input.resolve()
                    else:
                        cwd_relative_candidate = (Path.cwd() / explicit_leaderboard_input).resolve()
                        if cwd_relative_candidate.exists():
                            domain_input_path = cwd_relative_candidate
                        else:
                            domain_input_path = _resolve_pipeline_path(
                                explicit_leaderboard_input,
                                config_path=config_path,
                            )
                else:
                    inferred_context = _infer_feedback_seed_context_from_config(
                        config_path=config_path,
                        domain=seed_domain,
                    )
                    if not isinstance(inferred_context, dict):
                        raise ValueError("missing_seed_leaderboard_input_path")
                    inferred_input = inferred_context.get("input_path")
                    if not isinstance(inferred_input, Path):
                        raise ValueError("missing_seed_leaderboard_input_path")
                    domain_input_path = inferred_input
                    if domain_seed_source is None:
                        inferred_source = str(inferred_context.get("source") or "").strip().lower() or None
                        domain_seed_source = inferred_source
                    if domain_input_format is None:
                        inferred_format = str(inferred_context.get("input_format") or "").strip().lower() or None
                        domain_input_format = inferred_format

                if domain_input_path is None:
                    raise ValueError("missing_seed_leaderboard_input_path")
                if domain_seed_source is None:
                    domain_seed_source = "market_reviews"

                leaderboard_args = argparse.Namespace(
                    input_path=domain_input_path,
                    input_format=domain_input_format,
                    domain=seed_domain,
                    source=domain_seed_source,
                    timestamp_fields=str(
                        getattr(
                            args,
                            "seed_timestamp_fields",
                            "created_at,at,submission_date,date,timestamp,occurred_at",
                        )
                        or "created_at,at,submission_date,date,timestamp,occurred_at"
                    ),
                    as_of=getattr(args, "seed_as_of", None),
                    lookback_days=max(1, int(resolved_seed_lookback_days)),
                    min_cluster_count=max(1, int(getattr(args, "seed_min_cluster_count", 1) or 1)),
                    cluster_limit=max(1, int(getattr(args, "seed_cluster_limit", 20) or 20)),
                    leaderboard_limit=max(1, int(resolved_seed_leaderboard_limit)),
                    cooling_limit=max(1, int(getattr(args, "seed_cooling_limit", 10) or 10)),
                    app_fields=str(getattr(args, "seed_app_fields", DEFAULT_FITNESS_APP_FIELDS_CSV) or DEFAULT_FITNESS_APP_FIELDS_CSV),
                    top_apps_per_cluster=max(1, int(getattr(args, "seed_top_apps_per_cluster", 3) or 3)),
                    min_cross_app_count=max(1, int(resolved_min_cross_app_count_for_leaderboard)),
                    own_app_aliases=getattr(args, "seed_own_app_aliases", None),
                    trend_threshold=max(0.0, float(resolved_seed_trend_threshold)),
                    include_untimed_current=bool(getattr(args, "seed_include_untimed_current", False)),
                    strict=False,
                    output_path=domain_leaderboard_report_path,
                    json_compact=False,
                )
                domain_leaderboard_payload = _invoke_cli_json_command(
                    cmd_improvement_fitness_leaderboard,
                    args=leaderboard_args,
                )
            except Exception as exc:
                domain_leaderboard_payload = {
                    "status": "error",
                    "error": str(exc),
                    "output_path": str(domain_leaderboard_report_path),
                }
                domain_seed_payload = {
                    "status": "skipped_leaderboard_error",
                    "output_path": str(domain_seed_report_path),
                }
                stage_errors.append({"stage": "fitness_leaderboard", "domain": seed_domain, "error": str(exc)})
            else:
                try:
                    seed_args = argparse.Namespace(
                        leaderboard_path=domain_leaderboard_report_path,
                        domain=seed_domain,
                        source=domain_seed_hypothesis_source,
                        trends=str(resolved_trends),
                        limit=max(1, int(resolved_seed_limit)),
                        min_impact_score=float(resolved_min_impact_score),
                        min_impact_delta=float(resolved_min_impact_delta),
                        entry_source=str(resolved_entry_source),
                        fallback_entry_source=resolved_fallback_entry_source,
                        min_cross_app_count=max(0, int(resolved_min_cross_app_count)),
                        min_signal_count_current=int(resolved_min_signal_count_current),
                        owner=str(getattr(args, "seed_owner", "operator") or "operator").strip() or "operator",
                        lookup_limit=max(1, int(resolved_seed_lookup_limit)),
                        strict=False,
                        output_path=domain_seed_report_path,
                        json_compact=False,
                        repo_path=args.repo_path,
                        db_path=args.db_path,
                    )
                    domain_seed_payload = _invoke_cli_json_command(
                        cmd_improvement_seed_from_leaderboard,
                        args=seed_args,
                    )
                except Exception as exc:
                    domain_seed_payload = {
                        "status": "error",
                        "error": str(exc),
                        "output_path": str(domain_seed_report_path),
                    }
                    stage_errors.append({"stage": "seed_from_leaderboard", "domain": seed_domain, "error": str(exc)})

            seed_domain_runs.append(
                {
                    "domain": seed_domain,
                    "source": domain_seed_source or "market_reviews",
                    "hypothesis_source": domain_seed_hypothesis_source,
                    "input_path": str(domain_input_path) if domain_input_path is not None else None,
                    "input_format": domain_input_format,
                    "leaderboard_report_path": str(domain_leaderboard_report_path),
                    "seed_report_path": str(domain_seed_report_path),
                    "seed_lookback_days": int(resolved_seed_lookback_days),
                    "seed_lookback_days_source": str(seed_lookback_days_source),
                    "seed_leaderboard_limit": int(resolved_seed_leaderboard_limit),
                    "seed_leaderboard_limit_source": str(seed_leaderboard_limit_source),
                    "seed_trend_threshold": float(resolved_seed_trend_threshold),
                    "seed_trend_threshold_source": str(seed_trend_threshold_source),
                    "seed_limit": int(resolved_seed_limit),
                    "seed_limit_source": str(seed_limit_source),
                    "seed_lookup_limit": int(resolved_seed_lookup_limit),
                    "seed_lookup_limit_source": str(seed_lookup_limit_source),
                    "seed_trends": str(resolved_trends),
                    "seed_trends_source": str(trends_source),
                    "seed_min_impact_score": float(resolved_min_impact_score),
                    "seed_min_impact_score_source": str(min_impact_score_source),
                    "seed_min_impact_delta": float(resolved_min_impact_delta),
                    "seed_min_impact_delta_source": str(min_impact_delta_source),
                    "seed_entry_source": str(resolved_entry_source),
                    "seed_entry_source_source": str(entry_source_setting_source),
                    "seed_fallback_entry_source": (
                        str(resolved_fallback_entry_source)
                        if resolved_fallback_entry_source is not None
                        else "none"
                    ),
                    "seed_fallback_entry_source_source": str(fallback_entry_source_setting_source),
                    "seed_leaderboard_min_cross_app_count": int(resolved_min_cross_app_count_for_leaderboard),
                    "seed_min_cross_app_count": int(resolved_min_cross_app_count),
                    "seed_min_cross_app_count_source": str(min_cross_app_count_source),
                    "seed_min_signal_count_current": int(resolved_min_signal_count_current),
                    "seed_min_signal_count_current_source": str(min_signal_count_source),
                    "fitness_leaderboard": domain_leaderboard_payload,
                    "seed_from_leaderboard": domain_seed_payload,
                }
            )
            if str(domain_seed_payload.get("status") or "").strip().lower() == "ok":
                seed_output_path_raw = str(domain_seed_payload.get("output_path") or "").strip()
                if seed_output_path_raw:
                    seed_report_paths_for_draft.append(Path(seed_output_path_raw).expanduser().resolve())

        leaderboard_statuses = [
            str((row.get("fitness_leaderboard") or {}).get("status") or "")
            for row in seed_domain_runs
            if isinstance(row, dict)
        ]
        seed_statuses = [
            str((row.get("seed_from_leaderboard") or {}).get("status") or "")
            for row in seed_domain_runs
            if isinstance(row, dict)
        ]

        if len(seed_domain_runs) == 1:
            only_run = seed_domain_runs[0]
            leaderboard_payload = dict(only_run.get("fitness_leaderboard") or {})
            seed_payload = dict(only_run.get("seed_from_leaderboard") or {})
        else:
            leaderboard_payload = {
                "status": _aggregate_stage_status(leaderboard_statuses),
                "domain_count": len(seed_domain_runs),
                "runs": [
                    {
                        "domain": row.get("domain"),
                        "status": str((row.get("fitness_leaderboard") or {}).get("status") or ""),
                        "input_path": row.get("input_path"),
                        "source": row.get("source"),
                        "output_path": str((row.get("fitness_leaderboard") or {}).get("output_path") or ""),
                        "error": (row.get("fitness_leaderboard") or {}).get("error"),
                    }
                    for row in seed_domain_runs
                    if isinstance(row, dict)
                ],
            }
            seed_payload = {
                "status": _aggregate_stage_status(seed_statuses),
                "domain_count": len(seed_domain_runs),
                "runs": [
                    {
                        "domain": row.get("domain"),
                        "status": str((row.get("seed_from_leaderboard") or {}).get("status") or ""),
                        "source": row.get("hypothesis_source"),
                        "lookback_days": int(row.get("seed_lookback_days") or 0),
                        "lookback_days_source": str(row.get("seed_lookback_days_source") or ""),
                        "leaderboard_limit": int(row.get("seed_leaderboard_limit") or 0),
                        "leaderboard_limit_source": str(row.get("seed_leaderboard_limit_source") or ""),
                        "trend_threshold": _coerce_float(
                            row.get("seed_trend_threshold"),
                            default=0.25,
                        ),
                        "trend_threshold_source": str(row.get("seed_trend_threshold_source") or ""),
                        "limit": int(row.get("seed_limit") or 0),
                        "limit_source": str(row.get("seed_limit_source") or ""),
                        "lookup_limit": int(row.get("seed_lookup_limit") or 0),
                        "lookup_limit_source": str(row.get("seed_lookup_limit_source") or ""),
                        "trends": list(
                            (row.get("seed_from_leaderboard") or {}).get("trend_filters")
                            or _parse_csv_items(row.get("seed_trends"))
                        ),
                        "trends_source": str(row.get("seed_trends_source") or ""),
                        "min_impact_score": _coerce_float(
                            (row.get("seed_from_leaderboard") or {}).get("min_impact_score")
                            if (row.get("seed_from_leaderboard") or {}).get("min_impact_score") is not None
                            else row.get("seed_min_impact_score"),
                            default=0.0,
                        ),
                        "min_impact_score_source": str(row.get("seed_min_impact_score_source") or ""),
                        "min_impact_delta": _coerce_float(
                            (row.get("seed_from_leaderboard") or {}).get("min_impact_delta")
                            if (row.get("seed_from_leaderboard") or {}).get("min_impact_delta") is not None
                            else row.get("seed_min_impact_delta"),
                            default=0.0,
                        ),
                        "min_impact_delta_source": str(row.get("seed_min_impact_delta_source") or ""),
                        "entry_source": str(
                            (row.get("seed_from_leaderboard") or {}).get("requested_entry_source")
                            or row.get("seed_entry_source")
                            or "leaderboard"
                        ),
                        "entry_source_source": str(row.get("seed_entry_source_source") or ""),
                        "fallback_entry_source": str(
                            (row.get("seed_from_leaderboard") or {}).get("fallback_entry_source")
                            or row.get("seed_fallback_entry_source")
                            or "none"
                        ),
                        "fallback_entry_source_source": str(row.get("seed_fallback_entry_source_source") or ""),
                        "min_cross_app_count": int(
                            (row.get("seed_from_leaderboard") or {}).get("min_cross_app_count")
                            or row.get("seed_min_cross_app_count")
                            or 0
                        ),
                        "min_cross_app_count_source": str(row.get("seed_min_cross_app_count_source") or ""),
                        "min_signal_count_current": int(
                            (row.get("seed_from_leaderboard") or {}).get("min_signal_count_current")
                            or row.get("seed_min_signal_count_current")
                            or 0
                        ),
                        "min_signal_count_current_source": str(row.get("seed_min_signal_count_current_source") or ""),
                        "output_path": str((row.get("seed_from_leaderboard") or {}).get("output_path") or ""),
                        "created_count": int((row.get("seed_from_leaderboard") or {}).get("created_count") or 0),
                        "existing_count": int((row.get("seed_from_leaderboard") or {}).get("existing_count") or 0),
                        "error": (row.get("seed_from_leaderboard") or {}).get("error"),
                    }
                    for row in seed_domain_runs
                    if isinstance(row, dict)
                ],
            }

        if len(seed_report_paths_for_draft) > 1:
            try:
                combined_created: list[dict[str, Any]] = []
                combined_existing: list[dict[str, Any]] = []
                combined_skipped: list[dict[str, Any]] = []
                combined_errors: list[dict[str, Any]] = []
                combined_domains: list[str] = []
                for run in seed_domain_runs:
                    if not isinstance(run, dict):
                        continue
                    run_domain = str(run.get("domain") or "").strip().lower() or None
                    seed_report_path_value = str(run.get("seed_report_path") or "").strip()
                    if not run_domain or not seed_report_path_value:
                        continue
                    run_seed_status = str((run.get("seed_from_leaderboard") or {}).get("status") or "").strip().lower()
                    if run_seed_status != "ok":
                        continue
                    seed_report_path = Path(seed_report_path_value).expanduser().resolve()
                    loaded_seed_report = json.loads(seed_report_path.read_text(encoding="utf-8"))
                    if not isinstance(loaded_seed_report, dict):
                        continue
                    if run_domain not in combined_domains:
                        combined_domains.append(run_domain)
                    for row in list(loaded_seed_report.get("created") or []):
                        if not isinstance(row, dict):
                            continue
                        combined_created.append({"domain": run_domain, **dict(row)})
                    for row in list(loaded_seed_report.get("existing") or []):
                        if not isinstance(row, dict):
                            continue
                        combined_existing.append({"domain": run_domain, **dict(row)})
                    for row in list(loaded_seed_report.get("skipped") or []):
                        if not isinstance(row, dict):
                            continue
                        combined_skipped.append({"domain": run_domain, **dict(row)})
                    for row in list(loaded_seed_report.get("errors") or []):
                        if not isinstance(row, dict):
                            continue
                        combined_errors.append({"domain": run_domain, **dict(row)})

                combined_output_path = (output_dir / "seeding" / "combined_seed_report.json").resolve()
                combined_output_path.parent.mkdir(parents=True, exist_ok=True)
                combined_payload = {
                    "generated_at": utc_now_iso(),
                    "domain": "multi_domain",
                    "domains": combined_domains,
                    "source": "operator_cycle_multi_domain_seed",
                    "created_count": len(combined_created),
                    "existing_count": len(combined_existing),
                    "skipped_count": len(combined_skipped),
                    "error_count": len(combined_errors),
                    "created": combined_created,
                    "existing": combined_existing,
                    "skipped": combined_skipped,
                    "errors": combined_errors,
                    "status": "ok" if not combined_errors else "warning",
                    "output_path": str(combined_output_path),
                }
                combined_output_path.write_text(json.dumps(combined_payload, indent=2), encoding="utf-8")
                seed_combined_report_path = combined_output_path
                seed_payload["combined_output_path"] = str(combined_output_path)
            except Exception as exc:
                stage_errors.append({"stage": "seed_report_merge", "error": str(exc)})
    else:
        leaderboard_payload = {
            "status": "skipped_not_requested",
            "output_path": str(seed_leaderboard_report_path),
        }
        seed_payload = {
            "status": "skipped_not_requested",
            "output_path": str(seed_report_output_path),
        }
        seed_domain_runs = []

    auto_draft_seed_report_path: Path | None = None
    if seed_requested:
        if seed_combined_report_path is not None:
            auto_draft_seed_report_path = seed_combined_report_path
        elif len(seed_report_paths_for_draft) == 1:
            auto_draft_seed_report_path = seed_report_paths_for_draft[0]
        elif str(seed_payload.get("status") or "").strip().lower() == "ok":
            seed_output_path = str(seed_payload.get("output_path") or "").strip()
            if seed_output_path:
                auto_draft_seed_report_path = Path(seed_output_path).expanduser().resolve()
    draft_seed_report_path = (
        _resolve_path_from_base(getattr(args, "draft_seed_report_path"), base_dir=Path.cwd())
        if getattr(args, "draft_seed_report_path", None) is not None
        else auto_draft_seed_report_path
    )
    raw_draft_benchmark_report_path = (
        getattr(args, "draft_benchmark_report_path", None)
        if getattr(args, "draft_benchmark_report_path", None) is not None
        else operator_cycle_defaults.get("draft_benchmark_report_path")
    )
    draft_benchmark_max_age_hours_raw = (
        getattr(args, "draft_benchmark_max_age_hours", None)
        if getattr(args, "draft_benchmark_max_age_hours", None) is not None
        else operator_cycle_defaults.get("draft_benchmark_max_age_hours")
    )
    if draft_benchmark_max_age_hours_raw is not None and str(draft_benchmark_max_age_hours_raw).strip():
        resolved_draft_benchmark_max_age_hours = max(
            0.0,
            _coerce_float(draft_benchmark_max_age_hours_raw, default=96.0),
        )
        draft_benchmark_max_age_hours_source = (
            "cli_override"
            if getattr(args, "draft_benchmark_max_age_hours", None) is not None
            else "config_global"
        )
    else:
        resolved_draft_benchmark_max_age_hours = 96.0
        draft_benchmark_max_age_hours_source = "builtin_default"
    auto_draft_benchmark_reuse_status = "not_attempted"
    auto_draft_benchmark_reuse_reason = "not_attempted"
    auto_draft_benchmark_stale = False
    auto_draft_benchmark_recency = _inspect_benchmark_report_recency(benchmark_report_path)
    auto_draft_benchmark_report_path: Path | None = None
    if raw_draft_benchmark_report_path in (None, ""):
        draft_stage_requested_from_seed_or_flag = bool(
            getattr(args, "draft_enable", False)
            or draft_seed_report_path is not None
        )
        if draft_stage_requested_from_seed_or_flag:
            if bool(auto_draft_benchmark_recency.get("exists")):
                benchmark_age_hours = auto_draft_benchmark_recency.get("age_hours")
                stale_guard_enabled = float(resolved_draft_benchmark_max_age_hours) > 0.0
                auto_draft_benchmark_stale = bool(
                    stale_guard_enabled
                    and benchmark_age_hours is not None
                    and float(benchmark_age_hours) > float(resolved_draft_benchmark_max_age_hours)
                )
                if auto_draft_benchmark_stale:
                    auto_draft_benchmark_reuse_status = "stale_skipped"
                    auto_draft_benchmark_reuse_reason = (
                        "auto_benchmark_age_exceeds_max_hours"
                        f" ({round(float(benchmark_age_hours), 4)} > {round(float(resolved_draft_benchmark_max_age_hours), 4)})"
                    )
                else:
                    auto_draft_benchmark_report_path = benchmark_report_path
                    auto_draft_benchmark_reuse_status = "reused"
                    auto_draft_benchmark_reuse_reason = "output_default_existing"
            else:
                auto_draft_benchmark_reuse_status = "missing"
                auto_draft_benchmark_reuse_reason = "output_default_benchmark_missing"
        else:
            auto_draft_benchmark_reuse_status = "not_requested"
            auto_draft_benchmark_reuse_reason = "draft_stage_not_requested"
    else:
        auto_draft_benchmark_reuse_status = "explicit_path_provided"
        auto_draft_benchmark_reuse_reason = "explicit_or_configured_benchmark_path"
    draft_benchmark_report_path: Path | None = None
    draft_benchmark_report_path_source = "none"
    if raw_draft_benchmark_report_path is not None and str(raw_draft_benchmark_report_path).strip():
        if getattr(args, "draft_benchmark_report_path", None) is not None:
            draft_benchmark_report_path = _resolve_path_from_base(
                raw_draft_benchmark_report_path,
                base_dir=Path.cwd(),
            ).resolve()
            draft_benchmark_report_path_source = "cli_override"
        else:
            draft_benchmark_report_path = _resolve_pipeline_path(
                raw_draft_benchmark_report_path,
                config_path=config_path,
            ).resolve()
            draft_benchmark_report_path_source = "config_global"
    elif auto_draft_benchmark_report_path is not None:
        draft_benchmark_report_path = auto_draft_benchmark_report_path
        draft_benchmark_report_path_source = "output_default_existing"
    elif auto_draft_benchmark_reuse_status == "stale_skipped":
        draft_benchmark_report_path_source = "output_default_existing_stale_skipped"
    draft_benchmark_min_opportunity_raw = (
        getattr(args, "draft_benchmark_min_opportunity", None)
        if getattr(args, "draft_benchmark_min_opportunity", None) is not None
        else operator_cycle_defaults.get("draft_benchmark_min_opportunity")
    )
    if draft_benchmark_min_opportunity_raw is not None and str(draft_benchmark_min_opportunity_raw).strip():
        draft_benchmark_min_opportunity = _coerce_float(draft_benchmark_min_opportunity_raw, default=0.0)
        draft_benchmark_min_opportunity_source = (
            "cli_override"
            if getattr(args, "draft_benchmark_min_opportunity", None) is not None
            else "config_global"
        )
    else:
        draft_benchmark_min_opportunity = None
        draft_benchmark_min_opportunity_source = "none"

    draft_requested = bool(
        getattr(args, "draft_enable", False)
        or draft_seed_report_path is not None
        or draft_benchmark_report_path is not None
    )
    draft_base_config_path = (
        _resolve_path_from_base(getattr(args, "draft_config_path"), base_dir=Path.cwd())
        if getattr(args, "draft_config_path", None) is not None
        else config_path
    )
    draft_output_config_path = (
        _resolve_path_from_base(getattr(args, "draft_output_config_path"), base_dir=Path.cwd())
        if getattr(args, "draft_output_config_path", None) is not None
        else draft_base_config_path.with_name(f"{draft_base_config_path.stem}.operator_cycle_drafted.json").resolve()
    )

    draft_resolution_domain = (
        str(getattr(args, "draft_domain", None) or "").strip().lower()
        if getattr(args, "draft_domain", None) is not None
        else ""
    ) or None
    if draft_resolution_domain is None and len(seed_domain_runs) == 1:
        only_seed_domain = str(seed_domain_runs[0].get("domain") or "").strip().lower()
        if only_seed_domain:
            draft_resolution_domain = only_seed_domain
    if draft_resolution_domain is None and draft_seed_report_path is not None and draft_seed_report_path.exists():
        try:
            loaded_draft_seed_domain = json.loads(draft_seed_report_path.read_text(encoding="utf-8"))
        except Exception:
            loaded_draft_seed_domain = None
        if isinstance(loaded_draft_seed_domain, dict):
            seed_domain_raw = str(loaded_draft_seed_domain.get("domain") or "").strip().lower()
            if seed_domain_raw and seed_domain_raw not in {"multi_domain", "all", "*"}:
                draft_resolution_domain = seed_domain_raw

    resolved_draft_statuses, draft_statuses_source = _resolve_draft_statuses(
        domain=draft_resolution_domain,
        cli_value=getattr(args, "draft_statuses", None),
        defaults=operator_cycle_defaults,
    )
    resolved_draft_limit, draft_limit_source = _resolve_draft_limit(
        domain=draft_resolution_domain,
        cli_value=getattr(args, "draft_limit", None),
        defaults=operator_cycle_defaults,
    )
    resolved_draft_lookup_limit, draft_lookup_limit_source = _resolve_draft_lookup_limit(
        domain=draft_resolution_domain,
        cli_value=getattr(args, "draft_lookup_limit", None),
        defaults=operator_cycle_defaults,
    )
    if resolved_draft_lookup_limit < resolved_draft_limit:
        resolved_draft_lookup_limit = int(resolved_draft_limit)
        draft_lookup_limit_source = f"{draft_lookup_limit_source}_raised_to_limit"
    resolved_draft_environment, draft_environment_source = _resolve_draft_environment(
        domain=draft_resolution_domain,
        cli_value=getattr(args, "draft_environment", None),
        defaults=operator_cycle_defaults,
    )
    resolved_draft_default_sample_size, draft_default_sample_size_source = _resolve_draft_default_sample_size(
        domain=draft_resolution_domain,
        cli_value=getattr(args, "draft_default_sample_size", None),
        defaults=operator_cycle_defaults,
    )
    if getattr(args, "draft_evidence_pressure_enable", None) is not None:
        resolved_draft_evidence_pressure_enable = bool(getattr(args, "draft_evidence_pressure_enable"))
        draft_evidence_pressure_enable_source = "cli_override"
    elif operator_cycle_defaults.get("draft_evidence_pressure_enable") is not None:
        resolved_draft_evidence_pressure_enable = _coerce_bool(
            operator_cycle_defaults.get("draft_evidence_pressure_enable"),
            default=True,
        )
        draft_evidence_pressure_enable_source = "config_global"
    else:
        resolved_draft_evidence_pressure_enable = True
        draft_evidence_pressure_enable_source = "builtin_default"
    if getattr(args, "draft_evidence_pressure_min_priority_boost", None) is not None:
        resolved_draft_evidence_pressure_min_priority_boost = max(
            0.0,
            _coerce_float(getattr(args, "draft_evidence_pressure_min_priority_boost"), default=0.35),
        )
        draft_evidence_pressure_min_priority_boost_source = "cli_override"
    elif operator_cycle_defaults.get("draft_evidence_pressure_min_priority_boost") is not None:
        resolved_draft_evidence_pressure_min_priority_boost = max(
            0.0,
            _coerce_float(operator_cycle_defaults.get("draft_evidence_pressure_min_priority_boost"), default=0.35),
        )
        draft_evidence_pressure_min_priority_boost_source = "config_global"
    else:
        resolved_draft_evidence_pressure_min_priority_boost = 0.35
        draft_evidence_pressure_min_priority_boost_source = "builtin_default"
    if getattr(args, "draft_evidence_pressure_limit_increase", None) is not None:
        resolved_draft_evidence_pressure_limit_increase = max(
            0,
            _coerce_int(getattr(args, "draft_evidence_pressure_limit_increase"), default=2),
        )
        draft_evidence_pressure_limit_increase_source = "cli_override"
    elif operator_cycle_defaults.get("draft_evidence_pressure_limit_increase") is not None:
        resolved_draft_evidence_pressure_limit_increase = max(
            0,
            _coerce_int(operator_cycle_defaults.get("draft_evidence_pressure_limit_increase"), default=2),
        )
        draft_evidence_pressure_limit_increase_source = "config_global"
    else:
        resolved_draft_evidence_pressure_limit_increase = 2
        draft_evidence_pressure_limit_increase_source = "builtin_default"
    if getattr(args, "draft_evidence_pressure_statuses", None) is not None:
        resolved_draft_evidence_pressure_statuses = (
            str(getattr(args, "draft_evidence_pressure_statuses") or "").strip()
            or "queued,testing"
        )
        draft_evidence_pressure_statuses_source = "cli_override"
    elif operator_cycle_defaults.get("draft_evidence_pressure_statuses") is not None:
        resolved_draft_evidence_pressure_statuses = (
            str(operator_cycle_defaults.get("draft_evidence_pressure_statuses") or "").strip()
            or "queued,testing"
        )
        draft_evidence_pressure_statuses_source = "config_global"
    else:
        resolved_draft_evidence_pressure_statuses = "queued,testing"
        draft_evidence_pressure_statuses_source = "builtin_default"

    draft_payload: dict[str, Any]
    daily_config_path = config_path
    draft_statuses_value = str(resolved_draft_statuses)
    draft_statuses_auto_broadened = False
    draft_statuses_auto_reason: str | None = None
    if (
        draft_seed_report_path is not None
        and bool(getattr(args, "draft_include_existing", False))
        and draft_statuses_value.strip().lower() == "queued"
        and draft_seed_report_path.exists()
    ):
        try:
            loaded_draft_seed_report = json.loads(draft_seed_report_path.read_text(encoding="utf-8"))
        except Exception:
            loaded_draft_seed_report = None
        if isinstance(loaded_draft_seed_report, dict):
            seed_created_count = max(0, int(loaded_draft_seed_report.get("created_count") or 0))
            seed_existing_count = max(0, int(loaded_draft_seed_report.get("existing_count") or 0))
            if seed_created_count <= 0 and seed_existing_count > 0:
                draft_statuses_value = "queued,testing,validated,rejected"
                draft_statuses_auto_broadened = True
                draft_statuses_auto_reason = "seed_created_zero_existing_present"
                draft_statuses_source = f"{draft_statuses_source}_auto_broadened"
    if draft_requested:
        try:
            draft_args = argparse.Namespace(
                seed_report_path=draft_seed_report_path,
                benchmark_report_path=draft_benchmark_report_path,
                benchmark_min_opportunity=draft_benchmark_min_opportunity,
                include_existing=bool(getattr(args, "draft_include_existing", False)),
                domain=(
                    str(getattr(args, "draft_domain")).strip()
                    if getattr(args, "draft_domain", None) is not None
                    else None
                ),
                statuses=draft_statuses_value,
                limit=max(1, int(resolved_draft_limit)),
                lookup_limit=max(1, int(resolved_draft_lookup_limit)),
                pipeline_config_path=draft_base_config_path,
                write_config_path=draft_output_config_path,
                in_place=False,
                artifacts_dir=draft_artifacts_dir,
                overwrite_artifacts=bool(getattr(args, "draft_overwrite_artifacts", False)),
                environment=resolved_draft_environment,
                default_sample_size=max(
                    1,
                    int(resolved_draft_default_sample_size),
                ),
                evidence_runtime_history_path=resolved_evidence_runtime_history_path,
                evidence_runtime_history_window=max(1, int(resolved_evidence_runtime_history_window)),
                evidence_pressure_enable=bool(resolved_draft_evidence_pressure_enable),
                evidence_pressure_min_priority_boost=float(resolved_draft_evidence_pressure_min_priority_boost),
                evidence_pressure_limit_increase=max(0, int(resolved_draft_evidence_pressure_limit_increase)),
                evidence_pressure_statuses=str(resolved_draft_evidence_pressure_statuses),
                strict=False,
                output_path=draft_report_path,
                json_compact=False,
                repo_path=args.repo_path,
                db_path=args.db_path,
            )
            draft_payload = _invoke_cli_json_command(
                cmd_improvement_draft_experiment_jobs,
                args=draft_args,
            )
            resolved_output = str(draft_payload.get("config_output_path") or "").strip()
            if resolved_output:
                daily_config_path = Path(resolved_output).expanduser().resolve()
            else:
                daily_config_path = draft_output_config_path

            # Drafted configs may be written outside the base config directory.
            # Normalize relative artifact paths against the base config root so
            # daily-pipeline can resolve them consistently from the drafted file location.
            rewritten_artifact_paths = 0
            if daily_config_path.exists():
                loaded_draft_config = json.loads(daily_config_path.read_text(encoding="utf-8"))
                if isinstance(loaded_draft_config, dict):
                    experiment_jobs = list(loaded_draft_config.get("experiment_jobs") or [])
                    changed = False
                    for row in experiment_jobs:
                        if not isinstance(row, dict):
                            continue
                        artifact_raw = row.get("artifact_path")
                        if artifact_raw is None or not str(artifact_raw).strip():
                            continue
                        artifact_path = Path(str(artifact_raw)).expanduser()
                        if artifact_path.is_absolute():
                            continue
                        resolved_artifact_path = (draft_base_config_path.parent / artifact_path).resolve()
                        row["artifact_path"] = str(resolved_artifact_path)
                        rewritten_artifact_paths += 1
                        changed = True
                    if changed:
                        loaded_draft_config["experiment_jobs"] = experiment_jobs
                        daily_config_path.write_text(json.dumps(loaded_draft_config, indent=2), encoding="utf-8")
            if rewritten_artifact_paths > 0:
                draft_payload["rewritten_artifact_paths"] = int(rewritten_artifact_paths)
            draft_payload["requested_statuses"] = draft_statuses_value
            draft_payload["statuses_auto_broadened"] = bool(draft_statuses_auto_broadened)
            draft_payload["statuses_auto_reason"] = draft_statuses_auto_reason
            draft_payload["resolved_domain"] = draft_resolution_domain
            draft_payload["statuses_source"] = str(draft_statuses_source)
            draft_payload["resolved_limit"] = int(resolved_draft_limit)
            draft_payload["limit_source"] = str(draft_limit_source)
            draft_payload["resolved_lookup_limit"] = int(resolved_draft_lookup_limit)
            draft_payload["lookup_limit_source"] = str(draft_lookup_limit_source)
            draft_payload["resolved_environment"] = str(resolved_draft_environment)
            draft_payload["environment_source"] = str(draft_environment_source)
            draft_payload["resolved_default_sample_size"] = int(resolved_draft_default_sample_size)
            draft_payload["default_sample_size_source"] = str(draft_default_sample_size_source)
            draft_payload["benchmark_report_path"] = (
                str(draft_benchmark_report_path) if draft_benchmark_report_path is not None else None
            )
            draft_payload["benchmark_report_path_source"] = str(draft_benchmark_report_path_source)
            draft_payload["benchmark_min_opportunity"] = (
                float(draft_benchmark_min_opportunity)
                if draft_benchmark_min_opportunity is not None
                else None
            )
            draft_payload["benchmark_min_opportunity_source"] = str(draft_benchmark_min_opportunity_source)
            draft_payload["benchmark_max_age_hours"] = float(resolved_draft_benchmark_max_age_hours)
            draft_payload["benchmark_max_age_hours_source"] = str(draft_benchmark_max_age_hours_source)
            draft_payload["benchmark_auto_reuse_status"] = str(auto_draft_benchmark_reuse_status)
            draft_payload["benchmark_auto_reuse_reason"] = str(auto_draft_benchmark_reuse_reason)
            draft_payload["benchmark_auto_reuse_stale"] = bool(auto_draft_benchmark_stale)
            draft_payload["benchmark_auto_reuse_age_hours"] = (
                _coerce_float(auto_draft_benchmark_recency.get("age_hours"), default=0.0)
                if auto_draft_benchmark_recency.get("age_hours") is not None
                else None
            )
            draft_payload["benchmark_auto_reuse_generated_at"] = auto_draft_benchmark_recency.get("generated_at")
            draft_payload["benchmark_auto_reuse_generated_at_source"] = str(
                auto_draft_benchmark_recency.get("generated_at_source") or "none"
            )
            draft_payload["benchmark_auto_reuse_parse_error"] = auto_draft_benchmark_recency.get("parse_error")
            draft_payload["benchmark_auto_reuse_inspection_path"] = str(
                auto_draft_benchmark_recency.get("path") or benchmark_report_path
            )
            draft_payload["evidence_runtime_history_path"] = str(resolved_evidence_runtime_history_path)
            draft_payload["evidence_runtime_history_path_source"] = evidence_runtime_history_path_source
            draft_payload["evidence_runtime_history_window"] = int(resolved_evidence_runtime_history_window)
            draft_payload["evidence_runtime_history_window_source"] = evidence_runtime_history_window_source
            draft_payload["resolved_evidence_pressure_enable"] = bool(resolved_draft_evidence_pressure_enable)
            draft_payload["evidence_pressure_enable_source"] = str(draft_evidence_pressure_enable_source)
            draft_payload["resolved_evidence_pressure_min_priority_boost"] = float(
                resolved_draft_evidence_pressure_min_priority_boost
            )
            draft_payload["evidence_pressure_min_priority_boost_source"] = str(
                draft_evidence_pressure_min_priority_boost_source
            )
            draft_payload["resolved_evidence_pressure_limit_increase"] = int(
                resolved_draft_evidence_pressure_limit_increase
            )
            draft_payload["evidence_pressure_limit_increase_source"] = str(
                draft_evidence_pressure_limit_increase_source
            )
            draft_payload["resolved_evidence_pressure_statuses"] = str(resolved_draft_evidence_pressure_statuses)
            draft_payload["evidence_pressure_statuses_source"] = str(draft_evidence_pressure_statuses_source)
        except Exception as exc:
            draft_payload = {
                "status": "error",
                "error": str(exc),
                "output_path": str(draft_report_path),
                "config_output_path": str(draft_output_config_path),
                "requested_statuses": draft_statuses_value,
                "statuses_auto_broadened": bool(draft_statuses_auto_broadened),
                "statuses_auto_reason": draft_statuses_auto_reason,
                "resolved_domain": draft_resolution_domain,
                "statuses_source": str(draft_statuses_source),
                "resolved_limit": int(resolved_draft_limit),
                "limit_source": str(draft_limit_source),
                "resolved_lookup_limit": int(resolved_draft_lookup_limit),
                "lookup_limit_source": str(draft_lookup_limit_source),
                "resolved_environment": str(resolved_draft_environment),
                "environment_source": str(draft_environment_source),
                "resolved_default_sample_size": int(resolved_draft_default_sample_size),
                "default_sample_size_source": str(draft_default_sample_size_source),
                "benchmark_report_path": (
                    str(draft_benchmark_report_path) if draft_benchmark_report_path is not None else None
                ),
                "benchmark_report_path_source": str(draft_benchmark_report_path_source),
                "benchmark_min_opportunity": (
                    float(draft_benchmark_min_opportunity)
                    if draft_benchmark_min_opportunity is not None
                    else None
                ),
                "benchmark_min_opportunity_source": str(draft_benchmark_min_opportunity_source),
                "benchmark_max_age_hours": float(resolved_draft_benchmark_max_age_hours),
                "benchmark_max_age_hours_source": str(draft_benchmark_max_age_hours_source),
                "benchmark_auto_reuse_status": str(auto_draft_benchmark_reuse_status),
                "benchmark_auto_reuse_reason": str(auto_draft_benchmark_reuse_reason),
                "benchmark_auto_reuse_stale": bool(auto_draft_benchmark_stale),
                "benchmark_auto_reuse_age_hours": (
                    _coerce_float(auto_draft_benchmark_recency.get("age_hours"), default=0.0)
                    if auto_draft_benchmark_recency.get("age_hours") is not None
                    else None
                ),
                "benchmark_auto_reuse_generated_at": auto_draft_benchmark_recency.get("generated_at"),
                "benchmark_auto_reuse_generated_at_source": str(
                    auto_draft_benchmark_recency.get("generated_at_source") or "none"
                ),
                "benchmark_auto_reuse_parse_error": auto_draft_benchmark_recency.get("parse_error"),
                "benchmark_auto_reuse_inspection_path": str(
                    auto_draft_benchmark_recency.get("path") or benchmark_report_path
                ),
                "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
                "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
                "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
                "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
                "resolved_evidence_pressure_enable": bool(resolved_draft_evidence_pressure_enable),
                "evidence_pressure_enable_source": str(draft_evidence_pressure_enable_source),
                "resolved_evidence_pressure_min_priority_boost": float(
                    resolved_draft_evidence_pressure_min_priority_boost
                ),
                "evidence_pressure_min_priority_boost_source": str(
                    draft_evidence_pressure_min_priority_boost_source
                ),
                "resolved_evidence_pressure_limit_increase": int(resolved_draft_evidence_pressure_limit_increase),
                "evidence_pressure_limit_increase_source": str(draft_evidence_pressure_limit_increase_source),
                "resolved_evidence_pressure_statuses": str(resolved_draft_evidence_pressure_statuses),
                "evidence_pressure_statuses_source": str(draft_evidence_pressure_statuses_source),
            }
            daily_config_path = draft_base_config_path
            stage_errors.append({"stage": "draft_experiment_jobs", "error": str(exc)})
    else:
        draft_payload = {
            "status": "skipped_not_requested",
            "output_path": str(draft_report_path),
            "config_output_path": str(config_path),
            "requested_statuses": draft_statuses_value,
            "statuses_auto_broadened": bool(draft_statuses_auto_broadened),
            "statuses_auto_reason": draft_statuses_auto_reason,
            "resolved_domain": draft_resolution_domain,
            "statuses_source": str(draft_statuses_source),
            "resolved_limit": int(resolved_draft_limit),
            "limit_source": str(draft_limit_source),
            "resolved_lookup_limit": int(resolved_draft_lookup_limit),
            "lookup_limit_source": str(draft_lookup_limit_source),
            "resolved_environment": str(resolved_draft_environment),
            "environment_source": str(draft_environment_source),
            "resolved_default_sample_size": int(resolved_draft_default_sample_size),
            "default_sample_size_source": str(draft_default_sample_size_source),
            "benchmark_report_path": (
                str(draft_benchmark_report_path) if draft_benchmark_report_path is not None else None
            ),
            "benchmark_report_path_source": str(draft_benchmark_report_path_source),
            "benchmark_min_opportunity": (
                float(draft_benchmark_min_opportunity)
                if draft_benchmark_min_opportunity is not None
                else None
            ),
            "benchmark_min_opportunity_source": str(draft_benchmark_min_opportunity_source),
            "benchmark_max_age_hours": float(resolved_draft_benchmark_max_age_hours),
            "benchmark_max_age_hours_source": str(draft_benchmark_max_age_hours_source),
            "benchmark_auto_reuse_status": str(auto_draft_benchmark_reuse_status),
            "benchmark_auto_reuse_reason": str(auto_draft_benchmark_reuse_reason),
            "benchmark_auto_reuse_stale": bool(auto_draft_benchmark_stale),
            "benchmark_auto_reuse_age_hours": (
                _coerce_float(auto_draft_benchmark_recency.get("age_hours"), default=0.0)
                if auto_draft_benchmark_recency.get("age_hours") is not None
                else None
            ),
            "benchmark_auto_reuse_generated_at": auto_draft_benchmark_recency.get("generated_at"),
            "benchmark_auto_reuse_generated_at_source": str(
                auto_draft_benchmark_recency.get("generated_at_source") or "none"
            ),
            "benchmark_auto_reuse_parse_error": auto_draft_benchmark_recency.get("parse_error"),
            "benchmark_auto_reuse_inspection_path": str(
                auto_draft_benchmark_recency.get("path") or benchmark_report_path
            ),
            "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
            "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
            "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
            "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
            "resolved_evidence_pressure_enable": bool(resolved_draft_evidence_pressure_enable),
            "evidence_pressure_enable_source": str(draft_evidence_pressure_enable_source),
            "resolved_evidence_pressure_min_priority_boost": float(resolved_draft_evidence_pressure_min_priority_boost),
            "evidence_pressure_min_priority_boost_source": str(draft_evidence_pressure_min_priority_boost_source),
            "resolved_evidence_pressure_limit_increase": int(resolved_draft_evidence_pressure_limit_increase),
            "evidence_pressure_limit_increase_source": str(draft_evidence_pressure_limit_increase_source),
            "resolved_evidence_pressure_statuses": str(resolved_draft_evidence_pressure_statuses),
            "evidence_pressure_statuses_source": str(draft_evidence_pressure_statuses_source),
        }

    daily_payload: dict[str, Any]
    try:
        daily_args = argparse.Namespace(
            config_path=daily_config_path,
            allow_missing_inputs=bool(args.allow_missing_inputs),
            strict=False,
            output_path=daily_report_path,
            json_compact=False,
            repo_path=args.repo_path,
            db_path=args.db_path,
        )
        daily_payload = _invoke_cli_json_command(
            cmd_improvement_daily_pipeline,
            args=daily_args,
        )
    except Exception as exc:
        daily_payload = {
            "status": "error",
            "error": str(exc),
            "output_path": str(daily_report_path),
        }
        stage_errors.append({"stage": "daily_pipeline", "error": str(exc)})

    retest_payload: dict[str, Any]
    should_run_retests = daily_report_path.exists()
    if should_run_retests:
        try:
            retest_args = argparse.Namespace(
                pipeline_report_path=daily_report_path,
                max_runs=args.retest_max_runs,
                artifact_dir=retest_artifact_dir,
                environment=args.retest_environment,
                notes_prefix=str(args.retest_notes_prefix or "operator_cycle_retest"),
                allow_missing_jobs=bool(args.allow_missing_retests),
                strict=False,
                output_path=retest_report_path,
                json_compact=False,
                repo_path=args.repo_path,
                db_path=args.db_path,
            )
            retest_payload = _invoke_cli_json_command(
                cmd_improvement_execute_retests,
                args=retest_args,
            )
        except Exception as exc:
            retest_payload = {
                "status": "error",
                "error": str(exc),
                "output_path": str(retest_report_path),
            }
            stage_errors.append({"stage": "execute_retests", "error": str(exc)})
    else:
        retest_payload = {
            "status": "skipped_missing_daily_report",
            "output_path": str(retest_report_path),
        }

    daily_experiment_runs = [
        row
        for row in list(daily_payload.get("experiment_runs") or [])
        if isinstance(row, dict)
    ]
    retest_runs = [
        row
        for row in list(retest_payload.get("runs") or [])
        if isinstance(row, dict)
    ]
    blockers: list[dict[str, Any]] = []
    for row in daily_experiment_runs:
        verdict = str(row.get("verdict") or "").strip().lower()
        seed_evidence_record_ids = _normalize_record_id_list(row.get("seed_evidence_record_ids"))
        evidence_lookup_refs = _build_evidence_lookup_refs(seed_evidence_record_ids)
        evidence_lookup_command = _build_operator_evidence_lookup_command(
            config_path=config_path,
            record_ids=seed_evidence_record_ids,
        )
        if verdict in {"blocked_guardrail", "insufficient_data", "needs_iteration", "invalid_measurement"}:
            blockers.append(
                {
                    "stage": "daily_pipeline",
                    "hypothesis_id": row.get("hypothesis_id"),
                    "run_id": row.get("run_id"),
                    "verdict": verdict,
                    "root_cause_hints": list(row.get("root_cause_hints") or []),
                    "seed_evidence_record_ids": list(seed_evidence_record_ids),
                    "evidence_lookup_refs": list(evidence_lookup_refs),
                    "evidence_lookup_command": evidence_lookup_command,
                }
            )
    retest_deltas: list[dict[str, Any]] = []
    for row in retest_runs:
        side_by_side = dict(row.get("side_by_side") or {}) if isinstance(row.get("side_by_side"), dict) else {}
        transition = (
            dict(side_by_side.get("verdict_transition") or {})
            if isinstance(side_by_side.get("verdict_transition"), dict)
            else {}
        )
        previous_verdict = str(transition.get("previous") or "").strip().lower() or None
        current_verdict = str(transition.get("current") or row.get("verdict") or "").strip().lower() or None
        seed_evidence_record_ids = _normalize_record_id_list(row.get("seed_evidence_record_ids"))
        evidence_lookup_refs = _build_evidence_lookup_refs(seed_evidence_record_ids)
        evidence_lookup_command = _build_operator_evidence_lookup_command(
            config_path=config_path,
            record_ids=seed_evidence_record_ids,
        )
        retest_deltas.append(
            {
                "hypothesis_id": row.get("hypothesis_id"),
                "trigger_run_id": row.get("trigger_run_id"),
                "run_id": row.get("run_id"),
                "previous_verdict": previous_verdict,
                "current_verdict": current_verdict,
                "metric_transition": side_by_side.get("metric_transition"),
                "sample_transition": side_by_side.get("sample_transition"),
                "seed_evidence_record_ids": list(seed_evidence_record_ids),
                "evidence_lookup_refs": list(evidence_lookup_refs),
                "evidence_lookup_command": evidence_lookup_command,
            }
        )
        if current_verdict in {"blocked_guardrail", "insufficient_data", "needs_iteration", "invalid_measurement"}:
            blockers.append(
                {
                    "stage": "execute_retests",
                    "hypothesis_id": row.get("hypothesis_id"),
                    "run_id": row.get("run_id"),
                    "verdict": current_verdict,
                    "root_cause_hints": [],
                    "seed_evidence_record_ids": list(seed_evidence_record_ids),
                    "evidence_lookup_refs": list(evidence_lookup_refs),
                    "evidence_lookup_command": evidence_lookup_command,
                }
            )

    promotion_candidates: list[dict[str, Any]] = []
    for row in daily_experiment_runs:
        if str(row.get("verdict") or "").strip().lower() == "promote":
            seed_evidence_record_ids = _normalize_record_id_list(row.get("seed_evidence_record_ids"))
            evidence_lookup_refs = _build_evidence_lookup_refs(seed_evidence_record_ids)
            evidence_lookup_command = _build_operator_evidence_lookup_command(
                config_path=config_path,
                record_ids=seed_evidence_record_ids,
            )
            promotion_candidates.append(
                {
                    "stage": "daily_pipeline",
                    "hypothesis_id": row.get("hypothesis_id"),
                    "run_id": row.get("run_id"),
                    "seed_evidence_record_ids": list(seed_evidence_record_ids),
                    "evidence_lookup_refs": list(evidence_lookup_refs),
                    "evidence_lookup_command": evidence_lookup_command,
                }
            )
    for row in retest_runs:
        if str(row.get("verdict") or "").strip().lower() == "promote":
            seed_evidence_record_ids = _normalize_record_id_list(row.get("seed_evidence_record_ids"))
            evidence_lookup_refs = _build_evidence_lookup_refs(seed_evidence_record_ids)
            evidence_lookup_command = _build_operator_evidence_lookup_command(
                config_path=config_path,
                record_ids=seed_evidence_record_ids,
            )
            promotion_candidates.append(
                {
                    "stage": "execute_retests",
                    "hypothesis_id": row.get("hypothesis_id"),
                    "run_id": row.get("run_id"),
                    "seed_evidence_record_ids": list(seed_evidence_record_ids),
                    "evidence_lookup_refs": list(evidence_lookup_refs),
                    "evidence_lookup_command": evidence_lookup_command,
                }
            )

    retest_transition_counts: dict[str, int] = {}
    for row in retest_deltas:
        previous = str(row.get("previous_verdict") or "unknown")
        current = str(row.get("current_verdict") or "unknown")
        key = f"{previous}->{current}"
        retest_transition_counts[key] = int(retest_transition_counts.get(key) or 0) + 1

    unresolved_verdicts = {"blocked_guardrail", "insufficient_data", "needs_iteration", "invalid_measurement"}
    evidence_lookup_batch_record_ids: list[str] = []
    for row in blockers:
        if not isinstance(row, dict):
            continue
        for record_id in _normalize_record_id_list(row.get("seed_evidence_record_ids")):
            if record_id in evidence_lookup_batch_record_ids:
                continue
            evidence_lookup_batch_record_ids.append(record_id)
    for row in retest_deltas:
        if not isinstance(row, dict):
            continue
        current_verdict = str(row.get("current_verdict") or "").strip().lower()
        if current_verdict and current_verdict not in unresolved_verdicts:
            continue
        for record_id in _normalize_record_id_list(row.get("seed_evidence_record_ids")):
            if record_id in evidence_lookup_batch_record_ids:
                continue
            evidence_lookup_batch_record_ids.append(record_id)
    evidence_lookup_batch_command = _build_operator_evidence_lookup_command(
        config_path=config_path,
        record_ids=evidence_lookup_batch_record_ids,
        output_path=evidence_lookup_report_path,
    )
    evidence_lookup_batch_ready = bool(evidence_lookup_batch_record_ids)
    evidence_lookup_batch = {
        "ready": evidence_lookup_batch_ready,
        "record_ids": list(evidence_lookup_batch_record_ids),
        "record_count": len(evidence_lookup_batch_record_ids),
        "output_path": str(evidence_lookup_report_path),
        "command": evidence_lookup_batch_command,
        "next_action": (
            "Run the batch evidence lookup command to resolve unresolved blocker/retest source records."
            if evidence_lookup_batch_ready
            else "No unresolved blocker/retest evidence record IDs detected."
        ),
    }

    metrics = {
        "daily_promotions": _count_verdict(daily_experiment_runs, "promote"),
        "daily_blocked_guardrail": _count_verdict(daily_experiment_runs, "blocked_guardrail"),
        "daily_insufficient_data": _count_verdict(daily_experiment_runs, "insufficient_data"),
        "daily_needs_iteration": _count_verdict(daily_experiment_runs, "needs_iteration"),
        "retest_promotions": _count_verdict(retest_runs, "promote"),
        "retest_blocked_guardrail": _count_verdict(retest_runs, "blocked_guardrail"),
        "retest_insufficient_data": _count_verdict(retest_runs, "insufficient_data"),
        "retest_needs_iteration": _count_verdict(retest_runs, "needs_iteration"),
        "blocker_count": len(blockers),
        "promotion_candidate_count": len(promotion_candidates),
        "promotion_count": len(promotion_candidates),
        "blocked_promotion_count": 0,
        "retest_delta_count": len(retest_deltas),
    }
    suggested_actions: list[str] = []
    if int(metrics["daily_blocked_guardrail"] or 0) > 0 or int(metrics["retest_blocked_guardrail"] or 0) > 0:
        suggested_actions.append("Prioritize guardrail failures and adjust candidate risk controls before scale-up.")
    if int(metrics["daily_insufficient_data"] or 0) > 0 or int(metrics["retest_insufficient_data"] or 0) > 0:
        suggested_actions.append("Increase sample sizes for unresolved hypotheses in controlled cohorts.")
    if int(metrics["retest_promotions"] or 0) > 0:
        suggested_actions.append("Promote retest winners to the next validation stage and monitor live guardrails.")
    if evidence_lookup_batch_ready:
        suggested_actions.append("Run batch evidence lookup to gather source snippets for unresolved blocker/retest IDs.")
    if not suggested_actions:
        suggested_actions.append("No urgent blockers detected; continue ingesting feedback and validating new hypotheses.")

    stage_statuses = {
        "pull_feeds": str(pull_payload.get("status") or ""),
        "fitness_leaderboard": str(leaderboard_payload.get("status") or ""),
        "seed_from_leaderboard": str(seed_payload.get("status") or ""),
        "draft_experiment_jobs": str(draft_payload.get("status") or ""),
        "daily_pipeline": str(daily_payload.get("status") or ""),
        "execute_retests": str(retest_payload.get("status") or ""),
    }
    resolved_benchmark_top_limit, benchmark_top_limit_source = _resolve_benchmark_top_limit(
        cli_value=getattr(args, "benchmark_top_limit", None),
        defaults=operator_cycle_defaults,
    )
    benchmark_requested = bool(
        getattr(args, "benchmark_enable", False)
        or getattr(args, "benchmark_report_path", None) is not None
    )
    resolved_verify_matrix_path, verify_matrix_path_source = _resolve_verify_matrix_path(
        cli_value=getattr(args, "verify_matrix_path", None),
        defaults=operator_cycle_defaults,
        config_path=config_path,
    )
    verify_matrix_requested = bool(
        getattr(args, "verify_matrix_enable", False)
        or getattr(args, "verify_matrix_path", None) is not None
        or resolved_verify_matrix_path is not None
    )
    verify_matrix_alert_requested = bool(
        getattr(args, "verify_matrix_alert_enable", False)
        or getattr(args, "verify_matrix_alert_report_path", None) is not None
        or _coerce_bool(operator_cycle_defaults.get("verify_matrix_alert_enable"), default=False)
    )
    resolved_verify_matrix_alert_domain = (
        str(getattr(args, "verify_matrix_alert_domain", None)).strip()
        if getattr(args, "verify_matrix_alert_domain", None) is not None
        else str(operator_cycle_defaults.get("verify_matrix_alert_domain") or "").strip()
    ) or "markets"
    if getattr(args, "verify_matrix_alert_max_items", None) is not None:
        resolved_verify_matrix_alert_max_items = max(1, int(getattr(args, "verify_matrix_alert_max_items") or 1))
    else:
        resolved_verify_matrix_alert_max_items = max(
            1,
            _coerce_int(operator_cycle_defaults.get("verify_matrix_alert_max_items"), default=3),
        )
    resolved_verify_matrix_alert_urgency = (
        getattr(args, "verify_matrix_alert_urgency")
        if getattr(args, "verify_matrix_alert_urgency", None) is not None
        else operator_cycle_defaults.get("verify_matrix_alert_urgency")
    )
    resolved_verify_matrix_alert_confidence = (
        getattr(args, "verify_matrix_alert_confidence")
        if getattr(args, "verify_matrix_alert_confidence", None) is not None
        else operator_cycle_defaults.get("verify_matrix_alert_confidence")
    )
    knowledge_delta_alert_enable_arg = (
        getattr(args, "knowledge_delta_alert_enable", None)
        if getattr(args, "knowledge_delta_alert_enable", None) is not None
        else getattr(args, "knowledge_brief_delta_alert_enable", None)
    )
    knowledge_delta_alert_domain_arg = (
        getattr(args, "knowledge_delta_alert_domain", None)
        if getattr(args, "knowledge_delta_alert_domain", None) is not None
        else getattr(args, "knowledge_brief_delta_alert_domain", None)
    )
    knowledge_delta_alert_max_items_arg = (
        getattr(args, "knowledge_delta_alert_max_items", None)
        if getattr(args, "knowledge_delta_alert_max_items", None) is not None
        else getattr(args, "knowledge_brief_delta_alert_max_items", None)
    )
    knowledge_delta_alert_urgency_arg = (
        getattr(args, "knowledge_delta_alert_urgency", None)
        if getattr(args, "knowledge_delta_alert_urgency", None) is not None
        else getattr(args, "knowledge_brief_delta_alert_urgency", None)
    )
    knowledge_delta_alert_confidence_arg = (
        getattr(args, "knowledge_delta_alert_confidence", None)
        if getattr(args, "knowledge_delta_alert_confidence", None) is not None
        else getattr(args, "knowledge_brief_delta_alert_confidence", None)
    )
    knowledge_brief_enable_arg = getattr(args, "knowledge_brief_enable", None)
    knowledge_brief_requested = bool(
        _coerce_bool(knowledge_brief_enable_arg, default=False)
        or _coerce_bool(knowledge_delta_alert_enable_arg, default=False)
        or getattr(args, "knowledge_brief_report_path", None) is not None
        or knowledge_delta_alert_report_path_value is not None
        or _coerce_bool(operator_cycle_defaults.get("knowledge_brief_enable"), default=False)
        or _coerce_bool(operator_cycle_defaults.get("knowledge_delta_alert_enable"), default=False)
    )
    resolved_knowledge_brief_query = (
        str(getattr(args, "knowledge_brief_query", None)).strip()
        if getattr(args, "knowledge_brief_query", None) is not None
        else str(operator_cycle_defaults.get("knowledge_brief_query") or "").strip()
    )
    resolved_knowledge_brief_snapshot_label = (
        str(getattr(args, "knowledge_brief_snapshot_label", None)).strip()
        if getattr(args, "knowledge_brief_snapshot_label", None) is not None
        else str(operator_cycle_defaults.get("knowledge_brief_snapshot_label") or "").strip()
    ) or None
    resolved_knowledge_brief_displeasure_limit = max(
        1,
        _coerce_int(operator_cycle_defaults.get("knowledge_brief_displeasure_limit"), default=8),
    )
    resolved_knowledge_brief_hypothesis_limit = max(
        1,
        _coerce_int(operator_cycle_defaults.get("knowledge_brief_hypothesis_limit"), default=80),
    )
    resolved_knowledge_brief_experiment_limit = max(
        1,
        _coerce_int(operator_cycle_defaults.get("knowledge_brief_experiment_limit"), default=120),
    )
    resolved_knowledge_brief_controlled_test_limit = max(
        1,
        _coerce_int(operator_cycle_defaults.get("knowledge_brief_controlled_test_limit"), default=5),
    )
    resolved_knowledge_brief_min_cluster_count = max(
        1,
        _coerce_int(operator_cycle_defaults.get("knowledge_brief_min_cluster_count"), default=2),
    )
    knowledge_delta_alert_requested = bool(
        _coerce_bool(knowledge_delta_alert_enable_arg, default=False)
        or knowledge_delta_alert_report_path_value is not None
        or getattr(args, "knowledge_delta_snapshot_dir", None) is not None
        or getattr(args, "knowledge_delta_current_snapshot_path", None) is not None
        or getattr(args, "knowledge_delta_previous_snapshot_path", None) is not None
        or _coerce_bool(operator_cycle_defaults.get("knowledge_delta_alert_enable"), default=False)
    )
    if knowledge_delta_alert_requested:
        knowledge_brief_requested = True
    resolved_knowledge_delta_domains_raw = (
        getattr(args, "knowledge_delta_domains", None)
        if getattr(args, "knowledge_delta_domains", None) is not None
        else operator_cycle_defaults.get("knowledge_delta_domains")
    )
    resolved_knowledge_delta_domains = str(resolved_knowledge_delta_domains_raw or "").strip()
    if str(resolved_knowledge_delta_domains).lower() in {"none", "null"}:
        resolved_knowledge_delta_domains = ""
    if not resolved_knowledge_delta_domains:
        resolved_knowledge_delta_domains = DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV

    raw_knowledge_snapshot_dir = (
        getattr(args, "knowledge_delta_snapshot_dir", None)
        if getattr(args, "knowledge_delta_snapshot_dir", None) is not None
        else operator_cycle_defaults.get("knowledge_delta_snapshot_dir")
    )
    resolved_knowledge_snapshot_dir: Path | None = None
    if raw_knowledge_snapshot_dir is not None and str(raw_knowledge_snapshot_dir).strip():
        if getattr(args, "knowledge_delta_snapshot_dir", None) is not None:
            resolved_knowledge_snapshot_dir = _resolve_path_from_base(
                raw_knowledge_snapshot_dir,
                base_dir=Path.cwd(),
            ).resolve()
        else:
            resolved_knowledge_snapshot_dir = _resolve_pipeline_path(
                raw_knowledge_snapshot_dir,
                config_path=config_path,
            ).resolve()

    raw_knowledge_current_snapshot_path = (
        getattr(args, "knowledge_delta_current_snapshot_path", None)
        if getattr(args, "knowledge_delta_current_snapshot_path", None) is not None
        else operator_cycle_defaults.get("knowledge_delta_current_snapshot_path")
    )
    resolved_knowledge_current_snapshot_path: Path | None = None
    if raw_knowledge_current_snapshot_path is not None and str(raw_knowledge_current_snapshot_path).strip():
        if getattr(args, "knowledge_delta_current_snapshot_path", None) is not None:
            resolved_knowledge_current_snapshot_path = _resolve_path_from_base(
                raw_knowledge_current_snapshot_path,
                base_dir=Path.cwd(),
            ).resolve()
        else:
            resolved_knowledge_current_snapshot_path = _resolve_pipeline_path(
                raw_knowledge_current_snapshot_path,
                config_path=config_path,
            ).resolve()

    raw_knowledge_previous_snapshot_path = (
        getattr(args, "knowledge_delta_previous_snapshot_path", None)
        if getattr(args, "knowledge_delta_previous_snapshot_path", None) is not None
        else operator_cycle_defaults.get("knowledge_delta_previous_snapshot_path")
    )
    resolved_knowledge_previous_snapshot_path: Path | None = None
    if raw_knowledge_previous_snapshot_path is not None and str(raw_knowledge_previous_snapshot_path).strip():
        if getattr(args, "knowledge_delta_previous_snapshot_path", None) is not None:
            resolved_knowledge_previous_snapshot_path = _resolve_path_from_base(
                raw_knowledge_previous_snapshot_path,
                base_dir=Path.cwd(),
            ).resolve()
        else:
            resolved_knowledge_previous_snapshot_path = _resolve_pipeline_path(
                raw_knowledge_previous_snapshot_path,
                config_path=config_path,
            ).resolve()

    if getattr(args, "knowledge_delta_top_limit", None) is not None:
        resolved_knowledge_delta_top_limit = max(1, int(getattr(args, "knowledge_delta_top_limit") or 1))
    else:
        resolved_knowledge_delta_top_limit = max(
            1,
            _coerce_int(operator_cycle_defaults.get("knowledge_delta_top_limit"), default=10),
        )
    resolved_knowledge_delta_alert_domain = (
        str(knowledge_delta_alert_domain_arg).strip()
        if knowledge_delta_alert_domain_arg is not None
        else str(operator_cycle_defaults.get("knowledge_delta_alert_domain") or "").strip()
    ) or "operations"
    if knowledge_delta_alert_max_items_arg is not None:
        resolved_knowledge_delta_alert_max_items = max(
            1,
            int(knowledge_delta_alert_max_items_arg or 1),
        )
    else:
        resolved_knowledge_delta_alert_max_items = max(
            1,
            _coerce_int(operator_cycle_defaults.get("knowledge_delta_alert_max_items"), default=3),
        )
    resolved_knowledge_delta_alert_urgency = (
        knowledge_delta_alert_urgency_arg
        if knowledge_delta_alert_urgency_arg is not None
        else operator_cycle_defaults.get("knowledge_delta_alert_urgency")
    )
    resolved_knowledge_delta_alert_confidence = (
        knowledge_delta_alert_confidence_arg
        if knowledge_delta_alert_confidence_arg is not None
        else operator_cycle_defaults.get("knowledge_delta_alert_confidence")
    )
    if getattr(args, "knowledge_delta_min_worsening_score", None) is not None:
        resolved_knowledge_delta_min_worsening_score = max(
            1,
            int(getattr(args, "knowledge_delta_min_worsening_score") or 1),
        )
    else:
        resolved_knowledge_delta_min_worsening_score = max(
            1,
            _coerce_int(operator_cycle_defaults.get("knowledge_delta_min_worsening_score"), default=2),
        )
    if getattr(args, "knowledge_delta_min_urgency_delta", None) is not None:
        resolved_knowledge_delta_min_urgency_delta = max(
            0.0,
            float(getattr(args, "knowledge_delta_min_urgency_delta") or 0.0),
        )
    else:
        resolved_knowledge_delta_min_urgency_delta = max(
            0.0,
            _coerce_float(operator_cycle_defaults.get("knowledge_delta_min_urgency_delta"), default=0.25),
        )
    if getattr(args, "knowledge_delta_min_failure_rate_delta", None) is not None:
        resolved_knowledge_delta_min_failure_rate_delta = max(
            0.0,
            float(getattr(args, "knowledge_delta_min_failure_rate_delta") or 0.0),
        )
    else:
        resolved_knowledge_delta_min_failure_rate_delta = max(
            0.0,
            _coerce_float(operator_cycle_defaults.get("knowledge_delta_min_failure_rate_delta"), default=0.05),
        )
    if getattr(args, "knowledge_delta_min_blocked_guardrail_delta", None) is not None:
        resolved_knowledge_delta_min_blocked_guardrail_delta = max(
            1,
            int(getattr(args, "knowledge_delta_min_blocked_guardrail_delta") or 1),
        )
    else:
        resolved_knowledge_delta_min_blocked_guardrail_delta = max(
            1,
            _coerce_int(operator_cycle_defaults.get("knowledge_delta_min_blocked_guardrail_delta"), default=1),
        )
    promotion_lock_recheck_command = _build_operator_cycle_recheck_command(
        config_path=config_path,
        output_dir=output_dir,
        verify_matrix_path=resolved_verify_matrix_path,
        verify_matrix_alert_domain=resolved_verify_matrix_alert_domain,
        verify_matrix_alert_max_items=resolved_verify_matrix_alert_max_items,
        verify_matrix_alert_urgency=resolved_verify_matrix_alert_urgency,
        verify_matrix_alert_confidence=resolved_verify_matrix_alert_confidence,
    )
    benchmark_payload: dict[str, Any]
    verify_payload: dict[str, Any]
    verify_alert_payload: dict[str, Any]
    knowledge_brief_payload: dict[str, Any]
    knowledge_delta_alert_payload: dict[str, Any]
    stage_error_count = (
        int(pull_payload.get("error_count") or 0)
        + int(leaderboard_payload.get("error_count") or 0)
        + int(seed_payload.get("error_count") or 0)
        + int(draft_payload.get("error_count") or 0)
        + int(daily_payload.get("error_count") or 0)
        + int(retest_payload.get("error_count") or 0)
        + len(stage_errors)
    )
    overall_status = "warning" if (
        stage_errors
        or stage_error_count > 0
        or any(status == "error" for status in stage_statuses.values())
    ) else "ok"

    inbox_summary_base = {
        "generated_at": utc_now_iso(),
        "config_path": str(config_path),
        "daily_config_path": str(daily_config_path),
        "output_dir": str(output_dir),
        "stage_statuses": stage_statuses,
        "metrics": metrics,
        "promotions": promotion_candidates,
        "blockers": blockers,
        "retest_deltas": retest_deltas,
        "retest_transition_counts": retest_transition_counts,
        "evidence_lookup_batch": evidence_lookup_batch,
        "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
        "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
        "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
        "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
        "benchmark_stale_runtime_history_window": int(resolved_benchmark_stale_runtime_history_window),
        "benchmark_stale_runtime_history_window_source": benchmark_stale_runtime_history_window_source,
        "benchmark_stale_runtime_repeat_threshold": int(resolved_benchmark_stale_runtime_repeat_threshold),
        "benchmark_stale_runtime_repeat_threshold_source": benchmark_stale_runtime_repeat_threshold_source,
        "benchmark_stale_runtime_rate_ceiling": round(float(resolved_benchmark_stale_runtime_rate_ceiling), 4),
        "benchmark_stale_runtime_rate_ceiling_source": benchmark_stale_runtime_rate_ceiling_source,
        "benchmark_stale_runtime_consecutive_runs": int(resolved_benchmark_stale_runtime_consecutive_runs),
        "benchmark_stale_runtime_consecutive_runs_source": benchmark_stale_runtime_consecutive_runs_source,
        "suggested_actions": suggested_actions,
    }

    payload_for_benchmark = {
        "generated_at": utc_now_iso(),
        "status": overall_status,
        "config_path": str(config_path),
        "daily_config_path": str(daily_config_path),
        "output_dir": str(output_dir),
        "pull_report_path": str(pull_report_path),
        "seed_leaderboard_report_path": str(seed_leaderboard_report_path),
        "seed_report_path": str(seed_report_output_path),
        "draft_report_path": str(draft_report_path),
        "draft_output_config_path": str(draft_output_config_path),
        "draft_artifacts_dir": str(draft_artifacts_dir),
        "evidence_lookup_report_path": str(evidence_lookup_report_path),
        "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
        "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
        "benchmark_stale_runtime_history_window": int(resolved_benchmark_stale_runtime_history_window),
        "benchmark_stale_runtime_history_window_source": benchmark_stale_runtime_history_window_source,
        "benchmark_stale_runtime_repeat_threshold": int(resolved_benchmark_stale_runtime_repeat_threshold),
        "benchmark_stale_runtime_repeat_threshold_source": benchmark_stale_runtime_repeat_threshold_source,
        "benchmark_stale_runtime_rate_ceiling": round(float(resolved_benchmark_stale_runtime_rate_ceiling), 4),
        "benchmark_stale_runtime_rate_ceiling_source": benchmark_stale_runtime_rate_ceiling_source,
        "benchmark_stale_runtime_consecutive_runs": int(resolved_benchmark_stale_runtime_consecutive_runs),
        "benchmark_stale_runtime_consecutive_runs_source": benchmark_stale_runtime_consecutive_runs_source,
        "daily_report_path": str(daily_report_path),
        "retest_report_path": str(retest_report_path),
        "inbox_summary_path": str(inbox_summary_path),
        "stage_statuses": stage_statuses,
        "stage_error_count": stage_error_count,
        "stage_errors": stage_errors,
        "seed_domains": list(seed_domains),
        "seed_domain_runs": seed_domain_runs,
        "fitness_leaderboard": leaderboard_payload,
        "seed_from_leaderboard": seed_payload,
        "draft": draft_payload,
        "evidence_lookup_batch": evidence_lookup_batch,
        "summary": inbox_summary_base,
    }
    operator_report_path.parent.mkdir(parents=True, exist_ok=True)
    operator_report_path.write_text(json.dumps(payload_for_benchmark, indent=2), encoding="utf-8")

    if benchmark_requested:
        try:
            benchmark_args = argparse.Namespace(
                report_path=operator_report_path,
                top_limit=max(1, int(resolved_benchmark_top_limit)),
                output_path=benchmark_report_path,
                evidence_runtime_history_path=resolved_evidence_runtime_history_path,
                evidence_runtime_history_window=max(1, int(resolved_evidence_runtime_history_window)),
                strict=False,
                json_compact=False,
            )
            benchmark_payload = _invoke_cli_json_command(
                cmd_improvement_benchmark_frustrations,
                args=benchmark_args,
            )
            benchmark_payload["top_limit_source"] = str(benchmark_top_limit_source)
            benchmark_payload["evidence_runtime_history_path"] = str(resolved_evidence_runtime_history_path)
            benchmark_payload["evidence_runtime_history_path_source"] = evidence_runtime_history_path_source
            benchmark_payload["evidence_runtime_history_window"] = int(resolved_evidence_runtime_history_window)
            benchmark_payload["evidence_runtime_history_window_source"] = evidence_runtime_history_window_source
        except Exception as exc:
            benchmark_payload = {
                "status": "error",
                "error": str(exc),
                "output_path": str(benchmark_report_path),
                "top_limit": int(resolved_benchmark_top_limit),
                "top_limit_source": str(benchmark_top_limit_source),
                "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
                "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
                "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
                "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
            }
            stage_errors.append({"stage": "benchmark_frustrations", "error": str(exc)})
    else:
        benchmark_payload = {
            "status": "skipped_not_requested",
            "output_path": str(benchmark_report_path),
            "top_limit": int(resolved_benchmark_top_limit),
            "top_limit_source": str(benchmark_top_limit_source),
            "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
            "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
            "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
            "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
        }

    benchmark_actions = [
        str(item).strip()
        for item in list(benchmark_payload.get("suggested_actions") or [])
        if str(item).strip()
    ]
    for action in benchmark_actions[:2]:
        if action not in suggested_actions:
            suggested_actions.append(action)

    stage_statuses["benchmark_frustrations"] = str(benchmark_payload.get("status") or "")
    if verify_matrix_requested:
        if resolved_verify_matrix_path is None:
            verify_payload = {
                "status": "error",
                "error": "missing_verify_matrix_path",
                "output_path": str(verify_matrix_report_path),
                "matrix_path_source": str(verify_matrix_path_source),
                "error_count": 1,
            }
            stage_errors.append({"stage": "verify_matrix", "error": "missing_verify_matrix_path"})
        else:
            try:
                verify_args = argparse.Namespace(
                    matrix_path=resolved_verify_matrix_path,
                    report_path=operator_report_path,
                    output_path=verify_matrix_report_path,
                    strict=False,
                    json_compact=False,
                )
                verify_payload = _invoke_cli_json_command(
                    cmd_improvement_verify_matrix,
                    args=verify_args,
                )
                verify_payload["matrix_path_source"] = str(verify_matrix_path_source)
            except Exception as exc:
                verify_payload = {
                    "status": "error",
                    "error": str(exc),
                    "output_path": str(verify_matrix_report_path),
                    "matrix_path": str(resolved_verify_matrix_path),
                    "matrix_path_source": str(verify_matrix_path_source),
                    "error_count": 1,
                }
                stage_errors.append({"stage": "verify_matrix", "error": str(exc)})
    else:
        verify_payload = {
            "status": "skipped_not_requested",
            "output_path": str(verify_matrix_report_path),
            "matrix_path": (
                str(resolved_verify_matrix_path)
                if resolved_verify_matrix_path is not None
                else None
            ),
            "matrix_path_source": str(verify_matrix_path_source),
            "error_count": 0,
        }

    if str(verify_payload.get("status") or "") == "warning":
        verify_drift_severity = str(verify_payload.get("drift_severity") or "unknown")
        suggested_actions.append(
            "Resolve verify-matrix drift before advancing promotions "
            f"(severity={verify_drift_severity})."
        )

    stage_statuses["verify_matrix"] = str(verify_payload.get("status") or "")
    if verify_matrix_alert_requested:
        if resolved_verify_matrix_path is None:
            verify_alert_payload = {
                "status": "error",
                "error": "missing_verify_matrix_path",
                "output_path": str(verify_matrix_alert_report_path),
                "error_count": 1,
                "alert_created": False,
            }
            stage_errors.append({"stage": "verify_matrix_alert", "error": "missing_verify_matrix_path"})
        else:
            try:
                verify_alert_args = argparse.Namespace(
                    matrix_path=resolved_verify_matrix_path,
                    report_path=operator_report_path,
                    alert_domain=resolved_verify_matrix_alert_domain,
                    alert_urgency=resolved_verify_matrix_alert_urgency,
                    alert_confidence=resolved_verify_matrix_alert_confidence,
                    alert_max_items=max(1, int(resolved_verify_matrix_alert_max_items)),
                    output_path=verify_matrix_alert_report_path,
                    strict=False,
                    json_compact=False,
                    repo_path=args.repo_path,
                    db_path=args.db_path,
                )
                verify_alert_payload = _invoke_cli_json_command(
                    cmd_improvement_verify_matrix_alert,
                    args=verify_alert_args,
                )
                verify_alert_payload["alert_domain_source"] = (
                    "cli_override"
                    if getattr(args, "verify_matrix_alert_domain", None) is not None
                    else (
                        "config_global"
                        if operator_cycle_defaults.get("verify_matrix_alert_domain") is not None
                        else "builtin_default"
                    )
                )
                verify_alert_payload["alert_max_items_source"] = (
                    "cli_override"
                    if getattr(args, "verify_matrix_alert_max_items", None) is not None
                    else (
                        "config_global"
                        if operator_cycle_defaults.get("verify_matrix_alert_max_items") is not None
                        else "builtin_default"
                    )
                )
            except Exception as exc:
                verify_alert_payload = {
                    "status": "error",
                    "error": str(exc),
                    "output_path": str(verify_matrix_alert_report_path),
                    "matrix_path": str(resolved_verify_matrix_path),
                    "error_count": 1,
                    "alert_created": False,
                }
                stage_errors.append({"stage": "verify_matrix_alert", "error": str(exc)})
    else:
        verify_alert_payload = {
            "status": "skipped_not_requested",
            "output_path": str(verify_matrix_alert_report_path),
            "matrix_path": (
                str(resolved_verify_matrix_path)
                if resolved_verify_matrix_path is not None
                else None
            ),
            "error_count": 0,
            "alert_created": False,
        }

    verify_alert_actions = [
        str(item).strip()
        for item in list(verify_alert_payload.get("mitigation_actions") or [])
        if str(item).strip()
    ]
    for action in verify_alert_actions[:2]:
        if action not in suggested_actions:
            suggested_actions.append(action)

    stage_statuses["verify_matrix_alert"] = str(verify_alert_payload.get("status") or "")
    if knowledge_brief_requested:
        try:
            knowledge_brief_args = argparse.Namespace(
                domains=resolved_knowledge_delta_domains,
                query=resolved_knowledge_brief_query,
                displeasure_limit=max(1, int(resolved_knowledge_brief_displeasure_limit)),
                hypothesis_limit=max(1, int(resolved_knowledge_brief_hypothesis_limit)),
                experiment_limit=max(1, int(resolved_knowledge_brief_experiment_limit)),
                controlled_test_limit=max(1, int(resolved_knowledge_brief_controlled_test_limit)),
                min_cluster_count=max(1, int(resolved_knowledge_brief_min_cluster_count)),
                snapshot_dir=resolved_knowledge_snapshot_dir,
                snapshot_label=resolved_knowledge_brief_snapshot_label,
                write_snapshot=True,
                output_path=knowledge_brief_report_path,
                strict=False,
                json_compact=False,
                repo_path=args.repo_path,
                db_path=args.db_path,
            )
            knowledge_brief_payload = _invoke_cli_json_command(
                cmd_improvement_knowledge_brief,
                args=knowledge_brief_args,
            )
            knowledge_brief_payload["domains_source"] = (
                "cli_override"
                if getattr(args, "knowledge_delta_domains", None) is not None
                else (
                    "config_global"
                    if operator_cycle_defaults.get("knowledge_delta_domains") is not None
                    else "builtin_default"
                )
            )
            knowledge_brief_payload["query_source"] = (
                "cli_override"
                if getattr(args, "knowledge_brief_query", None) is not None
                else (
                    "config_global"
                    if operator_cycle_defaults.get("knowledge_brief_query") is not None
                    else "builtin_default"
                )
            )
            knowledge_brief_payload["snapshot_label_source"] = (
                "cli_override"
                if getattr(args, "knowledge_brief_snapshot_label", None) is not None
                else (
                    "config_global"
                    if operator_cycle_defaults.get("knowledge_brief_snapshot_label") is not None
                    else "builtin_default"
                )
            )
        except Exception as exc:
            knowledge_brief_payload = {
                "status": "error",
                "error": str(exc),
                "output_path": str(knowledge_brief_report_path),
                "error_count": 1,
                "domains": _normalize_improvement_knowledge_domains(resolved_knowledge_delta_domains),
            }
            stage_errors.append({"stage": "knowledge_brief", "error": str(exc)})
    else:
        knowledge_brief_payload = {
            "status": "skipped_not_requested",
            "output_path": str(knowledge_brief_report_path),
            "error_count": 0,
            "domains": _normalize_improvement_knowledge_domains(resolved_knowledge_delta_domains),
        }

    knowledge_brief_actions = [
        str(item).strip()
        for item in list(knowledge_brief_payload.get("suggested_actions") or [])
        if str(item).strip()
    ]
    for action in knowledge_brief_actions[:2]:
        if action not in suggested_actions:
            suggested_actions.append(action)

    stage_statuses["knowledge_brief"] = str(knowledge_brief_payload.get("status") or "")
    if knowledge_delta_alert_requested:
        try:
            knowledge_delta_alert_args = argparse.Namespace(
                domains=resolved_knowledge_delta_domains,
                snapshot_dir=resolved_knowledge_snapshot_dir,
                current_snapshot_path=resolved_knowledge_current_snapshot_path,
                previous_snapshot_path=resolved_knowledge_previous_snapshot_path,
                top_limit=max(1, int(resolved_knowledge_delta_top_limit)),
                alert_domain=resolved_knowledge_delta_alert_domain,
                alert_urgency=resolved_knowledge_delta_alert_urgency,
                alert_confidence=resolved_knowledge_delta_alert_confidence,
                alert_max_items=max(1, int(resolved_knowledge_delta_alert_max_items)),
                min_worsening_score=max(1, int(resolved_knowledge_delta_min_worsening_score)),
                min_urgency_delta=max(0.0, float(resolved_knowledge_delta_min_urgency_delta)),
                min_failure_rate_delta=max(0.0, float(resolved_knowledge_delta_min_failure_rate_delta)),
                min_blocked_guardrail_delta=max(1, int(resolved_knowledge_delta_min_blocked_guardrail_delta)),
                evidence_runtime_history_path=resolved_evidence_runtime_history_path,
                evidence_runtime_history_window=max(1, int(resolved_evidence_runtime_history_window)),
                output_path=knowledge_brief_delta_alert_report_path,
                strict=False,
                json_compact=False,
                repo_path=args.repo_path,
                db_path=args.db_path,
            )
            knowledge_delta_alert_payload = _invoke_cli_json_command(
                cmd_improvement_knowledge_brief_delta_alert,
                args=knowledge_delta_alert_args,
            )
            knowledge_delta_alert_payload["domains_source"] = (
                "cli_override"
                if getattr(args, "knowledge_delta_domains", None) is not None
                else (
                    "config_global"
                    if operator_cycle_defaults.get("knowledge_delta_domains") is not None
                    else "builtin_default"
                )
            )
            knowledge_delta_alert_payload["top_limit_source"] = (
                "cli_override"
                if getattr(args, "knowledge_delta_top_limit", None) is not None
                else (
                    "config_global"
                    if operator_cycle_defaults.get("knowledge_delta_top_limit") is not None
                    else "builtin_default"
                )
            )
            knowledge_delta_alert_payload["alert_domain_source"] = (
                "cli_override"
                if knowledge_delta_alert_domain_arg is not None
                else (
                    "config_global"
                    if operator_cycle_defaults.get("knowledge_delta_alert_domain") is not None
                    else "builtin_default"
                )
            )
            knowledge_delta_alert_payload["alert_max_items_source"] = (
                "cli_override"
                if knowledge_delta_alert_max_items_arg is not None
                else (
                    "config_global"
                    if operator_cycle_defaults.get("knowledge_delta_alert_max_items") is not None
                    else "builtin_default"
                )
            )
            knowledge_delta_alert_payload["evidence_runtime_history_path"] = str(
                resolved_evidence_runtime_history_path
            )
            knowledge_delta_alert_payload["evidence_runtime_history_path_source"] = (
                evidence_runtime_history_path_source
            )
            knowledge_delta_alert_payload["evidence_runtime_history_window"] = int(
                resolved_evidence_runtime_history_window
            )
            knowledge_delta_alert_payload["evidence_runtime_history_window_source"] = (
                evidence_runtime_history_window_source
            )
        except Exception as exc:
            knowledge_delta_alert_payload = {
                "status": "error",
                "error": str(exc),
                "output_path": str(knowledge_brief_delta_alert_report_path),
                "error_count": 1,
                "alert_created": False,
                "domains": _normalize_improvement_knowledge_domains(resolved_knowledge_delta_domains),
                "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
                "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
                "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
                "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
            }
            stage_errors.append({"stage": "knowledge_brief_delta_alert", "error": str(exc)})
    else:
        knowledge_delta_alert_payload = {
            "status": "skipped_not_requested",
            "output_path": str(knowledge_brief_delta_alert_report_path),
            "error_count": 0,
            "alert_created": False,
            "domains": _normalize_improvement_knowledge_domains(resolved_knowledge_delta_domains),
            "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
            "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
            "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
            "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
        }

    knowledge_alert_actions = [
        str(item).strip()
        for item in list(knowledge_delta_alert_payload.get("mitigation_actions") or [])
        if str(item).strip()
    ]
    for action in knowledge_alert_actions[:2]:
        if action not in suggested_actions:
            suggested_actions.append(action)

    stage_statuses["knowledge_brief_delta_alert"] = str(knowledge_delta_alert_payload.get("status") or "")
    knowledge_snapshot_dir_for_bootstrap = (
        resolved_knowledge_snapshot_dir
        if resolved_knowledge_snapshot_dir is not None
        else _resolve_knowledge_snapshot_dir(
            repo_path=args.repo_path.resolve(),
            snapshot_dir_value=None,
        )
    )
    knowledge_snapshot_inventory = _collect_knowledge_snapshot_inventory(knowledge_snapshot_dir_for_bootstrap)
    knowledge_delta_stage_status = str(stage_statuses.get("knowledge_brief_delta_alert") or "").strip().lower()
    knowledge_bootstrap_required = bool(knowledge_delta_alert_payload.get("bootstrap_required")) or (
        knowledge_delta_alert_requested and knowledge_delta_stage_status == "skipped_bootstrap"
    )
    knowledge_bootstrap_next_action_command = (
        _build_operator_cycle_knowledge_bootstrap_command(
            config_path=config_path,
            output_dir=output_dir,
            knowledge_domains=resolved_knowledge_delta_domains,
            knowledge_snapshot_dir=knowledge_snapshot_dir_for_bootstrap,
            knowledge_query=resolved_knowledge_brief_query,
            knowledge_snapshot_label=resolved_knowledge_brief_snapshot_label,
        )
        if knowledge_bootstrap_required
        else None
    )
    knowledge_bootstrap_active = bool(knowledge_brief_requested or knowledge_delta_alert_requested)
    knowledge_bootstrap_phase = (
        "not_requested"
        if not knowledge_bootstrap_active
        else ("bootstrap_pending" if knowledge_bootstrap_required else "ready")
    )
    knowledge_bootstrap_state = {
        "active": knowledge_bootstrap_active,
        "phase": knowledge_bootstrap_phase,
        "bootstrap_required": knowledge_bootstrap_required,
        "stage_status": str(stage_statuses.get("knowledge_brief_delta_alert") or ""),
        "snapshot_dir": str(knowledge_snapshot_dir_for_bootstrap),
        "versioned_snapshot_count": int(knowledge_snapshot_inventory.get("versioned_snapshot_count") or 0),
        "indexed_snapshot_count": int(knowledge_snapshot_inventory.get("indexed_existing_snapshot_count") or 0),
        "latest_snapshot_available": bool(knowledge_snapshot_inventory.get("latest_exists")),
        "minimum_required_snapshot_count": int(knowledge_snapshot_inventory.get("minimum_required_snapshot_count") or 2),
        "bootstrap_ready": bool(knowledge_snapshot_inventory.get("bootstrap_ready")),
        "next_action_command": knowledge_bootstrap_next_action_command,
        "next_action": (
            "Bootstrap in progress: capture one more knowledge snapshot, then rerun operator-cycle."
            if knowledge_bootstrap_required
            else (
                "Knowledge bootstrap ready for delta comparisons."
                if knowledge_delta_alert_requested
                else "Knowledge delta alert not requested."
            )
        ),
        "snapshot_inventory": knowledge_snapshot_inventory,
    }
    promotion_lock_interrupt_ids: list[str] = []
    if bool(verify_alert_payload.get("alert_created")):
        alert_block = dict(verify_alert_payload.get("alert") or {})
        alert_interrupt_id = str(alert_block.get("interrupt_id") or "").strip()
        if alert_interrupt_id:
            promotion_lock_interrupt_ids.append(alert_interrupt_id)
    promotion_lock_acknowledge_commands = [
        str(item).strip()
        for item in list(verify_alert_payload.get("acknowledge_commands") or [])
        if str(item).strip()
    ]
    if not promotion_lock_acknowledge_commands:
        promotion_lock_acknowledge_commands = [
            f"python3 -m jarvis.cli interrupts acknowledge {interrupt_id} --actor operator"
            for interrupt_id in promotion_lock_interrupt_ids
            if interrupt_id
        ]
    promotion_lock_active = bool(promotion_lock_interrupt_ids) or bool(verify_alert_payload.get("alert_created"))
    promotion_lock_interrupt_statuses: dict[str, str] = {}
    promotion_lock_unlock_resolution_error: str | None = None
    if promotion_lock_interrupt_ids:
        try:
            status_runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
            try:
                for interrupt_id in promotion_lock_interrupt_ids:
                    interrupt_row = status_runtime.interrupt_store.get(interrupt_id) or {}
                    interrupt_status = str(interrupt_row.get("status") or "").strip().lower() or "missing"
                    promotion_lock_interrupt_statuses[interrupt_id] = interrupt_status
            finally:
                status_runtime.close()
        except Exception as exc:
            promotion_lock_unlock_resolution_error = str(exc)
            for interrupt_id in promotion_lock_interrupt_ids:
                promotion_lock_interrupt_statuses.setdefault(interrupt_id, "unknown")
    promotion_lock_unlock_ready = bool(promotion_lock_interrupt_ids) and all(
        str(promotion_lock_interrupt_statuses.get(interrupt_id) or "").strip().lower() == "acknowledged"
        for interrupt_id in promotion_lock_interrupt_ids
    )
    promotion_lock_unlock_ready_commands = [
        str(promotion_lock_recheck_command).strip()
    ] if str(promotion_lock_recheck_command).strip() and str(promotion_lock_recheck_command).strip() != "none" else []
    promotion_lock_first_unlock_ready_command = (
        promotion_lock_unlock_ready_commands[0] if promotion_lock_unlock_ready_commands else "none"
    )
    promotion_lock = {
        "active": promotion_lock_active,
        "source": "verify_matrix_alert",
        "requires_acknowledgement": promotion_lock_active,
        "blocking_interrupt_ids": promotion_lock_interrupt_ids,
        "blocking_interrupt_statuses": promotion_lock_interrupt_statuses,
        "acknowledge_commands": promotion_lock_acknowledge_commands,
        "unlock_ready": promotion_lock_unlock_ready,
        "unlock_ready_commands": list(promotion_lock_unlock_ready_commands),
        "first_unlock_ready_command": promotion_lock_first_unlock_ready_command,
        "recheck_command": promotion_lock_recheck_command,
        "next_action": (
            "Run operator-cycle recheck to release frozen promotions."
            if promotion_lock_active and promotion_lock_unlock_ready
            else (
                "Acknowledge each blocking interrupt before advancing promotions."
                if promotion_lock_active
                else "No promotion lock active."
            )
        ),
    }
    if promotion_lock_unlock_resolution_error:
        promotion_lock["unlock_readiness_error"] = promotion_lock_unlock_resolution_error
    if promotion_lock_active:
        suggested_actions.append("Promotion lock active until verify-matrix alert interrupts are acknowledged.")
    if promotion_lock_unlock_ready and promotion_lock_active:
        suggested_actions.append("Blocking interrupts acknowledged; run operator-cycle recheck to release frozen promotions.")
    promotions = list(promotion_candidates)
    blocked_promotions: list[dict[str, Any]] = []
    if promotion_lock_active and promotion_candidates:
        blocked_promotions = [
            {
                **dict(row),
                "blocked_by": "verify_matrix_alert",
                "blocking_interrupt_ids": list(promotion_lock_interrupt_ids),
                "unlock_readiness": {
                    "status": (
                        "ready_to_recheck"
                        if promotion_lock_unlock_ready
                        else "blocked_pending_acknowledgement"
                    ),
                    "unlock_ready": promotion_lock_unlock_ready,
                    "requires_acknowledgement": not promotion_lock_unlock_ready,
                    "blocking_interrupt_ids": list(promotion_lock_interrupt_ids),
                    "blocking_interrupt_statuses": dict(promotion_lock_interrupt_statuses),
                    "acknowledge_commands": list(promotion_lock_acknowledge_commands),
                    "unlock_ready_commands": list(promotion_lock_unlock_ready_commands),
                    "first_unlock_ready_command": promotion_lock_first_unlock_ready_command,
                    "recheck_command": promotion_lock_recheck_command,
                    "next_action": (
                        "Rerun operator-cycle verification to release this frozen promotion."
                        if promotion_lock_unlock_ready
                        else "Acknowledge each blocking interrupt, then rerun operator-cycle verification."
                    ),
                },
            }
            for row in promotion_candidates
            if isinstance(row, dict)
        ]
        promotions = []
        suggested_actions.append(
            f"{len(blocked_promotions)} promotion candidate(s) frozen by verify-matrix alert lock."
        )
    metrics["promotion_count"] = len(promotions)
    metrics["blocked_promotion_count"] = len(blocked_promotions)
    promotion_lock["blocked_promotion_count"] = len(blocked_promotions)
    promotion_lock["promotion_candidates_count"] = len(promotion_candidates)
    promotion_lock["unlock_ready"] = promotion_lock_unlock_ready

    stage_error_count = (
        int(pull_payload.get("error_count") or 0)
        + int(leaderboard_payload.get("error_count") or 0)
        + int(seed_payload.get("error_count") or 0)
        + int(draft_payload.get("error_count") or 0)
        + int(daily_payload.get("error_count") or 0)
        + int(retest_payload.get("error_count") or 0)
        + int(benchmark_payload.get("error_count") or 0)
        + int(verify_payload.get("error_count") or 0)
        + int(verify_alert_payload.get("error_count") or 0)
        + int(knowledge_brief_payload.get("error_count") or 0)
        + int(knowledge_delta_alert_payload.get("error_count") or 0)
        + len(stage_errors)
    )
    overall_status = "warning" if (
        stage_errors
        or stage_error_count > 0
        or any(status == "error" for status in stage_statuses.values())
        or str(stage_statuses.get("verify_matrix") or "") == "warning"
        or str(stage_statuses.get("verify_matrix_alert") or "") == "warning"
        or str(stage_statuses.get("knowledge_brief_delta_alert") or "") == "warning"
    ) else "ok"

    inbox_summary = {
        "generated_at": utc_now_iso(),
        "config_path": str(config_path),
        "daily_config_path": str(daily_config_path),
        "output_dir": str(output_dir),
        "stage_statuses": stage_statuses,
        "metrics": metrics,
        "promotions": promotions,
        "blocked_promotions": blocked_promotions,
        "blockers": blockers,
        "retest_deltas": retest_deltas,
        "retest_transition_counts": retest_transition_counts,
        "evidence_lookup_batch": evidence_lookup_batch,
        "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
        "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
        "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
        "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
        "benchmark": benchmark_payload,
        "verify_matrix": verify_payload,
        "verify_matrix_alert": verify_alert_payload,
        "knowledge_brief": knowledge_brief_payload,
        "knowledge_brief_delta_alert": knowledge_delta_alert_payload,
        "knowledge_bootstrap_state": knowledge_bootstrap_state,
        "promotion_lock": promotion_lock,
        "suggested_actions": suggested_actions,
    }
    inbox_summary_path.parent.mkdir(parents=True, exist_ok=True)
    inbox_summary_path.write_text(json.dumps(inbox_summary, indent=2), encoding="utf-8")

    payload = {
        "generated_at": utc_now_iso(),
        "status": overall_status,
        "config_path": str(config_path),
        "daily_config_path": str(daily_config_path),
        "output_dir": str(output_dir),
        "operator_report_path": str(operator_report_path),
        "pull_report_path": str(pull_report_path),
        "seed_leaderboard_report_path": str(seed_leaderboard_report_path),
        "seed_report_path": str(seed_report_output_path),
        "draft_report_path": str(draft_report_path),
        "draft_output_config_path": str(draft_output_config_path),
        "draft_artifacts_dir": str(draft_artifacts_dir),
        "evidence_lookup_report_path": str(evidence_lookup_report_path),
        "evidence_runtime_history_path": str(resolved_evidence_runtime_history_path),
        "evidence_runtime_history_path_source": evidence_runtime_history_path_source,
        "evidence_runtime_history_window": int(resolved_evidence_runtime_history_window),
        "evidence_runtime_history_window_source": evidence_runtime_history_window_source,
        "benchmark_stale_runtime_history_window": int(resolved_benchmark_stale_runtime_history_window),
        "benchmark_stale_runtime_history_window_source": benchmark_stale_runtime_history_window_source,
        "benchmark_stale_runtime_repeat_threshold": int(resolved_benchmark_stale_runtime_repeat_threshold),
        "benchmark_stale_runtime_repeat_threshold_source": benchmark_stale_runtime_repeat_threshold_source,
        "benchmark_stale_runtime_rate_ceiling": round(float(resolved_benchmark_stale_runtime_rate_ceiling), 4),
        "benchmark_stale_runtime_rate_ceiling_source": benchmark_stale_runtime_rate_ceiling_source,
        "benchmark_stale_runtime_consecutive_runs": int(resolved_benchmark_stale_runtime_consecutive_runs),
        "benchmark_stale_runtime_consecutive_runs_source": benchmark_stale_runtime_consecutive_runs_source,
        "benchmark_report_path": str(benchmark_report_path),
        "verify_matrix_report_path": str(verify_matrix_report_path),
        "verify_matrix_alert_report_path": str(verify_matrix_alert_report_path),
        "knowledge_brief_report_path": str(knowledge_brief_report_path),
        "knowledge_brief_delta_alert_report_path": str(knowledge_brief_delta_alert_report_path),
        "daily_report_path": str(daily_report_path),
        "retest_report_path": str(retest_report_path),
        "inbox_summary_path": str(inbox_summary_path),
        "stage_statuses": stage_statuses,
        "stage_error_count": stage_error_count,
        "stage_errors": stage_errors,
        "seed_domains": list(seed_domains),
        "seed_domain_runs": seed_domain_runs,
        "fitness_leaderboard": leaderboard_payload,
        "seed_from_leaderboard": seed_payload,
        "draft": draft_payload,
        "benchmark": benchmark_payload,
        "metrics": metrics,
        "promotions": promotions,
        "blocked_promotions": blocked_promotions,
        "blockers": blockers,
        "retest_deltas": retest_deltas,
        "retest_transition_counts": retest_transition_counts,
        "evidence_lookup_batch": evidence_lookup_batch,
        "verify_matrix": verify_payload,
        "verify_matrix_alert": verify_alert_payload,
        "knowledge_brief": knowledge_brief_payload,
        "knowledge_brief_delta_alert": knowledge_delta_alert_payload,
        "knowledge_bootstrap_state": knowledge_bootstrap_state,
        "promotion_lock": promotion_lock,
        "summary": inbox_summary,
    }
    operator_report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if overall_status != "ok" and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def _build_matrix_verification_payload(*, matrix_path: Path, report_path: Path) -> dict[str, Any]:
    matrix_loaded = json.loads(matrix_path.read_text(encoding="utf-8"))
    if not isinstance(matrix_loaded, dict):
        raise ValueError("invalid_matrix_file:expected_json_object")
    scenarios = [row for row in list(matrix_loaded.get("scenarios") or []) if isinstance(row, dict)]

    report_loaded = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report_loaded, dict):
        raise ValueError("invalid_report_file:expected_json_object")

    resolved_daily_report_path = report_path
    if isinstance(report_loaded.get("experiment_runs"), list):
        daily_report = report_loaded
    else:
        daily_report_path_raw = report_loaded.get("daily_report_path")
        if daily_report_path_raw is None:
            raise ValueError("invalid_report_file:missing_experiment_runs_or_daily_report_path")
        daily_report_path = Path(str(daily_report_path_raw)).expanduser()
        if not daily_report_path.is_absolute():
            daily_report_path = (report_path.parent / daily_report_path).resolve()
        else:
            daily_report_path = daily_report_path.resolve()
        if not daily_report_path.exists():
            raise FileNotFoundError(str(daily_report_path))
        resolved_daily_report_path = daily_report_path
        daily_report = json.loads(daily_report_path.read_text(encoding="utf-8"))
        if not isinstance(daily_report, dict):
            raise ValueError("invalid_daily_report:expected_json_object")

    experiment_runs = [
        row
        for row in list(daily_report.get("experiment_runs") or [])
        if isinstance(row, dict)
    ]

    run_rows: list[dict[str, Any]] = []
    for index, run in enumerate(experiment_runs):
        resolution = dict(run.get("resolution") or {}) if isinstance(run.get("resolution"), dict) else {}
        selected = dict(run.get("selected_hypothesis") or {}) if isinstance(run.get("selected_hypothesis"), dict) else {}
        if not selected and isinstance(resolution.get("selected"), dict):
            selected = dict(resolution.get("selected") or {})
        resolved_domain = str(run.get("domain") or resolution.get("domain") or selected.get("domain") or "").strip().lower() or None
        resolved_friction_key = _normalize_friction_key(
            run.get("friction_key")
            if run.get("friction_key") is not None
            else (resolution.get("friction_key") if resolution.get("friction_key") is not None else selected.get("friction_key"))
        )
        artifact_raw = run.get("artifact_path")
        resolved_artifact_path: str | None = None
        if artifact_raw is not None and str(artifact_raw).strip():
            artifact_path = Path(str(artifact_raw)).expanduser()
            if not artifact_path.is_absolute():
                artifact_path = (resolved_daily_report_path.parent / artifact_path).resolve()
            else:
                artifact_path = artifact_path.resolve()
            resolved_artifact_path = str(artifact_path)

        run_rows.append(
            {
                "index": index,
                "run_id": run.get("run_id"),
                "hypothesis_id": run.get("hypothesis_id"),
                "verdict": str(run.get("verdict") or "").strip().lower() or None,
                "domain": resolved_domain,
                "friction_key": resolved_friction_key or None,
                "artifact_path": resolved_artifact_path,
            }
        )

    comparisons: list[dict[str, Any]] = []
    matched_count = 0
    mismatch_count = 0
    missing_count = 0
    invalid_count = 0
    mapped_run_indexes: set[int] = set()

    for index, scenario in enumerate(scenarios):
        scenario_domain = str(scenario.get("domain") or "").strip().lower()
        scenario_friction_key = _normalize_friction_key(scenario.get("friction_key"))
        expected_verdict = str(scenario.get("expected_verdict") or "").strip().lower()
        artifact_raw = scenario.get("artifact_path")
        scenario_artifact_path: str | None = None
        if artifact_raw is not None and str(artifact_raw).strip():
            path = Path(str(artifact_raw)).expanduser()
            if not path.is_absolute():
                path = (matrix_path.parent / path).resolve()
            else:
                path = path.resolve()
            scenario_artifact_path = str(path)

        scenario_id = (
            str(scenario.get("scenario_id") or "").strip()
            or str(scenario.get("id") or "").strip()
            or f"scenario_{index}"
        )

        if not expected_verdict:
            comparisons.append(
                {
                    "index": index,
                    "scenario_id": scenario_id,
                    "status": "invalid_scenario",
                    "error": "missing_expected_verdict",
                    "domain": scenario_domain or None,
                    "friction_key": scenario_friction_key or None,
                    "artifact_path": scenario_artifact_path,
                }
            )
            invalid_count += 1
            continue

        if not scenario_domain and not scenario_friction_key and not scenario_artifact_path:
            comparisons.append(
                {
                    "index": index,
                    "scenario_id": scenario_id,
                    "status": "invalid_scenario",
                    "error": "missing_match_fields",
                    "expected_verdict": expected_verdict,
                    "domain": None,
                    "friction_key": None,
                    "artifact_path": None,
                }
            )
            invalid_count += 1
            continue

        matches = list(run_rows)
        if scenario_artifact_path is not None:
            matches = [item for item in matches if str(item.get("artifact_path") or "") == scenario_artifact_path]
        if scenario_domain:
            matches = [item for item in matches if str(item.get("domain") or "") == scenario_domain]
        if scenario_friction_key:
            matches = [item for item in matches if _normalize_friction_key(item.get("friction_key")) == scenario_friction_key]

        selected_run = matches[-1] if matches else None
        if not isinstance(selected_run, dict):
            comparisons.append(
                {
                    "index": index,
                    "scenario_id": scenario_id,
                    "status": "missing_run",
                    "expected_verdict": expected_verdict,
                    "domain": scenario_domain or None,
                    "friction_key": scenario_friction_key or None,
                    "artifact_path": scenario_artifact_path,
                    "match_candidate_count": 0,
                }
            )
            missing_count += 1
            continue

        actual_verdict = str(selected_run.get("verdict") or "").strip().lower()
        comparison_status = "matched" if actual_verdict == expected_verdict else "mismatch"
        if comparison_status == "matched":
            matched_count += 1
        else:
            mismatch_count += 1
        selected_index_raw = selected_run.get("index")
        run_index = int(selected_index_raw) if selected_index_raw is not None else -1
        if run_index >= 0:
            mapped_run_indexes.add(run_index)

        comparisons.append(
            {
                "index": index,
                "scenario_id": scenario_id,
                "status": comparison_status,
                "expected_verdict": expected_verdict,
                "actual_verdict": actual_verdict or None,
                "run_id": selected_run.get("run_id"),
                "hypothesis_id": selected_run.get("hypothesis_id"),
                "domain": selected_run.get("domain") or scenario_domain or None,
                "friction_key": selected_run.get("friction_key") or scenario_friction_key or None,
                "artifact_path": selected_run.get("artifact_path") or scenario_artifact_path,
                "match_candidate_count": len(matches),
            }
        )

    unmapped_runs = [
        dict(row)
        for row in run_rows
        if (
            int(row.get("index")) if row.get("index") is not None else -1
        ) not in mapped_run_indexes
    ]
    unmapped_by_domain_counter: Counter[str] = Counter(
        str(row.get("domain") or "unknown").strip().lower() or "unknown"
        for row in unmapped_runs
    )
    unmapped_run_count_by_domain = [
        {"domain": domain, "count": int(count)}
        for domain, count in sorted(unmapped_by_domain_counter.items(), key=lambda item: (-int(item[1]), item[0]))
    ]

    total_scenarios = len(scenarios)
    verification_status = "ok"
    if mismatch_count > 0 or missing_count > 0 or invalid_count > 0 or bool(unmapped_runs):
        verification_status = "warning"

    summary = {
        "total_scenarios": total_scenarios,
        "matched_count": matched_count,
        "mismatch_count": mismatch_count,
        "missing_count": missing_count,
        "invalid_count": invalid_count,
        "match_rate": round((matched_count / total_scenarios), 4) if total_scenarios > 0 else 0.0,
        "mapped_run_count": len(mapped_run_indexes),
        "unmapped_run_count": len(unmapped_runs),
        "mapped_run_coverage_rate": round((len(mapped_run_indexes) / len(run_rows)), 4) if run_rows else 0.0,
    }

    return {
        "generated_at": utc_now_iso(),
        "status": verification_status,
        "matrix_path": str(matrix_path),
        "report_path": str(report_path),
        "daily_report_path": str(resolved_daily_report_path),
        "run_count": len(run_rows),
        "summary": summary,
        "comparisons": comparisons,
        "unmapped_runs": unmapped_runs,
        "unmapped_run_count_by_domain": unmapped_run_count_by_domain,
    }


def _classify_matrix_drift_severity(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    rows = [row for row in list(payload.get("comparisons") or []) if isinstance(row, dict)]
    unmapped_runs = [row for row in list(payload.get("unmapped_runs") or []) if isinstance(row, dict)]
    mismatches = [row for row in rows if str(row.get("status") or "") == "mismatch"]
    missing = [row for row in rows if str(row.get("status") or "") == "missing_run"]
    invalid = [row for row in rows if str(row.get("status") or "") == "invalid_scenario"]
    guardrail_mismatches = [
        row
        for row in mismatches
        if str(row.get("actual_verdict") or "").strip().lower() == "blocked_guardrail"
    ]

    mismatch_count = int(summary.get("mismatch_count") or len(mismatches))
    missing_count = int(summary.get("missing_count") or len(missing))
    invalid_count = int(summary.get("invalid_count") or len(invalid))
    unmapped_run_count = int(summary.get("unmapped_run_count") or len(unmapped_runs))
    guardrail_mismatch_count = len(guardrail_mismatches)
    total_issues = mismatch_count + missing_count + invalid_count + unmapped_run_count

    if total_issues <= 0:
        return {
            "severity": "none",
            "score": 0,
            "total_issues": 0,
            "mismatch_count": mismatch_count,
            "missing_count": missing_count,
            "invalid_count": invalid_count,
            "unmapped_run_count": unmapped_run_count,
            "guardrail_mismatch_count": guardrail_mismatch_count,
            "recommended_urgency": None,
            "recommended_confidence": None,
            "reasons": ["no_drift_detected"],
            "guardrail_scenarios": [],
        }

    score = 0
    reasons: list[str] = []
    if mismatch_count >= 1:
        score += 2
        reasons.append("has_mismatch")
    if mismatch_count >= 2:
        score += 1
        reasons.append("multiple_mismatches")
    if missing_count >= 1:
        score += 2
        reasons.append("has_missing_run")
    if missing_count >= 2:
        score += 1
        reasons.append("multiple_missing_runs")
    if invalid_count >= 1:
        score += 1
        reasons.append("has_invalid_scenarios")
    if unmapped_run_count >= 1:
        score += 1
        reasons.append("has_unmapped_runs")
    if unmapped_run_count >= 3:
        score += 1
        reasons.append("multiple_unmapped_runs")
    if guardrail_mismatch_count >= 1:
        score += 3
        reasons.append("guardrail_regression_detected")
    if guardrail_mismatch_count >= 2:
        score += 1
        reasons.append("multiple_guardrail_regressions")
    if total_issues >= 4:
        score += 1
        reasons.append("high_total_drift_issue_volume")

    severity = "critical" if score >= 5 else "warn"
    recommended_urgency = 0.98 if severity == "critical" else 0.9
    recommended_confidence = 0.95 if severity == "critical" else 0.86
    guardrail_scenarios = [
        str(row.get("scenario_id") or f"scenario_{index}")
        for index, row in enumerate(guardrail_mismatches)
    ]
    return {
        "severity": severity,
        "score": int(score),
        "total_issues": int(total_issues),
        "mismatch_count": mismatch_count,
        "missing_count": missing_count,
        "invalid_count": invalid_count,
        "unmapped_run_count": unmapped_run_count,
        "guardrail_mismatch_count": guardrail_mismatch_count,
        "recommended_urgency": float(recommended_urgency),
        "recommended_confidence": float(recommended_confidence),
        "reasons": reasons,
        "guardrail_scenarios": guardrail_scenarios,
    }


def _build_matrix_drift_mitigations(
    payload: dict[str, Any],
    *,
    max_items: int = 3,
) -> list[str]:
    rows = [row for row in list(payload.get("comparisons") or []) if isinstance(row, dict)]
    unmapped_runs = [row for row in list(payload.get("unmapped_runs") or []) if isinstance(row, dict)]
    mismatches = [row for row in rows if str(row.get("status") or "") == "mismatch"]
    missing = [row for row in rows if str(row.get("status") or "") == "missing_run"]
    invalid = [row for row in rows if str(row.get("status") or "") == "invalid_scenario"]
    severity_profile = _classify_matrix_drift_severity(payload)
    severity = str(severity_profile.get("severity") or "")

    actions: list[str] = []
    if severity == "critical":
        actions.append("Escalate immediately: freeze affected promotions until matrix drift is resolved.")
    if mismatches:
        domains = sorted(
            {
                str(row.get("domain") or "").strip().lower()
                for row in mismatches
                if str(row.get("domain") or "").strip()
            }
        )
        if domains:
            actions.append(
                f"Review hypothesis criteria and guardrails for mismatch domains: {', '.join(domains[:max(1, int(max_items))])}."
            )
        else:
            actions.append("Review mismatched scenarios and tighten experiment guardrails before promotion.")
    if missing:
        actions.append("Run missing controlled experiments before advancing operator-cycle promotions.")
    if invalid:
        actions.append("Fix invalid matrix scenarios (missing match fields or expected verdicts) to restore drift coverage.")
    if unmapped_runs:
        coverage_domains = sorted(
            {
                str(row.get("domain") or "").strip().lower()
                for row in unmapped_runs
                if str(row.get("domain") or "").strip()
            }
        )
        if coverage_domains:
            actions.append(
                "Add matrix scenarios for unmapped experiment runs in domains: "
                + ", ".join(coverage_domains[: max(1, int(max_items))])
                + "."
            )
        else:
            actions.append("Add matrix scenarios for unmapped experiment runs to restore controlled-test coverage.")
    if not actions:
        actions.append("No matrix drift detected; continue controlled validation cadence.")
    return actions


def cmd_improvement_verify_matrix(args: argparse.Namespace) -> None:
    matrix_path = args.matrix_path.resolve()
    report_path = args.report_path.resolve()
    payload = _build_matrix_verification_payload(matrix_path=matrix_path, report_path=report_path)
    severity_profile = _classify_matrix_drift_severity(payload)
    payload["drift_severity"] = str(severity_profile.get("severity") or "none")
    payload["severity_profile"] = severity_profile

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if str(payload.get("status") or "") != "ok" and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def cmd_improvement_verify_matrix_alert(args: argparse.Namespace) -> None:
    matrix_path = args.matrix_path.resolve()
    report_path = args.report_path.resolve()
    verification_payload = _build_matrix_verification_payload(matrix_path=matrix_path, report_path=report_path)
    verification_status = str(verification_payload.get("status") or "")
    severity_profile = _classify_matrix_drift_severity(verification_payload)
    drift_severity = str(severity_profile.get("severity") or "none")
    verification_payload["drift_severity"] = drift_severity
    verification_payload["severity_profile"] = severity_profile
    top_items = max(1, int(getattr(args, "alert_max_items", 3) or 3))
    mitigation_actions = _build_matrix_drift_mitigations(verification_payload, max_items=top_items)

    alert_payload: dict[str, Any] | None = None
    alert_created = False

    if verification_status != "ok":
        summary = dict(verification_payload.get("summary") or {})
        comparisons = [row for row in list(verification_payload.get("comparisons") or []) if isinstance(row, dict)]
        unmapped_rows = [
            row
            for row in list(verification_payload.get("unmapped_runs") or [])
            if isinstance(row, dict)
        ][:top_items]
        mismatch_rows = [row for row in comparisons if str(row.get("status") or "") == "mismatch"][:top_items]
        missing_rows = [row for row in comparisons if str(row.get("status") or "") == "missing_run"][:top_items]
        invalid_rows = [row for row in comparisons if str(row.get("status") or "") == "invalid_scenario"][:top_items]

        scenario_refs = [
            str(row.get("scenario_id") or f"scenario_{idx}")
            for idx, row in enumerate([*mismatch_rows, *missing_rows, *invalid_rows])
        ]
        for row in unmapped_rows:
            run_ref = str(row.get("run_id") or "").strip()
            if run_ref:
                scenario_refs.append(f"run:{run_ref}")
                continue
            hypothesis_ref = str(row.get("hypothesis_id") or "").strip()
            if hypothesis_ref:
                scenario_refs.append(f"hypothesis:{hypothesis_ref}")
                continue
            scenario_refs.append(f"unmapped_index:{int(row.get('index') or 0)}")
        compact_refs = ",".join(scenario_refs[:top_items]) if scenario_refs else "none"
        reason = (
            "matrix_drift_detected"
            + f" severity={drift_severity}"
            + f" mismatches={int(summary.get('mismatch_count') or 0)}"
            + f" missing={int(summary.get('missing_count') or 0)}"
            + f" invalid={int(summary.get('invalid_count') or 0)}"
            + f" unmapped_runs={int(summary.get('unmapped_run_count') or 0)}"
            + f" guardrail_mismatches={int(severity_profile.get('guardrail_mismatch_count') or 0)}"
            + f" top={compact_refs}"
        )
        why_now = "controlled matrix verification reported drift requiring operator review."
        why_not_later = "deferring drift triage risks propagating regressions into production decisions."
        alert_domain = str(getattr(args, "alert_domain", "markets") or "markets").strip().lower() or "markets"
        recommended_urgency = _coerce_float(severity_profile.get("recommended_urgency"), default=0.9)
        recommended_confidence = _coerce_float(severity_profile.get("recommended_confidence"), default=0.86)
        raw_urgency = getattr(args, "alert_urgency", None)
        raw_confidence = getattr(args, "alert_confidence", None)
        alert_urgency = max(
            0.0,
            min(
                1.0,
                (
                    _coerce_float(raw_urgency, default=recommended_urgency)
                    if raw_urgency is not None
                    else recommended_urgency
                ),
            ),
        )
        alert_confidence = max(
            0.0,
            min(
                1.0,
                (
                    _coerce_float(raw_confidence, default=recommended_confidence)
                    if raw_confidence is not None
                    else recommended_confidence
                ),
            ),
        )

        runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
        try:
            decision = InterruptDecision(
                interrupt_id=new_id("int"),
                candidate_id=new_id("cand"),
                domain=alert_domain,
                reason=reason,
                urgency_score=alert_urgency,
                confidence=alert_confidence,
                suppression_window_hit=False,
                delivered=True,
                why_now=why_now,
                why_not_later=why_not_later,
                status="delivered",
            )
            runtime.interrupt_store.store(decision)
            interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
            runtime.memory.append_event(
                "improvement.matrix_drift_alert_created",
                {
                    "interrupt_id": interrupt.get("interrupt_id"),
                    "domain": alert_domain,
                    "matrix_path": str(matrix_path),
                    "report_path": str(report_path),
                    "summary": summary,
                    "drift_severity": drift_severity,
                    "severity_profile": severity_profile,
                    "top_scenarios": scenario_refs[:top_items],
                    "mitigation_actions": mitigation_actions,
                },
            )
            alert_payload = {
                "interrupt_id": interrupt.get("interrupt_id"),
                "domain": interrupt.get("domain"),
                "status": interrupt.get("status"),
                "drift_severity": drift_severity,
                "urgency_score": interrupt.get("urgency_score"),
                "confidence": interrupt.get("confidence"),
                "reason": interrupt.get("reason"),
                "why_now": interrupt.get("why_now"),
                "why_not_later": interrupt.get("why_not_later"),
                "top_scenarios": scenario_refs[:top_items],
                "acknowledge_command": (
                    f"python3 -m jarvis.cli interrupts acknowledge {interrupt.get('interrupt_id')} --actor operator"
                    if str(interrupt.get("interrupt_id") or "").strip()
                    else None
                ),
            }
            alert_created = True
        finally:
            runtime.close()

    payload = {
        "generated_at": utc_now_iso(),
        "status": verification_status,
        "matrix_path": str(matrix_path),
        "report_path": str(report_path),
        "drift_severity": drift_severity,
        "severity_profile": severity_profile,
        "alert_created": alert_created,
        "alert": alert_payload,
        "acknowledge_commands": (
            [str(alert_payload.get("acknowledge_command"))]
            if isinstance(alert_payload, dict) and str(alert_payload.get("acknowledge_command") or "").strip()
            else []
        ),
        "mitigation_actions": mitigation_actions,
        "verification": verification_payload,
    }

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if verification_status != "ok" and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def _append_unique_string(items: list[str], value: Any) -> None:
    candidate = str(value or "").strip()
    if candidate and candidate not in items:
        items.append(candidate)


def cmd_improvement_reconcile_codeowner_review_gate_outputs(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_reconcile_codeowner_review_gate_payload:expected_json_object")

    reviews = dict(loaded.get("required_pull_request_reviews") or {})
    collaborator_count = _coerce_int(loaded.get("collaborator_count"), default=0)
    current_require_code_owner_reviews = bool(reviews.get("current_require_code_owner_reviews"))
    desired_require_code_owner_reviews = bool(reviews.get("desired_require_code_owner_reviews"))
    current_required_approving_review_count = _coerce_int(
        reviews.get("current_required_approving_review_count"),
        default=_coerce_int(reviews.get("required_approving_review_count"), default=1),
    )
    desired_required_approving_review_count = _coerce_int(
        reviews.get("desired_required_approving_review_count"),
        default=current_required_approving_review_count,
    )
    current_require_last_push_approval = bool(
        reviews.get("current_require_last_push_approval", reviews.get("require_last_push_approval"))
    )
    desired_require_last_push_approval = bool(
        reviews.get("desired_require_last_push_approval", current_require_last_push_approval)
    )
    status_checks = dict(loaded.get("required_status_checks") or {})
    current_required_status_checks: list[str] = []
    for value in list(
        status_checks.get("current_contexts")
        or status_checks.get("contexts")
        or []
    ):
        _append_unique_string(current_required_status_checks, value)
    current_required_status_checks = sorted(current_required_status_checks)

    desired_required_status_checks: list[str] = []
    for value in list(
        status_checks.get("desired_contexts")
        or current_required_status_checks
        or []
    ):
        _append_unique_string(desired_required_status_checks, value)
    desired_required_status_checks = sorted(desired_required_status_checks)
    required_status_checks_change_needed = bool(status_checks.get("change_needed"))
    current_required_status_checks_csv = ",".join(current_required_status_checks)
    desired_required_status_checks_csv = ",".join(desired_required_status_checks)
    provenance = dict(loaded.get("reconcile_provenance") or {})
    source_workflow_run_id = str(
        provenance.get("source_workflow_run_id", loaded.get("source_workflow_run_id")) or ""
    ).strip()
    source_workflow_run_conclusion = str(
        provenance.get("source_workflow_run_conclusion", loaded.get("source_workflow_run_conclusion")) or ""
    ).strip()
    source_workflow_name = str(
        provenance.get("source_workflow_name", loaded.get("source_workflow_name")) or ""
    ).strip()
    source_workflow_event = str(
        provenance.get("source_workflow_event", loaded.get("source_workflow_event")) or ""
    ).strip()
    source_workflow_run_url = str(
        provenance.get("source_workflow_run_url", loaded.get("source_workflow_run_url")) or ""
    ).strip()

    payload: dict[str, Any] = {
        "report_path": str(report_path),
        "collaborator_count": collaborator_count,
        "current_require_code_owner_reviews": current_require_code_owner_reviews,
        "desired_require_code_owner_reviews": desired_require_code_owner_reviews,
        "current_required_approving_review_count": current_required_approving_review_count,
        "desired_required_approving_review_count": desired_required_approving_review_count,
        "current_require_last_push_approval": current_require_last_push_approval,
        "desired_require_last_push_approval": desired_require_last_push_approval,
        "current_required_status_checks": current_required_status_checks,
        "desired_required_status_checks": desired_required_status_checks,
        "current_required_status_checks_csv": current_required_status_checks_csv,
        "desired_required_status_checks_csv": desired_required_status_checks_csv,
        "required_status_checks_change_needed": required_status_checks_change_needed,
        "source_workflow_run_id": source_workflow_run_id or None,
        "source_workflow_run_conclusion": source_workflow_run_conclusion or None,
        "source_workflow_name": source_workflow_name or None,
        "source_workflow_event": source_workflow_event or None,
        "source_workflow_run_url": source_workflow_run_url or None,
    }

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"collaborator_count={collaborator_count}",
            (
                "current_require_code_owner_reviews="
                + ("true" if current_require_code_owner_reviews else "false")
            ),
            (
                "desired_require_code_owner_reviews="
                + ("true" if desired_require_code_owner_reviews else "false")
            ),
            f"current_required_approving_review_count={current_required_approving_review_count}",
            f"desired_required_approving_review_count={desired_required_approving_review_count}",
            "current_require_last_push_approval=" + ("true" if current_require_last_push_approval else "false"),
            "desired_require_last_push_approval=" + ("true" if desired_require_last_push_approval else "false"),
            f"current_required_status_checks_csv={current_required_status_checks_csv}",
            f"desired_required_status_checks_csv={desired_required_status_checks_csv}",
            (
                "required_status_checks_change_needed="
                + ("true" if required_status_checks_change_needed else "false")
            ),
            f"source_workflow_run_id={source_workflow_run_id or 'none'}",
            f"source_workflow_run_conclusion={source_workflow_run_conclusion or 'none'}",
            f"source_workflow_name={source_workflow_name or 'none'}",
            f"source_workflow_event={source_workflow_event or 'none'}",
            f"source_workflow_run_url={source_workflow_run_url or 'none'}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- collaborator_count: `{collaborator_count}`",
                    f"- current_require_code_owner_reviews: `{'true' if current_require_code_owner_reviews else 'false'}`",
                    f"- desired_require_code_owner_reviews: `{'true' if desired_require_code_owner_reviews else 'false'}`",
                    f"- current_required_approving_review_count: `{current_required_approving_review_count}`",
                    f"- desired_required_approving_review_count: `{desired_required_approving_review_count}`",
                    f"- current_require_last_push_approval: `{'true' if current_require_last_push_approval else 'false'}`",
                    f"- desired_require_last_push_approval: `{'true' if desired_require_last_push_approval else 'false'}`",
                    (
                        "- current_required_status_checks_csv: `"
                        + (current_required_status_checks_csv or "none")
                        + "`"
                    ),
                    (
                        "- desired_required_status_checks_csv: `"
                        + (desired_required_status_checks_csv or "none")
                        + "`"
                    ),
                    (
                        "- required_status_checks_change_needed: `"
                        + ("true" if required_status_checks_change_needed else "false")
                        + "`"
                    ),
                    f"- source_workflow_run_id: `{source_workflow_run_id or 'none'}`",
                    f"- source_workflow_run_conclusion: `{source_workflow_run_conclusion or 'none'}`",
                    f"- source_workflow_name: `{source_workflow_name or 'none'}`",
                    f"- source_workflow_event: `{source_workflow_event or 'none'}`",
                    f"- source_workflow_run_url: `{source_workflow_run_url or 'none'}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )


def cmd_improvement_reconcile_codeowner_review_gate_runtime_alert(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    alert_path = (
        args.output_path.resolve()
        if getattr(args, "output_path", None) is not None
        else (report_path.parent / "codeowner_review_reconcile_drift_alert.json").resolve()
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (
        args.db_path.resolve()
        if getattr(args, "db_path", None) is not None
        else (alert_path.parent / "jarvis.db").resolve()
    )

    loaded: dict[str, Any] = {}
    report_missing = not report_path.exists()
    if not report_missing:
        parsed = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("invalid_reconcile_codeowner_review_gate_payload:expected_json_object")
        loaded = dict(parsed)

    collaborator_count = _coerce_int(loaded.get("collaborator_count"), default=0)
    status_checks = dict(loaded.get("required_status_checks") or {})
    provenance = dict(loaded.get("reconcile_provenance") or {})
    source_workflow_run_id = str(
        provenance.get("source_workflow_run_id", loaded.get("source_workflow_run_id")) or ""
    ).strip()
    source_workflow_run_conclusion = str(
        provenance.get("source_workflow_run_conclusion", loaded.get("source_workflow_run_conclusion")) or ""
    ).strip()
    source_workflow_name = str(
        provenance.get("source_workflow_name", loaded.get("source_workflow_name")) or ""
    ).strip()
    source_workflow_event = str(
        provenance.get("source_workflow_event", loaded.get("source_workflow_event")) or ""
    ).strip()
    source_workflow_run_url = str(
        provenance.get("source_workflow_run_url", loaded.get("source_workflow_run_url")) or ""
    ).strip()
    expected_trigger_event = str(loaded.get("reconcile_trigger_expected_event") or "workflow_run").strip()
    if not expected_trigger_event:
        expected_trigger_event = "workflow_run"

    non_workflow_run_events: list[str] = []
    for value in list(loaded.get("reconcile_trigger_non_workflow_events") or []):
        _append_unique_string(non_workflow_run_events, value)

    non_workflow_run_ids: list[str] = []
    for value in list(loaded.get("reconcile_trigger_non_workflow_run_ids") or []):
        _append_unique_string(non_workflow_run_ids, value)

    non_workflow_runs: list[dict[str, Any]] = []
    for item in list(loaded.get("reconcile_trigger_non_workflow_runs") or []):
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or "").strip()
        run_event = str(item.get("event") or "").strip()
        run_url = str(item.get("html_url") or "").strip()
        run_status = str(item.get("status") or "").strip()
        run_conclusion = str(item.get("conclusion") or "").strip()
        normalized = {
            "run_id": run_id or None,
            "event": run_event or "unknown",
            "html_url": run_url or None,
            "status": run_status or None,
            "conclusion": run_conclusion or None,
        }
        non_workflow_runs.append(normalized)
        _append_unique_string(non_workflow_run_ids, run_id)
        _append_unique_string(non_workflow_run_events, run_event)
    non_workflow_run_events = sorted(non_workflow_run_events)
    non_workflow_run_count = _coerce_int(
        loaded.get("reconcile_trigger_non_workflow_run_count"),
        default=len(non_workflow_runs),
    )
    if non_workflow_run_count < len(non_workflow_runs):
        non_workflow_run_count = len(non_workflow_runs)
    if len(non_workflow_run_events) > 0 and non_workflow_run_count <= 0:
        non_workflow_run_count = len(non_workflow_run_events)
    if len(non_workflow_run_ids) > 0 and non_workflow_run_count <= 0:
        non_workflow_run_count = len(non_workflow_run_ids)
    non_workflow_run_events_csv = ",".join(non_workflow_run_events) or "none"
    non_workflow_run_ids_csv = ",".join(non_workflow_run_ids) or "none"
    first_non_workflow_run_id = (
        str(non_workflow_run_ids[0]).strip()
        if non_workflow_run_ids
        else (
            str((non_workflow_runs[0] or {}).get("run_id") or "").strip()
            if non_workflow_runs
            else "none"
        )
    )
    first_non_workflow_run_event = (
        str(non_workflow_run_events[0]).strip()
        if non_workflow_run_events
        else (
            str((non_workflow_runs[0] or {}).get("event") or "").strip()
            if non_workflow_runs
            else "none"
        )
    )
    reconcile_trigger_event_change_needed = _coerce_bool(
        loaded.get("reconcile_trigger_event_change_needed"),
        default=False,
    )
    if non_workflow_run_count > 0:
        reconcile_trigger_event_change_needed = True

    current_required_status_checks: list[str] = []
    for value in list(
        status_checks.get("current_contexts")
        or status_checks.get("contexts")
        or []
    ):
        _append_unique_string(current_required_status_checks, value)
    current_required_status_checks = sorted(current_required_status_checks)

    desired_required_status_checks: list[str] = []
    for value in list(
        status_checks.get("desired_contexts")
        or status_checks.get("required_contexts")
        or current_required_status_checks
        or []
    ):
        _append_unique_string(desired_required_status_checks, value)
    desired_required_status_checks = sorted(desired_required_status_checks)
    current_required_status_checks_strict = _coerce_bool(
        status_checks.get("current_strict", status_checks.get("strict")),
        default=False,
    )
    desired_required_status_checks_strict = _coerce_bool(
        status_checks.get("desired_strict", current_required_status_checks_strict),
        default=current_required_status_checks_strict,
    )

    missing_required_status_checks = sorted(
        set(desired_required_status_checks) - set(current_required_status_checks)
    )
    extra_required_status_checks = sorted(
        set(current_required_status_checks) - set(desired_required_status_checks)
    )

    required_status_checks_change_needed = bool(status_checks.get("change_needed"))
    if current_required_status_checks != desired_required_status_checks:
        required_status_checks_change_needed = True
    if current_required_status_checks_strict != desired_required_status_checks_strict:
        required_status_checks_change_needed = True
    change_needed = bool(required_status_checks_change_needed or reconcile_trigger_event_change_needed)

    current_required_status_checks_csv = ",".join(current_required_status_checks)
    desired_required_status_checks_csv = ",".join(desired_required_status_checks)
    missing_required_status_checks_csv = ",".join(missing_required_status_checks) or "none"
    extra_required_status_checks_csv = ",".join(extra_required_status_checks) or "none"

    rerun_command = str(getattr(args, "rerun_command", None) or "").strip()
    if not rerun_command:
        repo_slug = str(loaded.get("repo_slug") or "Dankerbadge/JARVIS").strip() or "Dankerbadge/JARVIS"
        branch = str(loaded.get("branch") or "main").strip() or "main"
        min_collaborators = max(1, _coerce_int(loaded.get("min_collaborators"), default=2))
        rerun_parts = [
            "bash ./scripts/reconcile_codeowner_review_gate.sh",
            f"--repo-slug {repo_slug}",
            f"--branch {branch}",
            f"--min-collaborators {min_collaborators}",
        ]
        for context in desired_required_status_checks:
            rerun_parts.append(f"--required-status-check {context}")
        rerun_parts.append(
            "--required-status-check-strict "
            + ("true" if desired_required_status_checks_strict else "false")
        )
        rerun_parts.append("--apply")
        rerun_parts.append("> output/ci/codeowner_review_reconcile.json")
        rerun_command = " ".join(rerun_parts)
    rerun_command = rerun_command or "none"

    reason = (
        "codeowner_reconcile_audit_drift"
        + f" required_status_checks_change_needed={required_status_checks_change_needed}"
        + f" missing_contexts={missing_required_status_checks_csv}"
        + f" extra_contexts={extra_required_status_checks_csv}"
        + f" current_strict={str(current_required_status_checks_strict).lower()}"
        + f" desired_strict={str(desired_required_status_checks_strict).lower()}"
        + f" reconcile_trigger_event_change_needed={reconcile_trigger_event_change_needed}"
        + f" expected_trigger_event={expected_trigger_event}"
        + f" non_workflow_run_count={non_workflow_run_count}"
        + f" non_workflow_events={non_workflow_run_events_csv}"
    )
    why_now = (
        "status-check drift or reconcile trigger-event drift means baseline branch-protection enforcement is no longer fully trustworthy."
    )
    why_not_later = (
        "deferring reconcile audit drift can hide protection regressions or unexpected reconcile execution paths."
    )

    interrupt_id = ""
    acknowledge_command = "none"
    runtime_error = "none"
    runtime = None
    try:
        if change_needed:
            runtime = JarvisRuntime(
                db_path=db_path,
                repo_path=args.repo_path.resolve(),
            )
            missing_count = len(missing_required_status_checks)
            extra_count = len(extra_required_status_checks)
            event_count = max(0, int(non_workflow_run_count))
            urgency_score = max(
                0.72,
                min(0.98, 0.78 + (0.04 * min(5, missing_count + extra_count)) + (0.03 * min(4, event_count))),
            )
            confidence = max(
                0.72,
                min(0.98, 0.84 + (0.02 * min(5, missing_count)) + (0.02 * min(4, event_count))),
            )
            decision = InterruptDecision(
                interrupt_id=new_id("int"),
                candidate_id=new_id("cand"),
                domain="operations",
                reason=reason,
                urgency_score=urgency_score,
                confidence=confidence,
                suppression_window_hit=False,
                delivered=True,
                why_now=why_now,
                why_not_later=why_not_later,
                status="delivered",
            )
            runtime.interrupt_store.store(decision)
            interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
            interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
            if interrupt_id:
                acknowledge_command = (
                    "python3 -m jarvis.cli interrupts acknowledge "
                    f"{interrupt_id} --actor operator --db-path {db_path}"
                )
            runtime.memory.append_event(
                "improvement.reconcile_codeowner_review_gate_runtime_alert_created",
                {
                    "interrupt_id": interrupt_id or None,
                    "report_path": str(report_path),
                    "required_status_checks_change_needed": bool(required_status_checks_change_needed),
                    "reconcile_trigger_event_change_needed": bool(reconcile_trigger_event_change_needed),
                    "reconcile_trigger_expected_event": expected_trigger_event,
                    "reconcile_trigger_non_workflow_run_count": int(non_workflow_run_count),
                    "reconcile_trigger_non_workflow_events": non_workflow_run_events,
                    "reconcile_trigger_non_workflow_run_ids": non_workflow_run_ids,
                    "reconcile_trigger_non_workflow_runs": non_workflow_runs,
                    "current_required_status_checks": current_required_status_checks,
                    "desired_required_status_checks": desired_required_status_checks,
                    "missing_required_status_checks": missing_required_status_checks,
                    "extra_required_status_checks": extra_required_status_checks,
                    "current_required_status_checks_strict": bool(current_required_status_checks_strict),
                    "desired_required_status_checks_strict": bool(desired_required_status_checks_strict),
                    "rerun_command": rerun_command,
                    "source_workflow_run_id": source_workflow_run_id or None,
                    "source_workflow_run_conclusion": source_workflow_run_conclusion or None,
                    "source_workflow_name": source_workflow_name or None,
                    "source_workflow_event": source_workflow_event or None,
                    "source_workflow_run_url": source_workflow_run_url or None,
                },
            )
    except Exception as exc:
        runtime_error = str(exc).strip() or "unknown_runtime_error"
    finally:
        if runtime is not None:
            runtime.close()

    status = "ok"
    if report_missing:
        status = "warning"
    elif change_needed:
        status = "warning"

    first_repair_command = acknowledge_command if acknowledge_command != "none" else rerun_command
    if not first_repair_command:
        first_repair_command = "none"

    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": status,
        "report_path": str(report_path),
        "report_missing": bool(report_missing),
        "collaborator_count": collaborator_count,
        "change_needed": bool(change_needed),
        "required_status_checks_change_needed": bool(required_status_checks_change_needed),
        "reconcile_trigger_event_change_needed": bool(reconcile_trigger_event_change_needed),
        "reconcile_trigger_expected_event": expected_trigger_event,
        "reconcile_trigger_non_workflow_run_count": int(non_workflow_run_count),
        "reconcile_trigger_non_workflow_events": non_workflow_run_events,
        "reconcile_trigger_non_workflow_events_csv": non_workflow_run_events_csv,
        "reconcile_trigger_non_workflow_run_ids": non_workflow_run_ids,
        "reconcile_trigger_non_workflow_run_ids_csv": non_workflow_run_ids_csv,
        "reconcile_trigger_first_non_workflow_run_id": first_non_workflow_run_id or "none",
        "reconcile_trigger_first_non_workflow_event": first_non_workflow_run_event or "none",
        "reconcile_trigger_non_workflow_runs": non_workflow_runs,
        "current_required_status_checks": current_required_status_checks,
        "desired_required_status_checks": desired_required_status_checks,
        "missing_required_status_checks": missing_required_status_checks,
        "extra_required_status_checks": extra_required_status_checks,
        "current_required_status_checks_strict": bool(current_required_status_checks_strict),
        "desired_required_status_checks_strict": bool(desired_required_status_checks_strict),
        "current_required_status_checks_csv": current_required_status_checks_csv,
        "desired_required_status_checks_csv": desired_required_status_checks_csv,
        "missing_required_status_checks_csv": missing_required_status_checks_csv,
        "extra_required_status_checks_csv": extra_required_status_checks_csv,
        "source_workflow_run_id": source_workflow_run_id or None,
        "source_workflow_run_conclusion": source_workflow_run_conclusion or None,
        "source_workflow_name": source_workflow_name or None,
        "source_workflow_event": source_workflow_event or None,
        "source_workflow_run_url": source_workflow_run_url or None,
        "alert_created": bool(interrupt_id),
        "interrupt_id": interrupt_id or None,
        "interrupt_db_path": str(db_path),
        "acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
        "rerun_command": None if rerun_command == "none" else rerun_command,
        "first_repair_command": None if first_repair_command == "none" else first_repair_command,
        "reason": reason,
        "why_now": why_now,
        "why_not_later": why_not_later,
        "runtime_error": None if runtime_error == "none" else runtime_error,
    }
    alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    payload["codeowner_review_drift_alert_path"] = str(alert_path)
    payload["codeowner_review_drift_interrupt_id"] = interrupt_id or "none"
    payload["codeowner_review_drift_alert_created"] = 1 if interrupt_id else 0
    payload["codeowner_review_drift_change_needed"] = 1 if change_needed else 0
    payload["codeowner_review_drift_required_status_checks_change_needed"] = (
        1 if required_status_checks_change_needed else 0
    )
    payload["codeowner_review_drift_reconcile_trigger_event_change_needed"] = (
        1 if reconcile_trigger_event_change_needed else 0
    )
    payload["codeowner_review_drift_reconcile_trigger_expected_event"] = expected_trigger_event
    payload["codeowner_review_drift_reconcile_trigger_non_workflow_run_count"] = int(non_workflow_run_count)
    payload["codeowner_review_drift_reconcile_trigger_non_workflow_events_csv"] = non_workflow_run_events_csv
    payload["codeowner_review_drift_reconcile_trigger_non_workflow_run_ids_csv"] = non_workflow_run_ids_csv
    payload["codeowner_review_drift_reconcile_trigger_first_non_workflow_run_id"] = (
        first_non_workflow_run_id or "none"
    )
    payload["codeowner_review_drift_reconcile_trigger_first_non_workflow_event"] = (
        first_non_workflow_run_event or "none"
    )
    payload["codeowner_review_drift_acknowledge_command"] = acknowledge_command
    payload["codeowner_review_drift_rerun_command"] = rerun_command
    payload["codeowner_review_drift_first_repair_command"] = first_repair_command or "none"
    payload["codeowner_review_drift_error"] = runtime_error
    payload["codeowner_review_drift_current_contexts_csv"] = current_required_status_checks_csv or "none"
    payload["codeowner_review_drift_desired_contexts_csv"] = desired_required_status_checks_csv or "none"
    payload["codeowner_review_drift_missing_contexts_csv"] = missing_required_status_checks_csv
    payload["codeowner_review_drift_extra_contexts_csv"] = extra_required_status_checks_csv
    payload["codeowner_review_drift_source_workflow_run_id"] = source_workflow_run_id or "none"
    payload["codeowner_review_drift_source_workflow_run_conclusion"] = source_workflow_run_conclusion or "none"
    payload["codeowner_review_drift_source_workflow_name"] = source_workflow_name or "none"
    payload["codeowner_review_drift_source_workflow_event"] = source_workflow_event or "none"
    payload["codeowner_review_drift_source_workflow_run_url"] = source_workflow_run_url or "none"

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"codeowner_review_drift_alert_path={payload['codeowner_review_drift_alert_path']}",
            f"codeowner_review_drift_interrupt_id={payload['codeowner_review_drift_interrupt_id']}",
            f"codeowner_review_drift_alert_created={payload['codeowner_review_drift_alert_created']}",
            f"codeowner_review_drift_change_needed={payload['codeowner_review_drift_change_needed']}",
            (
                "codeowner_review_drift_required_status_checks_change_needed="
                f"{payload['codeowner_review_drift_required_status_checks_change_needed']}"
            ),
            (
                "codeowner_review_drift_reconcile_trigger_event_change_needed="
                f"{payload['codeowner_review_drift_reconcile_trigger_event_change_needed']}"
            ),
            (
                "codeowner_review_drift_reconcile_trigger_expected_event="
                f"{payload['codeowner_review_drift_reconcile_trigger_expected_event']}"
            ),
            (
                "codeowner_review_drift_reconcile_trigger_non_workflow_run_count="
                f"{payload['codeowner_review_drift_reconcile_trigger_non_workflow_run_count']}"
            ),
            (
                "codeowner_review_drift_reconcile_trigger_non_workflow_events_csv="
                f"{payload['codeowner_review_drift_reconcile_trigger_non_workflow_events_csv']}"
            ),
            (
                "codeowner_review_drift_reconcile_trigger_non_workflow_run_ids_csv="
                f"{payload['codeowner_review_drift_reconcile_trigger_non_workflow_run_ids_csv']}"
            ),
            (
                "codeowner_review_drift_reconcile_trigger_first_non_workflow_run_id="
                f"{payload['codeowner_review_drift_reconcile_trigger_first_non_workflow_run_id']}"
            ),
            (
                "codeowner_review_drift_reconcile_trigger_first_non_workflow_event="
                f"{payload['codeowner_review_drift_reconcile_trigger_first_non_workflow_event']}"
            ),
            f"codeowner_review_drift_current_contexts_csv={payload['codeowner_review_drift_current_contexts_csv']}",
            f"codeowner_review_drift_desired_contexts_csv={payload['codeowner_review_drift_desired_contexts_csv']}",
            f"codeowner_review_drift_missing_contexts_csv={payload['codeowner_review_drift_missing_contexts_csv']}",
            f"codeowner_review_drift_extra_contexts_csv={payload['codeowner_review_drift_extra_contexts_csv']}",
            f"codeowner_review_drift_source_workflow_run_id={payload['codeowner_review_drift_source_workflow_run_id']}",
            f"codeowner_review_drift_source_workflow_run_conclusion={payload['codeowner_review_drift_source_workflow_run_conclusion']}",
            f"codeowner_review_drift_source_workflow_name={payload['codeowner_review_drift_source_workflow_name']}",
            f"codeowner_review_drift_source_workflow_event={payload['codeowner_review_drift_source_workflow_event']}",
            f"codeowner_review_drift_source_workflow_run_url={payload['codeowner_review_drift_source_workflow_run_url']}",
            f"codeowner_review_drift_acknowledge_command={payload['codeowner_review_drift_acknowledge_command']}",
            f"codeowner_review_drift_rerun_command={payload['codeowner_review_drift_rerun_command']}",
            f"codeowner_review_drift_first_repair_command={payload['codeowner_review_drift_first_repair_command']}",
            f"codeowner_review_drift_error={payload['codeowner_review_drift_error']}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- change_needed: `{payload['codeowner_review_drift_change_needed']}`",
                    (
                        "- required_status_checks_change_needed: `"
                        + str(payload["codeowner_review_drift_required_status_checks_change_needed"])
                        + "`"
                    ),
                    (
                        "- reconcile_trigger_event_change_needed: `"
                        + str(payload["codeowner_review_drift_reconcile_trigger_event_change_needed"])
                        + "`"
                    ),
                    (
                        "- reconcile_trigger_expected_event: `"
                        + str(payload["codeowner_review_drift_reconcile_trigger_expected_event"])
                        + "`"
                    ),
                    (
                        "- reconcile_trigger_non_workflow_run_count: `"
                        + str(payload["codeowner_review_drift_reconcile_trigger_non_workflow_run_count"])
                        + "`"
                    ),
                    (
                        "- reconcile_trigger_non_workflow_events_csv: `"
                        + str(payload["codeowner_review_drift_reconcile_trigger_non_workflow_events_csv"])
                        + "`"
                    ),
                    (
                        "- reconcile_trigger_non_workflow_run_ids_csv: `"
                        + str(payload["codeowner_review_drift_reconcile_trigger_non_workflow_run_ids_csv"])
                        + "`"
                    ),
                    f"- alert_created: `{payload['codeowner_review_drift_alert_created']}`",
                    f"- interrupt_id: `{payload['codeowner_review_drift_interrupt_id']}`",
                    f"- current_contexts_csv: `{payload['codeowner_review_drift_current_contexts_csv']}`",
                    f"- desired_contexts_csv: `{payload['codeowner_review_drift_desired_contexts_csv']}`",
                    f"- missing_contexts_csv: `{payload['codeowner_review_drift_missing_contexts_csv']}`",
                    f"- extra_contexts_csv: `{payload['codeowner_review_drift_extra_contexts_csv']}`",
                    f"- source_workflow_run_id: `{payload['codeowner_review_drift_source_workflow_run_id']}`",
                    f"- source_workflow_run_conclusion: `{payload['codeowner_review_drift_source_workflow_run_conclusion']}`",
                    f"- source_workflow_name: `{payload['codeowner_review_drift_source_workflow_name']}`",
                    f"- source_workflow_event: `{payload['codeowner_review_drift_source_workflow_event']}`",
                    f"- source_workflow_run_url: `{payload['codeowner_review_drift_source_workflow_run_url']}`",
                    f"- first_repair_command: `{payload['codeowner_review_drift_first_repair_command']}`",
                    f"- runtime_error: `{payload['codeowner_review_drift_error']}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)):
        if report_missing:
            raise SystemExit(2)
        if change_needed and (runtime_error != "none" or not bool(interrupt_id)):
            raise SystemExit(2)


def cmd_improvement_domain_smoke_outputs(args: argparse.Namespace) -> None:
    domain = (
        str(getattr(args, "domain", None) or os.getenv("MATRIX_DOMAIN") or "unknown")
        .strip()
        .lower()
        or "unknown"
    )
    artifact_root = (
        args.artifact_root.resolve()
        if getattr(args, "artifact_root", None) is not None
        else Path("output/ci/domain_smoke").resolve()
    )
    summary_path = (
        args.summary_path.resolve()
        if getattr(args, "summary_path", None) is not None
        else (artifact_root / domain / f"{domain}_smoke_summary.json").resolve()
    )

    payload: dict[str, Any] = {}
    if summary_path.exists():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            loaded = {}
        if isinstance(loaded, dict):
            payload = dict(loaded)
    else:
        payload = {
            "status": "warning",
            "domain": domain,
            "reason": "domain_smoke_summary_missing",
            "pull_report_path": None,
            "leaderboard_report_path": None,
            "seed_report_path": None,
        }

    status = str(payload.get("status") or "warning").strip().lower() or "warning"
    reported_domain = str(payload.get("domain") or "").strip().lower()
    pull_report_path = str(payload.get("pull_report_path") or "none").strip() or "none"
    leaderboard_report_path = str(payload.get("leaderboard_report_path") or "none").strip() or "none"
    seed_report_path = str(payload.get("seed_report_path") or "none").strip() or "none"

    reason = "none"
    smoke_blocking = 0
    if not summary_path.exists():
        status = "warning"
        reason = "domain_smoke_summary_missing"
        smoke_blocking = 1
    elif not reported_domain:
        status = "warning"
        reason = "domain_smoke_domain_missing"
        smoke_blocking = 1
    elif reported_domain != domain:
        status = "warning"
        reason = f"domain_smoke_domain_mismatch:{reported_domain}"
        smoke_blocking = 1
    elif status != "ok":
        reason = f"domain_smoke_status_not_ok:{status}"
        smoke_blocking = 1

    normalized: dict[str, Any] = {
        "domain": domain,
        "summary_path": str(summary_path),
        "status": status,
        "reported_domain": reported_domain or "none",
        "reason": reason,
        "smoke_blocking": int(smoke_blocking),
        "pull_report_path": pull_report_path,
        "leaderboard_report_path": leaderboard_report_path,
        "seed_report_path": seed_report_path,
    }

    output_path = (
        args.output_path.resolve()
        if getattr(args, "output_path", None) is not None
        else None
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        normalized["output_path"] = str(output_path)

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"domain={domain}",
            f"summary_path={summary_path}",
            f"status={status}",
            f"reported_domain={reported_domain or 'none'}",
            f"reason={reason}",
            f"smoke_blocking={smoke_blocking}",
            f"pull_report_path={pull_report_path}",
            f"leaderboard_report_path={leaderboard_report_path}",
            f"seed_report_path={seed_report_path}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- domain: `{domain}`",
                    f"- status: `{status}`",
                    f"- reason: `{reason}`",
                    f"- summary_path: `{summary_path}`",
                    f"- pull_report_path: `{pull_report_path}`",
                    f"- leaderboard_report_path: `{leaderboard_report_path}`",
                    f"- seed_report_path: `{seed_report_path}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        normalized,
        compact=bool(getattr(args, "json_compact", False)),
    )


def cmd_improvement_domain_smoke_runtime_alert(args: argparse.Namespace) -> None:
    domain = (
        str(getattr(args, "domain", None) or os.getenv("MATRIX_DOMAIN") or "unknown")
        .strip()
        .lower()
        or "unknown"
    )
    smoke_status = (
        str(getattr(args, "smoke_status", None) or os.getenv("SMOKE_STATUS") or "warning")
        .strip()
        .lower()
        or "warning"
    )
    smoke_reason = (
        str(getattr(args, "smoke_reason", None) or os.getenv("SMOKE_REASON") or "domain_smoke_failure")
        .strip()
        or "domain_smoke_failure"
    )
    summary_path = Path(
        str(getattr(args, "summary_path", None) or os.getenv("SUMMARY_PATH") or "")
    ).expanduser()
    pull_report_path = (
        str(getattr(args, "pull_report_path", None) or os.getenv("PULL_REPORT_PATH") or "none").strip() or "none"
    )
    leaderboard_report_path = (
        str(getattr(args, "leaderboard_report_path", None) or os.getenv("LEADERBOARD_REPORT_PATH") or "none")
        .strip()
        or "none"
    )
    seed_report_path = (
        str(getattr(args, "seed_report_path", None) or os.getenv("SEED_REPORT_PATH") or "none").strip() or "none"
    )

    alert_path = (
        args.output_path.resolve()
        if getattr(args, "output_path", None) is not None
        else Path(f"output/ci/domain_smoke/{domain}/{domain}_smoke_alert.json").resolve()
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (
        args.db_path.resolve()
        if getattr(args, "db_path", None) is not None
        else (alert_path.parent / "jarvis.db").resolve()
    )

    rerun_command = str(getattr(args, "rerun_command", None) or "").strip()
    if not rerun_command:
        rerun_command = (
            "./scripts/run_improvement_domain_smoke.sh "
            "./configs/improvement_operator_knowledge_stack.json "
            f"{domain} --output-dir output/ci/domain_smoke/{domain} --allow-missing"
        )
    why_now = (
        f"domain smoke validation failed for {domain} "
        f"(status={smoke_status}, reason={smoke_reason}) and requires immediate operator triage."
    )
    why_not_later = (
        "untriaged smoke failures can hide degraded signal ingestion and seed quality "
        "before controlled experiments start."
    )

    interrupt_id = ""
    acknowledge_command = "none"
    runtime_error = "none"
    runtime = None
    try:
        runtime = JarvisRuntime(
            db_path=db_path,
            repo_path=args.repo_path.resolve(),
        )
        decision = InterruptDecision(
            interrupt_id=new_id("int"),
            candidate_id=new_id("cand"),
            domain=domain,
            reason=(
                "domain_smoke_failure"
                f" status={smoke_status}"
                f" reason={smoke_reason}"
            ),
            urgency_score=0.93,
            confidence=0.9,
            suppression_window_hit=False,
            delivered=True,
            why_now=why_now,
            why_not_later=why_not_later,
            status="delivered",
        )
        runtime.interrupt_store.store(decision)
        interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
        interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
        if interrupt_id:
            acknowledge_command = (
                "python3 -m jarvis.cli interrupts acknowledge "
                f"{interrupt_id} --actor operator --db-path {db_path}"
            )
        runtime.memory.append_event(
            "improvement.domain_smoke_alert_created",
            {
                "interrupt_id": interrupt_id or None,
                "domain": domain,
                "status": smoke_status,
                "reason": smoke_reason,
                "summary_path": str(summary_path.resolve()) if summary_path.exists() else str(summary_path),
                "pull_report_path": pull_report_path,
                "leaderboard_report_path": leaderboard_report_path,
                "seed_report_path": seed_report_path,
                "rerun_command": rerun_command,
            },
        )
    except Exception as exc:
        runtime_error = str(exc).strip() or "unknown_runtime_error"
    finally:
        if runtime is not None:
            runtime.close()

    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": "warning",
        "domain": domain,
        "smoke_status": smoke_status,
        "smoke_reason": smoke_reason,
        "summary_path": str(summary_path.resolve()) if summary_path.exists() else str(summary_path),
        "pull_report_path": pull_report_path,
        "leaderboard_report_path": leaderboard_report_path,
        "seed_report_path": seed_report_path,
        "alert_created": bool(interrupt_id),
        "interrupt_id": interrupt_id or None,
        "interrupt_db_path": str(db_path),
        "acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
        "rerun_command": rerun_command,
        "why_now": why_now,
        "why_not_later": why_not_later,
        "runtime_error": None if runtime_error == "none" else runtime_error,
    }
    alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    payload["alert_path"] = str(alert_path)
    payload["interrupt_id_output"] = interrupt_id or "none"
    payload["alert_created_output"] = 1 if interrupt_id else 0
    payload["acknowledge_command_output"] = acknowledge_command
    payload["runtime_error_output"] = runtime_error

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"alert_path={alert_path}",
            f"interrupt_id={interrupt_id or 'none'}",
            f"alert_created={1 if interrupt_id else 0}",
            f"acknowledge_command={acknowledge_command}",
            f"rerun_command={rerun_command}",
            f"runtime_error={runtime_error}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- domain: `{domain}`",
                    f"- interrupt_id: `{interrupt_id or 'none'}`",
                    f"- alert_created: `{1 if interrupt_id else 0}`",
                    f"- smoke_status: `{smoke_status}`",
                    f"- smoke_reason: `{smoke_reason}`",
                    f"- rerun_command: `{rerun_command}`",
                    f"- acknowledge_command: `{acknowledge_command}`",
                    f"- runtime_error: `{runtime_error}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)):
        if runtime_error != "none" or not bool(interrupt_id):
            raise SystemExit(2)


def cmd_improvement_domain_smoke_cross_domain_compact(args: argparse.Namespace) -> None:
    artifacts_root = (
        args.artifacts_root.resolve()
        if getattr(args, "artifacts_root", None) is not None
        else Path("output/ci/domain_smoke_artifacts").resolve()
    )
    summary_output_path = (
        args.output_path.resolve()
        if getattr(args, "output_path", None) is not None
        else Path("output/ci/domain_smoke/domain_smoke_cross_domain_summary.json").resolve()
    )
    summary_markdown_path = (
        args.markdown_path.resolve()
        if getattr(args, "markdown_path", None) is not None
        else Path("output/ci/domain_smoke/domain_smoke_cross_domain_summary.md").resolve()
    )
    summary_output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_markdown_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_json(path: Path) -> dict[str, Any]:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}

    summary_files = sorted(artifacts_root.glob("**/*_smoke_summary.json"))
    alert_files = sorted(artifacts_root.glob("**/*_smoke_alert.json"))
    artifact_dirs = sorted(path for path in artifacts_root.glob("domain-smoke-*") if path.is_dir())

    summary_by_domain: dict[str, dict[str, Any]] = {}
    for path in summary_files:
        summary_payload = _load_json(path)
        domain = str(summary_payload.get("domain") or "").strip().lower()
        if not domain:
            domain = path.name.replace("_smoke_summary.json", "").strip().lower()
        if not domain:
            continue
        summary_by_domain[domain] = summary_payload

    alert_by_domain: dict[str, dict[str, Any]] = {}
    for path in alert_files:
        alert_payload = _load_json(path)
        domain = str(alert_payload.get("domain") or "").strip().lower()
        if not domain:
            domain = path.name.replace("_smoke_alert.json", "").strip().lower()
        if not domain:
            continue
        alert_by_domain[domain] = alert_payload

    domains = set(summary_by_domain) | set(alert_by_domain)
    for path in artifact_dirs:
        suffix = path.name.removeprefix("domain-smoke-").strip().lower()
        if suffix:
            domains.add(suffix)

    rows: list[dict[str, Any]] = []
    for domain in sorted(domains):
        summary_payload = dict(summary_by_domain.get(domain) or {})
        alert_payload = dict(alert_by_domain.get(domain) or {})
        smoke_status = str(summary_payload.get("status") or "warning").strip().lower() or "warning"
        smoke_reason = (
            str(alert_payload.get("smoke_reason") or "").strip()
            or str(summary_payload.get("reason") or "").strip()
            or ("none" if smoke_status == "ok" else f"domain_smoke_status_not_ok:{smoke_status}")
        )
        alert_created = bool(alert_payload.get("alert_created"))
        interrupt_id = str(alert_payload.get("interrupt_id") or "").strip()
        acknowledge_command = str(alert_payload.get("acknowledge_command") or "").strip()
        rerun_command = str(alert_payload.get("rerun_command") or "").strip()
        runtime_error = str(alert_payload.get("runtime_error") or "").strip()

        risk_score = 0
        if smoke_status != "ok":
            risk_score += 70
        if alert_created:
            risk_score += 20
        if interrupt_id:
            risk_score += 5
        if runtime_error and runtime_error.lower() != "none":
            risk_score += 8
        if "missing" in smoke_reason:
            risk_score += 5
        if "mismatch" in smoke_reason:
            risk_score += 4

        if risk_score >= 90:
            risk_tier = "critical"
        elif risk_score >= 60:
            risk_tier = "warn"
        else:
            risk_tier = "ok"

        rows.append(
            {
                "domain": domain,
                "smoke_status": smoke_status,
                "smoke_reason": smoke_reason,
                "smoke_blocking": 1 if smoke_status != "ok" else 0,
                "risk_score": int(risk_score),
                "risk_tier": risk_tier,
                "alert_created": alert_created,
                "interrupt_id": interrupt_id or None,
                "acknowledge_command": acknowledge_command or None,
                "rerun_command": rerun_command or None,
                "runtime_error": runtime_error or None,
                "has_summary_artifact": domain in summary_by_domain,
                "has_alert_artifact": domain in alert_by_domain,
                "summary_path": summary_payload.get("summary_path"),
                "alert_path": alert_payload.get("alert_path"),
            }
        )

    rows.sort(key=lambda item: (-int(item.get("risk_score") or 0), str(item.get("domain") or "")))
    warning_count = sum(1 for row in rows if str(row.get("smoke_status") or "") != "ok")
    ok_count = sum(1 for row in rows if str(row.get("smoke_status") or "") == "ok")
    blocking_count = sum(int(row.get("smoke_blocking") or 0) for row in rows)
    alerts_created_count = sum(1 for row in rows if bool(row.get("alert_created")))
    top_risks = [row for row in rows if int(row.get("risk_score") or 0) > 0][:4]
    missing_summary_domains = [
        str(row.get("domain") or "")
        for row in rows
        if not bool(row.get("has_summary_artifact"))
    ]
    missing_alert_domains = [
        str(row.get("domain") or "")
        for row in rows
        if int(row.get("smoke_blocking") or 0) > 0 and not bool(row.get("has_alert_artifact"))
    ]
    summary_status = "ok" if warning_count == 0 else "warning"
    if not rows:
        summary_status = "warning"

    suggested_actions: list[str] = []
    for row in top_risks[:3]:
        row_domain = str(row.get("domain") or "unknown")
        rerun_command = str(row.get("rerun_command") or "").strip()
        acknowledge_command = str(row.get("acknowledge_command") or "").strip()
        if rerun_command:
            suggested_actions.append(f"[{row_domain}] rerun smoke loop: {rerun_command}")
        if acknowledge_command:
            suggested_actions.append(f"[{row_domain}] acknowledge interrupt: {acknowledge_command}")
    if not suggested_actions and rows:
        suggested_actions.append("No blocking cross-domain smoke risk detected; continue scheduled cadence.")
    if not rows:
        suggested_actions.append("No domain smoke artifacts found; verify upstream matrix artifact upload.")
    suggested_action_count = len(suggested_actions)
    first_suggested_action = suggested_actions[0] if suggested_actions else "none"

    per_domain_acknowledge_commands: list[str] = []
    for row in rows:
        acknowledge_command = str(row.get("acknowledge_command") or "").strip()
        if acknowledge_command and acknowledge_command not in per_domain_acknowledge_commands:
            per_domain_acknowledge_commands.append(acknowledge_command)
    per_domain_acknowledge_sequence = (
        " && ".join(per_domain_acknowledge_commands)
        if per_domain_acknowledge_commands
        else "none"
    )
    rerun_preview_commands: list[str] = []
    for row in rows:
        rerun_command = str(row.get("rerun_command") or "").strip()
        if rerun_command and rerun_command not in rerun_preview_commands:
            rerun_preview_commands.append(rerun_command)
    rerun_command_count = len(rerun_preview_commands)
    first_rerun_command = rerun_preview_commands[0] if rerun_preview_commands else "none"
    per_domain_acknowledge_command_count = len(per_domain_acknowledge_commands)
    first_per_domain_acknowledge_command = (
        per_domain_acknowledge_commands[0]
        if per_domain_acknowledge_commands
        else "none"
    )

    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": summary_status,
        "artifact_root": str(artifacts_root),
        "domain_count": len(rows),
        "summary_file_count": len(summary_files),
        "alert_file_count": len(alert_files),
        "ok_count": ok_count,
        "warning_count": warning_count,
        "blocking_count": blocking_count,
        "alerts_created_count": alerts_created_count,
        "missing_summary_domains": missing_summary_domains,
        "missing_alert_domains": missing_alert_domains,
        "top_risks": top_risks,
        "domains": rows,
        "suggested_actions": suggested_actions,
        "suggested_action_count": suggested_action_count,
        "first_suggested_action": first_suggested_action,
        "operator_ack_bundle": {
            "status": "ready" if per_domain_acknowledge_commands else "empty",
            "command_count": per_domain_acknowledge_command_count,
            "commands": per_domain_acknowledge_commands,
            "command_sequence": per_domain_acknowledge_sequence,
            "first_command": None
            if first_per_domain_acknowledge_command == "none"
            else first_per_domain_acknowledge_command,
            "per_domain_command_count": per_domain_acknowledge_command_count,
            "cross_domain_command_count": 0,
            "cross_domain_interrupt_id": None,
            "cross_domain_acknowledge_command": None,
        },
        "acknowledge_bundle_commands": per_domain_acknowledge_commands,
        "acknowledge_bundle_command_sequence": per_domain_acknowledge_sequence,
        "acknowledge_command_count": per_domain_acknowledge_command_count,
        "first_acknowledge_command": first_per_domain_acknowledge_command,
        "rerun_preview_commands": rerun_preview_commands,
        "rerun_command_count": rerun_command_count,
        "first_rerun_command": first_rerun_command,
    }
    summary_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    markdown_lines = [
        "# Domain Smoke Cross-Domain Summary",
        "",
        f"- status: `{summary_status}`",
        f"- domain_count: `{len(rows)}`",
        f"- warning_count: `{warning_count}`",
        f"- blocking_count: `{blocking_count}`",
        f"- alerts_created_count: `{alerts_created_count}`",
        "",
        "## Top Risks",
        "",
    ]
    if top_risks:
        for row in top_risks:
            markdown_lines.extend(
                [
                    f"- `{row.get('domain')}` risk_score={row.get('risk_score')} status={row.get('smoke_status')} reason={row.get('smoke_reason')}",
                    f"  - rerun_command: `{row.get('rerun_command') or 'none'}`",
                    f"  - acknowledge_command: `{row.get('acknowledge_command') or 'none'}`",
                ]
            )
    else:
        markdown_lines.append("- none")
    markdown_lines.extend(["", "## Suggested Actions", ""])
    for action in suggested_actions:
        markdown_lines.append(f"- {action}")
    summary_markdown_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    top_domain = str((top_risks[0] or {}).get("domain") or "none") if top_risks else "none"
    top_risk_score = int((top_risks[0] or {}).get("risk_score") or 0) if top_risks else 0
    payload["summary_path"] = str(summary_output_path)
    payload["summary_markdown_path"] = str(summary_markdown_path)
    payload["cross_domain_status"] = summary_status
    payload["top_domain"] = top_domain
    payload["top_risk_score"] = top_risk_score

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"summary_path={summary_output_path}",
            f"summary_markdown_path={summary_markdown_path}",
            f"cross_domain_status={summary_status}",
            f"domain_count={len(rows)}",
            f"warning_count={warning_count}",
            f"blocking_count={blocking_count}",
            f"alerts_created_count={alerts_created_count}",
            f"top_domain={top_domain}",
            f"top_risk_score={top_risk_score}",
            f"suggested_action_count={suggested_action_count}",
            f"first_suggested_action={first_suggested_action}",
            f"acknowledge_command_count={per_domain_acknowledge_command_count}",
            f"first_acknowledge_command={first_per_domain_acknowledge_command}",
            f"rerun_command_count={rerun_command_count}",
            f"first_rerun_command={first_rerun_command}",
            f"acknowledge_bundle_command_sequence={per_domain_acknowledge_sequence}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- status: `{summary_status}`",
                    f"- domain_count: `{len(rows)}`",
                    f"- warning_count: `{warning_count}`",
                    f"- blocking_count: `{blocking_count}`",
                    f"- alerts_created_count: `{alerts_created_count}`",
                    f"- top_domain: `{top_domain}`",
                    f"- top_risk_score: `{top_risk_score}`",
                    f"- suggested_action_count: `{suggested_action_count}`",
                    f"- first_suggested_action: `{first_suggested_action}`",
                    f"- acknowledge_command_count: `{per_domain_acknowledge_command_count}`",
                    f"- first_acknowledge_command: `{first_per_domain_acknowledge_command}`",
                    f"- rerun_command_count: `{rerun_command_count}`",
                    f"- first_rerun_command: `{first_rerun_command}`",
                    f"- acknowledge_bundle_command_sequence: `{per_domain_acknowledge_sequence}`",
                    f"- summary_path: `{summary_output_path}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )


def cmd_improvement_domain_smoke_cross_domain_runtime_alert(args: argparse.Namespace) -> None:
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(str(value).strip()))
        except Exception:
            return default

    summary_path = (
        args.summary_path.resolve()
        if getattr(args, "summary_path", None) is not None
        else Path("output/ci/domain_smoke/domain_smoke_cross_domain_summary.json").resolve()
    )
    summary_payload: dict[str, Any] = {}
    if summary_path.exists():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            loaded = {}
        if isinstance(loaded, dict):
            summary_payload = dict(loaded)

    warning_count = _to_int(
        getattr(args, "warning_count", None) if getattr(args, "warning_count", None) is not None else summary_payload.get("warning_count"),
        default=0,
    )
    blocking_count = _to_int(
        getattr(args, "blocking_count", None) if getattr(args, "blocking_count", None) is not None else summary_payload.get("blocking_count"),
        default=0,
    )
    top_domain = (
        str(getattr(args, "top_domain", None) or summary_payload.get("top_domain") or "none").strip().lower()
        or "none"
    )
    top_risk_score = _to_int(
        getattr(args, "top_risk_score", None) if getattr(args, "top_risk_score", None) is not None else summary_payload.get("top_risk_score"),
        default=0,
    )
    top_risks = [
        row
        for row in list(summary_payload.get("top_risks") or [])
        if isinstance(row, dict)
    ]

    alert_path = (
        args.output_path.resolve()
        if getattr(args, "output_path", None) is not None
        else Path("output/ci/domain_smoke/domain_smoke_cross_domain_alert.json").resolve()
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (
        args.db_path.resolve()
        if getattr(args, "db_path", None) is not None
        else (alert_path.parent / "jarvis.db").resolve()
    )

    rerun_commands = [
        str(row.get("rerun_command") or "").strip()
        for row in top_risks[:3]
        if str(row.get("rerun_command") or "").strip()
    ]
    domain_refs = [
        f"{str(row.get('domain') or 'unknown').strip().lower()}:{str(row.get('smoke_reason') or 'none').strip()}"
        for row in top_risks[:3]
    ]
    compact_refs = ",".join(domain_refs) if domain_refs else "none"
    rerun_command = rerun_commands[0] if rerun_commands else (
        "./scripts/run_improvement_domain_smoke.sh "
        "./configs/improvement_operator_knowledge_stack.json "
        f"{top_domain if top_domain != 'none' else 'quant_finance'} --allow-missing"
    )
    rerun_preview_commands = list(rerun_commands)
    if rerun_command and rerun_command not in rerun_preview_commands:
        rerun_preview_commands.append(rerun_command)
    rerun_command_count = len(rerun_preview_commands)
    first_rerun_command = rerun_preview_commands[0] if rerun_preview_commands else "none"

    reason = (
        "cross_domain_smoke_warning"
        + f" warning_count={warning_count}"
        + f" blocking_count={blocking_count}"
        + f" top_domain={top_domain}"
        + f" top_risk_score={top_risk_score}"
        + f" refs={compact_refs}"
    )
    why_now = (
        "cross-domain smoke aggregate detected active warning lanes and requires centralized triage "
        "before promotion cadence continues."
    )
    why_not_later = (
        "delayed cross-domain smoke triage can compound ingestion and seed-quality drift across "
        "quant, Kalshi weather, fitness, and market-ml loops."
    )

    interrupt_id = ""
    acknowledge_command = "none"
    runtime_error = "none"
    runtime = None
    try:
        runtime = JarvisRuntime(
            db_path=db_path,
            repo_path=args.repo_path.resolve(),
        )
        urgency_score = 0.96 if blocking_count > 0 else 0.91
        confidence = 0.92 if top_risk_score >= 80 else 0.88
        decision = InterruptDecision(
            interrupt_id=new_id("int"),
            candidate_id=new_id("cand"),
            domain="operations",
            reason=reason,
            urgency_score=urgency_score,
            confidence=confidence,
            suppression_window_hit=False,
            delivered=True,
            why_now=why_now,
            why_not_later=why_not_later,
            status="delivered",
        )
        runtime.interrupt_store.store(decision)
        interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
        interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
        if interrupt_id:
            acknowledge_command = (
                "python3 -m jarvis.cli interrupts acknowledge "
                f"{interrupt_id} --actor operator --db-path {db_path}"
            )
        runtime.memory.append_event(
            "improvement.domain_smoke_cross_domain_alert_created",
            {
                "interrupt_id": interrupt_id or None,
                "summary_path": str(summary_path),
                "warning_count": warning_count,
                "blocking_count": blocking_count,
                "top_domain": top_domain,
                "top_risk_score": top_risk_score,
                "top_risks": top_risks[:3],
                "rerun_commands": rerun_commands,
            },
        )
    except Exception as exc:
        runtime_error = str(exc).strip() or "unknown_runtime_error"
    finally:
        if runtime is not None:
            runtime.close()

    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": "warning",
        "summary_path": str(summary_path),
        "warning_count": warning_count,
        "blocking_count": blocking_count,
        "top_domain": top_domain,
        "top_risk_score": top_risk_score,
        "top_risks": top_risks[:3],
        "alert_created": bool(interrupt_id),
        "interrupt_id": interrupt_id or None,
        "interrupt_db_path": str(db_path),
        "acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
        "rerun_command": rerun_command,
        "rerun_commands": rerun_commands,
        "rerun_preview_commands": rerun_preview_commands,
        "rerun_command_count": rerun_command_count,
        "first_rerun_command": first_rerun_command,
        "reason": reason,
        "why_now": why_now,
        "why_not_later": why_not_later,
        "runtime_error": None if runtime_error == "none" else runtime_error,
    }
    suggested_actions = [
        str(item).strip()
        for item in list(summary_payload.get("suggested_actions") or [])
        if str(item).strip()
    ]
    suggested_action_count = len(suggested_actions)
    first_suggested_action = suggested_actions[0] if suggested_actions else "none"

    existing_bundle = dict(summary_payload.get("operator_ack_bundle") or {})
    bundle_commands = [
        str(command).strip()
        for command in list(existing_bundle.get("commands") or summary_payload.get("acknowledge_bundle_commands") or [])
        if str(command).strip()
    ]
    if not bundle_commands:
        for row in list(summary_payload.get("domains") or []):
            if not isinstance(row, dict):
                continue
            row_ack_command = str(row.get("acknowledge_command") or "").strip()
            if row_ack_command and row_ack_command not in bundle_commands:
                bundle_commands.append(row_ack_command)
    per_domain_command_count = len(bundle_commands)
    if acknowledge_command != "none" and acknowledge_command not in bundle_commands:
        bundle_commands.append(acknowledge_command)
    cross_domain_command_count = 1 if acknowledge_command != "none" and acknowledge_command in bundle_commands else 0
    bundle_command_count = len(bundle_commands)
    first_acknowledge_command = bundle_commands[0] if bundle_commands else "none"
    bundle_sequence = " && ".join(bundle_commands) if bundle_commands else "none"
    summary_payload["operator_ack_bundle"] = {
        "status": "ready" if bundle_commands else "empty",
        "command_count": bundle_command_count,
        "commands": bundle_commands,
        "command_sequence": bundle_sequence,
        "first_command": None if first_acknowledge_command == "none" else first_acknowledge_command,
        "per_domain_command_count": per_domain_command_count,
        "cross_domain_command_count": cross_domain_command_count,
        "cross_domain_interrupt_id": interrupt_id or None,
        "cross_domain_acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
    }
    summary_payload["acknowledge_bundle_commands"] = bundle_commands
    summary_payload["acknowledge_bundle_command_sequence"] = bundle_sequence
    summary_payload["acknowledge_command_count"] = bundle_command_count
    summary_payload["first_acknowledge_command"] = first_acknowledge_command
    summary_payload["rerun_preview_commands"] = rerun_preview_commands
    summary_payload["rerun_command_count"] = rerun_command_count
    summary_payload["first_rerun_command"] = first_rerun_command
    summary_payload["suggested_action_count"] = suggested_action_count
    summary_payload["first_suggested_action"] = first_suggested_action
    summary_payload["cross_domain_interrupt_id"] = interrupt_id or None
    summary_payload["cross_domain_alert_path"] = str(alert_path)
    if summary_path:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")

    payload["acknowledge_bundle_commands"] = bundle_commands
    payload["acknowledge_bundle_command_sequence"] = bundle_sequence
    payload["acknowledge_command_count"] = bundle_command_count
    payload["first_acknowledge_command"] = first_acknowledge_command
    payload["rerun_command_count"] = rerun_command_count
    payload["first_rerun_command"] = first_rerun_command
    payload["suggested_actions"] = suggested_actions
    payload["suggested_action_count"] = suggested_action_count
    payload["first_suggested_action"] = first_suggested_action
    alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    payload["cross_domain_alert_path"] = str(alert_path)
    payload["cross_domain_interrupt_id"] = interrupt_id or "none"
    payload["cross_domain_alert_created"] = 1 if interrupt_id else 0
    payload["cross_domain_acknowledge_command"] = acknowledge_command
    payload["cross_domain_rerun_command"] = rerun_command
    payload["cross_domain_runtime_error"] = runtime_error

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"cross_domain_alert_path={alert_path}",
            f"cross_domain_interrupt_id={interrupt_id or 'none'}",
            f"cross_domain_alert_created={1 if interrupt_id else 0}",
            f"cross_domain_acknowledge_command={acknowledge_command}",
            f"cross_domain_rerun_command={rerun_command}",
            f"suggested_action_count={suggested_action_count}",
            f"first_suggested_action={first_suggested_action}",
            f"acknowledge_command_count={bundle_command_count}",
            f"first_acknowledge_command={first_acknowledge_command}",
            f"rerun_command_count={rerun_command_count}",
            f"first_rerun_command={first_rerun_command}",
            f"acknowledge_bundle_command_sequence={bundle_sequence}",
            f"cross_domain_runtime_error={runtime_error}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- interrupt_id: `{interrupt_id or 'none'}`",
                    f"- alert_created: `{1 if interrupt_id else 0}`",
                    f"- warning_count: `{warning_count}`",
                    f"- blocking_count: `{blocking_count}`",
                    f"- top_domain: `{top_domain}`",
                    f"- top_risk_score: `{top_risk_score}`",
                    f"- rerun_command: `{rerun_command}`",
                    f"- acknowledge_command: `{acknowledge_command}`",
                    f"- suggested_action_count: `{suggested_action_count}`",
                    f"- first_suggested_action: `{first_suggested_action}`",
                    f"- acknowledge_command_count: `{bundle_command_count}`",
                    f"- first_acknowledge_command: `{first_acknowledge_command}`",
                    f"- rerun_command_count: `{rerun_command_count}`",
                    f"- first_rerun_command: `{first_rerun_command}`",
                    f"- acknowledge_bundle_command_sequence: `{bundle_sequence}`",
                    f"- runtime_error: `{runtime_error}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)):
        if runtime_error != "none" or not bool(interrupt_id):
            raise SystemExit(2)


def cmd_improvement_controlled_matrix_compact(args: argparse.Namespace) -> None:
    artifact_root = (
        args.artifact_root.resolve()
        if getattr(args, "artifact_root", None) is not None
        else Path("output/ci/controlled_matrix").resolve()
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    daily_report_path = (
        args.daily_report_path.resolve()
        if getattr(args, "daily_report_path", None) is not None
        else (artifact_root / "daily_pipeline_report.json").resolve()
    )
    verify_alert_path = (
        args.verify_alert_path.resolve()
        if getattr(args, "verify_alert_path", None) is not None
        else (artifact_root / "verify_matrix_alert_report.json").resolve()
    )
    summary_path = (
        args.output_path.resolve()
        if getattr(args, "output_path", None) is not None
        else (artifact_root / "controlled_matrix_summary.json").resolve()
    )
    summary_markdown_path = (
        args.markdown_path.resolve()
        if getattr(args, "markdown_path", None) is not None
        else (artifact_root / "controlled_matrix_summary.md").resolve()
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_markdown_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {}
    if verify_alert_path.exists():
        try:
            loaded = json.loads(verify_alert_path.read_text(encoding="utf-8"))
        except Exception:
            loaded = {}
        if isinstance(loaded, dict):
            payload = dict(loaded)

    matrix_status = str(payload.get("status") or "warning").strip() or "warning"
    drift_severity = str(payload.get("drift_severity") or "unknown").strip() or "unknown"
    severity_profile = dict(payload.get("severity_profile") or {})
    mismatch_count = _coerce_int(severity_profile.get("mismatch_count"), default=0)
    missing_count = _coerce_int(severity_profile.get("missing_count"), default=0)
    invalid_count = _coerce_int(severity_profile.get("invalid_count"), default=0)
    guardrail_mismatch_count = _coerce_int(severity_profile.get("guardrail_mismatch_count"), default=0)
    drift_score = _coerce_int(severity_profile.get("score"), default=0)

    alert = dict(payload.get("alert") or {})
    alert_created = bool(payload.get("alert_created"))
    interrupt_id = str(alert.get("interrupt_id") or "").strip()
    interrupt_id_output = interrupt_id if interrupt_id else "none"

    acknowledge_commands = [
        str(command).strip()
        for command in list(payload.get("acknowledge_commands") or [])
        if str(command).strip()
    ]
    acknowledge_command_count = len(acknowledge_commands)
    first_acknowledge_command = acknowledge_commands[0] if acknowledge_commands else "none"

    mitigation_actions = [
        str(action).strip()
        for action in list(payload.get("mitigation_actions") or [])
        if str(action).strip()
    ]
    mitigation_action_count = len(mitigation_actions)
    first_mitigation_action = mitigation_actions[0] if mitigation_actions else "none"

    top_scenarios = [
        str(item).strip()
        for item in list(alert.get("top_scenarios") or [])
        if str(item).strip()
    ]
    top_scenario_count = len(top_scenarios)
    first_top_scenario = top_scenarios[0] if top_scenarios else "none"

    rerun_command = str(getattr(args, "rerun_command", "") or "").strip()
    if not rerun_command:
        rerun_command = (
            "./scripts/run_improvement_verify_matrix_alert.sh "
            "./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json "
            "output/ci/controlled_matrix/daily_pipeline_report.json "
            "--output-path output/ci/controlled_matrix/verify_matrix_alert_report.json "
            "--json-compact --alert-domain operations --alert-max-items 4"
        )
    repair_commands = list(acknowledge_commands)
    _append_unique_string(repair_commands, rerun_command)
    repair_command_count = len(repair_commands)
    first_repair_command = repair_commands[0] if repair_commands else "none"
    operator_ack_bundle_command_sequence = " && ".join(repair_commands) if repair_commands else "none"

    suggested_actions: list[str] = []
    for action in mitigation_actions:
        _append_unique_string(suggested_actions, action)
    if acknowledge_commands:
        _append_unique_string(
            suggested_actions,
            f"[matrix] acknowledge alert: {first_acknowledge_command}",
        )
    _append_unique_string(
        suggested_actions,
        f"[matrix] rerun controlled matrix alert: {rerun_command}",
    )
    suggested_action_count = len(suggested_actions)
    first_suggested_action = suggested_actions[0] if suggested_actions else "none"
    operator_ack_bundle = {
        "status": "ready" if repair_commands else "empty",
        "command_count": repair_command_count,
        "commands": repair_commands,
        "command_sequence": operator_ack_bundle_command_sequence,
        "first_command": None if first_repair_command == "none" else first_repair_command,
    }

    summary_payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": matrix_status,
        "drift_severity": drift_severity,
        "drift_score": drift_score,
        "mismatch_count": mismatch_count,
        "missing_count": missing_count,
        "invalid_count": invalid_count,
        "guardrail_mismatch_count": guardrail_mismatch_count,
        "alert_created": alert_created,
        "interrupt_id": interrupt_id if interrupt_id else None,
        "daily_report_path": str(daily_report_path),
        "verify_matrix_alert_report_path": str(verify_alert_path),
        "verify_matrix_alert_report_present": verify_alert_path.exists(),
        "daily_report_present": daily_report_path.exists(),
        "acknowledge_commands": acknowledge_commands,
        "acknowledge_command_count": acknowledge_command_count,
        "first_acknowledge_command": first_acknowledge_command,
        "repair_commands": repair_commands,
        "repair_command_count": repair_command_count,
        "first_repair_command": first_repair_command,
        "operator_ack_bundle": operator_ack_bundle,
        "operator_ack_bundle_command_sequence": operator_ack_bundle_command_sequence,
        "mitigation_actions": mitigation_actions,
        "mitigation_action_count": mitigation_action_count,
        "first_mitigation_action": first_mitigation_action,
        "suggested_actions": suggested_actions,
        "suggested_action_count": suggested_action_count,
        "first_suggested_action": first_suggested_action,
        "top_scenarios": top_scenarios,
        "top_scenario_count": top_scenario_count,
        "first_top_scenario": first_top_scenario,
        "rerun_command": rerun_command,
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")

    markdown_lines = [
        "# Controlled Matrix Summary",
        "",
        f"- status: `{matrix_status}`",
        f"- drift_severity: `{drift_severity}`",
        f"- drift_score: `{drift_score}`",
        f"- mismatch_count: `{mismatch_count}`",
        f"- missing_count: `{missing_count}`",
        f"- invalid_count: `{invalid_count}`",
        f"- guardrail_mismatch_count: `{guardrail_mismatch_count}`",
        f"- alert_created: `{int(alert_created)}`",
        f"- interrupt_id: `{interrupt_id_output}`",
        f"- acknowledge_command_count: `{acknowledge_command_count}`",
        f"- first_acknowledge_command: `{first_acknowledge_command}`",
        f"- repair_command_count: `{repair_command_count}`",
        f"- first_repair_command: `{first_repair_command}`",
        f"- suggested_action_count: `{suggested_action_count}`",
        f"- first_suggested_action: `{first_suggested_action}`",
        f"- mitigation_action_count: `{mitigation_action_count}`",
        f"- first_mitigation_action: `{first_mitigation_action}`",
        f"- top_scenario_count: `{top_scenario_count}`",
        f"- first_top_scenario: `{first_top_scenario}`",
        f"- rerun_command: `{rerun_command}`",
        "",
        "## Top Scenarios",
        "",
    ]
    if top_scenarios:
        for scenario in top_scenarios:
            markdown_lines.append(f"- {scenario}")
    else:
        markdown_lines.append("- none")
    markdown_lines.extend(["", "## Suggested Actions", ""])
    if suggested_actions:
        for action in suggested_actions:
            markdown_lines.append(f"- {action}")
    else:
        markdown_lines.append("- none")
    markdown_lines.extend(["", "## Mitigation Actions", ""])
    if mitigation_actions:
        for action in mitigation_actions:
            markdown_lines.append(f"- {action}")
    else:
        markdown_lines.append("- none")
    summary_markdown_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    summary_payload["matrix_summary_path"] = str(summary_path)
    summary_payload["matrix_summary_markdown_path"] = str(summary_markdown_path)
    summary_payload["matrix_interrupt_id"] = interrupt_id_output

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"matrix_summary_path={summary_path}",
            f"matrix_summary_markdown_path={summary_markdown_path}",
            f"matrix_status={matrix_status}",
            f"drift_severity={drift_severity}",
            f"drift_score={drift_score}",
            f"mismatch_count={mismatch_count}",
            f"missing_count={missing_count}",
            f"invalid_count={invalid_count}",
            f"guardrail_mismatch_count={guardrail_mismatch_count}",
            f"alert_created={int(alert_created)}",
            f"matrix_interrupt_id={interrupt_id_output}",
            f"acknowledge_command_count={acknowledge_command_count}",
            f"first_acknowledge_command={first_acknowledge_command}",
            f"repair_command_count={repair_command_count}",
            f"first_repair_command={first_repair_command}",
            f"operator_ack_bundle_command_sequence={operator_ack_bundle_command_sequence}",
            f"mitigation_action_count={mitigation_action_count}",
            f"first_mitigation_action={first_mitigation_action}",
            f"suggested_action_count={suggested_action_count}",
            f"first_suggested_action={first_suggested_action}",
            f"top_scenario_count={top_scenario_count}",
            f"first_top_scenario={first_top_scenario}",
            f"rerun_command={rerun_command}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- status: `{matrix_status}`",
                    f"- drift_severity: `{drift_severity}`",
                    f"- drift_score: `{drift_score}`",
                    f"- mismatch_count: `{mismatch_count}`",
                    f"- missing_count: `{missing_count}`",
                    f"- invalid_count: `{invalid_count}`",
                    f"- guardrail_mismatch_count: `{guardrail_mismatch_count}`",
                    f"- alert_created: `{int(alert_created)}`",
                    f"- interrupt_id: `{interrupt_id_output}`",
                    f"- acknowledge_command_count: `{acknowledge_command_count}`",
                    f"- first_acknowledge_command: `{first_acknowledge_command}`",
                    f"- repair_command_count: `{repair_command_count}`",
                    f"- first_repair_command: `{first_repair_command}`",
                    f"- suggested_action_count: `{suggested_action_count}`",
                    f"- first_suggested_action: `{first_suggested_action}`",
                    f"- mitigation_action_count: `{mitigation_action_count}`",
                    f"- first_mitigation_action: `{first_mitigation_action}`",
                    f"- top_scenario_count: `{top_scenario_count}`",
                    f"- first_top_scenario: `{first_top_scenario}`",
                    f"- rerun_command: `{rerun_command}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        summary_payload,
        compact=bool(getattr(args, "json_compact", False)),
    )


def cmd_improvement_controlled_matrix_runtime_alert(args: argparse.Namespace) -> None:
    summary_path = (
        args.summary_path.resolve()
        if getattr(args, "summary_path", None) is not None
        else Path("output/ci/controlled_matrix/controlled_matrix_summary.json").resolve()
    )
    summary_payload: dict[str, Any] = {}
    if summary_path.exists():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            loaded = {}
        if isinstance(loaded, dict):
            summary_payload = dict(loaded)

    daily_outcome = str(getattr(args, "daily_outcome", None) or "unknown").strip().lower() or "unknown"
    matrix_status = (
        str(getattr(args, "matrix_status", None) or summary_payload.get("status") or "warning").strip().lower()
        or "warning"
    )
    drift_severity = (
        str(getattr(args, "drift_severity", None) or summary_payload.get("drift_severity") or "unknown")
        .strip()
        .lower()
        or "unknown"
    )
    first_repair_command = (
        str(getattr(args, "first_repair_command", None) or summary_payload.get("first_repair_command") or "")
        .strip()
    )
    rerun_command = (
        str(getattr(args, "rerun_command", None) or summary_payload.get("rerun_command") or "").strip()
    )
    if not first_repair_command:
        first_repair_command = rerun_command
    if not first_repair_command:
        first_repair_command = (
            "./scripts/run_improvement_verify_matrix_alert.sh "
            "./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json "
            "output/ci/controlled_matrix/daily_pipeline_report.json "
            "--output-path output/ci/controlled_matrix/verify_matrix_alert_report.json "
            "--json-compact --alert-domain operations --alert-max-items 4"
        )
    first_suggested_action = (
        str(
            getattr(args, "first_suggested_action", None)
            or summary_payload.get("first_suggested_action")
            or f"rerun controlled matrix triage: {first_repair_command}"
        ).strip()
        or f"rerun controlled matrix triage: {first_repair_command}"
    )

    alert_path = (
        args.output_path.resolve()
        if getattr(args, "output_path", None) is not None
        else (summary_path.parent / "controlled_matrix_runtime_alert.json").resolve()
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (
        args.db_path.resolve()
        if getattr(args, "db_path", None) is not None
        else (alert_path.parent / "jarvis.db").resolve()
    )

    reason = (
        "controlled_matrix_runtime_gate_failure"
        + f" daily_outcome={daily_outcome}"
        + f" matrix_status={matrix_status}"
        + f" drift_severity={drift_severity}"
    )
    why_now = (
        "controlled matrix validation failed without a direct matrix interrupt and requires "
        "immediate operator triage to restore reliable hypothesis testing cadence."
    )
    why_not_later = (
        "delaying controlled matrix runtime triage can leave quant, Kalshi weather, fitness, "
        "and market-ml validation coverage stale."
    )

    interrupt_id = ""
    acknowledge_command = "none"
    runtime_error = "none"
    runtime = None
    try:
        runtime = JarvisRuntime(
            db_path=db_path,
            repo_path=args.repo_path.resolve(),
        )
        urgency_score = 0.97 if daily_outcome != "success" else 0.93
        confidence = 0.92 if matrix_status != "ok" else 0.88
        decision = InterruptDecision(
            interrupt_id=new_id("int"),
            candidate_id=new_id("cand"),
            domain="operations",
            reason=reason,
            urgency_score=urgency_score,
            confidence=confidence,
            suppression_window_hit=False,
            delivered=True,
            why_now=why_now,
            why_not_later=why_not_later,
            status="delivered",
        )
        runtime.interrupt_store.store(decision)
        interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
        interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
        if interrupt_id:
            acknowledge_command = (
                "python3 -m jarvis.cli interrupts acknowledge "
                f"{interrupt_id} --actor operator --db-path {db_path}"
            )
        runtime.memory.append_event(
            "improvement.controlled_matrix_runtime_alert_created",
            {
                "interrupt_id": interrupt_id or None,
                "summary_path": str(summary_path),
                "daily_outcome": daily_outcome,
                "matrix_status": matrix_status,
                "drift_severity": drift_severity,
                "first_repair_command": first_repair_command,
            },
        )
    except Exception as exc:
        runtime_error = str(exc).strip() or "unknown_runtime_error"
    finally:
        if runtime is not None:
            runtime.close()

    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": "warning",
        "daily_outcome": daily_outcome,
        "matrix_status": matrix_status,
        "drift_severity": drift_severity,
        "summary_path": str(summary_path),
        "alert_created": bool(interrupt_id),
        "interrupt_id": interrupt_id or None,
        "interrupt_db_path": str(db_path),
        "acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
        "first_repair_command": first_repair_command,
        "first_suggested_action": first_suggested_action,
        "reason": reason,
        "why_now": why_now,
        "why_not_later": why_not_later,
        "runtime_error": None if runtime_error == "none" else runtime_error,
    }
    alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if summary_path and summary_payload:
        summary_payload["runtime_alert_path"] = str(alert_path)
        summary_payload["runtime_alert_created"] = bool(interrupt_id)
        summary_payload["runtime_interrupt_id"] = interrupt_id or None
        summary_payload["runtime_acknowledge_command"] = None if acknowledge_command == "none" else acknowledge_command
        summary_payload["runtime_first_repair_command"] = first_repair_command
        summary_payload["runtime_error"] = None if runtime_error == "none" else runtime_error

        acknowledge_commands = [
            str(command).strip()
            for command in list(summary_payload.get("acknowledge_commands") or [])
            if str(command).strip()
        ]
        if acknowledge_command != "none":
            _append_unique_string(acknowledge_commands, acknowledge_command)
        summary_payload["acknowledge_commands"] = acknowledge_commands
        summary_payload["acknowledge_command_count"] = len(acknowledge_commands)
        summary_payload["first_acknowledge_command"] = acknowledge_commands[0] if acknowledge_commands else "none"

        repair_commands = [
            str(command).strip()
            for command in list(summary_payload.get("repair_commands") or [])
            if str(command).strip()
        ]
        if not repair_commands:
            repair_commands = list(acknowledge_commands)
        _append_unique_string(repair_commands, first_repair_command)
        summary_payload["repair_commands"] = repair_commands
        summary_payload["repair_command_count"] = len(repair_commands)
        summary_payload["first_repair_command"] = repair_commands[0] if repair_commands else first_repair_command
        repair_sequence = " && ".join(repair_commands) if repair_commands else "none"
        summary_payload["operator_ack_bundle"] = {
            "status": "ready" if repair_commands else "empty",
            "command_count": len(repair_commands),
            "commands": repair_commands,
            "command_sequence": repair_sequence,
            "first_command": repair_commands[0] if repair_commands else None,
        }
        summary_payload["operator_ack_bundle_command_sequence"] = repair_sequence

        suggested_actions = [
            str(action).strip()
            for action in list(summary_payload.get("suggested_actions") or [])
            if str(action).strip()
        ]
        _append_unique_string(suggested_actions, first_suggested_action)
        if acknowledge_command != "none":
            _append_unique_string(suggested_actions, f"[runtime] acknowledge interrupt: {acknowledge_command}")
        _append_unique_string(suggested_actions, f"[runtime] repair command: {first_repair_command}")
        summary_payload["suggested_actions"] = suggested_actions
        summary_payload["suggested_action_count"] = len(suggested_actions)
        summary_payload["first_suggested_action"] = suggested_actions[0] if suggested_actions else "none"

        summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")

    payload["matrix_runtime_alert_path"] = str(alert_path)
    payload["matrix_runtime_interrupt_id"] = interrupt_id or "none"
    payload["matrix_runtime_alert_created"] = 1 if interrupt_id else 0
    payload["matrix_runtime_acknowledge_command"] = acknowledge_command
    payload["matrix_runtime_first_repair_command"] = first_repair_command
    payload["matrix_runtime_error"] = runtime_error

    if bool(getattr(args, "emit_github_output", False)):
        output_lines = [
            f"matrix_runtime_alert_path={alert_path}",
            f"matrix_runtime_interrupt_id={interrupt_id or 'none'}",
            f"matrix_runtime_alert_created={1 if interrupt_id else 0}",
            f"matrix_runtime_acknowledge_command={acknowledge_command}",
            f"matrix_runtime_first_repair_command={first_repair_command}",
            f"matrix_runtime_error={runtime_error}",
        ]
        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_output = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- interrupt_id: `{interrupt_id or 'none'}`",
                    f"- alert_created: `{1 if interrupt_id else 0}`",
                    f"- daily_outcome: `{daily_outcome}`",
                    f"- matrix_status: `{matrix_status}`",
                    f"- drift_severity: `{drift_severity}`",
                    f"- acknowledge_command: `{acknowledge_command}`",
                    f"- first_repair_command: `{first_repair_command}`",
                    f"- runtime_error: `{runtime_error}`",
                    "",
                ]
                with summary_output.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)):
        if runtime_error != "none" or not bool(interrupt_id):
            raise SystemExit(2)


def cmd_improvement_verify_matrix_compact(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_report_file:expected_json_object")

    verify_matrix = dict(loaded.get("verify_matrix") or {})
    verify_matrix_alert = dict(loaded.get("verify_matrix_alert") or {})
    promotion_lock = dict(loaded.get("promotion_lock") or {})
    summary = dict(verify_matrix.get("summary") or {})
    comparisons = [
        row
        for row in list(verify_matrix.get("comparisons") or [])
        if isinstance(row, dict)
    ]

    verify_matrix_status = str(verify_matrix.get("status") or "unknown").strip().lower() or "unknown"
    drift_severity = str(
        verify_matrix.get("drift_severity")
        or verify_matrix_alert.get("drift_severity")
        or "unknown"
    ).strip().lower() or "unknown"
    mismatch_count = _coerce_int(summary.get("mismatch_count"), default=0)
    missing_count = _coerce_int(summary.get("missing_count"), default=0)
    invalid_count = _coerce_int(summary.get("invalid_count"), default=0)

    required_domains = [
        "quant_finance",
        "kalshi_weather",
        "fitness_apps",
        "market_ml",
    ]
    status_rank = {
        "mismatch": 5,
        "missing_run": 4,
        "invalid_scenario": 4,
        "matched": 2,
    }
    domain_statuses: dict[str, str] = {}
    for domain in required_domains:
        rows = [
            row
            for row in comparisons
            if str(row.get("domain") or "").strip().lower() == domain
        ]
        if not rows:
            domain_statuses[domain] = "missing_domain"
            continue
        ranked = sorted(
            rows,
            key=lambda row: status_rank.get(str(row.get("status") or "").strip().lower(), 1),
            reverse=True,
        )
        domain_statuses[domain] = str(ranked[0].get("status") or "unknown").strip().lower() or "unknown"

    missing_domains = [
        domain
        for domain in required_domains
        if str(domain_statuses.get(domain) or "") == "missing_domain"
    ]
    required_domain_missing_count = len(missing_domains)
    missing_domain_count = required_domain_missing_count
    required_domain_count = len(required_domains)
    covered_domain_count = required_domain_count - missing_domain_count
    missing_domains_csv = ",".join(missing_domains) if missing_domains else "none"
    first_missing_domain = missing_domains[0] if missing_domains else "none"

    acknowledge_commands: list[str] = []
    for command in list(promotion_lock.get("acknowledge_commands") or []):
        _append_unique_string(acknowledge_commands, command)
    for command in list(verify_matrix_alert.get("acknowledge_commands") or []):
        _append_unique_string(acknowledge_commands, command)
    acknowledge_command_count = len(acknowledge_commands)
    first_acknowledge_command = acknowledge_commands[0] if acknowledge_commands else "none"

    recheck_command = str(promotion_lock.get("recheck_command") or "").strip()
    if not recheck_command:
        recheck_command = (
            "./scripts/run_improvement_verify_matrix_alert.sh "
            "./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json "
            "output/ci/operator_cycle/daily_pipeline_report.json "
            "--output-path output/ci/operator_cycle/verify_matrix_alert_report.json --json-compact"
        )
    unlock_ready_commands = [recheck_command] if recheck_command and recheck_command != "none" else []
    first_unlock_ready_command = unlock_ready_commands[0] if unlock_ready_commands else "none"

    repair_commands = list(acknowledge_commands)
    if recheck_command and recheck_command != "none":
        _append_unique_string(repair_commands, recheck_command)
    repair_command_count = len(repair_commands)
    first_repair_command = repair_commands[0] if repair_commands else "none"
    repair_command_sequence = " && ".join(repair_commands) if repair_commands else "none"

    alert_payload = dict(verify_matrix_alert.get("alert") or {})
    top_scenarios = [
        str(item).strip()
        for item in list(alert_payload.get("top_scenarios") or [])
        if str(item).strip()
    ]
    if not top_scenarios:
        for row in comparisons:
            status = str(row.get("status") or "").strip().lower()
            if status in {"mismatch", "missing_run", "invalid_scenario"}:
                scenario_id = str(row.get("scenario_id") or "").strip()
                if scenario_id:
                    _append_unique_string(top_scenarios, scenario_id)
            if len(top_scenarios) >= 4:
                break
    top_scenario_count = len(top_scenarios)
    first_top_scenario = top_scenarios[0] if top_scenarios else "none"

    suggested_actions: list[str] = []
    if missing_domain_count > 0:
        if recheck_command and recheck_command != "none":
            suggested_actions.append(f"restore required domain coverage via recheck: {recheck_command}")
        else:
            suggested_actions.append("restore required domain coverage by rerunning verify-matrix route.")
    if acknowledge_commands:
        suggested_actions.append(f"acknowledge active verify-matrix interrupts: {acknowledge_commands[0]}")
    if top_scenario_count > 0 and first_top_scenario != "none":
        suggested_actions.append(f"investigate top verify-matrix scenario: {first_top_scenario}")
    if not suggested_actions:
        suggested_actions.append("No verify-matrix coverage action required; continue operator-cycle cadence.")
    suggested_action_count = len(suggested_actions)
    first_suggested_action = suggested_actions[0] if suggested_actions else "none"

    operator_ack_bundle = {
        "status": "ready" if repair_commands else "empty",
        "command_count": repair_command_count,
        "commands": repair_commands,
        "command_sequence": repair_command_sequence,
        "first_command": None if first_repair_command == "none" else first_repair_command,
    }

    compact_status = (
        "ok"
        if verify_matrix_status == "ok" and required_domain_missing_count == 0
        else "warning"
    )

    compact_path = (
        args.output_path.resolve()
        if args.output_path is not None
        else (report_path.parent / "verify_matrix_compact.json").resolve()
    )
    compact_markdown_path = (
        args.markdown_path.resolve()
        if args.markdown_path is not None
        else (compact_path.parent / "verify_matrix_compact.md").resolve()
    )
    compact_path.parent.mkdir(parents=True, exist_ok=True)
    compact_markdown_path.parent.mkdir(parents=True, exist_ok=True)

    compact_payload = {
        "generated_at": utc_now_iso(),
        "status": compact_status,
        "verify_matrix_status": verify_matrix_status,
        "drift_severity": drift_severity,
        "mismatch_count": mismatch_count,
        "missing_count": missing_count,
        "invalid_count": invalid_count,
        "required_domains": required_domains,
        "required_domain_count": required_domain_count,
        "covered_domain_count": covered_domain_count,
        "domain_statuses": domain_statuses,
        "missing_domains": missing_domains,
        "missing_domains_csv": missing_domains_csv,
        "missing_domain_count": missing_domain_count,
        "required_domain_missing_count": required_domain_missing_count,
        "first_missing_domain": first_missing_domain,
        "acknowledge_commands": acknowledge_commands,
        "acknowledge_command_count": acknowledge_command_count,
        "first_acknowledge_command": first_acknowledge_command,
        "recheck_command": recheck_command,
        "unlock_ready_commands": unlock_ready_commands,
        "first_unlock_ready_command": first_unlock_ready_command,
        "repair_commands": repair_commands,
        "repair_command_count": repair_command_count,
        "first_repair_command": first_repair_command,
        "repair_command_sequence": repair_command_sequence,
        "operator_ack_bundle": operator_ack_bundle,
        "suggested_actions": suggested_actions,
        "suggested_action_count": suggested_action_count,
        "first_suggested_action": first_suggested_action,
        "top_scenarios": top_scenarios,
        "top_scenario_count": top_scenario_count,
        "first_top_scenario": first_top_scenario,
        "verify_matrix_report_path": str(loaded.get("verify_matrix_report_path") or ""),
        "verify_matrix_alert_report_path": str(loaded.get("verify_matrix_alert_report_path") or ""),
        "operator_cycle_report_path": str(report_path),
        "verify_matrix_compact_path": str(compact_path),
        "verify_matrix_compact_markdown_path": str(compact_markdown_path),
    }
    compact_path.write_text(json.dumps(compact_payload, indent=2), encoding="utf-8")

    markdown_lines = [
        "# Verify Matrix Compact Coverage",
        "",
        f"- status: `{compact_status}`",
        f"- verify_matrix_status: `{verify_matrix_status}`",
        f"- drift_severity: `{drift_severity}`",
        f"- mismatch_count: `{mismatch_count}`",
        f"- missing_count: `{missing_count}`",
        f"- invalid_count: `{invalid_count}`",
        f"- required_domain_missing_count: `{required_domain_missing_count}`",
        f"- missing_domain_count: `{missing_domain_count}`",
        f"- missing_domains_csv: `{missing_domains_csv}`",
        f"- first_missing_domain: `{first_missing_domain}`",
        f"- acknowledge_command_count: `{acknowledge_command_count}`",
        f"- first_acknowledge_command: `{first_acknowledge_command}`",
        f"- recheck_command: `{recheck_command}`",
        f"- first_unlock_ready_command: `{first_unlock_ready_command}`",
        f"- repair_command_count: `{repair_command_count}`",
        f"- first_repair_command: `{first_repair_command}`",
        f"- suggested_action_count: `{suggested_action_count}`",
        f"- first_suggested_action: `{first_suggested_action}`",
        f"- top_scenario_count: `{top_scenario_count}`",
        f"- first_top_scenario: `{first_top_scenario}`",
        "",
        "## Domain Statuses",
        "",
    ]
    for domain in required_domains:
        markdown_lines.append(f"- `{domain}`: `{domain_statuses.get(domain)}`")
    markdown_lines.extend(["", "## Suggested Actions", ""])
    for action in suggested_actions:
        markdown_lines.append(f"- {action}")
    compact_markdown_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8")

    if bool(getattr(args, "emit_github_output", False)):
        domain_statuses = dict(compact_payload.get("domain_statuses") or {})
        operator_ack_bundle = dict(compact_payload.get("operator_ack_bundle") or {})
        suggested_actions = [
            str(item).strip()
            for item in list(compact_payload.get("suggested_actions") or [])
            if str(item).strip()
        ]
        top_scenarios = [
            str(item).strip()
            for item in list(compact_payload.get("top_scenarios") or [])
            if str(item).strip()
        ]
        acknowledge_commands = [
            str(item).strip()
            for item in list(compact_payload.get("acknowledge_commands") or [])
            if str(item).strip()
        ]
        unlock_ready_commands = [
            str(item).strip()
            for item in list(compact_payload.get("unlock_ready_commands") or [])
            if str(item).strip()
        ]
        repair_commands = [
            str(item).strip()
            for item in list(compact_payload.get("repair_commands") or [])
            if str(item).strip()
        ]

        compact_status_out = str(compact_payload.get("status") or "unknown").strip().lower() or "unknown"
        verify_matrix_status_out = (
            str(compact_payload.get("verify_matrix_status") or "unknown").strip().lower() or "unknown"
        )
        drift_severity_out = str(compact_payload.get("drift_severity") or "unknown").strip().lower() or "unknown"
        required_domain_count_out = _coerce_int(compact_payload.get("required_domain_count"), default=0)
        covered_domain_count_out = _coerce_int(compact_payload.get("covered_domain_count"), default=0)
        missing_domain_count_out = _coerce_int(compact_payload.get("missing_domain_count"), default=0)
        missing_domains_csv_out = str(compact_payload.get("missing_domains_csv") or "none").strip() or "none"
        required_domain_missing_count_out = _coerce_int(
            compact_payload.get("required_domain_missing_count"),
            default=missing_domain_count_out,
        )
        first_missing_domain_out = str(compact_payload.get("first_missing_domain") or "none").strip() or "none"

        acknowledge_command_count_out = _coerce_int(
            compact_payload.get("acknowledge_command_count"),
            default=len(acknowledge_commands),
        )
        first_acknowledge_command_out = str(compact_payload.get("first_acknowledge_command") or "").strip()
        if not first_acknowledge_command_out:
            first_acknowledge_command_out = acknowledge_commands[0] if acknowledge_commands else "none"

        recheck_command_out = str(compact_payload.get("recheck_command") or "").strip()
        if not recheck_command_out:
            recheck_command_out = unlock_ready_commands[0] if unlock_ready_commands else "none"

        first_unlock_ready_command_out = str(compact_payload.get("first_unlock_ready_command") or "").strip()
        if not first_unlock_ready_command_out:
            first_unlock_ready_command_out = unlock_ready_commands[0] if unlock_ready_commands else "none"

        repair_command_count_out = _coerce_int(
            compact_payload.get("repair_command_count"),
            default=len(repair_commands),
        )
        first_repair_command_out = str(compact_payload.get("first_repair_command") or "").strip()
        if not first_repair_command_out:
            first_repair_command_out = repair_commands[0] if repair_commands else "none"

        repair_command_sequence_out = (
            str(
                compact_payload.get("repair_command_sequence")
                or operator_ack_bundle.get("command_sequence")
                or "none"
            ).strip()
            or "none"
        )

        suggested_action_count_out = _coerce_int(
            compact_payload.get("suggested_action_count"),
            default=len(suggested_actions),
        )
        first_suggested_action_out = str(compact_payload.get("first_suggested_action") or "").strip()
        if not first_suggested_action_out:
            first_suggested_action_out = suggested_actions[0] if suggested_actions else "none"

        top_scenario_count_out = _coerce_int(
            compact_payload.get("top_scenario_count"),
            default=len(top_scenarios),
        )
        first_top_scenario_out = str(compact_payload.get("first_top_scenario") or "").strip()
        if not first_top_scenario_out:
            first_top_scenario_out = top_scenarios[0] if top_scenarios else "none"

        compact_markdown_path_from_payload = Path(
            str(compact_payload.get("verify_matrix_compact_markdown_path") or compact_markdown_path)
        ).expanduser().resolve()
        compact_path_from_payload = Path(
            str(compact_payload.get("verify_matrix_compact_path") or compact_path)
        ).expanduser().resolve()

        output_lines = [
            f"verify_matrix_compact_path={compact_path_from_payload}",
            f"verify_matrix_compact_markdown_path={compact_markdown_path_from_payload}",
            f"verify_matrix_compact_status={compact_status_out}",
            f"verify_matrix_status={verify_matrix_status_out}",
            f"verify_matrix_drift_severity={drift_severity_out}",
            f"drift_severity={drift_severity_out}",
            f"required_domain_count={required_domain_count_out}",
            f"covered_domain_count={covered_domain_count_out}",
            f"missing_domain_count={missing_domain_count_out}",
            f"missing_domains_csv={missing_domains_csv_out}",
            f"verify_matrix_required_domain_missing_count={required_domain_missing_count_out}",
            f"verify_matrix_first_missing_domain={first_missing_domain_out}",
            f"acknowledge_command_count={acknowledge_command_count_out}",
            f"first_acknowledge_command={first_acknowledge_command_out}",
            f"verify_matrix_recheck_command={recheck_command_out}",
            f"verify_matrix_first_unlock_ready_command={first_unlock_ready_command_out}",
            f"first_unlock_ready_command={first_unlock_ready_command_out}",
            f"recheck_command={recheck_command_out}",
            f"repair_command_count={repair_command_count_out}",
            f"first_repair_command={first_repair_command_out}",
            f"operator_ack_bundle_command_sequence={repair_command_sequence_out}",
            f"suggested_action_count={suggested_action_count_out}",
            f"first_suggested_action={first_suggested_action_out}",
            f"top_scenario_count={top_scenario_count_out}",
            f"first_top_scenario={first_top_scenario_out}",
        ]

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- status: `{compact_status_out}`",
                    f"- verify_matrix_status: `{verify_matrix_status_out}`",
                    f"- drift_severity: `{drift_severity_out}`",
                    f"- missing_domain_count: `{missing_domain_count_out}`",
                    f"- missing_domains_csv: `{missing_domains_csv_out}`",
                    f"- first_unlock_ready_command: `{first_unlock_ready_command_out}`",
                    "",
                ]
                if bool(getattr(args, "summary_include_markdown", False)):
                    if compact_markdown_path_from_payload.exists():
                        markdown_text = compact_markdown_path_from_payload.read_text(encoding="utf-8").strip()
                        if markdown_text:
                            summary_lines.append(markdown_text)
                            summary_lines.append("")
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        compact_payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if compact_status != "ok" and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def cmd_improvement_verify_matrix_coverage_alert(args: argparse.Namespace) -> None:
    compact_path = args.compact_path.resolve()
    compact_payload: dict[str, Any] = {}
    if compact_path.exists():
        loaded = json.loads(compact_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            compact_payload = dict(loaded)
        else:
            raise ValueError("invalid_verify_matrix_compact_file:expected_json_object")

    missing_domain_count_override = getattr(args, "missing_domain_count", None)
    missing_domain_count = (
        _coerce_int(missing_domain_count_override, default=0)
        if missing_domain_count_override is not None
        else _coerce_int(compact_payload.get("missing_domain_count"), default=0)
    )
    missing_domains_csv = (
        str(getattr(args, "missing_domains_csv", None)).strip()
        if getattr(args, "missing_domains_csv", None) is not None
        else str(compact_payload.get("missing_domains_csv") or "").strip()
    ) or "none"
    first_missing_domain = (
        str(getattr(args, "first_missing_domain", None)).strip()
        if getattr(args, "first_missing_domain", None) is not None
        else str(compact_payload.get("first_missing_domain") or "").strip()
    ) or "none"
    recheck_command = (
        str(getattr(args, "recheck_command", None)).strip()
        if getattr(args, "recheck_command", None) is not None
        else ""
    )
    if not recheck_command:
        recheck_command = (
            str(getattr(args, "first_unlock_ready_command", None)).strip()
            if getattr(args, "first_unlock_ready_command", None) is not None
            else ""
        )
    if not recheck_command:
        recheck_command = str(compact_payload.get("recheck_command") or "").strip()
    if not recheck_command:
        recheck_command = str(compact_payload.get("first_unlock_ready_command") or "").strip()
    if not recheck_command:
        recheck_command = "none"

    compact_status = (
        str(getattr(args, "compact_status", None)).strip().lower()
        if getattr(args, "compact_status", None) is not None
        else str(compact_payload.get("status") or "").strip().lower()
    ) or "warning"

    alert_path = (
        args.output_path.resolve()
        if args.output_path is not None
        else (compact_path.parent / "verify_matrix_coverage_alert.json").resolve()
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (
        args.db_path.resolve()
        if getattr(args, "db_path", None) is not None
        else (alert_path.parent / "jarvis.db").resolve()
    )

    reason = (
        "verify_matrix_required_domain_coverage_gap"
        + f" missing_domain_count={missing_domain_count}"
        + f" missing_domains={missing_domains_csv}"
        + f" compact_status={compact_status}"
    )
    why_now = (
        "required domain coverage in controlled verify-matrix is incomplete and blocks "
        "reliable cross-domain promotion decisions."
    )
    why_not_later = (
        "delayed remediation of missing domain coverage can hide blind spots across quant, "
        "Kalshi weather, fitness, and market-ml loops."
    )

    interrupt_id = ""
    acknowledge_command = "none"
    runtime_error = "none"
    runtime = None
    try:
        if missing_domain_count > 0:
            runtime = JarvisRuntime(
                db_path=db_path,
                repo_path=args.repo_path.resolve(),
            )
            urgency_score = 0.96 if missing_domain_count >= 2 else 0.92
            confidence = 0.93 if missing_domain_count >= 2 else 0.9
            decision = InterruptDecision(
                interrupt_id=new_id("int"),
                candidate_id=new_id("cand"),
                domain="operations",
                reason=reason,
                urgency_score=urgency_score,
                confidence=confidence,
                suppression_window_hit=False,
                delivered=True,
                why_now=why_now,
                why_not_later=why_not_later,
                status="delivered",
            )
            runtime.interrupt_store.store(decision)
            interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
            interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
            if interrupt_id:
                acknowledge_command = (
                    "python3 -m jarvis.cli interrupts acknowledge "
                    f"{interrupt_id} --actor operator --db-path {db_path}"
                )
            runtime.memory.append_event(
                "improvement.verify_matrix_coverage_alert_created",
                {
                    "interrupt_id": interrupt_id or None,
                    "compact_path": str(compact_path),
                    "compact_status": compact_status,
                    "missing_domain_count": missing_domain_count,
                    "missing_domains_csv": missing_domains_csv,
                    "first_missing_domain": first_missing_domain,
                    "recheck_command": recheck_command,
                },
            )
    except Exception as exc:
        runtime_error = str(exc).strip() or "unknown_runtime_error"
    finally:
        if runtime is not None:
            runtime.close()

    status = "warning" if missing_domain_count > 0 else "ok"
    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": status,
        "compact_path": str(compact_path),
        "compact_status": compact_status,
        "missing_domain_count": missing_domain_count,
        "missing_domains_csv": missing_domains_csv,
        "first_missing_domain": first_missing_domain,
        "alert_created": bool(interrupt_id),
        "interrupt_id": interrupt_id or None,
        "interrupt_db_path": str(db_path),
        "acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
        "recheck_command": None if recheck_command == "none" else recheck_command,
        "first_unlock_ready_command": None if recheck_command == "none" else recheck_command,
        "first_repair_command": None,
        "reason": reason,
        "why_now": why_now,
        "why_not_later": why_not_later,
        "runtime_error": None if runtime_error == "none" else runtime_error,
    }

    first_repair_command = "none"
    if compact_payload:
        acknowledge_commands = [
            str(command).strip()
            for command in list(compact_payload.get("acknowledge_commands") or [])
            if str(command).strip()
        ]
        if acknowledge_command != "none" and acknowledge_command not in acknowledge_commands:
            acknowledge_commands.append(acknowledge_command)

        repair_commands = [
            str(command).strip()
            for command in list(compact_payload.get("repair_commands") or [])
            if str(command).strip()
        ]
        if not repair_commands:
            repair_commands = list(acknowledge_commands)
            if recheck_command != "none":
                repair_commands.append(recheck_command)
        else:
            if acknowledge_command != "none" and acknowledge_command not in repair_commands:
                repair_commands.append(acknowledge_command)
            if recheck_command != "none" and recheck_command not in repair_commands:
                repair_commands.append(recheck_command)
        repair_command_count = len(repair_commands)
        first_repair_command = repair_commands[0] if repair_commands else "none"
        repair_command_sequence = " && ".join(repair_commands) if repair_commands else "none"

        suggested_actions = [
            str(action).strip()
            for action in list(compact_payload.get("suggested_actions") or [])
            if str(action).strip()
        ]
        coverage_ack_action = (
            f"[coverage] acknowledge interrupt: {acknowledge_command}"
            if acknowledge_command != "none"
            else ""
        )
        coverage_recheck_action = (
            f"[coverage] rerun verify-matrix recheck: {recheck_command}"
            if recheck_command != "none"
            else ""
        )
        if coverage_ack_action and coverage_ack_action not in suggested_actions:
            suggested_actions.append(coverage_ack_action)
        if coverage_recheck_action and coverage_recheck_action not in suggested_actions:
            suggested_actions.append(coverage_recheck_action)
        suggested_action_count = len(suggested_actions)
        first_suggested_action = suggested_actions[0] if suggested_actions else "none"

        compact_payload["acknowledge_commands"] = acknowledge_commands
        compact_payload["acknowledge_command_count"] = len(acknowledge_commands)
        compact_payload["first_acknowledge_command"] = (
            acknowledge_commands[0] if acknowledge_commands else "none"
        )
        compact_payload["unlock_ready_commands"] = [recheck_command] if recheck_command != "none" else []
        compact_payload["first_unlock_ready_command"] = (
            recheck_command if recheck_command != "none" else "none"
        )
        compact_payload["repair_commands"] = repair_commands
        compact_payload["repair_command_count"] = repair_command_count
        compact_payload["first_repair_command"] = first_repair_command
        compact_payload["repair_command_sequence"] = repair_command_sequence
        compact_payload["operator_ack_bundle"] = {
            "status": "ready" if repair_commands else "empty",
            "command_count": repair_command_count,
            "commands": repair_commands,
            "command_sequence": repair_command_sequence,
            "first_command": None if first_repair_command == "none" else first_repair_command,
        }
        compact_payload["suggested_actions"] = suggested_actions
        compact_payload["suggested_action_count"] = suggested_action_count
        compact_payload["first_suggested_action"] = first_suggested_action
        compact_payload["coverage_alert_path"] = str(alert_path)
        compact_payload["coverage_interrupt_id"] = interrupt_id or None
        compact_payload["coverage_acknowledge_command"] = (
            None if acknowledge_command == "none" else acknowledge_command
        )
        compact_payload["coverage_alert_created"] = bool(interrupt_id)
        compact_payload["coverage_runtime_error"] = (
            None if runtime_error == "none" else runtime_error
        )
        compact_path.write_text(json.dumps(compact_payload, indent=2), encoding="utf-8")
    else:
        first_repair_command = acknowledge_command if acknowledge_command != "none" else recheck_command
    payload["first_repair_command"] = (
        None if first_repair_command in {"", "none"} else first_repair_command
    )

    alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["verify_matrix_coverage_alert_path"] = str(alert_path)
    payload["verify_matrix_coverage_interrupt_id"] = interrupt_id or "none"
    payload["verify_matrix_coverage_alert_created"] = 1 if interrupt_id else 0
    payload["verify_matrix_coverage_acknowledge_command"] = acknowledge_command
    payload["verify_matrix_coverage_recheck_command"] = recheck_command
    payload["verify_matrix_coverage_first_repair_command"] = (
        first_repair_command if first_repair_command else "none"
    )
    payload["verify_matrix_coverage_runtime_error"] = runtime_error

    if bool(getattr(args, "emit_github_output", False)):
        verify_matrix_coverage_alert_path = (
            str(payload.get("verify_matrix_coverage_alert_path") or "").strip() or str(alert_path)
        )
        verify_matrix_coverage_interrupt_id = (
            str(payload.get("verify_matrix_coverage_interrupt_id") or payload.get("interrupt_id") or "none")
            .strip()
            or "none"
        )
        verify_matrix_coverage_alert_created = _coerce_int(
            payload.get("verify_matrix_coverage_alert_created")
            if payload.get("verify_matrix_coverage_alert_created") is not None
            else payload.get("alert_created"),
            default=0,
        )
        verify_matrix_coverage_acknowledge_command = (
            str(payload.get("verify_matrix_coverage_acknowledge_command") or payload.get("acknowledge_command") or "none")
            .strip()
            or "none"
        )
        verify_matrix_coverage_recheck_command = (
            str(payload.get("verify_matrix_coverage_recheck_command") or payload.get("recheck_command") or "none")
            .strip()
            or "none"
        )
        verify_matrix_coverage_first_repair_command = (
            str(
                payload.get("verify_matrix_coverage_first_repair_command")
                or payload.get("first_repair_command")
                or ""
            ).strip()
        )
        if not verify_matrix_coverage_first_repair_command:
            verify_matrix_coverage_first_repair_command = (
                verify_matrix_coverage_acknowledge_command
                if verify_matrix_coverage_acknowledge_command != "none"
                else verify_matrix_coverage_recheck_command
            )
        verify_matrix_coverage_first_repair_command = (
            verify_matrix_coverage_first_repair_command or "none"
        )
        verify_matrix_coverage_runtime_error = (
            str(payload.get("verify_matrix_coverage_runtime_error") or payload.get("runtime_error") or "none")
            .strip()
            or "none"
        )
        missing_domain_count_out = _coerce_int(payload.get("missing_domain_count"), default=0)
        first_missing_domain_out = str(payload.get("first_missing_domain") or "none").strip() or "none"
        missing_domains_csv_out = str(payload.get("missing_domains_csv") or "none").strip() or "none"

        output_lines = [
            f"verify_matrix_coverage_alert_path={verify_matrix_coverage_alert_path}",
            f"verify_matrix_coverage_interrupt_id={verify_matrix_coverage_interrupt_id}",
            f"verify_matrix_coverage_alert_created={verify_matrix_coverage_alert_created}",
            f"verify_matrix_coverage_acknowledge_command={verify_matrix_coverage_acknowledge_command}",
            f"verify_matrix_coverage_recheck_command={verify_matrix_coverage_recheck_command}",
            f"verify_matrix_coverage_first_repair_command={verify_matrix_coverage_first_repair_command}",
            f"verify_matrix_coverage_runtime_error={verify_matrix_coverage_runtime_error}",
        ]

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- interrupt_id: `{verify_matrix_coverage_interrupt_id}`",
                    f"- alert_created: `{verify_matrix_coverage_alert_created}`",
                    f"- missing_domain_count: `{missing_domain_count_out}`",
                    f"- first_missing_domain: `{first_missing_domain_out}`",
                    f"- missing_domains_csv: `{missing_domains_csv_out}`",
                    f"- acknowledge_command: `{verify_matrix_coverage_acknowledge_command}`",
                    f"- recheck_command: `{verify_matrix_coverage_recheck_command}`",
                    f"- first_repair_command: `{verify_matrix_coverage_first_repair_command}`",
                    f"- runtime_error: `{verify_matrix_coverage_runtime_error}`",
                    "",
                ]
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    strict_requested = bool(getattr(args, "strict", False))
    if strict_requested and missing_domain_count > 0:
        if runtime_error != "none" or not bool(interrupt_id):
            raise SystemExit(2)


def cmd_improvement_verify_matrix_guardrail_gate(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    if not report_path.exists():
        raise SystemExit(f"missing_operator_report:{report_path}")

    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_report_file:expected_json_object")

    stage_error_count = _coerce_int(loaded.get("stage_error_count"), default=0)
    verify_matrix = dict(loaded.get("verify_matrix") or {})
    verify_matrix_status = str(verify_matrix.get("status") or "unknown").strip().lower() or "unknown"
    operator_status = str(loaded.get("status") or "unknown").strip().lower() or "unknown"

    failure_reason = "none"
    if stage_error_count > 0:
        failure_reason = (
            "operator_guardrail_gate_failed:stage_error_count>0 "
            f"(count={stage_error_count})"
        )
    elif verify_matrix_status != "ok":
        failure_reason = (
            "operator_guardrail_gate_failed:verify_matrix_status_not_ok "
            f"(status={verify_matrix_status})"
        )

    status = "ok" if failure_reason == "none" else "warning"
    payload: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": status,
        "report_path": str(report_path),
        "operator_status": operator_status,
        "stage_error_count": int(stage_error_count),
        "verify_matrix_status": verify_matrix_status,
        "failure_reason": failure_reason,
        "guardrail_gate_report": str(report_path),
        "guardrail_gate_operator_status": operator_status,
        "guardrail_gate_stage_error_count": int(stage_error_count),
        "guardrail_gate_verify_matrix_status": verify_matrix_status,
    }

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    if bool(getattr(args, "emit_github_output", False)):
        guardrail_gate_report = str(payload.get("guardrail_gate_report") or payload.get("report_path") or "").strip()
        guardrail_gate_report = guardrail_gate_report or str(report_path)
        guardrail_gate_operator_status = (
            str(payload.get("guardrail_gate_operator_status") or payload.get("operator_status") or "unknown")
            .strip()
            .lower()
            or "unknown"
        )
        guardrail_gate_stage_error_count = _coerce_int(
            payload.get("guardrail_gate_stage_error_count")
            if payload.get("guardrail_gate_stage_error_count") is not None
            else payload.get("stage_error_count"),
            default=0,
        )
        guardrail_gate_verify_matrix_status = (
            str(
                payload.get("guardrail_gate_verify_matrix_status")
                if payload.get("guardrail_gate_verify_matrix_status") is not None
                else payload.get("verify_matrix_status")
                or "unknown"
            )
            .strip()
            .lower()
            or "unknown"
        )
        output_lines = [
            f"guardrail_gate_report={guardrail_gate_report}",
            f"guardrail_gate_operator_status={guardrail_gate_operator_status}",
            f"guardrail_gate_stage_error_count={guardrail_gate_stage_error_count}",
            f"guardrail_gate_verify_matrix_status={guardrail_gate_verify_matrix_status}",
        ]

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- operator_status: `{guardrail_gate_operator_status}`",
                    f"- stage_error_count: `{guardrail_gate_stage_error_count}`",
                    f"- verify_matrix_status: `{guardrail_gate_verify_matrix_status}`",
                    "",
                ]
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )

    if bool(getattr(args, "strict", False)) and failure_reason != "none":
        raise SystemExit(failure_reason)


def _resolve_daily_report_from_improvement_report(
    *,
    report_path: Path,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_report_file:expected_json_object")

    if isinstance(loaded.get("experiment_runs"), list):
        return loaded, report_path, loaded

    daily_report_path_raw = loaded.get("daily_report_path")
    if daily_report_path_raw is None:
        raise ValueError("invalid_report_file:missing_experiment_runs_or_daily_report_path")
    daily_report_path = Path(str(daily_report_path_raw)).expanduser()
    if not daily_report_path.is_absolute():
        daily_report_path = (report_path.parent / daily_report_path).resolve()
    else:
        daily_report_path = daily_report_path.resolve()
    if not daily_report_path.exists():
        raise FileNotFoundError(str(daily_report_path))
    daily_loaded = json.loads(daily_report_path.read_text(encoding="utf-8"))
    if not isinstance(daily_loaded, dict):
        raise ValueError("invalid_daily_report:expected_json_object")
    return daily_loaded, daily_report_path, loaded


def _collect_recurring_pain_rows(operator_payload: dict[str, Any]) -> list[dict[str, Any]]:
    seed_domain_runs = [row for row in list(operator_payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
    if not seed_domain_runs:
        fallback_leaderboard = dict(operator_payload.get("fitness_leaderboard") or {})
        if isinstance(fallback_leaderboard.get("leaderboard"), list):
            fallback_domain = str(fallback_leaderboard.get("domain") or "").strip().lower() or "unknown"
            seed_domain_runs = [
                {
                    "domain": fallback_domain,
                    "fitness_leaderboard": fallback_leaderboard,
                }
            ]

    rows_by_key: dict[str, dict[str, Any]] = {}
    for run in seed_domain_runs:
        leaderboard_payload = dict(run.get("fitness_leaderboard") or {})
        leaderboard_rows = [row for row in list(leaderboard_payload.get("leaderboard") or []) if isinstance(row, dict)]
        seed_payload = dict(run.get("seed_from_leaderboard") or {})
        linked_hypothesis_ids_by_friction: dict[str, list[str]] = {}
        for seed_row in [*list(seed_payload.get("created") or []), *list(seed_payload.get("existing") or [])]:
            if not isinstance(seed_row, dict):
                continue
            linked_hypothesis_id = str(seed_row.get("hypothesis_id") or "").strip()
            if not linked_hypothesis_id:
                continue
            linked_friction_key = _normalize_friction_key(seed_row.get("friction_key"))
            if not linked_friction_key:
                linked_friction_key = _normalize_friction_key(seed_row.get("canonical_key"))
            if not linked_friction_key:
                continue
            ids = linked_hypothesis_ids_by_friction.setdefault(linked_friction_key, [])
            if linked_hypothesis_id not in ids:
                ids.append(linked_hypothesis_id)
        domain = (
            str(run.get("domain") or "").strip().lower()
            or str(leaderboard_payload.get("domain") or "").strip().lower()
            or "unknown"
        )
        for row in leaderboard_rows:
            friction_key = _normalize_friction_key(
                row.get("friction_key")
                if row.get("friction_key") is not None
                else row.get("canonical_key")
            )
            if not friction_key:
                continue
            canonical_key = str(row.get("canonical_key") or row.get("friction_key") or friction_key).strip()
            signal_count_current = max(0, _coerce_int(row.get("signal_count_current"), default=0))
            signal_count_previous = max(0, _coerce_int(row.get("signal_count_previous"), default=0))
            recurrence_score = signal_count_current + signal_count_previous
            impact_score_current = _coerce_float(row.get("impact_score_current"), default=0.0)
            impact_score_delta = _coerce_float(row.get("impact_score_delta"), default=0.0)
            trend = str(row.get("trend") or "").strip().lower() or "flat"
            trend_acceleration = max(0.0, impact_score_delta)
            if trend == "new" and trend_acceleration <= 0.0:
                trend_acceleration = max(0.0, impact_score_current)
            linked_hypothesis_ids = list(linked_hypothesis_ids_by_friction.get(friction_key) or [])
            key = f"{domain}:{friction_key}"
            existing = rows_by_key.get(key)
            if existing is None:
                rows_by_key[key] = {
                    "domain": domain,
                    "friction_key": friction_key,
                    "canonical_key": canonical_key,
                    "trend": trend,
                    "signal_count_current": signal_count_current,
                    "signal_count_previous": signal_count_previous,
                    "recurrence_score": recurrence_score,
                    "impact_score_current": round(float(impact_score_current), 4),
                    "impact_score_delta": round(float(impact_score_delta), 4),
                    "trend_acceleration": round(float(trend_acceleration), 4),
                    "source_count": 1,
                    "hypothesis_ids": linked_hypothesis_ids,
                }
                continue
            existing["signal_count_current"] = int(existing.get("signal_count_current") or 0) + signal_count_current
            existing["signal_count_previous"] = int(existing.get("signal_count_previous") or 0) + signal_count_previous
            existing["recurrence_score"] = int(existing.get("recurrence_score") or 0) + recurrence_score
            existing["impact_score_current"] = round(
                max(_coerce_float(existing.get("impact_score_current"), default=0.0), impact_score_current),
                4,
            )
            existing["impact_score_delta"] = round(
                max(_coerce_float(existing.get("impact_score_delta"), default=0.0), impact_score_delta),
                4,
            )
            existing["trend_acceleration"] = round(
                max(_coerce_float(existing.get("trend_acceleration"), default=0.0), trend_acceleration),
                4,
            )
            if str(existing.get("trend") or "") not in {"new", "rising"} and trend in {"new", "rising"}:
                existing["trend"] = trend
            existing["source_count"] = int(existing.get("source_count") or 0) + 1
            existing_hypothesis_ids = [str(item) for item in list(existing.get("hypothesis_ids") or []) if str(item)]
            for hypothesis_id in linked_hypothesis_ids:
                if hypothesis_id not in existing_hypothesis_ids:
                    existing_hypothesis_ids.append(hypothesis_id)
            existing["hypothesis_ids"] = existing_hypothesis_ids

    return list(rows_by_key.values())


def _collect_seed_hypothesis_context(operator_payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    seed_domain_runs = [row for row in list(operator_payload.get("seed_domain_runs") or []) if isinstance(row, dict)]
    if not seed_domain_runs:
        fallback_seed_payload = dict(operator_payload.get("seed_from_leaderboard") or {})
        fallback_fitness_payload = dict(operator_payload.get("fitness_leaderboard") or {})
        fallback_domain = (
            str(fallback_seed_payload.get("domain") or "").strip().lower()
            or str(fallback_fitness_payload.get("domain") or "").strip().lower()
        )
        seed_domain_runs = [
            {
                "domain": fallback_domain,
                "seed_from_leaderboard": fallback_seed_payload,
            }
        ]

    context_by_hypothesis_id: dict[str, dict[str, str]] = {}
    for run in seed_domain_runs:
        domain = str(run.get("domain") or "").strip().lower()
        seed_payload = dict(run.get("seed_from_leaderboard") or {})
        for seed_row in [*list(seed_payload.get("created") or []), *list(seed_payload.get("existing") or [])]:
            if not isinstance(seed_row, dict):
                continue
            hypothesis_id = str(seed_row.get("hypothesis_id") or "").strip()
            if not hypothesis_id:
                continue
            row_domain = str(seed_row.get("domain") or "").strip().lower() or domain
            friction_key = _normalize_friction_key(seed_row.get("friction_key"))
            if not friction_key:
                friction_key = _normalize_friction_key(seed_row.get("canonical_key"))
            if not row_domain or not friction_key:
                continue
            context_by_hypothesis_id[hypothesis_id] = {
                "domain": row_domain,
                "friction_key": friction_key,
            }
    return context_by_hypothesis_id


def _collect_implementation_outcomes(
    daily_report: dict[str, Any],
    *,
    hypothesis_context_by_id: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    runs = [row for row in list(daily_report.get("experiment_runs") or []) if isinstance(row, dict)]
    context_by_id = hypothesis_context_by_id or {}
    by_key: dict[str, dict[str, Any]] = {}
    for run in runs:
        resolution = dict(run.get("resolution") or {}) if isinstance(run.get("resolution"), dict) else {}
        selected = dict(resolution.get("selected") or {}) if isinstance(resolution.get("selected"), dict) else {}
        hypothesis_id = str(run.get("hypothesis_id") or "").strip()
        domain = (
            str(run.get("domain") or "").strip().lower()
            or str(resolution.get("domain") or "").strip().lower()
            or str(selected.get("domain") or "").strip().lower()
            or None
        )
        friction_key = _normalize_friction_key(
            run.get("friction_key")
            if run.get("friction_key") is not None
            else (
                resolution.get("friction_key")
                if resolution.get("friction_key") is not None
                else selected.get("friction_key")
            )
        )
        if (not domain or not friction_key) and hypothesis_id:
            context_row = dict(context_by_id.get(hypothesis_id) or {})
            if not domain:
                domain = str(context_row.get("domain") or "").strip().lower() or None
            if not friction_key:
                friction_key = _normalize_friction_key(
                    context_row.get("friction_key")
                    if context_row.get("friction_key") is not None
                    else context_row.get("canonical_key")
                )
        if not domain or not friction_key:
            continue
        key = f"{domain}:{friction_key}"
        entry = by_key.get(key)
        if entry is None:
            entry = {
                "domain": domain,
                "friction_key": friction_key,
                "run_count": 0,
                "promote_count": 0,
                "blocked_guardrail_count": 0,
                "insufficient_data_count": 0,
                "needs_iteration_count": 0,
                "invalid_measurement_count": 0,
                "other_verdict_count": 0,
                "hypothesis_ids": [],
            }
            by_key[key] = entry

        verdict = str(run.get("verdict") or "").strip().lower()
        entry["run_count"] = int(entry.get("run_count") or 0) + 1
        if verdict == "promote":
            entry["promote_count"] = int(entry.get("promote_count") or 0) + 1
        elif verdict == "blocked_guardrail":
            entry["blocked_guardrail_count"] = int(entry.get("blocked_guardrail_count") or 0) + 1
        elif verdict == "insufficient_data":
            entry["insufficient_data_count"] = int(entry.get("insufficient_data_count") or 0) + 1
        elif verdict == "needs_iteration":
            entry["needs_iteration_count"] = int(entry.get("needs_iteration_count") or 0) + 1
        elif verdict == "invalid_measurement":
            entry["invalid_measurement_count"] = int(entry.get("invalid_measurement_count") or 0) + 1
        else:
            entry["other_verdict_count"] = int(entry.get("other_verdict_count") or 0) + 1

        if hypothesis_id and hypothesis_id not in entry["hypothesis_ids"]:
            entry["hypothesis_ids"].append(hypothesis_id)

    rows: list[dict[str, Any]] = []
    for row in by_key.values():
        run_count = max(1, int(row.get("run_count") or 1))
        promote_count = int(row.get("promote_count") or 0)
        blocked_guardrail_count = int(row.get("blocked_guardrail_count") or 0)
        # Beta(1,1) prior keeps sparse run counts from looking falsely certain.
        adjusted_win_rate = (promote_count + 1.0) / (run_count + 2.0)
        confidence_score = min(1.0, run_count / 3.0)
        row["win_rate"] = round(promote_count / run_count, 4)
        row["adjusted_win_rate"] = round(float(adjusted_win_rate), 4)
        row["confidence_score"] = round(float(confidence_score), 4)
        row["guardrail_block_rate"] = round(blocked_guardrail_count / run_count, 4)
        rows.append(row)
    return rows


def _normalize_improvement_knowledge_domains(raw_value: Any) -> list[str]:
    requested = _parse_csv_items(str(raw_value or ""))
    if not requested:
        requested = _parse_csv_items(DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in requested:
        value = str(item or "").strip().lower()
        value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
        if value in {"none", "null"}:
            continue
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized or _parse_csv_items(DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV)


def _knowledge_text_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_knowledge_text_blob(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_knowledge_text_blob(item) for item in value)
    return str(value)


def _tokenize_knowledge_query(raw_query: Any) -> list[str]:
    text = str(raw_query or "").strip().lower()
    if not text:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[^a-z0-9]+", text):
        normalized = str(token or "").strip()
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def _knowledge_query_score(tokens: list[str], *values: Any) -> int:
    if not tokens:
        return 0
    haystack = " ".join(_knowledge_text_blob(value) for value in values).strip().lower()
    if not haystack:
        return 0
    return sum(1 for token in tokens if token in haystack)


def _filter_knowledge_rows_by_query(
    rows: list[dict[str, Any]],
    *,
    query_tokens: list[str],
    text_builder: Any,
) -> list[dict[str, Any]]:
    if not query_tokens:
        return [dict(row) for row in rows]
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        score = _knowledge_query_score(query_tokens, text_builder(row))
        if score <= 0:
            continue
        enriched = dict(row)
        enriched["query_score"] = int(score)
        scored.append((int(score), index, enriched))
    scored.sort(key=lambda item: (-int(item[0]), int(item[1])))
    return [item[2] for item in scored]


def _slugify_knowledge_snapshot_component(
    raw_value: Any,
    *,
    default: str,
    max_length: int = 28,
) -> str:
    value = str(raw_value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    if not value:
        value = default
    clipped = value[: max(1, int(max_length))]
    clipped = clipped.strip("_")
    return clipped or default


def _resolve_knowledge_snapshot_dir(
    *,
    repo_path: Path,
    snapshot_dir_value: Path | str | None,
) -> Path:
    if snapshot_dir_value is None:
        return (repo_path / "analysis" / "improvement" / "knowledge_snapshots").resolve()
    return _resolve_path_from_base(snapshot_dir_value, base_dir=repo_path).resolve()


def _build_knowledge_snapshot_metadata(
    *,
    generated_at: str,
    domains: list[str],
    query: str,
    snapshot_label: str | None,
) -> dict[str, str]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    domain_hint = _slugify_knowledge_snapshot_component(
        "-".join(domains[:2]) if domains else "",
        default="domains",
        max_length=24,
    )
    query_hint = _slugify_knowledge_snapshot_component(
        query,
        default="all_queries",
        max_length=24,
    )
    label_hint = _slugify_knowledge_snapshot_component(
        snapshot_label,
        default="",
        max_length=24,
    )
    fingerprint = hashlib.sha1(
        json.dumps(
            {
                "generated_at": generated_at,
                "domains": list(domains),
                "query": str(query or ""),
                "label": str(snapshot_label or ""),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:8]
    snapshot_id_parts = [timestamp, domain_hint, query_hint]
    if label_hint:
        snapshot_id_parts.append(label_hint)
    snapshot_id_parts.append(fingerprint)
    snapshot_id = "_".join(snapshot_id_parts)
    snapshot_file_name = f"knowledge_brief_{snapshot_id}.json"
    return {
        "snapshot_id": snapshot_id,
        "snapshot_file_name": snapshot_file_name,
    }


def _coerce_optional_snapshot_path(raw_value: Any, *, base_dir: Path) -> Path | None:
    if raw_value is None:
        return None
    raw_text = str(raw_value).strip()
    if not raw_text:
        return None
    return _resolve_path_from_base(raw_text, base_dir=base_dir).resolve()


def _load_knowledge_snapshot_payload(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"invalid_knowledge_snapshot_payload:{path}:expected_json_object")
    return dict(loaded)


def _load_knowledge_snapshot_index_rows(index_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not index_path.exists():
        return rows
    for line_number, raw_line in enumerate(index_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = str(raw_line or "").strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        path_value = str(row.get("path") or "").strip()
        if path_value:
            row = dict(row)
            row["path"] = str(Path(path_value).expanduser().resolve())
        row["_line_number"] = line_number
        rows.append(dict(row))
    return rows


def _resolve_knowledge_delta_snapshot_paths(
    *,
    snapshot_dir: Path,
    current_snapshot_path: Path | None,
    previous_snapshot_path: Path | None,
) -> tuple[Path, Path, dict[str, Any]]:
    snapshot_dir = snapshot_dir.resolve()
    latest_path = (snapshot_dir / "knowledge_brief_latest.json").resolve()
    index_path = (snapshot_dir / "knowledge_brief_index.jsonl").resolve()
    index_rows = _load_knowledge_snapshot_index_rows(index_path)

    candidate_paths: list[Path] = []
    seen_candidates: set[str] = set()
    for row in index_rows:
        candidate = str(row.get("path") or "").strip()
        if not candidate:
            continue
        resolved = Path(candidate).expanduser().resolve()
        key = str(resolved)
        if key in seen_candidates or not resolved.exists():
            continue
        seen_candidates.add(key)
        candidate_paths.append(resolved)
    for candidate in sorted(snapshot_dir.glob("knowledge_brief_*.json")):
        resolved = candidate.resolve()
        if resolved.name == "knowledge_brief_latest.json":
            continue
        key = str(resolved)
        if key in seen_candidates or not resolved.exists():
            continue
        seen_candidates.add(key)
        candidate_paths.append(resolved)

    current_source = "explicit"
    if current_snapshot_path is None:
        if latest_path.exists():
            current_snapshot_path = latest_path
            current_source = "latest_alias"
        elif candidate_paths:
            current_snapshot_path = candidate_paths[-1]
            current_source = "latest_versioned_fallback"
        else:
            raise ValueError(f"knowledge_snapshot_current_not_found:{snapshot_dir}")
    current_snapshot_path = current_snapshot_path.resolve()
    if not current_snapshot_path.exists():
        raise ValueError(f"knowledge_snapshot_current_missing:{current_snapshot_path}")

    previous_source = "explicit"
    if previous_snapshot_path is None:
        selected_previous: Path | None = None
        if current_snapshot_path == latest_path and len(candidate_paths) >= 2:
            selected_previous = candidate_paths[-2]
            previous_source = "index_previous_to_latest"
        elif current_snapshot_path in candidate_paths:
            current_index = candidate_paths.index(current_snapshot_path)
            if current_index > 0:
                selected_previous = candidate_paths[current_index - 1]
                previous_source = "index_previous_to_current"
        else:
            for candidate in reversed(candidate_paths):
                if candidate != current_snapshot_path:
                    selected_previous = candidate
                    previous_source = "latest_non_current_fallback"
                    break
        previous_snapshot_path = selected_previous

    if previous_snapshot_path is None:
        raise ValueError(f"knowledge_snapshot_previous_not_found:{snapshot_dir}")
    previous_snapshot_path = previous_snapshot_path.resolve()
    if not previous_snapshot_path.exists():
        raise ValueError(f"knowledge_snapshot_previous_missing:{previous_snapshot_path}")

    if previous_snapshot_path == current_snapshot_path and len(candidate_paths) >= 2:
        if current_snapshot_path == latest_path:
            previous_snapshot_path = candidate_paths[-2]
            previous_source = "index_previous_to_latest"
        else:
            try:
                current_index = candidate_paths.index(current_snapshot_path)
                if current_index > 0:
                    previous_snapshot_path = candidate_paths[current_index - 1]
                    previous_source = "index_previous_to_current"
            except ValueError:
                pass

    if previous_snapshot_path == current_snapshot_path:
        raise ValueError(f"knowledge_snapshot_previous_equals_current:{current_snapshot_path}")

    metadata = {
        "snapshot_dir": str(snapshot_dir),
        "latest_path": str(latest_path),
        "index_path": str(index_path),
        "index_entry_count": len(index_rows),
        "versioned_snapshot_count": len(candidate_paths),
        "current_snapshot_source": current_source,
        "previous_snapshot_source": previous_source,
    }
    return current_snapshot_path, previous_snapshot_path, metadata


def _parse_knowledge_delta_error_code(raw_error: str) -> str:
    text = str(raw_error or "").strip().lower()
    if not text:
        return ""
    return text.split(":", 1)[0].strip()


def _is_knowledge_delta_bootstrap_error(raw_error: str) -> bool:
    code = _parse_knowledge_delta_error_code(raw_error)
    return code in {
        "knowledge_snapshot_previous_not_found",
        "knowledge_snapshot_previous_equals_current",
    }


def _collect_knowledge_domain_metrics(
    snapshot: dict[str, Any],
    *,
    allowed_domains: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    knowledge_gaps_by_domain: Counter[str] = Counter()
    for gap in list(snapshot.get("knowledge_gaps") or []):
        gap_text = str(gap or "").strip()
        if not gap_text or ":" not in gap_text:
            continue
        domain, _ = gap_text.split(":", 1)
        normalized_domain = str(domain or "").strip().lower()
        if not normalized_domain:
            continue
        knowledge_gaps_by_domain[normalized_domain] += 1

    for row in [item for item in list(snapshot.get("domain_briefs") or []) if isinstance(item, dict)]:
        domain = str(row.get("domain") or "").strip().lower()
        if not domain:
            continue
        if allowed_domains is not None and domain not in allowed_domains:
            continue
        hypothesis_counts = dict(row.get("hypothesis_counts") or {})
        experiment_summary = dict(row.get("experiment_summary") or {})
        metrics[domain] = {
            "domain": domain,
            "friction_signal_count": int(row.get("friction_signal_count") or 0),
            "open_friction_count": int(row.get("open_friction_count") or 0),
            "displeasure_cluster_count": int(row.get("displeasure_cluster_count") or 0),
            "hypothesis_total_count": int(
                hypothesis_counts.get("total")
                if hypothesis_counts.get("total") is not None
                else sum(int(value or 0) for value in hypothesis_counts.values())
            ),
            "experiment_run_count": int(experiment_summary.get("run_count") or 0),
            "experiment_promote_count": int(experiment_summary.get("promote_count") or 0),
            "experiment_blocked_guardrail_count": int(experiment_summary.get("blocked_guardrail_count") or 0),
            "debug_hotspot_count": len([item for item in list(row.get("debug_hotspots") or []) if isinstance(item, dict)]),
            "controlled_test_candidate_count": len(
                [item for item in list(row.get("controlled_test_candidates") or []) if isinstance(item, dict)]
            ),
            "knowledge_gap_count": int(knowledge_gaps_by_domain.get(domain) or 0),
        }
    return metrics


def _collect_knowledge_priority_map(
    snapshot: dict[str, Any],
    *,
    allowed_domains: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in [item for item in list(snapshot.get("cross_domain_priority_board") or []) if isinstance(item, dict)]:
        domain = str(row.get("domain") or "").strip().lower()
        friction_key = _normalize_friction_key(row.get("friction_key"))
        if not domain or not friction_key:
            continue
        if allowed_domains is not None and domain not in allowed_domains:
            continue
        mapped[f"{domain}:{friction_key}"] = {
            "domain": domain,
            "friction_key": friction_key,
            "title": str(row.get("title") or ""),
            "summary": str(row.get("summary") or ""),
            "urgency_score": _coerce_float(row.get("urgency_score"), default=0.0),
            "impact_score": _coerce_float(row.get("impact_score"), default=0.0),
            "blocked_guardrail_rate": _coerce_float(row.get("blocked_guardrail_rate"), default=0.0),
            "signal_count": int(row.get("signal_count") or 0),
        }
    return mapped


def _collect_knowledge_debug_map(
    snapshot: dict[str, Any],
    *,
    allowed_domains: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in [item for item in list(snapshot.get("debug_hotspots") or []) if isinstance(item, dict)]:
        domain = str(row.get("domain") or "").strip().lower()
        friction_key = _normalize_friction_key(row.get("friction_key"))
        if not domain or not friction_key:
            continue
        if allowed_domains is not None and domain not in allowed_domains:
            continue
        mapped[f"{domain}:{friction_key}"] = {
            "domain": domain,
            "friction_key": friction_key,
            "failure_rate": _coerce_float(row.get("failure_rate"), default=0.0),
            "blocked_guardrail_count": int(row.get("blocked_guardrail_count") or 0),
            "run_count": int(row.get("run_count") or 0),
            "confidence_gap": bool(row.get("confidence_gap")),
        }
    return mapped


def cmd_improvement_knowledge_brief(args: argparse.Namespace) -> None:
    runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
    try:
        repo_path = args.repo_path.resolve()
        domains = _normalize_improvement_knowledge_domains(getattr(args, "domains", None))
        query = str(getattr(args, "query", "") or "").strip()
        query_tokens = _tokenize_knowledge_query(query)
        displeasure_limit = max(1, int(getattr(args, "displeasure_limit", 8) or 8))
        hypothesis_limit = max(1, int(getattr(args, "hypothesis_limit", 80) or 80))
        experiment_limit = max(1, int(getattr(args, "experiment_limit", 120) or 120))
        controlled_test_limit = max(1, int(getattr(args, "controlled_test_limit", 5) or 5))
        min_cluster_count = max(1, int(getattr(args, "min_cluster_count", 2) or 2))

        domain_briefs: list[dict[str, Any]] = []
        cross_domain_priority_rows: list[dict[str, Any]] = []
        global_debug_hotspots: list[dict[str, Any]] = []
        global_controlled_test_candidates: list[dict[str, Any]] = []
        knowledge_gaps: list[str] = []
        critical_knowledge_gaps: list[str] = []

        for domain in domains:
            frictions = runtime.list_domain_frictions(
                domain=domain,
                status=None,
                source=None,
                limit=max(200, int(displeasure_limit) * 60),
            )
            open_friction_count = sum(1 for row in frictions if str(row.get("status") or "").strip().lower() == "open")

            displeasure_summary = runtime.summarize_domain_displeasures(
                domain=domain,
                min_count=1,
                limit=max(20, int(displeasure_limit) * 4),
            )
            displeasure_rows_raw = [row for row in list(displeasure_summary.get("clusters") or []) if isinstance(row, dict)]
            top_displeasure_rows = [
                {
                    "domain": domain,
                    "canonical_key": str(row.get("canonical_key") or ""),
                    "friction_key": _normalize_friction_key(row.get("canonical_key")),
                    "signal_count": int(row.get("signal_count") or 0),
                    "impact_score": round(_coerce_float(row.get("impact_score"), default=0.0), 4),
                    "avg_severity": round(_coerce_float(row.get("avg_severity"), default=0.0), 4),
                    "avg_frustration_score": round(_coerce_float(row.get("avg_frustration_score"), default=0.0), 4),
                    "top_tags": [dict(item) for item in list(row.get("top_tags") or []) if isinstance(item, dict)],
                    "top_sources": [dict(item) for item in list(row.get("top_sources") or []) if isinstance(item, dict)],
                    "example_summary": str(row.get("example_summary") or ""),
                    "latest_seen_at": row.get("latest_seen_at"),
                }
                for row in displeasure_rows_raw
            ]
            top_displeasure_rows = _filter_knowledge_rows_by_query(
                top_displeasure_rows,
                query_tokens=query_tokens,
                text_builder=lambda row: [
                    row.get("canonical_key"),
                    row.get("friction_key"),
                    row.get("example_summary"),
                    row.get("top_tags"),
                    row.get("top_sources"),
                ],
            )
            top_displeasure_rows = top_displeasure_rows[:displeasure_limit]

            hypotheses = [
                row
                for row in runtime.list_hypotheses(domain=domain, status=None, limit=hypothesis_limit)
                if isinstance(row, dict)
            ]
            experiments = [
                row
                for row in runtime.list_hypothesis_experiments(domain=domain, status=None, limit=experiment_limit)
                if isinstance(row, dict)
            ]

            latest_run_by_hypothesis: dict[str, dict[str, Any]] = {}
            for run in experiments:
                hypothesis_id = str(run.get("hypothesis_id") or "").strip()
                if not hypothesis_id or hypothesis_id in latest_run_by_hypothesis:
                    continue
                latest_run_by_hypothesis[hypothesis_id] = run

            hypothesis_status_counts: dict[str, int] = {}
            hypothesis_friction_by_id: dict[str, str] = {}
            friction_stats: dict[str, dict[str, Any]] = {}
            top_hypothesis_rows: list[dict[str, Any]] = []
            hypothesis_controlled_candidates: list[dict[str, Any]] = []

            for hypothesis in hypotheses:
                hypothesis_id = str(hypothesis.get("hypothesis_id") or "").strip()
                status = str(hypothesis.get("status") or "queued").strip().lower() or "queued"
                hypothesis_status_counts[status] = int(hypothesis_status_counts.get(status) or 0) + 1
                friction_key = _normalize_friction_key(hypothesis.get("friction_key"))
                if friction_key:
                    hypothesis_friction_by_id[hypothesis_id] = friction_key
                    stats = friction_stats.setdefault(
                        friction_key,
                        {
                            "domain": domain,
                            "friction_key": friction_key,
                            "linked_hypothesis_ids": [],
                            "queued_hypothesis_count": 0,
                            "testing_hypothesis_count": 0,
                            "validated_hypothesis_count": 0,
                            "rejected_hypothesis_count": 0,
                            "run_count": 0,
                            "promote_count": 0,
                            "blocked_guardrail_count": 0,
                            "needs_iteration_count": 0,
                            "insufficient_data_count": 0,
                            "invalid_measurement_count": 0,
                            "other_verdict_count": 0,
                        },
                    )
                    if hypothesis_id and hypothesis_id not in list(stats.get("linked_hypothesis_ids") or []):
                        stats.setdefault("linked_hypothesis_ids", []).append(hypothesis_id)
                    if status == "queued":
                        stats["queued_hypothesis_count"] = int(stats.get("queued_hypothesis_count") or 0) + 1
                    elif status == "testing":
                        stats["testing_hypothesis_count"] = int(stats.get("testing_hypothesis_count") or 0) + 1
                    elif status == "validated":
                        stats["validated_hypothesis_count"] = int(stats.get("validated_hypothesis_count") or 0) + 1
                    elif status == "rejected":
                        stats["rejected_hypothesis_count"] = int(stats.get("rejected_hypothesis_count") or 0) + 1

                metadata = dict(hypothesis.get("metadata") or {}) if isinstance(hypothesis.get("metadata"), dict) else {}
                last_run = latest_run_by_hypothesis.get(hypothesis_id) or {}
                evaluation = dict(last_run.get("evaluation") or {}) if isinstance(last_run.get("evaluation"), dict) else {}
                top_hypothesis_rows.append(
                    {
                        "domain": domain,
                        "hypothesis_id": hypothesis_id,
                        "title": str(hypothesis.get("title") or ""),
                        "status": status,
                        "risk_level": str(hypothesis.get("risk_level") or ""),
                        "friction_key": friction_key or None,
                        "priority_score": round(_coerce_float(metadata.get("priority_score"), default=0.0), 4),
                        "statement": str(hypothesis.get("statement") or ""),
                        "proposed_change": str(hypothesis.get("proposed_change") or ""),
                        "last_experiment_verdict": str(evaluation.get("verdict") or "").strip().lower() or None,
                        "last_experiment_at": last_run.get("created_at"),
                    }
                )
                hypothesis_controlled_candidates.append(
                    {
                        "domain": domain,
                        "hypothesis_id": hypothesis_id or None,
                        "title": str(hypothesis.get("title") or ""),
                        "friction_key": friction_key or None,
                        "statement": str(hypothesis.get("statement") or ""),
                        "proposed_change": str(hypothesis.get("proposed_change") or ""),
                        "priority_score": round(_coerce_float(metadata.get("priority_score"), default=0.0), 4),
                        "risk_level": str(hypothesis.get("risk_level") or "medium"),
                        "recommended_environment": _default_controlled_environment_for_domain(domain),
                        "success_criteria": dict(hypothesis.get("success_criteria") or {}),
                        "evidence": {
                            "source": "registered_hypothesis",
                            "status": status,
                            "last_experiment_verdict": str(evaluation.get("verdict") or "").strip().lower() or None,
                            "last_experiment_at": last_run.get("created_at"),
                        },
                    }
                )

            for run in experiments:
                hypothesis_id = str(run.get("hypothesis_id") or "").strip()
                if not hypothesis_id:
                    continue
                friction_key = _normalize_friction_key(hypothesis_friction_by_id.get(hypothesis_id))
                if not friction_key:
                    continue
                stats = friction_stats.setdefault(
                    friction_key,
                    {
                        "domain": domain,
                        "friction_key": friction_key,
                        "linked_hypothesis_ids": [],
                        "queued_hypothesis_count": 0,
                        "testing_hypothesis_count": 0,
                        "validated_hypothesis_count": 0,
                        "rejected_hypothesis_count": 0,
                        "run_count": 0,
                        "promote_count": 0,
                        "blocked_guardrail_count": 0,
                        "needs_iteration_count": 0,
                        "insufficient_data_count": 0,
                        "invalid_measurement_count": 0,
                        "other_verdict_count": 0,
                    },
                )
                stats["run_count"] = int(stats.get("run_count") or 0) + 1
                evaluation = dict(run.get("evaluation") or {}) if isinstance(run.get("evaluation"), dict) else {}
                verdict = str(evaluation.get("verdict") or run.get("verdict") or "").strip().lower()
                if verdict == "promote":
                    stats["promote_count"] = int(stats.get("promote_count") or 0) + 1
                elif verdict == "blocked_guardrail":
                    stats["blocked_guardrail_count"] = int(stats.get("blocked_guardrail_count") or 0) + 1
                elif verdict == "needs_iteration":
                    stats["needs_iteration_count"] = int(stats.get("needs_iteration_count") or 0) + 1
                elif verdict == "insufficient_data":
                    stats["insufficient_data_count"] = int(stats.get("insufficient_data_count") or 0) + 1
                elif verdict == "invalid_measurement":
                    stats["invalid_measurement_count"] = int(stats.get("invalid_measurement_count") or 0) + 1
                else:
                    stats["other_verdict_count"] = int(stats.get("other_verdict_count") or 0) + 1

            top_hypothesis_rows.sort(
                key=lambda row: (
                    -float(row.get("priority_score") or 0.0),
                    str(row.get("status") or ""),
                    str(row.get("title") or ""),
                )
            )
            top_hypothesis_rows = _filter_knowledge_rows_by_query(
                top_hypothesis_rows,
                query_tokens=query_tokens,
                text_builder=lambda row: [
                    row.get("title"),
                    row.get("friction_key"),
                    row.get("statement"),
                    row.get("proposed_change"),
                    row.get("last_experiment_verdict"),
                ],
            )
            top_hypothesis_rows = top_hypothesis_rows[: max(displeasure_limit, min(20, hypothesis_limit))]

            debug_hotspots: list[dict[str, Any]] = []
            for stats in friction_stats.values():
                run_count = int(stats.get("run_count") or 0)
                if run_count <= 0:
                    continue
                blocked_guardrail_count = int(stats.get("blocked_guardrail_count") or 0)
                needs_iteration_count = int(stats.get("needs_iteration_count") or 0)
                insufficient_data_count = int(stats.get("insufficient_data_count") or 0)
                invalid_measurement_count = int(stats.get("invalid_measurement_count") or 0)
                failure_count = (
                    blocked_guardrail_count
                    + needs_iteration_count
                    + insufficient_data_count
                    + invalid_measurement_count
                )
                confidence_gap = run_count < 2
                if failure_count <= 0 and not confidence_gap:
                    continue
                debug_hotspots.append(
                    {
                        "domain": domain,
                        "friction_key": stats.get("friction_key"),
                        "run_count": run_count,
                        "failure_count": int(failure_count),
                        "failure_rate": round(float(failure_count) / float(max(1, run_count)), 4),
                        "blocked_guardrail_count": blocked_guardrail_count,
                        "blocked_guardrail_rate": round(float(blocked_guardrail_count) / float(max(1, run_count)), 4),
                        "needs_iteration_count": needs_iteration_count,
                        "insufficient_data_count": insufficient_data_count,
                        "invalid_measurement_count": invalid_measurement_count,
                        "confidence_gap": bool(confidence_gap),
                        "linked_hypothesis_ids": list(stats.get("linked_hypothesis_ids") or []),
                    }
                )
            debug_hotspots.sort(
                key=lambda row: (
                    -float(row.get("failure_rate") or 0.0),
                    -int(row.get("blocked_guardrail_count") or 0),
                    -int(row.get("run_count") or 0),
                    str(row.get("friction_key") or ""),
                )
            )
            debug_hotspots = _filter_knowledge_rows_by_query(
                debug_hotspots,
                query_tokens=query_tokens,
                text_builder=lambda row: [
                    row.get("friction_key"),
                    row.get("linked_hypothesis_ids"),
                ],
            )
            debug_hotspots = debug_hotspots[:displeasure_limit]

            controlled_candidates_raw = runtime.propose_friction_hypotheses(
                domain=domain,
                min_count=min_cluster_count,
                limit=max(controlled_test_limit, controlled_test_limit * 3),
            )
            controlled_test_candidates = [
                {
                    "domain": domain,
                    "title": str(row.get("title") or ""),
                    "friction_key": _normalize_friction_key(row.get("friction_key")),
                    "statement": str(row.get("statement") or ""),
                    "proposed_change": str(row.get("proposed_change") or ""),
                    "priority_score": round(_coerce_float(row.get("priority_score"), default=0.0), 4),
                    "risk_level": str(row.get("risk_level") or "medium"),
                    "recommended_environment": _default_controlled_environment_for_domain(domain),
                    "success_criteria": dict(row.get("success_criteria") or {}),
                    "evidence": dict(row.get("evidence") or {}),
                }
                for row in list(controlled_candidates_raw or [])
                if isinstance(row, dict)
            ]
            controlled_test_candidates = _filter_knowledge_rows_by_query(
                controlled_test_candidates,
                query_tokens=query_tokens,
                text_builder=lambda row: [
                    row.get("title"),
                    row.get("friction_key"),
                    row.get("statement"),
                    row.get("proposed_change"),
                    row.get("evidence"),
                ],
            )
            controlled_test_candidates.extend(
                _filter_knowledge_rows_by_query(
                    hypothesis_controlled_candidates,
                    query_tokens=query_tokens,
                    text_builder=lambda row: [
                        row.get("title"),
                        row.get("friction_key"),
                        row.get("statement"),
                        row.get("proposed_change"),
                        row.get("hypothesis_id"),
                        row.get("evidence"),
                    ],
                )
            )
            controlled_test_candidates.sort(
                key=lambda row: (
                    -int(row.get("query_score") or 0),
                    -float(row.get("priority_score") or 0.0),
                    str(row.get("title") or ""),
                )
            )
            deduped_controlled_candidates: list[dict[str, Any]] = []
            seen_controlled_keys: set[str] = set()
            for row in controlled_test_candidates:
                hypothesis_id = str(row.get("hypothesis_id") or "").strip()
                friction_key = str(row.get("friction_key") or "").strip().lower()
                title = str(row.get("title") or "").strip().lower()
                dedupe_key = hypothesis_id or f"{friction_key}:{title}"
                if not dedupe_key or dedupe_key in seen_controlled_keys:
                    continue
                seen_controlled_keys.add(dedupe_key)
                deduped_controlled_candidates.append(row)
                if len(deduped_controlled_candidates) >= controlled_test_limit:
                    break
            controlled_test_candidates = deduped_controlled_candidates

            run_verdict_counts: Counter[str] = Counter()
            for run in experiments:
                evaluation = dict(run.get("evaluation") or {}) if isinstance(run.get("evaluation"), dict) else {}
                verdict = str(evaluation.get("verdict") or run.get("verdict") or "").strip().lower() or "unknown"
                run_verdict_counts[verdict] += 1

            for row in top_displeasure_rows:
                friction_key = _normalize_friction_key(row.get("friction_key") or row.get("canonical_key"))
                stats = dict(friction_stats.get(friction_key) or {})
                run_count = int(stats.get("run_count") or 0)
                promote_count = int(stats.get("promote_count") or 0)
                blocked_guardrail_count = int(stats.get("blocked_guardrail_count") or 0)
                promote_rate = float(promote_count) / float(run_count) if run_count > 0 else 0.0
                blocked_guardrail_rate = float(blocked_guardrail_count) / float(run_count) if run_count > 0 else 0.0
                active_hypothesis_count = int(stats.get("queued_hypothesis_count") or 0) + int(
                    stats.get("testing_hypothesis_count") or 0
                )
                impact_score = _coerce_float(row.get("impact_score"), default=0.0)
                urgency_score = (
                    impact_score
                    * (1.0 + blocked_guardrail_rate + (1.0 - promote_rate))
                    * (1.0 + min(1.5, float(active_hypothesis_count) * 0.25))
                )
                cross_domain_priority_rows.append(
                    {
                        "domain": domain,
                        "title": str(row.get("canonical_key") or friction_key or "unlabeled_friction"),
                        "summary": str(row.get("example_summary") or row.get("canonical_key") or friction_key or ""),
                        "canonical_key": row.get("canonical_key"),
                        "friction_key": friction_key,
                        "impact_score": round(float(impact_score), 4),
                        "signal_count": int(row.get("signal_count") or 0),
                        "run_count": run_count,
                        "promote_rate": round(float(promote_rate), 4),
                        "blocked_guardrail_rate": round(float(blocked_guardrail_rate), 4),
                        "active_hypothesis_count": active_hypothesis_count,
                        "urgency_score": round(float(urgency_score), 4),
                        "query_score": int(row.get("query_score") or 0),
                    }
                )

            global_debug_hotspots.extend(debug_hotspots)
            global_controlled_test_candidates.extend(controlled_test_candidates)

            domain_actions: list[str] = []
            if not frictions:
                domain_actions.append(
                    f"Ingest additional {domain} feedback so common displeasures can be measured before new implementations."
                )
                gap = f"{domain}:missing_friction_signals"
                knowledge_gaps.append(gap)
                critical_knowledge_gaps.append(gap)
            if not hypotheses:
                domain_actions.append(f"Seed at least one {domain} hypothesis from current displeasure clusters.")
                knowledge_gaps.append(f"{domain}:missing_hypotheses")
            if not experiments:
                domain_actions.append(f"Run controlled {domain} experiments to validate benefits before rollout.")
                knowledge_gaps.append(f"{domain}:missing_experiment_runs")
            if not controlled_test_candidates:
                knowledge_gaps.append(f"{domain}:missing_controlled_test_candidates")
            if debug_hotspots:
                domain_actions.append(
                    "Debug highest-risk friction first: "
                    f"{debug_hotspots[0].get('friction_key')} "
                    f"(blocked_guardrail_count={debug_hotspots[0].get('blocked_guardrail_count')}, "
                    f"failure_rate={debug_hotspots[0].get('failure_rate')})."
                )
            if controlled_test_candidates:
                domain_actions.append(
                    "Schedule controlled validation for "
                    f"{controlled_test_candidates[0].get('friction_key')} "
                    f"in {controlled_test_candidates[0].get('recommended_environment')}."
                )
            if not domain_actions:
                domain_actions.append(
                    f"{domain} knowledge loop is healthy; continue ingest -> hypothesis -> controlled-test cadence."
                )

            hypothesis_status_counts["total"] = int(len(hypotheses))
            experiment_summary = {
                "run_count": int(len(experiments)),
                "promote_count": int(run_verdict_counts.get("promote") or 0),
                "blocked_guardrail_count": int(run_verdict_counts.get("blocked_guardrail") or 0),
                "needs_iteration_count": int(run_verdict_counts.get("needs_iteration") or 0),
                "insufficient_data_count": int(run_verdict_counts.get("insufficient_data") or 0),
                "invalid_measurement_count": int(run_verdict_counts.get("invalid_measurement") or 0),
                "other_verdict_count": int(
                    sum(
                        int(count)
                        for verdict, count in run_verdict_counts.items()
                        if verdict
                        not in {
                            "promote",
                            "blocked_guardrail",
                            "needs_iteration",
                            "insufficient_data",
                            "invalid_measurement",
                        }
                    )
                ),
            }

            domain_briefs.append(
                {
                    "domain": domain,
                    "query": query or None,
                    "query_token_count": len(query_tokens),
                    "friction_signal_count": int(len(frictions)),
                    "open_friction_count": int(open_friction_count),
                    "displeasure_cluster_count": int(displeasure_summary.get("cluster_count") or 0),
                    "top_displeasures": top_displeasure_rows,
                    "hypothesis_counts": hypothesis_status_counts,
                    "top_hypotheses": top_hypothesis_rows,
                    "experiment_summary": experiment_summary,
                    "debug_hotspots": debug_hotspots,
                    "controlled_test_candidates": controlled_test_candidates,
                    "suggested_actions": domain_actions,
                }
            )

        cross_domain_priority_rows.sort(
            key=lambda row: (
                -int(row.get("query_score") or 0),
                -float(row.get("urgency_score") or 0.0),
                -float(row.get("impact_score") or 0.0),
                str(row.get("domain") or ""),
                str(row.get("friction_key") or ""),
            )
        )
        cross_domain_priority_board = cross_domain_priority_rows[: max(10, int(displeasure_limit) * 3)]
        global_debug_hotspots.sort(
            key=lambda row: (
                -int(row.get("query_score") or 0),
                -float(row.get("failure_rate") or 0.0),
                -int(row.get("blocked_guardrail_count") or 0),
                -int(row.get("run_count") or 0),
                str(row.get("domain") or ""),
                str(row.get("friction_key") or ""),
            )
        )
        global_controlled_test_candidates.sort(
            key=lambda row: (
                -int(row.get("query_score") or 0),
                -float(row.get("priority_score") or 0.0),
                str(row.get("domain") or ""),
                str(row.get("title") or ""),
            )
        )
        capped_debug_hotspots = global_debug_hotspots[: max(10, int(displeasure_limit) * 3)]
        capped_controlled_test_candidates = global_controlled_test_candidates[
            : max(10, int(controlled_test_limit) * 3)
        ]

        suggested_actions: list[str] = []
        for row in cross_domain_priority_board[:3]:
            suggested_actions.append(
                "Prioritize "
                f"{row.get('domain')}:{row.get('friction_key')} "
                f"(urgency_score={row.get('urgency_score')}, impact_score={row.get('impact_score')}, "
                f"blocked_guardrail_rate={row.get('blocked_guardrail_rate')})."
            )
        if knowledge_gaps:
            for gap in knowledge_gaps[:3]:
                suggested_actions.append(f"Close knowledge gap: {gap}.")
        if not suggested_actions:
            suggested_actions.append(
                "Knowledge brief is populated; continue controlled testing and keep ingest cadence active."
            )

        status = "ok" if not critical_knowledge_gaps else "warning"
        generated_at = utc_now_iso()
        payload = {
            "generated_at": generated_at,
            "status": status,
            "domains": domains,
            "query": query or None,
            "query_tokens": query_tokens,
            "limits": {
                "displeasure_limit": displeasure_limit,
                "hypothesis_limit": hypothesis_limit,
                "experiment_limit": experiment_limit,
                "controlled_test_limit": controlled_test_limit,
                "min_cluster_count": min_cluster_count,
            },
            "domain_brief_count": len(domain_briefs),
            "domain_briefs": domain_briefs,
            "cross_domain_priority_board_count": len(cross_domain_priority_board),
            "cross_domain_priority_board": cross_domain_priority_board,
            "debug_hotspot_count": len(capped_debug_hotspots),
            "debug_hotspots": capped_debug_hotspots,
            "controlled_test_candidate_count": len(capped_controlled_test_candidates),
            "controlled_test_candidates": capped_controlled_test_candidates,
            "knowledge_gaps": sorted(set(str(item) for item in knowledge_gaps if str(item))),
            "suggested_actions": suggested_actions,
        }

        output_path = args.output_path.resolve() if args.output_path is not None else None
        if output_path is not None:
            payload["output_path"] = str(output_path)

        write_snapshot = bool(getattr(args, "write_snapshot", True))
        snapshot_label_raw = str(getattr(args, "snapshot_label", "") or "").strip()
        snapshot_label = snapshot_label_raw or None
        snapshot_dir = _resolve_knowledge_snapshot_dir(
            repo_path=repo_path,
            snapshot_dir_value=getattr(args, "snapshot_dir", None),
        )
        knowledge_snapshot: dict[str, Any] = {
            "enabled": False,
            "write_snapshot": bool(write_snapshot),
            "snapshot_dir": str(snapshot_dir),
            "snapshot_label": snapshot_label,
        }
        if write_snapshot:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_meta = _build_knowledge_snapshot_metadata(
                generated_at=generated_at,
                domains=domains,
                query=query,
                snapshot_label=snapshot_label,
            )
            snapshot_id = str(snapshot_meta.get("snapshot_id") or "").strip()
            snapshot_file_name = str(snapshot_meta.get("snapshot_file_name") or "").strip()
            snapshot_path = (snapshot_dir / snapshot_file_name).resolve()
            latest_path = (snapshot_dir / "knowledge_brief_latest.json").resolve()
            index_path = (snapshot_dir / "knowledge_brief_index.jsonl").resolve()
            snapshot_payload = dict(payload)
            snapshot_payload["knowledge_snapshot"] = {
                "enabled": True,
                "snapshot_id": snapshot_id,
                "path": str(snapshot_path),
                "snapshot_dir": str(snapshot_dir),
                "latest_path": str(latest_path),
                "index_path": str(index_path),
                "write_snapshot": True,
                "snapshot_label": snapshot_label,
            }
            snapshot_path.write_text(json.dumps(snapshot_payload, indent=2), encoding="utf-8")
            latest_path.write_text(json.dumps(snapshot_payload, indent=2), encoding="utf-8")
            index_row = {
                "generated_at": generated_at,
                "snapshot_id": snapshot_id,
                "path": str(snapshot_path),
                "status": str(payload.get("status") or ""),
                "domains": list(domains),
                "query": query or None,
                "domain_brief_count": int(payload.get("domain_brief_count") or 0),
                "cross_domain_priority_board_count": int(payload.get("cross_domain_priority_board_count") or 0),
                "debug_hotspot_count": int(payload.get("debug_hotspot_count") or 0),
                "controlled_test_candidate_count": int(payload.get("controlled_test_candidate_count") or 0),
                "knowledge_gap_count": len(list(payload.get("knowledge_gaps") or [])),
            }
            with index_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(index_row, sort_keys=True))
                fp.write("\n")
            knowledge_snapshot = {
                "enabled": True,
                "write_snapshot": True,
                "snapshot_id": snapshot_id,
                "path": str(snapshot_path),
                "snapshot_dir": str(snapshot_dir),
                "latest_path": str(latest_path),
                "index_path": str(index_path),
                "snapshot_label": snapshot_label,
            }
        else:
            knowledge_snapshot["reason"] = "disabled_by_flag"
        payload["knowledge_snapshot"] = knowledge_snapshot

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        _print_json_payload(
            payload,
            compact=bool(getattr(args, "json_compact", False)),
        )
        if status != "ok" and bool(getattr(args, "strict", False)):
            raise SystemExit(2)
    finally:
        runtime.close()


def cmd_improvement_knowledge_brief_delta(args: argparse.Namespace) -> None:
    repo_path = args.repo_path.resolve()
    domains = _normalize_improvement_knowledge_domains(getattr(args, "domains", None))
    allowed_domains = set(domains) if domains else None
    top_limit = max(1, int(getattr(args, "top_limit", 10) or 10))
    snapshot_dir = _resolve_knowledge_snapshot_dir(
        repo_path=repo_path,
        snapshot_dir_value=getattr(args, "snapshot_dir", None),
    )
    current_snapshot_path_input = _coerce_optional_snapshot_path(
        getattr(args, "current_snapshot_path", None),
        base_dir=snapshot_dir,
    )
    previous_snapshot_path_input = _coerce_optional_snapshot_path(
        getattr(args, "previous_snapshot_path", None),
        base_dir=snapshot_dir,
    )
    generated_at = utc_now_iso()

    output_path = args.output_path.resolve() if args.output_path is not None else None

    try:
        current_snapshot_path, previous_snapshot_path, resolution_meta = _resolve_knowledge_delta_snapshot_paths(
            snapshot_dir=snapshot_dir,
            current_snapshot_path=current_snapshot_path_input,
            previous_snapshot_path=previous_snapshot_path_input,
        )
        current_snapshot = _load_knowledge_snapshot_payload(current_snapshot_path)
        previous_snapshot = _load_knowledge_snapshot_payload(previous_snapshot_path)
        current_snapshot_meta = (
            dict(current_snapshot.get("knowledge_snapshot") or {})
            if isinstance(current_snapshot.get("knowledge_snapshot"), dict)
            else {}
        )
        previous_snapshot_meta = (
            dict(previous_snapshot.get("knowledge_snapshot") or {})
            if isinstance(previous_snapshot.get("knowledge_snapshot"), dict)
            else {}
        )
        current_snapshot_effective_path = Path(
            str(current_snapshot_meta.get("path") or current_snapshot_path)
        ).expanduser().resolve()
        previous_snapshot_effective_path = Path(
            str(previous_snapshot_meta.get("path") or previous_snapshot_path)
        ).expanduser().resolve()
    except Exception as exc:
        error_text = str(exc)
        error_code = _parse_knowledge_delta_error_code(error_text)
        bootstrap_required = _is_knowledge_delta_bootstrap_error(error_text)
        status = "skipped_bootstrap" if bootstrap_required else "warning"
        if bootstrap_required:
            suggested_actions = [
                "Bootstrap in progress: rerun `improvement knowledge-brief` to capture a second snapshot before delta comparisons.",
                "After the next snapshot is written, rerun `improvement knowledge-brief-delta` (or operator-cycle) to evaluate regressions.",
            ]
        else:
            suggested_actions = [
                "Generate at least two `improvement knowledge-brief` snapshots before running `knowledge-brief-delta`.",
                "If snapshots exist in a different directory, pass --snapshot-dir or explicit --current-snapshot-path/--previous-snapshot-path.",
            ]
        payload = {
            "generated_at": generated_at,
            "status": status,
            "domains": domains,
            "error": error_text,
            "error_code": error_code,
            "bootstrap_required": bootstrap_required,
            "snapshot_dir": str(snapshot_dir),
            "current_snapshot_path": str(current_snapshot_path_input) if current_snapshot_path_input is not None else None,
            "previous_snapshot_path": str(previous_snapshot_path_input) if previous_snapshot_path_input is not None else None,
            "domain_deltas": [],
            "accelerating_frictions": [],
            "cooling_frictions": [],
            "debug_regressions": [],
            "suggested_actions": suggested_actions,
        }
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            payload["output_path"] = str(output_path)
        _print_json_payload(payload, compact=bool(getattr(args, "json_compact", False)))
        if status not in {"ok", "skipped_bootstrap"} and bool(getattr(args, "strict", False)):
            raise SystemExit(2)
        return

    previous_domain_metrics = _collect_knowledge_domain_metrics(previous_snapshot, allowed_domains=allowed_domains)
    current_domain_metrics = _collect_knowledge_domain_metrics(current_snapshot, allowed_domains=allowed_domains)
    domain_names = sorted(set(previous_domain_metrics.keys()) | set(current_domain_metrics.keys()) | set(domains))

    domain_deltas: list[dict[str, Any]] = []
    for domain in domain_names:
        previous = dict(previous_domain_metrics.get(domain) or {})
        current = dict(current_domain_metrics.get(domain) or {})

        def _metric(name: str, *, source: dict[str, Any]) -> int:
            return int(source.get(name) or 0)

        def _delta(name: str) -> int:
            return _metric(name, source=current) - _metric(name, source=previous)

        open_friction_delta = _delta("open_friction_count")
        debug_hotspot_delta = _delta("debug_hotspot_count")
        blocked_guardrail_delta = _delta("experiment_blocked_guardrail_count")
        promote_delta = _delta("experiment_promote_count")
        knowledge_gap_delta = _delta("knowledge_gap_count")

        worsening_score = (
            max(0, open_friction_delta)
            + max(0, debug_hotspot_delta)
            + max(0, blocked_guardrail_delta)
            + max(0, knowledge_gap_delta)
            + max(0, -promote_delta)
        )
        improvement_score = (
            max(0, -open_friction_delta)
            + max(0, -debug_hotspot_delta)
            + max(0, -blocked_guardrail_delta)
            + max(0, promote_delta)
            + max(0, -knowledge_gap_delta)
        )
        trend = "flat"
        if worsening_score > improvement_score and worsening_score > 0:
            trend = "worsening"
        elif improvement_score > worsening_score and improvement_score > 0:
            trend = "improving"
        elif worsening_score > 0 or improvement_score > 0:
            trend = "mixed"

        domain_deltas.append(
            {
                "domain": domain,
                "trend": trend,
                "worsening_score": int(worsening_score),
                "improvement_score": int(improvement_score),
                "current": current,
                "previous": previous,
                "delta": {
                    "friction_signal_count": _delta("friction_signal_count"),
                    "open_friction_count": open_friction_delta,
                    "displeasure_cluster_count": _delta("displeasure_cluster_count"),
                    "hypothesis_total_count": _delta("hypothesis_total_count"),
                    "experiment_run_count": _delta("experiment_run_count"),
                    "experiment_promote_count": promote_delta,
                    "experiment_blocked_guardrail_count": blocked_guardrail_delta,
                    "debug_hotspot_count": debug_hotspot_delta,
                    "controlled_test_candidate_count": _delta("controlled_test_candidate_count"),
                    "knowledge_gap_count": knowledge_gap_delta,
                },
            }
        )

    domain_deltas.sort(
        key=lambda row: (
            -int(row.get("worsening_score") or 0),
            int(row.get("improvement_score") or 0),
            str(row.get("domain") or ""),
        )
    )

    previous_priority = _collect_knowledge_priority_map(previous_snapshot, allowed_domains=allowed_domains)
    current_priority = _collect_knowledge_priority_map(current_snapshot, allowed_domains=allowed_domains)
    priority_delta_rows: list[dict[str, Any]] = []
    for key in sorted(set(previous_priority.keys()) | set(current_priority.keys())):
        previous = dict(previous_priority.get(key) or {})
        current = dict(current_priority.get(key) or {})
        urgency_delta = _coerce_float(current.get("urgency_score"), default=0.0) - _coerce_float(
            previous.get("urgency_score"),
            default=0.0,
        )
        impact_delta = _coerce_float(current.get("impact_score"), default=0.0) - _coerce_float(
            previous.get("impact_score"),
            default=0.0,
        )
        blocked_rate_delta = _coerce_float(current.get("blocked_guardrail_rate"), default=0.0) - _coerce_float(
            previous.get("blocked_guardrail_rate"),
            default=0.0,
        )
        priority_delta_rows.append(
            {
                "domain": str(current.get("domain") or previous.get("domain") or "").strip().lower(),
                "friction_key": str(current.get("friction_key") or previous.get("friction_key") or ""),
                "title": str(current.get("title") or previous.get("title") or ""),
                "summary": str(current.get("summary") or previous.get("summary") or ""),
                "urgency_score_current": round(_coerce_float(current.get("urgency_score"), default=0.0), 4),
                "urgency_score_previous": round(_coerce_float(previous.get("urgency_score"), default=0.0), 4),
                "urgency_delta": round(float(urgency_delta), 4),
                "impact_score_current": round(_coerce_float(current.get("impact_score"), default=0.0), 4),
                "impact_score_previous": round(_coerce_float(previous.get("impact_score"), default=0.0), 4),
                "impact_delta": round(float(impact_delta), 4),
                "blocked_guardrail_rate_current": round(
                    _coerce_float(current.get("blocked_guardrail_rate"), default=0.0),
                    4,
                ),
                "blocked_guardrail_rate_previous": round(
                    _coerce_float(previous.get("blocked_guardrail_rate"), default=0.0),
                    4,
                ),
                "blocked_guardrail_rate_delta": round(float(blocked_rate_delta), 4),
                "signal_count_current": int(current.get("signal_count") or 0),
                "signal_count_previous": int(previous.get("signal_count") or 0),
                "is_new_in_current": bool(current and not previous),
                "was_removed_from_current": bool(previous and not current),
            }
        )

    accelerating_frictions = [
        row for row in priority_delta_rows if float(row.get("urgency_delta") or 0.0) > 0.0
    ]
    accelerating_frictions.sort(
        key=lambda row: (
            -float(row.get("urgency_delta") or 0.0),
            -float(row.get("impact_delta") or 0.0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        )
    )
    cooling_frictions = [
        row for row in priority_delta_rows if float(row.get("urgency_delta") or 0.0) < 0.0
    ]
    cooling_frictions.sort(
        key=lambda row: (
            float(row.get("urgency_delta") or 0.0),
            float(row.get("impact_delta") or 0.0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        )
    )

    previous_debug = _collect_knowledge_debug_map(previous_snapshot, allowed_domains=allowed_domains)
    current_debug = _collect_knowledge_debug_map(current_snapshot, allowed_domains=allowed_domains)
    debug_regressions: list[dict[str, Any]] = []
    for key in sorted(set(previous_debug.keys()) | set(current_debug.keys())):
        previous = dict(previous_debug.get(key) or {})
        current = dict(current_debug.get(key) or {})
        failure_rate_delta = _coerce_float(current.get("failure_rate"), default=0.0) - _coerce_float(
            previous.get("failure_rate"),
            default=0.0,
        )
        blocked_guardrail_delta = int(current.get("blocked_guardrail_count") or 0) - int(
            previous.get("blocked_guardrail_count") or 0
        )
        run_count_delta = int(current.get("run_count") or 0) - int(previous.get("run_count") or 0)
        if failure_rate_delta <= 0.0 and blocked_guardrail_delta <= 0 and not (current and not previous):
            continue
        debug_regressions.append(
            {
                "domain": str(current.get("domain") or previous.get("domain") or "").strip().lower(),
                "friction_key": str(current.get("friction_key") or previous.get("friction_key") or ""),
                "failure_rate_current": round(_coerce_float(current.get("failure_rate"), default=0.0), 4),
                "failure_rate_previous": round(_coerce_float(previous.get("failure_rate"), default=0.0), 4),
                "failure_rate_delta": round(float(failure_rate_delta), 4),
                "blocked_guardrail_count_current": int(current.get("blocked_guardrail_count") or 0),
                "blocked_guardrail_count_previous": int(previous.get("blocked_guardrail_count") or 0),
                "blocked_guardrail_count_delta": int(blocked_guardrail_delta),
                "run_count_current": int(current.get("run_count") or 0),
                "run_count_previous": int(previous.get("run_count") or 0),
                "run_count_delta": int(run_count_delta),
                "is_new_in_current": bool(current and not previous),
                "confidence_gap_current": bool(current.get("confidence_gap")),
            }
        )
    debug_regressions.sort(
        key=lambda row: (
            -float(row.get("failure_rate_delta") or 0.0),
            -int(row.get("blocked_guardrail_count_delta") or 0),
            -int(row.get("run_count_delta") or 0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        )
    )

    status = "ok"
    data_gaps: list[str] = []
    if not domain_deltas:
        status = "warning"
        data_gaps.append("missing_domain_deltas")
    if not previous_domain_metrics:
        status = "warning"
        data_gaps.append("previous_snapshot_missing_domain_metrics")
    if not current_domain_metrics:
        status = "warning"
        data_gaps.append("current_snapshot_missing_domain_metrics")

    summary = {
        "domain_count": len(domain_deltas),
        "worsening_domain_count": len([row for row in domain_deltas if str(row.get("trend") or "") == "worsening"]),
        "improving_domain_count": len([row for row in domain_deltas if str(row.get("trend") or "") == "improving"]),
        "accelerating_friction_count": len(accelerating_frictions),
        "cooling_friction_count": len(cooling_frictions),
        "debug_regression_count": len(debug_regressions),
    }

    suggested_actions: list[str] = []
    for row in domain_deltas[:3]:
        if int(row.get("worsening_score") or 0) <= 0:
            continue
        delta = dict(row.get("delta") or {})
        suggested_actions.append(
            "Stabilize "
            f"{row.get('domain')} first "
            f"(open_friction_delta={delta.get('open_friction_count')}, "
            f"blocked_guardrail_delta={delta.get('experiment_blocked_guardrail_count')}, "
            f"debug_hotspot_delta={delta.get('debug_hotspot_count')})."
        )
    for row in accelerating_frictions[:2]:
        suggested_actions.append(
            "Run controlled validation on accelerating friction "
            f"{row.get('domain')}:{row.get('friction_key')} "
            f"(urgency_delta={row.get('urgency_delta')}, blocked_guardrail_rate_delta={row.get('blocked_guardrail_rate_delta')})."
        )
    for row in debug_regressions[:2]:
        suggested_actions.append(
            "Debug regression hotspot "
            f"{row.get('domain')}:{row.get('friction_key')} "
            f"(failure_rate_delta={row.get('failure_rate_delta')}, "
            f"blocked_guardrail_count_delta={row.get('blocked_guardrail_count_delta')})."
        )
    if data_gaps:
        for gap in data_gaps[:2]:
            suggested_actions.append(f"Close data gap: {gap}.")
    if not suggested_actions:
        suggested_actions.append("Knowledge delta is stable; keep continuous ingestion and controlled-test cadence.")

    payload = {
        "generated_at": generated_at,
        "status": status,
        "bootstrap_required": False,
        "domains": domains,
        "top_limit": top_limit,
        "snapshot_dir": str(snapshot_dir),
        "current_snapshot_path": str(current_snapshot_effective_path),
        "previous_snapshot_path": str(previous_snapshot_effective_path),
        "current_snapshot_source": str(resolution_meta.get("current_snapshot_source") or ""),
        "previous_snapshot_source": str(resolution_meta.get("previous_snapshot_source") or ""),
        "current_snapshot_selection_source": str(resolution_meta.get("current_snapshot_source") or ""),
        "previous_snapshot_selection_source": str(resolution_meta.get("previous_snapshot_source") or ""),
        "current_snapshot_path_source": str(resolution_meta.get("current_snapshot_source") or ""),
        "previous_snapshot_path_source": str(resolution_meta.get("previous_snapshot_source") or ""),
        "snapshot_selection_source": "explicit"
        if str(resolution_meta.get("current_snapshot_source") or "").startswith("explicit")
        and str(resolution_meta.get("previous_snapshot_source") or "").startswith("explicit")
        else "auto",
        "current_snapshot_generated_at": current_snapshot.get("generated_at"),
        "previous_snapshot_generated_at": previous_snapshot.get("generated_at"),
        "snapshot_selection": {
            **resolution_meta,
            "current_snapshot_path": str(current_snapshot_effective_path),
            "previous_snapshot_path": str(previous_snapshot_effective_path),
            "current_snapshot_requested_path": str(current_snapshot_path),
            "previous_snapshot_requested_path": str(previous_snapshot_path),
            "current_source": str(resolution_meta.get("current_snapshot_source") or ""),
            "previous_source": str(resolution_meta.get("previous_snapshot_source") or ""),
            "source": "explicit"
            if str(resolution_meta.get("current_snapshot_source") or "").startswith("explicit")
            and str(resolution_meta.get("previous_snapshot_source") or "").startswith("explicit")
            else "auto",
            "current_snapshot_generated_at": current_snapshot.get("generated_at"),
            "previous_snapshot_generated_at": previous_snapshot.get("generated_at"),
            "domains": domains,
        },
        "summary": summary,
        "domain_delta_count": len(domain_deltas),
        "domain_deltas": domain_deltas[: max(top_limit, len(domains))],
        "accelerating_friction_count": len(accelerating_frictions),
        "accelerating_frictions": accelerating_frictions[:top_limit],
        "cooling_friction_count": len(cooling_frictions),
        "cooling_frictions": cooling_frictions[:top_limit],
        "debug_regression_count": len(debug_regressions),
        "debug_regressions": debug_regressions[:top_limit],
        "data_gaps": data_gaps,
        "suggested_actions": suggested_actions,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(payload, compact=bool(getattr(args, "json_compact", False)))
    if status not in {"ok", "skipped_bootstrap"} and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def _classify_knowledge_delta_alert_severity(
    delta_payload: dict[str, Any],
    *,
    min_worsening_score: int = 2,
    min_urgency_delta: float = 0.25,
    min_failure_rate_delta: float = 0.05,
    min_blocked_guardrail_delta: int = 1,
    evidence_runtime_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(delta_payload.get("status") or "").strip().lower()
    domain_deltas = [row for row in list(delta_payload.get("domain_deltas") or []) if isinstance(row, dict)]
    accelerating_rows = [row for row in list(delta_payload.get("accelerating_frictions") or []) if isinstance(row, dict)]
    debug_rows = [row for row in list(delta_payload.get("debug_regressions") or []) if isinstance(row, dict)]

    worsening_domains: list[dict[str, Any]] = []
    for row in domain_deltas:
        worsening_score = int(row.get("worsening_score") or 0)
        delta = dict(row.get("delta") or {})
        blocked_guardrail_delta = int(delta.get("experiment_blocked_guardrail_count") or 0)
        if worsening_score >= max(1, int(min_worsening_score)) or blocked_guardrail_delta >= int(min_blocked_guardrail_delta):
            worsening_domains.append(row)

    accelerating_frictions = [
        row
        for row in accelerating_rows
        if float(row.get("urgency_delta") or 0.0) >= float(min_urgency_delta)
        or float(row.get("blocked_guardrail_rate_delta") or 0.0) > 0.0
    ]
    debug_regressions = [
        row
        for row in debug_rows
        if float(row.get("failure_rate_delta") or 0.0) >= float(min_failure_rate_delta)
        or int(row.get("blocked_guardrail_count_delta") or 0) >= int(min_blocked_guardrail_delta)
    ]
    critical_domains = []
    for row in worsening_domains:
        delta = dict(row.get("delta") or {})
        blocked_guardrail_delta = int(delta.get("experiment_blocked_guardrail_count") or 0)
        debug_hotspot_delta = int(delta.get("debug_hotspot_count") or 0)
        if blocked_guardrail_delta >= max(1, int(min_blocked_guardrail_delta)) or debug_hotspot_delta >= 2:
            critical_domains.append(row)

    evidence_history = dict(evidence_runtime_history or {})
    evidence_trend = str(evidence_history.get("trend") or "").strip().lower()
    evidence_priority_boost = max(0.0, _coerce_float(evidence_history.get("priority_boost"), default=0.0))
    evidence_recent_unresolved_runs = max(
        0,
        _coerce_int(evidence_history.get("recent_unresolved_runs"), default=0),
    )
    evidence_recurring_id_count = max(
        0,
        _coerce_int(evidence_history.get("recurring_missing_record_id_count"), default=0),
    )
    evidence_regression_signals = 0
    if evidence_trend == "worsening":
        evidence_regression_signals += 2
    elif evidence_trend in {"persistent", "insufficient_history"} and evidence_recent_unresolved_runs > 0:
        evidence_regression_signals += 1
    if evidence_recurring_id_count >= 2:
        evidence_regression_signals += 1

    total_regression_signals = (
        len(worsening_domains)
        + len(accelerating_frictions)
        + len(debug_regressions)
        + evidence_regression_signals
    )
    if total_regression_signals <= 0:
        return {
            "severity": "none",
            "score": 0,
            "status": status or "ok",
            "worsening_domain_count": 0,
            "accelerating_friction_count": 0,
            "debug_regression_count": 0,
            "critical_domain_count": 0,
            "total_regression_signals": 0,
            "recommended_urgency": None,
            "recommended_confidence": None,
            "reasons": ["no_threshold_breaches"],
            "worsening_domains": [],
            "accelerating_frictions": [],
            "debug_regressions": [],
            "evidence_runtime_trend": evidence_trend or None,
            "evidence_runtime_priority_boost": round(float(evidence_priority_boost), 4),
            "evidence_runtime_history": evidence_history,
        }

    reasons: list[str] = []
    score = 0
    if worsening_domains:
        score += 2
        reasons.append("worsening_domains_detected")
    if len(worsening_domains) >= 2:
        score += 1
        reasons.append("multiple_worsening_domains")
    if accelerating_frictions:
        score += 2
        reasons.append("accelerating_frictions_detected")
    if len(accelerating_frictions) >= 2:
        score += 1
        reasons.append("multiple_accelerating_frictions")
    if debug_regressions:
        score += 2
        reasons.append("debug_regressions_detected")
    if len(debug_regressions) >= 2:
        score += 1
        reasons.append("multiple_debug_regressions")
    if critical_domains:
        score += 2
        reasons.append("critical_domain_regressions")
    if evidence_trend == "worsening":
        score += 2
        reasons.append("evidence_lookup_runtime_worsening")
    elif evidence_trend == "persistent" and evidence_recent_unresolved_runs > 0:
        score += 1
        reasons.append("evidence_lookup_runtime_persistent")
    elif evidence_trend == "insufficient_history" and evidence_recent_unresolved_runs > 0:
        score += 1
        reasons.append("evidence_lookup_runtime_emerging")
    if evidence_recurring_id_count >= 2:
        score += 1
        reasons.append("evidence_lookup_runtime_recurring_ids")
    if evidence_priority_boost >= 0.75:
        score += 1
        reasons.append("evidence_lookup_runtime_priority_boost_high")
    if status != "ok":
        score += 1
        reasons.append("delta_payload_warning_status")

    severity = "critical" if score >= 6 else "warn"
    return {
        "severity": severity,
        "score": int(score),
        "status": status or "ok",
        "worsening_domain_count": len(worsening_domains),
        "accelerating_friction_count": len(accelerating_frictions),
        "debug_regression_count": len(debug_regressions),
        "critical_domain_count": len(critical_domains),
        "total_regression_signals": int(total_regression_signals),
        "recommended_urgency": 0.97 if severity == "critical" else 0.88,
        "recommended_confidence": 0.94 if severity == "critical" else 0.83,
        "reasons": reasons,
        "worsening_domains": worsening_domains,
        "accelerating_frictions": accelerating_frictions,
        "debug_regressions": debug_regressions,
        "evidence_runtime_trend": evidence_trend or None,
        "evidence_runtime_priority_boost": round(float(evidence_priority_boost), 4),
        "evidence_runtime_history": evidence_history,
    }


def _build_knowledge_delta_mitigations(
    delta_payload: dict[str, Any],
    *,
    severity_profile: dict[str, Any],
    max_items: int = 3,
) -> list[str]:
    severity = str(severity_profile.get("severity") or "none")
    worsening_domains = [
        row
        for row in list(severity_profile.get("worsening_domains") or [])
        if isinstance(row, dict)
    ]
    accelerating = [
        row
        for row in list(severity_profile.get("accelerating_frictions") or [])
        if isinstance(row, dict)
    ]
    debug_rows = [
        row
        for row in list(severity_profile.get("debug_regressions") or [])
        if isinstance(row, dict)
    ]
    evidence_runtime_trend = str(severity_profile.get("evidence_runtime_trend") or "").strip().lower()
    evidence_history = dict(severity_profile.get("evidence_runtime_history") or {})
    evidence_recurring_ids = [
        str(item).strip()
        for item in list(evidence_history.get("recurring_missing_record_ids") or [])[: max(1, int(max_items))]
        if str(item).strip()
    ]
    actions: list[str] = []
    if severity == "critical":
        actions.append("Escalate immediately: freeze promoted implementations in affected domains until regressions are triaged.")
    for row in worsening_domains[: max(1, int(max_items))]:
        delta = dict(row.get("delta") or {})
        actions.append(
            "Stabilize domain "
            f"{row.get('domain')} "
            f"(worsening_score={row.get('worsening_score')}, "
            f"open_friction_delta={delta.get('open_friction_count')}, "
            f"blocked_guardrail_delta={delta.get('experiment_blocked_guardrail_count')})."
        )
    for row in accelerating[: max(1, int(max_items))]:
        actions.append(
            "Prioritize accelerating friction "
            f"{row.get('domain')}:{row.get('friction_key')} "
            f"(urgency_delta={row.get('urgency_delta')}, impact_delta={row.get('impact_delta')})."
        )
    for row in debug_rows[: max(1, int(max_items))]:
        actions.append(
            "Debug regression hotspot "
            f"{row.get('domain')}:{row.get('friction_key')} "
            f"(failure_rate_delta={row.get('failure_rate_delta')}, "
            f"blocked_guardrail_count_delta={row.get('blocked_guardrail_count_delta')})."
        )
    if evidence_runtime_trend in {"worsening", "persistent", "insufficient_history"}:
        action = (
            "Resolve recurring unresolved evidence lookup record IDs to prevent source-grounding blind spots "
            f"(trend={evidence_runtime_trend})."
        )
        if evidence_recurring_ids:
            action += f" Focus IDs: {','.join(evidence_recurring_ids)}."
        actions.append(action)
    if not actions:
        actions.append("No high-priority knowledge delta regressions detected; continue monitoring and controlled validation cadence.")
    return actions


def cmd_improvement_knowledge_brief_delta_alert(args: argparse.Namespace) -> None:
    snapshot_dir = _resolve_knowledge_snapshot_dir(
        repo_path=args.repo_path.resolve(),
        snapshot_dir_value=getattr(args, "snapshot_dir", None),
    )
    top_limit = max(1, int(getattr(args, "top_limit", 10) or 10))
    alert_max_items = max(1, int(getattr(args, "alert_max_items", 3) or 3))
    evidence_runtime_history_path_raw = getattr(args, "evidence_runtime_history_path", None)
    evidence_runtime_history_path: Path | None = None
    if evidence_runtime_history_path_raw is not None and str(evidence_runtime_history_path_raw).strip():
        evidence_runtime_history_path = _resolve_path_from_base(
            evidence_runtime_history_path_raw,
            base_dir=Path.cwd(),
        ).resolve()
    evidence_runtime_history_window = max(
        1,
        _coerce_int(getattr(args, "evidence_runtime_history_window", 7), default=7),
    )
    evidence_runtime_history = _summarize_evidence_runtime_history(
        history_path=evidence_runtime_history_path,
        window=evidence_runtime_history_window,
    )

    delta_args = argparse.Namespace(
        domains=getattr(args, "domains", None),
        snapshot_dir=getattr(args, "snapshot_dir", None),
        current_snapshot_path=getattr(args, "current_snapshot_path", None),
        previous_snapshot_path=getattr(args, "previous_snapshot_path", None),
        top_limit=top_limit,
        output_path=None,
        strict=False,
        json_compact=False,
        repo_path=args.repo_path,
        db_path=args.db_path,
    )
    delta_payload = _invoke_cli_json_command(
        cmd_improvement_knowledge_brief_delta,
        args=delta_args,
    )
    delta_status = str(delta_payload.get("status") or "warning").strip().lower() or "warning"
    bootstrap_required = bool(delta_payload.get("bootstrap_required"))
    severity_profile = _classify_knowledge_delta_alert_severity(
        delta_payload,
        min_worsening_score=max(1, int(getattr(args, "min_worsening_score", 2) or 2)),
        min_urgency_delta=max(0.0, float(getattr(args, "min_urgency_delta", 0.25) or 0.25)),
        min_failure_rate_delta=max(0.0, float(getattr(args, "min_failure_rate_delta", 0.05) or 0.05)),
        min_blocked_guardrail_delta=max(
            1,
            int(getattr(args, "min_blocked_guardrail_delta", 1) or 1),
        ),
        evidence_runtime_history=evidence_runtime_history,
    )
    drift_severity = str(severity_profile.get("severity") or "none")
    mitigations = _build_knowledge_delta_mitigations(
        delta_payload,
        severity_profile=severity_profile,
        max_items=alert_max_items,
    )
    if delta_status == "skipped_bootstrap":
        delta_suggested_actions = [
            str(item).strip()
            for item in list(delta_payload.get("suggested_actions") or [])
            if str(item).strip()
        ]
        mitigations = delta_suggested_actions[: max(1, alert_max_items)] or [
            "Bootstrap in progress: capture another knowledge snapshot before alerting on regressions.",
        ]
    alert_payload: dict[str, Any] | None = None
    alert_created = False

    if drift_severity in {"warn", "critical"}:
        recommended_urgency = _coerce_float(severity_profile.get("recommended_urgency"), default=0.88)
        recommended_confidence = _coerce_float(severity_profile.get("recommended_confidence"), default=0.83)
        raw_urgency = getattr(args, "alert_urgency", None)
        raw_confidence = getattr(args, "alert_confidence", None)
        alert_urgency = max(
            0.0,
            min(
                1.0,
                (
                    _coerce_float(raw_urgency, default=recommended_urgency)
                    if raw_urgency is not None
                    else recommended_urgency
                ),
            ),
        )
        alert_confidence = max(
            0.0,
            min(
                1.0,
                (
                    _coerce_float(raw_confidence, default=recommended_confidence)
                    if raw_confidence is not None
                    else recommended_confidence
                ),
            ),
        )
        alert_domain = str(getattr(args, "alert_domain", "operations") or "operations").strip().lower() or "operations"

        worsening_domains = [
            str(row.get("domain") or "").strip().lower()
            for row in list(severity_profile.get("worsening_domains") or [])[:alert_max_items]
            if isinstance(row, dict) and str(row.get("domain") or "").strip()
        ]
        accelerating_refs = [
            f"{str(row.get('domain') or '').strip().lower()}:{str(row.get('friction_key') or '').strip()}"
            for row in list(severity_profile.get("accelerating_frictions") or [])[:alert_max_items]
            if isinstance(row, dict)
            and str(row.get("domain") or "").strip()
            and str(row.get("friction_key") or "").strip()
        ]
        debug_refs = [
            f"{str(row.get('domain') or '').strip().lower()}:{str(row.get('friction_key') or '').strip()}"
            for row in list(severity_profile.get("debug_regressions") or [])[:alert_max_items]
            if isinstance(row, dict)
            and str(row.get("domain") or "").strip()
            and str(row.get("friction_key") or "").strip()
        ]
        reason = (
            "knowledge_delta_regression_detected"
            + f" severity={drift_severity}"
            + f" worsening_domains={int(severity_profile.get('worsening_domain_count') or 0)}"
            + f" accelerating_frictions={int(severity_profile.get('accelerating_friction_count') or 0)}"
            + f" debug_regressions={int(severity_profile.get('debug_regression_count') or 0)}"
            + f" domains={','.join(worsening_domains[:alert_max_items]) if worsening_domains else 'none'}"
        )
        why_now = "knowledge delta thresholds indicate active regression risk across tracked domains."
        why_not_later = "delaying triage can compound friction growth, guardrail failures, and downstream regressions."

        runtime = JarvisRuntime(db_path=args.db_path.resolve(), repo_path=args.repo_path.resolve())
        try:
            decision = InterruptDecision(
                interrupt_id=new_id("int"),
                candidate_id=new_id("cand"),
                domain=alert_domain,
                reason=reason,
                urgency_score=alert_urgency,
                confidence=alert_confidence,
                suppression_window_hit=False,
                delivered=True,
                why_now=why_now,
                why_not_later=why_not_later,
                status="delivered",
            )
            runtime.interrupt_store.store(decision)
            interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
            runtime.memory.append_event(
                "improvement.knowledge_delta_alert_created",
                {
                    "interrupt_id": interrupt.get("interrupt_id"),
                    "domain": alert_domain,
                    "drift_severity": drift_severity,
                    "severity_profile": severity_profile,
                    "snapshot_dir": str(snapshot_dir),
                    "current_snapshot_path": delta_payload.get("current_snapshot_path"),
                    "previous_snapshot_path": delta_payload.get("previous_snapshot_path"),
                    "worsening_domains": worsening_domains,
                    "accelerating_refs": accelerating_refs,
                    "debug_refs": debug_refs,
                    "mitigation_actions": mitigations,
                    "evidence_lookup_runtime_history": evidence_runtime_history,
                },
            )
            alert_payload = {
                "interrupt_id": interrupt.get("interrupt_id"),
                "domain": interrupt.get("domain"),
                "status": interrupt.get("status"),
                "drift_severity": drift_severity,
                "urgency_score": interrupt.get("urgency_score"),
                "confidence": interrupt.get("confidence"),
                "reason": interrupt.get("reason"),
                "why_now": interrupt.get("why_now"),
                "why_not_later": interrupt.get("why_not_later"),
                "worsening_domains": worsening_domains,
                "accelerating_refs": accelerating_refs,
                "debug_refs": debug_refs,
                "acknowledge_command": (
                    f"python3 -m jarvis.cli interrupts acknowledge {interrupt.get('interrupt_id')} --actor operator"
                    if str(interrupt.get("interrupt_id") or "").strip()
                    else None
                ),
            }
            alert_created = True
        finally:
            runtime.close()

    status = "warning" if alert_created else delta_status
    payload = {
        "generated_at": utc_now_iso(),
        "status": status,
        "domains": _normalize_improvement_knowledge_domains(getattr(args, "domains", None)),
        "snapshot_dir": str(snapshot_dir),
        "bootstrap_required": bootstrap_required,
        "drift_severity": drift_severity,
        "severity_profile": severity_profile,
        "alert_created": alert_created,
        "alert": alert_payload,
        "acknowledge_commands": (
            [str(alert_payload.get("acknowledge_command"))]
            if isinstance(alert_payload, dict) and str(alert_payload.get("acknowledge_command") or "").strip()
            else []
        ),
        "mitigation_actions": mitigations,
        "delta": delta_payload,
        "evidence_lookup_runtime_history": evidence_runtime_history,
        "thresholds": {
            "min_worsening_score": max(1, int(getattr(args, "min_worsening_score", 2) or 2)),
            "min_urgency_delta": max(0.0, float(getattr(args, "min_urgency_delta", 0.25) or 0.25)),
            "min_failure_rate_delta": max(0.0, float(getattr(args, "min_failure_rate_delta", 0.05) or 0.05)),
            "min_blocked_guardrail_delta": max(1, int(getattr(args, "min_blocked_guardrail_delta", 1) or 1)),
            "evidence_runtime_history_window": int(evidence_runtime_history_window),
        },
    }
    if evidence_runtime_history_path is not None:
        payload["evidence_lookup_runtime_history_path"] = str(evidence_runtime_history_path)

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if status not in {"ok", "skipped_bootstrap"} and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def _coerce_knowledge_domains_csv_from_report(report_payload: dict[str, Any]) -> str:
    knowledge_alert_block = dict(report_payload.get("knowledge_brief_delta_alert") or {})
    knowledge_brief_block = dict(report_payload.get("knowledge_brief") or {})
    candidates = [
        knowledge_alert_block.get("domains"),
        knowledge_brief_block.get("domains"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            raw = candidate
        elif isinstance(candidate, (list, tuple, set)):
            raw = ",".join(str(item).strip() for item in candidate if str(item).strip())
        else:
            raw = str(candidate or "")
        normalized = _normalize_improvement_knowledge_domains(raw)
        if normalized:
            return ",".join(normalized)
    return DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV


def _build_knowledge_route_operator_cycle_command(report_payload: dict[str, Any]) -> str | None:
    config_path_raw = str(report_payload.get("config_path") or "").strip()
    if not config_path_raw:
        return None
    config_path = Path(config_path_raw).expanduser()
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    else:
        config_path = config_path.resolve()

    output_dir_raw = str(report_payload.get("output_dir") or "").strip()
    if output_dir_raw:
        output_dir = Path(output_dir_raw).expanduser()
        if not output_dir.is_absolute():
            output_dir = (Path.cwd() / output_dir).resolve()
        else:
            output_dir = output_dir.resolve()
    else:
        output_dir = (config_path.parent / "output" / "improvement" / "operator_cycle").resolve()

    knowledge_bootstrap_state = dict(report_payload.get("knowledge_bootstrap_state") or {})
    snapshot_dir_raw = str(knowledge_bootstrap_state.get("snapshot_dir") or "").strip()
    snapshot_dir = Path(snapshot_dir_raw).expanduser().resolve() if snapshot_dir_raw else None

    knowledge_brief_block = dict(report_payload.get("knowledge_brief") or {})
    query = str(knowledge_brief_block.get("query") or "").strip()
    snapshot_label: str | None = None
    knowledge_snapshot_block = dict(knowledge_brief_block.get("knowledge_snapshot") or {})
    snapshot_metadata = dict(knowledge_snapshot_block.get("metadata") or {})
    snapshot_label_raw = str(snapshot_metadata.get("label") or "").strip()
    if snapshot_label_raw:
        snapshot_label = snapshot_label_raw

    return _build_operator_cycle_knowledge_bootstrap_command(
        config_path=config_path,
        output_dir=output_dir,
        knowledge_domains=_coerce_knowledge_domains_csv_from_report(report_payload),
        knowledge_snapshot_dir=snapshot_dir,
        knowledge_query=query,
        knowledge_snapshot_label=snapshot_label,
    )


def _resolve_knowledge_bootstrap_phase_from_report(
    report_payload: dict[str, Any],
) -> tuple[str, str, str]:
    knowledge_bootstrap_state = dict(report_payload.get("knowledge_bootstrap_state") or {})
    stage_statuses = dict(report_payload.get("stage_statuses") or {})
    phase_raw = str(knowledge_bootstrap_state.get("phase") or "").strip().lower()
    if phase_raw in {"bootstrap_pending", "ready", "not_requested"}:
        stage_status = str(
            knowledge_bootstrap_state.get("stage_status")
            or stage_statuses.get("knowledge_brief_delta_alert")
            or ""
        ).strip().lower()
        return phase_raw, "knowledge_bootstrap_state.phase", stage_status

    stage_status = str(
        knowledge_bootstrap_state.get("stage_status")
        or stage_statuses.get("knowledge_brief_delta_alert")
        or ""
    ).strip().lower()
    if bool(knowledge_bootstrap_state.get("bootstrap_required")) or stage_status == "skipped_bootstrap":
        return "bootstrap_pending", "derived_stage_status", stage_status
    if stage_status == "skipped_not_requested":
        return "not_requested", "derived_stage_status", stage_status
    if stage_status in {"ok", "warning"}:
        return "ready", "derived_stage_status", stage_status
    return "unknown", "unresolved", stage_status


def _build_knowledge_bootstrap_route_payload(*, report_path: Path) -> dict[str, Any]:
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_report_file:expected_json_object")

    source_report_type = (
        "operator_cycle_report"
        if str(loaded.get("operator_report_path") or "").strip()
        or str(loaded.get("inbox_summary_path") or "").strip()
        or isinstance(loaded.get("summary"), dict)
        else (
            "operator_inbox_summary"
            if isinstance(loaded.get("knowledge_bootstrap_state"), dict)
            and isinstance(loaded.get("stage_statuses"), dict)
            else "unknown_report"
        )
    )
    knowledge_bootstrap_state = dict(loaded.get("knowledge_bootstrap_state") or {})
    phase, phase_source, stage_status = _resolve_knowledge_bootstrap_phase_from_report(loaded)
    bootstrap_required = bool(knowledge_bootstrap_state.get("bootstrap_required")) or phase == "bootstrap_pending"
    next_action_command = str(knowledge_bootstrap_state.get("next_action_command") or "").strip() or None
    next_action = str(knowledge_bootstrap_state.get("next_action") or "").strip() or None
    draft_payload = dict(loaded.get("draft") or {})
    benchmark_auto_reuse_status = str(draft_payload.get("benchmark_auto_reuse_status") or "").strip().lower()
    benchmark_report_path_source = str(draft_payload.get("benchmark_report_path_source") or "").strip().lower()
    benchmark_auto_reuse_stale = bool(draft_payload.get("benchmark_auto_reuse_stale"))
    benchmark_stale_fallback = bool(
        benchmark_auto_reuse_stale
        or benchmark_auto_reuse_status == "stale_skipped"
        or benchmark_report_path_source == "output_default_existing_stale_skipped"
    )
    benchmark_stale_reason = str(draft_payload.get("benchmark_auto_reuse_reason") or "").strip()
    if benchmark_stale_fallback and not benchmark_stale_reason:
        benchmark_stale_reason = "auto benchmark reuse skipped because artifact exceeded stale age limit"
    benchmark_stale_age_hours_raw = draft_payload.get("benchmark_auto_reuse_age_hours")
    benchmark_stale_age_hours = (
        _coerce_float(benchmark_stale_age_hours_raw, default=0.0) if benchmark_stale_age_hours_raw is not None else None
    )
    benchmark_stale_max_age_hours_raw = draft_payload.get("benchmark_max_age_hours")
    benchmark_stale_max_age_hours = (
        _coerce_float(benchmark_stale_max_age_hours_raw, default=0.0)
        if benchmark_stale_max_age_hours_raw is not None
        else None
    )
    benchmark_stale_next_action = (
        "Draft skipped stale benchmark reuse; use the refreshed benchmark artifact from this run on the next draft cycle."
        if benchmark_stale_fallback
        else "none"
    )
    benchmark_stale_history_window_raw = loaded.get("benchmark_stale_runtime_history_window")
    benchmark_stale_history_window = (
        max(1, _coerce_int(benchmark_stale_history_window_raw, default=7))
        if benchmark_stale_history_window_raw is not None
        else 7
    )
    benchmark_stale_history_window_source = (
        str(loaded.get("benchmark_stale_runtime_history_window_source") or "").strip() or "builtin_default"
    )
    benchmark_stale_repeat_threshold_raw = loaded.get("benchmark_stale_runtime_repeat_threshold")
    benchmark_stale_repeat_threshold = (
        max(1, _coerce_int(benchmark_stale_repeat_threshold_raw, default=2))
        if benchmark_stale_repeat_threshold_raw is not None
        else 2
    )
    benchmark_stale_repeat_threshold_source = (
        str(loaded.get("benchmark_stale_runtime_repeat_threshold_source") or "").strip() or "builtin_default"
    )
    benchmark_stale_rate_ceiling_raw = loaded.get("benchmark_stale_runtime_rate_ceiling")
    benchmark_stale_rate_ceiling = (
        min(1.0, max(0.0, _coerce_float(benchmark_stale_rate_ceiling_raw, default=0.6)))
        if benchmark_stale_rate_ceiling_raw is not None
        else 0.6
    )
    benchmark_stale_rate_ceiling_source = (
        str(loaded.get("benchmark_stale_runtime_rate_ceiling_source") or "").strip() or "builtin_default"
    )
    benchmark_stale_rate_consecutive_runs_raw = loaded.get("benchmark_stale_runtime_consecutive_runs")
    benchmark_stale_rate_consecutive_runs = (
        max(1, _coerce_int(benchmark_stale_rate_consecutive_runs_raw, default=2))
        if benchmark_stale_rate_consecutive_runs_raw is not None
        else 2
    )
    benchmark_stale_rate_consecutive_runs_source = (
        str(loaded.get("benchmark_stale_runtime_consecutive_runs_source") or "").strip() or "builtin_default"
    )

    status = "ok"
    route = "noop"
    route_reason = "Knowledge bootstrap stage was not requested in this report."
    if phase == "bootstrap_pending":
        route = "bootstrap"
        route_reason = "Knowledge bootstrap requires another snapshot before delta comparisons."
        if not next_action_command:
            next_action_command = _build_knowledge_route_operator_cycle_command(loaded)
        if not next_action:
            next_action = "Capture one more knowledge snapshot, then rerun operator-cycle."
    elif phase == "ready":
        route = "run_cycle"
        route_reason = "Knowledge bootstrap is ready; continue operator-cycle cadence with delta alerts enabled."
        if not next_action_command:
            next_action_command = _build_knowledge_route_operator_cycle_command(loaded)
        if not next_action:
            next_action = "Knowledge bootstrap ready; continue operator-cycle monitoring cadence."
    elif phase == "not_requested":
        route = "noop"
        route_reason = "Knowledge delta alert stage is currently disabled for this report."
        if not next_action:
            next_action = "No knowledge bootstrap action required."
    else:
        status = "warning"
        route = "noop"
        route_reason = "Unable to resolve knowledge bootstrap phase from report payload."
        if not next_action_command:
            next_action_command = _build_knowledge_route_operator_cycle_command(loaded)
        if not next_action:
            next_action = "Inspect the report and rerun operator-cycle with knowledge stages enabled."

    return {
        "generated_at": utc_now_iso(),
        "status": status,
        "report_path": str(report_path),
        "source_report_type": source_report_type,
        "phase": phase,
        "phase_source": phase_source,
        "stage_status": stage_status,
        "route": route,
        "route_reason": route_reason,
        "bootstrap_required": bootstrap_required,
        "next_action": next_action,
        "next_action_command": next_action_command,
        "benchmark_stale_fallback": int(benchmark_stale_fallback),
        "benchmark_stale_reason": benchmark_stale_reason or "none",
        "benchmark_stale_age_hours": benchmark_stale_age_hours,
        "benchmark_stale_max_age_hours": benchmark_stale_max_age_hours,
        "benchmark_stale_next_action": benchmark_stale_next_action,
        "benchmark_stale_history_window": int(benchmark_stale_history_window),
        "benchmark_stale_history_window_source": benchmark_stale_history_window_source,
        "benchmark_stale_repeat_threshold": int(benchmark_stale_repeat_threshold),
        "benchmark_stale_repeat_threshold_source": benchmark_stale_repeat_threshold_source,
        "benchmark_stale_rate_ceiling": round(float(benchmark_stale_rate_ceiling), 4),
        "benchmark_stale_rate_ceiling_source": benchmark_stale_rate_ceiling_source,
        "benchmark_stale_rate_consecutive_runs": int(benchmark_stale_rate_consecutive_runs),
        "benchmark_stale_rate_consecutive_runs_source": benchmark_stale_rate_consecutive_runs_source,
        "knowledge_bootstrap_state": knowledge_bootstrap_state,
    }


def cmd_improvement_knowledge_bootstrap_route(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    payload = _build_knowledge_bootstrap_route_payload(report_path=report_path)
    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if str(payload.get("status") or "warning") != "ok" and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


def cmd_improvement_knowledge_bootstrap_followup_rerun(args: argparse.Namespace) -> None:
    route_artifact_path = args.route_artifact_path.resolve()
    if not route_artifact_path.exists():
        raise SystemExit(f"missing_initial_route_artifact:{route_artifact_path}")

    route_payload = json.loads(route_artifact_path.read_text(encoding="utf-8"))
    if not isinstance(route_payload, dict):
        raise ValueError("invalid_knowledge_bootstrap_route_artifact:expected_json_object")

    next_action_command = str(route_payload.get("next_action_command") or "").strip()
    if not next_action_command:
        raise SystemExit("missing_bootstrap_next_action_command")

    subprocess.run(["bash", "-lc", next_action_command], check=True)

    operator_report_path = args.operator_report_path.resolve()
    if not operator_report_path.exists():
        raise SystemExit(f"missing_operator_report_after_bootstrap_followup:{operator_report_path}")

    post_route_artifact_path = args.post_route_artifact_path.resolve()
    post_route_payload = _build_knowledge_bootstrap_route_payload(report_path=operator_report_path)
    post_route_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    post_route_artifact_path.write_text(json.dumps(post_route_payload, indent=2), encoding="utf-8")

    post_status = str(post_route_payload.get("status") or "warning").strip() or "warning"
    post_phase = str(post_route_payload.get("phase") or "unknown").strip() or "unknown"
    post_route = str(post_route_payload.get("route") or "noop").strip() or "noop"

    payload = {
        "generated_at": utc_now_iso(),
        "status": "ok",
        "route_artifact_path": str(route_artifact_path),
        "next_action_command": next_action_command,
        "operator_report_path": str(operator_report_path),
        "post_route_artifact_path": str(post_route_artifact_path),
        "post_status": post_status,
        "post_phase": post_phase,
        "post_route": post_route,
    }

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    if bool(getattr(args, "emit_github_output", False)):
        bootstrap_followup_command = str(payload.get("next_action_command") or "").strip() or "none"
        bootstrap_followup_status = str(payload.get("post_status") or "warning").strip() or "warning"
        bootstrap_followup_phase = str(payload.get("post_phase") or "unknown").strip() or "unknown"
        bootstrap_followup_route = str(payload.get("post_route") or "noop").strip() or "noop"
        output_lines = [
            f"bootstrap_followup_command={bootstrap_followup_command}",
            f"bootstrap_followup_status={bootstrap_followup_status}",
            f"bootstrap_followup_phase={bootstrap_followup_phase}",
            f"bootstrap_followup_route={bootstrap_followup_route}",
        ]

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- command: `{bootstrap_followup_command}`",
                    f"- post_status: `{bootstrap_followup_status}`",
                    f"- post_phase: `{bootstrap_followup_phase}`",
                    f"- post_route: `{bootstrap_followup_route}`",
                    "",
                ]
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)) and post_status != "ok":
        raise SystemExit(2)


def _build_knowledge_bootstrap_route_outputs_payload(
    *,
    artifact_path: Path,
    artifact_source: str | None = None,
) -> dict[str, Any]:
    if artifact_path.exists():
        loaded = json.loads(artifact_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid_knowledge_bootstrap_route_artifact:expected_json_object")
        payload = dict(loaded)
    else:
        payload = {
            "status": "warning",
            "phase": "unknown",
            "route": "noop",
            "next_action": "knowledge bootstrap route artifact missing",
            "next_action_command": None,
        }

    status = str(payload.get("status") or "warning").strip() or "warning"
    phase = str(payload.get("phase") or "unknown").strip() or "unknown"
    route = str(payload.get("route") or "noop").strip() or "noop"
    next_action = str(payload.get("next_action") or "").strip() or "none"
    next_action_command = str(payload.get("next_action_command") or "").strip() or "none"
    benchmark_stale_fallback_raw = payload.get("benchmark_stale_fallback")
    benchmark_stale_fallback = (
        int(bool(benchmark_stale_fallback_raw))
        if benchmark_stale_fallback_raw is not None
        else 0
    )
    benchmark_stale_reason = str(payload.get("benchmark_stale_reason") or "").strip() or "none"
    benchmark_stale_next_action = str(payload.get("benchmark_stale_next_action") or "").strip() or "none"
    benchmark_stale_age_hours_raw = payload.get("benchmark_stale_age_hours")
    benchmark_stale_age_hours = (
        _coerce_float(benchmark_stale_age_hours_raw, default=0.0)
        if benchmark_stale_age_hours_raw is not None
        else None
    )
    benchmark_stale_max_age_hours_raw = payload.get("benchmark_stale_max_age_hours")
    benchmark_stale_max_age_hours = (
        _coerce_float(benchmark_stale_max_age_hours_raw, default=0.0)
        if benchmark_stale_max_age_hours_raw is not None
        else None
    )
    benchmark_stale_history_window = max(
        1,
        _coerce_int(payload.get("benchmark_stale_history_window"), default=7),
    )
    benchmark_stale_history_window_source = (
        str(payload.get("benchmark_stale_history_window_source") or "").strip() or "builtin_default"
    )
    benchmark_stale_repeat_threshold = max(
        1,
        _coerce_int(payload.get("benchmark_stale_repeat_threshold"), default=2),
    )
    benchmark_stale_repeat_threshold_source = (
        str(payload.get("benchmark_stale_repeat_threshold_source") or "").strip() or "builtin_default"
    )
    benchmark_stale_rate_ceiling = min(
        1.0,
        max(0.0, _coerce_float(payload.get("benchmark_stale_rate_ceiling"), default=0.6)),
    )
    benchmark_stale_rate_ceiling_source = (
        str(payload.get("benchmark_stale_rate_ceiling_source") or "").strip() or "builtin_default"
    )
    benchmark_stale_rate_consecutive_runs = max(
        1,
        _coerce_int(payload.get("benchmark_stale_rate_consecutive_runs"), default=2),
    )
    benchmark_stale_rate_consecutive_runs_source = (
        str(payload.get("benchmark_stale_rate_consecutive_runs_source") or "").strip() or "builtin_default"
    )
    if benchmark_stale_fallback != 0 and benchmark_stale_reason == "none":
        benchmark_stale_reason = "auto benchmark reuse skipped because artifact exceeded stale age limit"
    if benchmark_stale_fallback != 0 and benchmark_stale_next_action == "none":
        benchmark_stale_next_action = (
            "Draft skipped stale benchmark reuse; use the refreshed benchmark artifact from this run on the next draft cycle."
        )
    route_blocking = status == "warning" and route != "bootstrap"

    result = {
        "generated_at": utc_now_iso(),
        "artifact_path": str(artifact_path),
        "status": status,
        "phase": phase,
        "route": route,
        "route_blocking": int(route_blocking),
        "next_action": next_action,
        "next_action_command": next_action_command,
        "benchmark_stale_fallback": int(benchmark_stale_fallback),
        "benchmark_stale_reason": benchmark_stale_reason,
        "benchmark_stale_next_action": benchmark_stale_next_action,
        "benchmark_stale_age_hours": benchmark_stale_age_hours,
        "benchmark_stale_max_age_hours": benchmark_stale_max_age_hours,
        "benchmark_stale_history_window": int(benchmark_stale_history_window),
        "benchmark_stale_history_window_source": benchmark_stale_history_window_source,
        "benchmark_stale_repeat_threshold": int(benchmark_stale_repeat_threshold),
        "benchmark_stale_repeat_threshold_source": benchmark_stale_repeat_threshold_source,
        "benchmark_stale_rate_ceiling": round(float(benchmark_stale_rate_ceiling), 4),
        "benchmark_stale_rate_ceiling_source": benchmark_stale_rate_ceiling_source,
        "benchmark_stale_rate_consecutive_runs": int(benchmark_stale_rate_consecutive_runs),
        "benchmark_stale_rate_consecutive_runs_source": benchmark_stale_rate_consecutive_runs_source,
    }
    if artifact_source is not None:
        result["artifact_source"] = str(artifact_source).strip() or "unknown"
    return result


def cmd_improvement_knowledge_bootstrap_route_outputs(args: argparse.Namespace) -> None:
    artifact_path = args.artifact_path.resolve()
    artifact_source_value = getattr(args, "artifact_source", None)
    artifact_source = (
        str(artifact_source_value).strip()
        if artifact_source_value is not None and str(artifact_source_value).strip()
        else None
    )
    payload = _build_knowledge_bootstrap_route_outputs_payload(
        artifact_path=artifact_path,
        artifact_source=artifact_source,
    )

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    if bool(getattr(args, "emit_github_output", False)):
        artifact_path_out = str(payload.get("artifact_path") or "").strip() or "none"
        artifact_source_out = str(payload.get("artifact_source") or "unknown").strip() or "unknown"
        status_out = str(payload.get("status") or "warning").strip() or "warning"
        phase_out = str(payload.get("phase") or "unknown").strip() or "unknown"
        route_out = str(payload.get("route") or "noop").strip() or "noop"
        route_blocking_out = int(payload.get("route_blocking") or 0)
        next_action_out = str(payload.get("next_action") or "").strip() or "none"
        next_action_command_out = str(payload.get("next_action_command") or "").strip() or "none"
        benchmark_stale_fallback_out = int(payload.get("benchmark_stale_fallback") or 0)
        benchmark_stale_reason_out = str(payload.get("benchmark_stale_reason") or "").strip() or "none"
        benchmark_stale_next_action_out = str(payload.get("benchmark_stale_next_action") or "").strip() or "none"
        benchmark_stale_age_hours_raw = payload.get("benchmark_stale_age_hours")
        benchmark_stale_age_hours_out = (
            str(_coerce_float(benchmark_stale_age_hours_raw, default=0.0))
            if benchmark_stale_age_hours_raw is not None
            else "none"
        )
        benchmark_stale_max_age_hours_raw = payload.get("benchmark_stale_max_age_hours")
        benchmark_stale_max_age_hours_out = (
            str(_coerce_float(benchmark_stale_max_age_hours_raw, default=0.0))
            if benchmark_stale_max_age_hours_raw is not None
            else "none"
        )
        benchmark_stale_history_window_out = max(1, _coerce_int(payload.get("benchmark_stale_history_window"), default=7))
        benchmark_stale_history_window_source_out = (
            str(payload.get("benchmark_stale_history_window_source") or "").strip() or "builtin_default"
        )
        benchmark_stale_repeat_threshold_out = max(
            1,
            _coerce_int(payload.get("benchmark_stale_repeat_threshold"), default=2),
        )
        benchmark_stale_repeat_threshold_source_out = (
            str(payload.get("benchmark_stale_repeat_threshold_source") or "").strip() or "builtin_default"
        )
        benchmark_stale_rate_ceiling_out = round(
            min(1.0, max(0.0, _coerce_float(payload.get("benchmark_stale_rate_ceiling"), default=0.6))),
            4,
        )
        benchmark_stale_rate_ceiling_source_out = (
            str(payload.get("benchmark_stale_rate_ceiling_source") or "").strip() or "builtin_default"
        )
        benchmark_stale_rate_consecutive_runs_out = max(
            1,
            _coerce_int(payload.get("benchmark_stale_rate_consecutive_runs"), default=2),
        )
        benchmark_stale_rate_consecutive_runs_source_out = (
            str(payload.get("benchmark_stale_rate_consecutive_runs_source") or "").strip() or "builtin_default"
        )
        include_artifact_source = bool(getattr(args, "summary_include_artifact_source", False))

        output_lines = [
            f"artifact_path={artifact_path_out}",
            f"status={status_out}",
            f"phase={phase_out}",
            f"route={route_out}",
            f"route_blocking={route_blocking_out}",
            f"next_action={next_action_out}",
            f"next_action_command={next_action_command_out}",
            f"benchmark_stale_fallback={benchmark_stale_fallback_out}",
            f"benchmark_stale_reason={benchmark_stale_reason_out}",
            f"benchmark_stale_age_hours={benchmark_stale_age_hours_out}",
            f"benchmark_stale_max_age_hours={benchmark_stale_max_age_hours_out}",
            f"benchmark_stale_next_action={benchmark_stale_next_action_out}",
            f"benchmark_stale_history_window={benchmark_stale_history_window_out}",
            f"benchmark_stale_history_window_source={benchmark_stale_history_window_source_out}",
            f"benchmark_stale_repeat_threshold={benchmark_stale_repeat_threshold_out}",
            f"benchmark_stale_repeat_threshold_source={benchmark_stale_repeat_threshold_source_out}",
            f"benchmark_stale_rate_ceiling={benchmark_stale_rate_ceiling_out}",
            f"benchmark_stale_rate_ceiling_source={benchmark_stale_rate_ceiling_source_out}",
            f"benchmark_stale_rate_consecutive_runs={benchmark_stale_rate_consecutive_runs_out}",
            f"benchmark_stale_rate_consecutive_runs_source={benchmark_stale_rate_consecutive_runs_source_out}",
        ]
        if include_artifact_source:
            output_lines.insert(1, f"artifact_source={artifact_source_out}")

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                ]
                if include_artifact_source:
                    summary_lines.append(f"- artifact_source: `{artifact_source_out}`")
                summary_lines.extend(
                    [
                        f"- status: `{status_out}`",
                        f"- phase: `{phase_out}`",
                        f"- route: `{route_out}`",
                        f"- route_blocking: `{route_blocking_out}`",
                        f"- next_action: `{next_action_out}`",
                        f"- next_action_command: `{next_action_command_out}`",
                        f"- benchmark_stale_fallback: `{benchmark_stale_fallback_out}`",
                        f"- benchmark_stale_reason: `{benchmark_stale_reason_out}`",
                        f"- benchmark_stale_age_hours: `{benchmark_stale_age_hours_out}`",
                        f"- benchmark_stale_max_age_hours: `{benchmark_stale_max_age_hours_out}`",
                        f"- benchmark_stale_next_action: `{benchmark_stale_next_action_out}`",
                        f"- benchmark_stale_history_window: `{benchmark_stale_history_window_out}`",
                        f"- benchmark_stale_history_window_source: `{benchmark_stale_history_window_source_out}`",
                        f"- benchmark_stale_repeat_threshold: `{benchmark_stale_repeat_threshold_out}`",
                        f"- benchmark_stale_repeat_threshold_source: `{benchmark_stale_repeat_threshold_source_out}`",
                        f"- benchmark_stale_rate_ceiling: `{benchmark_stale_rate_ceiling_out}`",
                        f"- benchmark_stale_rate_ceiling_source: `{benchmark_stale_rate_ceiling_source_out}`",
                        f"- benchmark_stale_rate_consecutive_runs: `{benchmark_stale_rate_consecutive_runs_out}`",
                        f"- benchmark_stale_rate_consecutive_runs_source: `{benchmark_stale_rate_consecutive_runs_source_out}`",
                        "",
                    ]
                )
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)) and int(payload.get("route_blocking") or 0) != 0:
        raise SystemExit(2)


def cmd_improvement_benchmark_stale_fallback_runtime_alert(args: argparse.Namespace) -> None:
    route_output_path = args.route_output_path.resolve()
    alert_path = (
        args.output_path.resolve()
        if args.output_path is not None
        else (route_output_path.parent / "benchmark_stale_fallback_runtime_alert.json").resolve()
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    history_path = _resolve_benchmark_stale_fallback_history_path(
        raw_value=getattr(args, "history_path", None),
        fallback_base_dir=alert_path.parent,
    )
    history_window = max(1, _coerce_int(getattr(args, "history_window", 7), default=7))
    repeat_threshold = max(1, _coerce_int(getattr(args, "repeat_threshold", 2), default=2))
    rate_ceiling = min(1.0, max(0.0, _coerce_float(getattr(args, "rate_ceiling", 0.6), default=0.6)))
    consecutive_runs = max(1, _coerce_int(getattr(args, "consecutive_runs", 2), default=2))
    db_path = (
        args.db_path.resolve()
        if getattr(args, "db_path", None) is not None
        else (alert_path.parent / "jarvis.db").resolve()
    )

    loaded: dict[str, Any] = {}
    route_missing = not route_output_path.exists()
    if not route_missing:
        parsed = json.loads(route_output_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("invalid_knowledge_bootstrap_route_outputs:expected_json_object")
        loaded = dict(parsed)

    route_status = str(loaded.get("status") or "unknown").strip().lower() if not route_missing else "missing"
    route_phase = str(loaded.get("phase") or "unknown").strip().lower() if not route_missing else "unknown"
    route_value = str(loaded.get("route") or "noop").strip().lower() if not route_missing else "noop"
    benchmark_stale_fallback = (
        _coerce_bool(loaded.get("benchmark_stale_fallback"), default=False)
        if not route_missing
        else False
    )
    benchmark_stale_reason = (
        str(loaded.get("benchmark_stale_reason") or "").strip() if not route_missing else ""
    ) or "none"
    benchmark_stale_next_action = (
        str(loaded.get("benchmark_stale_next_action") or "").strip() if not route_missing else ""
    ) or "none"
    benchmark_stale_age_hours_raw = loaded.get("benchmark_stale_age_hours") if not route_missing else None
    benchmark_stale_age_hours = (
        _coerce_float(benchmark_stale_age_hours_raw, default=0.0)
        if benchmark_stale_age_hours_raw is not None
        else None
    )
    benchmark_stale_max_age_hours_raw = loaded.get("benchmark_stale_max_age_hours") if not route_missing else None
    benchmark_stale_max_age_hours = (
        _coerce_float(benchmark_stale_max_age_hours_raw, default=0.0)
        if benchmark_stale_max_age_hours_raw is not None
        else None
    )
    rerun_command = (
        str(getattr(args, "rerun_command", None)).strip()
        if getattr(args, "rerun_command", None) is not None
        else ""
    )
    if not rerun_command and not route_missing:
        rerun_command = str(loaded.get("next_action_command") or "").strip()
    if not rerun_command:
        rerun_command = "none"

    generated_at = utc_now_iso()
    history_entry = {
        "generated_at": generated_at,
        "route_output_path": str(route_output_path),
        "alert_path": str(alert_path),
        "route_status": route_status,
        "route_phase": route_phase,
        "route": route_value,
        "benchmark_stale_fallback": bool(benchmark_stale_fallback),
        "benchmark_stale_reason": benchmark_stale_reason,
        "benchmark_stale_age_hours": benchmark_stale_age_hours,
        "benchmark_stale_max_age_hours": benchmark_stale_max_age_hours,
        "benchmark_stale_next_action": benchmark_stale_next_action,
        "route_missing": bool(route_missing),
    }
    history_append_error = _append_evidence_runtime_history_row(history_path, history_entry)
    history_summary = _summarize_benchmark_stale_fallback_history(
        history_path=history_path,
        window=history_window,
    )
    if history_append_error:
        history_summary = dict(history_summary)
        history_summary["append_error"] = history_append_error

    recent_stale_runs = max(0, _coerce_int(history_summary.get("recent_stale_runs"), default=0))
    recent_stale_rate = max(0.0, _coerce_float(history_summary.get("recent_stale_rate"), default=0.0))
    history_trend = str(history_summary.get("trend") or "").strip().lower() or "unknown"
    should_alert = bool(benchmark_stale_fallback) and recent_stale_runs >= repeat_threshold
    history_rows = _load_evidence_runtime_history_rows(history_path.resolve())

    def _row_is_stale_fallback(row: dict[str, Any]) -> bool:
        if row.get("benchmark_stale_fallback") is not None:
            return _coerce_bool(row.get("benchmark_stale_fallback"), default=False)
        if row.get("stale_fallback") is not None:
            return _coerce_bool(row.get("stale_fallback"), default=False)
        return False

    def _rolling_stale_rate(rows: list[dict[str, Any]], index: int, window_size: int) -> float:
        if not rows:
            return 0.0
        start = max(0, int(index) - int(window_size) + 1)
        subset = rows[start : index + 1]
        if not subset:
            return 0.0
        stale_runs = sum(1 for item in subset if _row_is_stale_fallback(item))
        return float(stale_runs) / float(len(subset))

    consecutive_rate_breach_runs = 0
    latest_rolling_rate = 0.0
    if history_rows:
        last_index = len(history_rows) - 1
        latest_rolling_rate = _rolling_stale_rate(history_rows, last_index, history_window)
        for index in range(last_index, -1, -1):
            rate_value = _rolling_stale_rate(history_rows, index, history_window)
            if rate_value + 1e-12 >= rate_ceiling:
                consecutive_rate_breach_runs += 1
                continue
            break
    rate_gate_blocking = bool(
        latest_rolling_rate + 1e-12 >= rate_ceiling
        and consecutive_rate_breach_runs >= consecutive_runs
    )
    rate_gate_reason = "none"
    if rate_gate_blocking:
        rate_gate_reason = (
            "benchmark_stale_recent_rate_above_ceiling_consecutive_runs "
            + f"(rate={round(float(latest_rolling_rate), 4)} "
            + f"ceiling={round(float(rate_ceiling), 4)} "
            + f"streak={int(consecutive_rate_breach_runs)} "
            + f"required={int(consecutive_runs)})"
        )
    reason = (
        "benchmark_stale_fallback_repeated"
        + f" recent_stale_runs={recent_stale_runs}"
        + f" repeat_threshold={repeat_threshold}"
        + f" recent_stale_rate={round(float(recent_stale_rate), 4)}"
        + f" route_phase={route_phase}"
        + f" route={route_value}"
    )
    why_now = (
        "repeated stale benchmark fallback means draft prioritization is operating without fresh benchmark context."
    )
    why_not_later = (
        "deferring stale benchmark fallback remediation can compound prioritization drift across operator cycles."
    )

    interrupt_id = ""
    acknowledge_command = "none"
    runtime_error = "none"
    runtime = None
    try:
        if should_alert:
            runtime = JarvisRuntime(
                db_path=db_path,
                repo_path=args.repo_path.resolve(),
            )
            urgency_score = max(0.68, min(0.98, 0.72 + (0.18 * recent_stale_rate)))
            confidence = max(0.7, min(0.98, 0.82 + (0.12 * recent_stale_rate)))
            decision = InterruptDecision(
                interrupt_id=new_id("int"),
                candidate_id=new_id("cand"),
                domain="operations",
                reason=reason,
                urgency_score=urgency_score,
                confidence=confidence,
                suppression_window_hit=False,
                delivered=True,
                why_now=why_now,
                why_not_later=why_not_later,
                status="delivered",
            )
            runtime.interrupt_store.store(decision)
            interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
            interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
            if interrupt_id:
                acknowledge_command = (
                    "python3 -m jarvis.cli interrupts acknowledge "
                    f"{interrupt_id} --actor operator --db-path {db_path}"
                )
            runtime.memory.append_event(
                "improvement.benchmark_stale_fallback_runtime_alert_created",
                {
                    "interrupt_id": interrupt_id or None,
                    "route_output_path": str(route_output_path),
                    "benchmark_stale_fallback": bool(benchmark_stale_fallback),
                    "benchmark_stale_reason": benchmark_stale_reason,
                    "benchmark_stale_age_hours": benchmark_stale_age_hours,
                    "benchmark_stale_max_age_hours": benchmark_stale_max_age_hours,
                    "history_trend": history_trend,
                    "recent_stale_runs": int(recent_stale_runs),
                    "repeat_threshold": int(repeat_threshold),
                    "rerun_command": rerun_command,
                },
            )
    except Exception as exc:
        runtime_error = str(exc).strip() or "unknown_runtime_error"
    finally:
        if runtime is not None:
            runtime.close()

    status = "ok"
    if route_missing:
        status = "warning"
    elif benchmark_stale_fallback:
        status = "warning"

    first_repair_command = (
        acknowledge_command
        if acknowledge_command != "none"
        else rerun_command
    )
    if not first_repair_command:
        first_repair_command = "none"

    payload: dict[str, Any] = {
        "generated_at": generated_at,
        "status": status,
        "route_output_path": str(route_output_path),
        "route_missing": bool(route_missing),
        "route_status": route_status,
        "route_phase": route_phase,
        "route": route_value,
        "benchmark_stale_fallback": bool(benchmark_stale_fallback),
        "benchmark_stale_reason": benchmark_stale_reason,
        "benchmark_stale_age_hours": benchmark_stale_age_hours,
        "benchmark_stale_max_age_hours": benchmark_stale_max_age_hours,
        "benchmark_stale_next_action": benchmark_stale_next_action,
        "repeat_threshold": int(repeat_threshold),
        "rate_ceiling": round(float(rate_ceiling), 4),
        "consecutive_runs": int(consecutive_runs),
        "should_alert": bool(should_alert),
        "alert_created": bool(interrupt_id),
        "rate_gate_blocking": bool(rate_gate_blocking),
        "rate_gate_reason": rate_gate_reason,
        "consecutive_rate_breach_runs": int(consecutive_rate_breach_runs),
        "latest_rolling_rate": round(float(latest_rolling_rate), 4),
        "interrupt_id": interrupt_id or None,
        "interrupt_db_path": str(db_path),
        "acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
        "rerun_command": None if rerun_command == "none" else rerun_command,
        "first_repair_command": None if first_repair_command == "none" else first_repair_command,
        "reason": reason,
        "why_now": why_now,
        "why_not_later": why_not_later,
        "runtime_error": None if runtime_error == "none" else runtime_error,
        "benchmark_stale_fallback_history_path": str(history_path),
        "benchmark_stale_fallback_history_window": int(history_window),
        "benchmark_stale_fallback_history": history_summary,
    }
    alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    payload["benchmark_stale_runtime_alert_path"] = str(alert_path)
    payload["benchmark_stale_runtime_interrupt_id"] = interrupt_id or "none"
    payload["benchmark_stale_runtime_alert_created"] = 1 if interrupt_id else 0
    payload["benchmark_stale_runtime_should_alert"] = 1 if should_alert else 0
    payload["benchmark_stale_runtime_acknowledge_command"] = acknowledge_command
    payload["benchmark_stale_runtime_rerun_command"] = rerun_command
    payload["benchmark_stale_runtime_first_repair_command"] = first_repair_command or "none"
    payload["benchmark_stale_runtime_error"] = runtime_error
    payload["benchmark_stale_runtime_history_trend"] = history_trend
    payload["benchmark_stale_recent_count"] = int(recent_stale_runs)
    payload["benchmark_stale_recent_rate"] = round(float(recent_stale_rate), 4)
    payload["benchmark_stale_repeat_threshold"] = int(repeat_threshold)
    payload["benchmark_stale_rate_ceiling"] = round(float(rate_ceiling), 4)
    payload["benchmark_stale_rate_consecutive_runs"] = int(consecutive_runs)
    payload["benchmark_stale_runtime_consecutive_rate_breach_runs"] = int(consecutive_rate_breach_runs)
    payload["benchmark_stale_runtime_latest_rolling_rate"] = round(float(latest_rolling_rate), 4)
    payload["benchmark_stale_runtime_rate_gate_blocking"] = 1 if rate_gate_blocking else 0
    payload["benchmark_stale_runtime_rate_gate_reason"] = rate_gate_reason
    payload["benchmark_stale_fallback_current"] = 1 if benchmark_stale_fallback else 0
    payload["benchmark_stale_history_append_error"] = history_append_error

    if bool(getattr(args, "emit_github_output", False)):
        alert_path_out = str(payload.get("benchmark_stale_runtime_alert_path") or str(alert_path)).strip() or str(alert_path)
        interrupt_id_out = (
            str(payload.get("benchmark_stale_runtime_interrupt_id") or payload.get("interrupt_id") or "none").strip()
            or "none"
        )
        alert_created_out = _coerce_int(
            payload.get("benchmark_stale_runtime_alert_created")
            if payload.get("benchmark_stale_runtime_alert_created") is not None
            else payload.get("alert_created"),
            default=0,
        )
        should_alert_out = _coerce_int(payload.get("benchmark_stale_runtime_should_alert"), default=0)
        acknowledge_out = (
            str(
                payload.get("benchmark_stale_runtime_acknowledge_command")
                or payload.get("acknowledge_command")
                or "none"
            ).strip()
            or "none"
        )
        rerun_out = (
            str(payload.get("benchmark_stale_runtime_rerun_command") or payload.get("rerun_command") or "none").strip()
            or "none"
        )
        first_repair_out = (
            str(
                payload.get("benchmark_stale_runtime_first_repair_command")
                or payload.get("first_repair_command")
                or ""
            ).strip()
        )
        if not first_repair_out:
            first_repair_out = acknowledge_out if acknowledge_out != "none" else rerun_out
        first_repair_out = first_repair_out or "none"
        runtime_error_out = (
            str(payload.get("benchmark_stale_runtime_error") or payload.get("runtime_error") or "none").strip()
            or "none"
        )
        history_trend_out = str(payload.get("benchmark_stale_runtime_history_trend") or history_trend).strip() or "none"
        recent_count_out = _coerce_int(payload.get("benchmark_stale_recent_count"), default=recent_stale_runs)
        recent_rate_out = round(_coerce_float(payload.get("benchmark_stale_recent_rate"), default=recent_stale_rate), 4)
        repeat_threshold_out = _coerce_int(payload.get("benchmark_stale_repeat_threshold"), default=repeat_threshold)
        rate_ceiling_out = round(_coerce_float(payload.get("benchmark_stale_rate_ceiling"), default=rate_ceiling), 4)
        consecutive_runs_out = _coerce_int(payload.get("benchmark_stale_rate_consecutive_runs"), default=consecutive_runs)
        consecutive_rate_breach_runs_out = _coerce_int(
            payload.get("benchmark_stale_runtime_consecutive_rate_breach_runs"),
            default=consecutive_rate_breach_runs,
        )
        latest_rolling_rate_out = round(
            _coerce_float(payload.get("benchmark_stale_runtime_latest_rolling_rate"), default=latest_rolling_rate),
            4,
        )
        rate_gate_blocking_out = _coerce_int(
            payload.get("benchmark_stale_runtime_rate_gate_blocking"),
            default=1 if rate_gate_blocking else 0,
        )
        rate_gate_reason_out = (
            str(payload.get("benchmark_stale_runtime_rate_gate_reason") or rate_gate_reason).strip()
            or "none"
        )
        stale_current_out = _coerce_int(payload.get("benchmark_stale_fallback_current"), default=0)

        output_lines = [
            f"benchmark_stale_runtime_alert_path={alert_path_out}",
            f"benchmark_stale_runtime_interrupt_id={interrupt_id_out}",
            f"benchmark_stale_runtime_alert_created={alert_created_out}",
            f"benchmark_stale_runtime_should_alert={should_alert_out}",
            f"benchmark_stale_runtime_acknowledge_command={acknowledge_out}",
            f"benchmark_stale_runtime_rerun_command={rerun_out}",
            f"benchmark_stale_runtime_first_repair_command={first_repair_out}",
            f"benchmark_stale_runtime_error={runtime_error_out}",
            f"benchmark_stale_runtime_history_trend={history_trend_out}",
            f"benchmark_stale_recent_count={recent_count_out}",
            f"benchmark_stale_recent_rate={recent_rate_out}",
            f"benchmark_stale_repeat_threshold={repeat_threshold_out}",
            f"benchmark_stale_rate_ceiling={rate_ceiling_out}",
            f"benchmark_stale_rate_consecutive_runs={consecutive_runs_out}",
            f"benchmark_stale_runtime_consecutive_rate_breach_runs={consecutive_rate_breach_runs_out}",
            f"benchmark_stale_runtime_latest_rolling_rate={latest_rolling_rate_out}",
            f"benchmark_stale_runtime_rate_gate_blocking={rate_gate_blocking_out}",
            f"benchmark_stale_runtime_rate_gate_reason={rate_gate_reason_out}",
            f"benchmark_stale_fallback_current={stale_current_out}",
        ]

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- interrupt_id: `{interrupt_id_out}`",
                    f"- alert_created: `{alert_created_out}`",
                    f"- should_alert: `{should_alert_out}`",
                    f"- stale_fallback_current: `{stale_current_out}`",
                    f"- history_trend: `{history_trend_out}`",
                    f"- recent_stale_count: `{recent_count_out}`",
                    f"- recent_stale_rate: `{recent_rate_out}`",
                    f"- repeat_threshold: `{repeat_threshold_out}`",
                    f"- rate_ceiling: `{rate_ceiling_out}`",
                    f"- consecutive_runs: `{consecutive_runs_out}`",
                    f"- consecutive_rate_breach_runs: `{consecutive_rate_breach_runs_out}`",
                    f"- latest_rolling_rate: `{latest_rolling_rate_out}`",
                    f"- rate_gate_blocking: `{rate_gate_blocking_out}`",
                    f"- rate_gate_reason: `{rate_gate_reason_out}`",
                    f"- acknowledge_command: `{acknowledge_out}`",
                    f"- rerun_command: `{rerun_out}`",
                    f"- first_repair_command: `{first_repair_out}`",
                    f"- runtime_error: `{runtime_error_out}`",
                    "",
                ]
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)):
        if route_missing:
            raise SystemExit(2)
        if should_alert and (runtime_error != "none" or not bool(interrupt_id)):
            raise SystemExit(2)


def _build_evidence_lookup_batch_outputs_payload(
    *,
    report_path: Path,
    report_source: str | None = None,
) -> dict[str, Any]:
    resolved_report_path = report_path.resolve()
    if not resolved_report_path.exists():
        result = {
            "generated_at": utc_now_iso(),
            "report_path": str(resolved_report_path),
            "status": "warning",
            "reason": "operator_cycle_report_missing",
            "blocking": 1,
            "report_status": "unknown",
            "source_report_type": "missing_report",
            "ready": 0,
            "record_ids": [],
            "record_count": 0,
            "first_record_id": "none",
            "record_ids_csv": "none",
            "command": "none",
            "evidence_report_path": "none",
            "next_action": "operator cycle report missing; rerun operator-cycle before evidence lookup batch.",
            "has_explicit_batch": 0,
        }
        if report_source is not None:
            result["report_source"] = str(report_source).strip() or "unknown"
        return result

    loaded = json.loads(resolved_report_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("invalid_operator_cycle_report:expected_json_object")

    source_report_type = (
        "operator_cycle_report"
        if isinstance(loaded.get("summary"), dict)
        or str(loaded.get("operator_report_path") or "").strip()
        or str(loaded.get("inbox_summary_path") or "").strip()
        else "operator_inbox_summary"
    )
    report_status = str(loaded.get("status") or "unknown").strip() or "unknown"
    has_explicit_batch = isinstance(loaded.get("evidence_lookup_batch"), dict)
    batch_block = dict(loaded.get("evidence_lookup_batch") or {})

    record_ids = _normalize_record_id_list(batch_block.get("record_ids"))
    if not record_ids:
        record_ids = _extract_evidence_record_ids_from_payload(loaded)
    record_count = _coerce_int(batch_block.get("record_count"), default=len(record_ids))
    if record_count <= 0 or record_count < len(record_ids):
        record_count = len(record_ids)
    first_record_id = record_ids[0] if record_ids else "none"
    record_ids_csv = ",".join(record_ids) if record_ids else "none"

    evidence_report_path = "none"
    evidence_report_path_value: Path | None = None
    evidence_report_path_raw = str(
        batch_block.get("output_path") or loaded.get("evidence_lookup_report_path") or ""
    ).strip()
    if evidence_report_path_raw:
        evidence_report_path_value = Path(evidence_report_path_raw).expanduser()
        if not evidence_report_path_value.is_absolute():
            evidence_report_path_value = (Path.cwd() / evidence_report_path_value).resolve()
        else:
            evidence_report_path_value = evidence_report_path_value.resolve()
        evidence_report_path = str(evidence_report_path_value)

    config_path_value: Path | None = None
    config_path_raw = str(loaded.get("config_path") or "").strip()
    if config_path_raw:
        config_path_value = Path(config_path_raw).expanduser()
        if not config_path_value.is_absolute():
            config_path_value = (Path.cwd() / config_path_value).resolve()
        else:
            config_path_value = config_path_value.resolve()

    command = str(batch_block.get("command") or "").strip()
    if (not command or command == "none") and config_path_value is not None and record_ids:
        command = _build_operator_evidence_lookup_command(
            config_path=config_path_value,
            record_ids=record_ids,
            output_path=evidence_report_path_value,
        )
    command = command or "none"

    ready = bool(batch_block.get("ready"))
    if not has_explicit_batch:
        ready = bool(record_ids and command != "none")
    ready = bool(ready and record_ids and command != "none")

    next_action = str(batch_block.get("next_action") or "").strip()
    if not next_action:
        next_action = (
            "Run the batch evidence lookup command to resolve unresolved blocker/retest source records."
            if ready
            else "No unresolved blocker/retest evidence record IDs detected."
        )

    status = "ok"
    reason = "ok" if has_explicit_batch else "derived_from_report_without_explicit_batch"
    blocking = 0
    if record_ids and command == "none":
        status = "warning"
        reason = "missing_evidence_lookup_command_for_nonempty_record_ids"
        blocking = 1

    result = {
        "generated_at": utc_now_iso(),
        "report_path": str(resolved_report_path),
        "status": status,
        "reason": reason,
        "blocking": int(blocking),
        "report_status": report_status,
        "source_report_type": source_report_type,
        "ready": 1 if ready else 0,
        "record_ids": list(record_ids),
        "record_count": int(record_count),
        "first_record_id": first_record_id,
        "record_ids_csv": record_ids_csv,
        "command": command,
        "evidence_report_path": evidence_report_path,
        "next_action": next_action,
        "has_explicit_batch": 1 if has_explicit_batch else 0,
    }
    if report_source is not None:
        result["report_source"] = str(report_source).strip() or "unknown"
    return result


def cmd_improvement_evidence_lookup_batch_outputs(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    report_source_value = getattr(args, "report_source", None)
    report_source = (
        str(report_source_value).strip()
        if report_source_value is not None and str(report_source_value).strip()
        else None
    )
    payload = _build_evidence_lookup_batch_outputs_payload(
        report_path=report_path,
        report_source=report_source,
    )

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    if bool(getattr(args, "emit_github_output", False)):
        report_path_out = str(payload.get("report_path") or "").strip() or "none"
        report_source_out = str(payload.get("report_source") or "unknown").strip() or "unknown"
        status_out = str(payload.get("status") or "warning").strip() or "warning"
        reason_out = str(payload.get("reason") or "unknown").strip() or "unknown"
        blocking_out = int(payload.get("blocking") or 0)
        report_status_out = str(payload.get("report_status") or "unknown").strip() or "unknown"
        ready_out = int(payload.get("ready") or 0)
        record_count_out = int(payload.get("record_count") or 0)
        first_record_id_out = str(payload.get("first_record_id") or "none").strip() or "none"
        record_ids_csv_out = str(payload.get("record_ids_csv") or "none").strip() or "none"
        command_out = str(payload.get("command") or "none").strip() or "none"
        evidence_report_path_out = str(payload.get("evidence_report_path") or "none").strip() or "none"
        next_action_out = str(payload.get("next_action") or "none").strip() or "none"
        include_report_source = bool(getattr(args, "summary_include_report_source", False))
        include_record_ids = bool(getattr(args, "summary_include_record_ids", False))

        output_lines = [
            f"report_path={report_path_out}",
            f"status={status_out}",
            f"reason={reason_out}",
            f"blocking={blocking_out}",
            f"report_status={report_status_out}",
            f"evidence_lookup_ready={ready_out}",
            f"ready={ready_out}",
            f"evidence_lookup_record_count={record_count_out}",
            f"record_count={record_count_out}",
            f"evidence_lookup_first_record_id={first_record_id_out}",
            f"first_record_id={first_record_id_out}",
            f"evidence_lookup_record_ids_csv={record_ids_csv_out}",
            f"evidence_lookup_command={command_out}",
            f"command={command_out}",
            f"evidence_lookup_report_path={evidence_report_path_out}",
            f"next_action={next_action_out}",
        ]
        if include_report_source:
            output_lines.insert(1, f"report_source={report_source_out}")

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                ]
                if include_report_source:
                    summary_lines.append(f"- report_source: `{report_source_out}`")
                summary_lines.extend(
                    [
                        f"- status: `{status_out}`",
                        f"- reason: `{reason_out}`",
                        f"- blocking: `{blocking_out}`",
                        f"- report_status: `{report_status_out}`",
                        f"- ready: `{ready_out}`",
                        f"- record_count: `{record_count_out}`",
                        f"- first_record_id: `{first_record_id_out}`",
                    ]
                )
                if include_record_ids:
                    summary_lines.append(f"- record_ids_csv: `{record_ids_csv_out}`")
                summary_lines.extend(
                    [
                        f"- command: `{command_out}`",
                        f"- evidence_report_path: `{evidence_report_path_out}`",
                        f"- next_action: `{next_action_out}`",
                        "",
                    ]
                )
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)) and int(payload.get("blocking") or 0) != 0:
        raise SystemExit(2)


def cmd_improvement_evidence_lookup_runtime_alert(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    alert_path = (
        args.output_path.resolve()
        if args.output_path is not None
        else (report_path.parent / "evidence_lookup_runtime_alert.json").resolve()
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    history_path = _resolve_evidence_runtime_history_path(
        raw_value=getattr(args, "history_path", None),
        fallback_base_dir=alert_path.parent,
    )
    history_window = max(1, _coerce_int(getattr(args, "history_window", 7), default=7))
    db_path = (
        args.db_path.resolve()
        if getattr(args, "db_path", None) is not None
        else (alert_path.parent / "jarvis.db").resolve()
    )

    loaded: dict[str, Any] = {}
    report_missing = not report_path.exists()
    if not report_missing:
        parsed = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("invalid_evidence_lookup_report:expected_json_object")
        loaded = dict(parsed)

    lookup_status = (
        "missing_report"
        if report_missing
        else str(loaded.get("status") or "unknown").strip().lower() or "unknown"
    )
    record_ids = _normalize_record_id_list(loaded.get("record_ids"))
    requested_count = (
        _coerce_int(loaded.get("requested_count"), default=len(record_ids))
        if not report_missing
        else 0
    )
    if requested_count <= 0:
        requested_count = len(record_ids)
    missing_record_ids = _normalize_record_id_list(loaded.get("missing_record_ids"))
    missing_count = (
        _coerce_int(loaded.get("missing_count"), default=len(missing_record_ids))
        if not report_missing
        else 0
    )
    if missing_count <= 0 or missing_count < len(missing_record_ids):
        missing_count = len(missing_record_ids)
    first_missing_record_id = missing_record_ids[0] if missing_record_ids else "none"
    missing_record_ids_csv = ",".join(missing_record_ids) if missing_record_ids else "none"
    resolved_record_id_count = (
        _coerce_int(loaded.get("resolved_record_id_count"), default=max(0, requested_count - missing_count))
        if not report_missing
        else 0
    )

    rerun_command = (
        str(getattr(args, "rerun_command", None)).strip()
        if getattr(args, "rerun_command", None) is not None
        else ""
    )
    if not rerun_command and not report_missing:
        config_path_raw = str(loaded.get("config_path") or "").strip()
        if config_path_raw and record_ids:
            config_path = Path(config_path_raw).expanduser()
            if not config_path.is_absolute():
                config_path = (Path.cwd() / config_path).resolve()
            else:
                config_path = config_path.resolve()
            rerun_command = _build_operator_evidence_lookup_command(
                config_path=config_path,
                record_ids=record_ids,
                output_path=report_path,
            )
    if not rerun_command:
        rerun_command = "none"

    reason = (
        "evidence_lookup_unresolved_records"
        + f" missing_count={missing_count}"
        + f" missing_record_ids={missing_record_ids_csv}"
        + f" lookup_status={lookup_status}"
    )
    why_now = (
        "unresolved evidence record IDs block source-grounded triage for blocker and retest decisions."
    )
    why_not_later = (
        "deferring unresolved evidence records can compound hypothesis drift and hide repeated source gaps."
    )

    interrupt_id = ""
    acknowledge_command = "none"
    runtime_error = "none"
    runtime = None
    try:
        if missing_count > 0:
            runtime = JarvisRuntime(
                db_path=db_path,
                repo_path=args.repo_path.resolve(),
            )
            missing_rate = float(missing_count) / float(max(1, requested_count))
            urgency_score = max(0.7, min(0.98, 0.78 + (0.2 * missing_rate)))
            confidence = max(0.7, min(0.98, 0.84 + (0.12 * missing_rate)))
            decision = InterruptDecision(
                interrupt_id=new_id("int"),
                candidate_id=new_id("cand"),
                domain="operations",
                reason=reason,
                urgency_score=urgency_score,
                confidence=confidence,
                suppression_window_hit=False,
                delivered=True,
                why_now=why_now,
                why_not_later=why_not_later,
                status="delivered",
            )
            runtime.interrupt_store.store(decision)
            interrupt = runtime.interrupt_store.get(decision.interrupt_id) or decision.to_dict()
            interrupt_id = str(interrupt.get("interrupt_id") or "").strip()
            if interrupt_id:
                acknowledge_command = (
                    "python3 -m jarvis.cli interrupts acknowledge "
                    f"{interrupt_id} --actor operator --db-path {db_path}"
                )
            runtime.memory.append_event(
                "improvement.evidence_lookup_runtime_alert_created",
                {
                    "interrupt_id": interrupt_id or None,
                    "report_path": str(report_path),
                    "lookup_status": lookup_status,
                    "requested_count": requested_count,
                    "resolved_record_id_count": resolved_record_id_count,
                    "missing_count": missing_count,
                    "missing_record_ids": missing_record_ids,
                    "rerun_command": rerun_command,
                },
            )
    except Exception as exc:
        runtime_error = str(exc).strip() or "unknown_runtime_error"
    finally:
        if runtime is not None:
            runtime.close()

    status = "ok"
    if report_missing:
        status = "warning"
    elif missing_count > 0:
        status = "warning"

    first_repair_command = (
        acknowledge_command
        if acknowledge_command != "none"
        else rerun_command
    )
    if not first_repair_command:
        first_repair_command = "none"
    generated_at = utc_now_iso()

    history_entry = {
        "generated_at": generated_at,
        "report_path": str(report_path),
        "alert_path": str(alert_path),
        "status": status,
        "lookup_status": lookup_status,
        "report_missing": bool(report_missing),
        "requested_count": int(requested_count),
        "resolved_record_id_count": int(resolved_record_id_count),
        "missing_count": int(missing_count),
        "missing_record_ids": list(missing_record_ids),
        "missing_record_ids_csv": missing_record_ids_csv,
        "first_missing_record_id": first_missing_record_id,
        "alert_created": bool(interrupt_id),
        "interrupt_id": interrupt_id or None,
        "runtime_error": None if runtime_error == "none" else runtime_error,
    }
    history_append_error = _append_evidence_runtime_history_row(history_path, history_entry)
    history_summary = _summarize_evidence_runtime_history(
        history_path=history_path,
        window=history_window,
    )
    if history_append_error:
        history_summary = dict(history_summary)
        history_summary["append_error"] = history_append_error

    payload: dict[str, Any] = {
        "generated_at": generated_at,
        "status": status,
        "report_path": str(report_path),
        "lookup_status": lookup_status,
        "report_missing": bool(report_missing),
        "requested_count": int(requested_count),
        "resolved_record_id_count": int(resolved_record_id_count),
        "missing_count": int(missing_count),
        "missing_record_ids": missing_record_ids,
        "missing_record_ids_csv": missing_record_ids_csv,
        "first_missing_record_id": first_missing_record_id,
        "alert_created": bool(interrupt_id),
        "interrupt_id": interrupt_id or None,
        "interrupt_db_path": str(db_path),
        "acknowledge_command": None if acknowledge_command == "none" else acknowledge_command,
        "rerun_command": None if rerun_command == "none" else rerun_command,
        "first_repair_command": None if first_repair_command == "none" else first_repair_command,
        "reason": reason,
        "why_now": why_now,
        "why_not_later": why_not_later,
        "runtime_error": None if runtime_error == "none" else runtime_error,
        "evidence_lookup_runtime_history_path": str(history_path),
        "evidence_lookup_runtime_history_window": int(history_window),
        "evidence_lookup_runtime_history": history_summary,
    }
    alert_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    payload["evidence_lookup_runtime_alert_path"] = str(alert_path)
    payload["evidence_lookup_runtime_interrupt_id"] = interrupt_id or "none"
    payload["evidence_lookup_runtime_alert_created"] = 1 if interrupt_id else 0
    payload["evidence_lookup_runtime_acknowledge_command"] = acknowledge_command
    payload["evidence_lookup_runtime_rerun_command"] = rerun_command
    payload["evidence_lookup_runtime_first_repair_command"] = first_repair_command or "none"
    payload["evidence_lookup_runtime_error"] = runtime_error
    payload["evidence_lookup_missing_count"] = int(missing_count)
    payload["evidence_lookup_first_missing_record_id"] = first_missing_record_id
    payload["evidence_lookup_missing_record_ids_csv"] = missing_record_ids_csv
    payload["evidence_lookup_runtime_history_trend"] = str(history_summary.get("trend") or "")
    payload["evidence_lookup_runtime_priority_boost"] = _coerce_float(
        history_summary.get("priority_boost"),
        default=0.0,
    )
    payload["evidence_lookup_runtime_history_append_error"] = history_append_error

    if bool(getattr(args, "emit_github_output", False)):
        alert_path_out = str(payload.get("evidence_lookup_runtime_alert_path") or str(alert_path)).strip() or str(alert_path)
        interrupt_id_out = (
            str(payload.get("evidence_lookup_runtime_interrupt_id") or payload.get("interrupt_id") or "none").strip()
            or "none"
        )
        alert_created_out = _coerce_int(
            payload.get("evidence_lookup_runtime_alert_created")
            if payload.get("evidence_lookup_runtime_alert_created") is not None
            else payload.get("alert_created"),
            default=0,
        )
        acknowledge_out = (
            str(
                payload.get("evidence_lookup_runtime_acknowledge_command")
                or payload.get("acknowledge_command")
                or "none"
            ).strip()
            or "none"
        )
        rerun_out = (
            str(payload.get("evidence_lookup_runtime_rerun_command") or payload.get("rerun_command") or "none").strip()
            or "none"
        )
        first_repair_out = (
            str(
                payload.get("evidence_lookup_runtime_first_repair_command")
                or payload.get("first_repair_command")
                or ""
            ).strip()
        )
        if not first_repair_out:
            first_repair_out = acknowledge_out if acknowledge_out != "none" else rerun_out
        first_repair_out = first_repair_out or "none"
        runtime_error_out = (
            str(payload.get("evidence_lookup_runtime_error") or payload.get("runtime_error") or "none").strip()
            or "none"
        )
        missing_count_out = _coerce_int(payload.get("evidence_lookup_missing_count"), default=missing_count)
        first_missing_record_out = (
            str(payload.get("evidence_lookup_first_missing_record_id") or first_missing_record_id).strip()
            or "none"
        )
        missing_record_ids_csv_out = (
            str(payload.get("evidence_lookup_missing_record_ids_csv") or missing_record_ids_csv).strip()
            or "none"
        )
        history_trend_out = str(payload.get("evidence_lookup_runtime_history_trend") or "none").strip() or "none"
        history_priority_boost_out = round(
            _coerce_float(payload.get("evidence_lookup_runtime_priority_boost"), default=0.0),
            4,
        )

        output_lines = [
            f"evidence_lookup_runtime_alert_path={alert_path_out}",
            f"evidence_lookup_runtime_interrupt_id={interrupt_id_out}",
            f"evidence_lookup_runtime_alert_created={alert_created_out}",
            f"evidence_lookup_runtime_acknowledge_command={acknowledge_out}",
            f"evidence_lookup_runtime_rerun_command={rerun_out}",
            f"evidence_lookup_runtime_first_repair_command={first_repair_out}",
            f"evidence_lookup_runtime_error={runtime_error_out}",
            f"evidence_lookup_missing_count={missing_count_out}",
            f"evidence_lookup_first_missing_record_id={first_missing_record_out}",
            f"evidence_lookup_missing_record_ids_csv={missing_record_ids_csv_out}",
            f"evidence_lookup_runtime_history_trend={history_trend_out}",
            f"evidence_lookup_runtime_priority_boost={history_priority_boost_out}",
        ]

        github_output = str(os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(output_lines) + "\n")

        summary_heading_raw = str(getattr(args, "summary_heading", "") or "").strip()
        if summary_heading_raw:
            github_step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
            if github_step_summary:
                summary_path = Path(github_step_summary).expanduser()
                summary_lines = [
                    f"## {summary_heading_raw}",
                    "",
                    f"- interrupt_id: `{interrupt_id_out}`",
                    f"- alert_created: `{alert_created_out}`",
                    f"- missing_count: `{missing_count_out}`",
                    f"- first_missing_record_id: `{first_missing_record_out}`",
                    f"- missing_record_ids_csv: `{missing_record_ids_csv_out}`",
                    f"- runtime_history_trend: `{history_trend_out}`",
                    f"- runtime_priority_boost: `{history_priority_boost_out}`",
                    f"- acknowledge_command: `{acknowledge_out}`",
                    f"- rerun_command: `{rerun_out}`",
                    f"- first_repair_command: `{first_repair_out}`",
                    f"- runtime_error: `{runtime_error_out}`",
                    "",
                ]
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(summary_lines) + "\n")

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if bool(getattr(args, "strict", False)):
        if report_missing:
            raise SystemExit(2)
        if missing_count > 0 and (runtime_error != "none" or not bool(interrupt_id)):
            raise SystemExit(2)


def cmd_improvement_benchmark_frustrations(args: argparse.Namespace) -> None:
    report_path = args.report_path.resolve()
    daily_report, resolved_daily_report_path, operator_payload = _resolve_daily_report_from_improvement_report(
        report_path=report_path
    )
    top_limit = max(1, int(getattr(args, "top_limit", 10) or 10))
    evidence_runtime_history_window = max(
        1,
        _coerce_int(getattr(args, "evidence_runtime_history_window", 7), default=7),
    )
    evidence_runtime_history_path_value = getattr(args, "evidence_runtime_history_path", None)
    if evidence_runtime_history_path_value is None:
        evidence_runtime_history_path_value = operator_payload.get("evidence_runtime_history_path")
    evidence_runtime_history_path = _resolve_evidence_runtime_history_path(
        raw_value=evidence_runtime_history_path_value,
        fallback_base_dir=report_path.parent,
        resolve_relative_to=report_path.parent,
    )
    evidence_runtime_history = _summarize_evidence_runtime_history(
        history_path=evidence_runtime_history_path,
        window=evidence_runtime_history_window,
    )
    evidence_runtime_priority_boost = max(
        0.0,
        _coerce_float(evidence_runtime_history.get("priority_boost"), default=0.0),
    )
    evidence_runtime_trend = str(evidence_runtime_history.get("trend") or "").strip().lower()
    evidence_runtime_multiplier = 1.0 + min(0.75, evidence_runtime_priority_boost)

    recurring_rows = _collect_recurring_pain_rows(operator_payload)
    hypothesis_context_by_id = _collect_seed_hypothesis_context(operator_payload)
    implementation_rows = _collect_implementation_outcomes(
        daily_report,
        hypothesis_context_by_id=hypothesis_context_by_id,
    )
    implementation_by_key = {
        f"{str(row.get('domain') or '')}:{str(row.get('friction_key') or '')}": row
        for row in implementation_rows
        if str(row.get("domain") or "") and str(row.get("friction_key") or "")
    }
    implementation_by_hypothesis_id: dict[str, dict[str, Any]] = {}
    for row in implementation_rows:
        for hypothesis_id in [str(item) for item in list(row.get("hypothesis_ids") or []) if str(item)]:
            implementation_by_hypothesis_id.setdefault(hypothesis_id, row)

    recurring_ranked = sorted(
        recurring_rows,
        key=lambda row: (
            -int(row.get("recurrence_score") or 0),
            -float(row.get("impact_score_current") or 0.0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        ),
    )
    trend_ranked = sorted(
        recurring_rows,
        key=lambda row: (
            -float(row.get("trend_acceleration") or 0.0),
            -float(row.get("impact_score_delta") or 0.0),
            -int(row.get("recurrence_score") or 0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        ),
    )
    win_rate_ranked = sorted(
        implementation_rows,
        key=lambda row: (
            -float(row.get("win_rate") or 0.0),
            -int(row.get("run_count") or 0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        ),
    )
    laggard_ranked = sorted(
        implementation_rows,
        key=lambda row: (
            float(row.get("win_rate") or 0.0),
            -int(row.get("run_count") or 0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        ),
    )

    priority_rows: list[dict[str, Any]] = []
    for row in recurring_rows:
        domain = str(row.get("domain") or "").strip().lower()
        friction_key = str(row.get("friction_key") or "").strip()
        if not domain or not friction_key:
            continue
        key = f"{domain}:{friction_key}"
        implementation = dict(implementation_by_key.get(key) or {})
        match_strategy = "domain_friction_key"
        matched_hypothesis_id: str | None = None
        if not implementation:
            for hypothesis_id in [str(item) for item in list(row.get("hypothesis_ids") or []) if str(item)]:
                linked = implementation_by_hypothesis_id.get(hypothesis_id)
                if isinstance(linked, dict):
                    implementation = dict(linked)
                    matched_hypothesis_id = hypothesis_id
                    match_strategy = "hypothesis_id"
                    break
        implementation_run_count = int(implementation.get("run_count") or 0)
        raw_win_rate = _coerce_float(implementation.get("win_rate"), default=0.0)
        adjusted_win_rate = _coerce_float(implementation.get("adjusted_win_rate"), default=raw_win_rate)
        confidence_score = _coerce_float(implementation.get("confidence_score"), default=0.0)
        if implementation_run_count <= 0:
            adjusted_win_rate = 0.0
            confidence_score = 0.0
        recurrence_score = max(0, _coerce_int(row.get("recurrence_score"), default=0))
        trend_boost = 1.25 if str(row.get("trend") or "") in {"new", "rising"} else 1.0
        trend_delta = max(0.0, _coerce_float(row.get("impact_score_delta"), default=0.0))
        uncertainty_bonus = max(0.0, (1.0 - confidence_score) * 0.25) if implementation_run_count > 0 else 0.0
        effective_gap = max(0.0, (1.0 - adjusted_win_rate) + uncertainty_bonus)
        opportunity_score = (float(recurrence_score) + trend_delta) * trend_boost * effective_gap
        opportunity_score = opportunity_score * evidence_runtime_multiplier
        priority_rows.append(
            {
                "domain": domain,
                "friction_key": friction_key,
                "canonical_key": row.get("canonical_key"),
                "trend": row.get("trend"),
                "recurrence_score": int(recurrence_score),
                "impact_score_current": round(_coerce_float(row.get("impact_score_current"), default=0.0), 4),
                "impact_score_delta": round(_coerce_float(row.get("impact_score_delta"), default=0.0), 4),
                "trend_acceleration": round(_coerce_float(row.get("trend_acceleration"), default=0.0), 4),
                "implementation_run_count": implementation_run_count,
                "implementation_win_rate": round(float(raw_win_rate), 4),
                "implementation_adjusted_win_rate": round(float(adjusted_win_rate), 4),
                "implementation_confidence_score": round(float(confidence_score), 4),
                "implementation_promote_count": int(implementation.get("promote_count") or 0),
                "implementation_guardrail_block_count": int(implementation.get("blocked_guardrail_count") or 0),
                "implementation_match_strategy": match_strategy if implementation else None,
                "implementation_matched_hypothesis_id": matched_hypothesis_id,
                "implementation_effective_gap": round(float(effective_gap), 4),
                "evidence_runtime_trend": evidence_runtime_trend or None,
                "evidence_runtime_priority_boost": round(float(evidence_runtime_priority_boost), 4),
                "opportunity_score": round(float(opportunity_score), 4),
            }
        )
    priority_rows.sort(
        key=lambda row: (
            -float(row.get("opportunity_score") or 0.0),
            -int(row.get("recurrence_score") or 0),
            str(row.get("domain") or ""),
            str(row.get("friction_key") or ""),
        )
    )

    recurring_top = recurring_ranked[:top_limit]
    trend_top = trend_ranked[:top_limit]
    win_rate_top = win_rate_ranked[:top_limit]
    laggard_top = laggard_ranked[:top_limit]
    priority_top = priority_rows[:top_limit]

    domains = sorted(
        {
            str(row.get("domain") or "").strip().lower()
            for row in [*recurring_rows, *implementation_rows]
            if str(row.get("domain") or "").strip()
        }
    )
    data_gaps: list[str] = []
    if not recurring_rows:
        data_gaps.append("missing_recurring_pain_rows")
    if not implementation_rows:
        data_gaps.append("missing_implementation_outcomes")

    summary = {
        "domain_count": len(domains),
        "domains": domains,
        "recurring_pain_count": len(recurring_rows),
        "implementation_count": len(implementation_rows),
        "priority_item_count": len(priority_rows),
        "avg_implementation_win_rate": round(
            (
                sum(_coerce_float(row.get("win_rate"), default=0.0) for row in implementation_rows)
                / len(implementation_rows)
            ),
            4,
        )
        if implementation_rows
        else 0.0,
        "avg_implementation_adjusted_win_rate": round(
            (
                sum(_coerce_float(row.get("adjusted_win_rate"), default=0.0) for row in implementation_rows)
                / len(implementation_rows)
            ),
            4,
        )
        if implementation_rows
        else 0.0,
        "evidence_runtime_history_trend": evidence_runtime_trend,
        "evidence_runtime_priority_boost": round(float(evidence_runtime_priority_boost), 4),
    }

    suggested_actions: list[str] = []
    for row in priority_top[:3]:
        suggested_actions.append(
            "Prioritize "
            f"{row.get('domain')}:{row.get('friction_key')} "
            f"(opportunity_score={row.get('opportunity_score')}, "
            f"adjusted_win_rate={row.get('implementation_adjusted_win_rate')}, trend={row.get('trend')})."
        )
    if not suggested_actions and laggard_top:
        weakest = laggard_top[0]
        suggested_actions.append(
            "Investigate lowest-win implementation "
            f"{weakest.get('domain')}:{weakest.get('friction_key')} "
            f"(win_rate={weakest.get('win_rate')}, runs={weakest.get('run_count')})."
        )
    if not suggested_actions:
        suggested_actions.append("Benchmark has no ranked rows yet; run operator-cycle with seed+draft enabled first.")
    if evidence_runtime_trend in {"worsening", "persistent"}:
        suggested_actions.append(
            "Escalate unresolved evidence lookup IDs first; runtime trend indicates recurring knowledge-source gaps."
        )

    status = "ok" if not data_gaps else "warning"
    payload = {
        "generated_at": utc_now_iso(),
        "status": status,
        "report_path": str(report_path),
        "daily_report_path": str(resolved_daily_report_path),
        "top_limit": top_limit,
        "summary": summary,
        "recurring_pains": recurring_top,
        "trend_acceleration": trend_top,
        "implementation_win_rates": win_rate_top,
        "implementation_laggards": laggard_top,
        "priority_board": priority_top,
        "evidence_lookup_runtime_history": evidence_runtime_history,
        "data_gaps": data_gaps,
        "suggested_actions": suggested_actions,
    }

    output_path = args.output_path.resolve() if args.output_path is not None else None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)

    _print_json_payload(
        payload,
        compact=bool(getattr(args, "json_compact", False)),
    )
    if status != "ok" and bool(getattr(args, "strict", False)):
        raise SystemExit(2)


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
    plans_eval_promotion.add_argument(
        "--enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_true",
    )
    plans_eval_promotion.add_argument(
        "--no-enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_false",
    )
    plans_eval_promotion.add_argument("--critical-drift-gate-limit", type=int, default=100)
    plans_eval_promotion.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_eval_promotion.add_argument("--db-path", type=Path, default=_default_db_path())
    plans_eval_promotion.set_defaults(enforce_critical_drift_gate=True)

    plans_gate_status = plans_sub.add_parser(
        "gate-status",
        help="Show critical drift gate blockers + acknowledge commands for this provider review",
    )
    plans_gate_status.add_argument("plan_id", type=str)
    plans_gate_status.add_argument("step_id", type=str)
    plans_gate_status.add_argument("--required-label", action="append", default=None)
    plans_gate_status.add_argument("--allow-no-required-checks", action="store_true")
    plans_gate_status.add_argument("--single-maintainer-override", action="store_true")
    plans_gate_status.add_argument("--override-actor", type=str, default=None)
    plans_gate_status.add_argument("--override-reason", type=str, default=None)
    plans_gate_status.add_argument("--override-sunset-condition", type=str, default=None)
    plans_gate_status.add_argument(
        "--enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_true",
    )
    plans_gate_status.add_argument(
        "--no-enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_false",
    )
    plans_gate_status.add_argument("--critical-drift-gate-limit", type=int, default=100)
    plans_gate_status.add_argument(
        "--output",
        type=str,
        choices=("json", "text"),
        default="json",
        help="Output format (json default, text for concise operator triage)",
    )
    plans_gate_status.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_gate_status.add_argument("--db-path", type=Path, default=_default_db_path())
    plans_gate_status.set_defaults(enforce_critical_drift_gate=True)

    plans_gate_status_all = plans_sub.add_parser(
        "gate-status-all",
        help="Scan recent provider reviews and show consolidated critical drift gate blockers",
    )
    plans_gate_status_all.add_argument("--limit", type=int, default=25)
    plans_gate_status_all.add_argument("--provider", type=str, default=None)
    plans_gate_status_all.add_argument("--repo-slug", type=str, default=None)
    plans_gate_status_all.add_argument("--required-label", action="append", default=None)
    plans_gate_status_all.add_argument("--allow-no-required-checks", action="store_true")
    plans_gate_status_all.add_argument("--single-maintainer-override", action="store_true")
    plans_gate_status_all.add_argument("--override-actor", type=str, default=None)
    plans_gate_status_all.add_argument("--override-reason", type=str, default=None)
    plans_gate_status_all.add_argument("--override-sunset-condition", type=str, default=None)
    plans_gate_status_all.add_argument(
        "--enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_true",
    )
    plans_gate_status_all.add_argument(
        "--no-enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_false",
    )
    plans_gate_status_all.add_argument("--critical-drift-gate-limit", type=int, default=100)
    plans_gate_status_all.add_argument(
        "--output",
        type=str,
        choices=("json", "text"),
        default="json",
        help="Output format (json default, text for consolidated operator triage)",
    )
    plans_gate_status_all.add_argument(
        "--only-blocked",
        action="store_true",
        help="Show only blocked gate rows in gate_rows output (still scans all reviews)",
    )
    plans_gate_status_all.add_argument(
        "--only-unlock-ready",
        action="store_true",
        help="Show only unlock-ready gate rows in gate_rows output (still scans all reviews)",
    )
    plans_gate_status_all.add_argument(
        "--fail-on-blocked",
        action="store_true",
        help="Exit non-zero when blocked steps are present (for CI/automation gating)",
    )
    plans_gate_status_all.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero when gate evaluation errors are present (takes precedence over blocked exit)",
    )
    plans_gate_status_all.add_argument(
        "--fail-on-zero-scanned",
        action="store_true",
        help="Exit non-zero when the scan returns zero review rows (takes precedence over blocked exit)",
    )
    plans_gate_status_all.add_argument(
        "--fail-on-zero-evaluated",
        action="store_true",
        help="Exit non-zero when no review steps were evaluable (takes precedence over blocked exit)",
    )
    plans_gate_status_all.add_argument(
        "--fail-on-zero-unlock-ready",
        action="store_true",
        help=(
            "Exit non-zero when no unlock-ready steps are available "
            "(takes precedence over blocked/empty-ack exit)"
        ),
    )
    plans_gate_status_all.add_argument(
        "--fail-on-empty-ack-commands",
        action="store_true",
        help="Exit non-zero when blocked steps exist but no actionable acknowledge commands are available",
    )
    plans_gate_status_all.add_argument(
        "--blocked-exit-code",
        type=int,
        default=2,
        help="Exit code to use with --fail-on-blocked when blockers are present (min 1)",
    )
    plans_gate_status_all.add_argument(
        "--error-exit-code",
        type=int,
        default=3,
        help="Exit code to use with --fail-on-errors when evaluation errors are present (min 1)",
    )
    plans_gate_status_all.add_argument(
        "--zero-scanned-exit-code",
        type=int,
        default=5,
        help="Exit code to use with --fail-on-zero-scanned when scan returns no review rows (min 1)",
    )
    plans_gate_status_all.add_argument(
        "--zero-evaluated-exit-code",
        type=int,
        default=4,
        help="Exit code to use with --fail-on-zero-evaluated when no steps are evaluable (min 1)",
    )
    plans_gate_status_all.add_argument(
        "--zero-unlock-ready-exit-code",
        type=int,
        default=8,
        help="Exit code to use with --fail-on-zero-unlock-ready when no unlock-ready steps are found (min 1)",
    )
    plans_gate_status_all.add_argument(
        "--empty-ack-commands-exit-code",
        type=int,
        default=6,
        help=(
            "Exit code to use with --fail-on-empty-ack-commands when blocked "
            "steps exist without acknowledge commands (min 1)"
        ),
    )
    plans_gate_status_all.add_argument(
        "--emit-ci-summary-path",
        type=Path,
        default=None,
        help=(
            "Optional markdown artifact path for CI systems. Overrides $GITHUB_STEP_SUMMARY when set "
            "and writes a concise gate-status-all summary with blockers, acknowledge commands, and exit reason"
        ),
    )
    plans_gate_status_all.add_argument(
        "--emit-ci-json-path",
        type=Path,
        default=None,
        help=(
            "Optional compact JSON artifact path for CI systems with counts, exit reason/code, "
            "blocked step IDs, acknowledge commands, and errors"
        ),
    )
    plans_gate_status_all.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit compact gate status fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    plans_gate_status_all.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    plans_gate_status_all.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_gate_status_all.add_argument("--db-path", type=Path, default=_default_db_path())
    plans_gate_status_all.set_defaults(enforce_critical_drift_gate=True)

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
    plans_promote_ready.add_argument(
        "--enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_true",
    )
    plans_promote_ready.add_argument(
        "--no-enforce-critical-drift-gate",
        dest="enforce_critical_drift_gate",
        action="store_false",
    )
    plans_promote_ready.add_argument("--critical-drift-gate-limit", type=int, default=100)
    plans_promote_ready.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_promote_ready.add_argument("--db-path", type=Path, default=_default_db_path())
    plans_promote_ready.set_defaults(enforce_critical_drift_gate=True)

    plans_project_backfill = plans_sub.add_parser(
        "backfill-project-signals",
        help="Run cursor-profile-backed project signal backfill summary (safe dry-run by default)",
    )
    plans_project_backfill.add_argument("project_id", type=str)
    plans_project_backfill.add_argument("--profile-key", type=str, default="default")
    plans_project_backfill.add_argument("--actor", type=str, default="operator")
    plans_project_backfill.add_argument(
        "--preset",
        type=str,
        default="balanced",
        choices=sorted(PROJECT_BACKFILL_PRESETS.keys()),
    )
    plans_project_backfill.add_argument(
        "--execute",
        action="store_true",
        help="Persist backfill markers and cursor movement (default is dry-run)",
    )
    plans_project_backfill.add_argument("--limit", type=int, default=None)
    plans_project_backfill.add_argument("--top-signal-types", type=int, default=None)
    plans_project_backfill.add_argument("--max-source-counts", type=int, default=None)
    plans_project_backfill.add_argument("--max-signal-type-counts", type=int, default=None)
    plans_project_backfill.add_argument(
        "--summary-only",
        action="store_true",
        help="Emit only summary-focused result payload (without full backfill block)",
    )
    plans_project_backfill.add_argument(
        "--json-compact",
        action="store_true",
        help="Emit minified JSON output",
    )
    plans_project_backfill.add_argument(
        "--output",
        type=str,
        default="json",
        choices=("json", "pretty", "warnings", "policy"),
        help=(
            "Output mode: machine JSON, human-readable pretty text, warnings-only JSON, "
            "or compact warning-policy provenance JSON"
        ),
    )
    plans_project_backfill.add_argument(
        "--color",
        type=str,
        default="auto",
        choices=("auto", "always", "never"),
        help="Color mode for --output pretty",
    )
    plans_project_backfill.add_argument(
        "--warning-policy-config",
        type=Path,
        default=None,
        help="Path to JSON warning-policy defaults (overridden by explicit CLI flags)",
    )
    plans_project_backfill.add_argument(
        "--warning-policy-profile",
        type=str,
        default=None,
        choices=sorted(WARNING_POLICY_PROFILES.keys()),
        help=(
            "Warning-policy profile defaults (explicit warning flags override profile values; "
            "env fallback: JARVIS_BACKFILL_WARNING_POLICY_PROFILE)"
        ),
    )
    plans_project_backfill.add_argument(
        "--suppress-warning-code",
        action="append",
        default=[],
        help=(
            "Suppress operator warning hints by code (repeatable; "
            "env defaults via JARVIS_BACKFILL_SUPPRESS_WARNING_CODES)"
        ),
    )
    plans_project_backfill.add_argument(
        "--min-warning-severity",
        type=str,
        default=None,
        choices=("info", "warning", "error"),
        help=(
            "Minimum warning severity to include in operator hints output "
            "(env fallback: JARVIS_BACKFILL_MIN_WARNING_SEVERITY; then warning profile)"
        ),
    )
    plans_project_backfill.add_argument(
        "--exit-code-policy",
        type=str,
        default=None,
        choices=("off", "warning", "error"),
        help=(
            "Exit code policy based on filtered warning severity "
            "(env fallback: JARVIS_BACKFILL_EXIT_CODE_POLICY; then warning profile)"
        ),
    )
    plans_project_backfill.add_argument(
        "--warning-exit-code",
        type=int,
        default=None,
        help=(
            "Exit code used for warning-level policy hits "
            "(env fallback: JARVIS_BACKFILL_WARNING_EXIT_CODE; then warning profile)"
        ),
    )
    plans_project_backfill.add_argument(
        "--error-exit-code",
        type=int,
        default=None,
        help=(
            "Exit code used for error-level policy hits "
            "(env fallback: JARVIS_BACKFILL_ERROR_EXIT_CODE; then warning profile)"
        ),
    )
    plans_project_backfill.add_argument("--since-updated-at", type=str, default=None)
    plans_project_backfill.add_argument("--since-outcomes-at", type=str, default=None)
    plans_project_backfill.add_argument("--since-review-artifacts-at", type=str, default=None)
    plans_project_backfill.add_argument("--since-merge-outcomes-at", type=str, default=None)
    plans_project_backfill.add_argument("--repo-path", type=Path, default=_default_repo_path())
    plans_project_backfill.add_argument("--db-path", type=Path, default=_default_db_path())

    plans_project_backfill.add_argument("--include-outcomes", dest="include_outcomes", action="store_true")
    plans_project_backfill.add_argument("--no-include-outcomes", dest="include_outcomes", action="store_false")
    plans_project_backfill.add_argument(
        "--include-review-artifacts",
        dest="include_review_artifacts",
        action="store_true",
    )
    plans_project_backfill.add_argument(
        "--no-include-review-artifacts",
        dest="include_review_artifacts",
        action="store_false",
    )
    plans_project_backfill.add_argument(
        "--include-merge-outcomes",
        dest="include_merge_outcomes",
        action="store_true",
    )
    plans_project_backfill.add_argument(
        "--no-include-merge-outcomes",
        dest="include_merge_outcomes",
        action="store_false",
    )
    plans_project_backfill.add_argument("--skip-seen", dest="skip_seen", action="store_true")
    plans_project_backfill.add_argument("--no-skip-seen", dest="skip_seen", action="store_false")
    plans_project_backfill.add_argument(
        "--load-since-from-cursor-profile",
        dest="load_since_from_cursor_profile",
        action="store_true",
    )
    plans_project_backfill.add_argument(
        "--no-load-since-from-cursor-profile",
        dest="load_since_from_cursor_profile",
        action="store_false",
    )
    plans_project_backfill.add_argument("--include-raw-signals", dest="include_raw_signals", action="store_true")
    plans_project_backfill.add_argument("--no-include-raw-signals", dest="include_raw_signals", action="store_false")
    plans_project_backfill.add_argument(
        "--include-raw-ingestions",
        dest="include_raw_ingestions",
        action="store_true",
    )
    plans_project_backfill.add_argument(
        "--no-include-raw-ingestions",
        dest="include_raw_ingestions",
        action="store_false",
    )
    plans_project_backfill.set_defaults(
        include_outcomes=None,
        include_review_artifacts=None,
        include_merge_outcomes=None,
        skip_seen=None,
        load_since_from_cursor_profile=None,
        max_source_counts=None,
        max_signal_type_counts=None,
        include_raw_signals=None,
        include_raw_ingestions=None,
    )

    improvement = sub.add_parser("improvement", help="Friction mining + hypothesis lab workflows")
    improvement_sub = improvement.add_subparsers(dest="improvement_cmd", required=True)
    improvement_cycle = improvement_sub.add_parser(
        "cycle-from-file",
        help="Ingest feedback file and run friction-to-hypothesis cycle with ranked inbox report",
    )
    improvement_cycle.add_argument("--domain", type=str, required=True)
    improvement_cycle.add_argument("--source", type=str, required=True)
    improvement_cycle.add_argument("--input-path", type=Path, required=True)
    improvement_cycle.add_argument(
        "--input-format",
        type=str,
        default=None,
        choices=("json", "jsonl", "ndjson", "csv"),
        help="Override auto-detected file format from extension",
    )
    improvement_cycle.add_argument("--default-segment", type=str, default="general")
    improvement_cycle.add_argument("--default-severity", type=float, default=3.0)
    improvement_cycle.add_argument("--default-frustration-score", type=float, default=None)
    improvement_cycle.add_argument("--status", type=str, default="open")
    improvement_cycle.add_argument("--min-cluster-count", type=int, default=2)
    improvement_cycle.add_argument("--proposal-limit", type=int, default=5)
    improvement_cycle.add_argument("--owner", type=str, default="operator")
    improvement_cycle.add_argument(
        "--auto-register",
        dest="auto_register",
        action="store_true",
        help="Auto-register deduped hypotheses from cycle proposals (default)",
    )
    improvement_cycle.add_argument(
        "--no-auto-register",
        dest="auto_register",
        action="store_false",
        help="Only propose hypotheses; do not persist new registry entries",
    )
    improvement_cycle.add_argument("--report-cluster-limit", type=int, default=10)
    improvement_cycle.add_argument("--report-hypothesis-limit", type=int, default=30)
    improvement_cycle.add_argument("--report-experiment-limit", type=int, default=50)
    improvement_cycle.add_argument("--report-queue-limit", type=int, default=20)
    improvement_cycle.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional path to write the ranked inbox report JSON",
    )
    improvement_cycle.add_argument("--json-compact", action="store_true")
    improvement_cycle.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_cycle.add_argument("--db-path", type=Path, default=_default_db_path())
    improvement_cycle.set_defaults(auto_register=True)

    improvement_experiment = improvement_sub.add_parser(
        "run-experiment-artifact",
        help="Run one hypothesis experiment directly from a backtest/paper-trade artifact JSON",
    )
    improvement_experiment.add_argument("--hypothesis-id", type=str, required=True)
    improvement_experiment.add_argument("--artifact-path", type=Path, required=True)
    improvement_experiment.add_argument("--environment", type=str, default=None)
    improvement_experiment.add_argument("--source-trace-id", type=str, default=None)
    improvement_experiment.add_argument("--notes", type=str, default=None)
    improvement_experiment.add_argument("--json-compact", action="store_true")
    improvement_experiment.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_experiment.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_seed = improvement_sub.add_parser(
        "seed-hypotheses",
        help="Seed reusable hypothesis templates into the registry for cross-domain operator workflows",
    )
    improvement_seed.add_argument("--template-path", type=Path, required=True)
    improvement_seed.add_argument("--owner", type=str, default="operator")
    improvement_seed.add_argument("--lookup-limit", type=int, default=400)
    improvement_seed.add_argument(
        "--allow-invalid-rows",
        action="store_true",
        help="Skip malformed template rows instead of treating them as command errors",
    )
    improvement_seed.add_argument("--strict", action="store_true")
    improvement_seed.add_argument("--output-path", type=Path, default=None)
    improvement_seed.add_argument("--json-compact", action="store_true")
    improvement_seed.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_seed.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_pull = improvement_sub.add_parser(
        "pull-feeds",
        help="Fetch configured external/local feedback feeds and materialize JSONL input files",
    )
    improvement_pull.add_argument("--config-path", type=Path, required=True)
    improvement_pull.add_argument(
        "--feed-names",
        type=str,
        default=None,
        help="Optional CSV of feed names to run (default runs all feeds in config)",
    )
    improvement_pull.add_argument(
        "--allow-missing",
        action="store_true",
        help="Do not fail the command on feed pull errors",
    )
    improvement_pull.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when feed pull errors are present",
    )
    improvement_pull.add_argument("--timeout-seconds", type=float, default=20.0)
    improvement_pull.add_argument("--output-path", type=Path, default=None)
    improvement_pull.add_argument("--json-compact", action="store_true")

    improvement_fitness_leaderboard = improvement_sub.add_parser(
        "fitness-leaderboard",
        help="Build a week-over-week fitness frustration leaderboard from merged market review feedback",
    )
    improvement_fitness_leaderboard.add_argument("--input-path", type=Path, required=True)
    improvement_fitness_leaderboard.add_argument(
        "--input-format",
        type=str,
        default=None,
        choices=("json", "jsonl", "ndjson", "csv"),
        help="Override auto-detected feedback file format",
    )
    improvement_fitness_leaderboard.add_argument("--domain", type=str, default="fitness_apps")
    improvement_fitness_leaderboard.add_argument("--source", type=str, default="market_reviews")
    improvement_fitness_leaderboard.add_argument(
        "--timestamp-fields",
        type=str,
        default="created_at,at,submission_date,date,timestamp,occurred_at",
        help="CSV field-path priority list used to resolve record timestamps",
    )
    improvement_fitness_leaderboard.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Window end timestamp (ISO8601). Defaults to current UTC time.",
    )
    improvement_fitness_leaderboard.add_argument("--lookback-days", type=int, default=7)
    improvement_fitness_leaderboard.add_argument("--min-cluster-count", type=int, default=1)
    improvement_fitness_leaderboard.add_argument("--cluster-limit", type=int, default=20)
    improvement_fitness_leaderboard.add_argument("--leaderboard-limit", type=int, default=12)
    improvement_fitness_leaderboard.add_argument("--cooling-limit", type=int, default=10)
    improvement_fitness_leaderboard.add_argument(
        "--evidence-sample-limit",
        type=int,
        default=3,
        help="Maximum evidence drilldown rows to include per cluster and window",
    )
    improvement_fitness_leaderboard.add_argument(
        "--app-fields",
        type=str,
        default=DEFAULT_FITNESS_APP_FIELDS_CSV,
        help="CSV field-path priority list used to resolve app/provider identity for each feedback record",
    )
    improvement_fitness_leaderboard.add_argument(
        "--top-apps-per-cluster",
        type=int,
        default=3,
        help="Maximum app breakdown rows to include per frustration cluster",
    )
    improvement_fitness_leaderboard.add_argument(
        "--min-cross-app-count",
        type=int,
        default=2,
        help="Minimum unique-app count required to classify a friction as shared market displeasure",
    )
    improvement_fitness_leaderboard.add_argument(
        "--own-app-aliases",
        type=str,
        default=None,
        help="Optional CSV of your app aliases used to surface whitespace candidate frustrations",
    )
    improvement_fitness_leaderboard.add_argument(
        "--trend-threshold",
        type=float,
        default=0.25,
        help="Minimum impact-score delta required to mark a cluster as rising/cooling",
    )
    improvement_fitness_leaderboard.add_argument(
        "--include-untimed-current",
        action="store_true",
        help="Include records without timestamps in the current window bucket",
    )
    improvement_fitness_leaderboard.add_argument("--strict", action="store_true")
    improvement_fitness_leaderboard.add_argument("--output-path", type=Path, default=None)
    improvement_fitness_leaderboard.add_argument("--json-compact", action="store_true")

    improvement_evidence_lookup = improvement_sub.add_parser(
        "evidence-lookup",
        help="Resolve seed evidence record IDs into source snippets/provenance for operator triage",
    )
    improvement_evidence_lookup.add_argument(
        "--record-ids",
        type=str,
        default=None,
        help="Optional CSV record IDs to resolve (can be combined with --operator-report-path extraction)",
    )
    improvement_evidence_lookup.add_argument(
        "--operator-report-path",
        type=Path,
        default=None,
        help="Optional operator-cycle report/summary JSON to extract record IDs from evidence fields",
    )
    improvement_evidence_lookup.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Optional pipeline config path used to derive feedback_jobs input sources",
    )
    improvement_evidence_lookup.add_argument(
        "--input-path",
        dest="input_paths",
        action="append",
        type=Path,
        default=None,
        help="Optional explicit feedback input path (repeat flag to scan multiple files)",
    )
    improvement_evidence_lookup.add_argument(
        "--input-format",
        type=str,
        default=None,
        choices=("json", "jsonl", "ndjson", "csv"),
        help="Optional explicit format for --input-path files",
    )
    improvement_evidence_lookup.add_argument(
        "--id-fields",
        type=str,
        default=DEFAULT_EVIDENCE_LOOKUP_ID_FIELDS_CSV,
        help="CSV field-path priority list used to resolve record IDs",
    )
    improvement_evidence_lookup.add_argument(
        "--summary-fields",
        type=str,
        default=DEFAULT_EVIDENCE_LOOKUP_SUMMARY_FIELDS_CSV,
        help="CSV field-path priority list used to extract summary/snippet text",
    )
    improvement_evidence_lookup.add_argument(
        "--timestamp-fields",
        type=str,
        default=DEFAULT_EVIDENCE_LOOKUP_TIMESTAMP_FIELDS_CSV,
        help="CSV field-path priority list used to extract timestamps",
    )
    improvement_evidence_lookup.add_argument(
        "--context-fields",
        type=str,
        default=DEFAULT_EVIDENCE_LOOKUP_CONTEXT_FIELDS_CSV,
        help="CSV field-paths included in match context payloads",
    )
    improvement_evidence_lookup.add_argument(
        "--snippet-max-chars",
        type=int,
        default=280,
        help="Maximum snippet length per matched record",
    )
    improvement_evidence_lookup.add_argument(
        "--limit-per-id",
        type=int,
        default=5,
        help="Maximum match rows emitted per requested record ID",
    )
    improvement_evidence_lookup.add_argument(
        "--include-record",
        action="store_true",
        help="Include full matched raw record objects in output payload",
    )
    improvement_evidence_lookup.add_argument(
        "--allow-missing-inputs",
        action="store_true",
        help="Skip missing input files instead of counting them as lookup errors",
    )
    improvement_evidence_lookup.add_argument("--strict", action="store_true")
    improvement_evidence_lookup.add_argument("--output-path", type=Path, default=None)
    improvement_evidence_lookup.add_argument("--json-compact", action="store_true")

    improvement_seed_from_leaderboard = improvement_sub.add_parser(
        "seed-from-leaderboard",
        help="Create queued hypotheses from leaderboard rising/new frustrations with dedupe safeguards",
    )
    improvement_seed_from_leaderboard.add_argument("--leaderboard-path", type=Path, required=True)
    improvement_seed_from_leaderboard.add_argument("--domain", type=str, default="fitness_apps")
    improvement_seed_from_leaderboard.add_argument("--source", type=str, default="fitness_leaderboard")
    improvement_seed_from_leaderboard.add_argument(
        "--trends",
        type=str,
        default="new,rising",
        help="CSV trend filter (for example: new,rising)",
    )
    improvement_seed_from_leaderboard.add_argument("--limit", type=int, default=8)
    improvement_seed_from_leaderboard.add_argument("--min-impact-score", type=float, default=0.0)
    improvement_seed_from_leaderboard.add_argument("--min-impact-delta", type=float, default=0.0)
    improvement_seed_from_leaderboard.add_argument(
        "--entry-source",
        type=str,
        default="leaderboard",
        choices=("leaderboard", "shared_market_displeasures", "white_space_candidates"),
        help="Select which leaderboard section to seed from",
    )
    improvement_seed_from_leaderboard.add_argument(
        "--fallback-entry-source",
        type=str,
        default="leaderboard",
        choices=("leaderboard", "shared_market_displeasures", "white_space_candidates", "none"),
        help="Optional fallback section used when --entry-source has no entries",
    )
    improvement_seed_from_leaderboard.add_argument(
        "--min-cross-app-count",
        type=int,
        default=0,
        help="Optional minimum cross-app coverage required per candidate entry (0 disables)",
    )
    improvement_seed_from_leaderboard.add_argument(
        "--min-signal-count-current",
        type=int,
        default=0,
        help="Optional minimum current-window signal count required per candidate entry (0 disables)",
    )
    improvement_seed_from_leaderboard.add_argument("--owner", type=str, default="operator")
    improvement_seed_from_leaderboard.add_argument("--lookup-limit", type=int, default=400)
    improvement_seed_from_leaderboard.add_argument("--strict", action="store_true")
    improvement_seed_from_leaderboard.add_argument("--output-path", type=Path, default=None)
    improvement_seed_from_leaderboard.add_argument("--json-compact", action="store_true")
    improvement_seed_from_leaderboard.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_seed_from_leaderboard.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_draft_experiments = improvement_sub.add_parser(
        "draft-experiment-jobs",
        help="Draft controlled experiment jobs and artifact templates from seeded/queued hypotheses",
    )
    improvement_draft_experiments.add_argument(
        "--seed-report-path",
        type=Path,
        default=None,
        help="Optional seed-from-leaderboard report path used to target newly created hypotheses first",
    )
    improvement_draft_experiments.add_argument(
        "--benchmark-report-path",
        type=Path,
        default=None,
        help="Optional benchmark-frustrations report path used to prioritize top opportunity hypotheses",
    )
    improvement_draft_experiments.add_argument(
        "--benchmark-min-opportunity",
        type=float,
        default=None,
        help="Optional minimum opportunity_score filter when using --benchmark-report-path",
    )
    improvement_draft_experiments.add_argument(
        "--include-existing",
        action="store_true",
        help="When using --seed-report-path, include seed report existing rows in candidate selection",
    )
    improvement_draft_experiments.add_argument("--domain", type=str, default=None)
    improvement_draft_experiments.add_argument(
        "--statuses",
        type=str,
        default="queued",
        help="CSV hypothesis status filter used during candidate selection",
    )
    improvement_draft_experiments.add_argument("--limit", type=int, default=8)
    improvement_draft_experiments.add_argument("--lookup-limit", type=int, default=400)
    improvement_draft_experiments.add_argument(
        "--pipeline-config-path",
        type=Path,
        default=None,
        help="Optional pipeline config to append drafted experiment_jobs into",
    )
    improvement_draft_experiments.add_argument(
        "--write-config-path",
        type=Path,
        default=None,
        help="Optional output path for the updated pipeline config (defaults to *.drafted.json)",
    )
    improvement_draft_experiments.add_argument(
        "--in-place",
        action="store_true",
        help="When set with --pipeline-config-path, write drafted jobs back into the same config file",
    )
    improvement_draft_experiments.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("analysis/improvement/experiment_artifacts"),
        help="Directory where drafted experiment artifact templates are written",
    )
    improvement_draft_experiments.add_argument(
        "--overwrite-artifacts",
        action="store_true",
        help="Overwrite existing artifact template files when names collide",
    )
    improvement_draft_experiments.add_argument(
        "--environment",
        type=str,
        default=None,
        help="Optional forced experiment environment for all drafted jobs",
    )
    improvement_draft_experiments.add_argument(
        "--default-sample-size",
        type=int,
        default=100,
        help="Fallback sample-size target when hypothesis success criteria omit min_sample_size",
    )
    improvement_draft_experiments.add_argument(
        "--evidence-runtime-history-path",
        type=Path,
        default=None,
        help="Optional evidence runtime history JSONL path used for pressure-aware draft prioritization",
    )
    improvement_draft_experiments.add_argument(
        "--evidence-runtime-history-window",
        type=int,
        default=7,
        help="History window size used for evidence runtime trend scoring",
    )
    improvement_draft_experiments.add_argument(
        "--evidence-pressure-enable",
        dest="evidence_pressure_enable",
        action="store_true",
        help="Enable pressure-aware draft prioritization from evidence runtime trend signals (default)",
    )
    improvement_draft_experiments.add_argument(
        "--no-evidence-pressure-enable",
        dest="evidence_pressure_enable",
        action="store_false",
        help="Disable pressure-aware draft prioritization from evidence runtime trend signals",
    )
    improvement_draft_experiments.add_argument(
        "--evidence-pressure-min-priority-boost",
        type=float,
        default=0.35,
        help="Minimum runtime history priority_boost required to apply pressure scheduling",
    )
    improvement_draft_experiments.add_argument(
        "--evidence-pressure-limit-increase",
        type=int,
        default=2,
        help="Additional draft limit applied when evidence pressure scheduling is triggered",
    )
    improvement_draft_experiments.add_argument(
        "--evidence-pressure-statuses",
        type=str,
        default="queued,testing",
        help="CSV statuses merged into --statuses when evidence pressure scheduling is triggered",
    )
    improvement_draft_experiments.add_argument("--strict", action="store_true")
    improvement_draft_experiments.add_argument("--output-path", type=Path, default=None)
    improvement_draft_experiments.add_argument("--json-compact", action="store_true")
    improvement_draft_experiments.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_draft_experiments.add_argument("--db-path", type=Path, default=_default_db_path())
    improvement_draft_experiments.set_defaults(evidence_pressure_enable=True)

    improvement_pipeline = improvement_sub.add_parser(
        "daily-pipeline",
        help="Run config-driven feedback cycles + artifact experiments for daily operations",
    )
    improvement_pipeline.add_argument("--config-path", type=Path, required=True)
    improvement_pipeline.add_argument(
        "--allow-missing-inputs",
        action="store_true",
        help="Skip missing feedback/artifact files instead of treating them as pipeline errors",
    )
    improvement_pipeline.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when pipeline errors are present",
    )
    improvement_pipeline.add_argument("--output-path", type=Path, default=None)
    improvement_pipeline.add_argument("--json-compact", action="store_true")
    improvement_pipeline.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_pipeline.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_retests = improvement_sub.add_parser(
        "execute-retests",
        help="Execute queued retest runs from a daily pipeline report and emit side-by-side comparisons",
    )
    improvement_retests.add_argument("--pipeline-report-path", type=Path, required=True)
    improvement_retests.add_argument("--max-runs", type=int, default=None)
    improvement_retests.add_argument("--artifact-dir", type=Path, default=None)
    improvement_retests.add_argument("--environment", type=str, default=None)
    improvement_retests.add_argument("--notes-prefix", type=str, default="auto_retest")
    improvement_retests.add_argument(
        "--allow-missing-jobs",
        action="store_true",
        help="Skip malformed/missing retest rows instead of reporting as errors",
    )
    improvement_retests.add_argument("--strict", action="store_true")
    improvement_retests.add_argument("--output-path", type=Path, default=None)
    improvement_retests.add_argument("--json-compact", action="store_true")
    improvement_retests.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_retests.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_operator_cycle = improvement_sub.add_parser(
        "operator-cycle",
        help=(
            "Run pull->(optional fitness leaderboard+seed)->(optional draft)->daily->retest pipeline "
            "and emit a single operator inbox summary report"
        ),
    )
    improvement_operator_cycle.add_argument("--config-path", type=Path, required=True)
    improvement_operator_cycle.add_argument("--output-dir", type=Path, default=None)
    improvement_operator_cycle.add_argument("--inbox-summary-path", type=Path, default=None)
    improvement_operator_cycle.add_argument("--feed-names", type=str, default=None)
    improvement_operator_cycle.add_argument("--feed-timeout-seconds", type=float, default=20.0)
    improvement_operator_cycle.add_argument(
        "--allow-missing-feeds",
        dest="allow_missing_feeds",
        action="store_true",
    )
    improvement_operator_cycle.add_argument(
        "--no-allow-missing-feeds",
        dest="allow_missing_feeds",
        action="store_false",
    )
    improvement_operator_cycle.add_argument(
        "--allow-missing-inputs",
        dest="allow_missing_inputs",
        action="store_true",
    )
    improvement_operator_cycle.add_argument(
        "--no-allow-missing-inputs",
        dest="allow_missing_inputs",
        action="store_false",
    )
    improvement_operator_cycle.add_argument(
        "--allow-missing-retests",
        dest="allow_missing_retests",
        action="store_true",
    )
    improvement_operator_cycle.add_argument(
        "--no-allow-missing-retests",
        dest="allow_missing_retests",
        action="store_false",
    )
    improvement_operator_cycle.add_argument("--retest-max-runs", type=int, default=None)
    improvement_operator_cycle.add_argument("--retest-artifact-dir", type=Path, default=None)
    improvement_operator_cycle.add_argument("--retest-environment", type=str, default=None)
    improvement_operator_cycle.add_argument("--retest-notes-prefix", type=str, default="operator_cycle_retest")
    improvement_operator_cycle.add_argument(
        "--seed-enable",
        action="store_true",
        help="Enable fitness-leaderboard + seed-from-leaderboard stages before draft/daily execution",
    )
    improvement_operator_cycle.add_argument(
        "--seed-domains",
        type=str,
        default=None,
        help="Optional comma-separated domain list for multi-domain seeding (for example: quant_finance,kalshi_weather,fitness_apps,market_ml)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-leaderboard-input-path",
        type=Path,
        default=None,
        help=(
            "Optional input file path for fitness-leaderboard "
            "(defaults to feedback_jobs input for --seed-domain)"
        ),
    )
    improvement_operator_cycle.add_argument("--seed-leaderboard-input-format", type=str, default=None)
    improvement_operator_cycle.add_argument(
        "--seed-leaderboard-report-path",
        type=Path,
        default=None,
        help="Optional report path for fitness-leaderboard stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-report-path",
        type=Path,
        default=None,
        help="Optional report path for seed-from-leaderboard stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument("--seed-domain", type=str, default="fitness_apps")
    improvement_operator_cycle.add_argument(
        "--seed-source",
        type=str,
        default=None,
        help="Optional leaderboard source override (defaults to feedback_jobs source when available)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-hypothesis-source",
        type=str,
        default=None,
        help="Optional hypothesis source tag for seed-from-leaderboard rows",
    )
    improvement_operator_cycle.add_argument(
        "--seed-trends",
        type=str,
        default=None,
        help="Optional seed trend filter override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-limit",
        type=int,
        default=None,
        help="Optional seed candidate limit override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-min-impact-score",
        type=float,
        default=None,
        help="Optional minimum impact score override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-min-impact-delta",
        type=float,
        default=None,
        help="Optional minimum impact delta override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-entry-source",
        type=str,
        default=None,
        choices=("leaderboard", "shared_market_displeasures", "white_space_candidates"),
        help="Optional seed entry source override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-fallback-entry-source",
        type=str,
        default=None,
        choices=("leaderboard", "shared_market_displeasures", "white_space_candidates", "none"),
        help="Optional seed fallback source override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument("--seed-owner", type=str, default="operator")
    improvement_operator_cycle.add_argument(
        "--seed-lookup-limit",
        type=int,
        default=None,
        help=(
            "Optional seed hypothesis lookup limit override (when omitted, resolves from config defaults per domain)"
        ),
    )
    improvement_operator_cycle.add_argument("--seed-as-of", type=str, default=None)
    improvement_operator_cycle.add_argument(
        "--seed-lookback-days",
        type=int,
        default=None,
        help="Optional leaderboard lookback-days override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument("--seed-min-cluster-count", type=int, default=1)
    improvement_operator_cycle.add_argument("--seed-cluster-limit", type=int, default=20)
    improvement_operator_cycle.add_argument(
        "--seed-leaderboard-limit",
        type=int,
        default=None,
        help="Optional leaderboard row-limit override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument("--seed-cooling-limit", type=int, default=10)
    improvement_operator_cycle.add_argument(
        "--seed-app-fields",
        type=str,
        default=DEFAULT_FITNESS_APP_FIELDS_CSV,
    )
    improvement_operator_cycle.add_argument("--seed-top-apps-per-cluster", type=int, default=3)
    improvement_operator_cycle.add_argument(
        "--seed-min-cross-app-count",
        type=int,
        default=None,
        help=(
            "Minimum cross-app coverage used by fitness-leaderboard shared-market ranking "
            "and seed-from-leaderboard candidate filtering (when omitted, resolves from config defaults per domain)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--seed-min-signal-count-current",
        type=int,
        default=None,
        help=(
            "Optional minimum current-window signal count required during seed-from-leaderboard filtering "
            "(when omitted, resolves from config defaults per domain)"
        ),
    )
    improvement_operator_cycle.add_argument("--seed-own-app-aliases", type=str, default=None)
    improvement_operator_cycle.add_argument(
        "--seed-trend-threshold",
        type=float,
        default=None,
        help="Optional leaderboard trend-threshold override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--seed-timestamp-fields",
        type=str,
        default="created_at,at,submission_date,date,timestamp,occurred_at",
    )
    improvement_operator_cycle.add_argument("--seed-include-untimed-current", action="store_true")
    improvement_operator_cycle.add_argument(
        "--draft-enable",
        action="store_true",
        help="Enable draft-experiment-jobs stage before daily-pipeline execution",
    )
    improvement_operator_cycle.add_argument(
        "--draft-seed-report-path",
        type=Path,
        default=None,
        help="Optional seed-from-leaderboard report to prioritize newly seeded hypotheses",
    )
    improvement_operator_cycle.add_argument(
        "--draft-config-path",
        type=Path,
        default=None,
        help="Optional base pipeline config for drafting (defaults to --config-path)",
    )
    improvement_operator_cycle.add_argument(
        "--draft-output-config-path",
        type=Path,
        default=None,
        help="Optional output config path for drafted experiment_jobs (defaults beside base config)",
    )
    improvement_operator_cycle.add_argument(
        "--draft-report-path",
        type=Path,
        default=None,
        help="Optional report path for draft-experiment-jobs stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--draft-artifacts-dir",
        type=Path,
        default=None,
        help="Optional artifact directory for drafted experiment templates (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument("--draft-domain", type=str, default=None)
    improvement_operator_cycle.add_argument(
        "--draft-statuses",
        type=str,
        default=None,
        help="Optional draft status filter override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--draft-limit",
        type=int,
        default=None,
        help="Optional draft selection limit override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument(
        "--draft-lookup-limit",
        type=int,
        default=None,
        help="Optional draft lookup limit override (when omitted, resolves from config defaults per domain)",
    )
    improvement_operator_cycle.add_argument("--draft-include-existing", action="store_true")
    improvement_operator_cycle.add_argument("--draft-overwrite-artifacts", action="store_true")
    improvement_operator_cycle.add_argument(
        "--draft-environment",
        type=str,
        default=None,
        help=(
            "Optional experiment environment override (when omitted, resolves from config defaults "
            "per domain and then controlled environment inference)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--draft-default-sample-size",
        type=int,
        default=None,
        help=(
            "Optional default sample size override used for drafted artifacts without explicit criteria "
            "(when omitted, resolves from config defaults per domain)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--draft-benchmark-report-path",
        type=Path,
        default=None,
        help=(
            "Optional benchmark-frustrations report path used by draft stage to prioritize high-pressure hypotheses "
            "(supports prior-cycle artifacts)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--draft-benchmark-min-opportunity",
        type=float,
        default=None,
        help="Optional minimum opportunity_score threshold used with --draft-benchmark-report-path",
    )
    improvement_operator_cycle.add_argument(
        "--draft-benchmark-max-age-hours",
        type=float,
        default=None,
        help=(
            "Optional max-age guard for auto-reused output benchmark report "
            "(<=0 disables; defaults to config or 96 hours)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--draft-evidence-pressure-enable",
        dest="draft_evidence_pressure_enable",
        action="store_true",
        help=(
            "Enable pressure-aware draft prioritization using evidence runtime trend signals "
            "(defaults to config or enabled)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--no-draft-evidence-pressure-enable",
        dest="draft_evidence_pressure_enable",
        action="store_false",
        help="Disable pressure-aware draft prioritization using evidence runtime trend signals",
    )
    improvement_operator_cycle.add_argument(
        "--draft-evidence-pressure-min-priority-boost",
        type=float,
        default=None,
        help="Optional minimum evidence runtime priority_boost needed to trigger draft pressure scheduling",
    )
    improvement_operator_cycle.add_argument(
        "--draft-evidence-pressure-limit-increase",
        type=int,
        default=None,
        help="Optional draft limit increment when pressure scheduling is triggered",
    )
    improvement_operator_cycle.add_argument(
        "--draft-evidence-pressure-statuses",
        type=str,
        default=None,
        help="Optional CSV statuses merged into draft status filters when pressure scheduling is triggered",
    )
    improvement_operator_cycle.add_argument(
        "--operator-report-path",
        type=Path,
        default=None,
        help="Optional path for persisted operator-cycle report (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--evidence-lookup-report-path",
        type=Path,
        default=None,
        help="Optional report path used by the precomputed evidence-lookup batch command (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--evidence-runtime-history-path",
        type=Path,
        default=None,
        help=(
            "Optional evidence-lookup runtime history JSONL path used by benchmark and knowledge-delta alert "
            "(defaults to <output-dir>/evidence_lookup_runtime_history.jsonl)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--evidence-runtime-history-window",
        type=int,
        default=None,
        help="Optional window size used when summarizing evidence runtime history (defaults to config or 7)",
    )
    improvement_operator_cycle.add_argument(
        "--benchmark-stale-runtime-history-window",
        type=int,
        default=None,
        help="Optional window size for benchmark stale fallback runtime history trend (defaults to config or 7)",
    )
    improvement_operator_cycle.add_argument(
        "--benchmark-stale-runtime-repeat-threshold",
        type=int,
        default=None,
        help="Optional repeat threshold for benchmark stale fallback runtime alerting (defaults to config or 2)",
    )
    improvement_operator_cycle.add_argument(
        "--benchmark-stale-runtime-rate-ceiling",
        type=float,
        default=None,
        help="Optional rolling-rate ceiling for benchmark stale fallback gate (defaults to config or 0.6)",
    )
    improvement_operator_cycle.add_argument(
        "--benchmark-stale-runtime-consecutive-runs",
        type=int,
        default=None,
        help="Optional consecutive-run threshold for benchmark stale fallback rate gate (defaults to config or 2)",
    )
    improvement_operator_cycle.add_argument(
        "--benchmark-enable",
        action="store_true",
        help="Enable benchmark-frustrations stage after retest execution",
    )
    improvement_operator_cycle.add_argument(
        "--benchmark-top-limit",
        type=int,
        default=None,
        help="Optional benchmark row limit override (when omitted, resolves from config defaults)",
    )
    improvement_operator_cycle.add_argument(
        "--benchmark-report-path",
        type=Path,
        default=None,
        help="Optional report path for benchmark-frustrations stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-enable",
        action="store_true",
        help="Enable verify-matrix stage after benchmark synthesis",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-path",
        type=Path,
        default=None,
        help=(
            "Optional controlled experiment matrix path "
            "(when omitted, resolves from config defaults and runs when available)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-report-path",
        type=Path,
        default=None,
        help="Optional report path for verify-matrix stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-alert-enable",
        action="store_true",
        help="Enable verify-matrix-alert stage after verify-matrix",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-alert-domain",
        type=str,
        default=None,
        help="Optional verify-matrix-alert domain override (defaults to config or 'markets')",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-alert-max-items",
        type=int,
        default=None,
        help="Optional verify-matrix-alert max item count override (defaults to config or 3)",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-alert-urgency",
        type=float,
        default=None,
        help="Optional verify-matrix-alert urgency override (0-1)",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-alert-confidence",
        type=float,
        default=None,
        help="Optional verify-matrix-alert confidence override (0-1)",
    )
    improvement_operator_cycle.add_argument(
        "--verify-matrix-alert-report-path",
        type=Path,
        default=None,
        help="Optional report path for verify-matrix-alert stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-brief-enable",
        action="store_true",
        help="Enable knowledge-brief snapshot stage prior to knowledge-delta alert evaluation",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-brief-query",
        type=str,
        default=None,
        help="Optional query used to prioritize knowledge-brief friction rows (defaults to config or empty)",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-brief-snapshot-label",
        type=str,
        default=None,
        help="Optional snapshot label for operator-cycle knowledge-brief snapshots",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-brief-report-path",
        type=Path,
        default=None,
        help="Optional report path for knowledge-brief stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-alert-enable",
        action="store_true",
        help="Enable knowledge-brief-delta-alert stage after verify-matrix-alert",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-domains",
        type=str,
        default=None,
        help=(
            "Optional comma-separated domain list for knowledge-delta alert "
            "(defaults to config or quant_finance,kalshi_weather,fitness_apps,market_ml)"
        ),
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-snapshot-dir",
        type=Path,
        default=None,
        help="Optional snapshot directory for knowledge-brief-delta-alert",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-current-snapshot-path",
        type=Path,
        default=None,
        help="Optional explicit current snapshot path for knowledge-brief-delta-alert",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-previous-snapshot-path",
        type=Path,
        default=None,
        help="Optional explicit previous snapshot path for knowledge-brief-delta-alert",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-top-limit",
        type=int,
        default=None,
        help="Optional top-limit override for knowledge-brief-delta rows (defaults to config or 10)",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-alert-domain",
        type=str,
        default=None,
        help="Optional knowledge-delta alert domain override (defaults to config or 'operations')",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-alert-max-items",
        type=int,
        default=None,
        help="Optional max mitigation items for knowledge-delta alert (defaults to config or 3)",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-alert-urgency",
        type=float,
        default=None,
        help="Optional knowledge-delta alert urgency override (0-1)",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-alert-confidence",
        type=float,
        default=None,
        help="Optional knowledge-delta alert confidence override (0-1)",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-min-worsening-score",
        type=int,
        default=None,
        help="Optional minimum worsening-score threshold for knowledge-delta alerts",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-min-urgency-delta",
        type=float,
        default=None,
        help="Optional minimum urgency-delta threshold for knowledge-delta alerts",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-min-failure-rate-delta",
        type=float,
        default=None,
        help="Optional minimum failure-rate delta threshold for knowledge-delta alerts",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-delta-min-blocked-guardrail-delta",
        type=int,
        default=None,
        help="Optional minimum blocked-guardrail delta threshold for knowledge-delta alerts",
    )
    improvement_operator_cycle.add_argument(
        "--knowledge-brief-delta-alert-report-path",
        type=Path,
        default=None,
        help="Optional report path for knowledge-brief-delta-alert stage (defaults under output-dir)",
    )
    improvement_operator_cycle.add_argument("--strict", action="store_true")
    improvement_operator_cycle.add_argument("--json-compact", action="store_true")
    improvement_operator_cycle.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_operator_cycle.add_argument("--db-path", type=Path, default=_default_db_path())
    improvement_operator_cycle.set_defaults(
        allow_missing_feeds=True,
        allow_missing_inputs=True,
        allow_missing_retests=True,
        draft_evidence_pressure_enable=None,
    )

    improvement_verify_matrix = improvement_sub.add_parser(
        "verify-matrix",
        help="Compare controlled experiment matrix expectations against actual pipeline verdicts",
    )
    improvement_verify_matrix.add_argument("--matrix-path", type=Path, required=True)
    improvement_verify_matrix.add_argument(
        "--report-path",
        type=Path,
        required=True,
        help="Path to daily-pipeline report or operator-cycle report",
    )
    improvement_verify_matrix.add_argument("--output-path", type=Path, default=None)
    improvement_verify_matrix.add_argument("--strict", action="store_true")
    improvement_verify_matrix.add_argument("--json-compact", action="store_true")

    improvement_verify_matrix_alert = improvement_sub.add_parser(
        "verify-matrix-alert",
        help="Run matrix verification and create a high-priority delivered interrupt when drift is detected",
    )
    improvement_verify_matrix_alert.add_argument("--matrix-path", type=Path, required=True)
    improvement_verify_matrix_alert.add_argument(
        "--report-path",
        type=Path,
        required=True,
        help="Path to daily-pipeline report or operator-cycle report",
    )
    improvement_verify_matrix_alert.add_argument("--alert-domain", type=str, default="markets")
    improvement_verify_matrix_alert.add_argument(
        "--alert-urgency",
        type=float,
        default=None,
        help="Optional override (0-1). Defaults to severity-based automatic value.",
    )
    improvement_verify_matrix_alert.add_argument(
        "--alert-confidence",
        type=float,
        default=None,
        help="Optional override (0-1). Defaults to severity-based automatic value.",
    )
    improvement_verify_matrix_alert.add_argument("--alert-max-items", type=int, default=3)
    improvement_verify_matrix_alert.add_argument("--output-path", type=Path, default=None)
    improvement_verify_matrix_alert.add_argument("--strict", action="store_true")
    improvement_verify_matrix_alert.add_argument("--json-compact", action="store_true")
    improvement_verify_matrix_alert.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_verify_matrix_alert.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_reconcile_codeowner_review_gate_outputs = improvement_sub.add_parser(
        "reconcile-codeowner-review-gate-outputs",
        help="Extract reconcile-codeowner-review-gate output fields for workflow step outputs",
    )
    improvement_reconcile_codeowner_review_gate_outputs.add_argument(
        "--report-path",
        type=Path,
        default=Path("output/ci/codeowner_review_reconcile.json"),
        help="Path to reconcile_codeowner_review_gate script output payload",
    )
    improvement_reconcile_codeowner_review_gate_outputs.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit reconcile fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_reconcile_codeowner_review_gate_outputs.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_reconcile_codeowner_review_gate_outputs.add_argument("--json-compact", action="store_true")

    improvement_reconcile_codeowner_review_gate_runtime_alert = improvement_sub.add_parser(
        "reconcile-codeowner-review-gate-runtime-alert",
        help="Create required-status-check drift interrupt artifact from reconcile-codeowner-review-gate output payload",
    )
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument(
        "--report-path",
        type=Path,
        default=Path("output/ci/codeowner_review_reconcile_drift_check.json"),
        help="Path to reconcile_codeowner_review_gate dry-run payload",
    )
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument(
        "--rerun-command",
        type=str,
        default=None,
        help="Optional explicit reconcile apply command to emit as repair guidance",
    )
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path for reconcile drift runtime alert artifact",
    )
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit reconcile drift runtime alert fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument("--strict", action="store_true")
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument("--json-compact", action="store_true")
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument(
        "--repo-path",
        type=Path,
        default=_default_repo_path(),
    )
    improvement_reconcile_codeowner_review_gate_runtime_alert.add_argument("--db-path", type=Path, default=None)

    improvement_domain_smoke_outputs = improvement_sub.add_parser(
        "domain-smoke-outputs",
        help="Extract normalized domain-smoke status outputs from per-domain smoke summary artifacts",
    )
    improvement_domain_smoke_outputs.add_argument("--domain", type=str, default=None)
    improvement_domain_smoke_outputs.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("output/ci/domain_smoke"),
        help="Artifact root directory for per-domain smoke artifacts",
    )
    improvement_domain_smoke_outputs.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help="Optional explicit smoke summary path (defaults under artifact root by domain)",
    )
    improvement_domain_smoke_outputs.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit normalized smoke output fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_domain_smoke_outputs.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_domain_smoke_outputs.add_argument("--output-path", type=Path, default=None)
    improvement_domain_smoke_outputs.add_argument("--json-compact", action="store_true")

    improvement_domain_smoke_runtime_alert = improvement_sub.add_parser(
        "domain-smoke-runtime-alert",
        help="Create domain smoke runtime interrupt alert artifact and emit compact routing outputs",
    )
    improvement_domain_smoke_runtime_alert.add_argument("--domain", type=str, default=None)
    improvement_domain_smoke_runtime_alert.add_argument("--smoke-status", type=str, default=None)
    improvement_domain_smoke_runtime_alert.add_argument("--smoke-reason", type=str, default=None)
    improvement_domain_smoke_runtime_alert.add_argument("--summary-path", type=Path, default=None)
    improvement_domain_smoke_runtime_alert.add_argument("--pull-report-path", type=str, default=None)
    improvement_domain_smoke_runtime_alert.add_argument("--leaderboard-report-path", type=str, default=None)
    improvement_domain_smoke_runtime_alert.add_argument("--seed-report-path", type=str, default=None)
    improvement_domain_smoke_runtime_alert.add_argument("--rerun-command", type=str, default=None)
    improvement_domain_smoke_runtime_alert.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path for domain smoke runtime alert artifact",
    )
    improvement_domain_smoke_runtime_alert.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit domain smoke runtime alert fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_domain_smoke_runtime_alert.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_domain_smoke_runtime_alert.add_argument("--strict", action="store_true")
    improvement_domain_smoke_runtime_alert.add_argument("--json-compact", action="store_true")
    improvement_domain_smoke_runtime_alert.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_domain_smoke_runtime_alert.add_argument("--db-path", type=Path, default=None)

    improvement_domain_smoke_cross_domain_compact = improvement_sub.add_parser(
        "domain-smoke-cross-domain-compact",
        help="Build cross-domain domain-smoke compact summary outputs from downloaded smoke artifacts",
    )
    improvement_domain_smoke_cross_domain_compact.add_argument(
        "--artifacts-root",
        type=Path,
        default=Path("output/ci/domain_smoke_artifacts"),
        help="Directory containing downloaded domain-smoke-* artifacts",
    )
    improvement_domain_smoke_cross_domain_compact.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path for cross-domain summary JSON artifact",
    )
    improvement_domain_smoke_cross_domain_compact.add_argument(
        "--markdown-path",
        type=Path,
        default=None,
        help="Path for cross-domain summary markdown artifact",
    )
    improvement_domain_smoke_cross_domain_compact.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit cross-domain compact fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_domain_smoke_cross_domain_compact.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_domain_smoke_cross_domain_compact.add_argument("--json-compact", action="store_true")

    improvement_domain_smoke_cross_domain_runtime_alert = improvement_sub.add_parser(
        "domain-smoke-cross-domain-runtime-alert",
        help="Create cross-domain smoke runtime interrupt alert and refresh aggregate ack/rerun routing fields",
    )
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument(
        "--summary-path",
        type=Path,
        default=Path("output/ci/domain_smoke/domain_smoke_cross_domain_summary.json"),
        help="Path to cross-domain smoke summary JSON artifact",
    )
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument("--warning-count", type=int, default=None)
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument("--blocking-count", type=int, default=None)
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument("--top-domain", type=str, default=None)
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument("--top-risk-score", type=int, default=None)
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path for cross-domain smoke runtime alert artifact",
    )
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit cross-domain runtime alert fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument("--strict", action="store_true")
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument("--json-compact", action="store_true")
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument(
        "--repo-path",
        type=Path,
        default=_default_repo_path(),
    )
    improvement_domain_smoke_cross_domain_runtime_alert.add_argument("--db-path", type=Path, default=None)

    improvement_controlled_matrix_compact = improvement_sub.add_parser(
        "controlled-matrix-compact",
        help="Build compact controlled-matrix drift summary JSON/Markdown artifacts from verify-matrix-alert output",
    )
    improvement_controlled_matrix_compact.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("output/ci/controlled_matrix"),
        help="Artifact root directory for controlled matrix outputs",
    )
    improvement_controlled_matrix_compact.add_argument(
        "--daily-report-path",
        type=Path,
        default=None,
        help="Optional daily pipeline report path (defaults under artifact root)",
    )
    improvement_controlled_matrix_compact.add_argument(
        "--verify-alert-path",
        type=Path,
        default=None,
        help="Optional verify-matrix-alert report path (defaults under artifact root)",
    )
    improvement_controlled_matrix_compact.add_argument("--output-path", type=Path, default=None)
    improvement_controlled_matrix_compact.add_argument("--markdown-path", type=Path, default=None)
    improvement_controlled_matrix_compact.add_argument("--rerun-command", type=str, default=None)
    improvement_controlled_matrix_compact.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit controlled matrix compact fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_controlled_matrix_compact.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_controlled_matrix_compact.add_argument("--json-compact", action="store_true")

    improvement_controlled_matrix_runtime_alert = improvement_sub.add_parser(
        "controlled-matrix-runtime-alert",
        help="Create controlled-matrix runtime interrupt alert artifact and refresh compact summary routing fields",
    )
    improvement_controlled_matrix_runtime_alert.add_argument(
        "--summary-path",
        type=Path,
        default=Path("output/ci/controlled_matrix/controlled_matrix_summary.json"),
        help="Path to controlled matrix compact summary JSON",
    )
    improvement_controlled_matrix_runtime_alert.add_argument(
        "--output-path",
        type=Path,
        default=Path("output/ci/controlled_matrix/controlled_matrix_runtime_alert.json"),
        help="Path for controlled matrix runtime alert artifact",
    )
    improvement_controlled_matrix_runtime_alert.add_argument("--daily-outcome", type=str, default=None)
    improvement_controlled_matrix_runtime_alert.add_argument("--matrix-status", type=str, default=None)
    improvement_controlled_matrix_runtime_alert.add_argument("--drift-severity", type=str, default=None)
    improvement_controlled_matrix_runtime_alert.add_argument("--first-repair-command", type=str, default=None)
    improvement_controlled_matrix_runtime_alert.add_argument("--first-suggested-action", type=str, default=None)
    improvement_controlled_matrix_runtime_alert.add_argument("--rerun-command", type=str, default=None)
    improvement_controlled_matrix_runtime_alert.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit controlled matrix runtime alert fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_controlled_matrix_runtime_alert.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_controlled_matrix_runtime_alert.add_argument("--strict", action="store_true")
    improvement_controlled_matrix_runtime_alert.add_argument("--json-compact", action="store_true")
    improvement_controlled_matrix_runtime_alert.add_argument(
        "--repo-path",
        type=Path,
        default=_default_repo_path(),
    )
    improvement_controlled_matrix_runtime_alert.add_argument("--db-path", type=Path, default=None)

    improvement_verify_matrix_compact = improvement_sub.add_parser(
        "verify-matrix-compact",
        help="Generate compact verify-matrix coverage JSON/Markdown artifacts from an operator-cycle report",
    )
    improvement_verify_matrix_compact.add_argument(
        "--report-path",
        type=Path,
        default=Path("output/ci/operator_cycle/operator_cycle_report.json"),
        help="Path to operator-cycle report (defaults to output/ci/operator_cycle/operator_cycle_report.json)",
    )
    improvement_verify_matrix_compact.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit compact gate fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_verify_matrix_compact.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_verify_matrix_compact.add_argument(
        "--summary-include-markdown",
        action="store_true",
        help="Append verify-matrix compact markdown body to step summary when summary heading is enabled",
    )
    improvement_verify_matrix_compact.add_argument("--output-path", type=Path, default=None)
    improvement_verify_matrix_compact.add_argument("--markdown-path", type=Path, default=None)
    improvement_verify_matrix_compact.add_argument("--strict", action="store_true")
    improvement_verify_matrix_compact.add_argument("--json-compact", action="store_true")

    improvement_verify_matrix_coverage_alert = improvement_sub.add_parser(
        "verify-matrix-coverage-alert",
        help="Create or refresh verify-matrix coverage interrupt alert artifacts from compact coverage payload",
    )
    improvement_verify_matrix_coverage_alert.add_argument(
        "--compact-path",
        type=Path,
        default=Path("output/ci/operator_cycle/verify_matrix_compact.json"),
        help="Path to verify-matrix compact JSON payload",
    )
    improvement_verify_matrix_coverage_alert.add_argument(
        "--output-path",
        type=Path,
        default=Path("output/ci/operator_cycle/verify_matrix_coverage_alert.json"),
        help="Path for verify-matrix coverage alert artifact",
    )
    improvement_verify_matrix_coverage_alert.add_argument("--missing-domain-count", type=int, default=None)
    improvement_verify_matrix_coverage_alert.add_argument("--missing-domains-csv", type=str, default=None)
    improvement_verify_matrix_coverage_alert.add_argument("--first-missing-domain", type=str, default=None)
    improvement_verify_matrix_coverage_alert.add_argument("--compact-status", type=str, default=None)
    improvement_verify_matrix_coverage_alert.add_argument("--recheck-command", type=str, default=None)
    improvement_verify_matrix_coverage_alert.add_argument("--first-unlock-ready-command", type=str, default=None)
    improvement_verify_matrix_coverage_alert.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit verify-matrix coverage alert fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_verify_matrix_coverage_alert.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_verify_matrix_coverage_alert.add_argument("--strict", action="store_true")
    improvement_verify_matrix_coverage_alert.add_argument("--json-compact", action="store_true")
    improvement_verify_matrix_coverage_alert.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_verify_matrix_coverage_alert.add_argument("--db-path", type=Path, default=None)

    improvement_verify_matrix_guardrail_gate = improvement_sub.add_parser(
        "verify-matrix-guardrail-gate",
        help="Evaluate operator-cycle guardrail gate status from operator report payload",
    )
    improvement_verify_matrix_guardrail_gate.add_argument(
        "--report-path",
        type=Path,
        default=Path("output/ci/operator_cycle/operator_cycle_report.json"),
        help="Path to operator-cycle report payload",
    )
    improvement_verify_matrix_guardrail_gate.add_argument(
        "--output-path",
        type=Path,
        default=Path("output/ci/operator_cycle/verify_matrix_guardrail_gate.json"),
        help="Path for guardrail gate output payload",
    )
    improvement_verify_matrix_guardrail_gate.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit guardrail gate fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_verify_matrix_guardrail_gate.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_verify_matrix_guardrail_gate.add_argument("--strict", action="store_true")
    improvement_verify_matrix_guardrail_gate.add_argument("--json-compact", action="store_true")

    improvement_benchmark_frustrations = improvement_sub.add_parser(
        "benchmark-frustrations",
        help="Rank recurring pains, trend acceleration, and implementation win-rates from operator-cycle outputs",
    )
    improvement_benchmark_frustrations.add_argument(
        "--report-path",
        type=Path,
        required=True,
        help="Path to operator-cycle report or daily-pipeline report",
    )
    improvement_benchmark_frustrations.add_argument(
        "--top-limit",
        type=int,
        default=10,
        help="Number of ranked rows to include per section",
    )
    improvement_benchmark_frustrations.add_argument(
        "--evidence-runtime-history-path",
        type=Path,
        default=None,
        help="Optional evidence runtime history JSONL path (defaults near report path)",
    )
    improvement_benchmark_frustrations.add_argument(
        "--evidence-runtime-history-window",
        type=int,
        default=7,
        help="History window size used for unresolved evidence trend scoring",
    )
    improvement_benchmark_frustrations.add_argument("--output-path", type=Path, default=None)
    improvement_benchmark_frustrations.add_argument("--strict", action="store_true")
    improvement_benchmark_frustrations.add_argument("--json-compact", action="store_true")
    improvement_benchmark_frustrations.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_benchmark_frustrations.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_knowledge_brief = improvement_sub.add_parser(
        "knowledge-brief",
        help=(
            "Summarize cross-domain knowledge capacity across frictions, hypotheses, experiments, "
            "debug hotspots, and controlled test candidates"
        ),
    )
    improvement_knowledge_brief.add_argument(
        "--domains",
        type=str,
        default=DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV,
        help="Comma-separated domains (defaults: quant_finance,kalshi_weather,fitness_apps,market_ml)",
    )
    improvement_knowledge_brief.add_argument(
        "--query",
        type=str,
        default="",
        help="Optional query string used to prioritize matching knowledge rows",
    )
    improvement_knowledge_brief.add_argument("--displeasure-limit", type=int, default=8)
    improvement_knowledge_brief.add_argument("--hypothesis-limit", type=int, default=80)
    improvement_knowledge_brief.add_argument("--experiment-limit", type=int, default=120)
    improvement_knowledge_brief.add_argument("--controlled-test-limit", type=int, default=5)
    improvement_knowledge_brief.add_argument("--min-cluster-count", type=int, default=2)
    improvement_knowledge_brief.add_argument(
        "--snapshot-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory for versioned knowledge snapshots "
            "(defaults to <repo>/analysis/improvement/knowledge_snapshots)"
        ),
    )
    improvement_knowledge_brief.add_argument(
        "--snapshot-label",
        type=str,
        default=None,
        help="Optional label included in snapshot metadata and filename hint",
    )
    improvement_knowledge_brief.add_argument(
        "--write-snapshot",
        dest="write_snapshot",
        action="store_true",
        help="Persist a versioned snapshot for this run (default)",
    )
    improvement_knowledge_brief.add_argument(
        "--no-write-snapshot",
        dest="write_snapshot",
        action="store_false",
        help="Skip snapshot persistence for this run",
    )
    improvement_knowledge_brief.add_argument("--output-path", type=Path, default=None)
    improvement_knowledge_brief.add_argument("--strict", action="store_true")
    improvement_knowledge_brief.add_argument("--json-compact", action="store_true")
    improvement_knowledge_brief.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_knowledge_brief.add_argument("--db-path", type=Path, default=_default_db_path())
    improvement_knowledge_brief.set_defaults(write_snapshot=True)

    improvement_knowledge_brief_delta = improvement_sub.add_parser(
        "knowledge-brief-delta",
        help=(
            "Compare two knowledge-brief snapshots and surface domain regressions, accelerating frictions, "
            "and debug-risk deltas"
        ),
    )
    improvement_knowledge_brief_delta.add_argument(
        "--domains",
        type=str,
        default=DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV,
        help="Comma-separated domains to include in delta comparisons",
    )
    improvement_knowledge_brief_delta.add_argument(
        "--snapshot-dir",
        type=Path,
        default=None,
        help="Snapshot directory (defaults to <repo>/analysis/improvement/knowledge_snapshots)",
    )
    improvement_knowledge_brief_delta.add_argument(
        "--current-snapshot-path",
        type=Path,
        default=None,
        help="Optional explicit current snapshot JSON path (defaults to latest snapshot alias)",
    )
    improvement_knowledge_brief_delta.add_argument(
        "--previous-snapshot-path",
        type=Path,
        default=None,
        help="Optional explicit previous snapshot JSON path (defaults to snapshot preceding current)",
    )
    improvement_knowledge_brief_delta.add_argument("--top-limit", type=int, default=10)
    improvement_knowledge_brief_delta.add_argument("--output-path", type=Path, default=None)
    improvement_knowledge_brief_delta.add_argument("--strict", action="store_true")
    improvement_knowledge_brief_delta.add_argument("--json-compact", action="store_true")
    improvement_knowledge_brief_delta.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_knowledge_brief_delta.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_knowledge_brief_delta_alert = improvement_sub.add_parser(
        "knowledge-brief-delta-alert",
        help="Run knowledge-brief delta and create a delivered interrupt when regression thresholds are exceeded",
    )
    improvement_knowledge_brief_delta_alert.add_argument(
        "--domains",
        type=str,
        default=DEFAULT_IMPROVEMENT_KNOWLEDGE_DOMAINS_CSV,
        help="Comma-separated domains to include in delta alert evaluation",
    )
    improvement_knowledge_brief_delta_alert.add_argument(
        "--snapshot-dir",
        type=Path,
        default=None,
        help="Snapshot directory (defaults to <repo>/analysis/improvement/knowledge_snapshots)",
    )
    improvement_knowledge_brief_delta_alert.add_argument(
        "--current-snapshot-path",
        type=Path,
        default=None,
        help="Optional explicit current snapshot JSON path",
    )
    improvement_knowledge_brief_delta_alert.add_argument(
        "--previous-snapshot-path",
        type=Path,
        default=None,
        help="Optional explicit previous snapshot JSON path",
    )
    improvement_knowledge_brief_delta_alert.add_argument("--top-limit", type=int, default=10)
    improvement_knowledge_brief_delta_alert.add_argument("--alert-domain", type=str, default="operations")
    improvement_knowledge_brief_delta_alert.add_argument(
        "--alert-urgency",
        type=float,
        default=None,
        help="Optional override (0-1). Defaults to severity-based automatic value.",
    )
    improvement_knowledge_brief_delta_alert.add_argument(
        "--alert-confidence",
        type=float,
        default=None,
        help="Optional override (0-1). Defaults to severity-based automatic value.",
    )
    improvement_knowledge_brief_delta_alert.add_argument("--alert-max-items", type=int, default=3)
    improvement_knowledge_brief_delta_alert.add_argument("--min-worsening-score", type=int, default=2)
    improvement_knowledge_brief_delta_alert.add_argument("--min-urgency-delta", type=float, default=0.25)
    improvement_knowledge_brief_delta_alert.add_argument("--min-failure-rate-delta", type=float, default=0.05)
    improvement_knowledge_brief_delta_alert.add_argument("--min-blocked-guardrail-delta", type=int, default=1)
    improvement_knowledge_brief_delta_alert.add_argument(
        "--evidence-runtime-history-path",
        type=Path,
        default=None,
        help="Optional evidence runtime history JSONL path used for severity amplification",
    )
    improvement_knowledge_brief_delta_alert.add_argument(
        "--evidence-runtime-history-window",
        type=int,
        default=7,
        help="History window size used when summarizing runtime evidence trend",
    )
    improvement_knowledge_brief_delta_alert.add_argument("--output-path", type=Path, default=None)
    improvement_knowledge_brief_delta_alert.add_argument("--strict", action="store_true")
    improvement_knowledge_brief_delta_alert.add_argument("--json-compact", action="store_true")
    improvement_knowledge_brief_delta_alert.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_knowledge_brief_delta_alert.add_argument("--db-path", type=Path, default=_default_db_path())

    improvement_knowledge_bootstrap_route = improvement_sub.add_parser(
        "knowledge-bootstrap-route",
        help="Resolve a stable automation route from operator-cycle knowledge bootstrap state",
    )
    improvement_knowledge_bootstrap_route.add_argument(
        "--report-path",
        type=Path,
        required=True,
        help="Path to operator-cycle report or operator inbox summary report",
    )
    improvement_knowledge_bootstrap_route.add_argument("--output-path", type=Path, default=None)
    improvement_knowledge_bootstrap_route.add_argument("--strict", action="store_true")
    improvement_knowledge_bootstrap_route.add_argument("--json-compact", action="store_true")

    improvement_knowledge_bootstrap_followup_rerun = improvement_sub.add_parser(
        "knowledge-bootstrap-followup-rerun",
        help="Execute one bootstrap follow-up rerun and regenerate post-bootstrap route artifact",
    )
    improvement_knowledge_bootstrap_followup_rerun.add_argument(
        "--route-artifact-path",
        type=Path,
        default=Path("output/ci/knowledge_bootstrap_route.json"),
        help="Path to initial knowledge bootstrap route artifact JSON",
    )
    improvement_knowledge_bootstrap_followup_rerun.add_argument(
        "--operator-report-path",
        type=Path,
        default=Path("output/ci/operator_cycle/operator_cycle_report.json"),
        help="Path to operator-cycle report generated by follow-up rerun command",
    )
    improvement_knowledge_bootstrap_followup_rerun.add_argument(
        "--post-route-artifact-path",
        type=Path,
        default=Path("output/ci/knowledge_bootstrap_route_post_bootstrap.json"),
        help="Path for regenerated post-bootstrap route artifact JSON",
    )
    improvement_knowledge_bootstrap_followup_rerun.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit bootstrap follow-up fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_knowledge_bootstrap_followup_rerun.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_knowledge_bootstrap_followup_rerun.add_argument("--output-path", type=Path, default=None)
    improvement_knowledge_bootstrap_followup_rerun.add_argument("--strict", action="store_true")
    improvement_knowledge_bootstrap_followup_rerun.add_argument("--json-compact", action="store_true")

    improvement_knowledge_bootstrap_route_outputs = improvement_sub.add_parser(
        "knowledge-bootstrap-route-outputs",
        help="Normalize route outputs from a knowledge bootstrap route artifact",
    )
    improvement_knowledge_bootstrap_route_outputs.add_argument(
        "--artifact-path",
        type=Path,
        default=Path("output/ci/knowledge_bootstrap_route.json"),
        help="Path to knowledge bootstrap route artifact JSON",
    )
    improvement_knowledge_bootstrap_route_outputs.add_argument(
        "--artifact-source",
        type=str,
        default=None,
        help="Optional artifact source label (for example: initial, post_bootstrap)",
    )
    improvement_knowledge_bootstrap_route_outputs.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit normalized fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_knowledge_bootstrap_route_outputs.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_knowledge_bootstrap_route_outputs.add_argument(
        "--summary-include-artifact-source",
        action="store_true",
        help="Include artifact_source in emitted output lines and summary rows",
    )
    improvement_knowledge_bootstrap_route_outputs.add_argument("--output-path", type=Path, default=None)
    improvement_knowledge_bootstrap_route_outputs.add_argument("--strict", action="store_true")
    improvement_knowledge_bootstrap_route_outputs.add_argument("--json-compact", action="store_true")

    improvement_benchmark_stale_fallback_runtime_alert = improvement_sub.add_parser(
        "benchmark-stale-fallback-runtime-alert",
        help="Create an operator interrupt when benchmark stale fallback repeats across route runs",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--route-output-path",
        type=Path,
        required=True,
        help="Path to normalized knowledge-bootstrap route outputs JSON",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path for benchmark stale fallback runtime alert artifact",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--rerun-command",
        type=str,
        default=None,
        help="Optional explicit rerun command to include in alert payload/outputs",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--history-path",
        type=Path,
        default=None,
        help="Optional JSONL path used to persist benchmark stale fallback history rows",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--history-window",
        type=int,
        default=7,
        help="Window size used when summarizing benchmark stale fallback history trend",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--repeat-threshold",
        type=int,
        default=2,
        help="Minimum recent stale fallback count required before creating an interrupt",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--rate-ceiling",
        type=float,
        default=0.6,
        help="Recent stale fallback rolling-rate ceiling used by the consecutive-run gate (0-1)",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--consecutive-runs",
        type=int,
        default=2,
        help="Required number of consecutive runs above rate ceiling before rate gate blocks",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit runtime alert fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument("--strict", action="store_true")
    improvement_benchmark_stale_fallback_runtime_alert.add_argument("--json-compact", action="store_true")
    improvement_benchmark_stale_fallback_runtime_alert.add_argument(
        "--repo-path",
        type=Path,
        default=_default_repo_path(),
    )
    improvement_benchmark_stale_fallback_runtime_alert.add_argument("--db-path", type=Path, default=None)

    improvement_evidence_lookup_batch_outputs = improvement_sub.add_parser(
        "evidence-lookup-batch-outputs",
        help="Normalize operator-cycle batch evidence lookup fields for automation outputs",
    )
    improvement_evidence_lookup_batch_outputs.add_argument(
        "--report-path",
        type=Path,
        default=Path("output/ci/operator_cycle/operator_cycle_report.json"),
        help="Path to operator-cycle report or operator inbox summary report",
    )
    improvement_evidence_lookup_batch_outputs.add_argument(
        "--report-source",
        type=str,
        default=None,
        help="Optional report source label (for example: initial, post_bootstrap)",
    )
    improvement_evidence_lookup_batch_outputs.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit normalized evidence lookup fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_evidence_lookup_batch_outputs.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_evidence_lookup_batch_outputs.add_argument(
        "--summary-include-report-source",
        action="store_true",
        help="Include report_source in emitted output lines and summary rows",
    )
    improvement_evidence_lookup_batch_outputs.add_argument(
        "--summary-include-record-ids",
        action="store_true",
        help="Include record_ids_csv in step summary rows",
    )
    improvement_evidence_lookup_batch_outputs.add_argument("--output-path", type=Path, default=None)
    improvement_evidence_lookup_batch_outputs.add_argument("--strict", action="store_true")
    improvement_evidence_lookup_batch_outputs.add_argument("--json-compact", action="store_true")

    improvement_evidence_lookup_runtime_alert = improvement_sub.add_parser(
        "evidence-lookup-runtime-alert",
        help="Create an operator interrupt when evidence lookup still has unresolved record IDs",
    )
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--report-path",
        type=Path,
        required=True,
        help="Path to evidence-lookup report JSON",
    )
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path for runtime alert artifact (defaults near report path)",
    )
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--rerun-command",
        type=str,
        default=None,
        help="Optional explicit rerun command to include in alert payload/outputs",
    )
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--history-path",
        type=Path,
        default=None,
        help="Optional JSONL path used to persist evidence runtime history rows",
    )
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--history-window",
        type=int,
        default=7,
        help="Window size used when summarizing runtime history trend in payload/outputs",
    )
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--emit-github-output",
        action="store_true",
        help="Emit runtime alert fields to GITHUB_OUTPUT and optional step-summary heading",
    )
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--summary-heading",
        type=str,
        default=None,
        help="Optional heading text appended to GITHUB_STEP_SUMMARY when emit-github-output is enabled",
    )
    improvement_evidence_lookup_runtime_alert.add_argument("--strict", action="store_true")
    improvement_evidence_lookup_runtime_alert.add_argument("--json-compact", action="store_true")
    improvement_evidence_lookup_runtime_alert.add_argument(
        "--repo-path",
        type=Path,
        default=_default_repo_path(),
    )
    improvement_evidence_lookup_runtime_alert.add_argument("--db-path", type=Path, default=None)

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
    if args.cmd == "plans" and args.plans_cmd == "gate-status":
        cmd_plans_gate_status(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "gate-status-all":
        cmd_plans_gate_status_all(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "promote-ready":
        cmd_plans_promote_ready(args)
        return
    if args.cmd == "plans" and args.plans_cmd == "backfill-project-signals":
        cmd_plans_backfill_project_signals(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "cycle-from-file":
        cmd_improvement_cycle_from_file(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "run-experiment-artifact":
        cmd_improvement_run_experiment_artifact(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "seed-hypotheses":
        cmd_improvement_seed_hypotheses(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "pull-feeds":
        cmd_improvement_pull_feeds(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "fitness-leaderboard":
        cmd_improvement_fitness_leaderboard(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "evidence-lookup":
        cmd_improvement_evidence_lookup(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "evidence-lookup-batch-outputs":
        cmd_improvement_evidence_lookup_batch_outputs(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "evidence-lookup-runtime-alert":
        cmd_improvement_evidence_lookup_runtime_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "seed-from-leaderboard":
        cmd_improvement_seed_from_leaderboard(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "draft-experiment-jobs":
        cmd_improvement_draft_experiment_jobs(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "daily-pipeline":
        cmd_improvement_daily_pipeline(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "execute-retests":
        cmd_improvement_execute_retests(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "operator-cycle":
        cmd_improvement_operator_cycle(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "verify-matrix":
        cmd_improvement_verify_matrix(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "verify-matrix-alert":
        cmd_improvement_verify_matrix_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "reconcile-codeowner-review-gate-outputs":
        cmd_improvement_reconcile_codeowner_review_gate_outputs(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "reconcile-codeowner-review-gate-runtime-alert":
        cmd_improvement_reconcile_codeowner_review_gate_runtime_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "domain-smoke-outputs":
        cmd_improvement_domain_smoke_outputs(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "domain-smoke-runtime-alert":
        cmd_improvement_domain_smoke_runtime_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "domain-smoke-cross-domain-compact":
        cmd_improvement_domain_smoke_cross_domain_compact(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "domain-smoke-cross-domain-runtime-alert":
        cmd_improvement_domain_smoke_cross_domain_runtime_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "controlled-matrix-compact":
        cmd_improvement_controlled_matrix_compact(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "controlled-matrix-runtime-alert":
        cmd_improvement_controlled_matrix_runtime_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "verify-matrix-compact":
        cmd_improvement_verify_matrix_compact(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "verify-matrix-coverage-alert":
        cmd_improvement_verify_matrix_coverage_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "verify-matrix-guardrail-gate":
        cmd_improvement_verify_matrix_guardrail_gate(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "benchmark-frustrations":
        cmd_improvement_benchmark_frustrations(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "knowledge-brief":
        cmd_improvement_knowledge_brief(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "knowledge-brief-delta":
        cmd_improvement_knowledge_brief_delta(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "knowledge-brief-delta-alert":
        cmd_improvement_knowledge_brief_delta_alert(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "knowledge-bootstrap-route":
        cmd_improvement_knowledge_bootstrap_route(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "knowledge-bootstrap-followup-rerun":
        cmd_improvement_knowledge_bootstrap_followup_rerun(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "knowledge-bootstrap-route-outputs":
        cmd_improvement_knowledge_bootstrap_route_outputs(args)
        return
    if args.cmd == "improvement" and args.improvement_cmd == "benchmark-stale-fallback-runtime-alert":
        cmd_improvement_benchmark_stale_fallback_runtime_alert(args)
        return

    raise ValueError("Unsupported command")


if __name__ == "__main__":
    main()
