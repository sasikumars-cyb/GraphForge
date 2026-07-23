"""Unit tests for the Planning Agent (PW-4).

Covers:
- Happy path: produces AgentOutput with graph_traversal + tool_call Evidence
- No-indexed-repos path: still produces Evidence (empty results are still evidence)
- Tool failure path: agent continues, evidence records the failure
- LLM failure path: raises (per AGENT_FRAMEWORK.md error policy)
- Output schema: PlanningResult fields populate correctly

All graph and LLM calls are mocked — no real Neo4j or OpenAI needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.planning.agent import PlanningAgent, PlanningLLMError
from app.agents.planning.schemas import PlanningResult
from app.agents.planning.tools import (
    GetIndexedRepositoriesTool,
    PlanningObservation,
    TraverseArchitectureGraphTool,
    format_graph_context,
    to_evidence,
)
from app.graph.models import GraphNode


# ---------------------------------------------------------------------------
# Tool unit tests (no I/O)
# ---------------------------------------------------------------------------


def test_planning_observation_fields() -> None:
    obs = PlanningObservation(
        tool_name="test_tool",
        summary="Found 5 components.",
        data={"count": 5},
    )
    assert obs.succeeded is True
    assert obs.error == ""


def test_to_evidence_graph_traversal() -> None:
    obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Traversed 2 repos, 10 components.",
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert ev.reference == "traverse_architecture_graph"
    assert "10 components" in ev.summary


def test_to_evidence_tool_call() -> None:
    obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="Found 3 indexed repositories.",
    )
    ev = to_evidence(obs, "tool_call")
    assert ev.kind == "tool_call"


def test_to_evidence_failed_observation_never_reports_graph_traversal() -> None:
    """P0-1: A failed tool must never produce graph_traversal evidence."""
    obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Connection refused",
        succeeded=False,
        error="Connection refused",
    )
    ev = to_evidence(obs, "graph_traversal")
    # Must NOT be graph_traversal — the traversal didn't actually happen
    assert ev.kind == "tool_call"
    assert "FAILED" in ev.summary


def test_to_evidence_failed_observation_summary_includes_failure() -> None:
    """P0-1: Failed evidence must clearly indicate failure in summary."""
    obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="DB unavailable",
        succeeded=False,
        error="DB unavailable",
    )
    ev = to_evidence(obs, "tool_call")
    assert "FAILED" in ev.summary


def test_to_evidence_succeeded_observation_preserves_kind() -> None:
    """Successful observations get their requested kind unchanged."""
    obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Found 5 components.",
        succeeded=True,
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert "FAILED" not in ev.summary


def test_format_graph_context_no_repos() -> None:
    repos_obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="No repos.",
        data={"indexed_repositories": [], "total_tracked": 0},
    )
    traverse_obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="No repos to traverse.",
        data={"components": [], "kafka_topics": [], "repository_count": 0},
    )
    ctx = format_graph_context(repos_obs, traverse_obs)
    assert "No repositories" in ctx


def test_format_graph_context_with_data() -> None:
    repos_obs = PlanningObservation(
        tool_name="get_indexed_repositories",
        summary="2 repos.",
        data={
            "indexed_repositories": [
                {"id": "r1", "name": "order-service", "owner": "acme"},
                {"id": "r2", "name": "payment-service", "owner": "acme"},
            ],
            "total_tracked": 2,
        },
    )
    traverse_obs = PlanningObservation(
        tool_name="traverse_architecture_graph",
        summary="Found 3 components.",
        data={
            "components": [
                {"id": "c1", "name": "OrderController", "type": "Controller", "repository": "order-service", "file_path": ""},
                {"id": "c2", "name": "PaymentService", "type": "Service", "repository": "payment-service", "file_path": ""},
            ],
            "kafka_topics": [
                {"id": "t1", "name": "order.created", "repository": "order-service"},
            ],
            "repository_count": 2,
        },
    )
    ctx = format_graph_context(repos_obs, traverse_obs)
    assert "order-service" in ctx
    assert "order.created" in ctx


# ---------------------------------------------------------------------------
# GetIndexedRepositoriesTool unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_indexed_repos_with_indexed_repos() -> None:
    mock_db = AsyncMock()
    mock_repo1 = MagicMock()
    mock_repo1.id = "uuid-1"
    mock_repo1.name = "order-service"
    mock_repo1.owner = "acme"

    # Mock db.execute().scalars().all() returning [repo1]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo1]
    mock_db.execute.return_value = mock_result

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    tool = GetIndexedRepositoriesTool(db=mock_db, graph_repository=mock_graph_repo)
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.tool_name == "get_indexed_repositories"
    assert len(obs.data["indexed_repositories"]) == 1
    assert obs.data["indexed_repositories"][0]["name"] == "order-service"


@pytest.mark.asyncio
async def test_get_indexed_repos_none_indexed() -> None:
    mock_db = AsyncMock()
    mock_repo1 = MagicMock()
    mock_repo1.id = "uuid-1"
    mock_repo1.name = "order-service"
    mock_repo1.owner = "acme"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo1]
    mock_db.execute.return_value = mock_result

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    tool = GetIndexedRepositoriesTool(db=mock_db, graph_repository=mock_graph_repo)
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.data["indexed_repositories"] == []
    assert obs.data["total_tracked"] == 1


@pytest.mark.asyncio
async def test_get_indexed_repos_db_failure() -> None:
    mock_db = AsyncMock()
    mock_db.execute.side_effect = Exception("DB unavailable")

    mock_graph_repo = AsyncMock()

    tool = GetIndexedRepositoriesTool(db=mock_db, graph_repository=mock_graph_repo)
    obs = await tool.execute()

    assert obs.succeeded is False
    assert "DB unavailable" in obs.error


# ---------------------------------------------------------------------------
# TraverseArchitectureGraphTool unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traverse_empty_repos_returns_empty_observation() -> None:
    mock_graph_repo = AsyncMock()
    tool = TraverseArchitectureGraphTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([])

    assert obs.tool_name == "traverse_architecture_graph"
    assert obs.data["components"] == []
    assert obs.data["kafka_topics"] == []
    assert "No indexed" in obs.summary


@pytest.mark.asyncio
async def test_traverse_returns_components_and_topics() -> None:
    mock_graph_repo = AsyncMock()

    component_node = GraphNode(
        id="comp-1",
        labels=["Component", "Service"],
        properties={"name": "OrderService", "file_path": "src/OrderService.java"},
    )
    topic_node = GraphNode(
        id="topic-1",
        labels=["KafkaTopic"],
        properties={"name": "order.created"},
    )

    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: (
            [component_node] if label == "Component" else [topic_node]
        )
    )

    tool = TraverseArchitectureGraphTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "repo-1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is True
    assert len(obs.data["components"]) == 1
    assert obs.data["components"][0]["name"] == "OrderService"
    assert len(obs.data["kafka_topics"]) == 1
    assert obs.data["kafka_topics"][0]["name"] == "order.created"
    assert "1 component" in obs.summary
    assert "1 Kafka topic" in obs.summary


@pytest.mark.asyncio
async def test_traverse_all_repos_fail_marks_as_failed() -> None:
    """P0-1: If all repos fail during traversal, succeeded must be False."""
    mock_graph_repo = AsyncMock()
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Neo4j connection refused")
    )

    tool = TraverseArchitectureGraphTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "repo-1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is False
    assert obs.error != ""
    assert "failed" in obs.summary.lower()


# ---------------------------------------------------------------------------
# PlanningAgent integration tests (mocked LLM + graph)
# ---------------------------------------------------------------------------


def _make_planning_context(display_name: str = "Plan a new Kafka consumer for order events") -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name=display_name,
    )
    mock_db = AsyncMock()
    return AgentContext(
        subject=subject,
        goal="plan_freeform",
        extras={"db": mock_db},
    )


def _make_llm_response(steps: int = 3) -> str:
    return json.dumps({
        "executive_summary": "A plan to implement the feature.",
        "implementation_steps": [
            {
                "order": i + 1,
                "description": f"Step {i + 1}",
                "affected_component": "OrderService",
                "risk_note": "",
            }
            for i in range(steps)
        ],
        "affected_components": ["OrderService"],
        "kafka_topics_involved": ["order.created"],
        "risk_considerations": ["Risk 1"],
        "graph_context_used": True,
    })


@pytest.mark.asyncio
async def test_planning_agent_happy_path_has_graph_evidence() -> None:
    """Core requirement: output must include at least one graph_traversal or
    tool_call Evidence entry per the Definition of Done."""
    context = _make_planning_context()

    # Mock graph repo
    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    component_node = GraphNode(
        id="c1", labels=["Component", "Service"],
        properties={"name": "OrderService"},
    )
    topic_node = GraphNode(
        id="t1", labels=["KafkaTopic"],
        properties={"name": "order.created"},
    )
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: (
            [component_node] if label == "Component" else [topic_node]
        )
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_make_llm_response())),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Must have evidence
    assert len(output.evidence) > 0

    # Core requirement: at least one graph_traversal or tool_call entry
    evidence_kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in evidence_kinds or "tool_call" in evidence_kinds, (
        "Planning Agent output must include at least one 'graph_traversal' or 'tool_call' "
        "Evidence entry — pure 'llm_reasoning' is not sufficient per the Definition of Done."
    )

    # Result is populated
    assert output.result["executive_summary"]
    assert len(output.result["implementation_steps"]) == 3

    # Confidence is within bounds
    assert 0.0 <= output.confidence.score <= 1.0

    # agent_id correct
    assert output.agent_id == "planning"
    assert output.subject_id == "freetext:abc123"


@pytest.mark.asyncio
async def test_planning_agent_no_indexed_repos_still_produces_evidence() -> None:
    """Even with no indexed repos, the agent must produce Evidence entries —
    querying the graph and finding nothing is still a graph traversal."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps({
        "executive_summary": "Plan based on general engineering practices.",
        "implementation_steps": [{"order": 1, "description": "Step 1", "affected_component": "", "risk_note": ""}],
        "affected_components": [],
        "kafka_topics_involved": [],
        "risk_considerations": [],
        "graph_context_used": False,
    })

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Evidence must still exist (even empty results produce evidence)
    assert len(output.evidence) > 0
    evidence_kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in evidence_kinds or "tool_call" in evidence_kinds

    # Lower confidence when no graph data
    assert output.confidence.score < 0.7


@pytest.mark.asyncio
async def test_planning_agent_llm_failure_raises() -> None:
    """Per AGENT_FRAMEWORK.md error policy: LLM failure raises, never
    returns a plausible-looking default."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(side_effect=PlanningLLMError("API key missing."))),
    ):
        agent = PlanningAgent()
        with pytest.raises(PlanningLLMError):
            await agent.run(context)


@pytest.mark.asyncio
async def test_planning_agent_output_matches_agent_output_contract() -> None:
    """Verify AgentOutput envelope fields match _contract.py schema."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)
    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=json.dumps({
                "executive_summary": "Plan.",
                "implementation_steps": [],
                "affected_components": [],
                "kafka_topics_involved": [],
                "risk_considerations": [],
                "graph_context_used": False,
            })),
        ),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Check all AgentOutput envelope fields
    assert output.agent_id == "planning"
    assert output.subject_id == "freetext:abc123"
    assert isinstance(output.confidence.score, float)
    assert isinstance(output.confidence.reasoning, str)
    assert isinstance(output.evidence, list)
    assert isinstance(output.result, dict)
    assert isinstance(output.graph_facts_written, list)
    assert isinstance(output.prompt_version, str)


# ---------------------------------------------------------------------------
# P0-1: Evidence integrity — failed tools must not produce false evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_agent_graph_unavailable_no_false_evidence() -> None:
    """P0-1: When the graph is unreachable (tool fails), the agent must
    not produce graph_traversal evidence that implies a successful query."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    # get_nodes_by_label raises — Neo4j is down
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Neo4j connection refused")
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps({
        "executive_summary": "Plan without graph.",
        "implementation_steps": [{"order": 1, "description": "Step 1"}],
        "affected_components": [],
        "kafka_topics_involved": [],
        "risk_considerations": [],
        "graph_context_used": False,
    })

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # No evidence entry should claim kind="graph_traversal" when the
    # traversal actually failed
    for ev in output.evidence:
        if ev.kind == "graph_traversal":
            assert False, (
                f"Found graph_traversal evidence '{ev.summary}' but the "
                "graph was unavailable — this is fabricated evidence."
            )

    # At least one evidence entry must indicate the failure
    failed_entries = [e for e in output.evidence if "FAILED" in e.summary]
    assert len(failed_entries) > 0


# ---------------------------------------------------------------------------
# Work Item 3: graph_context_used must be derived from execution, never
# trusted from the LLM's self-report.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_agent_graph_context_used_overridden_when_graph_fails() -> None:
    """The LLM claims graph_context_used=True, but the graph traversal
    actually failed — the persisted result must not repeat that claim."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Neo4j connection refused")
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    # LLM lies and claims it used graph context even though every
    # traversal call failed.
    llm_response = json.dumps({
        "executive_summary": "Plan.",
        "implementation_steps": [],
        "affected_components": [],
        "kafka_topics_involved": [],
        "risk_considerations": [],
        "graph_context_used": True,
    })

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    assert output.result["graph_context_used"] is False, (
        "graph_context_used must reflect actual tool execution, not the "
        "LLM's self-report — the graph traversal failed here."
    )


@pytest.mark.asyncio
async def test_planning_agent_graph_context_used_true_when_graph_has_data() -> None:
    """When graph traversal genuinely succeeds and returns data,
    graph_context_used must be True regardless of what the LLM reports."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    component_node = GraphNode(
        id="c1", labels=["Component", "Service"], properties={"name": "OrderService"},
    )
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: [component_node] if label == "Component" else []
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    # LLM under-reports — the code's own signal should win.
    llm_response = json.dumps({
        "executive_summary": "Plan.",
        "implementation_steps": [],
        "affected_components": [],
        "kafka_topics_involved": [],
        "risk_considerations": [],
        "graph_context_used": False,
    })

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    assert output.result["graph_context_used"] is True


# ---------------------------------------------------------------------------
# P1-3: Graph unavailable vs graph empty — confidence must distinguish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_agent_graph_unavailable_confidence_reasoning() -> None:
    """P1-3: When graph is unavailable, confidence reasoning must mention
    infrastructure error, not just 'no data'."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Connection refused")
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps({
        "executive_summary": "Plan.",
        "implementation_steps": [],
        "affected_components": [],
        "kafka_topics_involved": [],
        "risk_considerations": [],
        "graph_context_used": False,
    })

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Confidence must be lower than the healthy-empty case (0.45)
    assert output.confidence.score <= 0.35, (
        f"Graph unavailable should give very low confidence, got {output.confidence.score}"
    )
    assert "unavailable" in output.confidence.reasoning.lower()


@pytest.mark.asyncio
async def test_planning_agent_graph_empty_confidence_reasoning() -> None:
    """P1-3: When graph is healthy but empty, confidence reasoning must say
    'healthy' not 'unavailable'."""
    context = _make_planning_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    llm_response = json.dumps({
        "executive_summary": "Plan.",
        "implementation_steps": [],
        "affected_components": [],
        "kafka_topics_involved": [],
        "risk_considerations": [],
        "graph_context_used": False,
    })

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)),
    ):
        agent = PlanningAgent()
        output = await agent.run(context)

    # Should be healthy-empty confidence (0.45 + possible step bump)
    assert 0.40 <= output.confidence.score <= 0.55, (
        f"Healthy-empty graph should give moderate confidence, got {output.confidence.score}"
    )
    assert "healthy" in output.confidence.reasoning.lower()
    assert "unavailable" not in output.confidence.reasoning.lower()
