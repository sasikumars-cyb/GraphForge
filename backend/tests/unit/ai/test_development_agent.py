"""Unit tests for the Development Agent — Change Planning capability.

Covers:
- Tool unit tests (observation, evidence, formatting)
- RepositoryDiscoveryTool: indexed repos, none indexed, DB failure
- ComponentDiscoveryTool: empty repos, components found, all repos fail
- DependencyTraversalTool: empty repos, edges found, cross-repo coupling
- DevelopmentAgent integration: happy path, no graph data, graph unavailable, LLM failure
- Output schema: DevelopmentPlan fields populate correctly
- Agent registration: manifest declared, selector routes goal

All graph and LLM calls are mocked — no real Neo4j or OpenAI needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.development.agent import DevelopmentAgent, DevelopmentLLMError
from app.agents.development.manifest import DEVELOPMENT_MANIFEST
from app.agents.development.schemas import DevelopmentPlan
from app.agents.development.tools import (
    ComponentDiscoveryTool,
    DependencyTraversalTool,
    DevelopmentObservation,
    RepositoryDiscoveryTool,
    format_graph_context,
    to_evidence,
)
from app.graph.models import GraphEdge, GraphNode, GraphPayload

# ---------------------------------------------------------------------------
# DevelopmentObservation unit tests
# ---------------------------------------------------------------------------


def test_development_observation_fields() -> None:
    obs = DevelopmentObservation(
        tool_name="test_tool",
        summary="Found 5 components.",
        data={"count": 5},
    )
    assert obs.succeeded is True
    assert obs.error == ""


def test_development_observation_failure() -> None:
    obs = DevelopmentObservation(
        tool_name="test_tool",
        summary="Connection refused",
        succeeded=False,
        error="Connection refused",
    )
    assert obs.succeeded is False
    assert obs.error == "Connection refused"


# ---------------------------------------------------------------------------
# Evidence builder tests
# ---------------------------------------------------------------------------


def test_to_evidence_graph_traversal() -> None:
    obs = DevelopmentObservation(
        tool_name="discover_components",
        summary="Discovered 10 components across 2 repos.",
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert ev.reference == "discover_components"
    assert "10 components" in ev.summary


def test_to_evidence_tool_call() -> None:
    obs = DevelopmentObservation(
        tool_name="discover_repositories",
        summary="Found 3 indexed repositories.",
    )
    ev = to_evidence(obs, "tool_call")
    assert ev.kind == "tool_call"
    assert ev.reference == "discover_repositories"


def test_to_evidence_failed_never_reports_graph_traversal() -> None:
    """A failed tool must never produce graph_traversal evidence."""
    obs = DevelopmentObservation(
        tool_name="discover_components",
        summary="Connection refused",
        succeeded=False,
        error="Connection refused",
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "tool_call"
    assert "FAILED" in ev.summary


def test_to_evidence_succeeded_preserves_kind() -> None:
    obs = DevelopmentObservation(
        tool_name="traverse_dependencies",
        summary="Found 20 edges.",
        succeeded=True,
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert "FAILED" not in ev.summary


# ---------------------------------------------------------------------------
# RepositoryDiscoveryTool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_discovery_with_indexed_repos() -> None:
    mock_db = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.id = "uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    tool = RepositoryDiscoveryTool(db=mock_db, graph_repository=mock_graph_repo, user_id="user-1")
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.tool_name == "discover_repositories"
    assert len(obs.data["indexed_repositories"]) == 1
    assert obs.data["indexed_repositories"][0]["name"] == "order-service"


@pytest.mark.asyncio
async def test_repository_discovery_full_name_is_canonical_owner_slash_name() -> None:
    """Same identity-normalization regression as Planning's
    `GetIndexedRepositoriesTool` — this class duplicates its dict shape,
    so it needed the identical fix."""
    mock_db = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.id = "uuid-1"
    mock_repo.name = "prompt-library"
    mock_repo.owner = "sasikumars-cyb"
    mock_repo.full_name = "sasikumars-cyb/prompt-library"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    tool = RepositoryDiscoveryTool(db=mock_db, graph_repository=mock_graph_repo, user_id="user-1")
    obs = await tool.execute()

    repo = obs.data["indexed_repositories"][0]
    assert repo["full_name"] == "sasikumars-cyb/prompt-library"


@pytest.mark.asyncio
async def test_repository_discovery_none_indexed() -> None:
    mock_db = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.id = "uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"

    mock_repo_result = MagicMock()
    mock_repo_result.scalars.return_value.all.return_value = [mock_repo]
    # GraphHealthService issues a second query — for the latest
    # IndexingJob.status of repositories with no graph — only when
    # `has_graph` comes back False, which it does below.
    mock_jobs_result = MagicMock()
    mock_jobs_result.all.return_value = []
    mock_db.execute.side_effect = [mock_repo_result, mock_jobs_result]

    mock_graph_repo = AsyncMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)

    tool = RepositoryDiscoveryTool(db=mock_db, graph_repository=mock_graph_repo, user_id="user-1")
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.data["indexed_repositories"] == []
    assert obs.data["total_tracked"] == 1


@pytest.mark.asyncio
async def test_repository_discovery_db_failure() -> None:
    mock_db = AsyncMock()
    mock_db.execute.side_effect = Exception("DB unavailable")

    mock_graph_repo = AsyncMock()

    tool = RepositoryDiscoveryTool(db=mock_db, graph_repository=mock_graph_repo, user_id="user-1")
    obs = await tool.execute()

    assert obs.succeeded is False
    assert "DB unavailable" in obs.error


# ---------------------------------------------------------------------------
# ComponentDiscoveryTool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_component_discovery_empty_repos() -> None:
    mock_graph_repo = AsyncMock()
    tool = ComponentDiscoveryTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([])

    assert obs.tool_name == "discover_components"
    assert obs.data["components"] == []
    assert obs.data["kafka_topics"] == []
    assert "No indexed" in obs.summary


@pytest.mark.asyncio
async def test_component_discovery_finds_components_and_topics() -> None:
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

    tool = ComponentDiscoveryTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "repo-1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is True
    assert len(obs.data["components"]) == 1
    assert obs.data["components"][0]["name"] == "OrderService"
    assert obs.data["components"][0]["type"] == "Service"
    assert len(obs.data["kafka_topics"]) == 1
    assert obs.data["kafka_topics"][0]["name"] == "order.created"


@pytest.mark.asyncio
async def test_component_discovery_all_repos_fail() -> None:
    mock_graph_repo = AsyncMock()
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=Exception("Neo4j connection refused")
    )

    tool = ComponentDiscoveryTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "repo-1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is False
    assert obs.error != ""


# ---------------------------------------------------------------------------
# DependencyTraversalTool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_traversal_empty_repos() -> None:
    mock_graph_repo = AsyncMock()
    tool = DependencyTraversalTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([])

    assert obs.data["edges"] == []
    assert obs.data["cross_repo_edges"] == []
    assert "No indexed" in obs.summary


@pytest.mark.asyncio
async def test_dependency_traversal_finds_edges() -> None:
    mock_graph_repo = AsyncMock()

    edges = [
        GraphEdge(source_id="svc-1", target_id="topic-1", type="PRODUCES_TO", properties={}),
        GraphEdge(source_id="svc-2", target_id="topic-1", type="CONSUMES_FROM", properties={}),
    ]
    payload = GraphPayload(nodes=[], edges=edges)
    mock_graph_repo.get_full_graph = AsyncMock(return_value=payload)

    tool = DependencyTraversalTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "repo-1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is True
    assert len(obs.data["edges"]) == 2
    assert obs.data["total_edges"] == 2


@pytest.mark.asyncio
async def test_dependency_traversal_detects_cross_repo_coupling() -> None:
    mock_graph_repo = AsyncMock()

    # Repo 1: produces to topic-1
    payload1 = GraphPayload(
        nodes=[],
        edges=[
            GraphEdge(source_id="svc-1", target_id="topic-1", type="PRODUCES_TO", properties={})
        ],
    )
    # Repo 2: consumes from topic-1
    payload2 = GraphPayload(
        nodes=[],
        edges=[
            GraphEdge(source_id="topic-1", target_id="svc-2", type="CONSUMES_FROM", properties={})
        ],
    )

    mock_graph_repo.get_full_graph = AsyncMock(side_effect=[payload1, payload2])

    tool = DependencyTraversalTool(graph_repository=mock_graph_repo)
    repos = [
        {"id": "r1", "name": "order-service", "owner": "acme"},
        {"id": "r2", "name": "inventory-service", "owner": "acme"},
    ]
    obs = await tool.execute(repos)

    assert obs.succeeded is True
    assert len(obs.data["cross_repo_edges"]) == 1
    assert obs.data["cross_repo_edges"][0]["producer_repo"] == "order-service"
    assert obs.data["cross_repo_edges"][0]["consumer_repo"] == "inventory-service"


@pytest.mark.asyncio
async def test_dependency_traversal_all_repos_fail() -> None:
    mock_graph_repo = AsyncMock()
    mock_graph_repo.get_full_graph = AsyncMock(side_effect=Exception("Neo4j unavailable"))

    tool = DependencyTraversalTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "r1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is False
    assert obs.error != ""


# ---------------------------------------------------------------------------
# format_graph_context tests
# ---------------------------------------------------------------------------


def test_format_graph_context_no_repos() -> None:
    repos_obs = DevelopmentObservation(
        tool_name="discover_repositories",
        summary="No repos.",
        data={"indexed_repositories": [], "total_tracked": 0},
    )
    comp_obs = DevelopmentObservation(
        tool_name="discover_components",
        summary="No components.",
        data={"components": [], "kafka_topics": [], "repository_count": 0},
    )
    deps_obs = DevelopmentObservation(
        tool_name="traverse_dependencies",
        summary="No deps.",
        data={"edges": [], "cross_repo_edges": [], "total_edges": 0},
    )
    ctx = format_graph_context(repos_obs, comp_obs, deps_obs)
    assert "No repositories" in ctx


def test_format_graph_context_with_data() -> None:
    repos_obs = DevelopmentObservation(
        tool_name="discover_repositories",
        summary="2 repos.",
        data={
            "indexed_repositories": [
                {"id": "r1", "name": "order-service", "owner": "acme"},
                {"id": "r2", "name": "payment-service", "owner": "acme"},
            ],
            "total_tracked": 2,
        },
    )
    comp_obs = DevelopmentObservation(
        tool_name="discover_components",
        summary="3 components.",
        data={
            "components": [
                {
                    "id": "c1",
                    "name": "OrderController",
                    "type": "Controller",
                    "repository": "order-service",
                    "file_path": "src/OrderController.java",
                },
                {
                    "id": "c2",
                    "name": "PaymentService",
                    "type": "Service",
                    "repository": "payment-service",
                    "file_path": "",
                },
            ],
            "kafka_topics": [
                {"id": "t1", "name": "order.created", "repository": "order-service"},
            ],
            "repository_count": 2,
        },
    )
    deps_obs = DevelopmentObservation(
        tool_name="traverse_dependencies",
        summary="5 edges.",
        data={
            "edges": [
                {
                    "source": "svc-1",
                    "target": "topic-1",
                    "type": "PRODUCES_TO",
                    "repository": "order-service",
                },
                {
                    "source": "topic-1",
                    "target": "svc-2",
                    "type": "CONSUMES_FROM",
                    "repository": "payment-service",
                },
            ],
            "cross_repo_edges": [
                {
                    "topic": "topic-1",
                    "producer_repo": "order-service",
                    "consumer_repo": "payment-service",
                    "type": "CROSS_REPO_KAFKA",
                },
            ],
            "total_edges": 5,
        },
    )
    ctx = format_graph_context(repos_obs, comp_obs, deps_obs)
    assert "order-service" in ctx
    assert "order.created" in ctx
    assert "PRODUCES_TO" in ctx
    assert "Cross-repository" in ctx


# ---------------------------------------------------------------------------
# DevelopmentAgent integration tests (mocked LLM + graph)
# ---------------------------------------------------------------------------


def _make_development_context(display_name: str = "Implement JWT authentication") -> AgentContext:
    subject = Subject(
        subject_id="freetext:dev123",
        subject_type="freetext",
        display_name=display_name,
    )
    mock_db = AsyncMock()
    return AgentContext(
        subject=subject,
        goal="develop_change_plan",
        extras={"db": mock_db, "user_id": "user-1"},
    )


def _make_development_llm_response() -> str:
    return json.dumps(
        {
            "executive_summary": "Implement JWT auth across all services.",
            "repositories": [
                {"name": "order-service", "owner": "acme", "reason": "Needs auth middleware"},
                {"name": "payment-service", "owner": "acme", "reason": "Needs token validation"},
            ],
            "components": [
                {
                    "name": "OrderController",
                    "component_type": "Controller",
                    "repository": "order-service",
                    "file_path": "src/OrderController.java",
                    "change_description": "Add JWT filter",
                },
            ],
            "dependencies": [
                {
                    "source": "OrderController",
                    "target": "AuthService",
                    "relationship": "CALLS",
                    "risk_note": "New dependency introduced",
                },
            ],
            "reusable_implementations": [
                {
                    "name": "PaymentAuthFilter",
                    "repository": "payment-service",
                    "reason": "Already implements JWT validation pattern",
                },
            ],
            "implementation_phases": [
                {
                    "order": 1,
                    "title": "Create shared auth library",
                    "description": "Build JWT validation as a shared module.",
                    "affected_components": ["AuthService"],
                    "estimated_complexity": "medium",
                    "depends_on_phases": [],
                },
                {
                    "order": 2,
                    "title": "Integrate auth into order-service",
                    "description": "Add JWT filter to all controllers.",
                    "affected_components": ["OrderController"],
                    "estimated_complexity": "low",
                    "depends_on_phases": [1],
                },
            ],
            "risks": [
                {
                    "description": "Token expiry handling may cause cascading failures",
                    "severity": "medium",
                    "affected_component": "OrderController",
                    "mitigation": "Implement graceful token refresh",
                },
            ],
            "recommendations": [
                "Start with order-service as pilot before rolling out to all services",
                "Reuse PaymentAuthFilter pattern",
            ],
            "graph_context_used": True,
        }
    )


@pytest.mark.asyncio
async def test_development_agent_happy_path() -> None:
    """Core requirement: output must include graph_traversal + tool_call Evidence."""
    context = _make_development_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    component_node = GraphNode(
        id="c1",
        labels=["Component", "Controller"],
        properties={"name": "OrderController", "file_path": "src/OrderController.java"},
    )
    topic_node = GraphNode(
        id="t1",
        labels=["KafkaTopic"],
        properties={"name": "order.created"},
    )
    mock_graph_repo.get_nodes_by_label = AsyncMock(
        side_effect=lambda repo_id, label: (
            [component_node] if label == "Component" else [topic_node]
        )
    )
    mock_graph_repo.get_full_graph = AsyncMock(
        return_value=GraphPayload(
            nodes=[],
            edges=[GraphEdge(source_id="c1", target_id="t1", type="PRODUCES_TO", properties={})],
        )
    )

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_repo.full_name = "acme/order-service"
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_repo]
    mock_db.execute.return_value = mock_result

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_make_development_llm_response()),
        ),
    ):
        agent = DevelopmentAgent()
        output = await agent.run(context)

    # Must have evidence
    assert len(output.evidence) >= 3  # repos (tool_call) + components (graph) + deps (graph) + llm

    # Core requirement: at least one graph_traversal AND one tool_call
    evidence_kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in evidence_kinds
    assert "tool_call" in evidence_kinds
    assert "llm_reasoning" in evidence_kinds

    # Result populated correctly
    assert output.result["executive_summary"] == "Implement JWT auth across all services."
    assert len(output.result["repositories"]) == 2
    assert len(output.result["components"]) == 1
    assert len(output.result["implementation_phases"]) == 2
    assert len(output.result["risks"]) == 1
    assert len(output.result["reusable_implementations"]) == 1
    assert len(output.result["recommendations"]) == 2

    # Repository identity regression: repositories_consulted must be the
    # canonical "owner/name" identity, not the bare repository name.
    assert output.result["repositories_consulted"] == ["acme/order-service"]

    # Confidence within bounds
    assert 0.0 <= output.confidence.score <= 1.0
    assert output.confidence.score > 0.7  # has graph data

    # Agent ID
    assert output.agent_id == "development"
    assert output.prompt_version == "1.1"


@pytest.mark.asyncio
async def test_development_agent_no_indexed_repos() -> None:
    """When no repos are indexed, confidence is low but agent still runs."""
    context = _make_development_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "repo-uuid-1"
    mock_repo.name = "order-service"
    mock_repo.owner = "acme"
    mock_repo_result = MagicMock()
    mock_repo_result.scalars.return_value.all.return_value = [mock_repo]
    # GraphHealthService issues a second query — for the latest
    # IndexingJob.status of repositories with no graph — only when
    # `has_graph` comes back False, which it does above.
    mock_jobs_result = MagicMock()
    mock_jobs_result.all.return_value = []
    mock_db.execute.side_effect = [mock_repo_result, mock_jobs_result]

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_make_development_llm_response()),
        ),
    ):
        agent = DevelopmentAgent()
        output = await agent.run(context)

    assert output.confidence.score <= 0.5
    assert "general engineering practices" in output.confidence.reasoning


@pytest.mark.asyncio
async def test_development_agent_graph_unavailable() -> None:
    """When graph infrastructure fails, confidence is very low."""
    context = _make_development_context()

    mock_db = context.extras["db"]
    mock_db.execute.side_effect = Exception("DB down")

    mock_graph_repo = MagicMock()

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_make_development_llm_response()),
        ),
    ):
        agent = DevelopmentAgent()
        output = await agent.run(context)

    assert output.confidence.score <= 0.35
    assert "unavailable" in output.confidence.reasoning.lower()

    # Still has evidence (failure is recorded)
    assert len(output.evidence) > 0
    failed_evidence = [e for e in output.evidence if "FAILED" in e.summary]
    assert len(failed_evidence) >= 1

    # ADR 0027 — no repository-scoped evidence pool exists when the graph
    # is unavailable, so every component must fail closed to "not_checked"
    # (never "verified", never silently trusted).
    assert output.result["components"]
    assert all(c["file_path_verification"] == "not_checked" for c in output.result["components"])


@pytest.mark.asyncio
async def test_development_agent_graph_context_used_overridden_when_graph_fails() -> None:
    """Work Item 3: the LLM mock (`_make_development_llm_response`) claims
    graph_context_used=True, but the graph is unavailable here — the
    persisted result must not repeat that claim."""
    context = _make_development_context()

    mock_db = context.extras["db"]
    mock_db.execute.side_effect = Exception("DB down")

    mock_graph_repo = MagicMock()

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_make_development_llm_response()),
        ),
    ):
        agent = DevelopmentAgent()
        output = await agent.run(context)

    assert output.result["graph_context_used"] is False


@pytest.mark.asyncio
async def test_development_agent_llm_failure_raises() -> None:
    """LLM failure raises DevelopmentLLMError (per error policy)."""
    context = _make_development_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(side_effect=DevelopmentLLMError("Timeout")),
        ),
    ):
        agent = DevelopmentAgent()
        with pytest.raises(DevelopmentLLMError):
            await agent.run(context)


# ---------------------------------------------------------------------------
# Manifest and Registration tests
# ---------------------------------------------------------------------------


def test_development_manifest_fields() -> None:
    assert DEVELOPMENT_MANIFEST.agent_id == "development"
    assert "develop_change_plan" in DEVELOPMENT_MANIFEST.goals
    assert "freetext" in DEVELOPMENT_MANIFEST.accepted_subject_types
    assert DEVELOPMENT_MANIFEST.cost_class == "standard"
    assert DEVELOPMENT_MANIFEST.max_graph_hops == 3
    assert DEVELOPMENT_MANIFEST.output_schema_name == "DevelopmentPlan"


def test_development_agent_registered_in_global_registry() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry

    register_agents()

    manifests = global_registry.all_manifests()
    agent_ids = {m.agent_id for m in manifests}
    assert "development" in agent_ids


def test_selector_routes_develop_change_plan_goal() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry
    from app.orchestrator.selector import AgentSelector

    register_agents()

    selector = AgentSelector(global_registry)
    agent_id = selector.select("develop_change_plan")
    assert agent_id == "development"


def test_selector_includes_development_in_known_goals() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry
    from app.orchestrator.selector import AgentSelector

    register_agents()

    selector = AgentSelector(global_registry)
    goals = selector.known_goals()
    assert "develop_change_plan" in goals


# ---------------------------------------------------------------------------
# DevelopmentPlan schema tests
# ---------------------------------------------------------------------------


def test_development_plan_schema_validates() -> None:
    plan = DevelopmentPlan(
        goal="Implement JWT auth",
        executive_summary="Add JWT to all services.",
        repositories=[],
        components=[],
        dependencies=[],
        reusable_implementations=[],
        implementation_phases=[],
        risks=[],
        recommendations=[],
        graph_context_used=False,
    )
    assert plan.goal == "Implement JWT auth"
    data = plan.model_dump()
    assert "executive_summary" in data
    assert data["graph_context_used"] is False


def test_development_plan_with_full_data() -> None:
    from app.agents.development.schemas import (
        AffectedComponent,
        AffectedRepository,
        Dependency,
        ImplementationPhase,
        ReusableImplementation,
        Risk,
    )

    plan = DevelopmentPlan(
        goal="Split OrderService",
        executive_summary="Decompose into command and query services.",
        repositories=[
            AffectedRepository(name="order-service", owner="acme", reason="Primary target")
        ],
        components=[
            AffectedComponent(
                name="OrderController",
                component_type="Controller",
                repository="order-service",
                file_path="src/OrderController.java",
                change_description="Split into two controllers",
            )
        ],
        dependencies=[
            Dependency(
                source="OrderController",
                target="OrderService",
                relationship="CALLS",
                risk_note="Tight coupling",
            )
        ],
        reusable_implementations=[
            ReusableImplementation(
                name="PaymentQueryService", repository="payment-service", reason="Already CQRS"
            )
        ],
        implementation_phases=[
            ImplementationPhase(
                order=1,
                title="Phase 1",
                description="Extract queries",
                affected_components=["OrderController"],
                estimated_complexity="high",
                depends_on_phases=[],
            )
        ],
        risks=[
            Risk(
                description="Data consistency",
                severity="high",
                affected_component="OrderService",
                mitigation="Use eventual consistency",
            )
        ],
        recommendations=["Start with read model"],
        graph_context_used=True,
        repositories_consulted=["order-service", "payment-service"],
    )
    data = plan.model_dump()
    assert len(data["repositories"]) == 1
    assert len(data["components"]) == 1
    assert len(data["implementation_phases"]) == 1
    assert data["risks"][0]["severity"] == "high"
