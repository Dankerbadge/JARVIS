from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import EventEnvelope, new_id, utc_now_iso
from ..state_index import latest_personal_context_key, latest_user_model_key


class IdentitySkill:
    """Skill for user-model and personal-context state artifacts."""

    def __init__(self, workspace_path: str | Path) -> None:
        self.workspace_path = Path(workspace_path).resolve()

    def extract_candidates(self, event: EventEnvelope) -> list[dict[str, Any]]:
        source_type = str(event.source_type or "").strip().lower()
        payload = dict(event.payload or {})
        source_refs = [event.event_id]
        candidates: list[dict[str, Any]] = []

        if source_type in {"identity.user_model_updated", "identity.goal_hierarchy_updated"}:
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_user_model_key("default"),
                    "entity_type": "Artifact",
                    "value": {
                        "profile_id": "default",
                        "domain": "personal",
                        "project": "personal",
                        "model": payload,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.95,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )

        if source_type in {"personal.context_signal", "personal.context_snapshot"}:
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_personal_context_key("default"),
                    "entity_type": "Artifact",
                    "value": {
                        "profile_id": "default",
                        "domain": "personal",
                        "project": "personal",
                        "context": payload,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.9,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )
        return candidates

    def register_tools(self) -> dict[str, Any]:
        return {}

