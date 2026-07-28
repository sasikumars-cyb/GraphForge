"""Unit tests for app/agents/_contract.py — the frozen Agent Contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents._contract import (
    AgentContext,
    AgentManifest,
    AgentOutput,
    Confidence,
    Evidence,
    Subject,
)

# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------


def test_subject_minimal() -> None:
    s = Subject(subject_id="pr:abc123", subject_type="pull_request")
    assert s.subject_id == "pr:abc123"
    assert s.graph_node_ids == []
    assert s.display_name == ""


def test_subject_with_nodes() -> None:
    s = Subject(
        subject_id="freetext:xyz",
        subject_type="freetext",
        graph_node_ids=["node-1", "node-2"],
        display_name="Plan a new feature",
    )
    assert len(s.graph_node_ids) == 2


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_graph_traversal() -> None:
    e = Evidence(
        kind="graph_traversal",
        reference="traverse_architecture_graph",
        summary="Found 5 components.",
    )
    assert e.kind == "graph_traversal"


def test_evidence_tool_call() -> None:
    e = Evidence(kind="tool_call", reference="get_indexed_repositories", summary="Found 3 repos.")
    assert e.kind == "tool_call"


def test_evidence_llm_reasoning() -> None:
    e = Evidence(kind="llm_reasoning", reference="llm_synthesis", summary="Synthesized output.")
    assert e.kind == "llm_reasoning"


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_confidence_within_bounds() -> None:
    c = Confidence(score=0.88, reasoning="Good evidence.")
    assert 0.0 <= c.score <= 1.0


def test_confidence_score_below_zero_raises() -> None:
    with pytest.raises(ValidationError):
        Confidence(score=-0.1)


def test_confidence_score_above_one_raises() -> None:
    with pytest.raises(ValidationError):
        Confidence(score=1.1)


# ---------------------------------------------------------------------------
# AgentOutput
# ---------------------------------------------------------------------------


def test_agent_output_defaults() -> None:
    output = AgentOutput(
        agent_id="planning",
        subject_id="freetext:abc",
        confidence=Confidence(score=0.7),
        evidence=[],
    )
    assert output.graph_facts_written == []
    assert output.prompt_version == "1.0"
    assert output.output_ref is None


def test_agent_output_with_evidence() -> None:
    output = AgentOutput(
        agent_id="planning",
        subject_id="freetext:abc",
        confidence=Confidence(score=0.85, reasoning="Graph data found."),
        evidence=[
            Evidence(
                kind="graph_traversal",
                reference="traverse_architecture_graph",
                summary="3 components.",
            ),
            Evidence(kind="tool_call", reference="get_indexed_repositories", summary="2 repos."),
        ],
        result={"executive_summary": "Plan here"},
    )
    assert len(output.evidence) == 2
    kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in kinds
    assert "tool_call" in kinds


# ---------------------------------------------------------------------------
# AgentManifest
# ---------------------------------------------------------------------------


def test_manifest_requires_agent_id() -> None:
    with pytest.raises(ValueError):
        AgentManifest(
            agent_id="",
            purpose="Test",
            goals=frozenset({"test_goal"}),
            accepted_subject_types=frozenset({"freetext"}),
            cost_class="cheap",
        )


def test_manifest_requires_goals() -> None:
    with pytest.raises(ValueError):
        AgentManifest(
            agent_id="test",
            purpose="Test",
            goals=frozenset(),
            accepted_subject_types=frozenset({"freetext"}),
            cost_class="cheap",
        )


def test_manifest_valid() -> None:
    m = AgentManifest(
        agent_id="planning",
        purpose="Plan things.",
        goals=frozenset({"plan_freeform"}),
        accepted_subject_types=frozenset({"freetext"}),
        cost_class="standard",
        max_graph_hops=2,
    )
    assert m.agent_id == "planning"
    assert "plan_freeform" in m.goals


# ---------------------------------------------------------------------------
# AgentContext
# ---------------------------------------------------------------------------


def test_agent_context_extras() -> None:
    subject = Subject(subject_id="freetext:abc", subject_type="freetext")
    ctx = AgentContext(subject=subject, goal="plan_freeform", extras={"db": "mock_db"})
    assert ctx.extras["db"] == "mock_db"
