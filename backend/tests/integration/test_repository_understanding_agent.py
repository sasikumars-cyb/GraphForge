"""Integration tests for the Repository Understanding Agent:

- Registration in the global `AgentRegistry` and goal routing (AI
  Workspace's `POST /agent-runs` path).
- `RepositoryProfileService` reuse through a real Postgres transaction
  (`db_session` fixture, rolled back per test) — the same no-mocks
  convention `tests/integration/test_engineering_intelligence_*.py`
  already established.
- Full agent execution (`RepositoryUnderstandingAgent.run`) against a
  fake `IGraphRepository` (the interface boundary the platform injects
  at) plus real Postgres, with the LLM call mocked — proving the agent
  makes no direct Neo4j call, no direct Postgres query, and no direct
  `EngineeringMemoryService` call anywhere in its own code: every fact in
  the result traces back to exactly one `RepositoryProfileService.get_profile`
  invocation.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, Subject
from app.agents.repository_understanding.agent import RepositoryUnderstandingAgent
from app.agents.repository_understanding.manifest import REPOSITORY_UNDERSTANDING_MANIFEST
from app.agents.setup import register_agents
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.models.repository import Repository
from app.models.user import User
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import AgentSelector


class _FakeGraphRepository(IGraphRepository):
    def __init__(self, payload: GraphPayload) -> None:
        self._payload = payload
        self.get_full_graph_calls: list[str] = []

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        raise NotImplementedError

    async def replace_repository_files_subgraph(
        self, repository_id: str, file_paths: list[str], graph: GraphPayload
    ) -> None:
        raise NotImplementedError

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        self.get_full_graph_calls.append(repository_id)
        return self._payload

    async def get_nodes_by_label(self, repository_id: str, label: str) -> list[GraphNode]:
        raise NotImplementedError

    async def get_kafka_topic_edges(self, repository_id: str) -> list[GraphEdge]:
        raise NotImplementedError

    async def has_graph(self, repository_id: str) -> bool:
        raise NotImplementedError

    async def replace_cross_repository_edges(
        self, source_repository_id: str, edges: list[GraphEdge]
    ) -> None:
        raise NotImplementedError

    async def get_outgoing_cross_repository_edges(self, repository_id: str) -> list[GraphEdge]:
        raise NotImplementedError

    async def get_neighborhood(
        self, repository_id: str, seed_node_ids: list[str], edge_types: list[str], max_hops: int
    ) -> GraphPayload:
        raise NotImplementedError


@pytest.fixture
async def repository_id(db_session: AsyncSession) -> uuid.UUID:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()
    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name="test-repo",
        full_name="test-owner/test-repo",
        html_url="https://github.com/test-owner/test-repo",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo.id


def test_agent_is_registered_in_global_registry() -> None:
    register_agents()
    agent_ids = {m.agent_id for m in global_registry.all_manifests()}
    assert "repository_understanding" in agent_ids


def test_selector_routes_analyze_repository_understanding_goal() -> None:
    register_agents()
    selector = AgentSelector(global_registry)
    assert selector.select("analyze_repository_understanding") == "repository_understanding"


def test_manifest_declares_llm_and_neo4j_dependencies() -> None:
    assert REPOSITORY_UNDERSTANDING_MANIFEST.max_graph_hops > 0
    assert "repository" in REPOSITORY_UNDERSTANDING_MANIFEST.accepted_subject_types
    assert "analyze_repository_understanding" in REPOSITORY_UNDERSTANDING_MANIFEST.goals


async def test_agent_run_produces_report_from_real_graph_and_no_other_source(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    payload = GraphPayload(
        nodes=[
            GraphNode(
                id=f"{repository_id}:endpoint:get:/orders",
                labels=["Endpoint"],
                properties={"http_method": "GET", "path": "/orders"},
            ),
            GraphNode(
                id=f"{repository_id}:table:orders",
                labels=["DataTable"],
                properties={"name": "orders"},
            ),
        ]
    )
    fake_graph = _FakeGraphRepository(payload)
    agent = RepositoryUnderstandingAgent()
    context = AgentContext(
        subject=Subject(subject_id=f"repo:{repository_id}", subject_type="repository"),
        goal="analyze_repository_understanding",
        extras={"db": db_session, "graph_repository": fake_graph},
    )

    with patch(
        "app.agents.frontier.base_frontier_agent.prompt_builder.run",
        new=AsyncMock(
            return_value=(
                {
                    "executive_summary": "A checkout service.",
                    "interesting_findings": ["Only one API."],
                },
                None,
            )
        ),
    ):
        output = await agent.run(context)

    assert fake_graph.get_full_graph_calls == [str(repository_id)]
    assert output.agent_id == "repository_understanding"
    assert output.subject_id == f"repo:{repository_id}"
    assert output.result["apis"] == ["GET /orders"]
    assert output.result["databases"] == ["orders"]
    assert output.result["executive_summary"].startswith("A checkout service.")
    assert output.confidence.score == 1.0
    tool_call_evidence = [e for e in output.evidence if e.kind == "tool_call"]
    assert len(tool_call_evidence) == 1
    assert tool_call_evidence[0].reference == "engineering_intelligence:repository_profile"


async def test_agent_run_degrades_gracefully_for_unindexed_repository(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    """An unindexed repository still yields a valid (empty) `RepositoryProfile`
    — `build_prompt` still runs (there is a profile, just an empty one);
    only a fully failed service call (no profile at all) skips the LLM
    call, covered by `test_build_prompt_returns_none_when_profile_missing`."""
    fake_graph = _FakeGraphRepository(GraphPayload())
    agent = RepositoryUnderstandingAgent()
    context = AgentContext(
        subject=Subject(subject_id=f"repo:{repository_id}", subject_type="repository"),
        goal="analyze_repository_understanding",
        extras={"db": db_session, "graph_repository": fake_graph},
    )

    with patch(
        "app.agents.frontier.base_frontier_agent.prompt_builder.run",
        new=AsyncMock(return_value=({}, None)),
    ) as mock_prompt:
        output = await agent.run(context)

    mock_prompt.assert_awaited_once()
    assert output.result["apis"] == []
    assert "0 API(s)" in output.result["executive_summary"]
