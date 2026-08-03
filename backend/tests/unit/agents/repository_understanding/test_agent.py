"""`RepositoryUnderstandingAgent` unit tests — verifies the three-hook
contract and, per the RFC, that the agent performs NO retrieval itself:
`build_service_requests` requests exactly one `RepositoryProfileCall` and
nothing else; every other method only reads an already-computed
`RepositoryProfile`. Service execution and LLM calls are mocked (already
covered end-to-end by `tests/integration/test_repository_understanding_agent.py`
and by the Frontier Framework's own tests)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.frontier.service_executor import ExecutionResult, RepositoryProfileCall
from app.agents.repository_understanding.agent import RepositoryUnderstandingAgent
from app.services.engineering_intelligence.contracts import RepositoryProfile

_REPO_ID = "11111111-1111-1111-1111-111111111111"


def _context() -> AgentContext:
    return AgentContext(
        subject=Subject(subject_id=f"repo:{_REPO_ID}", subject_type="repository"),
        goal="analyze_repository_understanding",
        extras={"db": object()},
    )


def test_build_service_requests_requests_only_repository_profile() -> None:
    agent = RepositoryUnderstandingAgent()
    requests = agent.build_service_requests(_context())

    assert len(requests) == 1
    assert isinstance(requests[0], RepositoryProfileCall)
    assert str(requests[0].repository_id) == _REPO_ID


def test_build_prompt_summarizes_the_computed_profile_without_retrieval() -> None:
    agent = RepositoryUnderstandingAgent()
    profile = RepositoryProfile(
        repository_id=_REPO_ID, apis=("GET /orders",), databases=("orders",)
    )
    execution = ExecutionResult(
        calls=(RepositoryProfileCall(repository_id=_REPO_ID),), results=(profile,), errors=()
    )

    spec = agent.build_prompt(_context(), execution)

    assert spec is not None
    assert spec.stage == "repository_understanding"
    assert "GET /orders" in spec.user_prompt
    assert "orders" in spec.user_prompt


def test_build_prompt_returns_none_when_profile_missing() -> None:
    agent = RepositoryUnderstandingAgent()
    execution = ExecutionResult(calls=(), results=(), errors=("[0] repository_profile: failed",))

    assert agent.build_prompt(_context(), execution) is None


def test_render_response_uses_computed_profile_and_narrative() -> None:
    agent = RepositoryUnderstandingAgent()
    profile = RepositoryProfile(repository_id=_REPO_ID, apis=("GET /orders",))
    execution = ExecutionResult(
        calls=(RepositoryProfileCall(repository_id=_REPO_ID),), results=(profile,), errors=()
    )
    narrative = {"executive_summary": "A checkout service."}

    rendered = agent.render_response(_context(), execution, narrative)

    assert rendered["executive_summary"].startswith("A checkout service.")
    assert rendered["apis"] == ["GET /orders"]
    assert "markdown" in rendered


def test_render_response_degrades_gracefully_when_profile_missing() -> None:
    agent = RepositoryUnderstandingAgent()
    execution = ExecutionResult(calls=(), results=(), errors=())

    rendered = agent.render_response(_context(), execution, {})

    assert rendered["repository_id"] == _REPO_ID
    assert rendered["apis"] == []


@pytest.mark.asyncio
async def test_run_end_to_end_calls_only_repository_profile_service() -> None:
    agent = RepositoryUnderstandingAgent()
    profile = RepositoryProfile(repository_id=_REPO_ID, apis=("GET /orders",))

    with (
        patch(
            "app.agents.frontier.service_executor.repository_profile_service.get_profile",
            new=AsyncMock(return_value=profile),
        ) as mock_get_profile,
        patch(
            "app.agents.frontier.base_frontier_agent.prompt_builder.run",
            new=AsyncMock(return_value=({"executive_summary": "A checkout service."}, None)),
        ),
    ):
        context = AgentContext(
            subject=Subject(subject_id=f"repo:{_REPO_ID}", subject_type="repository"),
            goal="analyze_repository_understanding",
            extras={"db": object(), "graph_repository": object()},
        )
        output = await agent.run(context)

    mock_get_profile.assert_awaited_once()
    assert output.agent_id == "repository_understanding"
    assert output.result["apis"] == ["GET /orders"]
    assert output.confidence.score == 1.0
