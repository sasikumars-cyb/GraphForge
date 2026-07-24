"""Integration tests for the Agent Orchestrator HTTP API.

`POST/GET /api/v1/agent-runs` and the `/api/v1/workflows` lifecycle
(create -> continue -> get), exercised end-to-end: real routing, real auth,
real Postgres persistence (via the transactional `db_client` fixture) —
only the LLM call and the Neo4j driver are mocked.

Before this file, every agent/orchestrator test mocked the DB session
itself, so nothing had ever verified these routes work against a real
transactional session. In particular,
`test_continue_workflow_failed_stage_is_linked_to_workflow` is a
regression test for a confirmed bug: a failed stage run used to never get
`workflow_id`/`workflow_stage` set (the assignment happened only after a
successful `RunCoordinator.execute()`, which never returns on failure), so
a failed attempt was invisible in the workflow's stage list.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# POST/GET /api/v1/agent-runs
# ---------------------------------------------------------------------------


async def test_create_agent_run_happy_path(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE)),
    ):
        response = await db_client.post(
            "/api/v1/agent-runs",
            json={"subject_reference": "Add JWT auth across services", "goal": "plan_freeform"},
            headers=headers,
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["goal"] == "plan_freeform"
    run_id = body["run_id"]

    get_response = await db_client.get(f"/api/v1/agent-runs/{run_id}", headers=headers)
    assert get_response.status_code == 200
    detail = get_response.json()
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
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE)),
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


async def test_create_workflow_happy_path(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE)),
    ):
        response = await db_client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
    assert response.status_code == 202
    body = response.json()
    assert body["stage"] == "planning"
    assert body["status"] == "completed"
    workflow_id = body["workflow_id"]

    detail_response = await db_client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["current_stage"] == "development"
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["planning"]["status"] == "completed"
    assert stages_by_name["planning"]["run_id"] == body["run_id"]
    assert stages_by_name["development"]["status"] == "pending"
    assert stages_by_name["development"]["run_id"] is None


async def test_continue_workflow_happy_path(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE)),
    ):
        create_response = await db_client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
    workflow_id = create_response.json()["workflow_id"]

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_DEVELOPMENT_LLM_RESPONSE),
        ),
    ):
        continue_response = await db_client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
    assert continue_response.status_code == 202
    assert continue_response.json()["stage"] == "development"

    detail = (await db_client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)).json()
    assert detail["current_stage"] == "testing"
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["development"]["status"] == "completed"
    assert len(detail["runs"]) == 2


async def test_continue_workflow_failed_stage_is_linked_to_workflow(db_client: AsyncClient) -> None:
    """Regression test for the workflow-linkage bug: a failed stage run
    must still show up in the workflow's stage list as 'failed' with its
    run_id populated — not silently vanish as if never attempted."""
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE)),
    ):
        create_response = await db_client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
    workflow_id = create_response.json()["workflow_id"]

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(side_effect=RuntimeError("LLM provider unavailable")),
        ),
    ):
        continue_response = await db_client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
    assert continue_response.status_code == 500

    detail = (await db_client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)).json()
    stages_by_name = {s["stage"]: s for s in detail["stages"]}

    # Before the fix: status stayed "pending" and run_id stayed None here,
    # even though a real run was attempted and failed.
    assert stages_by_name["development"]["status"] == "failed"
    assert stages_by_name["development"]["run_id"] is not None

    failed_run_id = stages_by_name["development"]["run_id"]
    run_detail = (
        await db_client.get(f"/api/v1/agent-runs/{failed_run_id}", headers=headers)
    ).json()
    assert run_detail["status"] == "failed"
    assert run_detail["workflow_id"] == workflow_id
    assert "LLM provider unavailable" in (run_detail["error_message"] or "")


async def test_create_workflow_planning_failure_returns_original_app_error(
    db_client: AsyncClient,
) -> None:
    """Regression: Planning failures must surface their original AppError
    (e.g. provider 429) instead of crashing with MissingGreenlet inside
    _link_failed_run()."""
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    rate_limit_message = (
        "Rate limit reached for model llama-3.3-70b-versatile on tokens per day (TPD)."
    )

    with (
        patch("app.agents.planning.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.planning.agent.Neo4jGraphRepository", return_value=MagicMock()),
        patch(
            "app.agents.planning.agent._call_llm",
            new=AsyncMock(side_effect=PlanningLLMError(rate_limit_message)),
        ),
    ):
        response = await db_client.post(
            "/api/v1/workflows",
            json={"title": "Planning should fail but still link failed run"},
            headers=headers,
        )

    # Original AppError is preserved (not wrapped into workflow_execution_error)
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "planning_llm_error"
    assert body["error"]["message"] == rate_limit_message

    # The failed planning run is still linked to the workflow and visible
    # in stage history (no MissingGreenlet crash during linking).
    workflows_response = await db_client.get("/api/v1/workflows", headers=headers)
    assert workflows_response.status_code == 200
    target = next(
        w
        for w in workflows_response.json()["items"]
        if w["title"] == "Planning should fail but still link failed run"
    )

    detail_response = await db_client.get(
        f"/api/v1/workflows/{target['workflow_id']}",
        headers=headers,
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    stages_by_name = {s["stage"]: s for s in detail["stages"]}

    assert stages_by_name["planning"]["status"] == "failed"
    assert stages_by_name["planning"]["run_id"] is not None


async def test_get_workflow_not_found_returns_404(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.get(
        "/api/v1/workflows/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404
