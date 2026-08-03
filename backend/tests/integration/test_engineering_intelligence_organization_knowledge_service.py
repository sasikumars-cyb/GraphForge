"""`OrganizationKnowledgeService.compose` against real Postgres. Confirms
it composes existing services (never parses text, never calls an LLM) and
that one failing request doesn't discard the rest."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.models.repository import Repository
from app.models.user import User
from app.services.engineering_intelligence.contracts import RepositoryProfile, ServiceRequest
from app.services.engineering_intelligence.organization_knowledge_service import compose

pytestmark = pytest.mark.asyncio


class _FakeGraphRepository(IGraphRepository):
    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        raise NotImplementedError

    async def get_full_graph(self, repository_id: str) -> GraphPayload:
        return GraphPayload()

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
        return GraphPayload()


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


async def test_compose_executes_requests_and_returns_results_in_order(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    fake_graph = _FakeGraphRepository()
    requests = [
        ServiceRequest(
            service="repository_profile", arguments={"repository_id": str(repository_id)}
        ),
        ServiceRequest(
            service="dependency_query", arguments={"repository_ids": [str(repository_id)]}
        ),
    ]

    answer = await compose(db_session, fake_graph, requests)

    assert answer.errors == ()
    assert len(answer.results) == 2
    assert isinstance(answer.results[0], RepositoryProfile)


async def test_compose_records_error_without_discarding_other_results(
    db_session: AsyncSession, repository_id: uuid.UUID
) -> None:
    fake_graph = _FakeGraphRepository()
    requests = [
        ServiceRequest(service="repository_profile", arguments={}),  # missing repository_id
        ServiceRequest(
            service="repository_profile", arguments={"repository_id": str(repository_id)}
        ),
    ]

    answer = await compose(db_session, fake_graph, requests)

    assert len(answer.results) == 2
    assert answer.results[0] is None
    assert isinstance(answer.results[1], RepositoryProfile)
    assert len(answer.errors) == 1
    assert "[0] repository_profile" in answer.errors[0]
