"""GET /api/v1/architecture/summary (ADR 0023) — the org-scale summary
replacing ArchitecturePage.tsx's per-repository indexing-job fan-out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.models.repository import Repository

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "ada@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada",
}

REPO_ENGINE = {
    "provider_repo_id": "1001",
    "owner": "ada",
    "name": "engine",
    "full_name": "ada/engine",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/engine",
}
REPO_NOTES = {
    "provider_repo_id": "1002",
    "owner": "ada",
    "name": "notes",
    "full_name": "ada/notes",
    "private": True,
    "default_branch": "main",
    "html_url": "https://github.com/ada/notes",
}


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


async def test_no_tracked_repositories_returns_all_zeros(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.get("/api/v1/architecture/summary", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total_repositories"] == 0
    assert body["total_nodes"] == 0
    assert body["repositories"] == []
    assert body["domains"] == []
    assert body["unindexed_count"] == 0
    assert body["stale_count"] == 0


async def test_replaces_the_n_plus_one_with_real_per_repository_counts(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The one call this endpoint exists to make possible instead of N —
    both tracked repositories' node counts come back from a single
    request, no per-repository indexing-job fetch involved."""
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers=headers,
        json={"repositories": [REPO_ENGINE, REPO_NOTES]},
    )
    repos = {r["full_name"]: r["id"] for r in select_response.json()}
    engine_id, _notes_id = repos["ada/engine"], repos["ada/notes"]

    graph_repository = Neo4jGraphRepository(get_driver())
    await graph_repository.replace_repository_graph(
        engine_id,
        GraphPayload(
            nodes=[
                GraphNode(id=f"{engine_id}:s1", labels=["GraphNode", "Service"]),
                GraphNode(id=f"{engine_id}:s2", labels=["GraphNode", "Service"]),
                GraphNode(id=f"{engine_id}:t1", labels=["GraphNode", "KafkaTopic"]),
            ]
        ),
    )
    # notes: tracked but never indexed — zero nodes, no graph at all.

    # Mark `engine` as successfully indexed (what the "unindexed"/"stale"
    # distinction actually reads) — the endpoint reads
    # `Repository.last_indexed_at`, not indexing-job rows.
    result = await db_session.execute(select(Repository).where(Repository.id == engine_id))
    engine_repo = result.scalar_one()
    engine_repo.last_indexed_at = datetime.now(UTC)
    await db_session.flush()

    response = await db_client.get("/api/v1/architecture/summary", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total_repositories"] == 2
    assert body["total_nodes"] == 3
    assert body["unindexed_count"] == 1

    by_name = {r["full_name"]: r for r in body["repositories"]}
    assert by_name["ada/engine"]["node_count"] == 3
    assert by_name["ada/engine"]["node_counts_by_label"] == {"Service": 2, "KafkaTopic": 1}
    assert by_name["ada/engine"]["is_stale"] is False
    assert by_name["ada/notes"]["node_count"] == 0
    assert by_name["ada/notes"]["is_stale"] is True  # never indexed


async def test_stale_detection_uses_the_30_day_threshold(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE]}
    )
    engine_id = select_response.json()[0]["id"]

    result = await db_session.execute(select(Repository).where(Repository.id == engine_id))
    repo = result.scalar_one()
    repo.last_indexed_at = datetime.now(UTC) - timedelta(days=45)
    await db_session.flush()

    response = await db_client.get("/api/v1/architecture/summary", headers=headers)

    body = response.json()
    assert body["stale_count"] == 1
    assert body["repositories"][0]["is_stale"] is True
    assert body["unindexed_count"] == 0  # it WAS indexed, just a while ago


async def test_domain_grouping_via_patch_reflects_in_the_summary(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers=headers,
        json={"repositories": [REPO_ENGINE, REPO_NOTES]},
    )
    repos = {r["full_name"]: r["id"] for r in select_response.json()}

    patch_response = await db_client.patch(
        f"/api/v1/repositories/{repos['ada/engine']}", headers=headers, json={"domain": "Payments"}
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["domain"] == "Payments"

    response = await db_client.get("/api/v1/architecture/summary", headers=headers)
    body = response.json()

    by_domain = {d["domain"]: d for d in body["domains"]}
    assert by_domain["Payments"]["repository_count"] == 1
    assert by_domain[None]["repository_count"] == 1  # ada/notes, still ungrouped

    by_name = {r["full_name"]: r for r in body["repositories"]}
    assert by_name["ada/engine"]["domain"] == "Payments"
    assert by_name["ada/notes"]["domain"] is None


async def test_only_this_user_own_tracked_repositories_are_summarized(
    db_client: AsyncClient,
) -> None:
    """User-scoped, not admin-wide — the one deliberate deviation from the
    calibration/investigation-intelligence summary-endpoint precedent
    this otherwise mirrors."""
    token_a = await _register_and_get_token(db_client, USER_A)
    await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"repositories": [REPO_ENGINE]},
    )

    token_b = await _register_and_get_token(
        db_client,
        {
            "email": "grace@example.com",
            "password": "correct-horse-battery-staple",
            "full_name": "Grace",
        },
    )
    response = await db_client.get(
        "/api/v1/architecture/summary", headers={"Authorization": f"Bearer {token_b}"}
    )

    assert response.json()["total_repositories"] == 0


async def test_requires_authentication(db_client: AsyncClient) -> None:
    response = await db_client.get("/api/v1/architecture/summary")
    assert response.status_code == 401
