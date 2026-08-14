"""A report is the answer to a request somebody made, so `/reports`
exposes that request verbatim — not just the AI-generated short title of
the workflow that produced it.

The Reports page leads with `request`; without it the only text the API
offered was `title`/`workflow_title`, two near-identical 5-10 word labels
describing the workflow rather than what was actually asked.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_report import WorkflowReport

pytestmark = pytest.mark.asyncio

USER = {
    "email": "reports-request@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Ada",
}

LONG_REQUEST = (
    "We're seeing intermittent 504s on checkout during peak hours.\n"
    "Work out whether the order service's synchronous call into payments "
    "is the cause, and what it would take to make it asynchronous."
)


async def _headers(db_client: AsyncClient) -> dict[str, str]:
    await db_client.post("/api/v1/auth/register", json=USER)
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": USER["email"], "password": USER["password"]}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_report_carries_the_users_full_original_request(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _headers(db_client)
    me = await db_client.get("/api/v1/auth/me", headers=headers)

    workflow = Workflow(
        id=uuid.uuid4(),
        title="Checkout 504 investigation",
        original_prompt=LONG_REQUEST,
        user_id=uuid.UUID(me.json()["id"]),
    )
    db_session.add(workflow)
    await db_session.flush()
    report = WorkflowReport(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        title="Checkout 504 investigation",
        status="completed",
    )
    db_session.add(report)
    await db_session.flush()

    listed = await db_client.get("/api/v1/reports", headers=headers)
    assert listed.status_code == 200
    (summary,) = listed.json()
    # Verbatim and untruncated — the multi-line brief the user typed, not
    # a summary of it.
    assert summary["request"] == LONG_REQUEST
    assert summary["title"] == "Checkout 504 investigation"

    detail = await db_client.get(f"/api/v1/reports/{report.id}", headers=headers)
    assert detail.json()["request"] == LONG_REQUEST
