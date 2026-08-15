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


async def _seed_minimal_graph(repo_id: str) -> None:
    """Enough graph for a blast radius to resolve and produce real facts."""
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
            {
                "email": "grace@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Grace",
            },
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


REPO_ALPHA = {
    "provider_repo_id": "4001",
    "owner": "ada",
    "name": "billing-data-service",
    "full_name": "ada/billing-data-service",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/billing-data-service",
}
REPO_BETA = {
    "provider_repo_id": "4002",
    "owner": "ada",
    "name": "shipping-data-service",
    "full_name": "ada/shipping-data-service",
    "private": False,
    "default_branch": "main",
    "html_url": "https://github.com/ada/shipping-data-service",
}


class TestAmbiguousSubjectAsksInsteadOfGuessing:
    """C-1 — an unresolvable subject must produce a question, not a
    confident answer about whichever repository happened to score first."""

    async def _two_tied_repositories(self, db_client: AsyncClient, headers: dict[str, str]):
        await db_client.post(
            "/api/v1/repositories", headers=headers, json={"repositories": [REPO_ALPHA, REPO_BETA]}
        )

    async def test_ambiguous_question_asks_for_clarification_and_never_calls_the_llm(
        self, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        await self._two_tied_repositories(db_client, headers)

        async def _explode(*_args, **_kwargs):
            raise AssertionError(
                "the LLM must not be called when the subject could not be resolved"
            )

        monkeypatch.setattr(
            "app.services.conversation_service.StageAwareLLMProvider.complete", _explode
        )

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "What breaks if I change the payment service?"},
        )

        assert response.status_code == 201
        payload = response.json()["messages"][1]["payload"]
        assert payload["needs_clarification"] is True
        assert payload["resolved_repository_id"] is None
        # F — no evidence badges for an ungrounded turn.
        assert payload["evidence"] == []
        assert payload["impact"] is None
        # M-1 — the classified intent survives a failed resolution.
        assert payload["intent"] == "impact"
        assert [c["name"] for c in payload["candidates"]]

    async def test_naming_the_repository_outright_still_answers(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        await self._two_tied_repositories(db_client, headers)

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "What breaks if I change billing-data-service?"},
        )

        assert response.status_code == 201
        payload = response.json()["messages"][1]["payload"]
        assert payload["needs_clarification"] is False
        assert payload["resolved_repository_name"] == "billing-data-service"

    async def test_a_later_ambiguous_message_does_not_borrow_an_earlier_resolved_repository(
        self, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: turn 1 resolves a real repository; turn 2 asks an
        unrelated, ambiguous question. The old short-circuit only fired
        when `state.resolved_repository_id is None`, so turn 2 fell
        through to `_synthesize_general` — an LLM call reasoning over
        turn 1's stale `investigation_state` for a subject the
        deterministic layer never resolved. `needs_clarification` on ANY
        turn must block the LLM, not just the first one."""
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        await db_client.post(
            "/api/v1/repositories",
            headers=headers,
            json={"repositories": [REPO_INGESTION, REPO_ALPHA, REPO_BETA]},
        )

        turn_1 = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "What breaks if I change customer-ingestion?"},
        )
        assert turn_1.status_code == 201
        conversation_id = turn_1.json()["id"]
        turn_1_payload = turn_1.json()["messages"][1]["payload"]
        assert turn_1_payload["needs_clarification"] is False
        assert turn_1_payload["resolved_repository_name"] == "customer-ingestion"

        async def _explode(*_args, **_kwargs):
            raise AssertionError(
                "_synthesize_general/the LLM must not be reached on turn 2 — its own "
                "subject is ambiguous, regardless of turn 1's resolved repository"
            )

        monkeypatch.setattr(
            "app.services.conversation_service.StageAwareLLMProvider.complete", _explode
        )

        turn_2 = await db_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"message": "What breaks if I change the payment service?"},
        )

        assert turn_2.status_code == 200
        turn_2_payload = turn_2.json()["messages"][-1]["payload"]
        assert turn_2_payload["needs_clarification"] is True
        # No borrowing turn 1's repository, evidence, or impact to answer
        # turn 2's own unresolved subject.
        assert turn_2_payload["resolved_repository_id"] is None
        assert turn_2_payload["resolved_repository_name"] is None
        assert turn_2_payload["evidence"] == []
        assert turn_2_payload["impact"] is None
        assert turn_2_payload["intent"] == "impact"
        assert [c["name"] for c in turn_2_payload["candidates"]]


class TestConversationCrossUserIsolation:
    """The audit found 17 cross-user isolation suites and none for
    conversations — the behaviour was correct but unprotected."""

    async def test_another_user_cannot_read_or_post_to_a_conversation(
        self, db_client: AsyncClient
    ) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        created = await db_client.post(
            "/api/v1/conversations", headers=headers_a, json={"question": "anything at all"}
        )
        conversation_id = created.json()["id"]

        token_b = await _register_and_get_token(
            db_client,
            {
                "email": "intruder@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Intruder",
            },
        )
        headers_b = {"Authorization": f"Bearer {token_b}"}

        assert (
            await db_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers_b)
        ).status_code == 404
        assert (
            await db_client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=headers_b,
                json={"message": "leak it"},
            )
        ).status_code == 404
        assert (await db_client.get("/api/v1/conversations", headers=headers_b)).json() == []

    async def test_another_users_repository_name_resolves_to_nothing(
        self, db_client: AsyncClient
    ) -> None:
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
        assert body["candidates"] == []


class TestExternalProviderPolicyOnTheAskPath:
    """H-1 end to end. When the deployment does not permit external AI
    providers, an Ask turn must lose its narration and keep its deterministic
    graph answer — never silently ship private engineering metadata.

    This exercises the REAL `enforce_provider_policy`/`classify_deployment`
    (committed at cf46d08, untouched here) rather than monkeypatching the
    policy function itself — an earlier version of this test replaced
    `enforce_provider_policy` with a 2-argument lambda, but the real
    signature (also since cf46d08) takes THREE (`spec, config, settings`).
    Calling that lambda with 3 positional args raised a `TypeError` before
    the lambda's own deny-logic ever ran; that `TypeError` was swallowed by
    `_synthesize_general`'s own `except (..., TypeError)` handler and
    produced the exact same `degraded=True` outcome the test asserted —
    so the test passed whether or not the real policy denied anything.
    Verified: reverting the actual C-1/M-2/H-2 fixes in this file left this
    test green; only exercising the real policy through `get_settings`
    (below) makes it fail when policy enforcement is removed."""

    async def test_ask_degrades_instead_of_calling_a_denied_external_provider(
        self, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ai.config import resolver
        from app.core.config import Settings

        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_ingestion(db_client, headers)
        await _seed_minimal_graph(repo_id)

        # No `ai_settings`/`ai_provider_configs` rows exist for a fresh
        # test transaction, so `resolve()` falls through to
        # `settings.ai_provider` — "openai" by default, an EXTERNAL
        # provider — exactly the case the policy exists to catch. Denying
        # external providers here, through the real `Settings` the
        # resolver reads (`app.ai.config.resolver.get_settings`), exercises
        # `enforce_provider_policy`'s actual deny branch — nothing about
        # the policy function itself is replaced.
        monkeypatch.setattr(
            resolver, "get_settings", lambda: Settings(allow_external_ai_providers=False)
        )

        sent: list[str] = []

        async def _record_and_fail(*_args, **_kwargs):
            sent.append("called")
            raise AssertionError(
                "a denied external provider's complete() must never be invoked — "
                "the policy must reject it before any provider is built"
            )

        monkeypatch.setattr(
            "app.ai.providers.openai_provider.OpenAIProvider.complete", _record_and_fail
        )

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "What breaks if I change customer-ingestion?"},
        )

        assert response.status_code == 201
        payload = response.json()["messages"][1]["payload"]
        # Degraded via the real ExternalProviderNotPermittedError path —
        # no narration, but the deterministic answer survives.
        assert payload["degraded"] is True
        assert payload["resolved_repository_id"] == repo_id
        assert payload["impact"] is not None
        assert sent == [], "no external provider call may have been attempted"

    async def test_the_same_conversation_answers_normally_once_external_providers_are_permitted(
        self, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control case for the test above: with the identical setup,
        opting into external providers lets the LLM call actually happen
        and the turn is NOT degraded — proves the assertions above are
        sensitive to the policy decision, not merely to `complete()` being
        patched at all."""
        from app.ai.config import resolver
        from app.ai.providers.base import LLMResponse
        from app.core.config import Settings

        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}
        repo_id = await _select_ingestion(db_client, headers)
        await _seed_minimal_graph(repo_id)

        monkeypatch.setattr(
            resolver, "get_settings", lambda: Settings(allow_external_ai_providers=True)
        )

        async def _answer(*_args, **_kwargs):
            return LLMResponse(
                text='{"answer": "ok", "why": "", "entities": [], '
                '"grounded_in_new_facts": true}',
                finish_reason="stop",
            )

        monkeypatch.setattr("app.ai.providers.openai_provider.OpenAIProvider.complete", _answer)

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "What breaks if I change customer-ingestion?"},
        )

        assert response.status_code == 201
        payload = response.json()["messages"][1]["payload"]
        assert payload["degraded"] is False
        assert payload["resolved_repository_id"] == repo_id
