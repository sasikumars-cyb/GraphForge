"""GET /system/status — platform status aggregation, scoped per user.

`repositories_indexed`/`repositories_graph_missing`/`repositories_pending`
are all read from `GraphHealthService` (app.graph.health) now, the same
service Context Discovery reads — see that module's docstring for why a
completed `IndexingJob` and an actual Neo4j graph used to be treated as
interchangeable signals here, and weren't.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.indexing_job import IndexingJob

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


async def _write_real_graph(repository_id: str) -> None:
    """Writes an actual, minimal Neo4j graph for `repository_id` — what a
    real indexing run leaves behind (see app.indexer.graph.builder.
    build_graph, which always writes at least this root node). Tests that
    want `GraphHealthStatus.HEALTHY` need this; a Postgres-only
    `IndexingJob` row is no longer enough (see module docstring)."""
    graph_repository = Neo4jGraphRepository(get_driver())
    await graph_repository.replace_repository_graph(
        repository_id,
        GraphPayload(
            nodes=[
                GraphNode(
                    id=f"{repository_id}:repository",
                    labels=["Repository"],
                    properties={"repository_id": repository_id},
                )
            ]
        ),
    )


async def _clear_graph(repository_id: str) -> None:
    """Cleans up a graph written by `_write_real_graph` — same pattern the
    codebase already uses for real-Neo4j integration tests (e.g.
    test_cross_repo_linker.py); the Postgres side is rolled back
    automatically by `db_session`, but Neo4j is not."""
    graph_repository = Neo4jGraphRepository(get_driver())
    await graph_repository.replace_repository_graph(repository_id, GraphPayload())


async def test_indexing_counts_are_scoped_per_user(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regression test: repositories_indexed/repositories_pending used to
    count every user's IndexingJob rows with no join back to
    Repository.user_id — user A's dashboard showed indexing progress that
    included user B's repositories. Only user A's completed, graph-healthy
    repository should be counted here, even though user B also has one."""
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

    # A's repo has a completed indexing job AND a real graph (genuinely
    # healthy); B's has a running one — if the scoping bug were still
    # present, A's status response would count both.
    db_session.add_all(
        [
            IndexingJob(id=uuid.uuid4(), repository_id=repo_a_id, status="completed"),
            IndexingJob(id=uuid.uuid4(), repository_id=repo_b_id, status="running"),
        ]
    )
    await db_session.flush()
    await _write_real_graph(repo_a_id)
    try:
        response = await db_client.get(
            "/api/v1/system/status", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert response.status_code == 200
        knowledge_base = response.json()["knowledge_base"]
        assert knowledge_base["repositories_tracked"] == 1
        assert knowledge_base["repositories_indexed"] == 1
        assert knowledge_base["repositories_pending"] == 0
        assert knowledge_base["repositories_graph_missing"] == 0
    finally:
        await _clear_graph(repo_a_id)


async def test_completed_job_with_no_graph_reports_graph_missing_not_indexed(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The exact drift the Graph Health investigation found live: a
    repository whose indexing job completed in Postgres, but whose Neo4j
    graph is gone (or was never actually written). This must no longer be
    reported as "indexed" — it has its own, honest count instead."""
    token = await _register_and_get_token(db_client, USER_A)

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token}"},
        json={"repositories": [REPO_A]},
    )
    repo_id = select_response.json()[0]["id"]

    db_session.add(IndexingJob(id=uuid.uuid4(), repository_id=repo_id, status="completed"))
    await db_session.flush()
    # Deliberately no `_write_real_graph` call — Neo4j has nothing for
    # this repository, matching the live state found in the investigation.

    response = await db_client.get(
        "/api/v1/system/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    knowledge_base = response.json()["knowledge_base"]
    assert knowledge_base["repositories_tracked"] == 1
    assert knowledge_base["repositories_indexed"] == 0
    assert knowledge_base["repositories_graph_missing"] == 1
    assert knowledge_base["repositories_pending"] == 0


async def test_pending_job_with_no_graph_reports_indexing(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A repository with a pending/running job and no graph yet is
    "indexing", not "graph missing" — it was never healthy in the first
    place, so there's nothing to have lost."""
    token = await _register_and_get_token(db_client, USER_A)

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token}"},
        json={"repositories": [REPO_A]},
    )
    repo_id = select_response.json()[0]["id"]

    db_session.add(IndexingJob(id=uuid.uuid4(), repository_id=repo_id, status="running"))
    await db_session.flush()

    response = await db_client.get(
        "/api/v1/system/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    knowledge_base = response.json()["knowledge_base"]
    assert knowledge_base["repositories_tracked"] == 1
    assert knowledge_base["repositories_indexed"] == 0
    assert knowledge_base["repositories_pending"] == 1
    assert knowledge_base["repositories_graph_missing"] == 0


async def test_no_tracked_repositories_reports_all_zero_counts(
    db_client: AsyncClient,
) -> None:
    """A brand-new account with no tracked repositories at all — the
    "repository absent" case. Every count should be zero, not an error or
    a stale non-zero figure from an unrelated account."""
    token = await _register_and_get_token(db_client, USER_A)

    response = await db_client.get(
        "/api/v1/system/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    knowledge_base = response.json()["knowledge_base"]
    assert knowledge_base["repositories_tracked"] == 0
    assert knowledge_base["repositories_indexed"] == 0
    assert knowledge_base["repositories_pending"] == 0
    assert knowledge_base["repositories_graph_missing"] == 0


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
