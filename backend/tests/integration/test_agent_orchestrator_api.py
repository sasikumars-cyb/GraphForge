"""Integration tests for the Agent Orchestrator HTTP API.

`POST/GET /api/v1/agent-runs` and the `/api/v1/workflows` lifecycle
(create -> continue -> get), exercised end-to-end: real routing, real auth,
real Postgres persistence — only the LLM call and the Neo4j driver are
mocked.

Since run/stage execution is backgrounded (RunCoordinator.execute() split
into create_pending_run + execute_run, dispatched via
app.orchestrator.background_execution.schedule_run_execution — see that
module's docstring), the background task opens its own independent
AsyncSessionLocal(), separate from whatever session served the request.
That rules out the transactional `db_client` fixture here: its DB writes
live in a per-test transaction that's rolled back at teardown and never
visible to a second, real connection — confirmed by these tests failing
with `background_run_vanished` (the background task's `db.get(Run, run_id)`
finding nothing) when they still used `db_client`. These tests use the
plain `client` fixture instead (a real, committed connection on both
sides, matching test_background_execution_api.py's approach), and poll for
a run/stage to leave "queued"/"running" instead of assuming the run is
already terminal by the time the POST returns.

In particular, `test_continue_workflow_failed_stage_is_linked_to_workflow`
is a regression test for a confirmed bug: a failed stage run used to never
get `workflow_id`/`workflow_stage` set (the assignment happened only after
a successful `RunCoordinator.execute()`, which never returns on failure),
so a failed attempt was invisible in the workflow's stage list.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.agents.planning.agent import PlanningLLMError

pytestmark = pytest.mark.asyncio

REGISTER_PAYLOAD = {
    "email": "orchestrator-tests@example.com",
    "password": "correct-horse-battery-staple",
    "full_name": "Orchestrator Tester",
}

_PLANNING_LLM_RESPONSE = (
    '{"executive_summary": "A plan.", "implementation_steps": [], "graph_context_used": false}'
)
_DEVELOPMENT_LLM_RESPONSE = (
    '{"executive_summary": "A blueprint.", "repositories": [], "components": [], '
    '"dependencies": [], "reusable_implementations": [], "implementation_phases": [], '
    '"risks": [], "graph_context_used": false}'
)


@pytest.fixture(autouse=True)
def _mock_title_generation():
    """Title generation makes a real LLM call via create_llm_provider(); this
    dev environment's .env has real GROQ/OPENAI keys, so leaving this
    unmocked hits the live API on every workflow/run creation in this file
    (confirmed: caused real 429 rate-limit failures). Identity fallback
    keeps assertions on workflow/run titles predictable."""
    identity = AsyncMock(side_effect=lambda objective, **_kwargs: objective)
    with (
        patch("app.services.workflow_service.generate_title", identity),
        patch("app.api.v1.routers.agent_runs.generate_title", identity),
    ):
        yield


async def _register_and_get_token(db_client: AsyncClient) -> str:
    await db_client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    login_response = await db_client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    return str(login_response.json()["access_token"])


async def _register_unique_and_get_token(client: AsyncClient) -> str:
    """Like `_register_and_get_token`, but with a unique email — for tests
    on the plain `client` fixture, whose writes are real and committed
    (not rolled back), so reusing REGISTER_PAYLOAD's fixed email across
    tests would collide."""
    payload = {**REGISTER_PAYLOAD, "email": f"orchestrator-{uuid.uuid4()}@example.com"}
    await client.post("/api/v1/auth/register", json=payload)
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    return str(login_response.json()["access_token"])


async def _poll_run_until_terminal(
    client: AsyncClient, run_id: str, headers: dict, timeout_s: float = 5.0
) -> dict:
    """Poll GET /agent-runs/{run_id} until it leaves queued/running.

    Backgrounded execution means a run is not terminal by the time the
    creating POST returns — tests that need the final status/result must
    poll for it instead of asserting on the immediate response body.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        response = await client.get(f"/api/v1/agent-runs/{run_id}", headers=headers)
        detail = response.json()
        if detail["status"] not in ("queued", "running"):
            return detail
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"Run {run_id} did not reach a terminal status in time")
        await asyncio.sleep(0.05)


async def _poll_workflow_stage_until_terminal(
    client: AsyncClient, workflow_id: str, stage: str, headers: dict, timeout_s: float = 5.0
) -> dict:
    """Poll GET /workflows/{id} until the named stage leaves pending/running."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        response = await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)
        detail = response.json()
        stages_by_name = {s["stage"]: s for s in detail["stages"]}
        if stages_by_name[stage]["status"] not in ("pending", "queued", "running"):
            return detail
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"Workflow {workflow_id} stage {stage} did not finish in time")
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# POST/GET /api/v1/agent-runs
# ---------------------------------------------------------------------------


async def test_create_agent_run_happy_path(client: AsyncClient) -> None:
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
        ),
    ):
        response = await client.post(
            "/api/v1/agent-runs",
            json={"subject_reference": "Add JWT auth across services", "goal": "plan_freeform"},
            headers=headers,
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["goal"] == "plan_freeform"
        run_id = body["run_id"]

        detail = await _poll_run_until_terminal(client, run_id, headers)

    assert detail["status"] == "completed"
    assert len(detail["steps"]) == 1
    assert detail["steps"][0]["agent_id"] == "planning"
    assert detail["workflow_id"] is None


async def test_create_agent_run_invalid_goal_returns_404(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.post(
        "/api/v1/agent-runs",
        json={"subject_reference": "Some task", "goal": "not_a_real_goal"},
        headers=headers,
    )
    assert response.status_code == 404


async def test_list_agent_runs_filters_by_subject_id(db_client: AsyncClient) -> None:
    """Regression test: GET /agent-runs?subject_id=... (documented in
    API_CONTRACTS.md, previously unimplemented) now actually filters."""
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
        ),
    ):
        first = await db_client.post(
            "/api/v1/agent-runs",
            json={"subject_reference": "Task A - unique text one", "goal": "plan_freeform"},
            headers=headers,
        )
        second = await db_client.post(
            "/api/v1/agent-runs",
            json={"subject_reference": "Task B - unique text two", "goal": "plan_freeform"},
            headers=headers,
        )

    first_subject_id = (
        await db_client.get(f"/api/v1/agent-runs/{first.json()['run_id']}", headers=headers)
    ).json()["subject"]["subject_id"]
    second_subject_id = (
        await db_client.get(f"/api/v1/agent-runs/{second.json()['run_id']}", headers=headers)
    ).json()["subject"]["subject_id"]
    assert first_subject_id != second_subject_id

    list_response = await db_client.get(
        "/api/v1/agent-runs", params={"subject_id": first_subject_id}, headers=headers
    )
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert len(items) == 1
    assert items[0]["run_id"] == first.json()["run_id"]


# ---------------------------------------------------------------------------
# /api/v1/workflows lifecycle
# ---------------------------------------------------------------------------


async def test_create_workflow_happy_path(client: AsyncClient) -> None:
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
        ),
    ):
        response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        assert response.status_code == 202
        body = response.json()
        assert body["stage"] == "planning"
        assert body["status"] == "queued"
        workflow_id = body["workflow_id"]

        detail = await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", headers)

    assert detail["current_stage"] == "development"
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["planning"]["status"] == "completed"
    assert stages_by_name["planning"]["run_id"] == body["run_id"]
    assert stages_by_name["development"]["status"] == "pending"
    assert stages_by_name["development"]["run_id"] is None


async def test_continue_workflow_happy_path(client: AsyncClient) -> None:
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", headers)

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_DEVELOPMENT_LLM_RESPONSE),
        ),
    ):
        continue_response = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert continue_response.status_code == 202
        assert continue_response.json()["stage"] == "development"

        detail = await _poll_workflow_stage_until_terminal(
            client, workflow_id, "development", headers
        )

    assert detail["current_stage"] == "testing"
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["development"]["status"] == "completed"
    assert len(detail["runs"]) == 2


async def test_continue_workflow_failed_stage_is_linked_to_workflow(client: AsyncClient) -> None:
    """Regression test for the workflow-linkage bug: a failed stage run
    must still show up in the workflow's stage list as 'failed' with its
    run_id populated — not silently vanish as if never attempted.

    Execution is backgrounded, so the failure no longer surfaces as an
    HTTP error on the /continue call itself (that response is already sent
    by the time the agent actually raises) — /continue now returns 202
    with the stage still "running"/"queued", and the failure is only
    observable by polling the workflow/run afterward."""
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", headers)

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(side_effect=RuntimeError("LLM provider unavailable")),
        ),
    ):
        continue_response = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert continue_response.status_code == 202

        detail = await _poll_workflow_stage_until_terminal(
            client, workflow_id, "development", headers
        )

    stages_by_name = {s["stage"]: s for s in detail["stages"]}

    # Before the fix: status stayed "pending" and run_id stayed None here,
    # even though a real run was attempted and failed.
    assert stages_by_name["development"]["status"] == "failed"
    assert stages_by_name["development"]["run_id"] is not None

    failed_run_id = stages_by_name["development"]["run_id"]
    run_detail = (await client.get(f"/api/v1/agent-runs/{failed_run_id}", headers=headers)).json()
    assert run_detail["status"] == "failed"
    assert run_detail["workflow_id"] == workflow_id
    assert "LLM provider unavailable" in (run_detail["error_message"] or "")


async def test_create_workflow_planning_failure_is_linked_and_error_preserved(
    client: AsyncClient,
) -> None:
    """Regression: Planning failures must surface their original AppError
    message (e.g. provider 429) instead of crashing with MissingGreenlet
    inside _link_failed_run(), and the failed run must still be linked to
    the workflow (visible in stage history), not silently vanish.

    Execution is backgrounded, so a downstream agent failure can no longer
    surface as an HTTP error status on the creating POST itself — that
    response (202, "queued") is already sent before the agent ever runs.
    What used to be `assert response.status_code == 502` with the error in
    the response body is now only observable by polling the run/workflow
    afterward and checking the persisted error_message — this test now
    verifies the message/linkage survive the trip through backgrounding
    rather than the (no longer possible) synchronous HTTP surfacing."""
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    rate_limit_message = (
        "Rate limit reached for model llama-3.3-70b-versatile on tokens per day (TPD)."
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(side_effect=PlanningLLMError(rate_limit_message)),
        ),
    ):
        response = await client.post(
            "/api/v1/workflows",
            json={"title": "Planning should fail but still link failed run"},
            headers=headers,
        )
        assert response.status_code == 202
        workflow_id = response.json()["workflow_id"]

        detail = await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", headers)

    stages_by_name = {s["stage"]: s for s in detail["stages"]}

    # The failed planning run is still linked to the workflow and visible
    # in stage history (no MissingGreenlet crash during linking).
    assert stages_by_name["planning"]["status"] == "failed"
    assert stages_by_name["planning"]["run_id"] is not None

    # Original AppError message is preserved on the run (not swallowed or
    # replaced with a generic message).
    failed_run_id = stages_by_name["planning"]["run_id"]
    run_detail = (await client.get(f"/api/v1/agent-runs/{failed_run_id}", headers=headers)).json()
    assert run_detail["status"] == "failed"
    assert run_detail["error_message"] == rate_limit_message


async def test_get_workflow_not_found_returns_404(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.get(
        "/api/v1/workflows/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# IDOR regression tests — a second user must not be able to read, cancel, or
# delete another user's workflow/run, and must not see it in their own list.
# ---------------------------------------------------------------------------


async def test_workflow_not_visible_or_mutable_by_a_different_user(client: AsyncClient) -> None:
    owner_token = await _register_unique_and_get_token(client)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_token = await _register_unique_and_get_token(client)
    other_headers = {"Authorization": f"Bearer {other_token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Owner-only workflow"}, headers=owner_headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", owner_headers)

    # A different, unrelated user must not be able to read it...
    get_response = await client.get(f"/api/v1/workflows/{workflow_id}", headers=other_headers)
    assert get_response.status_code == 404

    # ...continue it...
    continue_response = await client.post(
        f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=other_headers
    )
    assert continue_response.status_code == 404

    # ...cancel it...
    cancel_response = await client.post(
        f"/api/v1/workflows/{workflow_id}/cancel", headers=other_headers
    )
    assert cancel_response.status_code == 404

    # ...or delete it.
    delete_response = await client.delete(f"/api/v1/workflows/{workflow_id}", headers=other_headers)
    assert delete_response.status_code == 404

    # It also must not appear in the other user's own workflow list.
    list_response = await client.get("/api/v1/workflows", headers=other_headers)
    listed_ids = {item["workflow_id"] for item in list_response.json()["items"]}
    assert workflow_id not in listed_ids

    # The actual owner can still do all of the above.
    owner_get_response = await client.get(f"/api/v1/workflows/{workflow_id}", headers=owner_headers)
    assert owner_get_response.status_code == 200


async def test_run_not_visible_or_mutable_by_a_different_user(client: AsyncClient) -> None:
    owner_token = await _register_unique_and_get_token(client)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_token = await _register_unique_and_get_token(client)
    other_headers = {"Authorization": f"Bearer {other_token}"}

    create_response = await client.post(
        "/api/v1/agent-runs",
        json={"subject_reference": "freetext:some standalone task", "goal": "plan_freeform"},
        headers=owner_headers,
    )
    run_id = create_response.json()["run_id"]

    get_response = await client.get(f"/api/v1/agent-runs/{run_id}", headers=other_headers)
    assert get_response.status_code == 404

    cancel_response = await client.post(
        f"/api/v1/agent-runs/{run_id}/cancel", headers=other_headers
    )
    assert cancel_response.status_code == 404

    delete_response = await client.delete(f"/api/v1/agent-runs/{run_id}", headers=other_headers)
    assert delete_response.status_code == 404

    list_response = await client.get("/api/v1/agent-runs", headers=other_headers)
    listed_ids = {item["run_id"] for item in list_response.json()["items"]}
    assert run_id not in listed_ids

    owner_get_response = await client.get(f"/api/v1/agent-runs/{run_id}", headers=owner_headers)
    assert owner_get_response.status_code == 200
