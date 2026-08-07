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


# ---------------------------------------------------------------------------
# ADR 0023 — PATCH domain, GET .../graph/types, GET .../graph/nodes/{id}/neighbors,
# GET .../graph?after=
# ---------------------------------------------------------------------------


async def _select_one(db_client: AsyncClient, headers: dict[str, str]) -> str:
    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE]}
    )
    return str(select_response.json()[0]["id"])


class TestUpdateRepositoryDomain:
    async def test_sets_the_domain(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        response = await db_client.patch(
            f"/api/v1/repositories/{repo_id}", headers=headers, json={"domain": "Payments"}
        )

        assert response.status_code == 200
        assert response.json()["domain"] == "Payments"

        listed = await db_client.get("/api/v1/repositories", headers=headers)
        # RepositoryResponse includes domain — set persists across a
        # fresh read, not just echoed back from the PATCH response.
        assert next(r for r in listed.json() if r["id"] == repo_id)["domain"] == "Payments"

    async def test_clears_the_domain_with_explicit_null(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)
        await db_client.patch(
            f"/api/v1/repositories/{repo_id}", headers=headers, json={"domain": "Payments"}
        )

        response = await db_client.patch(
            f"/api/v1/repositories/{repo_id}", headers=headers, json={"domain": None}
        )

        assert response.status_code == 200
        assert response.json()["domain"] is None

    async def test_rejects_an_empty_string(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        response = await db_client.patch(
            f"/api/v1/repositories/{repo_id}", headers=headers, json={"domain": "  "}
        )

        assert response.status_code == 400

    async def test_cannot_update_another_users_repository(self, db_client: AsyncClient) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        repo_id = await _select_one(db_client, {"Authorization": f"Bearer {token_a}"})

        token_b = await _register_and_get_token(db_client, USER_B)
        response = await db_client.patch(
            f"/api/v1/repositories/{repo_id}",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"domain": "Payments"},
        )

        assert response.status_code == 404


class TestGraphTypes:
    async def test_returns_real_counts_per_label(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=f"{repo_id}:s1", labels=["GraphNode", "Service"]),
                    GraphNode(id=f"{repo_id}:s2", labels=["GraphNode", "Service"]),
                    GraphNode(id=f"{repo_id}:t1", labels=["GraphNode", "KafkaTopic"]),
                ]
            ),
        )

        response = await db_client.get(f"/api/v1/repositories/{repo_id}/graph/types", headers=headers)

        assert response.status_code == 200
        assert response.json()["counts"] == {"Service": 2, "KafkaTopic": 1}

    async def test_empty_repository_returns_empty_counts(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        response = await db_client.get(f"/api/v1/repositories/{repo_id}/graph/types", headers=headers)

        assert response.status_code == 200
        assert response.json()["counts"] == {}


class TestGraphNodeNeighbors:
    async def test_returns_the_induced_neighborhood(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        a, b, c = f"{repo_id}:a", f"{repo_id}:b", f"{repo_id}:c"
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=a, labels=["GraphNode", "Component"]),
                    GraphNode(id=b, labels=["GraphNode", "Component"]),
                    GraphNode(id=c, labels=["GraphNode", "Component"]),
                ],
                edges=[
                    GraphEdge(source_id=a, target_id=b, type="CALLS"),
                    GraphEdge(source_id=b, target_id=c, type="CALLS"),
                ],
            ),
        )

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph/nodes/{a}/neighbors",
            headers=headers,
            params={"hops": 1},
        )

        assert response.status_code == 200
        body = response.json()
        node_ids = {n["id"] for n in body["nodes"]}
        assert node_ids == {a, b}  # c is 2 hops away, outside hops=1

    async def test_default_edge_types_exclude_cross_repository_types(
        self, db_client: AsyncClient
    ) -> None:
        """No `edge_types` supplied — defaults to every non-cross-repository
        type, since a UI click-to-expand interaction doesn't generally
        know which specific relationship types matter."""
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        node_id = f"{repo_id}:a"
        await graph_repository.replace_repository_graph(
            repo_id, GraphPayload(nodes=[GraphNode(id=node_id, labels=["GraphNode", "Component"])])
        )

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph/nodes/{node_id}/neighbors", headers=headers
        )

        assert response.status_code == 200
        assert {n["id"] for n in response.json()["nodes"]} == {node_id}

    async def test_unknown_edge_type_returns_400(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph/nodes/x/neighbors",
            headers=headers,
            params={"edge_types": ["NOT_A_REAL_TYPE"]},
        )

        assert response.status_code == 400

    async def test_direction_outgoing_follows_only_forward_edges(
        self, db_client: AsyncClient
    ) -> None:
        """The Dependency lens's own toggle: seeded from b in a->b->c,
        'outgoing' must reach c (what b depends on) but not a (what
        depends on b)."""
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        a, b, c = f"{repo_id}:a", f"{repo_id}:b", f"{repo_id}:c"
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=a, labels=["GraphNode", "Component"]),
                    GraphNode(id=b, labels=["GraphNode", "Component"]),
                    GraphNode(id=c, labels=["GraphNode", "Component"]),
                ],
                edges=[
                    GraphEdge(source_id=a, target_id=b, type="CALLS"),
                    GraphEdge(source_id=b, target_id=c, type="CALLS"),
                ],
            ),
        )

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph/nodes/{b}/neighbors",
            headers=headers,
            params={"hops": 1, "direction": "outgoing"},
        )

        assert response.status_code == 200
        assert {n["id"] for n in response.json()["nodes"]} == {b, c}

    async def test_direction_incoming_follows_only_backward_edges(
        self, db_client: AsyncClient
    ) -> None:
        """Same seed, opposite direction: 'incoming' must reach a (what
        depends on b) but not c (what b depends on)."""
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        a, b, c = f"{repo_id}:a", f"{repo_id}:b", f"{repo_id}:c"
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=a, labels=["GraphNode", "Component"]),
                    GraphNode(id=b, labels=["GraphNode", "Component"]),
                    GraphNode(id=c, labels=["GraphNode", "Component"]),
                ],
                edges=[
                    GraphEdge(source_id=a, target_id=b, type="CALLS"),
                    GraphEdge(source_id=b, target_id=c, type="CALLS"),
                ],
            ),
        )

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph/nodes/{b}/neighbors",
            headers=headers,
            params={"hops": 1, "direction": "incoming"},
        )

        assert response.status_code == 200
        assert {n["id"] for n in response.json()["nodes"]} == {a, b}

    async def test_direction_defaults_to_any_unchanged(self, db_client: AsyncClient) -> None:
        """No `direction` supplied — must match the original undirected
        behavior byte-for-byte, the same regression guard already applied
        to get_neighborhood itself."""
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        a, b, c = f"{repo_id}:a", f"{repo_id}:b", f"{repo_id}:c"
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=a, labels=["GraphNode", "Component"]),
                    GraphNode(id=b, labels=["GraphNode", "Component"]),
                    GraphNode(id=c, labels=["GraphNode", "Component"]),
                ],
                edges=[
                    GraphEdge(source_id=a, target_id=b, type="CALLS"),
                    GraphEdge(source_id=b, target_id=c, type="CALLS"),
                ],
            ),
        )

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph/nodes/{b}/neighbors",
            headers=headers,
            params={"hops": 1},
        )

        assert response.status_code == 200
        assert {n["id"] for n in response.json()["nodes"]} == {a, b, c}


class TestGraphCursorPagination:
    async def test_after_continues_from_the_previous_page(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_one(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        nodes = [
            GraphNode(id=f"{repo_id}:n{i}", labels=["GraphNode", "Component"]) for i in range(5)
        ]
        await graph_repository.replace_repository_graph(repo_id, GraphPayload(nodes=nodes))

        first = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph", headers=headers, params={"limit": 3}
        )
        assert first.json()["truncated"] is True
        cursor = first.json()["next_cursor"]
        assert cursor is not None

        second = await db_client.get(
            f"/api/v1/repositories/{repo_id}/graph",
            headers=headers,
            params={"limit": 3, "after": cursor},
        )

        assert second.status_code == 200
        first_ids = {n["id"] for n in first.json()["nodes"]}
        second_ids = {n["id"] for n in second.json()["nodes"]}
        assert first_ids.isdisjoint(second_ids)
        assert len(second_ids) == 2
        assert second.json()["truncated"] is False
