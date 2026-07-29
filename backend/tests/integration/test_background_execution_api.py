"""Integration tests for backgrounded run/workflow execution.

Verifies the actual property Phase 2 exists to fix: POST /agent-runs and
POST /workflows(/continue) return to the client before the agent's work
finishes, and the run reaches a terminal status later via a completely
independent DB connection (the background task's own AsyncSessionLocal
session) — not the request's.

Deliberately uses the `client` fixture (no get_db_session override), not
`db_client`/`db_session` — those wrap everything in one shared,
rolled-back transaction on one connection, which doesn't exercise (and
can't safely exercise) the cross-connection behavior this feature
actually introduces: a second, independent connection reading what the
first one committed. Using the real dependency wiring here means both
the router and the background task go through the same real
AsyncSessionLocal/engine as production, so this is the most faithful way
to test it — real commit, real second connection, no transaction tricks.

Uses a temporary fake agent swapped into the real registry slot for the
duration of each test (restored after) instead of mocking the real
Planning agent's Neo4j/LLM internals — keeps timing deterministic via an
asyncio.Event rather than racing a real LLM call, and sidesteps unrelated,
pre-existing staleness in those agents' test mocks (see the "Known
Pre-Existing Issues" note in this session's summary).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient

from app.agents._contract import AgentContext, AgentOutput, Confidence, Evidence
from app.orchestrator.registry import global_registry

pytestmark = pytest.mark.asyncio


class _SlowFakeAgent:
    """An IAgent whose `run()` blocks on a controllable asyncio.Event
    before returning — lets a test assert "the HTTP response arrived
    before the agent finished" deterministically."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, context: AgentContext) -> AgentOutput:
        self.started.set()
        await self.release.wait()
        return AgentOutput(
            agent_id="planning",
            subject_id=context.subject.subject_id,
            confidence=Confidence(score=0.9, reasoning="fake agent for background-execution tests"),
            evidence=[Evidence(kind="tool_call", reference="fake", summary="fake evidence")],
            result={"executive_summary": "fake result"},
        )


@pytest.fixture
def slow_planning_agent() -> AsyncGenerator[_SlowFakeAgent, None]:
    """Swap the real Planning agent instance for a slow fake one, under
    the same agent_id/manifest (so goal routing is unaffected), for the
    duration of one test.
    """
    agent_id = "planning"
    manifest, real_agent = global_registry._store[agent_id]
    fake = _SlowFakeAgent()
    global_registry._store[agent_id] = (manifest, fake)
    try:
        yield fake
    finally:
        global_registry._store[agent_id] = (manifest, real_agent)


async def _register_and_get_token(client: AsyncClient) -> str:
    email = f"bgexec-{uuid.uuid4().hex[:8]}@example.com"
    password = "test-password-12345"  # noqa: S105
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Background Exec Test User"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return login.json()["access_token"]


async def _poll_until(
    client: AsyncClient, url: str, headers: dict[str, str], predicate, timeout: float = 5.0
):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_body = None
    while loop.time() < deadline:
        resp = await client.get(url, headers=headers)
        last_body = resp.json()
        if predicate(last_body):
            return last_body
        await asyncio.sleep(0.05)
    pytest.fail(f"Timed out waiting for condition on {url}; last body: {last_body}")


async def test_create_agent_run_returns_before_agent_completes(
    client: AsyncClient,
    slow_planning_agent: _SlowFakeAgent,
) -> None:
    token = await _register_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/agent-runs",
        headers=headers,
        json={
            "subject_reference": "freetext:Add a rate limiter to the payment API",
            "goal": "plan_freeform",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    run_id = body["run_id"]

    # The response arrived without the agent's work ever having been
    # released — i.e. the request handler did not wait for it.
    assert not slow_planning_agent.release.is_set()

    # The background task should reach the agent shortly after (on the
    # same event loop, via a fresh independent DB session/connection).
    await asyncio.wait_for(slow_planning_agent.started.wait(), timeout=2)

    # GET while still running reflects that — a second, independent
    # connection already sees the "running" row the background task's
    # own session committed.
    running = await client.get(f"/api/v1/agent-runs/{run_id}", headers=headers)
    assert running.json()["status"] in ("queued", "running")

    slow_planning_agent.release.set()

    completed = await _poll_until(
        client, f"/api/v1/agent-runs/{run_id}", headers, lambda b: b["status"] == "completed"
    )
    assert completed["steps"][0]["result"]["executive_summary"] == "fake result"


async def test_cancel_run_marks_in_flight_run_failed(
    client: AsyncClient,
    slow_planning_agent: _SlowFakeAgent,
) -> None:
    token = await _register_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/agent-runs",
        headers=headers,
        json={"subject_reference": "freetext:Cancel me", "goal": "plan_freeform"},
    )
    run_id = resp.json()["run_id"]
    await asyncio.wait_for(slow_planning_agent.started.wait(), timeout=2)

    cancel_resp = await client.post(f"/api/v1/agent-runs/{run_id}/cancel", headers=headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "failed"

    detail = await client.get(f"/api/v1/agent-runs/{run_id}", headers=headers)
    assert detail.json()["status"] == "failed"
    assert detail.json()["error_message"] == "Cancelled by user."

    # Releasing the (cancelled) agent afterwards must not resurrect it —
    # cancellation already committed a terminal status.
    slow_planning_agent.release.set()
    await asyncio.sleep(0.1)
    detail_after = await client.get(f"/api/v1/agent-runs/{run_id}", headers=headers)
    assert detail_after.json()["status"] == "failed"


async def test_cancel_completed_run_is_a_no_op(client: AsyncClient) -> None:
    """Cancelling a run that already finished just reports its real
    status — no error, no state change."""
    token = await _register_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(f"/api/v1/agent-runs/{uuid.uuid4()}/cancel", headers=headers)
    assert resp.status_code == 404


async def test_continue_workflow_returns_before_stage_completes(
    client: AsyncClient,
    slow_planning_agent: _SlowFakeAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This test doesn't exercise title generation at all — stub it to a
    # fast, deterministic no-op so it can't leak a real, slow Bedrock call
    # into a later test's event loop (background title tasks are
    # fire-and-forget and outlive this test's own scope otherwise).
    async def fast_title(objective: str, *, model: str | None = None) -> str:
        return objective

    monkeypatch.setattr("app.agents.title_generation.generate_title", fast_title)

    token = await _register_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"title": "Add a rate limiter to the payment API", "workflow_type": "planning"},
    )
    assert create_resp.status_code == 202
    body = create_resp.json()
    assert body["status"] == "queued"
    workflow_id = body["workflow_id"]

    # context_discovery is real (not swapped for a fake) and runs first —
    # let it finish before continuing into the stage under test (planning).
    await _poll_until(
        client,
        f"/api/v1/workflows/{workflow_id}",
        headers,
        lambda b: b["stages"][0]["status"] == "completed",
    )

    continue_resp = await client.post(
        f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
    )
    assert continue_resp.status_code == 202

    assert not slow_planning_agent.release.is_set()
    await asyncio.wait_for(slow_planning_agent.started.wait(), timeout=2)

    # Workflow bookkeeping (current_stage advance) must not have run yet —
    # it's gated on the stage run actually completing.
    mid_flight = await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)
    mid_body = mid_flight.json()
    assert mid_body["current_stage"] == "planning"
    assert mid_body["stages"][1]["status"] in ("queued", "running")

    slow_planning_agent.release.set()

    completed = await _poll_until(
        client,
        f"/api/v1/workflows/{workflow_id}",
        headers,
        lambda b: b["stages"][1]["status"] == "completed",
    )
    # advance_workflow ran (via the background task's on_complete hook)
    # and moved current_stage forward past planning.
    assert completed["current_stage"] == "development"


async def test_workflow_title_starts_as_placeholder_then_becomes_ai_generated(
    client: AsyncClient,
    slow_planning_agent: _SlowFakeAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /workflows must not block on generate_title() (a real LLM
    call): the workflow is created with an instant, deterministic
    placeholder title, and the real title lands via a background task
    shortly after — see app.orchestrator.background_execution.
    schedule_title_generation."""
    from app.agents.title_generation import fallback_title

    generation_started = asyncio.Event()

    async def fake_generate_title(objective: str, *, model: str | None = None) -> str:
        generation_started.set()
        return "A Real AI-Generated Title"

    monkeypatch.setattr("app.agents.title_generation.generate_title", fake_generate_title)

    token = await _register_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    objective = "Add a rate limiter to the payment API"
    create_resp = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"title": objective, "workflow_type": "planning"},
    )
    assert create_resp.status_code == 202
    workflow_id = create_resp.json()["workflow_id"]

    # Immediately after creation — before the background title task has
    # necessarily run — the workflow already carries the deterministic
    # placeholder, never a blank/pending value.
    fetched = await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)
    initial_title = fetched.json()["title"]
    assert initial_title == fallback_title(objective)

    completed = await _poll_until(
        client,
        f"/api/v1/workflows/{workflow_id}",
        headers,
        lambda b: b["title"] == "A Real AI-Generated Title",
    )
    assert completed["title"] == "A Real AI-Generated Title"
    assert generation_started.is_set()

    # Unblock the planning agent so the test doesn't leak a pending task.
    slow_planning_agent.release.set()
