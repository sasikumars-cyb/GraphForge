"""KAN-33 — cross-user isolation for `/api/v1/reports/*`.

`reports.py` inherits ownership from the parent `Workflow`
(`WorkflowReport` has no `user_id` of its own) and deliberately treats a
workflow with `user_id IS NULL` as visible to everyone — the router's
own docstring calls this out explicitly ("a report has no owner of its
own, it inherits its workflow's"). This file verifies both halves: a
report under another user's owned workflow 404s, and a report under an
ownerless workflow is visible to any authenticated user (intentional,
not a gap).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_report import WorkflowReport

pytestmark = pytest.mark.asyncio

USER_A = {
    "email": "reports-owner-a@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Owner A",
}
USER_B = {
    "email": "reports-intruder-b@example.com",
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
async def owned_report(
    db_client: AsyncClient, db_session: AsyncSession, user_a_headers: dict[str, str]
) -> AsyncGenerator[WorkflowReport, None]:
    owner_id = await _owner_user_id(db_client, user_a_headers)
    workflow = Workflow(
        id=uuid.uuid4(),
        title="Blueprint: Rate Limiter",
        original_prompt="Add a rate limiter.",
        user_id=owner_id,
    )
    db_session.add(workflow)
    await db_session.flush()
    report = WorkflowReport(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        title="Rate Limiter Report",
        status="completed",
    )
    db_session.add(report)
    await db_session.flush()
    yield report


@pytest.fixture
async def ownerless_report(
    db_session: AsyncSession,
) -> AsyncGenerator[WorkflowReport, None]:
    workflow = Workflow(
        id=uuid.uuid4(),
        title="System-generated Blueprint",
        original_prompt="No owner.",
        user_id=None,
    )
    db_session.add(workflow)
    await db_session.flush()
    report = WorkflowReport(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        title="Ownerless Report",
        status="completed",
    )
    db_session.add(report)
    await db_session.flush()
    yield report


async def test_get_report_404s_for_another_users_report(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_report: WorkflowReport
) -> None:
    resp = await db_client.get(f"/api/v1/reports/{owned_report.id}", headers=user_b_headers)
    assert resp.status_code == 404


async def test_get_report_succeeds_for_the_owner(
    db_client: AsyncClient, user_a_headers: dict[str, str], owned_report: WorkflowReport
) -> None:
    resp = await db_client.get(f"/api/v1/reports/{owned_report.id}", headers=user_a_headers)
    assert resp.status_code == 200


async def test_list_reports_never_includes_another_users_report(
    db_client: AsyncClient, user_b_headers: dict[str, str], owned_report: WorkflowReport
) -> None:
    resp = await db_client.get("/api/v1/reports", headers=user_b_headers)
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()}
    assert str(owned_report.id) not in ids


async def test_ownerless_report_is_visible_to_any_authenticated_user(
    db_client: AsyncClient, user_b_headers: dict[str, str], ownerless_report: WorkflowReport
) -> None:
    resp = await db_client.get(f"/api/v1/reports/{ownerless_report.id}", headers=user_b_headers)
    assert resp.status_code == 200


async def test_ownerless_report_appears_in_any_authenticated_users_list(
    db_client: AsyncClient, user_b_headers: dict[str, str], ownerless_report: WorkflowReport
) -> None:
    resp = await db_client.get("/api/v1/reports", headers=user_b_headers)
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()}
    assert str(ownerless_report.id) in ids


async def test_unauthenticated_requests_are_401(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/reports")
    assert resp.status_code == 401
