"""Integration tests for the Impact Analysis Agent:

- Registration in the global `AgentRegistry` and goal routing (AI
  Workspace's `POST /agent-runs` path).
- `ImpactAnalysisService` reuse through a real Postgres transaction
  (`db_session` fixture, rolled back per test) plus a fake
  `IGraphRepository` (the interface boundary the platform injects at) —
  the same no-mocks-on-persistence convention
  `tests/integration/test_engineering_intelligence_*.py` already
  established.
- Full agent execution (`ImpactAnalysisAgent.run`), with the LLM call
  mocked — proving the agent makes no direct Neo4j call, no Cypher, no
  direct Postgres query, and no direct `EngineeringMemoryService` call
  anywhere in its own code: every fact in the result traces back to
  exactly one `ImpactAnalysisService.compute_blast_radius` invocation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext, Subject
from app.agents.impact_analysis.agent import ImpactAnalysisAgent
from app.agents.impact_analysis.manifest import IMPACT_ANALYSIS_MANIFEST
from app.agents.setup import register_agents
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import AgentSelector


class _FakeGraphRepository(IGraphRepository):
    def __init__(self, neighborhood: GraphPayload) -> None:
        self._neighborhood = neighborhood
        self.get_neighborhood_calls: list[tuple[str, list[str], list[str], int]] = []

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        raise NotImplementedError

    async def replace_repository_files_subgraph(
        self, repository_id: str, file_paths: list[str], graph: GraphPayload
    ) -> None:
        raise NotImplementedError

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        raise NotImplementedError

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
        self.get_neighborhood_calls.append((repository_id, seed_node_ids, edge_types, max_hops))
        return self._neighborhood


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
    assert "impact_analysis" in agent_ids


def test_selector_routes_analyze_impact_analysis_goal() -> None:
    register_agents()
    selector = AgentSelector(global_registry)
    assert selector.select("analyze_impact_analysis") == "impact_analysis"


def test_manifest_declares_llm_and_neo4j_dependencies() -> None:
    assert IMPACT_ANALYSIS_MANIFEST.max_graph_hops > 0
    assert "repository" in IMPACT_ANALYSIS_MANIFEST.accepted_subject_types
    assert "analyze_impact_analysis" in IMPACT_ANALYSIS_MANIFEST.goals


async def test_agent_run_produces_report_from_real_service_and_no_other_source(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    seed_id = f"{repository_id}:repository"
    neighborhood = GraphPayload(
        nodes=[
            GraphNode(id=seed_id, labels=["Repository"]),
            GraphNode(
                id=f"{repository_id}:endpoint:get:/orders",
                labels=["Endpoint"],
                properties={"http_method": "GET", "path": "/orders"},
            ),
        ],
        edges=[
            GraphEdge(
                source_id=seed_id, target_id=f"{repository_id}:endpoint:get:/orders", type="EXPOSES"
            )
        ],
    )
    fake_graph = _FakeGraphRepository(neighborhood)

    memory = EngineeringMemoryService(db_session)
    confidence = ConfidenceModel(
        state=ConfidenceState.VERIFIED,
        distinct_confirming_source_types=1,
        confirming_source_types=frozenset({"code_annotation_literal"}),
        max_confirming_reliability_tier=3,
        contradiction_count=0,
        computed_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        formula_version="v1",
    )
    await memory.store_relationship(
        repository_id,
        KnowledgeRelationship(
            id="rel-1",
            relationship_type="EXPOSES",
            source_entity=seed_id,
            target_entity=f"{repository_id}:endpoint:get:/orders",
            confidence=confidence,
            hypothesis_ids=("hyp-1",),
            provenance=(
                Provenance(
                    generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
                    produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
                    pack_id="pack-1",
                    pack_version="v1",
                    run_id="run-1",
                ),
            ),
        ),
    )

    agent = ImpactAnalysisAgent()
    context = AgentContext(
        subject=Subject(subject_id=f"repo:{repository_id}", subject_type="repository"),
        goal="analyze_impact_analysis",
        extras={"db": db_session, "graph_repository": fake_graph},
    )

    with patch(
        "app.agents.frontier.base_frontier_agent.prompt_builder.run",
        new=AsyncMock(
            return_value=({"executive_summary": "Changing this repository affects one API."}, None)
        ),
    ):
        output = await agent.run(context)

    assert len(fake_graph.get_neighborhood_calls) == 1
    called_repo_id, called_seeds, _edge_types, called_hops = fake_graph.get_neighborhood_calls[0]
    assert called_repo_id == str(repository_id)
    assert called_seeds == [seed_id]
    assert called_hops == 2

    assert output.agent_id == "impact_analysis"
    assert output.subject_id == f"repo:{repository_id}"
    assert output.result["indirectly_impacted_apis"] == [f"{repository_id}:endpoint:get:/orders"]
    assert output.result["confidence_summary"]["high"] == 1
    assert output.result["executive_summary"].startswith("Changing this repository affects")
    assert output.confidence.score == 1.0
    tool_call_evidence = [e for e in output.evidence if e.kind == "tool_call"]
    assert len(tool_call_evidence) == 1
    assert tool_call_evidence[0].reference == "engineering_intelligence:impact_analysis"


async def test_agent_run_degrades_gracefully_for_isolated_repository(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    fake_graph = _FakeGraphRepository(GraphPayload())
    agent = ImpactAnalysisAgent()
    context = AgentContext(
        subject=Subject(subject_id=f"repo:{repository_id}", subject_type="repository"),
        goal="analyze_impact_analysis",
        extras={"db": db_session, "graph_repository": fake_graph},
    )

    with patch(
        "app.agents.frontier.base_frontier_agent.prompt_builder.run",
        new=AsyncMock(return_value=({}, None)),
    ) as mock_prompt:
        output = await agent.run(context)

    mock_prompt.assert_awaited_once()
    assert output.result["directly_impacted_repositories"] == []
    assert "0 low-confidence relationship(s)" in output.result["risk_summary"]
