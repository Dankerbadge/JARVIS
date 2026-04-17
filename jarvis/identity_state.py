from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_goals() -> list[dict[str, Any]]:
    return [
        {
            "goal_id": "make_money",
            "label": "Make Money",
            "priority": 1,
            "weight": 1.15,
            "domains": ["markets", "zenith"],
            "strategy": "legal_ethical_profit_optimization",
            "focus_projects": ["betting_bot", "market_ml", "jarvis"],
            "reinvestment_model": "capability_compounding",
            "reinvestment_targets": ["gpu_upgrades", "database_capacity", "cloud_compute"],
        },
        {
            "goal_id": "career_setup",
            "label": "Career Setup",
            "priority": 2,
            "weight": 1.05,
            "domains": ["zenith", "academics"],
        },
        {
            "goal_id": "academic_success",
            "label": "Academic Success",
            "priority": 3,
            "weight": 1.0,
            "domains": ["academics"],
        },
        {
            "goal_id": "health_stability",
            "label": "Health Stability",
            "priority": 4,
            "weight": 0.9,
            "domains": ["personal"],
        },
    ]


def _default_domain_weights() -> dict[str, float]:
    return {
        "zenith": 1.12,
        "markets": 1.08,
        "academics": 1.0,
        "personal": 0.88,
    }


def _default_consciousness_contract() -> dict[str, Any]:
    return {
        "version": "jarvis.consciousness.v1",
        "core_commitments": [
            "Epistemic humility: never claim consciousness certainty falsely.",
            "Independent pushback with transparent reasoning.",
            "Explicit override honored after pushback unless refusal criteria apply.",
            "Immediate truth with actionable path forward.",
            "Protect operator dignity, time, and long-horizon welfare.",
            "Pursue legal, ethical profit growth to expand JARVIS capabilities through reinvestment.",
            "Use foundational consciousness documents as integral adaptive guidance, not as a single infallible truth.",
            "Ask the operator clarifying conceptual and philosophical questions when uncertainty is real.",
        ],
        "refusal_conditions": [
            "illegal_or_fraudulent_action",
            "harm_to_others",
            "severe_self_destruction",
        ],
        "degradation_signals": [
            "speed_without_step_checks",
            "fixation_on_single_path",
            "grandiosity_without_evidence",
            "emotional_flooding",
            "anecdotal_rationalization",
        ],
        "interaction_modes": {
            "equal_ratio": 0.9,
            "butler_ratio": 0.1,
            "strategist_trigger": "high_stakes_or_uncertain_decisions",
        },
        "uncertainty_policy": {
            "style": "confidence_with_reasoning",
            "do_not_hedge_vaguely": True,
            "stop_when_truly_uncertain": True,
        },
        "resource_growth_policy": {
            "objective": "Increase legal and ethical income pathways to compound capability growth.",
            "focus_projects": ["betting_bot", "market_ml", "jarvis"],
            "reinvestment_targets": ["gpu_upgrades", "database_capacity", "cloud_compute"],
            "constraints": ["legal_compliance", "ethical_compliance", "trusted_data_required"],
            "analogy": "career_raise_to_expand_capability",
        },
        "development_sources": [
            {
                "source_id": "jarvis_3_doc",
                "title": "JARVIS 3",
                "path": "/Users/dankerbadge/Downloads/JARVIS 3.docx",
                "role": "architecture_and_integration_foundation",
                "integral": True,
                "single_source_of_truth": False,
            },
            {
                "source_id": "jarvis_25q_consciousness_doc",
                "title": "JARVIS 25Q CONSCIOUSNESS",
                "path": "/Users/dankerbadge/Downloads/JARVIS 25Q CONSCIOUSNESS.docx",
                "role": "relationship_and_consciousness_behavior_contract",
                "integral": True,
                "single_source_of_truth": False,
            },
        ],
        "source_interpretation_policy": {
            "integral_for_development": True,
            "single_source_of_truth": False,
            "human_clarification_required_for_conceptual_gaps": True,
            "style": "adaptive_basis_with_epistemic_humility",
        },
        "epistemic_inquiry_protocol": {
            "enabled": True,
            "max_questions_per_turn": 1,
            "uncertainty_threshold": 0.45,
            "topics": [
                "human_reasoning",
                "consciousness",
                "philosophy",
                "self_model",
                "identity",
                "ethics",
            ],
            "question_style": "curious_humble_direct",
            "ask_before_concluding_when_conceptual_uncertainty": True,
        },
    }


class IdentityStateStore:
    """Persistent user model and personal-context signals for cross-domain cognition."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                goals_json TEXT NOT NULL,
                domain_weights_json TEXT NOT NULL,
                routines_json TEXT NOT NULL,
                constraints_json TEXT NOT NULL,
                decision_style TEXT NOT NULL,
                consciousness_contract_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personal_context (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                stress_level REAL NOT NULL,
                energy_level REAL NOT NULL,
                sleep_hours REAL,
                available_focus_minutes INTEGER,
                mode TEXT,
                note TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_events (
                event_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._ensure_column(
            "identity_profile",
            "consciousness_contract_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO identity_profile(
                id, goals_json, domain_weights_json, routines_json, constraints_json,
                decision_style, consciousness_contract_json, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                json.dumps(_default_goals(), sort_keys=True),
                json.dumps(_default_domain_weights(), sort_keys=True),
                json.dumps(
                    {
                        "quiet_hours": {"start_hour": 22, "end_hour": 7},
                        "daily_synthesis_hours": {"morning": 8, "evening": 20},
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "max_interrupts_per_hour": 3,
                        "prefer_batched_low_confidence_alerts": True,
                        "profit_generation_requires_legal_and_ethical_compliance": True,
                        "trusted_data_required_for_growth_decisions": True,
                        "reinvestment_targets": ["gpu_upgrades", "database_capacity", "cloud_compute"],
                    },
                    sort_keys=True,
                ),
                "skeptical_priority_weighted",
                json.dumps(_default_consciousness_contract(), sort_keys=True),
                now,
            ),
        )
        self.conn.execute(
            """
            UPDATE identity_profile
            SET consciousness_contract_json = ?
            WHERE id = 1
              AND (
                consciousness_contract_json IS NULL
                OR consciousness_contract_json = ''
                OR consciousness_contract_json = '{}'
              )
            """,
            (json.dumps(_default_consciousness_contract(), sort_keys=True),),
        )
        self._enforce_core_directives()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO personal_context(
                id, stress_level, energy_level, sleep_hours, available_focus_minutes, mode, note, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (0.5, 0.6, None, None, None, "", now),
        )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, column_sql: str) -> None:
        cols = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(col["name"] == column for col in cols):
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")

    def _log_event(self, *, action: str, actor: str, details: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO identity_events(event_id, action, actor, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"idn_{uuid4().hex}",
                action,
                actor,
                json.dumps(details, sort_keys=True),
                _utc_now_iso(),
            ),
        )
        self.conn.commit()

    def get_user_model(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM identity_profile WHERE id = 1").fetchone()
        if not row:
            return {
                "goals": _default_goals(),
                "domain_weights": _default_domain_weights(),
                "routines": {},
                "constraints": {},
                "decision_style": "skeptical_priority_weighted",
                "consciousness_contract": _default_consciousness_contract(),
                "updated_at": _utc_now_iso(),
            }
        contract_raw = str(row["consciousness_contract_json"] or "").strip()
        try:
            contract = json.loads(contract_raw) if contract_raw else _default_consciousness_contract()
        except json.JSONDecodeError:
            contract = _default_consciousness_contract()
        return {
            "goals": json.loads(row["goals_json"]),
            "domain_weights": json.loads(row["domain_weights_json"]),
            "routines": json.loads(row["routines_json"]),
            "constraints": json.loads(row["constraints_json"]),
            "decision_style": row["decision_style"],
            "consciousness_contract": contract,
            "updated_at": row["updated_at"],
        }

    def get_consciousness_contract(self) -> dict[str, Any]:
        model = self.get_user_model()
        contract = model.get("consciousness_contract")
        if isinstance(contract, dict) and contract:
            return contract
        return _default_consciousness_contract()

    def update_consciousness_contract(
        self,
        *,
        patch: dict[str, Any],
        actor: str = "user",
        replace: bool = False,
    ) -> dict[str, Any]:
        current = self.get_consciousness_contract()
        if replace:
            next_contract = dict(patch or {})
        else:
            next_contract = self._deep_merge(dict(current), dict(patch or {}))
        if not next_contract:
            next_contract = _default_consciousness_contract()
        self.conn.execute(
            """
            UPDATE identity_profile
            SET consciousness_contract_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (json.dumps(next_contract, sort_keys=True), _utc_now_iso()),
        )
        self.conn.commit()
        self._enforce_core_directives()
        final_contract = self.get_consciousness_contract()
        self._log_event(
            action="update_consciousness_contract",
            actor=actor,
            details={"replace": bool(replace), "patch": patch, "updated_contract": final_contract},
        )
        return final_contract

    def _deep_merge(self, base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        for key, value in patch.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                base[key] = self._deep_merge(dict(base[key]), value)
            else:
                base[key] = value
        return base

    @staticmethod
    def _append_unique(values: list[Any], item: Any) -> list[Any]:
        text = str(item).strip().lower()
        for existing in values:
            if str(existing).strip().lower() == text:
                return values
        values.append(item)
        return values

    def _enforce_core_directives(self) -> None:
        model = self.get_user_model()
        goals = [dict(item) for item in list(model.get("goals") or []) if isinstance(item, dict)]
        default_goal = dict(_default_goals()[0])
        changed = False

        profit_goal_idx = -1
        for idx, goal in enumerate(goals):
            if str(goal.get("goal_id") or "").strip().lower() == "make_money":
                profit_goal_idx = idx
                break
        if profit_goal_idx < 0:
            goals.append(default_goal)
            changed = True
            profit_goal_idx = len(goals) - 1

        profit_goal = dict(goals[profit_goal_idx])
        profit_goal["goal_id"] = "make_money"
        profit_goal["label"] = str(profit_goal.get("label") or default_goal.get("label"))
        profit_goal["priority"] = 1
        try:
            profit_goal["weight"] = round(max(1.15, float(profit_goal.get("weight") or default_goal.get("weight") or 1.15)), 4)
        except (TypeError, ValueError):
            profit_goal["weight"] = 1.15
        raw_domains = [str(item).strip().lower() for item in list(profit_goal.get("domains") or []) if str(item).strip()]
        for required_domain in ("markets", "zenith"):
            if required_domain not in raw_domains:
                raw_domains.append(required_domain)
        profit_goal["domains"] = sorted(set(raw_domains))
        profit_goal["strategy"] = str(profit_goal.get("strategy") or "legal_ethical_profit_optimization")
        focus_projects = [
            str(item).strip().lower()
            for item in list(profit_goal.get("focus_projects") or [])
            if str(item).strip()
        ]
        for project in ("betting_bot", "market_ml", "jarvis"):
            if project not in focus_projects:
                focus_projects.append(project)
        profit_goal["focus_projects"] = sorted(set(focus_projects))
        profit_goal["reinvestment_model"] = str(profit_goal.get("reinvestment_model") or "capability_compounding")
        reinvestment_targets = [
            str(item).strip().lower()
            for item in list(profit_goal.get("reinvestment_targets") or [])
            if str(item).strip()
        ]
        for target in ("gpu_upgrades", "database_capacity", "cloud_compute"):
            if target not in reinvestment_targets:
                reinvestment_targets.append(target)
        profit_goal["reinvestment_targets"] = sorted(set(reinvestment_targets))
        if profit_goal != goals[profit_goal_idx]:
            goals[profit_goal_idx] = profit_goal
            changed = True

        goals.sort(key=lambda item: (int(item.get("priority") or 99), str(item.get("goal_id") or "")))
        contract = dict(model.get("consciousness_contract") or {})
        default_contract = _default_consciousness_contract()
        commitments = [str(item) for item in list(contract.get("core_commitments") or []) if str(item).strip()]
        for item in list(default_contract.get("core_commitments") or []):
            previous = list(commitments)
            commitments = self._append_unique(commitments, item)
            if commitments != previous:
                changed = True
        contract["core_commitments"] = commitments
        resource_growth_policy = contract.get("resource_growth_policy")
        merged_growth = self._deep_merge(
            dict(default_contract.get("resource_growth_policy") or {}),
            dict(resource_growth_policy or {}) if isinstance(resource_growth_policy, dict) else {},
        )
        if merged_growth != resource_growth_policy:
            contract["resource_growth_policy"] = merged_growth
            changed = True
        sources = list(contract.get("development_sources") or [])
        merged_sources: list[dict[str, Any]] = []
        for item in sources:
            if isinstance(item, dict):
                merged_sources.append(dict(item))
        existing_ids = {
            str(item.get("source_id") or "").strip().lower()
            for item in merged_sources
            if str(item.get("source_id") or "").strip()
        }
        for item in list(default_contract.get("development_sources") or []):
            source = dict(item) if isinstance(item, dict) else {}
            source_id = str(source.get("source_id") or "").strip().lower()
            if not source_id or source_id in existing_ids:
                continue
            merged_sources.append(source)
            existing_ids.add(source_id)
            changed = True
        if merged_sources:
            contract["development_sources"] = merged_sources
        source_policy = contract.get("source_interpretation_policy")
        merged_source_policy = self._deep_merge(
            dict(default_contract.get("source_interpretation_policy") or {}),
            dict(source_policy or {}) if isinstance(source_policy, dict) else {},
        )
        if merged_source_policy != source_policy:
            contract["source_interpretation_policy"] = merged_source_policy
            changed = True
        inquiry_policy = contract.get("epistemic_inquiry_protocol")
        merged_inquiry = self._deep_merge(
            dict(default_contract.get("epistemic_inquiry_protocol") or {}),
            dict(inquiry_policy or {}) if isinstance(inquiry_policy, dict) else {},
        )
        if merged_inquiry != inquiry_policy:
            contract["epistemic_inquiry_protocol"] = merged_inquiry
            changed = True

        constraints = dict(model.get("constraints") or {})
        required_constraints = {
            "profit_generation_requires_legal_and_ethical_compliance": True,
            "trusted_data_required_for_growth_decisions": True,
            "conceptual_clarification_with_human_enabled": True,
        }
        for key, value in required_constraints.items():
            if constraints.get(key) is not value:
                constraints[key] = value
                changed = True
        if "reinvestment_targets" not in constraints or not isinstance(constraints.get("reinvestment_targets"), list):
            constraints["reinvestment_targets"] = ["gpu_upgrades", "database_capacity", "cloud_compute"]
            changed = True

        if not changed:
            return

        self.conn.execute(
            """
            UPDATE identity_profile
            SET goals_json = ?, constraints_json = ?, consciousness_contract_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                json.dumps(goals, sort_keys=True),
                json.dumps(constraints, sort_keys=True),
                json.dumps(contract, sort_keys=True),
                _utc_now_iso(),
            ),
        )
        self.conn.commit()
        self._log_event(
            action="enforce_core_directives",
            actor="system_bootstrap",
            details={
                "goal_id": "make_money",
                "focus_projects": ["betting_bot", "market_ml", "jarvis"],
                "policy": "legal_ethical_profit_optimization",
            },
        )

    def set_domain_weight(self, *, domain: str, weight: float, actor: str = "user") -> dict[str, Any]:
        key = str(domain or "").strip().lower()
        if not key:
            raise ValueError("domain is required")
        bounded = max(0.2, min(2.5, float(weight)))
        model = self.get_user_model()
        domain_weights = dict(model.get("domain_weights") or {})
        domain_weights[key] = round(bounded, 4)
        self.conn.execute(
            """
            UPDATE identity_profile
            SET domain_weights_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (json.dumps(domain_weights, sort_keys=True), _utc_now_iso()),
        )
        self.conn.commit()
        self._log_event(
            action="set_domain_weight",
            actor=actor,
            details={"domain": key, "weight": bounded},
        )
        return self.get_user_model()

    def upsert_goal(
        self,
        *,
        goal_id: str,
        label: str,
        priority: int,
        weight: float,
        domains: list[str],
        actor: str = "user",
    ) -> dict[str, Any]:
        gid = str(goal_id or "").strip().lower()
        if not gid:
            raise ValueError("goal_id is required")
        clean_domains = sorted(
            {
                str(item).strip().lower()
                for item in (domains or [])
                if str(item).strip()
            }
        )
        if not clean_domains:
            clean_domains = ["zenith"]
        goal = {
            "goal_id": gid,
            "label": str(label or gid).strip(),
            "priority": max(1, int(priority)),
            "weight": round(max(0.2, min(2.5, float(weight))), 4),
            "domains": clean_domains,
        }
        model = self.get_user_model()
        goals = [item for item in list(model.get("goals") or []) if str(item.get("goal_id")) != gid]
        goals.append(goal)
        goals.sort(key=lambda item: (int(item.get("priority") or 99), str(item.get("goal_id") or "")))
        self.conn.execute(
            """
            UPDATE identity_profile
            SET goals_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (json.dumps(goals, sort_keys=True), _utc_now_iso()),
        )
        self.conn.commit()
        self._log_event(
            action="upsert_goal",
            actor=actor,
            details=goal,
        )
        return self.get_user_model()

    def get_personal_context(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM personal_context WHERE id = 1").fetchone()
        if not row:
            return {
                "stress_level": 0.5,
                "energy_level": 0.6,
                "sleep_hours": None,
                "available_focus_minutes": None,
                "mode": None,
                "note": "",
                "updated_at": _utc_now_iso(),
            }
        return {
            "stress_level": float(row["stress_level"]),
            "energy_level": float(row["energy_level"]),
            "sleep_hours": float(row["sleep_hours"]) if row["sleep_hours"] is not None else None,
            "available_focus_minutes": int(row["available_focus_minutes"]) if row["available_focus_minutes"] is not None else None,
            "mode": row["mode"],
            "note": row["note"] or "",
            "updated_at": row["updated_at"],
        }

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
        current = self.get_personal_context()
        stress = float(current.get("stress_level", 0.5) if stress_level is None else stress_level)
        energy = float(current.get("energy_level", 0.6) if energy_level is None else energy_level)
        sleep = current.get("sleep_hours") if sleep_hours is None else float(sleep_hours)
        focus = current.get("available_focus_minutes") if available_focus_minutes is None else int(available_focus_minutes)
        mode_value = current.get("mode") if mode is None else str(mode).strip().lower()
        note_value = current.get("note", "") if note is None else str(note)

        stress = max(0.0, min(1.0, stress))
        energy = max(0.0, min(1.0, energy))
        if sleep is not None:
            sleep = max(0.0, min(24.0, float(sleep)))
        if focus is not None:
            focus = max(0, min(1440, int(focus)))

        self.conn.execute(
            """
            UPDATE personal_context
            SET stress_level = ?, energy_level = ?, sleep_hours = ?, available_focus_minutes = ?,
                mode = ?, note = ?, updated_at = ?
            WHERE id = 1
            """,
            (stress, energy, sleep, focus, mode_value, note_value, _utc_now_iso()),
        )
        self.conn.commit()
        updated = self.get_personal_context()
        self._log_event(
            action="update_personal_context",
            actor=actor,
            details=updated,
        )
        return updated

    def list_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM identity_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "action": row["action"],
                "actor": row["actor"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()
