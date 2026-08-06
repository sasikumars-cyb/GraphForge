"""Integration tests for GET /api/v1/metrics/workflows/{id}/llm-usage —
the per-stage LLM usage breakdown (model, tokens, cost, latency, call
count) a workflow's "Recent Workflows" row on the Metrics page links to.

LLMInvocation rows are inserted directly via db_session (same technique
test_calibration_api.py uses) rather than run through the real agent
execution path - metrics_service.get_workflow_llm_usage only reads these
rows and doesn't care how they were produced; the real end-to-end write
path is covered by test_llm_invocation_persistence.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_step import AgentStep
from app.models.llm_invocation import LLMInvocation
from app.models.run import Run
from app.models.workflow import Workflow

pytestmark = pytest.mark.asyncio


async def _register_and_login(db_client: AsyncClient, email: str) -> dict[str, str]:
    await db_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple", "full_name": "Test"},
    )
    login = await db_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery-staple"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _owner_user_id(db_client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    me = await db_client.get("/api/v1/auth/me", headers=headers)
    return uuid.UUID(me.json()["id"])


async def _make_run_with_invocation(
    db_session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    stage: str,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    latency_ms: int,
) -> None:
    run = Run(
        id=uuid.uuid4(),
        subject_id=f"workflow:{workflow_id}",
        subject_type="workflow",
        goal=stage,
        workflow_id=workflow_id,
        workflow_stage=stage,
        status="completed",
    )
    db_session.add(run)
    await db_session.flush()

    step = AgentStep(
        id=uuid.uuid4(),
        run_id=run.id,
        agent_id=stage,
        status="completed",
    )
    db_session.add(step)
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add(
        LLMInvocation(
            id=uuid.uuid4(),
            agent_step_id=step.id,
            run_id=run.id,
            provider=provider,
            model=model,
            stage=stage,
            status="completed",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=cost_usd,
            latency_ms=latency_ms,
            started_at=now,
            finished_at=now,
        )
    )
    await db_session.flush()


@pytest.fixture
async def owner_headers(db_client: AsyncClient) -> dict[str, str]:
    email = f"llm-usage-owner-{uuid.uuid4().hex[:8]}@example.com"
    return await _register_and_login(db_client, email)


@pytest.fixture
async def other_user_headers(db_client: AsyncClient) -> dict[str, str]:
    email = f"llm-usage-other-{uuid.uuid4().hex[:8]}@example.com"
    return await _register_and_login(db_client, email)


async def test_groups_llm_usage_by_stage_with_correct_totals(
    db_client: AsyncClient,
    db_session: AsyncSession,
    owner_headers: dict[str, str],
) -> None:
    owner_id = await _owner_user_id(db_client, owner_headers)
    workflow = Workflow(
        id=uuid.uuid4(),
        user_id=owner_id,
        title="Add rate limiting",
        original_prompt="Add rate limiting to the payment API",
    )
    db_session.add(workflow)
    await db_session.flush()

    # Planning: two calls, two different models (a fallback) - proves
    # `models` lists every model actually used in the stage, not just one.
    await _make_run_with_invocation(
        db_session,
        workflow_id=workflow.id,
        stage="planning",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.01,
        latency_ms=800,
    )
    await _make_run_with_invocation(
        db_session,
        workflow_id=workflow.id,
        stage="planning",
        model="claude-haiku",
        provider="bedrock",
        prompt_tokens=200,
        completion_tokens=100,
        cost_usd=0.02,
        latency_ms=1200,
    )
    # Development: one call.
    await _make_run_with_invocation(
        db_session,
        workflow_id=workflow.id,
        stage="development",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=500,
        completion_tokens=300,
        cost_usd=0.05,
        latency_ms=2000,
    )
    await db_session.commit()

    response = await db_client.get(
        f"/api/v1/metrics/workflows/{workflow.id}/llm-usage", headers=owner_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_id"] == str(workflow.id)
    assert body["workflow_title"] == "Add rate limiting"

    by_stage = {s["stage"]: s for s in body["stages"]}
    assert set(by_stage) == {"planning", "development"}

    planning = by_stage["planning"]
    assert planning["calls"] == 2
    assert planning["input_tokens"] == 300
    assert planning["output_tokens"] == 150
    assert planning["total_tokens"] == 450
    assert planning["cost_usd"] == pytest.approx(0.03)
    assert planning["models"] == ["claude-haiku", "gpt-4o"]

    development = by_stage["development"]
    assert development["calls"] == 1
    assert development["input_tokens"] == 500
    assert development["output_tokens"] == 300
    assert development["cost_usd"] == pytest.approx(0.05)
    assert development["models"] == ["gpt-4o"]


async def test_another_users_workflow_is_404_not_403(
    db_client: AsyncClient,
    db_session: AsyncSession,
    owner_headers: dict[str, str],
    other_user_headers: dict[str, str],
) -> None:
    owner_id = await _owner_user_id(db_client, owner_headers)
    workflow = Workflow(
        id=uuid.uuid4(),
        user_id=owner_id,
        title="Private workflow",
        original_prompt="Something private",
    )
    db_session.add(workflow)
    await db_session.flush()
    await _make_run_with_invocation(
        db_session,
        workflow_id=workflow.id,
        stage="planning",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.001,
        latency_ms=100,
    )
    await db_session.commit()

    response = await db_client.get(
        f"/api/v1/metrics/workflows/{workflow.id}/llm-usage", headers=other_user_headers
    )

    assert response.status_code == 404


async def test_invalid_workflow_id_is_404(
    db_client: AsyncClient, owner_headers: dict[str, str]
) -> None:
    response = await db_client.get(
        "/api/v1/metrics/workflows/not-a-uuid/llm-usage", headers=owner_headers
    )

    assert response.status_code == 404
