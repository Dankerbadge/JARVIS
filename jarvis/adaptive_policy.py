from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _clamp(value: float, *, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in dict(patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged.get(key) or {}), value)
        else:
            merged[key] = value
    return merged


def default_adaptive_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "routing": {
            "codex_bias": 0.0,
            "gpt_bias": 0.0,
            "delegate_score_threshold": 1.5,
            "app_scope_weight": 1.0,
            "write_signal_weight": 1.0,
            "read_signal_weight": 0.7,
            "routing_query_forces_gpt": True,
        },
        "tiering": {
            "instant_max_words": 6,
            "pro_min_words": 20,
            "extended_min_words": 40,
            "deep_research_min_words": 70,
        },
        "relationship_mode": {
            "uncertainty_strategist_threshold": 0.6,
            "high_stakes_prefers_strategist": True,
            "disputed_prefers_strategist": True,
            "explicit_directive_to_butler": True,
        },
        "tone": {
            "warmth_bias": 0.0,
            "challenge_bias": 0.0,
            "compression_bias": 0.0,
            "calmness_bias": 0.0,
            "deference_bias": 0.0,
            "humor_bias": 0.0,
        },
        "pushback": {
            "auto_pushback_on_high_stakes_disputed": True,
            "severity_bias": 0.0,
        },
        "runtime": {
            "auto_calibration_enabled": True,
            "auto_calibration_every_turns": 20,
        },
        "self_patch": {
            "enabled": True,
            "auto_execute": True,
            "cooldown_minutes": 30,
            "max_open_tasks": 2,
            "weekly_remaining_percent": 100.0,
            "min_weekly_remaining_percent": 40.0,
            "allowed_projects": ["jarvis", "market_ml", "betting_bot"],
            "default_project": "jarvis",
            "default_auto_approval_source": "codex",
            "allowed_approval_sources": ["gpt", "codex", "owner"],
            "major_change_requires_owner": True,
            "external_access_requires_owner": True,
            "minor_external_access_allowed": True,
            "min_voice_turns": 6,
            "min_continuity_failure_rate": 0.3,
            "min_mode_accuracy": 0.55,
            "min_codex_tasks": 6,
            "min_codex_fail_rate": 0.45,
            "min_interrupted_turns": 4,
            "min_interruption_recovery_rate": 0.5,
            "min_reviews": 5,
            "min_negative_review_rate": 0.6,
        },
        "metadata": {
            "revision": "",
            "updated_at": "",
            "reason": "bootstrap",
            "calibration_runs": 0,
        },
    }


class AdaptivePolicyStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptive_policy_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                revision TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptive_policy_history (
                history_id TEXT PRIMARY KEY,
                revision TEXT NOT NULL,
                reason TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adaptive_policy_history_created
            ON adaptive_policy_history(created_at DESC)
            """
        )
        self.conn.commit()

    def _stamp_policy(
        self,
        policy: dict[str, Any],
        *,
        reason: str,
        calibration_runs: int | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        revision = _new_id("apol")
        stamped = copy.deepcopy(policy)
        metadata = stamped.get("metadata") if isinstance(stamped.get("metadata"), dict) else {}
        runs = int(metadata.get("calibration_runs") or 0)
        if calibration_runs is not None:
            runs = int(calibration_runs)
        metadata.update(
            {
                "revision": revision,
                "updated_at": now,
                "reason": str(reason or "manual"),
                "calibration_runs": runs,
            }
        )
        stamped["metadata"] = metadata
        return stamped

    def _write_policy(
        self,
        *,
        policy: dict[str, Any],
        reason: str,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stamped = self._stamp_policy(policy, reason=reason, calibration_runs=int((policy.get("metadata") or {}).get("calibration_runs") or 0))
        revision = str((stamped.get("metadata") or {}).get("revision") or _new_id("apol"))
        now = str((stamped.get("metadata") or {}).get("updated_at") or _utc_now_iso())
        history_id = _new_id("aph")
        metrics_json = json.dumps(dict(metrics or {}), sort_keys=True)
        policy_json = json.dumps(stamped, sort_keys=True)
        self.conn.execute(
            """
            INSERT INTO adaptive_policy_state(singleton, revision, policy_json, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                revision = excluded.revision,
                policy_json = excluded.policy_json,
                updated_at = excluded.updated_at
            """,
            (revision, policy_json, now),
        )
        self.conn.execute(
            """
            INSERT INTO adaptive_policy_history(
                history_id, revision, reason, metrics_json, policy_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (history_id, revision, str(reason or "manual"), metrics_json, policy_json, now),
        )
        self.conn.commit()
        return {
            "revision": revision,
            "reason": str(reason or "manual"),
            "metrics": dict(metrics or {}),
            "policy": stamped,
            "updated_at": now,
        }

    def get_policy(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT policy_json
            FROM adaptive_policy_state
            WHERE singleton = 1
            LIMIT 1
            """
        ).fetchone()
        if row:
            return json.loads(str(row["policy_json"] or "{}"))
        bootstrap = default_adaptive_policy()
        written = self._write_policy(policy=bootstrap, reason="bootstrap", metrics={"source": "default"})
        return dict(written.get("policy") or bootstrap)

    def update_patch(
        self,
        *,
        patch: dict[str, Any],
        reason: str,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.get_policy()
        merged = _deep_merge(current, dict(patch or {}))
        # Clamp a few numeric policy values so bad patches do not break routing.
        routing = merged.get("routing") if isinstance(merged.get("routing"), dict) else {}
        routing["codex_bias"] = round(_clamp(float(routing.get("codex_bias") or 0.0), low=-2.0, high=2.0), 4)
        routing["gpt_bias"] = round(_clamp(float(routing.get("gpt_bias") or 0.0), low=-2.0, high=2.0), 4)
        routing["delegate_score_threshold"] = round(
            _clamp(float(routing.get("delegate_score_threshold") or 1.5), low=0.2, high=6.0),
            4,
        )
        merged["routing"] = routing

        tone = merged.get("tone") if isinstance(merged.get("tone"), dict) else {}
        for key in ("warmth_bias", "challenge_bias", "compression_bias", "calmness_bias", "deference_bias", "humor_bias"):
            tone[key] = round(_clamp(float(tone.get(key) or 0.0), low=-0.35, high=0.35), 4)
        merged["tone"] = tone

        relationship = merged.get("relationship_mode") if isinstance(merged.get("relationship_mode"), dict) else {}
        relationship["uncertainty_strategist_threshold"] = round(
            _clamp(float(relationship.get("uncertainty_strategist_threshold") or 0.6), low=0.2, high=0.95),
            4,
        )
        merged["relationship_mode"] = relationship

        pushback = merged.get("pushback") if isinstance(merged.get("pushback"), dict) else {}
        pushback["severity_bias"] = round(_clamp(float(pushback.get("severity_bias") or 0.0), low=-0.8, high=0.8), 4)
        merged["pushback"] = pushback

        tiering = merged.get("tiering") if isinstance(merged.get("tiering"), dict) else {}
        tiering["instant_max_words"] = int(_clamp(float(tiering.get("instant_max_words") or 6), low=1, high=20))
        tiering["pro_min_words"] = int(_clamp(float(tiering.get("pro_min_words") or 20), low=5, high=80))
        tiering["extended_min_words"] = int(
            _clamp(float(tiering.get("extended_min_words") or 40), low=max(10, tiering["pro_min_words"]), high=140)
        )
        tiering["deep_research_min_words"] = int(
            _clamp(float(tiering.get("deep_research_min_words") or 70), low=max(20, tiering["extended_min_words"]), high=220)
        )
        merged["tiering"] = tiering

        runtime = merged.get("runtime") if isinstance(merged.get("runtime"), dict) else {}
        runtime["auto_calibration_enabled"] = bool(runtime.get("auto_calibration_enabled", True))
        runtime["auto_calibration_every_turns"] = int(
            _clamp(float(runtime.get("auto_calibration_every_turns") or 20), low=5, high=200)
        )
        merged["runtime"] = runtime

        self_patch = merged.get("self_patch") if isinstance(merged.get("self_patch"), dict) else {}
        self_patch["enabled"] = bool(self_patch.get("enabled", True))
        self_patch["auto_execute"] = bool(self_patch.get("auto_execute", True))
        self_patch["cooldown_minutes"] = int(
            _clamp(float(self_patch.get("cooldown_minutes") or 30), low=1, high=1440)
        )
        self_patch["max_open_tasks"] = int(_clamp(float(self_patch.get("max_open_tasks") or 2), low=0, high=20))
        self_patch["weekly_remaining_percent"] = round(
            _clamp(float(self_patch.get("weekly_remaining_percent") or 100.0), low=0.0, high=100.0),
            4,
        )
        self_patch["min_weekly_remaining_percent"] = round(
            _clamp(float(self_patch.get("min_weekly_remaining_percent") or 40.0), low=0.0, high=100.0),
            4,
        )
        allowed_projects = self_patch.get("allowed_projects")
        if isinstance(allowed_projects, list):
            normalized_projects = [
                str(item).strip().lower()
                for item in allowed_projects
                if str(item).strip()
            ]
            self_patch["allowed_projects"] = sorted(set(normalized_projects)) or ["jarvis", "market_ml", "betting_bot"]
        else:
            self_patch["allowed_projects"] = ["jarvis", "market_ml", "betting_bot"]
        default_project = str(self_patch.get("default_project") or "").strip().lower() or "jarvis"
        if default_project not in set(self_patch["allowed_projects"]):
            default_project = str(self_patch["allowed_projects"][0])
        self_patch["default_project"] = default_project
        default_auto_approval_source = str(self_patch.get("default_auto_approval_source") or "codex").strip().lower() or "codex"
        allowed_approval_sources = self_patch.get("allowed_approval_sources")
        if isinstance(allowed_approval_sources, list):
            normalized_approvers = [
                str(item).strip().lower()
                for item in allowed_approval_sources
                if str(item).strip()
            ]
            self_patch["allowed_approval_sources"] = sorted(set(normalized_approvers)) or ["gpt", "codex", "owner"]
        else:
            self_patch["allowed_approval_sources"] = ["gpt", "codex", "owner"]
        if default_auto_approval_source not in set(self_patch["allowed_approval_sources"]):
            default_auto_approval_source = "codex"
        self_patch["default_auto_approval_source"] = default_auto_approval_source
        self_patch["major_change_requires_owner"] = bool(self_patch.get("major_change_requires_owner", True))
        self_patch["external_access_requires_owner"] = bool(self_patch.get("external_access_requires_owner", True))
        self_patch["minor_external_access_allowed"] = bool(self_patch.get("minor_external_access_allowed", True))
        self_patch["min_voice_turns"] = int(_clamp(float(self_patch.get("min_voice_turns") or 6), low=0, high=500))
        self_patch["min_continuity_failure_rate"] = round(
            _clamp(float(self_patch.get("min_continuity_failure_rate") or 0.3), low=0.0, high=1.0),
            4,
        )
        self_patch["min_mode_accuracy"] = round(
            _clamp(float(self_patch.get("min_mode_accuracy") or 0.55), low=0.0, high=1.0),
            4,
        )
        self_patch["min_codex_tasks"] = int(_clamp(float(self_patch.get("min_codex_tasks") or 6), low=0, high=10000))
        self_patch["min_codex_fail_rate"] = round(
            _clamp(float(self_patch.get("min_codex_fail_rate") or 0.45), low=0.0, high=1.0),
            4,
        )
        self_patch["min_interrupted_turns"] = int(
            _clamp(float(self_patch.get("min_interrupted_turns") or 4), low=0, high=10000)
        )
        self_patch["min_interruption_recovery_rate"] = round(
            _clamp(float(self_patch.get("min_interruption_recovery_rate") or 0.5), low=0.0, high=1.0),
            4,
        )
        self_patch["min_reviews"] = int(_clamp(float(self_patch.get("min_reviews") or 5), low=0, high=10000))
        self_patch["min_negative_review_rate"] = round(
            _clamp(float(self_patch.get("min_negative_review_rate") or 0.6), low=0.0, high=1.0),
            4,
        )
        merged["self_patch"] = self_patch

        return self._write_policy(policy=merged, reason=reason, metrics=metrics)

    def list_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM adaptive_policy_history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 50), 500)),),
        ).fetchall()
        return [
            {
                "history_id": row["history_id"],
                "revision": row["revision"],
                "reason": row["reason"],
                "metrics": json.loads(str(row["metrics_json"] or "{}")),
                "policy": json.loads(str(row["policy_json"] or "{}")),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()
