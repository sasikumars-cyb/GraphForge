"""KAN-44 — cross-user isolation for `/api/v1/sessions/*`.

Before this ticket, `EngineeringSession` had no `user_id` column at all and
every endpoint under `engineering_sessions.py` (27 of them) had no
ownership check anywhere — `SessionService.list_sessions()` returned every
Session in the database regardless of caller, and every sub-resource
endpoint's `_require_same_session` only verified an artifact belonged to
the *named* Session, never that the caller had any relationship to that
Session. Product decision recorded on the ticket: Sessions are private per
creator, matching the `user_id` convention every other user-owned resource
in the app (Repository, Workflow, Run) already uses.

The fix is one dependency (`_verified_session_owner`) every
`session_id`-scoped endpoint now depends on, so this file tests it as a
representative sample across read, list, write, and sub-resource-write
endpoints — not all 27 — the same "trust the code review, then prove it
end-to-end for a representative sample" methodology
`test_agent_runs_cross_user_isolation.py` already established, since the
gate is structurally identical at every call site (one dependency, not 27
hand-written checks that could individually drift).

Uses `db_client` (rolled-back transaction, real Postgres, no Neo4j) — this
router touches only Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.belief import Hypothesis
from app.models.engineering_session import EngineeringSession
from app.models.participant import Participant

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "session-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "session-intruder-b@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Intruder B",
}


async def _register_and_login(db_client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    await db_client.post("/api/v1/auth/register", json=payload)
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _owner_user_id(db_client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    me = await db_client.get("/api/v1/auth/me", headers=headers)
    return uuid.UUID(me.json()["id"])


@pytest.fixture
async def user_a_headers(db_client: AsyncClient) -> dict[str, str]:
    return await _register_and_login(db_client, USER_A)


@pytest.fixture
async def user_b_headers(db_client: AsyncClient) -> dict[str, str]:
    return await _register_and_login(db_client, USER_B)


@pytest.fixture
async def owned_session(
    db_client: AsyncClient, db_session: AsyncSession, user_a_headers: dict[str, str]
) -> AsyncGenerator[EngineeringSession, None]:
    """A Session owned by User A, created directly (not via the API) so
    these tests exercise only the read/write ownership gate, not
    `create_session` itself."""
    owner_id = await _owner_user_id(db_client, user_a_headers)
    session = EngineeringSession(
        title="User A's investigation", status="orienting", user_id=owner_id
    )
    db_session.add(session)
    await db_session.flush()
    yield session


@pytest.fixture
async def hypothesis_in_owned_session(
    db_session: AsyncSession, owned_session: EngineeringSession
) -> AsyncGenerator[Hypothesis, None]:
    """A real sub-resource artifact inside User A's Session — proves the
    ownership gate blocks access even when User B somehow knows a real
    artifact id, not only when guessing session ids blind."""
    participant = Participant(kind="agent", display_name="investigator", agent_role="investigator")
    db_session.add(participant)
    await db_session.flush()
    hypothesis = Hypothesis(
        session_id=owned_session.id,
        participant_id=participant.id,
        description="A hypothesis only User A should be able to see.",
        status="proposed",
    )
    db_session.add(hypothesis)
    await db_session.flush()
    yield hypothesis


class TestSessionReadWrite:
    async def test_get_session_404s_for_another_user(
        self,
        db_client: AsyncClient,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        resp = await db_client.get(f"/api/v1/sessions/{owned_session.id}", headers=user_b_headers)
        assert resp.status_code == 404

    async def test_get_session_succeeds_for_the_owner(
        self,
        db_client: AsyncClient,
        user_a_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        resp = await db_client.get(f"/api/v1/sessions/{owned_session.id}", headers=user_a_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == str(owned_session.id)

    async def test_update_status_404s_for_another_user(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        resp = await db_client.patch(
            f"/api/v1/sessions/{owned_session.id}/status",
            json={"status": "converging"},
            headers=user_b_headers,
        )
        assert resp.status_code == 404
        # Confirm the owner's session status wasn't touched by the rejected
        # request.
        untouched = await db_session.get(EngineeringSession, owned_session.id)
        assert untouched is not None
        assert untouched.status == "orienting"

    async def test_list_sessions_never_includes_another_users_session(
        self,
        db_client: AsyncClient,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        resp = await db_client.get("/api/v1/sessions", headers=user_b_headers)
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert str(owned_session.id) not in ids

    async def test_list_sessions_includes_the_owners_own_session(
        self,
        db_client: AsyncClient,
        user_a_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        resp = await db_client.get("/api/v1/sessions", headers=user_a_headers)
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert str(owned_session.id) in ids


class TestSubResourceReadWrite:
    """Every sub-resource path (timeline, understanding, hypotheses,
    beliefs, evidence, recommendations, decisions, contradictions) is
    gated by the exact same `_verified_session_owner` dependency — these
    four are a representative sample, not an exhaustive sweep, per the
    module docstring."""

    async def test_get_timeline_404s_for_another_user(
        self,
        db_client: AsyncClient,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        resp = await db_client.get(
            f"/api/v1/sessions/{owned_session.id}/timeline", headers=user_b_headers
        )
        assert resp.status_code == 404

    async def test_get_working_understanding_404s_for_another_user(
        self,
        db_client: AsyncClient,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        resp = await db_client.get(
            f"/api/v1/sessions/{owned_session.id}/understanding", headers=user_b_headers
        )
        assert resp.status_code == 404

    async def test_propose_hypothesis_404s_for_another_user(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
    ) -> None:
        """A write attempt against someone else's Session — not just a
        read — must be rejected the same way."""
        resp = await db_client.post(
            f"/api/v1/sessions/{owned_session.id}/hypotheses",
            json={"description": "An intruder's hypothesis", "confidence": 0.5},
            headers=user_b_headers,
        )
        assert resp.status_code == 404

        # Confirm nothing was actually written into User A's session.
        result = await db_session.execute(
            select(Hypothesis).where(Hypothesis.session_id == owned_session.id)
        )
        assert result.first() is None

    async def test_list_hypotheses_404s_for_another_user_even_with_a_real_artifact_id(
        self,
        db_client: AsyncClient,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
        hypothesis_in_owned_session: Hypothesis,
    ) -> None:
        """The list endpoint itself 404s before any artifact-level check
        even runs — User B never gets far enough to learn the real
        hypothesis exists."""
        resp = await db_client.get(
            f"/api/v1/sessions/{owned_session.id}/hypotheses", headers=user_b_headers
        )
        assert resp.status_code == 404

    async def test_resolve_hypothesis_404s_for_another_user(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        user_b_headers: dict[str, str],
        owned_session: EngineeringSession,
        hypothesis_in_owned_session: Hypothesis,
    ) -> None:
        """Knowing the real hypothesis_id (e.g. leaked in a log, or
        guessed) is not enough — the session-ownership gate runs first,
        before `_require_same_session` ever gets a chance to compare ids."""
        resp = await db_client.post(
            f"/api/v1/sessions/{owned_session.id}/hypotheses/"
            f"{hypothesis_in_owned_session.id}/resolve",
            json={"belief_statement": "Confirmed by an intruder", "belief_confidence": 0.9},
            headers=user_b_headers,
        )
        assert resp.status_code == 404

        untouched = await db_session.get(Hypothesis, hypothesis_in_owned_session.id)
        assert untouched is not None
        assert untouched.status == "proposed"

    async def test_list_hypotheses_succeeds_for_the_owner(
        self,
        db_client: AsyncClient,
        user_a_headers: dict[str, str],
        owned_session: EngineeringSession,
        hypothesis_in_owned_session: Hypothesis,
    ) -> None:
        resp = await db_client.get(
            f"/api/v1/sessions/{owned_session.id}/hypotheses", headers=user_a_headers
        )
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()}
        assert str(hypothesis_in_owned_session.id) in ids


async def test_unauthenticated_requests_are_401(
    db_client: AsyncClient, owned_session: EngineeringSession
) -> None:
    resp = await db_client.get(f"/api/v1/sessions/{owned_session.id}")
    assert resp.status_code == 401
