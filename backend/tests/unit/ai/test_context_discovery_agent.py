"""Unit tests for the Context Discovery Agent (goal=discover_context).

Covers:
- build_context_discovery_result: flat projection from a nested
  WorkingContext (Planning's read shape must stay unchanged)
- _working_context_from_result: the resume-path inverse
- _confidence_for: derives its single score from capability-specific
  confidence (overall()), never the other way around
- ContextDiscoveryAgent.run(): AgentOutput envelope, evidence, and the
  fresh-vs-resume dispatch to run_discovery_loop/resume_discovery

No real Neo4j/LLM/DB — `run_discovery_loop`/`resume_discovery` are mocked
to return a hand-built `DiscoveryLoopResult`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Evidence, Subject
from app.agents.context_discovery.agent import (
    ContextDiscoveryAgent,
    _confidence_for,
    _working_context_from_result,
    build_context_discovery_result,
)
from app.context_pipeline.reasoning_loop import DiscoveryLoopResult
from app.context_pipeline.working_context import (
    BlockingIssue,
    CapabilityConfidence,
    ClarificationQuestion,
    Compatibility,
    ContextMetadata,
    GraphKnowledge,
    Knowledge,
    Reasoning,
    WorkingContext,
)


def _make_working_context(
    *,
    confidence: CapabilityConfidence | None = None,
    blocking_issues: list[BlockingIssue] | None = None,
) -> WorkingContext:
    return WorkingContext(
        metadata=ContextMetadata(
            goal="Add a rate limiter to the payment API",
            iteration=1,
        ),
        knowledge=Knowledge(
            entities=[],
            repositories=[{"id": "r1", "name": "payment-service", "owner": "acme"}],
            architecture={
                "components": [{"id": "c1", "name": "RateLimiter"}],
                "topics": [{"id": "t1", "name": "payment.throttled"}],
            },
            implementation_candidates=["payment-service"],
            graph=GraphKnowledge(available=True, has_data=True),
        ),
        reasoning=Reasoning(
            confidence=confidence
            or CapabilityConfidence(
                work_item=1.0,
                repository=0.9,
                architecture=0.85,
                implementation_candidates=0.8,
                documentation=0.85,
            ),
            blocking_issues=blocking_issues or [],
        ),
        compatibility=Compatibility(
            original_request="Add a rate limiter to the payment API",
            enriched_text="Add a rate limiter to the payment API",
        ),
    )


def _blocking_issue(question_id: str = "repo_not_found") -> BlockingIssue:
    return BlockingIssue(
        issue_id=question_id,
        type="repository_not_found",
        severity="blocking",
        message="No repository matched.",
        reason="No repository matched.",
        clarification_question=ClarificationQuestion(
            question_id=question_id, question="Which repository?", why="No match found."
        ),
    )


def _make_context(
    display_name: str = "Add a rate limiter to the payment API",
    resume: dict | None = None,
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name=display_name,
    )
    extras = {"db": AsyncMock(), "user_id": "user-1"}
    if resume is not None:
        extras["resume"] = resume
    return AgentContext(subject=subject, goal="discover_context", extras=extras)


# ---------------------------------------------------------------------------
# build_context_discovery_result / _working_context_from_result round-trip
# ---------------------------------------------------------------------------


def test_build_context_discovery_result_projects_core_fields() -> None:
    wc = _make_working_context()
    wc.reasoning.readiness = "READY"
    result = build_context_discovery_result(wc)

    assert result.original_request == wc.compatibility.original_request
    assert result.enriched_text == wc.compatibility.enriched_text
    assert result.indexed_repositories == wc.knowledge.repositories
    assert result.graph_components == wc.knowledge.architecture["components"]
    assert result.graph_topics == wc.knowledge.architecture["topics"]
    assert result.graph_available is True
    assert result.graph_has_data is True
    assert result.readiness == "READY"
    assert result.confidence == wc.reasoning.confidence.overall()
    assert result.prompt_version == "3.0"
    assert result.capability_confidence["repository"] == 0.9
    assert result.discovery_summary["readiness"] == "READY"


def test_build_context_discovery_result_serializes_unresolved_questions() -> None:
    wc = _make_working_context(blocking_issues=[_blocking_issue("repo_ambiguous")])
    wc.reasoning.readiness = "BLOCKED"
    result = build_context_discovery_result(wc)

    assert len(result.unresolved_questions) == 1
    q = result.unresolved_questions[0]
    assert q["question_id"] == "repo_ambiguous"
    assert q["blocking"] is True
    assert result.blocking_reasons == ["No repository matched."]
    assert len(result.blocking_issues) == 1


def test_build_context_discovery_result_excludes_resolved_issues_from_unresolved() -> None:
    issue = _blocking_issue("repo_not_found")
    wc = _make_working_context(blocking_issues=[issue])
    wc.reasoning.resolve_issue("repo_not_found", "payment-service")
    result = build_context_discovery_result(wc)

    assert result.unresolved_questions == []
    assert result.blocking_reasons == []
    # Still present in the full structured list for the Discovery Summary /
    # audit trail — resolved, not deleted.
    assert len(result.blocking_issues) == 1
    assert result.blocking_issues[0]["resolved"] is True


def test_working_context_from_result_round_trips_persisted_shape() -> None:
    wc = _make_working_context(blocking_issues=[_blocking_issue("repo_ambiguous")])
    wc.reasoning.readiness = "BLOCKED"
    persisted = build_context_discovery_result(wc).model_dump()

    reconstructed = _working_context_from_result(persisted)

    assert reconstructed.compatibility.original_request == wc.compatibility.original_request
    assert reconstructed.knowledge.repositories == wc.knowledge.repositories
    assert reconstructed.knowledge.architecture == wc.knowledge.architecture
    assert reconstructed.reasoning.readiness == "BLOCKED"
    assert len(reconstructed.reasoning.blocking_issues) == 1
    assert reconstructed.reasoning.blocking_issues[0].issue_id == "repo_ambiguous"
    assert reconstructed.reasoning.confidence.repository == 0.9


# ---------------------------------------------------------------------------
# _confidence_for
# ---------------------------------------------------------------------------


def test_confidence_for_reads_capability_overall() -> None:
    wc = _make_working_context()
    wc.reasoning.readiness = "READY"
    confidence = _confidence_for(wc)
    assert confidence.score == wc.reasoning.confidence.overall()
    assert "READY" in confidence.reasoning


def test_confidence_for_reports_blocked_readiness_and_open_issues() -> None:
    wc = _make_working_context(blocking_issues=[_blocking_issue("q1")])
    wc.reasoning.readiness = "BLOCKED"
    confidence = _confidence_for(wc)
    assert confidence.score == wc.reasoning.confidence.overall()
    assert "BLOCKED" in confidence.reasoning
    assert "1 open issue" in confidence.reasoning


# ---------------------------------------------------------------------------
# ContextDiscoveryAgent.run() — fresh discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_run_fresh_happy_path_output_contract() -> None:
    context = _make_context()
    wc = _make_working_context()
    wc.reasoning.readiness = "READY"
    loop_result = DiscoveryLoopResult(
        working_context=wc,
        evidence=[Evidence(kind="tool_call", reference="neo4j_graph", summary="Queried graph.")],
    )

    with patch(
        "app.agents.context_discovery.agent.run_discovery_loop",
        new=AsyncMock(return_value=loop_result),
    ) as mock_loop:
        agent = ContextDiscoveryAgent()
        output = await agent.run(context)

    mock_loop.assert_awaited_once()
    assert output.agent_id == "context_discovery"
    assert output.subject_id == "freetext:abc123"
    assert output.confidence.score == wc.reasoning.confidence.overall()
    assert output.awaiting_input is False
    assert output.pending_question is None
    assert output.result["readiness"] == "READY"


@pytest.mark.asyncio
async def test_agent_run_pauses_and_sets_pending_question() -> None:
    context = _make_context()
    wc = _make_working_context(blocking_issues=[_blocking_issue("repo_not_found")])
    wc.reasoning.readiness = "BLOCKED"
    loop_result = DiscoveryLoopResult(working_context=wc, evidence=[])

    with patch(
        "app.agents.context_discovery.agent.run_discovery_loop",
        new=AsyncMock(return_value=loop_result),
    ):
        agent = ContextDiscoveryAgent()
        output = await agent.run(context)

    assert output.awaiting_input is True
    assert output.pending_question is not None
    assert output.pending_question["question_id"] == "repo_not_found"


@pytest.mark.asyncio
async def test_agent_run_does_not_pause_once_exhausted() -> None:
    """A WorkingContext that's BLOCKED-but-exhausted (round cap reached)
    must complete, not pause again — otherwise the loop would keep asking
    past the cap it just enforced."""
    context = _make_context()
    wc = _make_working_context(blocking_issues=[_blocking_issue("repo_not_found")])
    wc.reasoning.readiness = "BLOCKED"
    wc.reasoning.exhausted = True
    loop_result = DiscoveryLoopResult(working_context=wc, evidence=[])

    with patch(
        "app.agents.context_discovery.agent.run_discovery_loop",
        new=AsyncMock(return_value=loop_result),
    ):
        agent = ContextDiscoveryAgent()
        output = await agent.run(context)

    assert output.awaiting_input is False
    assert output.pending_question is None
    assert output.result["readiness"] == "BLOCKED"


@pytest.mark.asyncio
async def test_agent_run_appends_summary_evidence() -> None:
    context = _make_context()
    wc = _make_working_context()
    wc.reasoning.readiness = "READY"
    base_evidence = [Evidence(kind="tool_call", reference="neo4j_graph", summary="Queried graph.")]
    loop_result = DiscoveryLoopResult(working_context=wc, evidence=list(base_evidence))

    with patch(
        "app.agents.context_discovery.agent.run_discovery_loop",
        new=AsyncMock(return_value=loop_result),
    ):
        agent = ContextDiscoveryAgent()
        output = await agent.run(context)

    assert len(output.evidence) == len(base_evidence) + 1
    assert output.evidence[:-1] == base_evidence
    assert output.evidence[-1].kind == "llm_reasoning"
    assert output.evidence[-1].reference == "context_discovery_summary"


# ---------------------------------------------------------------------------
# ContextDiscoveryAgent.run() — resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_run_resume_dispatches_to_resume_discovery() -> None:
    seed = _make_working_context(blocking_issues=[_blocking_issue("repo_not_found")])
    seed.reasoning.readiness = "BLOCKED"
    persisted = build_context_discovery_result(seed).model_dump()
    context = _make_context(
        resume={
            "working_context": persisted,
            "answer": {"question_id": "repo_not_found", "answer": "payment-service"},
        }
    )
    resumed_wc = _make_working_context()
    resumed_wc.reasoning.readiness = "READY"
    loop_result = DiscoveryLoopResult(working_context=resumed_wc, evidence=[])

    with (
        patch(
            "app.agents.context_discovery.agent.resume_discovery",
            new=AsyncMock(return_value=loop_result),
        ) as mock_resume,
        patch(
            "app.agents.context_discovery.agent.run_discovery_loop",
            new=AsyncMock(),
        ) as mock_fresh,
    ):
        agent = ContextDiscoveryAgent()
        output = await agent.run(context)

    mock_resume.assert_awaited_once()
    mock_fresh.assert_not_called()
    call_kwargs = mock_resume.await_args.kwargs
    assert call_kwargs["question_id"] == "repo_not_found"
    assert call_kwargs["answer"] == "payment-service"
    assert call_kwargs["db"] is context.extras["db"]
    assert output.result["readiness"] == "READY"
    assert output.awaiting_input is False
