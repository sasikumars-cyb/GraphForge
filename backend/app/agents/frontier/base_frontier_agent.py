"""`BaseFrontierAgent` — the shared run loop every Engineering Intelligence
Agent inherits. Implements `IAgent` (the frozen Protocol in
`app.agents._contract`); `RunCoordinator` calls `run()` exactly as it
calls every other agent — no orchestrator-side change of any kind.

Subclasses implement exactly three hooks and nothing else:

- `build_service_requests(context) -> list[ServiceCall]` — pure, no I/O
  (`app.agents.frontier.service_request_builder.ServiceRequestBuilder`).
- `build_prompt(context, execution) -> PromptSpec | None` — pure; `None`
  skips the LLM call entirely (a valid, deliberate choice, not a failure
  path).
- `render_response(context, execution, narrative) -> dict[str, object]` —
  pure; turns computed facts into the agent's own result shape.

Everything else — pulling `db`/`graph_repository` off `context.extras`,
calling the Engineering Intelligence Services, calling the LLM, timing,
and assembling the final `AgentOutput` — lives here, once. Exceptions
raised by any hook or by `ServiceExecutor.execute` are NOT caught here:
`RunCoordinator.execute_run` already wraps `await agent.run(context)` and
persists failures as a failed `Run`/`AgentStep` (its own docstring:
"Never swallows exceptions — errors are persisted as..."); catching here
too would just duplicate that handling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.agents._contract import AgentContext, AgentOutput
from app.agents.frontier import prompt_builder, result_mapper, service_executor
from app.agents.frontier.agent_context import get_db, get_graph_repository
from app.agents.frontier.agent_metrics import AgentMetrics
from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.frontier.service_executor import ExecutionResult, ServiceCall


def _confidence_states(execution: ExecutionResult) -> list[str]:
    """Best-effort: only some service results carry
    `RelationshipInsight`s (`BlastRadius.relationships`,
    `QueryResult.relationships`); others (`RepositoryProfile`, a tuple of
    `ArchitectureFinding`) don't. `getattr(..., "relationships", ())`
    reads what's there without this shared loop needing to know each
    service's return type."""
    states: list[str] = []
    for result in execution.results:
        for insight in getattr(result, "relationships", ()):
            state = getattr(insight, "confidence_state", None)
            if state is not None:
                states.append(state)
    return states


class BaseFrontierAgent(ABC):
    """`agent_id` and `default_stage` are set by each subclass — the same
    two identifiers `documentation_health` already declares (agent_id in
    its own `AgentOutput.agent_id`, stage via `STAGE_DOCUMENTATION_HEALTH`)."""

    agent_id: str
    default_stage: str

    @abstractmethod
    def build_service_requests(self, context: AgentContext) -> list[ServiceCall]: ...

    @abstractmethod
    def build_prompt(
        self, context: AgentContext, execution: ExecutionResult
    ) -> PromptSpec | None: ...

    @abstractmethod
    def render_response(
        self, context: AgentContext, execution: ExecutionResult, narrative: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def run(self, context: AgentContext) -> AgentOutput:
        metrics = AgentMetrics()
        metrics.start_run()

        db = get_db(context)
        graph_repository = get_graph_repository(context)

        requests = self.build_service_requests(context)

        metrics.start_service_call()
        execution = await service_executor.execute(db, graph_repository, requests)
        metrics.stop_service_call()
        metrics.record_confidence_states(_confidence_states(execution))

        prompt_spec = self.build_prompt(context, execution)
        narrative: dict[str, Any] = {}
        narrative_evidence = None
        if prompt_spec is not None:
            metrics.start_llm_call()
            narrative, narrative_evidence = await prompt_builder.run(context, prompt_spec)
            metrics.stop_llm_call()

        rendered = self.render_response(context, execution, narrative)
        metrics.stop_run()

        return result_mapper.to_agent_output(
            agent_id=self.agent_id,
            subject_id=context.subject.subject_id,
            execution=execution,
            narrative_evidence=narrative_evidence,
            rendered=rendered,
            metrics=metrics,
            output_ref=f"{self.agent_id}:{context.subject.subject_id}",
        )
