"""`POST /repositories/{id}/index` and the three graph-read endpoints.

Uses the plain `client` fixture (real, committed `AsyncSessionLocal`) rather
than `db_client`'s rolled-back test transaction: `POST .../index` schedules
a durable job (see `app.indexer.workers.index_worker`,
`app.orchestrator.job_queue`) that a `Worker` claims and runs against its
*own* DB session, which would never see data written inside a savepoint
that's rolled back instead of committed. Every row created here is cleaned
up explicitly in the `registered_repository` fixture's teardown instead.

As of KAN-18, `POST .../index` only enqueues — it no longer runs indexing
synchronously within the request/response cycle the way FastAPI's
`BackgroundTasks` used to (Starlette awaits a `BackgroundTasks` callback as
part of sending the response, which is why no test here ever needed to poll
before). `_poll_indexing_job_until_terminal` below is the same
poll-for-a-terminal-state discipline `test_agent_orchestrator_api.py`
already established for agent runs, applied here for the same reason.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from app.database.session import AsyncSessionLocal
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.indexing_job import IndexingJob
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def _poll_indexing_job_until_terminal(
    client: AsyncClient, repository_id: str, headers: dict[str, str], timeout_s: float = 10.0
) -> dict[str, object] | None:
    """Poll GET .../index until the latest job leaves pending/running, or
    return `None` if no job was ever triggered (a real, valid outcome for
    fixture teardown when a test never actually called POST .../index —
    see `test_get_latest_indexing_job_404s_before_any_run`)."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        response = await client.get(f"/api/v1/repositories/{repository_id}/index", headers=headers)
        if response.status_code == 404:
            return None
        body: dict[str, object] = response.json()
        if body["status"] not in ("pending", "running"):
            return body
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(
                f"Indexing job for repository {repository_id} did not finish in time"
            )
        await asyncio.sleep(0.05)


@pytest.fixture
async def registered_repository(
    client: AsyncClient, spring_boot_git_repo: Path
) -> AsyncGenerator[tuple[dict[str, str], str], None]:
    email = f"indexer-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S106

    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Indexer Test"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    select_response = await client.post(
        "/api/v1/repositories",
        headers=headers,
        json={
            "repositories": [
                {
                    "provider_repo_id": "smoke-1",
                    "owner": "local",
                    "name": "smoke-repo",
                    "full_name": "local/smoke-repo",
                    "private": False,
                    "default_branch": "main",
                    "html_url": str(spring_boot_git_repo),
                }
            ]
        },
    )
    repository_id = select_response.json()[0]["id"]

    yield headers, repository_id

    # Wait for any indexing job this test triggered to actually reach a
    # terminal state before deleting rows out from under it — see module
    # docstring. Without this, a still-in-flight worker task can find its
    # Repository/IndexingJob row already gone (or racing a concurrent
    # delete), a real timing hazard the old synchronous BackgroundTasks
    # execution never exposed.
    await _poll_indexing_job_until_terminal(client, repository_id, headers)

    async with AsyncSessionLocal() as session:
        await session.execute(delete(User).where(User.email == email))
        await session.commit()
    await Neo4jGraphRepository(get_driver()).replace_repository_graph(repository_id, GraphPayload())


async def test_trigger_index_then_read_graph_services_dependencies(
    client: AsyncClient, registered_repository: tuple[dict[str, str], str]
) -> None:
    headers, repository_id = registered_repository

    trigger_response = await client.post(
        f"/api/v1/repositories/{repository_id}/index", headers=headers
    )
    assert trigger_response.status_code == 202
    assert trigger_response.json()["status"] == "pending"
    assert trigger_response.json()["repository_id"] == repository_id

    finished = await _poll_indexing_job_until_terminal(client, repository_id, headers)
    assert finished is not None
    assert finished["status"] == "completed"

    graph_response = await client.get(
        f"/api/v1/repositories/{repository_id}/graph", headers=headers
    )
    assert graph_response.status_code == 200
    graph = graph_response.json()
    node_label_sets = {tuple(sorted(n["labels"])) for n in graph["nodes"]}
    assert ("Component", "Controller", "GraphNode") in node_label_sets
    assert len(graph["edges"]) > 0

    services_response = await client.get(
        f"/api/v1/repositories/{repository_id}/services", headers=headers
    )
    assert services_response.status_code == 200
    service_names = {n["properties"]["name"] for n in services_response.json()["nodes"]}
    # Every "Component"-labelled node: the Controller, the Service, the
    # FeignClient, and the two Kafka classes (plain @Component, not a
    # Controller/Service/FeignClient - see app.indexer.graph.builder).
    assert service_names == {
        "OrderController",
        "OrderService",
        "PaymentClient",
        "OrderEventProducer",
        "OrderEventListener",
    }

    dependencies_response = await client.get(
        f"/api/v1/repositories/{repository_id}/dependencies", headers=headers
    )
    assert dependencies_response.status_code == 200
    artifact_ids = {n["properties"]["artifact_id"] for n in dependencies_response.json()["nodes"]}
    assert "spring-boot-starter-web" in artifact_ids


async def test_get_latest_indexing_job_after_trigger(
    client: AsyncClient, registered_repository: tuple[dict[str, str], str]
) -> None:
    headers, repository_id = registered_repository

    await client.post(f"/api/v1/repositories/{repository_id}/index", headers=headers)

    body = await _poll_indexing_job_until_terminal(client, repository_id, headers)
    assert body is not None
    assert body["repository_id"] == repository_id
    assert body["status"] == "completed"
    assert body["finished_at"] is not None


async def test_get_latest_indexing_job_404s_before_any_run(
    client: AsyncClient, registered_repository: tuple[dict[str, str], str]
) -> None:
    headers, repository_id = registered_repository

    response = await client.get(f"/api/v1/repositories/{repository_id}/index", headers=headers)
    assert response.status_code == 404


async def test_conflict_when_a_job_is_already_pending_or_running(
    client: AsyncClient, registered_repository: tuple[dict[str, str], str]
) -> None:
    headers, repository_id = registered_repository

    async with AsyncSessionLocal() as session:
        session.add(IndexingJob(repository_id=uuid.UUID(repository_id), status="running"))
        await session.commit()

    response = await client.post(f"/api/v1/repositories/{repository_id}/index", headers=headers)

    assert response.status_code == 409

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(IndexingJob).where(IndexingJob.repository_id == uuid.UUID(repository_id))
        )
        await session.commit()


async def test_index_endpoint_404s_for_another_users_repository(
    client: AsyncClient, registered_repository: tuple[dict[str, str], str]
) -> None:
    _, repository_id = registered_repository

    other_email = f"other-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S106
    await client.post(
        "/api/v1/auth/register",
        json={"email": other_email, "password": password, "full_name": "Other User"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": other_email, "password": password}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    try:
        for path in ("index", "graph", "services", "dependencies"):
            method = client.post if path == "index" else client.get
            response = await method(
                f"/api/v1/repositories/{repository_id}/{path}", headers=other_headers
            )
            assert response.status_code == 404
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(User).where(User.email == other_email))
            await session.commit()


async def test_unsupported_repository_marks_job_failed(
    client: AsyncClient, unsupported_git_repo: Path
) -> None:
    email = f"unsupported-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S106
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Unsupported Test"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    select_response = await client.post(
        "/api/v1/repositories",
        headers=headers,
        json={
            "repositories": [
                {
                    "provider_repo_id": "unsupported-1",
                    "owner": "local",
                    "name": "unsupported-repo",
                    "full_name": "local/unsupported-repo",
                    "private": False,
                    "default_branch": "main",
                    "html_url": str(unsupported_git_repo),
                }
            ]
        },
    )
    repository_id = select_response.json()[0]["id"]

    try:
        await client.post(f"/api/v1/repositories/{repository_id}/index", headers=headers)
        await _poll_indexing_job_until_terminal(client, repository_id, headers)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(IndexingJob).where(IndexingJob.repository_id == uuid.UUID(repository_id))
            )
            job = result.scalar_one()
            assert job.status == "failed"
            assert job.error_message is not None
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(User).where(User.email == email))
            await session.commit()
