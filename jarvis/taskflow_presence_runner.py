from __future__ import annotations

from typing import Any


class TaskFlowPresenceRunner:
    """Durable heartbeat/re-attachment runner intended for Task Flow orchestration."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def _extract_checklist(self, surfaces: dict[str, Any], surface_name: str) -> list[str]:
        files = list(surfaces.get("files") or [])
        for item in files:
            if str(item.get("name") or "").strip().upper() != surface_name.upper():
                continue
            content = str(item.get("content") or "")
            lines: list[str] = []
            for raw in content.splitlines():
                stripped = raw.strip()
                if not stripped.startswith("- "):
                    continue
                lines.append(stripped[2:].strip())
            return lines
        return []

    def run_cycle(self, *, reason: str = "taskflow_presence_cycle") -> dict[str, Any]:
        refreshed = self.runtime.refresh_consciousness_surfaces(reason=reason)
        surfaces = self.runtime.get_consciousness_surfaces(include_content=True)
        heartbeat_checklist = self._extract_checklist(surfaces, "HEARTBEAT")
        boot_checklist = self._extract_checklist(surfaces, "BOOT")
        ended_sessions = self.runtime.list_surface_sessions(status="ended", limit=200)
        health = self.runtime.get_presence_health()
        bridge = health.get("bridge") if isinstance(health.get("bridge"), dict) else {}
        heartbeat = self.runtime.run_presence_heartbeat()
        unresolved_risks = self.runtime.state_graph.get_active_entities("Risk")
        return {
            "reason": reason,
            "surfaces_refreshed": len(list(refreshed.get("files") or [])),
            "presence_heartbeat": heartbeat,
            "unresolved_risk_count": len(unresolved_risks),
            "heartbeat_checklist": heartbeat_checklist,
            "boot_checklist": boot_checklist,
            "missed_session_count": len(ended_sessions),
            "reconnect_required": not bool(bridge.get("connected")),
            "consciousness_contract_hash": self.runtime.get_consciousness_contract_hash(),
        }
