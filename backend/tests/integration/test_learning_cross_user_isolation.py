"""KAN-33 — cross-user isolation for `/api/v1/repositories/{repository_id}
/learning/*` (`learning.py`).

Continues the router sweep. `learning.py` uses the same
`_get_owned_repository` pattern already verified in `repositories.py`
and `learning.py`'s own `submit_feedback`/`list_events`/`get_statistics`
are all Postgres-only (`LearningEngineService`/`LearningEventRecord`
have no Neo4j dependency), so — unlike `parity.py`, which calls a
Neo4j-backed parity check and can't be exercised in this sandbox — this
router's full surface is testable here.
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
    "email": "learning-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "learning-intruder-b@example.com",
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
        github_repo_id="654321",
        source="github",
        owner="acme",
        name="gadgets",
        full_name="acme/gadgets",
        default_branch="main",
        html_url="https://github.com/acme/gadgets",
    )
    db_session.add(repo)
    await db_session.flush()
    yield repo


_FEEDBACK_BODY = {
    # "flag_weak_evidence" targets a relationship (so relationship_type/
    # source_entity/target_entity are required) but isn't a
    # _CORRECTION_KINDS member, so submit_feedback tolerates no prior
    # relationship history existing for it - unlike "approve"/"reject"/
    # "correct_confidence", which 404 when there's nothing to correct.
    # Keeps this fixture testing ownership, not graph state that would
    # need a real indexed repository to exist.
    "kind": "flag_weak_evidence",
    "reason": "Evidence for this relationship looked thin.",
    "relationship_type": "CALLS_SERVICE",
    "source_entity": "OrderService",
    "target_entity": "PaymentService",
}


async def test_submit_feedback_404s_for_another_users_repository(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_repository: Repository
) -> None:
    resp = await db_client.post(
        f"/api/v1/repositories/{owned_repository.id}/learning/feedback",
        json=_FEEDBACK_BODY,
        headers=user_b_headers,
    )
    assert resp.status_code == 404


async def test_submit_feedback_succeeds_for_the_owner(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_repository: Repository
) -> None:
    resp = await db_client.post(
        f"/api/v1/repositories/{owned_repository.id}/learning/feedback",
        json=_FEEDBACK_BODY,
        headers=user_a_headers,
    )
    assert resp.status_code == 200


async def test_list_events_404s_for_another_users_repository(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_repository: Repository
) -> None:
    resp = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/learning/events", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_list_events_succeeds_for_the_owner_and_reflects_submitted_feedback(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_repository: Repository
) -> None:
    await db_client.post(
        f"/api/v1/repositories/{owned_repository.id}/learning/feedback",
        json=_FEEDBACK_BODY,
        headers=user_a_headers,
    )
    resp = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/learning/events", headers=user_a_headers
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_get_statistics_404s_for_another_users_repository(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_repository: Repository
) -> None:
    resp = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/learning/statistics", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_get_statistics_succeeds_for_the_owner(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_repository: Repository
) -> None:
    resp = await db_client.get(
        f"/api/v1/repositories/{owned_repository.id}/learning/statistics", headers=user_a_headers
    )
    assert resp.status_code == 200


async def test_unauthenticated_requests_are_401(
    db_client: AsyncClient, owned_repository: Repository
) -> None:
    resp = await db_client.get(f"/api/v1/repositories/{owned_repository.id}/learning/statistics")
    assert resp.status_code == 401
