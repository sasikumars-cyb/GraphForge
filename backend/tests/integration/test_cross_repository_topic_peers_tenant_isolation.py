"""KAN-45 — regression test proving `find_cross_repository_topic_peers`
(and its two REST callers) never surfaces a component belonging to a
repository the caller doesn't own, even when two repositories - owned by
different users - happen to produce/consume a Kafka topic sharing the
exact same literal name.

Uses `Neo4jGraphRepository.replace_repository_graph` directly with
synthetic `KafkaTopic`/`Component` nodes rather than running the full
indexer - the vulnerability and the fix are both in the traversal query
itself, not in how a real repository gets indexed, so a hand-built graph
proves the same thing with far less setup. Real Neo4j and Postgres, no
mocks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.repository import Repository

pytestmark = pytest.mark.asyncio

SHARED_TOPIC_NAME = "order-events"

TENANT_A = {
    "email": "topic-tenant-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Tenant A",
}
TENANT_B = {
    "email": "topic-tenant-b@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Tenant B",
}


async def _register_and_login(db_client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    await db_client.post("/api/v1/auth/register", json=payload)
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _owner_user_id(db_client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    me = await db_client.get("/api/v1/auth/me", headers=headers)
    return uuid.UUID(me.json()["id"])


def _repo_with_topic_producer(repository_id: str, component_name: str) -> GraphPayload:
    """One `Component` producing to a `KafkaTopic` named `SHARED_TOPIC_NAME`
    - the minimal shape `find_cross_repository_topic_peers` matches against."""
    topic_id = f"{repository_id}:kafka-topic:{SHARED_TOPIC_NAME}"
    component_id = f"{repository_id}:component:{component_name}"
    return GraphPayload(
        nodes=[
            GraphNode(id=topic_id, labels=["KafkaTopic"], properties={"name": SHARED_TOPIC_NAME}),
            GraphNode(id=component_id, labels=["Component"], properties={"name": component_name}),
        ],
        edges=[
            GraphEdge(
                source_id=component_id,
                target_id=topic_id,
                type="PRODUCES_TO",
                properties={"method_name": "publish"},
            )
        ],
    )


@pytest.fixture
async def graphs_cleanup() -> AsyncGenerator[list[str], None]:
    """Yields a mutable list the test appends repository ids to; every one
    of them gets its Neo4j graph wiped at teardown, mirroring the existing
    `graph_repository` fixture pattern in `test_materializer_replay.py`."""
    repository_ids: list[str] = []
    yield repository_ids
    repo = Neo4jGraphRepository(get_driver())
    for repository_id in repository_ids:
        await repo.replace_repository_graph(repository_id, GraphPayload())


async def _tracked_repository(
    db_session: AsyncSession, owner_id: uuid.UUID, name: str
) -> Repository:
    repo = Repository(
        id=uuid.uuid4(),
        user_id=owner_id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        source="github",
        owner="acme",
        name=name,
        full_name=f"acme/{name}",
        default_branch="main",
        html_url=f"https://github.com/acme/{name}",
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def test_another_tenants_component_never_appears_on_a_topic_name_collision(
    db_client: AsyncClient,
    db_session: AsyncSession,
    graphs_cleanup: list[str],
) -> None:
    tenant_a_headers = await _register_and_login(db_client, TENANT_A)
    tenant_b_headers = await _register_and_login(db_client, TENANT_B)
    tenant_a_id = await _owner_user_id(db_client, tenant_a_headers)
    tenant_b_id = await _owner_user_id(db_client, tenant_b_headers)

    repo_a = await _tracked_repository(db_session, tenant_a_id, "tenant-a-repo")
    repo_b = await _tracked_repository(db_session, tenant_b_id, "tenant-b-repo")

    graph_repository = Neo4jGraphRepository(get_driver())
    graphs_cleanup.extend([str(repo_a.id), str(repo_b.id)])
    await graph_repository.replace_repository_graph(
        str(repo_a.id), _repo_with_topic_producer(str(repo_a.id), "TenantAProducer")
    )
    await graph_repository.replace_repository_graph(
        str(repo_b.id), _repo_with_topic_producer(str(repo_b.id), "TenantBProducer")
    )

    response = await db_client.get(
        "/api/v1/repositories/cross-repository-links", headers=tenant_a_headers
    )

    assert response.status_code == 200
    component_names = {link["component_name"] for link in response.json()}
    repository_ids = {link["repository_id"] for link in response.json()}
    # Tenant A's own producer may legitimately appear (self-match, see
    # find_cross_repository_topic_peers's docstring); tenant B's never may.
    assert "TenantBProducer" not in component_names
    assert str(repo_b.id) not in repository_ids


async def test_the_single_repository_endpoint_has_the_same_isolation(
    db_client: AsyncClient,
    db_session: AsyncSession,
    graphs_cleanup: list[str],
) -> None:
    tenant_a_headers = await _register_and_login(
        db_client, {**TENANT_A, "email": "topic-tenant-a2@example.com"}
    )
    tenant_b_headers = await _register_and_login(
        db_client, {**TENANT_B, "email": "topic-tenant-b2@example.com"}
    )
    tenant_a_id = await _owner_user_id(db_client, tenant_a_headers)
    tenant_b_id = await _owner_user_id(db_client, tenant_b_headers)

    repo_a = await _tracked_repository(db_session, tenant_a_id, "tenant-a-repo-2")
    repo_b = await _tracked_repository(db_session, tenant_b_id, "tenant-b-repo-2")

    graph_repository = Neo4jGraphRepository(get_driver())
    graphs_cleanup.extend([str(repo_a.id), str(repo_b.id)])
    await graph_repository.replace_repository_graph(
        str(repo_a.id), _repo_with_topic_producer(str(repo_a.id), "TenantAProducer2")
    )
    await graph_repository.replace_repository_graph(
        str(repo_b.id), _repo_with_topic_producer(str(repo_b.id), "TenantBProducer2")
    )

    response = await db_client.get(
        f"/api/v1/repositories/{repo_a.id}/cross-repository-links", headers=tenant_a_headers
    )

    assert response.status_code == 200
    component_names = {link["component_name"] for link in response.json()}
    assert "TenantBProducer2" not in component_names
