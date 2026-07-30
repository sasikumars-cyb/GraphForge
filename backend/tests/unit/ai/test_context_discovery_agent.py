"""Unit tests for the Context Discovery Agent (goal=discover_context).

The agent itself is a thin adapter over the reasoning engine, so these tests
cover exactly that seam:

- the AgentOutput envelope (confidence from evidence-derived assessments,
  contract-shaped evidence projected from the ledger)
- the fresh-vs-resume dispatch
- pausing with a pending question, and *not* pausing when there's nothing
  answerable
- the persisted result carrying the full working memory needed to resume

The engine's own behavior is tested in test_context_reasoning_engine.py; here
`discover`/`resume` are patched so the adapter can be tested in isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.context_discovery.agent import ContextDiscoveryAgent, _confidence_for
from app.context_pipeline.reasoning.memory import (
    ClarificationQuestion,
    KnowledgeGap,
    WorkingContext,
)
from app.context_pipeline.reasoning.projection import build_result


def _state(*, request: str = "Add a rate limiter to the payment API") -> WorkingContext:
    """A WorkingContext with a real, satisfied ledger — built through the
    ledger's own API so every fact carries genuine evidence (the ledger
    rejects orphans, which is what makes this realistic rather than a stub)."""
    state = WorkingContext()
    state.metadata.goal = request
    state.derived["original_request"] = request
    state.derived["enriched_text"] = request

    state.ledger.add_evidence(
        provider="graph",
        action="survey_architecture",
        outcome="success",
        summary="Looked up indexed repositories: 1 found.",
    )
    # Recorded under the traversal action because that is what the
    # `architecture` capability reads for reachability — the repository lookup
    # above hits Postgres and proves nothing about the graph.
    graph_ev = state.ledger.add_evidence(
        provider="graph",
        action="traverse_architecture_graph",
        outcome="success",
        summary="Traversed the architecture graph: 1 component(s), 1 topic(s).",
    )
    repo = state.ledger.add_fact(
        kind="repository",
        subject="payment-service",
        provider="graph",
        evidence_id=graph_ev.evidence_id,
        value={"name": "payment-service"},
    )
    state.ledger.add_fact(
        kind="component",
        subject="RateLimiter",
        provider="graph",
        evidence_id=graph_ev.evidence_id,
        value={"name": "RateLimiter", "repository": "payment-service"},
    )
    state.ledger.add_fact(
        kind="topic",
        subject="payment.throttled",
        provider="graph",
        evidence_id=graph_ev.evidence_id,
        value={"name": "payment.throttled", "repository": "payment-service"},
    )
    state.ledger.add_inference(
        kind="repository_candidate",
        statement="payment-service",
        supporting_fact_ids=[repo.fact_id],
    )
    state.refresh_assessments()
    return state


def _blocked_state() -> WorkingContext:
    """A state that is genuinely blocked with an answerable question."""
    state = WorkingContext()
    state.metadata.goal = "Fix the retry logic"
    state.metadata.providers_exhausted = True
    ev = state.ledger.add_evidence(
        provider="graph", action="survey_architecture", outcome="success", summary="0 repositories."
    )
    state.ledger.add_fact(
        kind="reference",
        subject="payment-service",
        provider="request_parser",
        evidence_id=ev.evidence_id,
        value={"type": "local_repository"},
    )
    state.gaps.append(
        KnowledgeGap(
            gap_id="gap_repository",
            capability="repository",
            summary="The repository this work belongs to could not be determined.",
            why="Planning is scoped to one service.",
            severity="blocking",
            question=ClarificationQuestion(
                question_id="gap_repository",
                question="Which repository should I use for this work?",
                why="Nothing in the request matched an indexed repository.",
                options=["payment-service", "billing-service"],
            ),
        )
    )
    state.refresh_assessments()
    return state


def _context(resume: dict | None = None) -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name="Add a rate limiter to the payment API",
    )
    extras: dict = {"db": AsyncMock(), "user_id": None}
    if resume is not None:
        extras["resume"] = resume
    return AgentContext(subject=subject, goal="discover_context", extras=extras)


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_confidence_comes_from_evidence_derived_assessments() -> None:
    state = _state()
    confidence = _confidence_for(state)
    assert confidence.score == state.confidence
    assert "READY" in confidence.reasoning
    # The reasoning string must carry the per-capability breakdown, so a score
    # is never shown without the decomposition that produced it.
    assert "Repository" in confidence.reasoning
    assert "Architecture" in confidence.reasoning


def test_confidence_reasoning_names_what_is_missing_when_unsatisfied() -> None:
    confidence = _confidence_for(_blocked_state())
    assert "Missing" in confidence.reasoning
    assert "Repository" in confidence.reasoning


def test_confidence_excludes_inapplicable_capabilities() -> None:
    """A request with no ticket must not be scored on `work_item` at all —
    scoring an unexamined capability 1.0 by default is what inflated the old
    design's overall confidence."""
    state = _state()
    work_item = state.assessment_for("work_item")
    assert work_item is not None
    assert work_item.necessity == "not_applicable"
    assert "Work item" not in _confidence_for(state).reasoning


# ---------------------------------------------------------------------------
# run() — fresh discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_fresh_output_contract() -> None:
    state = _state()
    with patch(
        "app.agents.context_discovery.agent.discover", new=AsyncMock(return_value=state)
    ) as mock_discover:
        output = await ContextDiscoveryAgent().run(_context())

    mock_discover.assert_awaited_once()
    assert output.agent_id == "context_discovery"
    assert output.subject_id == "freetext:abc123"
    assert output.confidence.score == state.confidence
    assert output.awaiting_input is False
    assert output.pending_question is None
    assert output.result["readiness"] == "READY"
    assert output.prompt_version == "4.0"


@pytest.mark.asyncio
async def test_run_projects_ledger_into_contract_evidence() -> None:
    """The agent contract requires at least one non-LLM evidence entry; graph
    retrievals must project as graph_traversal so that holds from real work."""
    state = _state()
    with patch("app.agents.context_discovery.agent.discover", new=AsyncMock(return_value=state)):
        output = await ContextDiscoveryAgent().run(_context())

    assert output.evidence
    assert any(e.kind == "graph_traversal" for e in output.evidence)
    assert all(e.status is not None for e in output.evidence)


@pytest.mark.asyncio
async def test_run_pauses_with_pending_question_when_blocked() -> None:
    state = _blocked_state()
    with patch("app.agents.context_discovery.agent.discover", new=AsyncMock(return_value=state)):
        output = await ContextDiscoveryAgent().run(_context())

    assert output.awaiting_input is True
    assert output.pending_question is not None
    assert output.pending_question["question_id"] == "gap_repository"
    assert output.result["readiness"] == "BLOCKED"
    # Options must be real repository values, never UI instructions.
    assert "Select a repository" not in output.pending_question["options"]


@pytest.mark.asyncio
async def test_run_does_not_pause_when_blocked_without_an_answerable_question() -> None:
    """BLOCKED with nothing a human could answer must complete, not pause —
    otherwise the workflow waits forever on a question that was never asked."""
    state = _blocked_state()
    state.gaps[0].question = None
    with patch("app.agents.context_discovery.agent.discover", new=AsyncMock(return_value=state)):
        output = await ContextDiscoveryAgent().run(_context())

    assert output.awaiting_input is False
    assert output.pending_question is None
    assert output.result["readiness"] == "BLOCKED"


@pytest.mark.asyncio
async def test_run_result_carries_resumable_working_memory() -> None:
    state = _blocked_state()
    with patch("app.agents.context_discovery.agent.discover", new=AsyncMock(return_value=state)):
        output = await ContextDiscoveryAgent().run(_context())

    memory = output.result["working_memory"]
    assert memory["ledger"]["facts"], "facts must survive into the persisted state"
    assert memory["gaps"][0]["gap_id"] == "gap_repository"


# ---------------------------------------------------------------------------
# run() — resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_resume_dispatches_to_resume_with_restored_memory() -> None:
    paused = build_result(_blocked_state())
    resumed = _state()

    with (
        patch(
            "app.agents.context_discovery.agent.resume", new=AsyncMock(return_value=resumed)
        ) as mock_resume,
        patch("app.agents.context_discovery.agent.discover", new=AsyncMock()) as mock_discover,
    ):
        output = await ContextDiscoveryAgent().run(
            _context(
                resume={
                    "working_context": paused,
                    "answer": {"question_id": "gap_repository", "answer": "payment-service"},
                }
            )
        )

    mock_resume.assert_awaited_once()
    mock_discover.assert_not_called()
    kwargs = mock_resume.await_args.kwargs
    assert kwargs["question_id"] == "gap_repository"
    assert kwargs["answer"] == "payment-service"
    # The restored state must be the real persisted memory, not a rebuild from
    # the flat projection.
    assert kwargs["state"].gaps[0].gap_id == "gap_repository"
    assert output.result["readiness"] == "READY"
    assert output.awaiting_input is False


@pytest.mark.asyncio
async def test_resume_without_persisted_memory_fails_loudly() -> None:
    """A run persisted before reasoning-driven discovery has no working memory.
    Resuming must fail rather than silently starting from nothing, which would
    quietly discard everything the paused run knew."""
    with (
        patch("app.agents.context_discovery.agent.resume", new=AsyncMock()),
        pytest.raises(ValueError, match="no persisted working memory"),
    ):
        await ContextDiscoveryAgent().run(
            _context(
                resume={
                    "working_context": {"readiness": "BLOCKED"},
                    "answer": {"question_id": "gap_repository", "answer": "x"},
                }
            )
        )
