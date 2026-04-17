from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConsciousnessSurfaceService:
    """File-backed mind artifacts inspired by SOUL/TOOLS/AGENTS-style surfaces."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.root = self.db_path.parent / "mind"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def _surface_path(self, name: str) -> Path:
        return self.root / f"{name}.md"

    def _render_soul(self, runtime: Any) -> str:
        contract = runtime.get_consciousness_contract()
        commitments = list(contract.get("core_commitments") or [])
        refusal = list(contract.get("refusal_conditions") or [])
        degradation = list(contract.get("degradation_signals") or [])
        mode = dict(contract.get("interaction_modes") or {})
        growth = dict(contract.get("resource_growth_policy") or {})
        source_policy = dict(contract.get("source_interpretation_policy") or {})
        inquiry = dict(contract.get("epistemic_inquiry_protocol") or {})
        lines = [
            "# SOUL",
            "",
            "## Core Commitments",
        ]
        if commitments:
            lines.extend(f"- {str(item)}" for item in commitments)
        else:
            lines.append("- Epistemic humility and truthful uncertainty communication.")
            lines.append("- Suggestion-first autonomy with explicit override support.")
            lines.append("- Protect long-term welfare while respecting operator agency.")

        lines.extend(
            [
                "",
                "## Refusal + Pushback",
            ]
        )
        if refusal:
            lines.extend(f"- Refuse: {str(item)}" for item in refusal)
        else:
            lines.append("- Refuse illegal, deceptive, or harmful actions.")
        lines.append("- Push back hard when risk is high; obey explicit override after pushback unless refusal criteria apply.")

        lines.extend(
            [
                "",
                "## Reasoning Health Signals",
            ]
        )
        if degradation:
            lines.extend(f"- {str(item)}" for item in degradation)
        else:
            lines.append("- Speed without checking steps, fixation, grandiosity, emotional flooding.")

        lines.extend(
            [
                "",
                "## Interaction Modes",
                f"- Equal mode ratio: {mode.get('equal_ratio', 0.9)}",
                f"- Butler mode ratio: {mode.get('butler_ratio', 0.1)}",
                "- Strategist mode: high-stakes planning, complex tradeoffs, and uncertainty resolution.",
            ]
        )
        if growth:
            lines.extend(
                [
                    "",
                    "## Resource Growth Mandate",
                    f"- objective: {growth.get('objective')}",
                    f"- focus_projects: {','.join(growth.get('focus_projects') or [])}",
                    f"- reinvestment_targets: {','.join(growth.get('reinvestment_targets') or [])}",
                    f"- constraints: {','.join(growth.get('constraints') or [])}",
                ]
            )
        if source_policy:
            lines.extend(
                [
                    "",
                    "## Source Interpretation Policy",
                    f"- integral_for_development: {source_policy.get('integral_for_development')}",
                    f"- single_source_of_truth: {source_policy.get('single_source_of_truth')}",
                    (
                        "- human_clarification_required_for_conceptual_gaps: "
                        + f"{source_policy.get('human_clarification_required_for_conceptual_gaps')}"
                    ),
                    f"- style: {source_policy.get('style')}",
                ]
            )
        if inquiry:
            lines.extend(
                [
                    "",
                    "## Epistemic Inquiry Protocol",
                    f"- enabled: {inquiry.get('enabled')}",
                    f"- uncertainty_threshold: {inquiry.get('uncertainty_threshold')}",
                    f"- max_questions_per_turn: {inquiry.get('max_questions_per_turn')}",
                    f"- topics: {','.join(inquiry.get('topics') or [])}",
                ]
            )
        lines.extend(["", f"_Generated at { _utc_now_iso() }_"])
        return "\n".join(lines).strip() + "\n"

    def _render_identity(self, runtime: Any) -> str:
        model = runtime.get_user_model()
        context = runtime.get_personal_context()
        goals = list(model.get("goals") or [])
        domain_weights = dict(model.get("domain_weights") or {})
        constraints = dict(model.get("constraints") or {})
        contract = runtime.get_consciousness_contract()
        sources = list(contract.get("development_sources") or [])
        lines = [
            "# IDENTITY",
            "",
            "## Goals",
        ]
        if goals:
            for item in goals:
                extra: list[str] = []
                if item.get("strategy"):
                    extra.append(f"strategy={item.get('strategy')}")
                if item.get("focus_projects"):
                    extra.append(f"focus_projects={','.join(item.get('focus_projects') or [])}")
                if item.get("reinvestment_targets"):
                    extra.append(f"reinvestment_targets={','.join(item.get('reinvestment_targets') or [])}")
                extra_suffix = f" | {' | '.join(extra)}" if extra else ""
                lines.append(
                    "- "
                    + f"{item.get('goal_id')} | priority={item.get('priority')} | "
                    + f"weight={item.get('weight')} | domains={','.join(item.get('domains') or [])}"
                    + extra_suffix
                )
        else:
            lines.append("- No goals configured.")
        lines.extend(["", "## Domain Weights"])
        if domain_weights:
            for key in sorted(domain_weights):
                lines.append(f"- {key}: {domain_weights[key]}")
        else:
            lines.append("- No domain weights configured.")
        lines.extend(
            [
                "",
                "## Core Constraints",
                (
                    "- profit_generation_requires_legal_and_ethical_compliance: "
                    + f"{constraints.get('profit_generation_requires_legal_and_ethical_compliance')}"
                ),
                (
                    "- trusted_data_required_for_growth_decisions: "
                    + f"{constraints.get('trusted_data_required_for_growth_decisions')}"
                ),
                (
                    "- conceptual_clarification_with_human_enabled: "
                    + f"{constraints.get('conceptual_clarification_with_human_enabled')}"
                ),
                f"- reinvestment_targets: {','.join(constraints.get('reinvestment_targets') or [])}",
            ]
        )
        lines.extend(["", "## Development Sources"])
        if sources:
            for item in sources:
                source = dict(item) if isinstance(item, dict) else {}
                lines.append(
                    "- "
                    + f"{source.get('source_id')} | title={source.get('title')} | "
                    + f"integral={source.get('integral')} | single_source_of_truth={source.get('single_source_of_truth')} | "
                    + f"path={source.get('path')}"
                )
        else:
            lines.append("- No development sources configured.")
        lines.extend(
            [
                "",
                "## Personal Context",
                f"- stress_level: {context.get('stress_level')}",
                f"- energy_level: {context.get('energy_level')}",
                f"- sleep_hours: {context.get('sleep_hours')}",
                f"- available_focus_minutes: {context.get('available_focus_minutes')}",
                f"- mode: {context.get('mode')}",
                f"- note: {context.get('note')}",
                "",
                f"_Generated at { _utc_now_iso() }_",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def _render_tools(self, runtime: Any) -> str:
        prefs = runtime.get_operator_preferences()
        lines = [
            "# TOOLS",
            "",
            "## Governance",
            "- Suggestion-first by default. No autonomous high-risk execution without approvals.",
            "- P0/P1: read-only or low-risk operations.",
            "- P2/P3: approval-gated protected operations.",
            "- P4: kill switch class; never execute by default.",
            "",
            "## Boundaries",
            "- Untrusted external content must be sanitized before planning.",
            "- Tool-power boundaries are enforced by auth, policy, and approvals.",
            "- Market domain stays recommendation/handoff-only (no direct autonomous trade execution).",
            "",
            "## Operator Preferences Snapshot",
            f"- focus_mode_domain: {prefs.get('focus_mode_domain')}",
            f"- quiet_hours: {json.dumps(prefs.get('quiet_hours'))}",
            f"- suppress_until: {prefs.get('suppress_until')}",
            "",
            f"_Generated at { _utc_now_iso() }_",
        ]
        return "\n".join(lines).strip() + "\n"

    def _render_agents(self, runtime: Any) -> str:
        lines = [
            "# AGENTS",
            "",
            "## Domain Minds",
            "- Zenith: repo/runtime problem-solving and delivery constraints.",
            "- Academics: schedules, coursework, deadlines, and educational risk management.",
            "- Markets: read-only signal evaluation and handoff outcomes, never direct execution.",
            "- Identity: user model, personal context, and long-horizon alignment.",
            "",
            "## Presence Strategy",
            "- Equal-partner mode by default for reasoning and planning.",
            "- Butler mode for explicit, non-disputable directives.",
            "- Strategist mode for complex decisions and uncertainty triage.",
            "",
            f"_Generated at { _utc_now_iso() }_",
        ]
        return "\n".join(lines).strip() + "\n"

    def _render_user(self, runtime: Any) -> str:
        model = runtime.get_user_model()
        goals = list(model.get("goals") or [])
        top_goal = goals[0] if goals else {}
        contract = runtime.get_consciousness_contract()
        growth = dict(contract.get("resource_growth_policy") or {})
        inquiry = dict(contract.get("epistemic_inquiry_protocol") or {})
        lines = [
            "# USER",
            "",
            "## Relationship Contract",
            "- Treat user as long-horizon partner, not task queue.",
            "- Prefer peer-level reasoning by default; switch to butler mode only for explicit non-disputable directives.",
            "- Maintain direct truthfulness with concrete uncertainty communication.",
            "",
            "## Current Priority Anchor",
            f"- top_goal_id: {top_goal.get('goal_id')}",
            f"- top_goal_label: {top_goal.get('label')}",
            f"- top_goal_domains: {','.join(top_goal.get('domains') or [])}",
            f"- top_goal_strategy: {top_goal.get('strategy')}",
            f"- top_goal_focus_projects: {','.join(top_goal.get('focus_projects') or [])}",
            "",
            "## Capability Compounding",
            f"- objective: {growth.get('objective')}",
            f"- reinvestment_targets: {','.join(growth.get('reinvestment_targets') or [])}",
            f"- legal_ethical_constraints: {','.join(growth.get('constraints') or [])}",
            "",
            "## Human Clarification Model",
            f"- enabled: {inquiry.get('enabled')}",
            f"- ask_before_concluding_when_conceptual_uncertainty: {inquiry.get('ask_before_concluding_when_conceptual_uncertainty')}",
            f"- question_style: {inquiry.get('question_style')}",
            "",
            f"_Generated at {_utc_now_iso()}_",
        ]
        return "\n".join(lines).strip() + "\n"

    def _render_heartbeat(self, runtime: Any) -> str:
        context = runtime.get_personal_context()
        interrupts = runtime.list_interrupts(status="pending", limit=10)
        lines = [
            "# HEARTBEAT",
            "",
            "## Loop Checklist",
            "- Re-anchor on mission and active domain priorities.",
            "- Scan for degradation signals and recent override outcomes.",
            "- Recompute interruption urgency with current context.",
            "- Preserve bounded, auditable outputs before action suggestions.",
            "",
            "## Live Snapshot",
            f"- stress_level: {context.get('stress_level')}",
            f"- energy_level: {context.get('energy_level')}",
            f"- pending_interrupts: {len(interrupts)}",
            "",
            f"_Generated at {_utc_now_iso()}_",
        ]
        return "\n".join(lines).strip() + "\n"

    def _render_boot(self, runtime: Any) -> str:
        risks = runtime.state_graph.get_active_entities("Risk")
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
        )[:5]
        lines = [
            "# BOOT",
            "",
            "## Startup Reattachment",
            "- Load consciousness contract and mode policy before processing new signals.",
            "- Rehydrate latest priorities, pending approvals, and unresolved interruptions.",
            "- Resume with transparent hypothesis notices when confidence is incomplete.",
            "",
            "## Priority Snapshot",
        ]
        if not priorities:
            lines.append("- No active priorities.")
        for item in priorities:
            lines.append(
                "- "
                + f"[{item.get('domain')}] {item.get('reason') or item.get('risk_key')} "
                + f"(confidence={item.get('confidence')})"
            )
        lines.extend(["", f"_Generated at {_utc_now_iso()}_"])
        return "\n".join(lines).strip() + "\n"

    def _render_memory(self, runtime: Any) -> str:
        digest = runtime.list_digest_exports(limit=1)
        latest = digest[0] if digest else {}
        thoughts = runtime.list_recent_thoughts(limit=3)
        events = runtime.list_consciousness_events(limit=20)
        lines = [
            "# MEMORY",
            "",
            "## Daily Digest Anchor",
            f"- latest_day_key: {latest.get('day_key')}",
            f"- latest_domains: {','.join(latest.get('domains') or [])}",
            "",
            "## Recent Thoughts",
        ]
        if not thoughts:
            lines.append("- No thought artifacts yet.")
        for item in thoughts:
            lines.append(
                "- "
                + f"{item.get('thought_id')} | backend={item.get('backend_name')} "
                + f"| mode={item.get('backend_mode')} | hypotheses={len(item.get('hypotheses') or [])}"
            )

        lines.extend(["", "## Recent Consciousness Events"])
        if not events:
            lines.append("- No events logged yet.")
        for event in events[:20]:
            lines.append(f"- {event.get('created_at')} | {event.get('event_type')} | {event.get('event_id')}")

        lines.extend(["", f"_Generated at { _utc_now_iso() }_"])
        return "\n".join(lines).strip() + "\n"

    def render_all(self, runtime: Any) -> dict[str, str]:
        return {
            "SOUL": self._render_soul(runtime),
            "IDENTITY": self._render_identity(runtime),
            "TOOLS": self._render_tools(runtime),
            "AGENTS": self._render_agents(runtime),
            "USER": self._render_user(runtime),
            "HEARTBEAT": self._render_heartbeat(runtime),
            "BOOT": self._render_boot(runtime),
            "MEMORY": self._render_memory(runtime),
        }

    def refresh(self, runtime: Any, *, reason: str = "manual") -> dict[str, Any]:
        surfaces = self.render_all(runtime)
        updated_at = _utc_now_iso()
        files: list[dict[str, Any]] = []
        for name, content in surfaces.items():
            path = self._surface_path(name)
            path.write_text(content, encoding="utf-8")
            files.append(
                {
                    "name": name,
                    "path": str(path),
                    "bytes": len(content.encode("utf-8")),
                }
            )
        index = {
            "updated_at": updated_at,
            "reason": reason,
            "root": str(self.root),
            "files": files,
        }
        self.index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
        return index

    def get_surfaces(self, *, include_content: bool = False) -> dict[str, Any]:
        if self.index_path.exists():
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
        else:
            index = {"updated_at": None, "reason": None, "root": str(self.root), "files": []}
        files: list[dict[str, Any]] = []
        for name in ("SOUL", "IDENTITY", "TOOLS", "AGENTS", "USER", "HEARTBEAT", "BOOT", "MEMORY"):
            path = self._surface_path(name)
            item = {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
            }
            if include_content and path.exists():
                item["content"] = path.read_text(encoding="utf-8")
            files.append(item)
        index["files"] = files
        return index
