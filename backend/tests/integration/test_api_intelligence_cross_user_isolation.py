"""KAN-33 — cross-user isolation for
`/api/v1/api-intelligence/runs/{run_id}/export/{export_format}`.

`_load_completed_result` (`api_intelligence.py`) filters on
`Run.user_id == user.id` before anything else runs — no rendering, no
LLM call, no side effect happens until ownership is confirmed — so this
file only needs to prove the 404 path; there is nothing to trigger on
the success path worth a full round-trip test here (that's the
renderers' own concern, not an ownership one).
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
    "email": "api-intel-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "api-intel-intruder-b@example.com",
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
        goal="analyze_api_intelligence",
        status="completed",
        user_id=owner_id,
    )
    db_session.add(run)
    await db_session.flush()
    yield run


async def test_export_404s_for_another_users_run(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_run: Run
) -> None:
    resp = await db_client.get(
        f"/api/v1/api-intelligence/runs/{owned_run.id}/export/json", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_export_404s_for_a_nonexistent_run(
    db_client: AsyncClient, user_a_headers: dict[str, str]
) -> None:
    resp = await db_client.get(
        f"/api/v1/api-intelligence/runs/{uuid.uuid4()}/export/json", headers=user_a_headers
    )
    assert resp.status_code == 404


async def test_unauthenticated_requests_are_401(db_client: AsyncClient, owned_run: Run) -> None:
    resp = await db_client.get(f"/api/v1/api-intelligence/runs/{owned_run.id}/export/json")
    assert resp.status_code == 401
