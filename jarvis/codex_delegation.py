from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from .models import new_id, utc_now_iso

EFFORT_TIER_PRIORITY: tuple[str, ...] = (
    "deep_research",
    "extended_thinking",
    "pro",
    "thinking",
    "instant",
)

EFFORT_TIER_LABELS: dict[str, str] = {
    "instant": "instant",
    "thinking": "thinking",
    "pro": "pro",
    "extended_thinking": "extended thinking",
    "deep_research": "deep research",
}

EFFORT_TIER_REASONING_EFFORT: dict[str, str] = {
    "instant": "low",
    "thinking": "medium",
    "pro": "high",
    "extended_thinking": "high",
    "deep_research": "xhigh",
}

EFFORT_TIER_TIMEOUT_MULTIPLIER: dict[str, float] = {
    "instant": 0.75,
    "thinking": 1.0,
    "pro": 1.35,
    "extended_thinking": 1.9,
    "deep_research": 2.4,
}

EFFORT_TIER_ALIASES: dict[str, str] = {
    "instant": "instant",
    "quick": "instant",
    "fast": "instant",
    "thinking": "thinking",
    "think": "thinking",
    "pro": "pro",
    "professional": "pro",
    "extended": "extended_thinking",
    "extended_thinking": "extended_thinking",
    "extended thinking": "extended_thinking",
    "deep": "deep_research",
    "deep_research": "deep_research",
    "deep research": "deep_research",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _safe_excerpt(text: str | None, *, limit: int = 12000) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit]


def _normalize_effort_tier(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    compact = raw.replace("-", "_")
    compact = re.sub(r"\s+", " ", compact)
    if compact in EFFORT_TIER_ALIASES:
        return EFFORT_TIER_ALIASES[compact]
    compact_key = compact.replace(" ", "_")
    return EFFORT_TIER_ALIASES.get(compact_key)


def _tier_label(tier: str | None) -> str:
    key = _normalize_effort_tier(tier) or "thinking"
    return EFFORT_TIER_LABELS.get(key, "thinking")


def _reasoning_effort_for_tier(tier: str | None) -> str:
    key = _normalize_effort_tier(tier) or "thinking"
    return EFFORT_TIER_REASONING_EFFORT.get(key, "medium")


def _timeout_multiplier_for_tier(tier: str | None) -> float:
    key = _normalize_effort_tier(tier) or "thinking"
    return float(EFFORT_TIER_TIMEOUT_MULTIPLIER.get(key, 1.0))


class CodexTaskStore:
    """SQLite-backed store for JARVIS -> Codex delegation tasks."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_tasks (
                    task_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_surface TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    write_enabled INTEGER NOT NULL,
                    auto_execute INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    completed_at TEXT,
                    exit_code INTEGER,
                    last_message TEXT,
                    stdout_excerpt TEXT,
                    stderr_excerpt TEXT,
                    run_dir TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_text TEXT,
                    context_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_codex_tasks_created
                ON codex_tasks(created_at DESC)
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_codex_tasks_status
                ON codex_tasks(status, updated_at DESC)
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_codex_tasks_dedupe
                ON codex_tasks(dedupe_key, status)
                """
            )
            self.conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        context_map = json.loads(row["context_json"] or "{}")
        effort_tier = _normalize_effort_tier(context_map.get("effort_tier")) or "thinking"
        reasoning_effort = str(context_map.get("reasoning_effort") or "").strip().lower() or _reasoning_effort_for_tier(
            effort_tier
        )
        return {
            "task_id": row["task_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "source_surface": row["source_surface"],
            "session_id": row["session_id"],
            "actor": row["actor"],
            "prompt": row["prompt"],
            "dedupe_key": row["dedupe_key"],
            "write_enabled": bool(row["write_enabled"]),
            "auto_execute": bool(row["auto_execute"]),
            "status": row["status"],
            "attempt_count": int(row["attempt_count"] or 0),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "exit_code": row["exit_code"],
            "last_message": row["last_message"],
            "stdout_excerpt": row["stdout_excerpt"],
            "stderr_excerpt": row["stderr_excerpt"],
            "run_dir": row["run_dir"],
            "result": json.loads(row["result_json"] or "{}"),
            "error_text": row["error_text"],
            "context": context_map,
            "effort_tier": effort_tier,
            "effort_label": _tier_label(effort_tier),
            "reasoning_effort": reasoning_effort,
        }

    def find_active_duplicate(self, *, dedupe_key: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT *
                FROM codex_tasks
                WHERE dedupe_key = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def create_task(
        self,
        *,
        source_surface: str,
        session_id: str,
        actor: str,
        prompt: str,
        dedupe_key: str,
        write_enabled: bool,
        auto_execute: bool,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        task_id = new_id("cdx")
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO codex_tasks (
                    task_id, created_at, updated_at, source_surface, session_id, actor, prompt,
                    dedupe_key, write_enabled, auto_execute, status, context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    task_id,
                    now,
                    now,
                    source_surface,
                    session_id,
                    actor,
                    prompt,
                    dedupe_key,
                    1 if write_enabled else 0,
                    1 if auto_execute else 0,
                    json.dumps(dict(context or {}), sort_keys=True),
                ),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM codex_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise RuntimeError("Failed to create codex task.")
        return self._row_to_dict(row)

    def mark_running(self, *, task_id: str, run_dir: str) -> dict[str, Any]:
        now = utc_now_iso()
        with self.lock:
            self.conn.execute(
                """
                UPDATE codex_tasks
                SET status = 'running',
                    updated_at = ?,
                    started_at = COALESCE(started_at, ?),
                    attempt_count = attempt_count + 1,
                    run_dir = ?,
                    error_text = NULL
                WHERE task_id = ?
                """,
                (now, now, run_dir, task_id),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM codex_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        return self._row_to_dict(row)

    def mark_finished(
        self,
        *,
        task_id: str,
        status: str,
        exit_code: int | None,
        last_message: str | None,
        stdout_excerpt: str | None,
        stderr_excerpt: str | None,
        result: dict[str, Any] | None = None,
        error_text: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.lock:
            self.conn.execute(
                """
                UPDATE codex_tasks
                SET status = ?,
                    updated_at = ?,
                    completed_at = ?,
                    exit_code = ?,
                    last_message = ?,
                    stdout_excerpt = ?,
                    stderr_excerpt = ?,
                    result_json = ?,
                    error_text = ?
                WHERE task_id = ?
                """,
                (
                    status,
                    now,
                    now,
                    exit_code,
                    last_message,
                    stdout_excerpt,
                    stderr_excerpt,
                    json.dumps(dict(result or {}), sort_keys=True),
                    error_text,
                    task_id,
                ),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM codex_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        return self._row_to_dict(row)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM codex_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit or 50), 250))
        if status and str(status).strip() and str(status).strip().lower() != "all":
            with self.lock:
                rows = self.conn.execute(
                    """
                    SELECT * FROM codex_tasks
                    WHERE status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (str(status).strip().lower(), capped),
                ).fetchall()
        else:
            with self.lock:
                rows = self.conn.execute(
                    """
                    SELECT * FROM codex_tasks
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (capped,),
                ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def summarize(self, *, limit: int = 200) -> dict[str, Any]:
        rows = self.list(status="all", limit=limit)
        counts: dict[str, int] = {}
        tier_counts: dict[str, int] = {}
        for row in rows:
            key = str(row.get("status") or "unknown").lower()
            counts[key] = counts.get(key, 0) + 1
            tier = _normalize_effort_tier(row.get("effort_tier")) or "thinking"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        return {
            "total": sum(counts.values()),
            "by_status": counts,
            "by_effort_tier": tier_counts,
            "limit": limit,
        }

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class CodexDelegationService:
    """Classify, queue, and execute Codex tasks as JARVIS' code execution arm."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        repo_path: str | Path,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.store = CodexTaskStore(db_path)
        self.runner = runner or subprocess.run
        self.runtime_root = self.repo_path / ".jarvis" / "runtime" / "codex_tasks"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.Lock()

    @staticmethod
    def classify_work_item(
        *,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_text = str(text or "").strip()
        normalized = re.sub(r"\s+", " ", raw_text.lower())
        context_map = dict(context or {})
        adaptive_policy = context_map.get("adaptive_policy") if isinstance(context_map.get("adaptive_policy"), dict) else {}
        tiering_policy = (
            adaptive_policy.get("tiering")
            if isinstance(adaptive_policy.get("tiering"), dict)
            else {}
        )
        instant_max_words = int(context_map.get("tier_instant_max_words") or tiering_policy.get("instant_max_words") or 6)
        pro_min_words = int(context_map.get("tier_pro_min_words") or tiering_policy.get("pro_min_words") or 20)
        extended_min_words = int(
            context_map.get("tier_extended_min_words") or tiering_policy.get("extended_min_words") or 40
        )
        deep_min_words = int(
            context_map.get("tier_deep_research_min_words") or tiering_policy.get("deep_research_min_words") or 70
        )
        instant_max_words = max(1, min(instant_max_words, 30))
        pro_min_words = max(5, min(pro_min_words, 120))
        extended_min_words = max(pro_min_words, min(extended_min_words, 220))
        deep_min_words = max(extended_min_words, min(deep_min_words, 320))

        explicit_tier = (
            _normalize_effort_tier(context_map.get("effort_tier"))
            or _normalize_effort_tier(context_map.get("reasoning_tier"))
            or _normalize_effort_tier(context_map.get("tier"))
            or _normalize_effort_tier(context_map.get("work_item_tier"))
            or _normalize_effort_tier(context_map.get("effort_level"))
        )
        explicit_reasoning_effort = str(context_map.get("reasoning_effort") or "").strip().lower()

        if explicit_tier:
            tier = explicit_tier
            matched_signals = [f"explicit_tier:{explicit_tier}"]
            score_map = {name: 0 for name in EFFORT_TIER_PRIORITY}
            score_map[tier] = 99
        else:
            score_map: dict[str, int] = {name: 0 for name in EFFORT_TIER_PRIORITY}
            matched_signals: list[str] = []

            lexical_rules: list[tuple[str, tuple[str, ...], int]] = [
                (
                    "deep_research",
                    (
                        "deep research",
                        "research",
                        "citations",
                        "sources",
                        "source links",
                        "verify",
                        "benchmark",
                        "literature",
                        "latest",
                        "up-to-date",
                    ),
                    4,
                ),
                (
                    "extended_thinking",
                    (
                        "extended thinking",
                        "extended",
                        "comprehensive",
                        "thorough",
                        "deep dive",
                        "full review",
                        "end-to-end analysis",
                    ),
                    3,
                ),
                (
                    "pro",
                    (
                        "production",
                        "professional",
                        "ship-ready",
                        "fully wire",
                        "fully wired",
                        "100%",
                        "full implementation",
                        "not bandaid",
                        "fix it 100",
                    ),
                    3,
                ),
                (
                    "thinking",
                    (
                        "thinking",
                        "think",
                        "analyze",
                        "analysis",
                        "reason",
                        "walk me through",
                        "explain",
                    ),
                    2,
                ),
                (
                    "instant",
                    (
                        "instant",
                        "quick",
                        "quickly",
                        "asap",
                        "one-liner",
                        "one liner",
                        "brief",
                        "short answer",
                    ),
                    2,
                ),
            ]
            for tier_key, phrases, points in lexical_rules:
                for phrase in phrases:
                    if phrase in normalized:
                        score_map[tier_key] += points
                        matched_signals.append(f"phrase:{phrase}")

            word_count = len([token for token in normalized.split(" ") if token.strip()])
            if word_count <= instant_max_words:
                score_map["instant"] += 2
                matched_signals.append("heuristic:short_prompt")
            elif word_count >= pro_min_words:
                score_map["pro"] += 1
                matched_signals.append("heuristic:long_prompt")
            if word_count >= extended_min_words:
                score_map["extended_thinking"] += 2
                matched_signals.append("heuristic:very_long_prompt")
            if word_count >= deep_min_words:
                score_map["deep_research"] += 2
                matched_signals.append("heuristic:extreme_length")
            if "?" in raw_text and any(token in normalized for token in ("why", "how", "tradeoff", "risk")):
                score_map["thinking"] += 2
                matched_signals.append("heuristic:question_reasoning")
            if any(token in normalized for token in ("file", "code", "repo", "app", "backend", "frontend")):
                score_map["pro"] += 1
                matched_signals.append("heuristic:code_scope")

            tier = max(
                EFFORT_TIER_PRIORITY,
                key=lambda name: (score_map.get(name, 0), -EFFORT_TIER_PRIORITY.index(name)),
            )
            if all((score_map.get(name, 0) <= 0 for name in EFFORT_TIER_PRIORITY)):
                tier = "thinking"
                matched_signals.append("heuristic:default_thinking")

        reasoning_effort = explicit_reasoning_effort or _reasoning_effort_for_tier(tier)
        tier_label = _tier_label(tier)
        return {
            "effort_tier": tier,
            "effort_label": tier_label,
            "reasoning_effort": reasoning_effort,
            "signals": matched_signals,
            "scores": score_map,
            "is_explicit": bool(explicit_tier),
        }

    @staticmethod
    def classify_intent(
        *,
        text: str,
        explicit_directive: bool = False,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_text = str(text or "").strip()
        lowered = raw_text.lower()
        normalized = re.sub(r"\s+", " ", lowered)
        context_map = dict(context or {})
        adaptive_policy = context_map.get("adaptive_policy") if isinstance(context_map.get("adaptive_policy"), dict) else {}
        routing_policy = (
            adaptive_policy.get("routing")
            if isinstance(adaptive_policy.get("routing"), dict)
            else {}
        )
        force = context_map.get("codex_delegate")
        if isinstance(force, bool):
            force_delegate = force
        else:
            force_delegate = str(force or "").strip().lower() in {"1", "true", "yes", "on"}

        app_tokens = (
            "app",
            "repo",
            "code",
            "file",
            "frontend",
            "backend",
            "ui",
            "server",
            "project",
        )
        write_tokens = (
            "change",
            "edit",
            "modify",
            "implement",
            "fix",
            "update",
            "refactor",
            "rewrite",
            "patch",
            "add",
            "remove",
        )
        read_tokens = (
            "figure out",
            "diagnose",
            "inspect",
            "analyze",
            "review",
            "investigate",
            "why",
            "how",
        )
        def _contains_phrase_or_word(token: str) -> bool:
            value = str(token or "").strip().lower()
            if not value:
                return False
            if " " in value:
                return value in normalized
            return re.search(rf"\b{re.escape(value)}\b", normalized) is not None

        has_app_scope = any(_contains_phrase_or_word(token) for token in app_tokens)
        has_write_signal = any(_contains_phrase_or_word(token) for token in write_tokens)
        has_read_signal = any(_contains_phrase_or_word(token) for token in read_tokens)
        mentions_codex = "codex" in normalized or "chatgpt tools" in normalized
        mentions_gpt = any(token in normalized for token in ("chatgpt", "gpt", "reason about", "explain"))
        routing_query = any(
            phrase in normalized
            for phrase in (
                "codex or gpt",
                "gpt or codex",
                "which engine",
                "what engine",
                "engine route",
                "routing decision",
                "route this",
                "what tier",
                "which tier",
                "what effort tier",
            )
        )

        read_only_hint = any(
            phrase in normalized
            for phrase in ("read only", "readonly", "no write", "dont change", "don't change")
        )
        write_enabled = has_write_signal or bool(explicit_directive)
        if read_only_hint:
            write_enabled = False

        forced_engine_raw = str(
            context_map.get("execution_engine")
            or context_map.get("engine")
            or context_map.get("route_engine")
            or ""
        ).strip().lower()
        forced_engine = forced_engine_raw if forced_engine_raw in {"codex", "gpt"} else None

        should_delegate = bool(force_delegate or mentions_codex or (has_app_scope and (has_write_signal or has_read_signal)))
        app_scope_weight = float(
            context_map.get("route_app_scope_weight")
            or routing_policy.get("app_scope_weight")
            or 1.0
        )
        write_signal_weight = float(
            context_map.get("route_write_signal_weight")
            or routing_policy.get("write_signal_weight")
            or 1.0
        )
        read_signal_weight = float(
            context_map.get("route_read_signal_weight")
            or routing_policy.get("read_signal_weight")
            or 0.7
        )
        codex_bias = float(context_map.get("route_codex_bias") or routing_policy.get("codex_bias") or 0.0)
        gpt_bias = float(context_map.get("route_gpt_bias") or routing_policy.get("gpt_bias") or 0.0)
        delegate_score_threshold = float(
            context_map.get("route_delegate_score_threshold")
            or routing_policy.get("delegate_score_threshold")
            or 1.5
        )
        route_score = 0.0
        if has_app_scope:
            route_score += app_scope_weight
        if has_write_signal:
            route_score += write_signal_weight
        if has_read_signal:
            route_score += read_signal_weight
        if mentions_codex:
            route_score += 2.0
        if mentions_gpt:
            route_score -= 0.3
        route_score += codex_bias
        route_score -= gpt_bias
        score_delegate = route_score >= max(0.2, delegate_score_threshold)
        should_delegate = bool(should_delegate or score_delegate)
        if forced_engine == "codex":
            should_delegate = bool(raw_text)
        elif forced_engine == "gpt":
            should_delegate = False
        routing_query_forces_gpt = context_map.get("routing_query_forces_gpt")
        if routing_query_forces_gpt is None:
            routing_query_forces_gpt = routing_policy.get("routing_query_forces_gpt", True)
        if isinstance(routing_query_forces_gpt, str):
            routing_query_forces_gpt = routing_query_forces_gpt.strip().lower() in {"1", "true", "yes", "on"}
        if routing_query and bool(routing_query_forces_gpt):
            should_delegate = False

        if not raw_text:
            should_delegate = False
        engine_route = "codex" if should_delegate else "gpt"
        if forced_engine == "gpt":
            route_reason = "forced_gpt"
        elif forced_engine == "codex":
            route_reason = "forced_codex"
        elif routing_query:
            route_reason = "routing_query"
        elif force_delegate:
            route_reason = "codex_delegate_flag"
        elif mentions_codex:
            route_reason = "mentions_codex"
        elif has_app_scope and (has_write_signal or has_read_signal):
            route_reason = "code_or_app_scope"
        elif score_delegate:
            route_reason = "routing_score_threshold"
        elif mentions_gpt:
            route_reason = "gpt_reasoning_scope"
        else:
            route_reason = "default_gpt"

        work_item = CodexDelegationService.classify_work_item(text=raw_text, context=context_map)
        explicit_tier = any(
            str(context_map.get(key) or "").strip()
            for key in ("effort_tier", "reasoning_tier", "tier", "work_item_tier", "effort_level")
        )
        if routing_query and not explicit_tier:
            work_item["effort_tier"] = "thinking"
            work_item["effort_label"] = "thinking"
            work_item["reasoning_effort"] = "medium"
            signals = work_item.get("signals") if isinstance(work_item.get("signals"), list) else []
            if "heuristic:routing_query_default_tier" not in signals:
                signals.append("heuristic:routing_query_default_tier")
            work_item["signals"] = signals
        return {
            "should_delegate": should_delegate,
            "write_enabled": bool(write_enabled),
            "has_app_scope": has_app_scope,
            "has_write_signal": has_write_signal,
            "has_read_signal": has_read_signal,
            "mentions_codex": mentions_codex,
            "mentions_gpt": mentions_gpt,
            "routing_query": routing_query,
            "read_only_hint": read_only_hint,
            "explicit_directive": bool(explicit_directive),
            "engine_route": engine_route,
            "route_reason": route_reason,
            "forced_engine": forced_engine,
            "route_score": round(route_score, 4),
            "effort_tier": work_item.get("effort_tier"),
            "effort_label": work_item.get("effort_label"),
            "reasoning_effort": work_item.get("reasoning_effort"),
            "effort_signals": work_item.get("signals"),
        }

    @staticmethod
    def _build_prompt(
        *,
        user_text: str,
        write_enabled: bool,
        source_surface: str,
        session_id: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        mode = "read/write implementation" if write_enabled else "analysis/read-only"
        context_map = dict(context or {})
        requested_path = str(context_map.get("target_path") or context_map.get("path") or "").strip()
        adaptive_revision = str(context_map.get("adaptive_policy_revision") or "").strip()
        effort_tier = _normalize_effort_tier(context_map.get("effort_tier")) or "thinking"
        reasoning_effort = str(context_map.get("reasoning_effort") or "").strip().lower() or _reasoning_effort_for_tier(
            effort_tier
        )
        lines = [
            "You are Codex acting as the execution arm for JARVIS.",
            f"Execution mode: {mode}.",
            f"Conversation surface: {source_surface or 'unknown'}.",
            f"Session id: {session_id or 'default'}.",
            f"Requested work tier: {_tier_label(effort_tier)}.",
            f"Reasoning effort target: {reasoning_effort}.",
            (f"Adaptive policy revision: {adaptive_revision}." if adaptive_revision else "Adaptive policy revision: unknown."),
            "",
            "User objective:",
            user_text.strip() or "(no objective text provided)",
            "",
        ]
        if requested_path:
            lines.extend(
                [
                    f"Primary target path hint: {requested_path}",
                    "",
                ]
            )
        lines.extend(
            [
                "Required output:",
                "- concise summary of what you changed/found",
                "- explicit file paths touched",
                "- tests or checks you ran and their result",
            ]
        )
        if write_enabled:
            lines.append("- apply concrete repo changes directly when needed")
        else:
            lines.append("- do not modify files")
        return "\n".join(lines).strip()

    def _dedupe_key(self, *, source_surface: str, session_id: str, prompt: str) -> str:
        normalized = " ".join(str(prompt or "").lower().split())
        digest = hashlib.sha256(f"{source_surface}|{session_id}|{normalized}".encode("utf-8")).hexdigest()
        return digest[:40]

    def submit_task(
        self,
        *,
        user_text: str,
        source_surface: str,
        session_id: str,
        actor: str,
        write_enabled: bool,
        auto_execute: bool,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_map = dict(context or {})
        work_item = self.classify_work_item(text=user_text, context=context_map)
        context_map.setdefault("effort_tier", work_item.get("effort_tier"))
        context_map.setdefault("effort_label", work_item.get("effort_label"))
        context_map.setdefault("reasoning_effort", work_item.get("reasoning_effort"))
        context_map.setdefault("effort_signals", work_item.get("signals"))
        context_map.setdefault("effort_scores", work_item.get("scores"))
        prompt = self._build_prompt(
            user_text=user_text,
            write_enabled=write_enabled,
            source_surface=source_surface,
            session_id=session_id,
            context=context_map,
        )
        dedupe_key = self._dedupe_key(
            source_surface=source_surface,
            session_id=session_id,
            prompt=prompt,
        )
        existing = self.store.find_active_duplicate(dedupe_key=dedupe_key)
        if existing:
            return {
                "ok": True,
                "duplicate": True,
                "task": existing,
                "execution": {
                    "background": bool(existing.get("status") == "running"),
                    "status": existing.get("status"),
                },
                "work_item": {
                    "effort_tier": existing.get("effort_tier"),
                    "effort_label": existing.get("effort_label"),
                    "reasoning_effort": existing.get("reasoning_effort"),
                    "signals": (
                        (existing.get("context") or {}).get("effort_signals")
                        if isinstance(existing.get("context"), dict)
                        else []
                    ),
                    "scores": (
                        (existing.get("context") or {}).get("effort_scores")
                        if isinstance(existing.get("context"), dict)
                        else {}
                    ),
                    "is_explicit": False,
                },
            }

        task = self.store.create_task(
            source_surface=source_surface,
            session_id=session_id,
            actor=actor,
            prompt=prompt,
            dedupe_key=dedupe_key,
            write_enabled=write_enabled,
            auto_execute=auto_execute,
            context=context_map,
        )
        execution: dict[str, Any] = {"background": False, "status": "queued"}
        if auto_execute:
            execution = self.execute_task(task["task_id"], background=True)
            task = self.store.get(task["task_id"]) or task
        return {
            "ok": True,
            "duplicate": False,
            "task": task,
            "execution": execution,
            "work_item": work_item,
        }

    def _build_codex_command(self, *, task: dict[str, Any], last_message_path: Path) -> list[str]:
        context = task.get("context") if isinstance(task.get("context"), dict) else {}
        tier = _normalize_effort_tier(context.get("effort_tier")) or _normalize_effort_tier(task.get("effort_tier")) or "thinking"
        tier_env = str(tier).upper()
        reasoning_effort = str(context.get("reasoning_effort") or "").strip().lower() or _reasoning_effort_for_tier(tier)
        model = (
            str(context.get("codex_model") or "").strip()
            or str(os.getenv(f"JARVIS_CODEX_MODEL_{tier_env}") or "").strip()
            or str(os.getenv("JARVIS_CODEX_MODEL") or "").strip()
        )

        cmd = [
            "codex",
            "exec",
            "--cd",
            str(self.repo_path),
            "--output-last-message",
            str(last_message_path),
        ]
        if model:
            cmd.extend(["-m", model])
        if reasoning_effort:
            cmd.extend(["-c", f'reasoning_effort="{reasoning_effort}"'])

        dangerous = _env_bool("JARVIS_CODEX_DANGEROUS_MODE", False)
        write_enabled = bool(task.get("write_enabled"))
        if dangerous:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            if write_enabled:
                cmd.append("--full-auto")
                cmd.extend(["-s", "workspace-write"])
            else:
                cmd.extend(["-s", "read-only"])
        cmd.append(str(task.get("prompt") or ""))
        return cmd

    def _git_changed_files(self) -> list[str]:
        proc = subprocess.run(
            ["git", "-C", str(self.repo_path), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return []
        files: list[str] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
        return sorted(set(files))

    def _execute_worker(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task:
            return
        run_dir = self.runtime_root / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = run_dir / "prompt.txt"
        last_message_path = run_dir / "last_message.txt"
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        prompt_path.write_text(str(task.get("prompt") or ""), encoding="utf-8")
        self.store.mark_running(task_id=task_id, run_dir=str(run_dir))
        cmd = self._build_codex_command(task=task, last_message_path=last_message_path)
        context = task.get("context") if isinstance(task.get("context"), dict) else {}
        effort_tier = _normalize_effort_tier(context.get("effort_tier")) or _normalize_effort_tier(task.get("effort_tier")) or "thinking"
        reasoning_effort = str(context.get("reasoning_effort") or "").strip().lower() or _reasoning_effort_for_tier(
            effort_tier
        )
        timeout_base = max(30.0, float(os.getenv("JARVIS_CODEX_EXEC_TIMEOUT_SECONDS") or "1500"))
        timeout_multiplier = _timeout_multiplier_for_tier(effort_tier)
        timeout_seconds = max(30, int(timeout_base * timeout_multiplier))

        try:
            completed = self.runner(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            stdout_text = str(completed.stdout or "")
            stderr_text = str(completed.stderr or "")
            stdout_path.write_text(stdout_text, encoding="utf-8")
            stderr_path.write_text(stderr_text, encoding="utf-8")
            last_message = ""
            if last_message_path.exists():
                try:
                    last_message = last_message_path.read_text(encoding="utf-8")
                except OSError:
                    last_message = ""
            changed_files = self._git_changed_files() if bool(task.get("write_enabled")) else []
            result = {
                "command": cmd,
                "timeout_seconds": timeout_seconds,
                "timeout_multiplier": timeout_multiplier,
                "effort_tier": effort_tier,
                "effort_label": _tier_label(effort_tier),
                "reasoning_effort": reasoning_effort,
                "changed_files": changed_files,
                "last_message_path": str(last_message_path),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
            status = "completed" if int(completed.returncode) == 0 else "failed"
            self.store.mark_finished(
                task_id=task_id,
                status=status,
                exit_code=int(completed.returncode),
                last_message=_safe_excerpt(last_message, limit=20000),
                stdout_excerpt=_safe_excerpt(stdout_text),
                stderr_excerpt=_safe_excerpt(stderr_text),
                result=result,
                error_text=None if status == "completed" else "codex_exec_nonzero",
            )
        except Exception as exc:  # pragma: no cover - defensive path
            self.store.mark_finished(
                task_id=task_id,
                status="failed",
                exit_code=None,
                last_message="",
                stdout_excerpt="",
                stderr_excerpt=_safe_excerpt(str(exc), limit=4000),
                result={"error": str(exc)},
                error_text="codex_exec_exception",
            )

    def execute_task(self, task_id: str, *, background: bool = True) -> dict[str, Any]:
        task = self.store.get(task_id)
        if not task:
            return {"ok": False, "error": "codex_task_not_found", "task_id": task_id}
        if background:
            with self._threads_lock:
                existing = self._threads.get(task_id)
                if existing and existing.is_alive():
                    return {"ok": True, "background": True, "status": "running", "task_id": task_id}
                thread = threading.Thread(
                    target=self._execute_worker,
                    args=(task_id,),
                    name=f"jarvis-codex-{task_id}",
                    daemon=True,
                )
                self._threads[task_id] = thread
                thread.start()
            return {"ok": True, "background": True, "status": "running", "task_id": task_id}

        self._execute_worker(task_id)
        latest = self.store.get(task_id)
        return {"ok": True, "background": False, "status": (latest or {}).get("status"), "task": latest}

    def list_tasks(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(status=status, limit=limit)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.store.get(task_id)
        if not task:
            return None
        with self._threads_lock:
            thread = self._threads.get(task_id)
        task["background_running"] = bool(thread and thread.is_alive())
        return task

    def summarize(self, *, limit: int = 200) -> dict[str, Any]:
        summary = self.store.summarize(limit=limit)
        with self._threads_lock:
            active = sum(1 for thread in self._threads.values() if thread.is_alive())
        summary["background_threads_running"] = active
        return summary

    def close(self) -> None:
        self.store.close()
