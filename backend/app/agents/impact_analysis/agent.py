"""Impact Analysis Agent — goal=analyze_impact_analysis.

The second Frontier Engineering Intelligence Agent, following the exact
`RepositoryUnderstandingAgent` pattern: inherits `BaseFrontierAgent` and
implements exactly the three hooks. Everything else comes from the
framework unmodified.

This agent performs no retrieval or traversal itself:
`build_service_requests` returns exactly one `ImpactAnalysisCall`, and
every other method here only ever reads the `BlastRadius`
`ServiceExecutor` already computed. No Neo4j call, no Cypher, no
Postgres query, no `EngineeringMemoryService` call appears anywhere in
this file.

The blast radius is seeded from the repository's own root graph node
(`f"{repository_id}:repository"` — the `CONTAINS`-edge hub every
`Controller`/`Service`/`FeignClient`/`MavenDependency` in that repository
connects to; see `app.indexer.graph.builder.build_graph`), since the only
`subject_reference` form available (`repo:<uuid>`) resolves to a whole
repository, not a single component — this answers "what does a change to
this repository affect," not "what does changing this one method affect."
"""

from __future__ import annotations

from typing import Any

from app.agents._contract import AgentContext
from app.agents.frontier.agent_context import get_repository_id
from app.agents.frontier.base_frontier_agent import BaseFrontierAgent
from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.frontier.service_executor import ExecutionResult, ImpactAnalysisCall, ServiceCall
from app.agents.impact_analysis.prompt import build_impact_analysis_prompt
from app.agents.impact_analysis.renderer import render_impact_analysis
from app.agents.llm import STAGE_IMPACT_ANALYSIS
from app.services.engineering_intelligence.contracts import BlastRadius, EntityReference


class ImpactAnalysisAgent(BaseFrontierAgent):
    agent_id = "impact_analysis"
    default_stage = STAGE_IMPACT_ANALYSIS

    def build_service_requests(self, context: AgentContext) -> list[ServiceCall]:
        repository_id = get_repository_id(context)
        entity = EntityReference(
            repository_id=str(repository_id), node_id=f"{repository_id}:repository"
        )
        return [ImpactAnalysisCall(entity=entity, direction="downstream", max_hops=2)]

    def build_prompt(self, context: AgentContext, execution: ExecutionResult) -> PromptSpec | None:
        blast_radius = self._blast_radius(execution)
        if blast_radius is None:
            return None
        return build_impact_analysis_prompt(blast_radius)

    def render_response(
        self, context: AgentContext, execution: ExecutionResult, narrative: dict[str, Any]
    ) -> dict[str, Any]:
        blast_radius = self._blast_radius(execution)
        if blast_radius is None:
            repository_id = get_repository_id(context)
            blast_radius = BlastRadius(
                seed=EntityReference(
                    repository_id=str(repository_id), node_id=f"{repository_id}:repository"
                ),
                direction="downstream",
                max_hops=2,
            )
        return render_impact_analysis(blast_radius, narrative)

    def _blast_radius(self, execution: ExecutionResult) -> BlastRadius | None:
        for result in execution.results:
            if isinstance(result, BlastRadius):
                return result
        return None
