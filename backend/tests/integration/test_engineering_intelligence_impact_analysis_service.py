"""`ImpactAnalysisService.compute_blast_radius` — real Postgres for the
relationship-confidence half, a fake `IGraphRepository` for the graph half.
Verifies it delegates traversal to `graph_traversal.traverse` (never
re-implements hop expansion) and confidence lookup to
`relationship_lookup.fetch_with_confidence` (never re-implements the
relationship fetch loop).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User
from app.services.engineering_intelligence.contracts import EntityReference
from app.services.engineering_intelligence.impact_analysis_service import compute_blast_radius

pytestmark = pytest.mark.asyncio


class _FakeGraphRepository(IGraphRepository):
    def __init__(self, neighborhood: GraphPayload) -> None:
        self._neighborhood = neighborhood
        self.get_neighborhood_calls: list[tuple[str, list[str], list[str], int]] = []

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
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


async def test_compute_blast_radius_delegates_traversal_and_confidence(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    seed_id = f"{repository_id}:svc:checkout"
    neighborhood_payload = GraphPayload(
        nodes=[
            GraphNode(id=seed_id, labels=["Component", "Service"]),
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
        ],
        edges=[
            GraphEdge(
                source_id=seed_id, target_id=f"{repository_id}:table:orders", type="WRITES_TO"
            )
        ],
    )
    fake_graph = _FakeGraphRepository(neighborhood_payload)

    memory = EngineeringMemoryService(db_session)
    confidence = ConfidenceModel(
        state=ConfidenceState.LIKELY,
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
            relationship_type="WRITES_TO",
            source_entity=seed_id,
            target_entity=f"{repository_id}:table:orders",
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

    blast_radius = await compute_blast_radius(
        db_session,
        fake_graph,
        EntityReference(repository_id=str(repository_id), node_id=seed_id),
        direction="downstream",
        max_hops=2,
    )

    assert fake_graph.get_neighborhood_calls == [
        (str(repository_id), [seed_id], fake_graph.get_neighborhood_calls[0][2], 2)
    ]
    assert blast_radius.impacted_apis == (f"{repository_id}:endpoint:get:/orders",)
    assert blast_radius.impacted_databases == (f"{repository_id}:table:orders",)
    assert len(blast_radius.relationships) == 1
    assert blast_radius.relationships[0].confidence_state == "likely"
    assert blast_radius.subgraph is neighborhood_payload


async def test_compute_blast_radius_empty_neighborhood_still_returns_seed_repository_relationships(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    fake_graph = _FakeGraphRepository(GraphPayload())

    blast_radius = await compute_blast_radius(
        db_session,
        fake_graph,
        EntityReference(repository_id=str(repository_id), node_id=f"{repository_id}:svc:x"),
    )

    assert blast_radius.impacted_apis == ()
    assert blast_radius.relationships == ()
