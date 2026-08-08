"""Unit tests for the Test Planning Agent.

Covers:
- Tool unit tests (observation, evidence, formatting)
- TestRepositoryDiscoveryTool: indexed repos, none indexed, DB failure
- TestComponentDiscoveryTool: empty repos, components found, all repos fail
- TestDependencyTraversalTool: empty repos, edges found, cross-repo, integration points
- TestPlanningAgent integration: happy path, no graph data, graph unavailable, LLM failure
- Output schema: TestPlan fields populate correctly
- Agent registration: manifest declared, selector routes goal

All graph and LLM calls are mocked — no real Neo4j or OpenAI needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.testing.agent import TestingLLMError, TestPlanningAgent
from app.agents.testing.manifest import TESTING_MANIFEST
from app.agents.testing.schemas import TestPlan
from app.agents.testing.tools import (
    TestComponentDiscoveryTool,
    TestDependencyTraversalTool,
    TestingObservation,
    TestRepositoryDiscoveryTool,
    format_graph_context,
    to_evidence,
)
from app.graph.models import GraphEdge, GraphNode, GraphPayload

# ---------------------------------------------------------------------------
# Observation unit tests
# ---------------------------------------------------------------------------


def test_testing_observation_fields() -> None:
    obs = TestingObservation(
        tool_name="test_tool",
        summary="Found 5 components.",
        data={"count": 5},
    )
    assert obs.succeeded is True
    assert obs.error == ""


def test_testing_observation_failure() -> None:
    obs = TestingObservation(
        tool_name="test_tool",
        summary="Connection refused",
        succeeded=False,
        error="Connection refused",
    )
    assert obs.succeeded is False


# ---------------------------------------------------------------------------
# Evidence builder tests
# ---------------------------------------------------------------------------


def test_to_evidence_graph_traversal() -> None:
    obs = TestingObservation(
        tool_name="discover_test_components",
        summary="Discovered 10 components.",
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert ev.reference == "discover_test_components"


def test_to_evidence_tool_call() -> None:
    obs = TestingObservation(
        tool_name="discover_test_repositories",
        summary="Found 3 repos.",
    )
    ev = to_evidence(obs, "tool_call")
    assert ev.kind == "tool_call"


def test_to_evidence_failed_never_reports_graph_traversal() -> None:
    obs = TestingObservation(
        tool_name="discover_test_components",
        summary="Neo4j down",
        succeeded=False,
        error="Neo4j down",
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "tool_call"
    assert "FAILED" in ev.summary


def test_to_evidence_succeeded_preserves_kind() -> None:
    obs = TestingObservation(
        tool_name="traverse_test_dependencies",
        summary="Found 20 edges.",
        succeeded=True,
    )
    ev = to_evidence(obs, "graph_traversal")
    assert ev.kind == "graph_traversal"
    assert "FAILED" not in ev.summary


# ---------------------------------------------------------------------------
# TestRepositoryDiscoveryTool tests
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

    tool = TestRepositoryDiscoveryTool(db=mock_db, graph_repository=mock_graph_repo)
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.tool_name == "discover_test_repositories"
    assert len(obs.data["indexed_repositories"]) == 1


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

    tool = TestRepositoryDiscoveryTool(db=mock_db, graph_repository=mock_graph_repo)
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

    tool = TestRepositoryDiscoveryTool(db=mock_db, graph_repository=mock_graph_repo)
    obs = await tool.execute()

    assert obs.succeeded is True
    assert obs.data["indexed_repositories"] == []


@pytest.mark.asyncio
async def test_repository_discovery_db_failure() -> None:
    mock_db = AsyncMock()
    mock_db.execute.side_effect = Exception("DB unavailable")

    tool = TestRepositoryDiscoveryTool(db=mock_db, graph_repository=AsyncMock())
    obs = await tool.execute()

    assert obs.succeeded is False
    assert "DB unavailable" in obs.error


# ---------------------------------------------------------------------------
# TestComponentDiscoveryTool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_component_discovery_empty_repos() -> None:
    tool = TestComponentDiscoveryTool(graph_repository=AsyncMock())
    obs = await tool.execute([])

    assert obs.data["components"] == []
    assert "No indexed" in obs.summary


@pytest.mark.asyncio
async def test_component_discovery_finds_components() -> None:
    mock_graph_repo = AsyncMock()

    component_node = GraphNode(
        id="comp-1",
        labels=["Component", "Controller"],
        properties={"name": "OrderController", "file_path": "src/OrderController.java"},
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

    tool = TestComponentDiscoveryTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "r1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is True
    assert len(obs.data["components"]) == 1
    assert obs.data["components"][0]["name"] == "OrderController"
    assert len(obs.data["kafka_topics"]) == 1


@pytest.mark.asyncio
async def test_component_discovery_all_repos_fail() -> None:
    mock_graph_repo = AsyncMock()
    mock_graph_repo.get_nodes_by_label = AsyncMock(side_effect=Exception("Neo4j down"))

    tool = TestComponentDiscoveryTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "r1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is False


# ---------------------------------------------------------------------------
# TestDependencyTraversalTool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_traversal_empty_repos() -> None:
    tool = TestDependencyTraversalTool(graph_repository=AsyncMock())
    obs = await tool.execute([])

    assert obs.data["edges"] == []
    assert obs.data["integration_points"] == []


@pytest.mark.asyncio
async def test_dependency_traversal_finds_integration_points() -> None:
    mock_graph_repo = AsyncMock()
    edges = [
        GraphEdge(source_id="svc-1", target_id="topic-1", type="PRODUCES_TO", properties={}),
        GraphEdge(source_id="svc-2", target_id="svc-3", type="CALLS", properties={}),
        GraphEdge(source_id="x", target_id="y", type="CONTAINS", properties={}),
    ]
    payload = GraphPayload(nodes=[], edges=edges)
    mock_graph_repo.get_full_graph = AsyncMock(return_value=payload)

    tool = TestDependencyTraversalTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "r1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is True
    # Only PRODUCES_TO and CALLS are integration points
    assert len(obs.data["integration_points"]) == 2


@pytest.mark.asyncio
async def test_dependency_traversal_detects_cross_repo_coupling() -> None:
    mock_graph_repo = AsyncMock()

    payload1 = GraphPayload(
        nodes=[],
        edges=[
            GraphEdge(source_id="svc-1", target_id="topic-1", type="PRODUCES_TO", properties={})
        ],
    )
    payload2 = GraphPayload(
        nodes=[],
        edges=[
            GraphEdge(source_id="topic-1", target_id="svc-2", type="CONSUMES_FROM", properties={})
        ],
    )

    mock_graph_repo.get_full_graph = AsyncMock(side_effect=[payload1, payload2])

    tool = TestDependencyTraversalTool(graph_repository=mock_graph_repo)
    obs = await tool.execute(
        [
            {"id": "r1", "name": "order-service", "owner": "acme"},
            {"id": "r2", "name": "inventory-service", "owner": "acme"},
        ]
    )

    assert obs.succeeded is True
    assert len(obs.data["cross_repo_edges"]) == 1
    assert obs.data["cross_repo_edges"][0]["producer_repo"] == "order-service"
    assert obs.data["cross_repo_edges"][0]["consumer_repo"] == "inventory-service"


@pytest.mark.asyncio
async def test_dependency_traversal_all_repos_fail() -> None:
    mock_graph_repo = AsyncMock()
    mock_graph_repo.get_full_graph = AsyncMock(side_effect=Exception("Neo4j down"))

    tool = TestDependencyTraversalTool(graph_repository=mock_graph_repo)
    obs = await tool.execute([{"id": "r1", "name": "order-service", "owner": "acme"}])

    assert obs.succeeded is False


# ---------------------------------------------------------------------------
# format_graph_context tests
# ---------------------------------------------------------------------------


def test_format_graph_context_no_repos() -> None:
    repos_obs = TestingObservation(
        tool_name="discover_test_repositories",
        summary="No repos.",
        data={"indexed_repositories": [], "total_tracked": 0},
    )
    comp_obs = TestingObservation(
        tool_name="discover_test_components",
        summary="No comps.",
        data={"components": [], "kafka_topics": [], "repository_count": 0},
    )
    deps_obs = TestingObservation(
        tool_name="traverse_test_dependencies",
        summary="No deps.",
        data={"edges": [], "cross_repo_edges": [], "integration_points": [], "total_edges": 0},
    )
    ctx = format_graph_context(repos_obs, comp_obs, deps_obs)
    assert "No repositories" in ctx


def test_format_graph_context_with_data() -> None:
    repos_obs = TestingObservation(
        tool_name="discover_test_repositories",
        summary="2 repos.",
        data={
            "indexed_repositories": [
                {"id": "r1", "name": "order-service", "owner": "acme"},
                {"id": "r2", "name": "payment-service", "owner": "acme"},
            ],
            "total_tracked": 2,
        },
    )
    comp_obs = TestingObservation(
        tool_name="discover_test_components",
        summary="2 comps.",
        data={
            "components": [
                {
                    "id": "c1",
                    "name": "OrderController",
                    "type": "Controller",
                    "repository": "order-service",
                    "file_path": "",
                },
            ],
            "kafka_topics": [
                {"id": "t1", "name": "order.created", "repository": "order-service"},
            ],
            "repository_count": 2,
        },
    )
    deps_obs = TestingObservation(
        tool_name="traverse_test_dependencies",
        summary="3 edges.",
        data={
            "edges": [
                {
                    "source": "svc-1",
                    "target": "topic-1",
                    "type": "PRODUCES_TO",
                    "repository": "order-service",
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
            "integration_points": [
                {
                    "source": "svc-1",
                    "target": "topic-1",
                    "type": "PRODUCES_TO",
                    "repository": "order-service",
                },
            ],
            "total_edges": 3,
        },
    )
    ctx = format_graph_context(repos_obs, comp_obs, deps_obs)
    assert "order-service" in ctx
    assert "order.created" in ctx
    assert "Integration points" in ctx
    assert "Cross-repository" in ctx


# ---------------------------------------------------------------------------
# TestPlanningAgent integration tests (mocked LLM + graph)
# ---------------------------------------------------------------------------


def _make_testing_context(
    display_name: str = "Test strategy for JWT authentication",
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:test123",
        subject_type="freetext",
        display_name=display_name,
    )
    mock_db = AsyncMock()
    return AgentContext(
        subject=subject,
        goal="plan_tests",
        extras={"db": mock_db},
    )


def _make_testing_llm_response() -> str:
    return json.dumps(
        {
            "executive_summary": "Comprehensive test strategy for JWT authentication.",
            "test_scope": {
                "in_scope": ["Authentication flow", "Token validation"],
                "out_of_scope": ["UI styling", "Performance at scale"],
            },
            "affected_repositories": ["order-service", "payment-service"],
            "affected_components": ["OrderController", "PaymentService"],
            "regression_tests": [
                {
                    "component": "OrderController",
                    "description": "Existing order creation still works with auth",
                    "priority": "critical",
                    "automated": True,
                },
            ],
            "integration_tests": [
                {
                    "source_component": "OrderController",
                    "target_component": "AuthService",
                    "relationship": "CALLS",
                    "description": "JWT token validation on order creation",
                    "priority": "critical",
                },
            ],
            "edge_cases": [
                {
                    "description": "Expired token with valid signature",
                    "component": "OrderController",
                    "severity": "high",
                    "category": "boundary",
                },
            ],
            "environment_requirements": [
                {
                    "name": "Integration",
                    "description": "Full service stack with auth provider",
                    "services_required": ["order-service", "auth-service"],
                },
            ],
            "execution_order": [
                {
                    "order": 1,
                    "title": "Unit Tests",
                    "description": "Token parsing and validation logic",
                    "test_types": ["unit"],
                    "depends_on_phases": [],
                },
                {
                    "order": 2,
                    "title": "Integration Tests",
                    "description": "Auth middleware with real service calls",
                    "test_types": ["integration"],
                    "depends_on_phases": [1],
                },
            ],
            "automation_candidates": [
                {
                    "description": "JWT token expiry validation",
                    "component": "AuthService",
                    "test_type": "unit",
                    "reason": "Deterministic, fast, high value",
                },
            ],
            "manual_validations": [
                {
                    "description": "OAuth flow UX across browsers",
                    "component": "LoginPage",
                    "reason": "Browser-specific rendering",
                },
            ],
            "risks": [
                {
                    "description": "Token rotation may break cached sessions",
                    "severity": "medium",
                    "affected_component": "OrderController",
                    "mitigation": "Test with short-lived tokens",
                },
            ],
            "recommendations": [
                "Start with unit tests for token parsing",
                "Run integration tests against a staging auth provider",
            ],
            "graph_context_used": True,
        }
    )


@pytest.mark.asyncio
async def test_testing_agent_happy_path() -> None:
    """Output must include graph_traversal + tool_call Evidence."""
    context = _make_testing_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)
    component_node = GraphNode(
        id="c1",
        labels=["Component", "Controller"],
        properties={"name": "OrderController"},
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
        patch("app.agents.testing.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.testing.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.testing.agent._call_llm",
            new=AsyncMock(return_value=_make_testing_llm_response()),
        ),
    ):
        agent = TestPlanningAgent()
        output = await agent.run(context)

    # Evidence validation
    assert len(output.evidence) >= 3
    evidence_kinds = {e.kind for e in output.evidence}
    assert "graph_traversal" in evidence_kinds
    assert "tool_call" in evidence_kinds
    assert "llm_reasoning" in evidence_kinds

    # Result fields
    assert output.result["executive_summary"]
    assert len(output.result["regression_tests"]) == 1
    assert len(output.result["integration_tests"]) == 1
    assert len(output.result["edge_cases"]) == 1
    assert len(output.result["execution_order"]) == 2
    assert len(output.result["automation_candidates"]) == 1
    assert len(output.result["manual_validations"]) == 1

    # Repository identity regression: repositories_consulted must be the
    # canonical "owner/name" identity, not the bare repository name.
    assert output.result["repositories_consulted"] == ["acme/order-service"]

    # Confidence
    assert 0.0 <= output.confidence.score <= 1.0
    assert output.confidence.score > 0.7
    assert output.agent_id == "testing"
    assert output.prompt_version == "1.2"

    # Readiness-guardrail regression: the "this is a test PLAN, not an
    # execution" disclaimer must never appear in verification_warnings (a
    # true-on-every-run statement of fact is not a verification finding —
    # see app.agents.verification's module docstring) — it lives in
    # execution_status_note instead, which is always populated and does
    # not participate in blocking classification.
    assert not any("test PLAN" in w for w in output.result["verification_warnings"])
    assert "test PLAN" in output.result["execution_status_note"]
    assert "not" in output.result["execution_status_note"].lower()


@pytest.mark.asyncio
async def test_testing_agent_no_indexed_repos() -> None:
    context = _make_testing_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))

    mock_db = context.extras["db"]
    mock_repo = MagicMock()
    mock_repo.id = "uuid-1"
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
        patch("app.agents.testing.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.testing.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.testing.agent._call_llm",
            new=AsyncMock(return_value=_make_testing_llm_response()),
        ),
    ):
        agent = TestPlanningAgent()
        output = await agent.run(context)

    assert output.confidence.score <= 0.5
    assert "general QA practices" in output.confidence.reasoning


@pytest.mark.asyncio
async def test_testing_agent_graph_unavailable() -> None:
    context = _make_testing_context()

    mock_db = context.extras["db"]
    mock_db.execute.side_effect = Exception("DB down")

    with (
        patch("app.agents.testing.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.testing.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch(
            "app.agents.testing.agent._call_llm",
            new=AsyncMock(return_value=_make_testing_llm_response()),
        ),
    ):
        agent = TestPlanningAgent()
        output = await agent.run(context)

    assert output.confidence.score <= 0.35
    assert "unavailable" in output.confidence.reasoning.lower()
    failed_evidence = [e for e in output.evidence if "FAILED" in e.summary]
    assert len(failed_evidence) >= 1


@pytest.mark.asyncio
async def test_testing_agent_graph_context_used_overridden_when_graph_fails() -> None:
    """Work Item 3: the LLM mock (`_make_testing_llm_response`) claims
    graph_context_used=True, but the graph is unavailable here — the
    persisted result must not repeat that claim."""
    context = _make_testing_context()

    mock_db = context.extras["db"]
    mock_db.execute.side_effect = Exception("DB down")

    with (
        patch("app.agents.testing.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.testing.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch(
            "app.agents.testing.agent._call_llm",
            new=AsyncMock(return_value=_make_testing_llm_response()),
        ),
    ):
        agent = TestPlanningAgent()
        output = await agent.run(context)

    assert output.result["graph_context_used"] is False


@pytest.mark.asyncio
async def test_testing_agent_llm_failure_raises() -> None:
    context = _make_testing_context()

    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=False)
    mock_graph_repo.get_nodes_by_label = AsyncMock(return_value=[])
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))

    mock_db = context.extras["db"]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    with (
        patch("app.agents.testing.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.testing.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.testing.agent._call_llm",
            new=AsyncMock(side_effect=TestingLLMError("Timeout")),
        ),
    ):
        agent = TestPlanningAgent()
        with pytest.raises(TestingLLMError):
            await agent.run(context)


# ---------------------------------------------------------------------------
# Manifest and Registration tests
# ---------------------------------------------------------------------------


def test_testing_manifest_fields() -> None:
    assert TESTING_MANIFEST.agent_id == "testing"
    assert "plan_tests" in TESTING_MANIFEST.goals
    assert "freetext" in TESTING_MANIFEST.accepted_subject_types
    assert TESTING_MANIFEST.cost_class == "standard"
    assert TESTING_MANIFEST.max_graph_hops == 3
    assert TESTING_MANIFEST.output_schema_name == "TestPlan"


def test_testing_agent_registered_in_global_registry() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry

    register_agents()
    agent_ids = {m.agent_id for m in global_registry.all_manifests()}
    assert "testing" in agent_ids


def test_selector_routes_plan_tests_goal() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry
    from app.orchestrator.selector import AgentSelector

    register_agents()
    selector = AgentSelector(global_registry)
    assert selector.select("plan_tests") == "testing"


def test_selector_includes_plan_tests_in_known_goals() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry
    from app.orchestrator.selector import AgentSelector

    register_agents()
    selector = AgentSelector(global_registry)
    assert "plan_tests" in selector.known_goals()


# ---------------------------------------------------------------------------
# TestPlan schema tests
# ---------------------------------------------------------------------------


def test_test_plan_schema_validates() -> None:
    plan = TestPlan(
        goal="Test JWT auth",
        executive_summary="Test all auth flows.",
    )
    assert plan.goal == "Test JWT auth"
    data = plan.model_dump()
    assert "executive_summary" in data
    assert data["graph_context_used"] is False


def test_test_plan_with_full_data() -> None:
    from app.agents.testing.schemas import (
        AutomationCandidate,
        EdgeCase,
        EnvironmentRequirement,
        ExecutionPhase,
        IntegrationTest,
        ManualValidation,
        RegressionTest,
        TestRisk,
        TestScope,
    )

    plan = TestPlan(
        goal="Test JWT auth",
        executive_summary="Comprehensive strategy.",
        test_scope=TestScope(in_scope=["Auth flow"], out_of_scope=["UI tests"]),
        affected_repositories=["order-service"],
        affected_components=["OrderController"],
        regression_tests=[
            RegressionTest(
                component="OrderController",
                description="Order creation works",
                priority="critical",
                automated=True,
            )
        ],
        integration_tests=[
            IntegrationTest(
                source_component="OrderController",
                target_component="AuthService",
                relationship="CALLS",
                description="Token validation",
                priority="high",
            )
        ],
        edge_cases=[
            EdgeCase(
                description="Expired token",
                component="OrderController",
                severity="high",
                category="boundary",
            )
        ],
        environment_requirements=[
            EnvironmentRequirement(
                name="Staging", description="Full stack", services_required=["auth-service"]
            )
        ],
        execution_order=[
            ExecutionPhase(
                order=1,
                title="Unit Tests",
                description="Token logic",
                test_types=["unit"],
                depends_on_phases=[],
            )
        ],
        automation_candidates=[
            AutomationCandidate(
                description="Token expiry", component="AuthService", test_type="unit", reason="Fast"
            )
        ],
        manual_validations=[
            ManualValidation(
                description="OAuth UX", component="LoginPage", reason="Browser-specific"
            )
        ],
        risks=[
            TestRisk(
                description="Token rotation",
                severity="medium",
                affected_component="OrderController",
                mitigation="Short-lived tokens",
            )
        ],
        recommendations=["Start with unit tests"],
        graph_context_used=True,
        repositories_consulted=["order-service"],
    )
    data = plan.model_dump()
    assert len(data["regression_tests"]) == 1
    assert len(data["integration_tests"]) == 1
    assert len(data["edge_cases"]) == 1
    assert data["test_scope"]["in_scope"] == ["Auth flow"]
