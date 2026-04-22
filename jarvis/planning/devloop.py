from __future__ import annotations

from typing import Any
from uuid import uuid4

from .milestones import MilestonePlanner
from .next_action_ranker import NextActionRanker
from .project_graph import ProjectGraphStore


class ProjectDevLoop:
    def __init__(
        self,
        store: ProjectGraphStore,
        *,
        ranker: NextActionRanker | None = None,
        milestones: MilestonePlanner | None = None,
    ) -> None:
        self.store = store
        self.ranker = ranker or NextActionRanker()
        self.milestones = milestones or MilestonePlanner(store)

    def _project_root_node(self, project_id: str) -> dict[str, Any]:
        return self.store.upsert_node(
            project_id=project_id,
            node_id=f"project:{project_id}",
            kind="project",
            status="active",
            external_ref=project_id,
            score=1.0,
            payload={},
        )

    def ingest_signal(self, *, project_id: str, signal: dict[str, Any]) -> dict[str, Any]:
        project_root = self._project_root_node(project_id)
        signal_type = str(signal.get("type") or signal.get("signal_type") or "unknown").strip().lower()
        normalized_signal = dict(signal or {})
        results: dict[str, Any] = {
            "project_id": str(project_id),
            "signal_type": signal_type,
            "created_nodes": [],
            "created_edges": [],
            "created_actions": [],
        }

        if signal_type in {"ci_failed", "check_failed", "workflow_failed"}:
            run_ref = str(signal.get("run_id") or signal.get("check_id") or f"ci_{uuid4().hex}")
            node = self.store.upsert_node(
                project_id=project_id,
                node_id=f"ci:{run_ref}",
                kind="ci_run",
                status="failed",
                external_ref=run_ref,
                score=0.9,
                payload=normalized_signal,
            )
            edge = self.store.upsert_edge(
                project_id=project_id,
                from_node=str(project_root.get("node_id")),
                to_node=str(node.get("node_id")),
                relation="tracks",
                payload={"source": "ci"},
            )
            action = self.store.record_action(
                project_id=project_id,
                action_type="fix_ci",
                reason="CI run failed and should be repaired before downstream work.",
                expected_value=0.9,
                confidence=0.85,
                required_authority="soft",
                metadata={"signal": normalized_signal},
            )
            results["created_nodes"].append(node)
            results["created_edges"].append(edge)
            results["created_actions"].append(action)
            return results

        if signal_type in {"pull_request_opened", "pull_request_updated", "pull_request_review_changes"}:
            pr_ref = str(signal.get("pr_number") or signal.get("pr_id") or f"pr_{uuid4().hex}")
            status = "open"
            if signal_type == "pull_request_review_changes":
                status = "changes_requested"
            node = self.store.upsert_node(
                project_id=project_id,
                node_id=f"pr:{pr_ref}",
                kind="pull_request",
                status=status,
                external_ref=pr_ref,
                score=0.75,
                payload=normalized_signal,
            )
            edge = self.store.upsert_edge(
                project_id=project_id,
                from_node=str(project_root.get("node_id")),
                to_node=str(node.get("node_id")),
                relation="implements",
                payload={"source": "github"},
            )
            action_type = "review_pr" if status == "open" else "address_review_feedback"
            action = self.store.record_action(
                project_id=project_id,
                action_type=action_type,
                reason="PR signal indicates active code review flow requiring intervention.",
                expected_value=0.78 if status == "open" else 0.84,
                confidence=0.81,
                required_authority="none",
                metadata={"signal": normalized_signal},
            )
            results["created_nodes"].append(node)
            results["created_edges"].append(edge)
            results["created_actions"].append(action)
            return results

        if signal_type in {"milestone_due", "milestone_created", "milestone_updated"}:
            milestone_id = str(signal.get("milestone_id") or signal.get("title") or f"ms_{uuid4().hex}")
            milestone = self.milestones.ensure_milestone(
                project_id=project_id,
                milestone_id=f"milestone:{milestone_id}",
                status=str(signal.get("status") or "open"),
                payload=normalized_signal,
            )
            edge = self.store.upsert_edge(
                project_id=project_id,
                from_node=str(project_root.get("node_id")),
                to_node=str(milestone.get("node_id")),
                relation="tracks",
                payload={"source": "milestone"},
            )
            action = self.store.record_action(
                project_id=project_id,
                action_type="align_milestone",
                reason="Milestone signal indicates schedule coordination is needed.",
                expected_value=0.7,
                confidence=0.76,
                required_authority="none",
                metadata={"signal": normalized_signal},
            )
            results["created_nodes"].append(milestone)
            results["created_edges"].append(edge)
            results["created_actions"].append(action)
            return results

        generic_id = str(signal.get("id") or f"sig_{uuid4().hex}")
        node = self.store.upsert_node(
            project_id=project_id,
            node_id=f"signal:{generic_id}",
            kind="signal",
            status="new",
            external_ref=generic_id,
            score=0.4,
            payload=normalized_signal,
        )
        edge = self.store.upsert_edge(
            project_id=project_id,
            from_node=str(project_root.get("node_id")),
            to_node=str(node.get("node_id")),
            relation="observes",
            payload={"source": "generic"},
        )
        action = self.store.record_action(
            project_id=project_id,
            action_type="triage_signal",
            reason="New project signal should be triaged.",
            expected_value=0.5,
            confidence=0.65,
            required_authority="none",
            metadata={"signal": normalized_signal},
        )
        results["created_nodes"].append(node)
        results["created_edges"].append(edge)
        results["created_actions"].append(action)
        return results

    def get_project_graph(
        self,
        *,
        project_id: str,
        node_limit: int = 200,
        edge_limit: int = 200,
    ) -> dict[str, Any]:
        nodes = self.store.list_nodes(project_id=project_id, limit=node_limit)
        edges = self.store.list_edges(project_id=project_id, limit=edge_limit)
        return {
            "project_id": str(project_id),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }

    def propose_next_actions(self, *, project_id: str, limit: int = 5) -> list[dict[str, Any]]:
        actions = self.store.list_actions(project_id=project_id, limit=max(50, int(limit) * 5))
        return self.ranker.rank(actions, limit=limit)
