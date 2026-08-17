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
from app.schemas.ask import MAX_QUESTION_LENGTH
from app.services.ask_grounding import ASK_GROUNDING_RATE_LIMIT

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

        response = await db_client.post("/api/v1/ask", headers=headers, json={"question": "   "})

        assert response.status_code == 422

    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post("/api/v1/ask", json={"question": "anything"})
        assert response.status_code == 401


class TestTruncatedImpactIsHonestlyPresented:
    """M-2 — integration-level regression: this exercises `ground_impact`'s
    own `answer`/`summary` construction against a real blast radius, not
    just `build_impact_facts()` in isolation. The audit found the
    always-visible answer stating a bare, capped count ("12 downstream
    repositories may be affected") with no indication it was a sample —
    the caveat only lived in `why`, which the UI renders behind a
    collapsed "Why" disclosure."""

    async def test_a_blast_radius_larger_than_the_cap_says_more_than_not_an_exact_count(
        self, db_client: AsyncClient
    ) -> None:
        from app.services.ask_grounding import _MAX_AFFECTED_PER_KIND

        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_ingestion(db_client, headers)

        # More downstream repositories than the reporting cap — a real
        # blast radius the structured lists must bound but the visible
        # text must not silently understate.
        true_downstream_count = _MAX_AFFECTED_PER_KIND + 3
        graph_repository = Neo4jGraphRepository(get_driver())
        await graph_repository.replace_repository_graph(
            repo_id,
            GraphPayload(
                nodes=[
                    GraphNode(id=f"{repo_id}:repository", labels=["GraphNode", "Repository"]),
                    *[
                        GraphNode(id=f"other-{i}:repository", labels=["GraphNode", "Repository"])
                        for i in range(true_downstream_count)
                    ],
                ],
                edges=[
                    GraphEdge(
                        source_id=f"{repo_id}:repository",
                        target_id=f"other-{i}:repository",
                        type="DEPENDS_ON_REPOSITORY",
                    )
                    for i in range(true_downstream_count)
                ],
            ),
        )

        response = await db_client.post(
            "/api/v1/ask",
            headers=headers,
            json={"question": "What breaks if I change customer-ingestion?"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["impact"]["truncated"] is True
        assert len(body["impact"]["affected_repositories"]) == _MAX_AFFECTED_PER_KIND
        # The always-visible fields — never a bare, understated exact
        # count once the true total exceeds the cap.
        assert f"more than {_MAX_AFFECTED_PER_KIND}" in body["answer"]
        assert f"more than {_MAX_AFFECTED_PER_KIND}" in body["impact"]["summary"]
        assert str(true_downstream_count) not in body["answer"]

    async def test_a_blast_radius_within_the_cap_states_the_exact_count(
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
                    GraphNode(id="other-0:repository", labels=["GraphNode", "Repository"]),
                ],
                edges=[
                    GraphEdge(
                        source_id=f"{repo_id}:repository",
                        target_id="other-0:repository",
                        type="DEPENDS_ON_REPOSITORY",
                    )
                ],
            ),
        )

        response = await db_client.post(
            "/api/v1/ask",
            headers=headers,
            json={"question": "What breaks if I change customer-ingestion?"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["impact"]["truncated"] is False
        assert "more than" not in body["answer"]
        assert "1 downstream repository may be affected" in body["answer"]


class TestRequestLimits:
    """H-2 — the two OWASP-LLM10 controls the audit found missing: a
    server-side length cap and a per-user rate limit. Both must reject
    before any graph query or LLM call happens."""

    async def test_a_question_at_the_limit_is_accepted(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        question = "a" * MAX_QUESTION_LENGTH
        response = await db_client.post(
            "/api/v1/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": question},
        )
        assert response.status_code == 200

    async def test_a_question_one_character_over_the_limit_is_rejected(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        response = await db_client.post(
            "/api/v1/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "a" * (MAX_QUESTION_LENGTH + 1)},
        )
        assert response.status_code == 422

    async def test_a_very_large_question_is_rejected(self, db_client: AsyncClient) -> None:
        """The audit sent 400 KB and got a 201 plus a 33-second provider
        call."""
        token = await _register_and_get_token(db_client, USER_A)
        response = await db_client.post(
            "/api/v1/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "a" * 400_000},
        )
        assert response.status_code == 422

    async def test_conversation_endpoints_enforce_the_same_cap(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        response = await db_client.post(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "a" * 400_000},
        )
        assert response.status_code == 422

    async def test_exceeding_the_rate_limit_returns_429(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        codes = [
            (
                await db_client.post(
                    "/api/v1/ask", headers=headers, json={"question": "what depends on anything"}
                )
            ).status_code
            for _ in range(ASK_GROUNDING_RATE_LIMIT + 2)
        ]
        assert 429 in codes, "no request was rate limited"
        assert codes[0] == 200, "the first request must still succeed"

    async def test_ask_and_conversations_share_one_budget_not_two(
        self, db_client: AsyncClient
    ) -> None:
        """H-2 regression: `/ask` and `POST /conversations` used to be
        counted under independent keys (`ask:{user}` / `conversation_turn:
        {user}`), so a caller alternating between the two endpoints got
        ~2x the intended per-user throughput — each single limiter's own
        30/60s number was enforced, but the aggregate across the surface
        wasn't. Alternating strictly must exhaust the SAME budget as
        hitting either endpoint alone."""
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        codes: list[int] = []
        for i in range(ASK_GROUNDING_RATE_LIMIT + 2):
            if i % 2 == 0:
                response = await db_client.post(
                    "/api/v1/ask",
                    headers=headers,
                    json={"question": "what depends on anything"},
                )
            else:
                response = await db_client.post(
                    "/api/v1/conversations",
                    headers=headers,
                    json={"question": "what depends on anything else"},
                )
            codes.append(response.status_code)

        assert 429 in codes, (
            "alternating endpoints must still hit the shared budget within "
            f"{ASK_GROUNDING_RATE_LIMIT + 2} combined requests"
        )
        # The budget is exhausted at the SAME total request count as
        # hitting one endpoint alone would exhaust it — proves the second
        # endpoint provided no additional, independent quota.
        first_429 = codes.index(429)
        assert first_429 == ASK_GROUNDING_RATE_LIMIT, (
            f"expected the {ASK_GROUNDING_RATE_LIMIT + 1}th combined request to be the "
            f"first 429 (shared budget), got it at position {first_429 + 1}"
        )

    async def test_the_rate_limit_is_per_user(self, db_client: AsyncClient) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        for _ in range(ASK_GROUNDING_RATE_LIMIT + 2):
            await db_client.post(
                "/api/v1/ask", headers=headers_a, json={"question": "what depends on anything"}
            )

        token_b = await _register_and_get_token(
            db_client,
            {
                "email": "second-asker@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Second",
            },
        )
        response = await db_client.post(
            "/api/v1/ask",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"question": "what depends on anything"},
        )
        assert response.status_code == 200, "one user's budget must not consume another's"


class TestAskCrossUserIsolation:
    """The audit found 17 cross-user isolation suites elsewhere and none
    for `/ask` itself (`test_conversations_api.py` gained one for the
    conversational endpoint; this is the mirror for the single-shot one).
    Real HTTP path, real auth, no mocking of the boundary under test."""

    async def test_another_user_cannot_reach_a_repository_through_ask(
        self, db_client: AsyncClient
    ) -> None:
        """User A tracks and indexes a repository with a real graph; User
        B, who has never selected any repository, asks about it by exact
        name. The response must not resolve, answer, or otherwise surface
        anything about User A's repository."""
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        repo_id = await _select_ingestion(db_client, headers_a)

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

        token_b = await _register_and_get_token(
            db_client,
            {
                "email": "intruder@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Intruder",
            },
        )
        response = await db_client.post(
            "/api/v1/ask",
            headers={"Authorization": f"Bearer {token_b}"},
            json={
                "question": "What will be affected if we change the customer ingestion pipeline?"
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] != "answered"
        assert body["resolved_repository_id"] is None
        assert body["impact"] is None
        assert body["candidates"] == []

    async def test_an_exact_name_match_for_another_users_repository_does_not_resolve(
        self, db_client: AsyncClient
    ) -> None:
        """C-1's exact-name fast path is the one rule most likely to
        accidentally ignore tenant scoping if `_resolve_repository`'s own
        DB query ever regressed — naming User A's repository exactly must
        still fail to resolve for User B."""
        token_a = await _register_and_get_token(db_client, USER_A)
        await _select_ingestion(db_client, {"Authorization": f"Bearer {token_a}"})

        token_b = await _register_and_get_token(
            db_client,
            {
                "email": "intruder2@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Intruder Two",
            },
        )
        response = await db_client.post(
            "/api/v1/ask",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"question": "What breaks if I change customer-ingestion?"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] != "answered"
        assert body["resolved_repository_id"] is None
        assert body["resolved_repository_name"] is None

    async def test_the_response_never_contains_the_other_users_repository_details(
        self, db_client: AsyncClient
    ) -> None:
        """Whole-body check, not just the structured fields above: no
        trace of User A's repository name, full name, or id anywhere in
        what User B receives — covers `answer`/`why`/`evidence`/`candidates`
        prose as well as the structured fields."""
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        repo_id = await _select_ingestion(db_client, headers_a)

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

        token_b = await _register_and_get_token(
            db_client,
            {
                "email": "intruder3@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Intruder Three",
            },
        )
        response = await db_client.post(
            "/api/v1/ask",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"question": "What breaks if I change customer-ingestion?"},
        )

        assert response.status_code == 200
        body_text = response.text
        # Checks `full_name` ("ada/customer-ingestion") and the raw id, not
        # the bare repo `name` ("customer-ingestion") — User B's own
        # question text legitimately contains that substring (it's echoed
        # back via `AskResponse.question`), so asserting its absence would
        # be a false positive, not a leak check.
        assert str(REPO_INGESTION["full_name"]) not in body_text
        assert repo_id not in body_text
