"""GET /api/v1/repositories/{id}/impact — the fast, deterministic
blast-radius endpoint Impact Check's radial graph reads directly.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver

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


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


async def _select_engine(db_client: AsyncClient, headers: dict[str, str]) -> str:
    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ENGINE]}
    )
    return str(select_response.json()[0]["id"])


def _chain_graph(repository_id: str) -> GraphPayload:
    """seed --CALLS--> a --CALLS--> b --CALLS--> c, plus an unrelated
    node z with no path to any of them."""
    node_ids = ["seed", "a", "b", "c", "z"]
    nodes = [
        GraphNode(id=f"{repository_id}:{n}", labels=["GraphNode", "Component"], properties={"name": n})
        for n in node_ids
    ]
    edges = [
        GraphEdge(source_id=f"{repository_id}:{x}", target_id=f"{repository_id}:{y}", type="CALLS")
        for x, y in [("seed", "a"), ("a", "b"), ("b", "c")]
    ]
    return GraphPayload(nodes=nodes, edges=edges)


class TestBlastRadius:
    async def test_returns_nodes_with_hop_distance_within_max_hops(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_engine(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        await graph_repository.replace_repository_graph(repo_id, _chain_graph(repo_id))

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/impact",
            headers=headers,
            params={"node_id": f"{repo_id}:seed", "max_hops": 2},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["seed_node_id"] == f"{repo_id}:seed"
        assert body["max_hops"] == 2

        nodes_by_id = {n["id"]: n for n in body["graph"]["nodes"]}
        # seed, a (1 hop), b (2 hops) — c (3 hops) and z (unreachable) excluded.
        assert set(nodes_by_id) == {f"{repo_id}:seed", f"{repo_id}:a", f"{repo_id}:b"}
        assert nodes_by_id[f"{repo_id}:seed"]["properties"]["hop_distance"] == 0
        assert nodes_by_id[f"{repo_id}:a"]["properties"]["hop_distance"] == 1
        assert nodes_by_id[f"{repo_id}:b"]["properties"]["hop_distance"] == 2

    async def test_defaults_to_seeding_from_the_repository_s_own_node(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_engine(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=f"{repo_id}:repository", labels=["GraphNode", "Repository"]),
                    GraphNode(id=f"{repo_id}:svc", labels=["GraphNode", "Service"]),
                ],
                edges=[
                    GraphEdge(
                        source_id=f"{repo_id}:repository", target_id=f"{repo_id}:svc", type="CONTAINS"
                    )
                ],
            ),
        )

        response = await db_client.get(f"/api/v1/repositories/{repo_id}/impact", headers=headers)

        assert response.status_code == 200
        assert response.json()["seed_node_id"] == f"{repo_id}:repository"

    async def test_max_hops_is_bounded(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_engine(db_client, headers)

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/impact",
            headers=headers,
            params={"max_hops": 99},
        )

        assert response.status_code == 422  # ge=1, le=5

    async def test_empty_graph_returns_empty_result_not_an_error(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_engine(db_client, headers)

        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/impact",
            headers=headers,
            params={"node_id": f"{repo_id}:does-not-exist"},
        )

        assert response.status_code == 200
        assert response.json()["graph"]["nodes"] == []

    async def test_cannot_check_impact_on_another_users_repository(
        self, db_client: AsyncClient
    ) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        repo_id = await _select_engine(db_client, {"Authorization": f"Bearer {token_a}"})

        token_b = await _register_and_get_token(db_client, USER_B)
        response = await db_client.get(
            f"/api/v1/repositories/{repo_id}/impact",
            headers={"Authorization": f"Bearer {token_b}"},
        )

        assert response.status_code == 404

    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(
            "/api/v1/repositories/00000000-0000-0000-0000-000000000000/impact"
        )
        assert response.status_code == 401
