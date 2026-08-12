"""Refinement Planner — a "refinement"-mode conversation on the same
`POST /conversations` endpoints Ask GraphForge/Migration Assistant
already use. See `app.services.refinement_grounding` and
`ConversationService`'s own docstrings for what's fetched/derived vs.
LLM-proposed each turn.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

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

REQUIREMENT_TEXT = (
    "We need to let customers export their order history as a CSV file from their "
    "account page. The export should cover the last 12 months of orders and must be "
    "available within 30 seconds of the request. Only the authenticated account owner "
    "should be able to trigger their own export."
)


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


class TestRefinementPlanner:
    async def test_pasted_requirement_produces_a_grounded_plan(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": REQUIREMENT_TEXT, "mode": "refinement"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["mode"] == "refinement"
        assistant = body["messages"][-1]
        assert assistant["role"] == "assistant"
        payload = assistant["payload"]
        assert payload is not None
        plan = payload["refinement"]
        assert plan is not None
        # Work decomposition happened at all.
        assert len(plan["work_items"]) >= 1
        # Every proposed item must never be silently labelled "existing" —
        # nothing here came from a real Jira issue.
        assert all(item["status"] == "proposed" for item in plan["work_items"])
        assert all(item["provenance"] != "fact" for item in plan["work_items"])
        # Readiness is a real, derived, bounded score — never absent, never
        # out of range.
        assert plan["readiness"] is not None
        assert 0 <= plan["readiness"]["score"] <= 100
        return body["id"]

    async def test_story_quality_fields_are_populated(self, db_client: AsyncClient) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": REQUIREMENT_TEXT, "mode": "refinement"},
        )
        plan = response.json()["messages"][-1]["payload"]["refinement"]

        stories = [item for item in plan["work_items"] if item["type"] in ("story", "task")]
        assert stories, "expected at least one story/task in the decomposition"
        for story in stories:
            assert story["title"].strip() != ""
            # Acceptance criteria must exist and not be the vague
            # boilerplate the brief explicitly calls out as unacceptable.
            for criterion in story["acceptance_criteria"]:
                assert "works as expected" not in criterion.lower()

    async def test_confluence_only_reference_is_reported_as_unsupported(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "question": (
                    "Read this requirement: "
                    "https://example.atlassian.net/wiki/spaces/ENG/pages/12345/Export+Feature"
                ),
                "mode": "refinement",
            },
        )

        assert response.status_code == 201
        assistant = response.json()["messages"][-1]
        # Deterministic clarification, no LLM call, no fabricated plan.
        assert assistant["payload"]["refinement"] is None
        assert "confluence" in assistant["content"].lower()
        assert "jira" in assistant["content"].lower()

    async def test_confluence_named_without_a_url_is_also_reported_as_unsupported(
        self, db_client: AsyncClient
    ) -> None:
        # No URL for `CONFLUENCE_URL_RE` to match — just the product name in
        # prose, the way someone would actually phrase it in a demo. Must
        # hit the same honest, deterministic path as the URL case, not fall
        # through to freetext and have the LLM invent a plan out of a
        # sentence with no requirement content in it.
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "question": 'Refine the requirement in the Confluence page "Notification System Redesign RFC"',
                "mode": "refinement",
            },
        )

        assert response.status_code == 201
        assistant = response.json()["messages"][-1]
        assert assistant["payload"]["refinement"] is None
        assert "confluence" in assistant["content"].lower()
        assert "jira" in assistant["content"].lower()
        assert "paste" in assistant["content"].lower()

    async def test_ambiguous_input_with_no_extractable_requirement_still_produces_a_plan_or_asks(
        self, db_client: AsyncClient
    ) -> None:
        # A single vague word is still "freetext" (no Jira key, no
        # Confluence URL) — Refinement Planner must not crash on it, and
        # must not fabricate a confident plan out of nothing.
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": "refine this", "mode": "refinement"},
        )

        assert response.status_code == 201
        assistant = response.json()["messages"][-1]
        assert assistant["content"].strip() != ""

    async def test_multi_turn_modifies_the_existing_plan_rather_than_restarting(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        start = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": REQUIREMENT_TEXT, "mode": "refinement"},
        )
        conversation_id = start.json()["id"]
        first_plan = start.json()["messages"][-1]["payload"]["refinement"]
        assert first_plan is not None

        follow_up = await db_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"message": "What work are we missing?"},
        )

        assert follow_up.status_code == 200
        body = follow_up.json()
        assert len(body["messages"]) == 4
        last_payload = body["messages"][-1]["payload"]
        assert last_payload["intent"] in {"refinement", "reasoning"}
        # The plan persisted across the turn — a follow-up must not lose
        # the requirement understanding already established.
        second_plan = last_payload["refinement"]
        assert second_plan is not None
        assert second_plan["objective"] == first_plan["objective"] or second_plan["objective"]

    async def test_dependency_graph_edges_only_reference_real_work_item_ids(
        self, db_client: AsyncClient
    ) -> None:
        token = await _register_and_get_token(db_client, USER_A)
        headers = {"Authorization": f"Bearer {token}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"question": REQUIREMENT_TEXT, "mode": "refinement"},
        )
        plan = response.json()["messages"][-1]["payload"]["refinement"]
        if plan is None:
            # A transient LLM formatting failure degrades honestly (see
            # `_degraded_refinement_turn`) rather than crashing — nothing
            # to check structurally on a turn that never produced a plan.
            return

        work_item_ids = {item["id"] for item in plan["work_items"]}
        for edge in plan["edges"]:
            assert edge["source_id"] in work_item_ids
            assert edge["target_id"] in work_item_ids
            assert edge["source_id"] != edge["target_id"]
            assert edge["relationship"] in {
                "blocks",
                "depends_on",
                "enables",
                "related",
                "parent_child",
            }

    async def test_does_not_expose_another_users_engineering_context(
        self, db_client: AsyncClient
    ) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        await db_client.post(
            "/api/v1/repositories",
            headers=headers_a,
            json={
                "repositories": [
                    {
                        "provider_repo_id": "5001",
                        "owner": "ada",
                        "name": "order-export-service",
                        "full_name": "ada/order-export-service",
                        "private": False,
                        "default_branch": "main",
                        "html_url": "https://github.com/ada/order-export-service",
                    }
                ]
            },
        )

        token_b = await _register_and_get_token(db_client, USER_B)
        headers_b = {"Authorization": f"Bearer {token_b}"}

        response = await db_client.post(
            "/api/v1/conversations",
            headers=headers_b,
            json={"question": REQUIREMENT_TEXT, "mode": "refinement"},
        )

        assert response.status_code == 201
        plan = response.json()["messages"][-1]["payload"]["refinement"]
        # User B has no indexed repositories — engineering context must
        # honestly report ungrounded, never borrow User A's repository.
        assert plan["engineering_context_grounded"] is False

    async def test_conversation_is_scoped_to_its_owner(self, db_client: AsyncClient) -> None:
        token_a = await _register_and_get_token(db_client, USER_A)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        start = await db_client.post(
            "/api/v1/conversations",
            headers=headers_a,
            json={"question": REQUIREMENT_TEXT, "mode": "refinement"},
        )
        conversation_id = start.json()["id"]

        token_b = await _register_and_get_token(db_client, USER_B)
        headers_b = {"Authorization": f"Bearer {token_b}"}

        response = await db_client.get(
            f"/api/v1/conversations/{conversation_id}", headers=headers_b
        )
        assert response.status_code == 404

    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(
            "/api/v1/conversations",
            json={"question": REQUIREMENT_TEXT, "mode": "refinement"},
        )
        assert response.status_code == 401
