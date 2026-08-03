"""`DependencyQueryAgent` unit tests — verifies the three-hook contract
and, per the RFC, that the agent performs NO retrieval itself:
`build_service_requests` requests exactly one `DependencyQueryCall` and
nothing else; every other method only reads an already-computed
`QueryResult`. Service execution and LLM calls are mocked (already
covered end-to-end by `tests/integration/test_dependency_query_agent.py`
and by the Frontier Framework's own tests)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from app.agents._contract import AgentContext, Subject
from app.agents.dependency_query.agent import DependencyQueryAgent
from app.agents.frontier.service_executor import DependencyQueryCall, ExecutionResult
from app.services.engineering_intelligence.contracts import QueryResult, RelationshipInsight

_REPO_ID = "11111111-1111-1111-1111-111111111111"


def _context() -> AgentContext:
    return AgentContext(
        subject=Subject(subject_id=f"repo:{_REPO_ID}", subject_type="repository"),
        goal="analyze_dependency_query",
        extras={"db": object()},
    )


def test_build_service_requests_requests_only_dependency_query() -> None:
    agent = DependencyQueryAgent()
    requests = agent.build_service_requests(_context())

    assert len(requests) == 1
    assert isinstance(requests[0], DependencyQueryCall)
    assert requests[0].repository_ids == (uuid.UUID(_REPO_ID),)


def test_build_prompt_summarizes_the_computed_query_result_without_retrieval() -> None:
    agent = DependencyQueryAgent()
    result = QueryResult(
        relationships=(
            RelationshipInsight(
                relationship_key="k",
                relationship_type="CALLS_SERVICE",
                source_entity=f"{_REPO_ID}:svc:a",
                target_entity="repo-2:svc:b",
                confidence_state="verified",
                explanation=None,
            ),
        ),
        total_matched=1,
    )
    execution = ExecutionResult(
        calls=(DependencyQueryCall(repository_ids=(uuid.UUID(_REPO_ID),)),),
        results=(result,),
        errors=(),
    )

    spec = agent.build_prompt(_context(), execution)

    assert spec is not None
    assert spec.stage == "dependency_query"
    assert "repo-2" in spec.user_prompt


def test_build_prompt_returns_none_when_query_result_missing() -> None:
    agent = DependencyQueryAgent()
    execution = ExecutionResult(calls=(), results=(), errors=("[0] dependency_query: failed",))

    assert agent.build_prompt(_context(), execution) is None


def test_render_response_uses_computed_result_and_narrative() -> None:
    agent = DependencyQueryAgent()
    result = QueryResult(
        relationships=(
            RelationshipInsight(
                relationship_key="k",
                relationship_type="CALLS_SERVICE",
                source_entity=f"{_REPO_ID}:svc:a",
                target_entity="repo-2:svc:b",
                confidence_state="verified",
                explanation=None,
            ),
        ),
        total_matched=1,
    )
    execution = ExecutionResult(
        calls=(DependencyQueryCall(repository_ids=(uuid.UUID(_REPO_ID),)),),
        results=(result,),
        errors=(),
    )
    narrative = {"repository": "This repository has one dependency."}

    rendered = agent.render_response(_context(), execution, narrative)

    assert rendered["executive_summary"].startswith("This repository has one dependency.")
    assert len(rendered["direct_dependencies"]) == 1


def test_render_response_degrades_gracefully_when_query_result_missing() -> None:
    agent = DependencyQueryAgent()
    execution = ExecutionResult(calls=(), results=(), errors=())

    rendered = agent.render_response(_context(), execution, {})

    assert rendered["repository_id"] == _REPO_ID
    assert rendered["direct_dependencies"] == []


async def test_run_end_to_end_calls_only_dependency_query_service() -> None:
    agent = DependencyQueryAgent()
    result = QueryResult(
        relationships=(
            RelationshipInsight(
                relationship_key="k",
                relationship_type="CALLS_SERVICE",
                source_entity=f"{_REPO_ID}:svc:a",
                target_entity="repo-2:svc:b",
                confidence_state="verified",
                explanation=None,
            ),
        ),
        total_matched=1,
    )

    with (
        patch(
            "app.agents.frontier.service_executor.dependency_query_service.search",
            new=AsyncMock(return_value=result),
        ) as mock_search,
        patch(
            "app.agents.frontier.base_frontier_agent.prompt_builder.run",
            new=AsyncMock(
                return_value=({"repository": "This repository has one dependency."}, None)
            ),
        ),
    ):
        context = AgentContext(
            subject=Subject(subject_id=f"repo:{_REPO_ID}", subject_type="repository"),
            goal="analyze_dependency_query",
            extras={"db": object()},
        )
        output = await agent.run(context)

    mock_search.assert_awaited_once()
    assert output.agent_id == "dependency_query"
    assert len(output.result["direct_dependencies"]) == 1
    assert output.confidence.score == 1.0
