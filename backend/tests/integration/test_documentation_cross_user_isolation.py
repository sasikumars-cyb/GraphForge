"""KAN-33 — cross-user isolation for
`/api/v1/documentation/runs/{run_id}/create-pr`.

`_load_completed_result` (`documentation.py`) filters on
`Run.user_id == user.id` before any GitHub write is attempted — same
shape as `api_intelligence.py`'s equivalent — so this file only needs
to prove the 404 path; the real GitHub PR-creation call never runs
until ownership is confirmed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "docs-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "docs-intruder-b@example.com",
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
async def owned_run(
    db_client: AsyncClient, db_session: AsyncSession, user_a_headers: dict[str, str]
) -> AsyncGenerator[Run, None]:
    owner_id = await _owner_user_id(db_client, user_a_headers)
    run = Run(
        id=uuid.uuid4(),
        subject_id="acme/widgets",
        subject_type="repository",
        goal="review_documentation",
        status="completed",
        user_id=owner_id,
    )
    db_session.add(run)
    await db_session.flush()
    yield run


async def test_create_pr_404s_for_another_users_run(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_run: Run
) -> None:
    resp = await db_client.post(
        f"/api/v1/documentation/runs/{owned_run.id}/create-pr", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_create_pr_404s_for_a_nonexistent_run(
    db_client: AsyncClient, user_a_headers: dict[str, str]
) -> None:
    resp = await db_client.post(
        f"/api/v1/documentation/runs/{uuid.uuid4()}/create-pr", headers=user_a_headers
    )
    assert resp.status_code == 404


async def test_unauthenticated_requests_are_401(db_client: AsyncClient, owned_run: Run) -> None:
    resp = await db_client.post(f"/api/v1/documentation/runs/{owned_run.id}/create-pr")
    assert resp.status_code == 401
