from __future__ import annotations

from typing import Any

from ..models import PlanArtifact
from ..skills.academics import AcademicsSkill
from ..skills.markets import MarketsSkill
from ..skills.zenith import ZenithSkill
from ..state_graph import StateGraph


class Planner:
    def __init__(
        self,
        zenith: ZenithSkill,
        academics: AcademicsSkill,
        markets: MarketsSkill,
        state_graph: StateGraph,
    ) -> None:
        self.zenith = zenith
        self.academics = academics
        self.markets = markets
        self.state_graph = state_graph

    def build_plans(self, triggers: list[dict[str, Any]]) -> list[PlanArtifact]:
        if not triggers:
            return []
        risks = self.state_graph.get_active_entities("Risk")
        domains = {
            str(item.get("domain") or item.get("project") or "").strip().lower()
            for item in triggers
        }
        known_domains = {value for value in domains if value in {"zenith", "academics", "markets"}}
        if not known_domains:
            known_domains = {"zenith"}
        plans: list[PlanArtifact] = []
        if "zenith" in known_domains:
            zenith_plan = self.zenith.propose_plan(risks)
            if zenith_plan:
                plans.append(zenith_plan)
        if "academics" in known_domains:
            academics_plan = self.academics.propose_plan(risks)
            if academics_plan:
                plans.append(academics_plan)
        if "markets" in known_domains:
            markets_plan = self.markets.propose_plan(risks)
            if markets_plan:
                plans.append(markets_plan)
        return plans

