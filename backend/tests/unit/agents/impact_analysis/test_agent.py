"""`ImpactAnalysisAgent` unit tests — verifies the three-hook contract and,
per the RFC, that the agent performs NO traversal itself:
`build_service_requests` requests exactly one `ImpactAnalysisCall` and
nothing else; every other method only reads an already-computed
`BlastRadius`. Service execution and LLM calls are mocked (already
covered end-to-end by `tests/integration/test_impact_analysis_agent.py`
and by the Frontier Framework's own tests)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.agents._contract import AgentContext, Subject
from app.agents.frontier.service_executor import ExecutionResult, ImpactAnalysisCall
from app.agents.impact_analysis.agent import ImpactAnalysisAgent
from app.services.engineering_intelligence.contracts import BlastRadius, EntityReference

_REPO_ID = "11111111-1111-1111-1111-111111111111"


def _context() -> AgentContext:
    return AgentContext(
        subject=Subject(subject_id=f"repo:{_REPO_ID}", subject_type="repository"),
        goal="analyze_impact_analysis",
        extras={"db": object()},
    )


def test_build_service_requests_requests_only_impact_analysis() -> None:
    agent = ImpactAnalysisAgent()
    requests = agent.build_service_requests(_context())

    assert len(requests) == 1
    assert isinstance(requests[0], ImpactAnalysisCall)
    assert requests[0].entity.repository_id == _REPO_ID
    assert requests[0].entity.node_id == f"{_REPO_ID}:repository"
    assert requests[0].direction == "downstream"


def test_build_prompt_summarizes_the_computed_blast_radius_without_retrieval() -> None:
    agent = ImpactAnalysisAgent()
    blast_radius = BlastRadius(
        seed=EntityReference(repository_id=_REPO_ID, node_id=f"{_REPO_ID}:repository"),
        direction="downstream",
        max_hops=2,
        impacted_repositories=("repo-2",),
    )
    execution = ExecutionResult(
        calls=(ImpactAnalysisCall(entity=blast_radius.seed),),
        results=(blast_radius,),
        errors=(),
    )

    spec = agent.build_prompt(_context(), execution)

    assert spec is not None
    assert spec.stage == "impact_analysis"
    assert "repo-2" in spec.user_prompt


def test_build_prompt_returns_none_when_blast_radius_missing() -> None:
    agent = ImpactAnalysisAgent()
    execution = ExecutionResult(calls=(), results=(), errors=("[0] impact_analysis: failed",))

    assert agent.build_prompt(_context(), execution) is None


def test_render_response_uses_computed_blast_radius_and_narrative() -> None:
    agent = ImpactAnalysisAgent()
    blast_radius = BlastRadius(
        seed=EntityReference(repository_id=_REPO_ID, node_id=f"{_REPO_ID}:repository"),
        direction="downstream",
        max_hops=2,
        impacted_repositories=("repo-2",),
    )
    execution = ExecutionResult(
        calls=(ImpactAnalysisCall(entity=blast_radius.seed),),
        results=(blast_radius,),
        errors=(),
    )
    narrative = {"executive_summary": "Changing this repository affects repo-2."}

    rendered = agent.render_response(_context(), execution, narrative)

    assert rendered["executive_summary"].startswith("Changing this repository affects repo-2.")
    assert rendered["directly_impacted_repositories"] == ["repo-2"]


def test_render_response_degrades_gracefully_when_blast_radius_missing() -> None:
    agent = ImpactAnalysisAgent()
    execution = ExecutionResult(calls=(), results=(), errors=())

    rendered = agent.render_response(_context(), execution, {})

    assert rendered["seed_repository_id"] == _REPO_ID
    assert rendered["directly_impacted_repositories"] == []


async def test_run_end_to_end_calls_only_impact_analysis_service() -> None:
    agent = ImpactAnalysisAgent()
    blast_radius = BlastRadius(
        seed=EntityReference(repository_id=_REPO_ID, node_id=f"{_REPO_ID}:repository"),
        direction="downstream",
        max_hops=2,
        impacted_repositories=("repo-2",),
    )

    with (
        patch(
            "app.agents.frontier.service_executor.impact_analysis_service.compute_blast_radius",
            new=AsyncMock(return_value=blast_radius),
        ) as mock_compute,
        patch(
            "app.agents.frontier.base_frontier_agent.prompt_builder.run",
            new=AsyncMock(
                return_value=(
                    {"executive_summary": "Changing this repository affects repo-2."},
                    None,
                )
            ),
        ),
    ):
        context = AgentContext(
            subject=Subject(subject_id=f"repo:{_REPO_ID}", subject_type="repository"),
            goal="analyze_impact_analysis",
            extras={"db": object(), "graph_repository": object()},
        )
        output = await agent.run(context)

    mock_compute.assert_awaited_once()
    assert output.agent_id == "impact_analysis"
    assert output.result["directly_impacted_repositories"] == ["repo-2"]
    assert output.confidence.score == 1.0
