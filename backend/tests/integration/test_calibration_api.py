"""KAN-23 — GET /api/v1/calibration/summary.

Covers: admin-only access, per-bucket approval-rate aggregation, and the
new by_prompt_version breakdown (join against AgentStep.prompt_version)
including the flagged_miscalibrated threshold logic.

Rows are inserted directly via db_session rather than driven through the
full workflow-approval flow (`_record_confidence_calibration` in
workflow_service.py) — that flow is exercised elsewhere
(test_workflow_lifecycle-style tests); this file's job is the
aggregation endpoint itself, which only needs Workflow/Run/AgentStep/
ConfidenceCalibration rows to exist with the right shape.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_step import AgentStep
from app.models.confidence_calibration import ConfidenceCalibration
from app.models.run import Run
from app.models.user import User
from app.models.workflow import Workflow

pytestmark = pytest.mark.asyncio


async def _register_and_get_token(db_client: AsyncClient, email: str) -> str:
    await db_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple", "full_name": "Test User"},
    )
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery-staple"}
    )
    return str(login.json()["access_token"])


async def _promote_to_admin(db_session: AsyncSession, email: str) -> None:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = "admin"
    await db_session.commit()


async def _register_admin(db_client: AsyncClient, db_session: AsyncSession, email: str) -> str:
    token = await _register_and_get_token(db_client, email)
    await _promote_to_admin(db_session, email)
    return token


async def _make_run_and_step(
    db_session: AsyncSession, *, agent_id: str, prompt_version: str
) -> Run:
    run = Run(
        id=uuid.uuid4(),
        subject_id="acme/widgets#1",
        subject_type="pull_request",
        goal=agent_id,
    )
    db_session.add(run)
    await db_session.flush()

    step = AgentStep(
        id=uuid.uuid4(),
        run_id=run.id,
        agent_id=agent_id,
        prompt_version=prompt_version,
    )
    db_session.add(step)
    await db_session.flush()
    return run


async def _add_calibration_row(
    db_session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: str,
    confidence_score: float,
    decision: str,
) -> None:
    db_session.add(
        ConfidenceCalibration(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            run_id=run_id,
            agent_id=agent_id,
            confidence_score=confidence_score,
            decision=decision,
        )
    )


async def _make_workflow(db_session: AsyncSession) -> Workflow:
    workflow = Workflow(
        id=uuid.uuid4(),
        title="test workflow",
        original_prompt="test",
    )
    db_session.add(workflow)
    await db_session.flush()
    return workflow


async def test_summary_requires_admin(db_client: AsyncClient, db_session: AsyncSession) -> None:
    token = await _register_and_get_token(db_client, "non-admin@example.com")

    response = await db_client.get(
        "/api/v1/calibration/summary", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


async def test_summary_rejects_unauthenticated_requests(db_client: AsyncClient) -> None:
    response = await db_client.get("/api/v1/calibration/summary")

    assert response.status_code == 401


async def test_summary_is_empty_with_no_calibration_rows(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_admin(db_client, db_session, "admin-empty@example.com")

    response = await db_client.get(
        "/api/v1/calibration/summary", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"agents": []}


async def test_summary_aggregates_bucket_and_prompt_version_stats(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_admin(db_client, db_session, "admin-agg@example.com")
    workflow = await _make_workflow(db_session)

    # code_review, prompt_version "1.0": 4 decisions, 3 approved (75%)
    for approved in (True, True, True, False):
        run = await _make_run_and_step(db_session, agent_id="code_review", prompt_version="1.0")
        await _add_calibration_row(
            db_session,
            workflow_id=workflow.id,
            run_id=run.id,
            agent_id="code_review",
            confidence_score=0.9,
            decision="approved" if approved else "rejected",
        )
    await db_session.commit()

    response = await db_client.get(
        "/api/v1/calibration/summary", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["agents"]) == 1
    agent = body["agents"][0]
    assert agent["agent_id"] == "code_review"
    assert agent["total_decisions"] == 4
    assert agent["approval_rate"] == pytest.approx(0.75)
    assert len(agent["buckets"]) == 1
    assert agent["buckets"][0]["bucket"] == "0.85 - 1.0"
    assert agent["buckets"][0]["total"] == 4
    assert len(agent["by_prompt_version"]) == 1
    version_stat = agent["by_prompt_version"][0]
    assert version_stat["prompt_version"] == "1.0"
    assert version_stat["total"] == 4
    assert version_stat["approved"] == 3
    assert version_stat["approval_rate"] == pytest.approx(0.75)
    # Only prompt_version "1.0" exists for this agent, so its rate equals
    # the agent's overall rate — never flagged regardless of threshold.
    assert version_stat["flagged_miscalibrated"] is False


async def test_summary_flags_a_prompt_version_diverging_from_its_agents_overall_rate(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_admin(db_client, db_session, "admin-flag@example.com")
    workflow = await _make_workflow(db_session)

    # "1.0": 5 decisions, all approved (100%)
    for _ in range(5):
        run = await _make_run_and_step(db_session, agent_id="planner", prompt_version="1.0")
        await _add_calibration_row(
            db_session,
            workflow_id=workflow.id,
            run_id=run.id,
            agent_id="planner",
            confidence_score=0.9,
            decision="approved",
        )

    # "2.0": 5 decisions, all rejected (0%) — diverges from the agent's
    # overall rate (50%) by 50 points, well past the 20-point threshold,
    # and has exactly _MIN_DECISIONS_FOR_FLAGGING (5) decisions.
    for _ in range(5):
        run = await _make_run_and_step(db_session, agent_id="planner", prompt_version="2.0")
        await _add_calibration_row(
            db_session,
            workflow_id=workflow.id,
            run_id=run.id,
            agent_id="planner",
            confidence_score=0.9,
            decision="rejected",
        )
    await db_session.commit()

    response = await db_client.get(
        "/api/v1/calibration/summary", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    agent = response.json()["agents"][0]
    assert agent["total_decisions"] == 10
    assert agent["approval_rate"] == pytest.approx(0.5)

    by_version = {v["prompt_version"]: v for v in agent["by_prompt_version"]}
    assert by_version["1.0"]["flagged_miscalibrated"] is True
    assert by_version["2.0"]["flagged_miscalibrated"] is True


async def test_summary_does_not_flag_a_diverging_version_below_the_decision_floor(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_admin(db_client, db_session, "admin-floor@example.com")
    workflow = await _make_workflow(db_session)

    # "1.0": 10 decisions, all approved.
    for _ in range(10):
        run = await _make_run_and_step(db_session, agent_id="tester", prompt_version="1.0")
        await _add_calibration_row(
            db_session,
            workflow_id=workflow.id,
            run_id=run.id,
            agent_id="tester",
            confidence_score=0.9,
            decision="approved",
        )

    # "2.0": only 2 decisions, both rejected — a 100-point divergence from
    # the agent's overall rate, but below _MIN_DECISIONS_FOR_FLAGGING (5),
    # so it must not be flagged despite the huge gap.
    for _ in range(2):
        run = await _make_run_and_step(db_session, agent_id="tester", prompt_version="2.0")
        await _add_calibration_row(
            db_session,
            workflow_id=workflow.id,
            run_id=run.id,
            agent_id="tester",
            confidence_score=0.9,
            decision="rejected",
        )
    await db_session.commit()

    response = await db_client.get(
        "/api/v1/calibration/summary", headers={"Authorization": f"Bearer {token}"}
    )

    by_version = {v["prompt_version"]: v for v in response.json()["agents"][0]["by_prompt_version"]}
    assert by_version["2.0"]["total"] == 2
    assert by_version["2.0"]["flagged_miscalibrated"] is False
