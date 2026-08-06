"""`POST /pull-requests/{id}/analyze` and `GET /pull-requests/{id}/analysis`.

Uses the plain `client` fixture (real, committed `AsyncSessionLocal`)
rather than `db_client`'s rolled-back test transaction - the setup step
here triggers `POST /repositories/{id}/index`, which enqueues a durable job
(see `app.indexer.workers.index_worker`, `app.orchestrator.job_queue`) a
`Worker` runs against its *own* DB session, which would never see data
written inside a savepoint that's rolled back instead of committed (see
`test_indexing_api.py` for the same pattern). The analyze/analysis
endpoints themselves are synchronous (no background task), but they still
need the indexed repository's row to actually be committed and visible —
and, since KAN-18, need the indexing job to have actually *finished*, which
`POST .../index` returning 202 no longer implies (see
`_poll_indexing_job_until_terminal` below and `test_indexing_api.py`'s
module docstring for why: Starlette used to await a `BackgroundTasks`
callback within the response cycle itself, which is why no fixture here
ever needed to poll before).

`GitHubVersionControlProvider.list_changed_files` is patched at the class
level (same technique `test_github_oauth.py` uses for the OAuth provider)
so these hit our own routes/engine/DB/Neo4j for real, without a real
GitHub account or network call.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.database.session import AsyncSessionLocal
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.integrations.github import GitHubVersionControlProvider
from app.integrations.interfaces import ChangedFile
from app.models.pull_request import PullRequest
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def _poll_indexing_job_until_terminal(
    client: AsyncClient, repository_id: str, headers: dict[str, str], timeout_s: float = 10.0
) -> dict[str, object] | None:
    """Same helper as `test_indexing_api.py`'s own — duplicated rather than
    imported to keep these two files independently runnable/readable, the
    same trade-off `_poll_run_until_terminal`-style helpers already make
    across this test suite."""
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
async def indexed_repository_with_pr(
    client: AsyncClient, spring_boot_git_repo: Path
) -> AsyncGenerator[tuple[dict[str, str], str, str], None]:
    """A tracked, indexed repository (real clone + real Neo4j graph) with
    one PR row inserted directly (there's no HTTP endpoint to create a PR -
    normally that's the GitHub webhook's job). Cleans up the user (cascade
    deletes the repository/PR/analysis) and the Neo4j graph afterward."""
    email = f"impact-analyst-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"  # noqa: S106

    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Impact Analyst"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    select_response = await client.post(
        "/api/v1/repositories",
        headers=headers,
        json={
            "repositories": [
                {
                    "provider_repo_id": "api-test-1",
                    "owner": "local",
                    "name": "api-test-repo",
                    "full_name": "local/api-test-repo",
                    "private": False,
                    "default_branch": "main",
                    "html_url": str(spring_boot_git_repo),
                }
            ]
        },
    )
    repository_id = select_response.json()[0]["id"]

    index_response = await client.post(
        f"/api/v1/repositories/{repository_id}/index", headers=headers
    )
    assert index_response.status_code == 202
    finished_job = await _poll_indexing_job_until_terminal(client, repository_id, headers)
    assert finished_job is not None
    assert finished_job["status"] == "completed"

    async with AsyncSessionLocal() as session:
        pull_request = PullRequest(
            repository_id=uuid.UUID(repository_id),
            github_pr_id="9001",
            number=7,
            title="Change the producer",
            state="open",
            is_draft=False,
            author_login="tester",
            html_url="https://example.invalid/pr/7",
            head_ref="feature",
            head_sha="deadbeef",
            base_ref="main",
            github_created_at=datetime.now(UTC),
            github_updated_at=datetime.now(UTC),
        )
        session.add(pull_request)
        await session.commit()
        await session.refresh(pull_request)
        pull_request_id = str(pull_request.id)

    yield headers, repository_id, pull_request_id

    async with AsyncSessionLocal() as session:
        await session.execute(delete(User).where(User.email == email))
        await session.commit()
    await Neo4jGraphRepository(get_driver()).replace_repository_graph(repository_id, GraphPayload())


async def _register_and_get_token(client: AsyncClient, email: str) -> str:
    password = "correct-horse-battery-staple"  # noqa: S106
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Other User"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return str(login.json()["access_token"])


async def test_get_analysis_before_analyze_is_404(
    client: AsyncClient, indexed_repository_with_pr: tuple[dict[str, str], str, str]
) -> None:
    headers, _, pull_request_id = indexed_repository_with_pr

    response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/analysis", headers=headers
    )

    assert response.status_code == 404


async def test_analyze_then_read_persisted_analysis(
    client: AsyncClient, indexed_repository_with_pr: tuple[dict[str, str], str, str]
) -> None:
    headers, _, pull_request_id = indexed_repository_with_pr

    with patch.object(
        GitHubVersionControlProvider,
        "list_changed_files",
        AsyncMock(
            return_value=[
                ChangedFile(
                    path="src/main/java/com/example/orders/OrderEventProducer.java",
                    status="modified",
                )
            ]
        ),
    ):
        analyze_response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/analyze", headers=headers
        )

    assert analyze_response.status_code == 200
    body = analyze_response.json()
    assert body["risk"] == "HIGH"
    assert {n["name"] for n in body["directly_impacted_services"]} == {"OrderEventProducer"}
    assert {n["name"] for n in body["indirectly_impacted_services"]} == {"OrderEventListener"}
    assert len(body["dependency_paths"]) == 2

    read_response = await client.get(
        f"/api/v1/pull-requests/{pull_request_id}/analysis", headers=headers
    )
    assert read_response.status_code == 200
    assert read_response.json()["risk"] == "HIGH"
    assert read_response.json()["id"] == body["id"]


async def test_reanalyze_replaces_the_prior_analysis(
    client: AsyncClient, indexed_repository_with_pr: tuple[dict[str, str], str, str]
) -> None:
    headers, _, pull_request_id = indexed_repository_with_pr

    with patch.object(
        GitHubVersionControlProvider,
        "list_changed_files",
        AsyncMock(
            return_value=[
                ChangedFile(
                    path="src/main/java/com/example/orders/OrderDto.java", status="modified"
                )
            ]
        ),
    ):
        first = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/analyze", headers=headers
        )
    assert first.json()["risk"] == "LOW"

    with patch.object(
        GitHubVersionControlProvider,
        "list_changed_files",
        AsyncMock(return_value=[ChangedFile(path="pom.xml", status="modified")]),
    ):
        second = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/analyze", headers=headers
        )
    assert second.json()["risk"] == "HIGH"
    assert second.json()["id"] == first.json()["id"]


async def test_analyze_endpoint_404s_for_another_users_pull_request(
    client: AsyncClient, indexed_repository_with_pr: tuple[dict[str, str], str, str]
) -> None:
    _, _, pull_request_id = indexed_repository_with_pr
    other_email = f"someone-else-{uuid.uuid4().hex[:8]}@example.com"
    other_headers = {
        "Authorization": f"Bearer {await _register_and_get_token(client, other_email)}"
    }

    try:
        analyze_response = await client.post(
            f"/api/v1/pull-requests/{pull_request_id}/analyze", headers=other_headers
        )
        analysis_response = await client.get(
            f"/api/v1/pull-requests/{pull_request_id}/analysis", headers=other_headers
        )

        assert analyze_response.status_code == 404
        assert analysis_response.status_code == 404
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(User).where(User.email == other_email))
            await session.commit()


async def test_analyze_nonexistent_pull_request_is_404(client: AsyncClient) -> None:
    email = f"solo-{uuid.uuid4().hex[:8]}@example.com"
    headers = {"Authorization": f"Bearer {await _register_and_get_token(client, email)}"}

    try:
        response = await client.post(
            f"/api/v1/pull-requests/{uuid.uuid4()}/analyze", headers=headers
        )
        assert response.status_code == 404
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(User).where(User.email == email))
            await session.commit()


async def test_unauthenticated_requests_are_401(
    client: AsyncClient, indexed_repository_with_pr: tuple[dict[str, str], str, str]
) -> None:
    _, _, pull_request_id = indexed_repository_with_pr

    response = await client.get(f"/api/v1/pull-requests/{pull_request_id}/analysis")

    assert response.status_code == 401
