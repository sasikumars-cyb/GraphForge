"""GET /system/status — platform status aggregation, scoped per user."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indexing_job import IndexingJob
from app.models.repository import Repository

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "system-status-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada",
}
USER_B = {
    "email": "system-status-b@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Grace",
}

REPO_A = {
    "provider_repo_id": "9001",
    "owner": "ada",
    "name": "engine",
    "full_name": "ada/engine",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/engine",
}
REPO_B = {
    "provider_repo_id": "9002",
    "owner": "grace",
    "name": "compiler",
    "full_name": "grace/compiler",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/grace/compiler",
}


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


async def test_indexing_counts_are_scoped_per_user(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regression test: repositories_indexed/repositories_pending used to
    count every user's IndexingJob rows with no join back to
    Repository.user_id — user A's dashboard showed indexing progress that
    included user B's repositories. Only user A's completed job should be
    counted here, even though user B also has one."""
    token_a = await _register_and_get_token(db_client, USER_A)
    token_b = await _register_and_get_token(db_client, USER_B)

    select_a = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"repositories": [REPO_A]},
    )
    select_b = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"repositories": [REPO_B]},
    )
    repo_a_id = select_a.json()[0]["id"]
    repo_b_id = select_b.json()[0]["id"]

    # A's repo has a completed indexing job; B's has a running one — if the
    # bug were still present, A's status response would count both.
    db_session.add_all(
        [
            IndexingJob(id=uuid.uuid4(), repository_id=repo_a_id, status="completed"),
            IndexingJob(id=uuid.uuid4(), repository_id=repo_b_id, status="running"),
        ]
    )
    await db_session.flush()

    response = await db_client.get(
        "/api/v1/system/status", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert response.status_code == 200
    knowledge_base = response.json()["knowledge_base"]
    assert knowledge_base["repositories_tracked"] == 1
    assert knowledge_base["repositories_indexed"] == 1
    assert knowledge_base["repositories_pending"] == 0
