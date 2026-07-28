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


async def test_keyless_provider_is_reported_configured_and_active(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: the provider list was a hardcoded openai/gemini/groq
    triple, so Bedrock — which needs no API key because it uses the AWS
    credential chain — was missing entirely. A working Bedrock install fell
    through to `providers[0]` (openai, unconfigured) and the Control Center
    reported "Degraded / AI Provider: none" while every agent run succeeded.

    The list is now derived from the provider registry, so this asserts the
    general property rather than Bedrock specifically: a keyless provider
    that is the configured `ai_provider` must report configured + active,
    carry a model, and leave the platform healthy.
    """
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AI_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-20250514")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    token = await _register_and_get_token(db_client, USER_A)
    try:
        response = await db_client.get(
            "/api/v1/system/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        body = response.json()

        by_name = {p["name"]: p for p in body["ai_providers"]}
        assert "bedrock" in by_name, "registry-declared providers must all be reported"

        assert body["ai_provider"]["name"] == "bedrock"
        assert body["ai_provider"]["active"] is True
        assert body["ai_provider"]["configured"] is True
        assert body["ai_provider"]["model"] == "us.anthropic.claude-sonnet-4-20250514"
        assert body["platform_status"] == "healthy"

        # An unconfigured provider is still listed, just not active.
        assert by_name["openai"]["active"] is False
        assert by_name["openai"]["configured"] is False
    finally:
        get_settings.cache_clear()
