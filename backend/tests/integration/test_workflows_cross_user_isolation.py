"""KAN-33 — cross-user isolation for every `/api/v1/workflows/*` endpoint.

`workflows.py` is the single most consequential router to get this wrong:
`POST /workflows/{id}/approve` is the one authorization gate standing
between a request and a real GitHub write (see KAN-28,
`app.agents.git_ops._authorization`) — a cross-user leak here isn't just
data exposure, it's the ability to approve, reject, cancel, or override
someone else's execution plan.

Code review (this ticket's own audit) found every endpoint already routes
through `workflow_service.get_workflow`/`get_workflow_for_update` with
`user_id=user.id`, which enforces ownership via the shared
`_check_workflow_owned` helper and raises `NotFoundError` (404, not 403 —
deliberately, so a workflow owned by someone else is indistinguishable
from one that doesn't exist, closing the IDOR-oracle gap). What was
missing was an HTTP-level test actually proving it for every endpoint,
rather than trusting the code review alone - this file is that proof.

Uses `db_client` (rolled-back transaction, real Postgres, no Neo4j) —
every endpoint under test here is Neo4j-free by construction (workflow
approval/lifecycle state lives entirely in Postgres).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "intruder-b@example.com",
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
async def owned_planning_workflow(
    db_client: AsyncClient, db_session: AsyncSession, user_a_headers: dict[str, str]
) -> AsyncGenerator[Workflow, None]:
    """A completed, approved Planning workflow owned by User A — the
    shape every mutating endpoint under test needs (approve/reject/cancel
    all require a real, addressable workflow; several also require
    specific status/stage combinations checked separately from
    ownership, so this fixture picks values that pass those checks too,
    isolating what each test actually verifies to ownership alone)."""
    owner_id = await _owner_user_id(db_client, user_a_headers)
    workflow = Workflow(
        id=uuid.uuid4(),
        title="Blueprint: Rate Limiter",
        original_prompt="Add a rate limiter to the payment API.",
        current_stage="engineering_review",
        status="approved",
        workflow_type="planning",
        user_id=owner_id,
    )
    db_session.add(workflow)
    await db_session.flush()
    yield workflow


async def test_get_workflow_404s_for_another_users_workflow(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.get(
        f"/api/v1/workflows/{owned_planning_workflow.id}", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_get_workflow_succeeds_for_the_owner(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.get(
        f"/api/v1/workflows/{owned_planning_workflow.id}", headers=user_a_headers
    )
    assert resp.status_code == 200
    assert resp.json()["workflow_id"] == str(owned_planning_workflow.id)


async def test_list_workflows_never_includes_another_users_workflow(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.get("/api/v1/workflows", headers=user_b_headers)
    assert resp.status_code == 200
    ids = {item["workflow_id"] for item in resp.json()["items"]}
    assert str(owned_planning_workflow.id) not in ids


async def test_delete_workflow_404s_for_another_users_workflow(
    db_client: AsyncClient,
    db_session: AsyncSession,
    user_b_headers: dict[str, str],
    owned_planning_workflow: Workflow,
) -> None:
    resp = await db_client.delete(
        f"/api/v1/workflows/{owned_planning_workflow.id}", headers=user_b_headers
    )
    assert resp.status_code == 404
    # Confirm it wasn't actually deleted despite the 404.
    still_there = await db_session.get(Workflow, owned_planning_workflow.id)
    assert still_there is not None


async def test_approve_workflow_404s_for_another_users_workflow(
    db_client: AsyncClient,
    db_session: AsyncSession,
    user_b_headers: dict[str, str],
    owned_planning_workflow: Workflow,
) -> None:
    """The single most consequential check in this file: approval is what
    gates a subsequent auto_execution workflow's real GitHub writes
    (KAN-28) - User B must not be able to approve User A's blueprint."""
    resp = await db_client.post(
        f"/api/v1/workflows/{owned_planning_workflow.id}/approve", headers=user_b_headers
    )
    assert resp.status_code == 404
    unchanged = await db_session.get(Workflow, owned_planning_workflow.id)
    assert unchanged is not None
    assert unchanged.approved_by_user_id is None


async def test_reject_workflow_404s_for_another_users_workflow(
    db_client: AsyncClient,
    db_session: AsyncSession,
    user_b_headers: dict[str, str],
    owned_planning_workflow: Workflow,
) -> None:
    resp = await db_client.post(
        f"/api/v1/workflows/{owned_planning_workflow.id}/reject", headers=user_b_headers
    )
    assert resp.status_code == 404
    unchanged = await db_session.get(Workflow, owned_planning_workflow.id)
    assert unchanged is not None
    assert unchanged.status == "approved"


async def test_cancel_workflow_404s_for_another_users_workflow(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.post(
        f"/api/v1/workflows/{owned_planning_workflow.id}/cancel", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_stage_override_404s_for_another_users_workflow(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.patch(
        f"/api/v1/workflows/{owned_planning_workflow.id}/stages/engineering_review/override",
        headers=user_b_headers,
        json={"override": {"note": "hijacked"}},
    )
    assert resp.status_code == 404


async def test_continue_workflow_404s_for_another_users_workflow(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.post(
        f"/api/v1/workflows/{owned_planning_workflow.id}/continue", headers=user_b_headers, json={}
    )
    assert resp.status_code == 404


async def test_clarify_workflow_404s_for_another_users_workflow(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.post(
        f"/api/v1/workflows/{owned_planning_workflow.id}/clarify",
        headers=user_b_headers,
        json={"question_id": "q1", "answer": "hijacked"},
    )
    assert resp.status_code == 404


async def test_understanding_endpoint_404s_for_another_users_workflow(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    resp = await db_client.get(
        f"/api/v1/workflows/{owned_planning_workflow.id}/understanding", headers=user_b_headers
    )
    assert resp.status_code == 404


async def test_create_auto_execution_cannot_reference_another_users_approved_blueprint(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_planning_workflow: Workflow
) -> None:
    """The other direction of the same gate KAN-28 documented: an
    auto_execution workflow can only be created from an approved Planning
    blueprint the caller owns - User B referencing User A's (real,
    approved) blueprint as source_workflow_id must be rejected exactly
    like a nonexistent one, not treated as a valid source because it
    happens to be approved."""
    resp = await db_client.post(
        "/api/v1/workflows",
        headers=user_b_headers,
        json={
            "title": "Hijacked execution",
            "workflow_type": "auto_execution",
            "source_workflow_id": str(owned_planning_workflow.id),
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "source_workflow_not_found"
