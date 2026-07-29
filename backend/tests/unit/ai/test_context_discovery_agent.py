"""Unit tests for the Context Discovery Agent (goal=discover_context).

Covers:
- build_context_discovery_result: projection from EnrichedPlanningRequest
- _confidence_for: the three-tier confidence formula (unavailable / has
  data / healthy-empty), mirrored from Planning's own historical formula
- ContextDiscoveryAgent.run(): AgentOutput envelope, evidence, and that
  the pipeline is invoked with the right arguments

No real Neo4j/LLM/DB — ContextResolutionPipeline.resolve() is mocked to
return a hand-built EnrichedPlanningRequest.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Evidence, Subject
from app.agents.context_discovery.agent import (
    ContextDiscoveryAgent,
    _confidence_for,
    build_context_discovery_result,
)
from app.agents.planning.classifier import analyse
from app.context_pipeline.models import (
    AdditionalContextRecommendation,
    EnrichedPlanningRequest,
    ProviderCapability,
    Reference,
    ReferenceType,
)


def _make_enriched(
    *,
    graph_available: bool = True,
    graph_has_data: bool = False,
    indexed_repositories: list | None = None,
    graph_components: list | None = None,
    graph_topics: list | None = None,
    resolved_references: list[Reference] | None = None,
    recommendation: AdditionalContextRecommendation | None = None,
) -> EnrichedPlanningRequest:
    request = "Add a rate limiter to the payment API"
    return EnrichedPlanningRequest(
        original_request=request,
        enriched_text=request,
        resolved_references=resolved_references or [],
        artifacts=[],
        profile=analyse(request),
        indexed_repositories=indexed_repositories or [],
        graph_components=graph_components or [],
        graph_topics=graph_topics or [],
        ranked_repository_names=[],
        graph_context_text="",
        graph_available=graph_available,
        graph_has_data=graph_has_data,
        additional_context_recommendation=recommendation,
        evidence=[
            Evidence(kind="tool_call", reference="neo4j_graph", summary="Queried the graph.")
        ],
        planning_metadata={"references_detected": 0},
    )


def _make_context(display_name: str = "Add a rate limiter to the payment API") -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name=display_name,
    )
    return AgentContext(
        subject=subject,
        goal="discover_context",
        extras={"db": AsyncMock(), "user_id": "user-1"},
    )


# ---------------------------------------------------------------------------
# build_context_discovery_result
# ---------------------------------------------------------------------------


def test_build_context_discovery_result_projects_core_fields() -> None:
    enriched = _make_enriched(
        graph_has_data=True,
        indexed_repositories=[{"id": "r1", "name": "payment-service", "owner": "acme"}],
        graph_components=[{"id": "c1", "name": "RateLimiter"}],
        graph_topics=[{"id": "t1", "name": "payment.throttled"}],
    )

    result = build_context_discovery_result(enriched)

    assert result.original_request == enriched.original_request
    assert result.enriched_text == enriched.enriched_text
    assert result.indexed_repositories == enriched.indexed_repositories
    assert result.graph_components == enriched.graph_components
    assert result.graph_topics == enriched.graph_topics
    assert result.graph_available is True
    assert result.graph_has_data is True
    assert result.prompt_version == "1.0"


def test_build_context_discovery_result_serializes_references() -> None:
    ref = Reference(
        type=ReferenceType.JIRA_ISSUE,
        provider="jira",
        confidence=0.95,
        raw_value="PROT-123",
        normalized_value="PROT-123",
    )
    enriched = _make_enriched(resolved_references=[ref])

    result = build_context_discovery_result(enriched)

    assert len(result.resolved_references) == 1
    serialized = result.resolved_references[0]
    # The StrEnum must be flattened to a plain string for JSON storage.
    assert serialized["type"] == "jira_issue"
    assert isinstance(serialized["type"], str)
    assert serialized["raw_value"] == "PROT-123"


def test_build_context_discovery_result_no_recommendation_is_none() -> None:
    enriched = _make_enriched(recommendation=None)
    result = build_context_discovery_result(enriched)
    assert result.additional_context_recommendation is None


def test_build_context_discovery_result_serializes_recommendation() -> None:
    recommendation = AdditionalContextRecommendation(
        should_search=True,
        capability=ProviderCapability.DOCUMENTATION,
        reasoning="The brief references a design doc that wasn't resolved.",
    )
    enriched = _make_enriched(recommendation=recommendation)

    result = build_context_discovery_result(enriched)

    assert result.additional_context_recommendation == {
        "should_search": True,
        "capability": "documentation",
        "reasoning": "The brief references a design doc that wasn't resolved.",
    }


# ---------------------------------------------------------------------------
# _confidence_for — three-tier formula
# ---------------------------------------------------------------------------


def test_confidence_graph_unavailable_is_low() -> None:
    enriched = _make_enriched(graph_available=False)
    confidence = _confidence_for(enriched)
    assert confidence.score == 0.25
    assert "unavailable" in confidence.reasoning.lower()


def test_confidence_graph_has_data_is_high() -> None:
    enriched = _make_enriched(
        graph_available=True,
        graph_has_data=True,
        indexed_repositories=[{"id": "r1", "name": "payment-service"}],
        graph_components=[{"id": "c1", "name": "RateLimiter"}],
        graph_topics=[],
    )
    confidence = _confidence_for(enriched)
    assert confidence.score == 0.85
    assert "1 component" in confidence.reasoning


def test_confidence_graph_healthy_but_empty_is_moderate() -> None:
    enriched = _make_enriched(graph_available=True, graph_has_data=False)
    confidence = _confidence_for(enriched)
    assert confidence.score == 0.40
    assert "healthy" in confidence.reasoning.lower()


# ---------------------------------------------------------------------------
# ContextDiscoveryAgent.run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_discovery_agent_happy_path_output_contract() -> None:
    context = _make_context()
    enriched = _make_enriched(
        graph_has_data=True,
        indexed_repositories=[{"id": "r1", "name": "payment-service", "owner": "acme"}],
        graph_components=[{"id": "c1", "name": "RateLimiter"}],
    )

    with patch(
        "app.agents.context_discovery.agent.ContextResolutionPipeline"
    ) as mock_pipeline_cls:
        mock_pipeline_cls.return_value.resolve = AsyncMock(return_value=enriched)
        agent = ContextDiscoveryAgent()
        output = await agent.run(context)

    assert output.agent_id == "context_discovery"
    assert output.subject_id == "freetext:abc123"
    assert 0.0 <= output.confidence.score <= 1.0
    assert isinstance(output.result, dict)
    assert output.result["original_request"] == enriched.original_request
    assert output.prompt_version == "1.0"


@pytest.mark.asyncio
async def test_context_discovery_agent_appends_summary_evidence_to_pipeline_evidence() -> None:
    """The pipeline's own evidence (graph/provider calls) must be
    preserved verbatim, with exactly one additional llm_reasoning summary
    entry appended — never replaced."""
    context = _make_context()
    enriched = _make_enriched()

    with patch(
        "app.agents.context_discovery.agent.ContextResolutionPipeline"
    ) as mock_pipeline_cls:
        mock_pipeline_cls.return_value.resolve = AsyncMock(return_value=enriched)
        agent = ContextDiscoveryAgent()
        output = await agent.run(context)

    assert len(output.evidence) == len(enriched.evidence) + 1
    assert output.evidence[:-1] == enriched.evidence
    summary_evidence = output.evidence[-1]
    assert summary_evidence.kind == "llm_reasoning"
    assert summary_evidence.reference == "context_discovery_summary"


@pytest.mark.asyncio
async def test_context_discovery_agent_passes_subject_and_user_to_pipeline() -> None:
    context = _make_context(display_name="Fix the null pointer crash in the export job")
    enriched = _make_enriched()

    with patch(
        "app.agents.context_discovery.agent.ContextResolutionPipeline"
    ) as mock_pipeline_cls:
        mock_resolve = AsyncMock(return_value=enriched)
        mock_pipeline_cls.return_value.resolve = mock_resolve
        agent = ContextDiscoveryAgent()
        await agent.run(context)

    mock_resolve.assert_awaited_once()
    call_kwargs = mock_resolve.await_args.kwargs
    assert call_kwargs["raw_request"] == "Fix the null pointer crash in the export job"
    assert call_kwargs["user_id"] == "user-1"
    assert call_kwargs["db"] is context.extras["db"]
