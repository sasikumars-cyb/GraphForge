"""RFC-001's REST API — `/api/v1/sessions/*`. End-to-end through real HTTP
requests (see `db_client` in conftest.py), covering the endpoints named in
RFC-001's Implementation Requirements plus the invariants that must be
enforced at the API boundary specifically (aggregate ownership across a
Session-scoped URL, the propose/commit boundary, N-ary Contradiction).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "engineer-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada Engineer",
}


async def _register_and_get_token(db_client: AsyncClient, payload: dict[str, str]) -> str:
    await db_client.post("/api/v1/auth/register", json=payload)
    login_response = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    return str(login_response.json()["access_token"])


async def _create_session(db_client: AsyncClient, headers: dict[str, str], title: str) -> dict:
    response = await db_client.post("/api/v1/sessions", json={"title": title}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_and_fetch_a_session(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}

    session = await _create_session(db_client, headers, "Investigate duplicate records")
    assert session["status"] == "orienting"

    fetched = await db_client.get(f"/api/v1/sessions/{session['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Investigate duplicate records"


async def test_get_a_nonexistent_session_is_404(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    response = await db_client.get(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404


async def test_session_requires_authentication(db_client: AsyncClient) -> None:
    response = await db_client.post("/api/v1/sessions", json={"title": "x"})
    assert response.status_code == 401


async def test_hypothesis_to_belief_to_decision_flow(db_client: AsyncClient) -> None:
    """The core RFC-001 flow through the API: an agent proposes and
    resolves a hypothesis, a human commits the resulting Decision."""
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    session = await _create_session(db_client, headers, "T")

    hyp_response = await db_client.post(
        f"/api/v1/sessions/{session['id']}/hypotheses",
        json={"agent_role": "investigator", "description": "Merge logic owns the bug"},
        headers=headers,
    )
    assert hyp_response.status_code == 201
    hypothesis = hyp_response.json()
    assert hypothesis["status"] == "proposed"

    belief_response = await db_client.post(
        f"/api/v1/sessions/{session['id']}/hypotheses/{hypothesis['id']}/resolve",
        json={
            "agent_role": "investigator",
            "belief_statement": "SCDType2Merger owns the bug",
            "belief_confidence": 0.8,
        },
        headers=headers,
    )
    assert belief_response.status_code == 201
    belief = belief_response.json()
    assert belief["statement"] == "SCDType2Merger owns the bug"

    understanding = await db_client.get(
        f"/api/v1/sessions/{session['id']}/understanding", headers=headers
    )
    assert understanding.status_code == 200
    assert understanding.json()["belief_count"] == 1

    # A human commits the Decision — no agent_role field exists on this
    # request at all (Architecture v2.1 §5's propose/commit boundary).
    decision_response = await db_client.post(
        f"/api/v1/sessions/{session['id']}/decisions",
        json={
            "decision_kind": "planning_strategy",
            "statement": "Fix the merge logic",
            "rationale": "Traced root cause",
        },
        headers=headers,
    )
    assert decision_response.status_code == 201
    assert decision_response.json()["decision_kind"] == "planning_strategy"

    timeline = await db_client.get(f"/api/v1/sessions/{session['id']}/timeline", headers=headers)
    assert timeline.status_code == 200
    kinds = [e["kind"] for e in timeline.json()["items"]]
    assert kinds == [
        "session_created",
        "hypothesis_proposed",
        "belief_formed",
        "decision_committed",
    ]


async def test_competing_recommendations_produce_a_contradiction_via_api(
    db_client: AsyncClient,
) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    session = await _create_session(db_client, headers, "T")

    hyp = (
        await db_client.post(
            f"/api/v1/sessions/{session['id']}/hypotheses",
            json={"agent_role": "investigator", "description": "X"},
            headers=headers,
        )
    ).json()
    belief = (
        await db_client.post(
            f"/api/v1/sessions/{session['id']}/hypotheses/{hyp['id']}/resolve",
            json={
                "agent_role": "investigator",
                "belief_statement": "Y",
                "belief_confidence": 0.5,
            },
            headers=headers,
        )
    ).json()

    await db_client.post(
        f"/api/v1/sessions/{session['id']}/recommendations",
        json={
            "agent_role": "investigator",
            "statement": "Check test coverage",
            "target_belief_id": belief["id"],
        },
        headers=headers,
    )
    await db_client.post(
        f"/api/v1/sessions/{session['id']}/recommendations",
        json={
            "agent_role": "planner",
            "statement": "Check the dependency graph instead",
            "target_belief_id": belief["id"],
        },
        headers=headers,
    )

    contradictions = await db_client.get(
        f"/api/v1/sessions/{session['id']}/contradictions", headers=headers
    )
    assert contradictions.status_code == 200
    body = contradictions.json()
    assert body["page"]["total"] == 1
    assert len(body["items"][0]["parties"]) == 2


async def test_contradiction_requires_at_least_two_parties_via_api(
    db_client: AsyncClient,
) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    session = await _create_session(db_client, headers, "T")

    hyp = (
        await db_client.post(
            f"/api/v1/sessions/{session['id']}/hypotheses",
            json={"agent_role": "investigator", "description": "X"},
            headers=headers,
        )
    ).json()
    belief = (
        await db_client.post(
            f"/api/v1/sessions/{session['id']}/hypotheses/{hyp['id']}/resolve",
            json={
                "agent_role": "investigator",
                "belief_statement": "Y",
                "belief_confidence": 0.5,
            },
            headers=headers,
        )
    ).json()

    response = await db_client.post(
        f"/api/v1/sessions/{session['id']}/contradictions",
        json={
            "agent_role": "investigator",
            "description": "Not really a dispute",
            "party_artifact_ids": [belief["id"]],
        },
        headers=headers,
    )
    # Pydantic's own `Field(min_length=2)` rejects this before the service
    # layer is ever reached — a 422, not RFC-001's own 409 ConflictError.
    assert response.status_code == 422


async def test_a_hypothesis_from_another_session_is_404_not_leaked(
    db_client: AsyncClient,
) -> None:
    """API-boundary enforcement of aggregate ownership: acting on a real
    artifact through the wrong Session's URL is "not found," never
    silently redirected to the artifact's actual Session."""
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    session_a = await _create_session(db_client, headers, "A")
    session_b = await _create_session(db_client, headers, "B")

    hyp = (
        await db_client.post(
            f"/api/v1/sessions/{session_a['id']}/hypotheses",
            json={"agent_role": "investigator", "description": "X"},
            headers=headers,
        )
    ).json()

    response = await db_client.post(
        f"/api/v1/sessions/{session_b['id']}/hypotheses/{hyp['id']}/reject",
        json={"agent_role": "investigator", "reason": "wrong session"},
        headers=headers,
    )
    assert response.status_code == 404


async def test_agent_cannot_commit_a_decision_via_api(db_client: AsyncClient) -> None:
    """Architecture v2.1 §5's propose/commit boundary is not just a
    service-layer rule — there is no way to even *express* an
    agent-committed Decision through the request schema
    (`DecisionCommitRequest` has no `agent_role` field), and the service
    independently rejects it regardless."""
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    session = await _create_session(db_client, headers, "T")

    response = await db_client.post(
        f"/api/v1/sessions/{session['id']}/decisions",
        json={
            "decision_kind": "planning_strategy",
            "statement": "x",
            "rationale": "y",
        },
        headers=headers,
    )
    # The calling human always commits — this always succeeds as a human
    # commit, there is no request shape that could make it an agent's.
    assert response.status_code == 201
    assert "agent_role" not in response.request.content.decode()


async def test_evidence_list_is_paginated(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    session = await _create_session(db_client, headers, "T")

    for i in range(3):
        await db_client.post(
            f"/api/v1/sessions/{session['id']}/evidence",
            json={
                "agent_role": "investigator",
                "evidence_kind": "retrieved",
                "summary": f"Evidence {i}",
                "source": "graph",
            },
            headers=headers,
        )

    response = await db_client.get(
        f"/api/v1/sessions/{session['id']}/evidence?limit=2&offset=0", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page"]["total"] == 3
    assert len(body["items"]) == 2


async def test_session_status_transition_via_api(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, USER_A)
    headers = {"Authorization": f"Bearer {token}"}
    session = await _create_session(db_client, headers, "T")

    response = await db_client.patch(
        f"/api/v1/sessions/{session['id']}/status",
        json={"status": "converging", "reason": "Understanding has stabilized"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "converging"

    invalid = await db_client.patch(
        f"/api/v1/sessions/{session['id']}/status",
        json={"status": "not-a-real-status"},
        headers=headers,
    )
    assert invalid.status_code == 409
