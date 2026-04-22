from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
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
from .improvement import FeedbackFeedPuller
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

    next_action = (
        "Acknowledge each blocking interrupt and rerun plans promote-ready."
        if bool(gate_status.get("blocked"))
        else "No blocking critical drift alerts."
    )
    return {
        "plan_id": str(plan_id),
        "step_id": str(step_id),
        "gate_mode": str(gate_status.get("mode") or "disabled"),
        "blocked": bool(gate_status.get("blocked")),
        "blocking_interrupt_count": len(blocking_interrupt_ids),
        "blocking_interrupt_ids": blocking_interrupt_ids,
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
    lines.append(f"next_action: {payload['next_action']}")
    return "\n".join(lines)


def _render_gate_status_all_ci_summary(payload: dict[str, Any]) -> str:
    blocked_steps = list(payload.get("blocked_steps") or [])
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
    return {
        "scanned_review_count": int(payload.get("scanned_review_count") or 0),
        "evaluated_step_count": int(payload.get("evaluated_step_count") or 0),
        "visible_step_count": int(payload.get("visible_step_count") or 0),
        "blocked_step_count": int(payload.get("blocked_step_count") or 0),
        "error_count": int(payload.get("error_count") or 0),
        "exit_reason": str(payload.get("exit_reason") or ""),
        "exit_code": int(payload.get("exit_code") or 0),
        "exit_triggered": bool(payload.get("exit_triggered")),
        "blocked_exit_triggered": bool(payload.get("blocked_exit_triggered")),
        "error_exit_triggered": bool(payload.get("error_exit_triggered")),
        "zero_scanned_exit_triggered": bool(payload.get("zero_scanned_exit_triggered")),
        "zero_evaluated_exit_triggered": bool(payload.get("zero_evaluated_exit_triggered")),
        "empty_ack_commands_exit_triggered": bool(payload.get("empty_ack_commands_exit_triggered")),
        "blocked_steps": [
            {
                "plan_id": str(item.get("plan_id") or ""),
                "step_id": str(item.get("step_id") or ""),
                "blocking_interrupt_ids": list(item.get("blocking_interrupt_ids") or []),
            }
            for item in list(payload.get("blocked_steps") or [])
        ],
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
        fail_on_blocked = bool(getattr(args, "fail_on_blocked", False))
        fail_on_errors = bool(getattr(args, "fail_on_errors", False))
        fail_on_zero_scanned = bool(getattr(args, "fail_on_zero_scanned", False))
        fail_on_zero_evaluated = bool(getattr(args, "fail_on_zero_evaluated", False))
        fail_on_empty_ack_commands = bool(getattr(args, "fail_on_empty_ack_commands", False))
        blocked_exit_code = max(1, int(getattr(args, "blocked_exit_code", 2) or 2))
        error_exit_code = max(1, int(getattr(args, "error_exit_code", 3) or 3))
        zero_scanned_exit_code = max(1, int(getattr(args, "zero_scanned_exit_code", 5) or 5))
        zero_evaluated_exit_code = max(1, int(getattr(args, "zero_evaluated_exit_code", 4) or 4))
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
        deduped_ack_commands: list[str] = []
        seen_commands: set[str] = set()
        for row in blocked_rows:
            for command in list(row.get("acknowledge_commands") or []):
                normalized = str(command).strip()
                if not normalized or normalized in seen_commands:
                    continue
                seen_commands.add(normalized)
                deduped_ack_commands.append(normalized)
        visible_rows = blocked_rows if only_blocked else rows
        blocked_exit_triggered = fail_on_blocked and bool(blocked_rows)
        error_exit_triggered = fail_on_errors and bool(errors)
        zero_scanned_exit_triggered = fail_on_zero_scanned and len(review_refs) == 0
        zero_evaluated_exit_triggered = fail_on_zero_evaluated and len(review_refs) > 0 and len(rows) == 0
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
            else "No blocking critical drift alerts across scanned review steps."
        )
        payload = {
            "only_blocked": only_blocked,
            "fail_on_blocked": fail_on_blocked,
            "fail_on_errors": fail_on_errors,
            "fail_on_zero_scanned": fail_on_zero_scanned,
            "fail_on_zero_evaluated": fail_on_zero_evaluated,
            "fail_on_empty_ack_commands": fail_on_empty_ack_commands,
            "blocked_exit_code": int(blocked_exit_code),
            "error_exit_code": int(error_exit_code),
            "zero_scanned_exit_code": int(zero_scanned_exit_code),
            "zero_evaluated_exit_code": int(zero_evaluated_exit_code),
            "empty_ack_commands_exit_code": int(empty_ack_commands_exit_code),
            "scanned_review_count": len(review_refs),
            "evaluated_step_count": len(rows),
            "visible_step_count": len(visible_rows),
            "non_blocking_step_count": max(0, len(rows) - len(blocked_rows)),
            "blocked_step_count": len(blocked_rows),
            "error_count": len(errors),
            "blocked_steps": [
                {
                    "plan_id": str(item.get("plan_id") or ""),
                    "step_id": str(item.get("step_id") or ""),
                    "blocking_interrupt_ids": list(item.get("blocking_interrupt_ids") or []),
                    "acknowledge_commands": list(item.get("acknowledge_commands") or []),
                }
                for item in blocked_rows
            ],
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
        output_mode = str(getattr(args, "output", "json") or "json").strip().lower()
        if output_mode == "text":
            lines = [
                f"only_blocked: {'yes' if only_blocked else 'no'}",
                f"fail_on_blocked: {'yes' if fail_on_blocked else 'no'}",
                f"fail_on_errors: {'yes' if fail_on_errors else 'no'}",
                f"fail_on_zero_scanned: {'yes' if fail_on_zero_scanned else 'no'}",
                f"fail_on_zero_evaluated: {'yes' if fail_on_zero_evaluated else 'no'}",
                f"fail_on_empty_ack_commands: {'yes' if fail_on_empty_ack_commands else 'no'}",
                f"blocked_exit_code: {int(blocked_exit_code)}",
                f"error_exit_code: {int(error_exit_code)}",
                f"zero_scanned_exit_code: {int(zero_scanned_exit_code)}",
                f"zero_evaluated_exit_code: {int(zero_evaluated_exit_code)}",
                f"empty_ack_commands_exit_code: {int(empty_ack_commands_exit_code)}",
                f"scanned_review_count: {int(payload['scanned_review_count'])}",
                f"evaluated_step_count: {int(payload['evaluated_step_count'])}",
                f"visible_step_count: {int(payload['visible_step_count'])}",
                f"blocked_step_count: {int(payload['blocked_step_count'])}",
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
                            retest_runs.append(
                                {
                                    "index": index,
                                    **dict(retest_entry),
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
        normalized_jobs.append({"index": index, **dict(item)})
    if not normalized_jobs:
        for index, run in enumerate(list(loaded.get("experiment_runs") or [])):
            if not isinstance(run, dict):
                continue
            retest = run.get("retest")
            if not isinstance(retest, dict):
                continue
            if not bool(retest.get("queued")):
                continue
            normalized_jobs.append(
                {
                    "index": index,
                    **dict(retest),
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


def cmd_improvement_operator_cycle(args: argparse.Namespace) -> None:
    config_path = args.config_path.resolve()
    default_output_dir = (config_path.parent / "output" / "improvement" / "operator_cycle").resolve()
    output_dir = _resolve_path_near(
        default_output_dir.parent,
        args.output_dir,
        default_name=default_output_dir.name,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    pull_report_path = _resolve_path_near(output_dir, None, default_name="pull_feeds_report.json")
    daily_report_path = _resolve_path_near(output_dir, None, default_name="daily_pipeline_report.json")
    retest_report_path = _resolve_path_near(output_dir, None, default_name="retest_execution_report.json")
    inbox_summary_path = _resolve_path_near(
        output_dir,
        args.inbox_summary_path,
        default_name="operator_inbox_summary.json",
    )
    retest_artifact_dir = _resolve_path_near(
        output_dir,
        args.retest_artifact_dir,
        default_name="retest_artifacts",
    )

    stage_errors: list[dict[str, Any]] = []

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

    daily_payload: dict[str, Any]
    try:
        daily_args = argparse.Namespace(
            config_path=config_path,
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
        if verdict in {"blocked_guardrail", "insufficient_data", "needs_iteration", "invalid_measurement"}:
            blockers.append(
                {
                    "stage": "daily_pipeline",
                    "hypothesis_id": row.get("hypothesis_id"),
                    "run_id": row.get("run_id"),
                    "verdict": verdict,
                    "root_cause_hints": list(row.get("root_cause_hints") or []),
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
        retest_deltas.append(
            {
                "hypothesis_id": row.get("hypothesis_id"),
                "trigger_run_id": row.get("trigger_run_id"),
                "run_id": row.get("run_id"),
                "previous_verdict": previous_verdict,
                "current_verdict": current_verdict,
                "metric_transition": side_by_side.get("metric_transition"),
                "sample_transition": side_by_side.get("sample_transition"),
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
                }
            )

    promotions: list[dict[str, Any]] = []
    for row in daily_experiment_runs:
        if str(row.get("verdict") or "").strip().lower() == "promote":
            promotions.append(
                {
                    "stage": "daily_pipeline",
                    "hypothesis_id": row.get("hypothesis_id"),
                    "run_id": row.get("run_id"),
                }
            )
    for row in retest_runs:
        if str(row.get("verdict") or "").strip().lower() == "promote":
            promotions.append(
                {
                    "stage": "execute_retests",
                    "hypothesis_id": row.get("hypothesis_id"),
                    "run_id": row.get("run_id"),
                }
            )

    retest_transition_counts: dict[str, int] = {}
    for row in retest_deltas:
        previous = str(row.get("previous_verdict") or "unknown")
        current = str(row.get("current_verdict") or "unknown")
        key = f"{previous}->{current}"
        retest_transition_counts[key] = int(retest_transition_counts.get(key) or 0) + 1

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
        "promotion_count": len(promotions),
        "retest_delta_count": len(retest_deltas),
    }
    suggested_actions: list[str] = []
    if int(metrics["daily_blocked_guardrail"] or 0) > 0 or int(metrics["retest_blocked_guardrail"] or 0) > 0:
        suggested_actions.append("Prioritize guardrail failures and adjust candidate risk controls before scale-up.")
    if int(metrics["daily_insufficient_data"] or 0) > 0 or int(metrics["retest_insufficient_data"] or 0) > 0:
        suggested_actions.append("Increase sample sizes for unresolved hypotheses in controlled cohorts.")
    if int(metrics["retest_promotions"] or 0) > 0:
        suggested_actions.append("Promote retest winners to the next validation stage and monitor live guardrails.")
    if not suggested_actions:
        suggested_actions.append("No urgent blockers detected; continue ingesting feedback and validating new hypotheses.")

    stage_statuses = {
        "pull_feeds": str(pull_payload.get("status") or ""),
        "daily_pipeline": str(daily_payload.get("status") or ""),
        "execute_retests": str(retest_payload.get("status") or ""),
    }
    stage_error_count = int(pull_payload.get("error_count") or 0) + int(daily_payload.get("error_count") or 0) + int(
        retest_payload.get("error_count") or 0
    )
    overall_status = "ok"
    if stage_errors or stage_error_count > 0 or any(status == "error" for status in stage_statuses.values()):
        overall_status = "warning"

    inbox_summary = {
        "generated_at": utc_now_iso(),
        "config_path": str(config_path),
        "output_dir": str(output_dir),
        "stage_statuses": stage_statuses,
        "metrics": metrics,
        "promotions": promotions,
        "blockers": blockers,
        "retest_deltas": retest_deltas,
        "retest_transition_counts": retest_transition_counts,
        "suggested_actions": suggested_actions,
    }
    inbox_summary_path.parent.mkdir(parents=True, exist_ok=True)
    inbox_summary_path.write_text(json.dumps(inbox_summary, indent=2), encoding="utf-8")

    payload = {
        "generated_at": utc_now_iso(),
        "status": overall_status,
        "config_path": str(config_path),
        "output_dir": str(output_dir),
        "pull_report_path": str(pull_report_path),
        "daily_report_path": str(daily_report_path),
        "retest_report_path": str(retest_report_path),
        "inbox_summary_path": str(inbox_summary_path),
        "stage_statuses": stage_statuses,
        "stage_error_count": stage_error_count,
        "stage_errors": stage_errors,
        "summary": inbox_summary,
    }
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

    total_scenarios = len(scenarios)
    verification_status = "ok"
    if mismatch_count > 0 or missing_count > 0 or invalid_count > 0:
        verification_status = "warning"

    summary = {
        "total_scenarios": total_scenarios,
        "matched_count": matched_count,
        "mismatch_count": mismatch_count,
        "missing_count": missing_count,
        "invalid_count": invalid_count,
        "match_rate": round((matched_count / total_scenarios), 4) if total_scenarios > 0 else 0.0,
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
    }


def _classify_matrix_drift_severity(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    rows = [row for row in list(payload.get("comparisons") or []) if isinstance(row, dict)]
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
    guardrail_mismatch_count = len(guardrail_mismatches)
    total_issues = mismatch_count + missing_count + invalid_count

    if total_issues <= 0:
        return {
            "severity": "none",
            "score": 0,
            "total_issues": 0,
            "mismatch_count": mismatch_count,
            "missing_count": missing_count,
            "invalid_count": invalid_count,
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
        mismatch_rows = [row for row in comparisons if str(row.get("status") or "") == "mismatch"][:top_items]
        missing_rows = [row for row in comparisons if str(row.get("status") or "") == "missing_run"][:top_items]
        invalid_rows = [row for row in comparisons if str(row.get("status") or "") == "invalid_scenario"][:top_items]

        scenario_refs = [
            str(row.get("scenario_id") or f"scenario_{idx}")
            for idx, row in enumerate([*mismatch_rows, *missing_rows, *invalid_rows])
        ]
        compact_refs = ",".join(scenario_refs[:top_items]) if scenario_refs else "none"
        reason = (
            "matrix_drift_detected"
            + f" severity={drift_severity}"
            + f" mismatches={int(summary.get('mismatch_count') or 0)}"
            + f" missing={int(summary.get('missing_count') or 0)}"
            + f" invalid={int(summary.get('invalid_count') or 0)}"
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
        help="Run pull->daily->retest pipeline and emit a single operator inbox summary report",
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
    improvement_operator_cycle.add_argument("--strict", action="store_true")
    improvement_operator_cycle.add_argument("--json-compact", action="store_true")
    improvement_operator_cycle.add_argument("--repo-path", type=Path, default=_default_repo_path())
    improvement_operator_cycle.add_argument("--db-path", type=Path, default=_default_db_path())
    improvement_operator_cycle.set_defaults(
        allow_missing_feeds=True,
        allow_missing_inputs=True,
        allow_missing_retests=True,
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

    raise ValueError("Unsupported command")


if __name__ == "__main__":
    main()
