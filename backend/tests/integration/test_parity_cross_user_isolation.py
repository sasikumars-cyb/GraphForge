"""KAN-33 — cross-user isolation for `/api/v1/repositories/{id}/parity`,
the one router the KAN-33 sweep report (`docs/reports/KAN-11-*`) left
"genuinely unverified — Neo4j-dependent, untestable in this sandbox".

This session has a real Neo4j reachable (the project's own dev-stack
container), closing that gap: `_get_owned_repository` (see
`app/api/v1/routers/parity.py`) raises `NotFoundError` (404) before
`run_parity_check`/`Neo4jGraphRepository` are ever reached, so the
negative case this file proves - another user gets a 404, not their
repository's real parity data or a 403 that would confirm the ID exists -
needs no indexed graph or Neo4j write at all. Same 404-not-403 pattern
already verified for `workflows.py`/`agent_runs.py`/`knowledge.py`/
`repositories.py` etc.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "parity-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Parity Owner A",
}
USER_B = {
    "email": "parity-intruder-b@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Parity Intruder B",
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
        github_repo_id="987654",
        source="github",
        owner="acme",
        name="parity-target",
        full_name="acme/parity-target",
        default_branch="main",
        html_url="https://github.com/acme/parity-target",
    )
    db_session.add(repo)
    await db_session.flush()
    yield repo


async def test_another_users_repository_parity_is_404_not_403(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_repository: Repository
) -> None:
    response = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/parity", headers=user_b_headers
    )

    # 404, not 403 - a 403 would confirm the repository ID exists, an
    # existence oracle the ownership check is specifically designed to
    # close (see workflows.py's own precedent for this pattern).
    assert response.status_code == 404


async def test_the_owner_can_reach_the_endpoint_past_the_ownership_check(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_repository: Repository
) -> None:
    # Never-indexed repository - proves the ownership gate is passed
    # (no 404) and the request reaches run_parity_check for real, without
    # requiring a full clone/parse/index pipeline in this test. What
    # run_parity_check itself does with an ungraphed repository is
    # covered by tests/{unit,integration}/test_parity_service.py, not
    # re-tested here.
    response = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/parity", headers=user_a_headers
    )

    assert response.status_code != 404
