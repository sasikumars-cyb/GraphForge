"""POST /api/v1/ask — the Home page's "ask GraphForge anything" entry
point. See app.api.v1.routers.ask's own docstring for what this
deliberately does and doesn't attempt to answer itself.
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

REPO_INGESTION = {
    "provider_repo_id": "2001",
    "owner": "ada",
    "name": "customer-ingestion",
    "full_name": "ada/customer-ingestion",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/customer-ingestion",
}


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


async def _select_ingestion(db_client: AsyncClient, headers: dict[str, str]) -> str:
    select_response = await db_client.post(
        "/api/v1/repositories", headers=headers, json={"repositories": [REPO_INGESTION]}
    )
    return str(select_response.json()[0]["id"])


class TestAsk:
    async def test_impact_question_resolves_to_the_matching_repository(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_ingestion(db_client, headers)

        graph_repository = Neo4jGraphRepository(get_driver())
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=f"{repo_id}:repository", labels=["GraphNode", "Repository"]),
                    GraphNode(id=f"{repo_id}:transform", labels=["GraphNode", "Service"]),
                ],
                edges=[
                    GraphEdge(
                        source_id=f"{repo_id}:repository",
                        target_id=f"{repo_id}:transform",
                        type="CALLS",
                    )
                ],
            ),
        )

        response = await db_client.post(
            "/api/v1/ask",
            headers=headers,
            json={
                "question": "What will be affected if we change the customer ingestion pipeline?"
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        assert body["intent"] == "impact"
        assert body["resolved_repository_id"] == repo_id
        assert body["impact"] is not None
        assert body["impact"]["severity"] in {"low", "medium", "high"}
        assert any(a["kind"] == "explore_impact" for a in body["actions"])
        assert any(e["source"] == "Dependency Graph" for e in body["evidence"])

    async def test_dependency_question_uses_dependency_query_service(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        await _select_ingestion(db_client, headers)

        response = await db_client.post(
            "/api/v1/ask",
            headers=headers,
            json={"question": "Which repositories depend on customer-ingestion?"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        assert body["intent"] == "dependency"
        assert body["resolved_repository_id"] is not None

    async def test_unmatched_question_routes_to_investigation_instead_of_guessing(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        await _select_ingestion(db_client, headers)

        response = await db_client.post(
            "/api/v1/ask",
            headers=headers,
            json={"question": "What documentation explains this system?"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "route_to_investigation"
        assert body["intent"] == "general"
        assert body["resolved_repository_id"] is None

    async def test_empty_question_is_rejected(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/ask", headers=headers, json={"question": "   "}
        )

        assert response.status_code == 422

    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post("/api/v1/ask", json={"question": "anything"})
        assert response.status_code == 401
