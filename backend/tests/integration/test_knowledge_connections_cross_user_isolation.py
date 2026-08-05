"""KAN-33 — cross-user isolation for `/api/v1/knowledge/connections/*`,
specifically the one per-user-sensitive resource this router touches.

`knowledge.py` is intentionally almost entirely admin-shared: every
`KnowledgeConnection` row (Jira, Confluence, generic tool integrations)
has no `user_id` at all and is meant to be visible/editable by any admin
— that's the router's actual multi-tenancy model (org-wide integration
config, admin-gated), not a gap. Verified by code review; no isolation
test is written for that path because there is no ownership boundary to
prove.

The one exception is `GitHubConnection` — a real per-admin-user OAuth
row (`user_id`, unique per user; see `app/models/github_connection.py`).
`delete_connection` and `check_health` both fall back to it and
correctly gate on `github_row.user_id == current_user.id` before
touching it. This file proves that gate holds at the HTTP level: one
admin must not be able to delete or read the health of another admin's
personal GitHub connection.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github_connection import GitHubConnection
from app.models.user import User

pytestmark = pytest.mark.asyncio

ADMIN_A = {
    "email": "gh-admin-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Admin A",
}
ADMIN_B = {
    "email": "gh-admin-b@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Admin B",
}


async def _register_and_login(db_client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    await db_client.post("/api/v1/auth/register", json=payload)
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _promote_to_admin(db_session: AsyncSession, email: str) -> None:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = "admin"
    await db_session.commit()


async def _register_admin(
    db_client: AsyncClient, db_session: AsyncSession, payload: dict[str, str]
) -> dict[str, str]:
    headers = await _register_and_login(db_client, payload)
    await _promote_to_admin(db_session, payload["email"])
    return headers


@pytest.fixture
async def admin_a_headers(db_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    return await _register_admin(db_client, db_session, ADMIN_A)


@pytest.fixture
async def admin_b_headers(db_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    return await _register_admin(db_client, db_session, ADMIN_B)


@pytest.fixture
async def admin_a_id(db_client: AsyncClient, admin_a_headers: dict[str, str]) -> uuid.UUID:
    me = await db_client.get("/api/v1/auth/me", headers=admin_a_headers)
    return uuid.UUID(me.json()["id"])


@pytest.fixture
async def owned_github_connection(
    db_session: AsyncSession, admin_a_id: uuid.UUID
) -> AsyncGenerator[GitHubConnection, None]:
    row = GitHubConnection(
        id=uuid.uuid4(),
        user_id=admin_a_id,
        github_user_id="12345",
        github_username="admin-a-on-github",
        encrypted_access_token="not-a-real-token",
    )
    db_session.add(row)
    await db_session.flush()
    yield row


async def test_another_admin_cannot_delete_your_github_connection(
    db_client: AsyncClient,
    db_session: AsyncSession,
    admin_b_headers: dict[str, str],
    owned_github_connection: GitHubConnection,
) -> None:
    resp = await db_client.delete(
        f"/api/v1/knowledge/connections/{owned_github_connection.id}", headers=admin_b_headers
    )
    assert resp.status_code == 404
    still_there = await db_session.get(GitHubConnection, owned_github_connection.id)
    assert still_there is not None


async def test_owner_can_delete_their_own_github_connection(
    db_client: AsyncClient,
    db_session: AsyncSession,
    admin_a_headers: dict[str, str],
    owned_github_connection: GitHubConnection,
) -> None:
    resp = await db_client.delete(
        f"/api/v1/knowledge/connections/{owned_github_connection.id}", headers=admin_a_headers
    )
    assert resp.status_code == 204
    gone = await db_session.get(GitHubConnection, owned_github_connection.id)
    assert gone is None


async def test_another_admin_cannot_read_health_of_your_github_connection(
    db_client: AsyncClient,
    admin_b_headers: dict[str, str],
    owned_github_connection: GitHubConnection,
) -> None:
    resp = await db_client.post(
        f"/api/v1/knowledge/connections/{owned_github_connection.id}/health",
        headers=admin_b_headers,
    )
    assert resp.status_code == 404


async def test_owner_can_read_health_of_their_own_github_connection(
    db_client: AsyncClient,
    admin_a_headers: dict[str, str],
    owned_github_connection: GitHubConnection,
) -> None:
    resp = await db_client.post(
        f"/api/v1/knowledge/connections/{owned_github_connection.id}/health",
        headers=admin_a_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_non_admin_cannot_reach_the_knowledge_router_at_all(
    db_client: AsyncClient, owned_github_connection: GitHubConnection
) -> None:
    non_admin_headers = await _register_and_login(
        db_client,
        {
            "email": "non-admin-knowledge@example.com",
            "password": "correct-horse-battery-staple",
            "full_name": "Regular User",
        },
    )
    resp = await db_client.get("/api/v1/knowledge/connections", headers=non_admin_headers)
    assert resp.status_code == 403
