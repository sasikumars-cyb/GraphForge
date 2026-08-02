"""Selecting/persisting repositories and listing their pull requests."""

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services.indexing_service import index_repository

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "ada@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada",
}
USER_B = {
    "email": "grace@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Grace",
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


async def test_selecting_repositories_persists_metadata(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE, REPO_NOTES]}
    )

    assert response.status_code == 200
    full_names = {repo["full_name"] for repo in response.json()}
    assert full_names == {"ada/engine", "ada/notes"}

    listed = await db_client.get("/api/v1/repositories", headers=headers)
    assert {repo["full_name"] for repo in listed.json()} == {"ada/engine", "ada/notes"}


async def test_resubmitting_a_smaller_selection_untracks_the_rest(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE, REPO_NOTES]}
    )
    response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE]}
    )

    assert response.status_code == 200
    assert [repo["full_name"] for repo in response.json()] == ["ada/engine"]


async def test_repositories_are_scoped_per_user(db_client: AsyncClient) -> None:
    token_a = await _register_and_get_token(db_client, USER_A)
    token_b = await _register_and_get_token(db_client, USER_B)

    await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"repositories": [REPO_ENGINE]},
    )

    response_b = await db_client.get(
        "/api/v1/repositories", headers={"Authorization": f"Bearer {token_b}"}
    )

    assert response_b.json() == []


async def test_pull_requests_for_a_freshly_selected_repo_is_empty(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE]}
    )
    repo_id = select_response.json()[0]["id"]

    response = await db_client.get(f"/api/v1/repositories/{repo_id}/pull-requests", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_pull_requests_endpoint_404s_for_another_users_repository(
    db_client: AsyncClient,
) -> None:
    token_a = await _register_and_get_token(db_client, USER_A)
    token_b = await _register_and_get_token(db_client, USER_B)

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"repositories": [REPO_ENGINE]},
    )
    repo_id = select_response.json()[0]["id"]

    response = await db_client.get(
        f"/api/v1/repositories/{repo_id}/pull-requests",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 404


async def test_remove_repository_deletes_it_and_clears_its_graph(
    db_client: AsyncClient, spring_boot_git_repo: Path
) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers=headers,
        json={"repositories": [{**REPO_ENGINE, "html_url": str(spring_boot_git_repo)}]},
    )
    repo_id = select_response.json()[0]["id"]

    await index_repository(repository_id=repo_id, html_url=str(spring_boot_git_repo), ref="main")
    assert await Neo4jGraphRepository(get_driver()).has_graph(repo_id) is True

    response = await db_client.delete(f"/api/v1/repositories/{repo_id}", headers=headers)

    assert response.status_code == 204
    assert await Neo4jGraphRepository(get_driver()).has_graph(repo_id) is False

    listed = await db_client.get("/api/v1/repositories", headers=headers)
    assert listed.json() == []

    prs = await db_client.get(f"/api/v1/repositories/{repo_id}/pull-requests", headers=headers)
    assert prs.status_code == 404


async def test_remove_repository_404s_for_another_users_repository(
    db_client: AsyncClient,
) -> None:
    token_a = await _register_and_get_token(db_client, USER_A)
    token_b = await _register_and_get_token(db_client, USER_B)

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"repositories": [REPO_ENGINE]},
    )
    repo_id = select_response.json()[0]["id"]

    response = await db_client.delete(
        f"/api/v1/repositories/{repo_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 404

    still_listed = await db_client.get(
        "/api/v1/repositories", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert len(still_listed.json()) == 1


async def test_cross_repository_edges_endpoint_returns_structural_edges(
    db_client: AsyncClient,
) -> None:
    """Regression test for the Architecture page's missing dependency
    graph: `cross_repo_linker.relink_account` computes real CALLS_SERVICE/
    SHARES_TOPIC/DEPENDS_ON_REPOSITORY edges between Repository nodes
    (`replace_cross_repository_edges`), but no endpoint ever read them back
    - `GET /repositories/cross-repository-edges` is that endpoint."""
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    select_response = await db_client.post(
        "/api/v1/repositories",
        headers=headers,
        json={"repositories": [REPO_ENGINE, REPO_NOTES]},
    )
    repos = {r["full_name"]: r["id"] for r in select_response.json()}
    engine_id, notes_id = repos["ada/engine"], repos["ada/notes"]

    graph_repository = Neo4jGraphRepository(get_driver())
    # `_write_edges` MATCHes both endpoints rather than MERGE-ing them, so
    # each repository's own Repository node must exist first - normally
    # written by `replace_repository_graph` during indexing.
    await graph_repository.replace_repository_graph(
        engine_id,
        GraphPayload(nodes=[GraphNode(id=f"{engine_id}:repository", labels=["Repository"])], edges=[]),
    )
    await graph_repository.replace_repository_graph(
        notes_id,
        GraphPayload(nodes=[GraphNode(id=f"{notes_id}:repository", labels=["Repository"])], edges=[]),
    )
    await graph_repository.replace_cross_repository_edges(
        engine_id,
        [
            GraphEdge(
                source_id=f"{engine_id}:repository",
                target_id=f"{notes_id}:repository",
                type="CALLS_SERVICE",
                properties={"confidence": "structural"},
            )
        ],
    )

    response = await db_client.get("/api/v1/repositories/cross-repository-edges", headers=headers)

    assert response.status_code == 200
    edges = response.json()
    assert len(edges) == 1
    assert edges[0]["source_id"] == f"{engine_id}:repository"
    assert edges[0]["target_id"] == f"{notes_id}:repository"
    assert edges[0]["type"] == "CALLS_SERVICE"


async def test_remove_repository_requires_authentication(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    select_response = await db_client.post(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {token}"},
        json={"repositories": [REPO_ENGINE]},
    )
    repo_id = select_response.json()[0]["id"]

    response = await db_client.delete(f"/api/v1/repositories/{repo_id}")

    assert response.status_code == 401
