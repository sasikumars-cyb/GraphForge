"""Unit tests for the Review Agent Adapter (PW-3).

Covers:
- resolve_pr_subject: builds the correct Subject shape
- _extract_pr_uuid: valid/invalid subject_id parsing, never silently wrong
- _map_evidence: honest mapping from reasoning_log to Evidence (skip steps
  excluded, graph-read tools -> graph_traversal, others -> tool_call, LLM
  synthesis always last)
- ReviewAgentAdapter.run(): produces a correct AgentOutput, wrapping the
  existing InvestigationAgent without modifying it

All Neo4j/GitHub/LLM collaborators are mocked — no real infrastructure needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.review_adapter import (
    REVIEW_MANIFEST,
    ReviewAgentAdapter,
    _extract_pr_uuid,
    _map_evidence,
    resolve_pr_subject,
)
from app.ai.agent.investigation_agent import InvestigationResult
from app.ai.agent.models import Observation, ReasoningStep
from app.ai.schemas.analysis_result import AIAnalysisResult, ConfidenceScore
from app.core.exceptions import NotFoundError


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_review_manifest_fields() -> None:
    assert REVIEW_MANIFEST.agent_id == "review"
    assert REVIEW_MANIFEST.goals == frozenset({"review_pr"})
    assert REVIEW_MANIFEST.accepted_subject_types == frozenset({"pull_request"})


# ---------------------------------------------------------------------------
# resolve_pr_subject
# ---------------------------------------------------------------------------


def test_resolve_pr_subject_builds_expected_shape() -> None:
    pr_id = uuid.uuid4()
    subject = resolve_pr_subject(pr_id)

    assert subject.subject_id == f"pr:{pr_id}"
    assert subject.subject_type == "pull_request"
    assert subject.graph_node_ids == []
    assert subject.display_name == f"PR {pr_id}"


def test_resolve_pr_subject_uses_provided_display_name() -> None:
    pr_id = uuid.uuid4()
    subject = resolve_pr_subject(pr_id, display_name="Add JWT auth")
    assert subject.display_name == "Add JWT auth"


# ---------------------------------------------------------------------------
# _extract_pr_uuid
# ---------------------------------------------------------------------------


def test_extract_pr_uuid_valid() -> None:
    pr_id = uuid.uuid4()
    subject = Subject(subject_id=f"pr:{pr_id}", subject_type="pull_request", graph_node_ids=[], display_name="")
    assert _extract_pr_uuid(subject) == pr_id


def test_extract_pr_uuid_missing_prefix_raises() -> None:
    subject = Subject(subject_id="not-a-pr-ref", subject_type="pull_request", graph_node_ids=[], display_name="")
    with pytest.raises(NotFoundError, match="expects subject_id 'pr:<uuid>'"):
        _extract_pr_uuid(subject)


def test_extract_pr_uuid_malformed_uuid_raises() -> None:
    subject = Subject(subject_id="pr:not-a-valid-uuid", subject_type="pull_request", graph_node_ids=[], display_name="")
    with pytest.raises(NotFoundError, match="does not contain a valid UUID"):
        _extract_pr_uuid(subject)


# ---------------------------------------------------------------------------
# _map_evidence
# ---------------------------------------------------------------------------


def _make_result(reasoning_log: list[ReasoningStep], confidence_score: float = 0.8) -> InvestigationResult:
    analysis = AIAnalysisResult(
        executive_summary="A change was analyzed.",
        confidence=ConfidenceScore(score=confidence_score, reasoning="Grounded in real diff data."),
        prompt_version="1.0.0",
    )
    return InvestigationResult(analysis=analysis, reasoning_log=reasoning_log)


def test_map_evidence_skips_steps_with_no_tool_selected() -> None:
    """A skip decision is a recorded reasoning step, not fabricated evidence."""
    result = _make_result([
        ReasoningStep(step_number=1, goal="g", plan="p", tool_selected=None, observation=None, decision="skip"),
    ])
    evidence = _map_evidence(result)

    # Only the trailing llm_reasoning entry should be present — the skip
    # step contributed nothing.
    assert len(evidence) == 1
    assert evidence[0].kind == "llm_reasoning"


def test_map_evidence_graph_read_tools_map_to_graph_traversal() -> None:
    result = _make_result([
        ReasoningStep(
            step_number=1, goal="g", plan="p",
            tool_selected="read_dependency_graph",
            observation=Observation(tool_name="read_dependency_graph", summary="Found 3 dependents."),
            decision="proceed",
        ),
    ])
    evidence = _map_evidence(result)

    graph_entries = [e for e in evidence if e.kind == "graph_traversal"]
    assert len(graph_entries) == 1
    assert graph_entries[0].reference == "read_dependency_graph"
    assert "3 dependents" in graph_entries[0].summary


def test_map_evidence_other_tools_map_to_tool_call() -> None:
    result = _make_result([
        ReasoningStep(
            step_number=1, goal="g", plan="p",
            tool_selected="get_recent_file_authors",
            observation=Observation(tool_name="get_recent_file_authors", summary="Found 2 authors."),
            decision="proceed",
        ),
    ])
    evidence = _map_evidence(result)

    tool_entries = [e for e in evidence if e.kind == "tool_call"]
    assert len(tool_entries) == 1
    assert tool_entries[0].reference == "get_recent_file_authors"


def test_map_evidence_missing_observation_falls_back_to_generic_summary() -> None:
    result = _make_result([
        ReasoningStep(
            step_number=1, goal="g", plan="p",
            tool_selected="get_recent_file_authors",
            observation=None,
            decision="proceed",
        ),
    ])
    evidence = _map_evidence(result)
    tool_entries = [e for e in evidence if e.kind == "tool_call"]
    assert "no observation recorded" in tool_entries[0].summary


def test_map_evidence_llm_synthesis_always_appended_last() -> None:
    result = _make_result(
        [
            ReasoningStep(
                step_number=1, goal="g", plan="p",
                tool_selected="read_dependency_graph",
                observation=Observation(tool_name="read_dependency_graph", summary="ok"),
                decision="proceed",
            ),
        ],
        confidence_score=0.73,
    )
    evidence = _map_evidence(result)

    assert evidence[-1].kind == "llm_reasoning"
    assert evidence[-1].reference == "llm_synthesis"
    assert "0.73" in evidence[-1].summary


# ---------------------------------------------------------------------------
# ReviewAgentAdapter.run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_agent_adapter_run_builds_correct_agent_output() -> None:
    pr_id = uuid.uuid4()
    subject = resolve_pr_subject(pr_id)
    context = AgentContext(subject=subject, goal="review_pr", extras={"db": AsyncMock()})

    fake_result = _make_result(
        [
            ReasoningStep(
                step_number=1, goal="review", plan="check dependents",
                tool_selected="traverse_dependency_graph",
                observation=Observation(tool_name="traverse_dependency_graph", summary="2 downstream services."),
                decision="proceed",
            ),
        ],
        confidence_score=0.91,
    )

    mock_investigation_agent = AsyncMock()
    mock_investigation_agent.investigate = AsyncMock(return_value=fake_result)

    with (
        patch("app.agents.review_adapter.get_driver", return_value=MagicMock()),
        patch("app.agents.review_adapter.Neo4jGraphRepository", return_value=MagicMock()),
        patch("app.agents.review_adapter.Neo4jImpactGraphReader", return_value=MagicMock()),
        patch("app.agents.review_adapter.create_version_control_provider", return_value=MagicMock()),
        patch("app.agents.review_adapter.create_llm_provider", return_value=MagicMock()),
        patch("app.agents.review_adapter.InvestigationAgent", return_value=mock_investigation_agent),
    ):
        adapter = ReviewAgentAdapter()
        output = await adapter.run(context)

    mock_investigation_agent.investigate.assert_awaited_once_with(pr_id)

    assert output.agent_id == "review"
    assert output.subject_id == f"pr:{pr_id}"
    assert output.confidence.score == 0.91
    assert output.prompt_version == "1.0.0"
    assert output.output_ref == f"ai-analysis:{pr_id}"
    assert output.result["executive_summary"] == "A change was analyzed."

    evidence_kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in evidence_kinds
    assert "llm_reasoning" in evidence_kinds


@pytest.mark.asyncio
async def test_review_agent_adapter_run_raises_for_malformed_subject() -> None:
    """A non-PR subject must raise, never silently produce a wrong result."""
    subject = Subject(subject_id="freetext:abc", subject_type="freetext", graph_node_ids=[], display_name="")
    context = AgentContext(subject=subject, goal="review_pr", extras={"db": AsyncMock()})

    adapter = ReviewAgentAdapter()
    with pytest.raises(NotFoundError):
        await adapter.run(context)
