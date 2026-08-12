"""POST /api/v1/conversations, POST /api/v1/conversations/{id}/messages —
the Home page's conversational investigation loop. See
app.services.conversation_service's own docstring for what's grounded vs.
reasoned each turn.
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
    "provider_repo_id": "3001",
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


class TestConversations:
    async def test_starting_a_conversation_grounds_the_first_turn(
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
            "/api/v1/conversations",
            headers=headers,
            json={
                "question": "What will be affected if we change the customer ingestion pipeline?"
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][1]["role"] == "assistant"
        payload = body["messages"][1]["payload"]
        assert payload is not None
        assert payload["resolved_repository_id"] == repo_id
        assert payload["intent"] == "impact"
        return body["id"]

    async def test_follow_up_reuses_the_resolved_repository_without_repeating_it(
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

        start = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "What breaks if we change customer-ingestion?"},
        )
        conversation_id = start.json()["id"]

        follow_up = await db_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"message": "What should I test?"},
        )

        assert follow_up.status_code == 200
        body = follow_up.json()
        assert len(body["messages"]) == 4
        last = body["messages"][-1]
        assert last["role"] == "assistant"
        assert last["content"].strip() != ""
        # A pure follow-up must not re-run the deterministic grounding —
        # its intent is "reasoning" (LLM available) or a "general"
        # degraded fallback (LLM call failed, e.g. rate-limited) — never
        # a fresh "impact"/"dependency" re-grounding. Either way, the
        # already-resolved repository must carry forward without the
        # user repeating it.
        assert last["payload"]["intent"] in {"reasoning", "general"}
        assert last["payload"]["resolved_repository_id"] == repo_id

    async def test_conversation_is_scoped_to_its_owner(self, db_client: AsyncClient) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        start = await db_client.post(
            "/api/v1/conversations", headers=headers_a, json={"question": "hello"}
        )
        conversation_id = start.json()["id"]

        token_b = await _register_and_get_token(
            db_client,
            {"email": "grace@example.com", "password": "correct-horse-battery-staple", "full_name": "Grace"},
        )
        headers_b = {"Authorization": f"Bearer {token_b}"}

        response = await db_client.get(
            f"/api/v1/conversations/{conversation_id}", headers=headers_b
        )
        assert response.status_code == 404

    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post("/api/v1/conversations", json={"question": "anything"})
        assert response.status_code == 401

    async def test_list_recent_returns_most_recently_active_first(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        first = await db_client.post(
            "/api/v1/conversations", headers=headers, json={"question": "first investigation"}
        )
        second = await db_client.post(
            "/api/v1/conversations", headers=headers, json={"question": "second investigation"}
        )
        # Touch the first conversation again — it should now sort ahead of
        # the second, which hasn't been touched since it was created.
        await db_client.post(
            f"/api/v1/conversations/{first.json()['id']}/messages",
            headers=headers,
            json={"message": "a follow-up"},
        )

        response = await db_client.get("/api/v1/conversations?limit=5", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["id"] == first.json()["id"]
        assert body[1]["id"] == second.json()["id"]
        assert "messages" not in body[0]
