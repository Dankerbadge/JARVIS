from __future__ import annotations

from typing import Any

from .project_graph import ProjectGraphStore


class MilestonePlanner:
    def __init__(self, store: ProjectGraphStore) -> None:
        self.store = store

    def ensure_milestone(
        self,
        *,
        project_id: str,
        milestone_id: str,
        status: str = "open",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.store.upsert_node(
            project_id=project_id,
            node_id=str(milestone_id),
            kind="milestone",
            status=status,
            external_ref=str(payload.get("external_ref")) if isinstance(payload, dict) and payload.get("external_ref") else None,
            score=None,
            payload=payload,
        )

    def summarize_progress(self, *, project_id: str) -> dict[str, Any]:
        nodes = self.store.list_nodes(project_id=project_id, limit=1000)
        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for node in nodes:
            kind = str(node.get("kind") or "unknown")
            status = str(node.get("status") or "unknown")
            by_kind[kind] = int(by_kind.get(kind, 0)) + 1
            by_status[status] = int(by_status.get(status, 0)) + 1
        return {
            "project_id": str(project_id),
            "node_count": len(nodes),
            "by_kind": by_kind,
            "by_status": by_status,
        }
