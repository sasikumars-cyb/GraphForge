"""Dependency Query Agent — goal=analyze_dependency_query.

The third Frontier Engineering Intelligence Agent, and the second
Engineering Intelligence Service (after `RepositoryProfileService` and
`ImpactAnalysisService`) plugged into `BaseFrontierAgent` with zero
framework changes — validating the framework generalizes across services,
not just across agents built on the same one.

This agent performs no retrieval itself: `build_service_requests` returns
exactly one `DependencyQueryCall`, and every other method here only ever
reads the `QueryResult` `ServiceExecutor` already computed. No Neo4j call,
no Cypher, no Postgres query, no `EngineeringMemoryService` call appears
anywhere in this file.
"""

from __future__ import annotations

from typing import Any

from app.agents._contract import AgentContext
from app.agents.dependency_query.prompt import build_dependency_query_prompt
from app.agents.dependency_query.renderer import render_dependency_query
from app.agents.frontier.agent_context import get_repository_id
from app.agents.frontier.base_frontier_agent import BaseFrontierAgent
from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.frontier.service_executor import DependencyQueryCall, ExecutionResult, ServiceCall
from app.agents.llm import STAGE_DEPENDENCY_QUERY
from app.services.engineering_intelligence.contracts import QueryResult


class DependencyQueryAgent(BaseFrontierAgent):
    agent_id = "dependency_query"
    default_stage = STAGE_DEPENDENCY_QUERY

    def build_service_requests(self, context: AgentContext) -> list[ServiceCall]:
        repository_id = get_repository_id(context)
        return [DependencyQueryCall(repository_ids=(repository_id,))]

    def build_prompt(self, context: AgentContext, execution: ExecutionResult) -> PromptSpec | None:
        result = self._query_result(execution)
        if result is None:
            return None
        return build_dependency_query_prompt(str(get_repository_id(context)), result)

    def render_response(
        self, context: AgentContext, execution: ExecutionResult, narrative: dict[str, Any]
    ) -> dict[str, Any]:
        repository_id = str(get_repository_id(context))
        result = self._query_result(execution)
        if result is None:
            result = QueryResult()
        return render_dependency_query(repository_id, result, narrative)

    def _query_result(self, execution: ExecutionResult) -> QueryResult | None:
        for result in execution.results:
            if isinstance(result, QueryResult):
                return result
        return None
