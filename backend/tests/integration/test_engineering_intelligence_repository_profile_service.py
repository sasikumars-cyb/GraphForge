"""`RepositoryProfileService.get_profile` against a real Postgres
transaction for the evidence-pack half, and a fake `IGraphRepository` for
the graph-structure half (the interface boundary the platform already
injects at — see `app.graph.interfaces.IGraphRepository`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User
from app.services.engineering_intelligence.repository_profile_service import get_profile

pytestmark = pytest.mark.asyncio


class _FakeGraphRepository(IGraphRepository):
    def __init__(self, payload: GraphPayload) -> None:
        self._payload = payload

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        raise NotImplementedError

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
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


def _payload() -> GraphPayload:
    return GraphPayload(
        nodes=[
            GraphNode(
                id="repo-1:endpoint:get:/orders",
                labels=["Endpoint"],
                properties={"http_method": "GET", "path": "/orders"},
            ),
            GraphNode(
                id="repo-1:table:orders", labels=["DataTable"], properties={"name": "orders"}
            ),
            GraphNode(
                id="repo-1:topic:order-events",
                labels=["KafkaTopic"],
                properties={"name": "order-events"},
            ),
            GraphNode(
                id="repo-1:feign:billing",
                labels=["Component", "FeignClient"],
                properties={"name": "BillingClient"},
            ),
            GraphNode(
                id="repo-1:dep:commons",
                labels=["MavenDependency"],
                properties={"group_id": "org.apache", "artifact_id": "commons-lang3"},
            ),
        ],
        edges=[],
    )


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


async def test_get_profile_groups_nodes_by_category(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    fake_graph = _FakeGraphRepository(_payload())

    profile = await get_profile(db_session, fake_graph, repository_id)

    assert profile.apis == ("GET /orders",)
    assert profile.databases == ("orders",)
    assert profile.queues == ("order-events",)
    assert profile.integrations == ("BillingClient",)
    assert profile.dependencies == ("org.apache:commons-lang3",)
    assert "1 API(s)" in profile.architecture_summary


async def test_get_profile_incorporates_repository_evidence_narrative(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    fake_graph = _FakeGraphRepository(GraphPayload())
    memory = EngineeringMemoryService(db_session)
    item = EvidenceItem(
        id="evidence-1",
        kind="repository_readme",
        source_type="documentation",
        reliability_tier=2,
        reference=EvidenceReference(
            repository_id=str(repository_id), source_type="documentation", locator="README.md"
        ),
        raw_value="A checkout service handling order placement.",
        provenance=Provenance(
            generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
            produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
            pack_id="pack-1",
            pack_version="v1",
            run_id="run-1",
        ),
    )
    pack = EngineeringEvidencePack(
        id="pack-1",
        repository_id=str(repository_id),
        commit_sha="abc123",
        schema_version="v1",
        items=(item,),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
    )
    await memory.store_evidence_pack(repository_id, pack)

    profile = await get_profile(db_session, fake_graph, repository_id)

    assert "checkout service" in profile.architecture_summary


async def test_get_profile_returns_empty_categories_for_unindexed_repository(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    fake_graph = _FakeGraphRepository(GraphPayload())

    profile = await get_profile(db_session, fake_graph, repository_id)

    assert profile.apis == ()
    assert profile.databases == ()
    assert profile.queues == ()
    assert profile.integrations == ()
    assert profile.dependencies == ()
