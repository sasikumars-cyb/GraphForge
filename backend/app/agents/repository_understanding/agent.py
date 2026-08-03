"""Repository Understanding Agent — goal=analyze_repository_understanding.

The reference implementation of a Frontier Engineering Intelligence Agent:
inherits `BaseFrontierAgent` and implements exactly the three hooks
(`build_service_requests`, `build_prompt`, `render_response`). Everything
else — pulling `db`/`graph_repository` off `AgentContext.extras`, calling
`RepositoryProfileService`, calling the LLM, timing, and assembling the
final `AgentOutput` — comes from the framework unmodified.

This agent performs no retrieval itself: `build_service_requests` returns
exactly one `RepositoryProfileCall`, and every other method here only
ever reads the `RepositoryProfile` `ServiceExecutor` already computed. No
Neo4j call, no Postgres query, no `EngineeringMemoryService` call appears
anywhere in this file.
"""

from __future__ import annotations

from typing import Any

from app.agents._contract import AgentContext
from app.agents.frontier.agent_context import get_repository_id
from app.agents.frontier.base_frontier_agent import BaseFrontierAgent
from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.frontier.service_executor import ExecutionResult, RepositoryProfileCall, ServiceCall
from app.agents.llm import STAGE_REPOSITORY_UNDERSTANDING
from app.agents.repository_understanding.prompt import build_repository_understanding_prompt
from app.agents.repository_understanding.renderer import render_repository_understanding
from app.services.engineering_intelligence.contracts import RepositoryProfile


class RepositoryUnderstandingAgent(BaseFrontierAgent):
    agent_id = "repository_understanding"
    default_stage = STAGE_REPOSITORY_UNDERSTANDING

    def build_service_requests(self, context: AgentContext) -> list[ServiceCall]:
        return [RepositoryProfileCall(repository_id=get_repository_id(context))]

    def build_prompt(self, context: AgentContext, execution: ExecutionResult) -> PromptSpec | None:
        profile = self._profile(execution)
        if profile is None:
            return None
        return build_repository_understanding_prompt(profile)

    def render_response(
        self, context: AgentContext, execution: ExecutionResult, narrative: dict[str, Any]
    ) -> dict[str, Any]:
        profile = self._profile(execution)
        if profile is None:
            repository_id = str(get_repository_id(context))
            profile = RepositoryProfile(repository_id=repository_id)
        return render_repository_understanding(profile, narrative)

    def _profile(self, execution: ExecutionResult) -> RepositoryProfile | None:
        for result in execution.results:
            if isinstance(result, RepositoryProfile):
                return result
        return None
