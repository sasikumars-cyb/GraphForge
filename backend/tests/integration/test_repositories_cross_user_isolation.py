"""KAN-33 — cross-user isolation for the Neo4j-free slice of
`/api/v1/repositories/*`.

Continues the router sweep (`workflows.py`, `agent_runs.py`,
`knowledge.py` — see those files' own docstrings) to `repositories.py`,
which uses the same `_get_owned_repository` ownership-check pattern.
Deliberately scoped to endpoints that never touch Neo4j
(`list_repositories`, `list_pull_requests`) — `remove_repository` and
every graph/architecture endpoint on this router call
`Neo4jGraphRepository`/`Neo4jImpactGraphReader` directly and cannot be
exercised in this sandbox (Docker registry blocked by org egress
policy), consistent with every other epic in this session that noted
Neo4j-dependent paths as unverified here rather than silently skipped.

Investigating this router's Neo4j-touching endpoints also surfaced a
real, separate finding — `find_cross_repository_topic_peers` has no
tenant filter at the graph-query level, filed as KAN-45 — which is a
different bug class than the ownership-check pattern this file verifies
(HTTP-layer 404-not-403 gating on Postgres-backed resources).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pull_request import PullRequest
from app.models.repository import Repository

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "repo-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "repo-intruder-b@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Intruder B",
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


@pytest.fixture
async def user_a_headers(db_client: AsyncClient) -> dict[str, str]:
    return await _register_and_login(db_client, USER_A)


@pytest.fixture
async def user_b_headers(db_client: AsyncClient) -> dict[str, str]:
    return await _register_and_login(db_client, USER_B)


@pytest.fixture
async def owned_repository(
    db_client: AsyncClient, db_session: AsyncSession, user_a_headers: dict[str, str]
) -> AsyncGenerator[Repository, None]:
    owner_id = await _owner_user_id(db_client, user_a_headers)
    repo = Repository(
        id=uuid.uuid4(),
        user_id=owner_id,
        github_repo_id="123456",
        source="github",
        owner="acme",
        name="widgets",
        full_name="acme/widgets",
        default_branch="main",
        html_url="https://github.com/acme/widgets",
    )
    db_session.add(repo)
    await db_session.flush()
    yield repo


async def test_list_repositories_never_includes_another_users_repository(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_repository: Repository
) -> None:
    resp = await db_client.get("/api/v1/repositories", headers=user_b_headers)
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()}
    assert str(owned_repository.id) not in ids


async def test_list_pull_requests_404s_for_another_users_repository(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_repository: Repository
) -> None:
    resp = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/pull-requests", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_list_pull_requests_succeeds_for_the_owner(
    db_client: AsyncClient,
    db_session: AsyncSession,
    user_a_headers: dict[str, str],
    owned_repository: Repository,
) -> None:
    now = datetime.now(UTC)
    pr = PullRequest(
        id=uuid.uuid4(),
        repository_id=owned_repository.id,
        github_pr_id="pr-1",
        number=1,
        title="Add a widget",
        state="open",
        author_login="octocat",
        html_url="https://github.com/acme/widgets/pull/1",
        head_ref="feature/widget",
        head_sha="abc123",
        base_ref="main",
        github_created_at=now,
        github_updated_at=now,
    )
    db_session.add(pr)
    await db_session.flush()

    resp = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/pull-requests", headers=user_a_headers
    )
    assert resp.status_code == 200
    numbers = {item["number"] for item in resp.json()}
    assert 1 in numbers


async def test_unauthenticated_requests_are_401(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/repositories")
    assert resp.status_code == 401
