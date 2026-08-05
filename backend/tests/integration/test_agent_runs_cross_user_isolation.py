"""KAN-33 — cross-user isolation for `/api/v1/agent-runs/*`.

Continues the `workflows.py` sweep (see `test_workflows_cross_user_
isolation.py`'s own docstring for why that router went first) to the
next-highest-value target named in KAN-11's implementation report:
`agent_runs.py` is where standalone runs live, and — per KAN-9's
`_authorization.py` audit — is also the router `create_run` uses to
resolve `planning_run_id` into a standalone planning context fed
straight into a git_ops-capable agent, making an ownership gap here a
path to smuggling another user's planning output into your own run.

Code review found every mutating/reading endpoint already routes through
`_get_owned_run`/`_run_ownership_clause` (`agent_runs.py`), which — like
`workflow_service`'s equivalent — raises `NotFoundError` (404, not 403)
so a run owned by someone else is indistinguishable from one that
doesn't exist. This file is the HTTP-level proof, the same discipline
`test_workflows_cross_user_isolation.py` established: trust the code
review, then prove it end-to-end.

Uses `db_client` (rolled-back transaction, real Postgres, no Neo4j) —
`agent_runs.py`'s read/cancel/delete/list paths are all Neo4j-free by
construction (Run/AgentStep state lives entirely in Postgres). `POST
/agent-runs` itself (run creation) is deliberately out of scope here —
it schedules real background agent execution via
`app.orchestrator.background_execution`, which is a different, heavier
concern than the ownership check this file targets; its one
ownership-relevant behavior (rejecting another user's `planning_run_id`)
is already covered by KAN-9's
`test_no_write_goal_can_receive_a_standalone_planning_context`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "run-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "run-intruder-b@example.com",
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
async def owned_run(
    db_client: AsyncClient, db_session: AsyncSession, user_a_headers: dict[str, str]
) -> AsyncGenerator[Run, None]:
    """A completed, standalone run owned by User A. `completed` status is
    picked deliberately for the cancel endpoint's sake: it takes the
    no-op/report-only branch there, isolating what that test verifies to
    ownership alone rather than also exercising background-task
    cancellation."""
    owner_id = await _owner_user_id(db_client, user_a_headers)
    run = Run(
        id=uuid.uuid4(),
        subject_id="acme/widgets#7",
        subject_type="pull_request",
        goal="code_review",
        status="completed",
        user_id=owner_id,
    )
    db_session.add(run)
    await db_session.flush()
    yield run


async def test_get_run_404s_for_another_users_run(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_run: Run
) -> None:
    resp = await db_client.get(f"/api/v1/agent-runs/{owned_run.id}", headers=user_b_headers)
    assert resp.status_code == 404


async def test_get_run_succeeds_for_the_owner(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_run: Run
) -> None:
    resp = await db_client.get(f"/api/v1/agent-runs/{owned_run.id}", headers=user_a_headers)
    assert resp.status_code == 200
    assert resp.json()["run_id"] == str(owned_run.id)


async def test_list_runs_never_includes_another_users_run(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_run: Run
) -> None:
    resp = await db_client.get("/api/v1/agent-runs", headers=user_b_headers)
    assert resp.status_code == 200
    ids = {item["run_id"] for item in resp.json()["items"]}
    assert str(owned_run.id) not in ids


async def test_cancel_run_404s_for_another_users_run(
    db_client: AsyncClient,
    db_session: AsyncSession,
    user_b_headers: dict[str, str],
    owned_run: Run,
) -> None:
    resp = await db_client.post(f"/api/v1/agent-runs/{owned_run.id}/cancel", headers=user_b_headers)
    assert resp.status_code == 404
    # Confirm the owner's run status wasn't touched by the rejected request.
    still_completed = await db_session.get(Run, owned_run.id)
    assert still_completed is not None
    assert still_completed.status == "completed"


async def test_delete_run_404s_for_another_users_run(
    db_client: AsyncClient,
    db_session: AsyncSession,
    user_b_headers: dict[str, str],
    owned_run: Run,
) -> None:
    resp = await db_client.delete(f"/api/v1/agent-runs/{owned_run.id}", headers=user_b_headers)
    assert resp.status_code == 404
    # Confirm it wasn't actually deleted despite the 404.
    still_there = await db_session.get(Run, owned_run.id)
    assert still_there is not None


async def test_delete_run_succeeds_for_the_owner(
    db_client: AsyncClient,
    db_session: AsyncSession,
    user_a_headers: dict[str, str],
    owned_run: Run,
) -> None:
    resp = await db_client.delete(f"/api/v1/agent-runs/{owned_run.id}", headers=user_a_headers)
    assert resp.status_code == 204
    gone = await db_session.get(Run, owned_run.id)
    assert gone is None


async def test_unauthenticated_requests_are_401(db_client: AsyncClient, owned_run: Run) -> None:
    resp = await db_client.get(f"/api/v1/agent-runs/{owned_run.id}")
    assert resp.status_code == 401
