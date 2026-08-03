"""`BaseFrontierAgent.run` — end-to-end through a minimal concrete
subclass, with `ServiceExecutor`'s underlying service calls mocked
(already covered for real by `tests/integration/test_engineering_intelligence_*.py`
and `test_service_executor.py`). Verifies the three-hook contract and
that everything else (extras access, timing, output assembly) needs zero
code in the subclass."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.frontier.base_frontier_agent import BaseFrontierAgent
from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.frontier.service_executor import DependencyQueryCall, ExecutionResult

pytestmark = pytest.mark.asyncio


class _RepositoryUnderstandingAgent(BaseFrontierAgent):
    agent_id = "repository_understanding"
    default_stage = "repository_understanding"

    def build_service_requests(self, context: AgentContext) -> list:
        return [DependencyQueryCall(repository_ids=())]

    def build_prompt(self, context: AgentContext, execution: ExecutionResult) -> PromptSpec | None:
        return PromptSpec(system_prompt="sys", user_prompt="user", stage=self.default_stage)

    def render_response(
        self, context: AgentContext, execution: ExecutionResult, narrative: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "summary": narrative.get("summary", "no narrative"),
            "call_count": len(execution.calls),
        }


class _NoPromptAgent(BaseFrontierAgent):
    agent_id = "dependency_explorer"
    default_stage = "dependency_explorer"

    def build_service_requests(self, context: AgentContext) -> list:
        return []

    def build_prompt(self, context: AgentContext, execution: ExecutionResult) -> PromptSpec | None:
        return None

    def render_response(
        self, context: AgentContext, execution: ExecutionResult, narrative: dict[str, Any]
    ) -> dict[str, Any]:
        return {"narrative_was": narrative}


def _context() -> AgentContext:
    return AgentContext(
        subject=Subject(
            subject_id="repo:11111111-1111-1111-1111-111111111111", subject_type="repository"
        ),
        goal="analyze_repository_understanding",
        extras={"db": object()},
    )


async def test_run_executes_services_calls_llm_and_renders_result() -> None:
    agent = _RepositoryUnderstandingAgent()
    with (
        patch(
            "app.agents.frontier.base_frontier_agent.service_executor.execute",
            new=AsyncMock(
                return_value=ExecutionResult(
                    calls=(DependencyQueryCall(repository_ids=()),), results=("result",), errors=()
                )
            ),
        ) as mock_execute,
        patch(
            "app.agents.frontier.base_frontier_agent.prompt_builder.run",
            new=AsyncMock(return_value=({"summary": "narrated"}, None)),
        ) as mock_prompt,
    ):
        output = await agent.run(_context())

    mock_execute.assert_awaited_once()
    mock_prompt.assert_awaited_once()
    assert output.agent_id == "repository_understanding"
    assert output.subject_id == "repo:11111111-1111-1111-1111-111111111111"
    assert output.result["summary"] == "narrated"
    assert output.result["call_count"] == 1
    assert output.confidence.score == 1.0
    assert output.output_ref == "repository_understanding:repo:11111111-1111-1111-1111-111111111111"
    assert "metrics" in output.result
    assert output.result["metrics"]["total_duration_ms"] is not None


async def test_run_skips_llm_call_when_build_prompt_returns_none() -> None:
    agent = _NoPromptAgent()
    with (
        patch(
            "app.agents.frontier.base_frontier_agent.service_executor.execute",
            new=AsyncMock(return_value=ExecutionResult(calls=(), results=(), errors=())),
        ),
        patch(
            "app.agents.frontier.base_frontier_agent.prompt_builder.run", new=AsyncMock()
        ) as mock_prompt,
    ):
        output = await agent.run(_context())

    mock_prompt.assert_not_called()
    assert output.result["narrative_was"] == {}


async def test_run_passes_db_and_graph_repository_from_context_extras() -> None:
    agent = _NoPromptAgent()
    sentinel_db = object()
    sentinel_graph = object()
    context = AgentContext(
        subject=Subject(subject_id="repo:x", subject_type="repository"),
        goal="g",
        extras={"db": sentinel_db, "graph_repository": sentinel_graph},
    )

    with patch(
        "app.agents.frontier.base_frontier_agent.service_executor.execute",
        new=AsyncMock(return_value=ExecutionResult(calls=(), results=(), errors=())),
    ) as mock_execute:
        await agent.run(context)

    args, _ = mock_execute.call_args
    assert args[0] is sentinel_db
    assert args[1] is sentinel_graph
