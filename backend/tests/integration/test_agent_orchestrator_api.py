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
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.agents.planning.agent import PlanningLLMError
from app.tools.interfaces import ToolResult

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


def _populated_graph() -> ToolResult:
    """A knowledge graph with one indexed repository and its architecture.

    Needed because Context Discovery's readiness is genuinely evidence-based:
    against an empty graph it reports BLOCKED (it cannot tell which service a
    request belongs to, and there is no architecture to reason over), and the
    readiness gate then refuses Planning — correctly. A workflow-orchestration
    test wants a discovery stage that actually succeeds, so it has to supply a
    graph that actually contains something.
    """
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": "auth-service", "id": "repo-1"}],
            "components": [
                {"name": "JwtTokenService", "repository": "auth-service", "type": "service"}
            ],
            "kafka_topics": [],
            "context_text": "auth-service: JwtTokenService",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "1 indexed repository",
            "_traverse_summary": "1 component",
        },
        summary="Queried the architecture graph.",
    )


def _discovery_graph_patch():
    """Patch the graph investigator's retrieval so Context Discovery sees a
    real repository. Patches the provider method rather than the Neo4j driver
    because `GetIndexedRepositoriesTool` reads per-user repository rows from
    Postgres — stubbing the driver alone still yields an empty graph."""
    return patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=_populated_graph()),
    )


@pytest.fixture(autouse=True)
def _mock_title_generation():
    """Title generation makes a real LLM call via create_llm_provider(); this
    dev environment's .env has real GROQ/OPENAI keys, so leaving this
    unmocked hits the live API on every workflow/run creation in this file
    (confirmed: caused real 429 rate-limit failures).

    Workflow/run creation itself no longer calls generate_title() at all —
    both now assign an instant, deterministic placeholder synchronously
    (app.agents.title_generation.fallback_title) and generate the real
    title in a background task (app.orchestrator.background_execution.
    schedule_title_generation) that this fixture's tests never await. That
    background task still calls the real generate_title(), so it's mocked
    at its one canonical import location — patching it there covers both
    the workflow and the agent-run code paths, unlike the two router-level
    patches this used to need."""
    identity = AsyncMock(side_effect=lambda objective, **_kwargs: objective)
    with patch("app.agents.title_generation.generate_title", identity):
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


async def _poll_workflow_until(
    client: AsyncClient,
    workflow_id: str,
    headers: dict,
    predicate: Callable[[dict], bool],
    timeout_s: float = 5.0,
) -> dict:
    """Poll GET /workflows/{id} until `predicate` holds.

    Needed for the clarify path: the stage is already terminal from the pause,
    so waiting on stage status returns immediately and races the resumed run.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        detail = (await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)).json()
        if predicate(detail):
            return detail
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"Workflow {workflow_id} never satisfied the predicate: {detail}")
        await asyncio.sleep(0.05)


async def _poll_workflow_stage_until_terminal(
    client: AsyncClient, workflow_id: str, stage: str, headers: dict, timeout_s: float = 5.0
) -> dict:
    """Poll GET /workflows/{id} until the named stage leaves pending/running.

    A completed stage also implies `current_stage` has advanced past it —
    both are written in the same commit (`workflow_service.
    finalize_stage_run`, called as `RunCoordinator.execute_run`'s
    `on_pre_commit` hook — see that method's own docstring on why this
    must be atomic). Waiting for both here, not just the stage's own
    status, closes a real (if rare) window this helper used to miss: a
    caller reading `detail["stages"][stage]["status"] == "completed"`
    the instant this function returns, while a *concurrent* GET request
    — issued by this same polling loop a beat earlier and still in
    flight when the commit lands — can return a response whose JSON was
    serialized from a snapshot fetched fractionally before the commit's
    both writes became visible together, if the two reads happen to
    straddle it. Retrying once more here (still within the same
    deadline) is enough for a concurrent poll to catch up to the single
    atomic commit rather than the caller asserting on a half-advanced
    read.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        response = await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)
        detail = response.json()
        stages_by_name = {s["stage"]: s for s in detail["stages"]}
        stage_status = stages_by_name[stage]["status"]
        stage_terminal = stage_status not in ("pending", "queued", "running")
        # Only a *completed* stage implies further advancement; a failed
        # stage correctly leaves current_stage right where it was. A
        # completed *final* stage of a sequence also correctly leaves
        # current_stage unchanged (advance_workflow only moves it to
        # "completed" for workflow_types whose TERMINAL_BEHAVIOR is
        # itself "completed" — a "planning"-type workflow's last stage
        # instead flips `status` to "awaiting_approval" and leaves
        # current_stage alone) — so either current_stage moving on, or
        # the workflow's own status leaving "in_progress", counts as
        # "fully advanced," not current_stage alone.
        advanced_or_failed = (
            stage_status != "completed"
            or detail["current_stage"] != stage
            or detail["status"] != "in_progress"
        )
        if stage_terminal and advanced_or_failed:
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


async def test_standalone_development_run_grounds_in_a_prior_planning_run(
    client: AsyncClient,
) -> None:
    """End-to-end: a standalone Planning run, then a standalone Development
    run referencing it via `planning_run_id` — proves the full router ->
    `_load_standalone_planning_context` -> shim -> `get_stage_result()` ->
    `format_planning_block()` chain actually works over real HTTP/DB, not
    just the isolated helper-function unit tests in
    test_agent_runs_standalone_planning.py."""
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
        planning_response = await client.post(
            "/api/v1/agent-runs",
            json={"subject_reference": "Add JWT auth across services", "goal": "plan_freeform"},
            headers=headers,
        )
        assert planning_response.status_code == 202
        planning_run_id = planning_response.json()["run_id"]
        planning_detail = await _poll_run_until_terminal(client, planning_run_id, headers)
    assert planning_detail["status"] == "completed"

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_DEVELOPMENT_LLM_RESPONSE),
        ),
    ):
        dev_response = await client.post(
            "/api/v1/agent-runs",
            json={
                "subject_reference": "Implement the plan",
                "goal": "develop_change_plan",
                "planning_run_id": planning_run_id,
            },
            headers=headers,
        )
        assert dev_response.status_code == 202
        dev_run_id = dev_response.json()["run_id"]
        dev_detail = await _poll_run_until_terminal(client, dev_run_id, headers)

    assert dev_detail["status"] == "completed"
    grounding_evidence = [
        e
        for e in dev_detail["steps"][0]["evidence"]
        if e["reference"] == "read_prior_stage_context"
    ]
    assert grounding_evidence, "expected a read_prior_stage_context evidence entry"
    assert "Read the full Planning stage result" in grounding_evidence[0]["summary"]
    # Not part of a Workflow — this is what proves grounding came from the
    # planning_run_id shim, not from a real Workflow linkage.
    assert dev_detail["workflow_id"] is None


async def test_planning_run_id_rejected_for_unsupported_goal(client: AsyncClient) -> None:
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
        planning_response = await client.post(
            "/api/v1/agent-runs",
            json={"subject_reference": "Add JWT auth across services", "goal": "plan_freeform"},
            headers=headers,
        )
        planning_run_id = planning_response.json()["run_id"]
        await _poll_run_until_terminal(client, planning_run_id, headers)

    response = await client.post(
        "/api/v1/agent-runs",
        json={
            "subject_reference": "Another plan",
            "goal": "plan_freeform",
            "planning_run_id": planning_run_id,
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "planning_run_id_unsupported_goal"


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
        # context_discovery is now the first stage of a "planning"-type
        # workflow (see WORKFLOW_TYPE_STAGES) — it runs before planning,
        # not instead of it.
        assert body["stage"] == "context_discovery"
        assert body["status"] == "queued"
        workflow_id = body["workflow_id"]

        detail = await _poll_workflow_stage_until_terminal(
            client, workflow_id, "context_discovery", headers
        )

    assert detail["current_stage"] == "planning"
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["context_discovery"]["status"] == "completed"
    assert stages_by_name["context_discovery"]["run_id"] == body["run_id"]
    assert stages_by_name["planning"]["status"] == "pending"
    assert stages_by_name["planning"]["run_id"] is None


async def test_continue_workflow_happy_path(client: AsyncClient) -> None:
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Stage 1: context_discovery — runs automatically on creation, no LLM
    # call of its own in this test (no Jira/Confluence/GitHub reference in
    # the title, and enable_context_discovery defaults to False), only the
    # Neo4j graph tool it shares with every other graph-reading agent.
    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        _discovery_graph_patch(),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "context_discovery", headers)

    # Stage 2: planning — now reads context_discovery's result via
    # get_stage_result() rather than calling the Neo4j tool itself, so no
    # Neo4j mock is needed here, only its own LLM call.
    with patch(
        "app.agents.planning.agent._call_llm",
        new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
    ):
        continue_response = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert continue_response.status_code == 202
        assert continue_response.json()["stage"] == "planning"

        await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", headers)

    # Stage 3: development
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

    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert detail["current_stage"] == "testing"
    assert stages_by_name["development"]["status"] == "completed"
    assert len(detail["runs"]) == 3


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
        _discovery_graph_patch(),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "context_discovery", headers)

    with patch(
        "app.agents.planning.agent._call_llm",
        new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
    ):
        continue_response = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert continue_response.status_code == 202
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
        _discovery_graph_patch(),
    ):
        response = await client.post(
            "/api/v1/workflows",
            json={"title": "Planning should fail but still link failed run"},
            headers=headers,
        )
        assert response.status_code == 202
        workflow_id = response.json()["workflow_id"]

        await _poll_workflow_stage_until_terminal(client, workflow_id, "context_discovery", headers)

    with patch(
        "app.agents.planning.agent._call_llm",
        new=AsyncMock(side_effect=PlanningLLMError(rate_limit_message)),
    ):
        continue_response = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert continue_response.status_code == 202

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
        _discovery_graph_patch(),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Owner-only workflow"}, headers=owner_headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(
            client, workflow_id, "context_discovery", owner_headers
        )

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


# ---------------------------------------------------------------------------
# PATCH /workflows/{id}/stages/{stage}/override — human-override mechanism
# ---------------------------------------------------------------------------


async def test_override_context_discovery_result_is_consumed_by_planning(
    client: AsyncClient,
) -> None:
    """End-to-end: a human-corrected indexed_repositories list, applied via
    PATCH .../stages/context_discovery/override after context_discovery
    completes, must be what Planning's prompt actually reflects when the
    workflow is continued — not the AI's original (uncorrected) output.
    Exercises the full get_stage_result() override-merge path over real
    HTTP/DB, not just the unit-level merge in test_context_override.py."""
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        _discovery_graph_patch(),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "context_discovery", headers)

    # `graph_context_text` is the text Planning's prompt is rendered from, and is
    # one of the few fields a human may correct — the machine's verdict and its
    # evidence are not overridable (see workflow_service._OVERRIDABLE_FIELDS).
    override = {"graph_context_text": "Indexed repositories: corrected-service (acme)."}
    override_response = await client.patch(
        f"/api/v1/workflows/{workflow_id}/stages/context_discovery/override",
        json={"override": override},
        headers=headers,
    )
    assert override_response.status_code == 200
    assert override_response.json()["workflow_id"] == workflow_id

    captured_prompt: dict[str, str] = {}

    async def _capture_prompt(user_prompt: str, **_kwargs: object) -> str:
        captured_prompt["prompt"] = user_prompt
        return _PLANNING_LLM_RESPONSE

    with patch("app.agents.planning.agent._call_llm", new=_capture_prompt):
        continue_response = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert continue_response.status_code == 202
        await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", headers)

    assert "corrected-service" in captured_prompt["prompt"], (
        "Planning must consume the overridden context_discovery result, "
        "not the AI's original (uncorrected) indexed repository."
    )


async def test_override_stage_result_404s_for_a_stage_that_never_completed(
    client: AsyncClient,
) -> None:
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        _discovery_graph_patch(),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "context_discovery", headers)

    # "planning" hasn't run yet — nothing to override.
    response = await client.patch(
        f"/api/v1/workflows/{workflow_id}/stages/planning/override",
        json={"override": {"executive_summary": "Edited."}},
        headers=headers,
    )
    assert response.status_code == 404


async def test_override_stage_result_not_visible_or_mutable_by_a_different_user(
    client: AsyncClient,
) -> None:
    owner_token = await _register_unique_and_get_token(client)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_token = await _register_unique_and_get_token(client)
    other_headers = {"Authorization": f"Bearer {other_token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        _discovery_graph_patch(),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Owner-only workflow"}, headers=owner_headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(
            client, workflow_id, "context_discovery", owner_headers
        )

    response = await client.patch(
        f"/api/v1/workflows/{workflow_id}/stages/context_discovery/override",
        json={"override": {"indexed_repositories": []}},
        headers=other_headers,
    )
    assert response.status_code == 404


async def test_planning_is_refused_when_context_discovery_is_blocked(
    client: AsyncClient,
) -> None:
    """The safety property the whole readiness model exists for: Planning must
    never start when Context Discovery could not establish enough context.

    Run against an empty graph — no indexed repositories, no architecture —
    which is exactly the state where a plan would be guesswork. The refusal
    must also say *what* is missing and what to do about it: a 400 that only
    says "blocked" leaves the user with no way forward.
    """
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        detail = await _poll_workflow_stage_until_terminal(
            client, workflow_id, "context_discovery", headers
        )

    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["context_discovery"]["status"] == "completed"

    response = await client.post(
        f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "context_discovery_blocked"
    message = body["error"]["message"]
    # Names the specific missing capability, the specific unsatisfied signal,
    # and the remediation — not a generic "unspecified".
    assert "repository" in message.lower()
    assert "no repositories are indexed" in message.lower()
    assert "Index the repository" in message

    # And BLOCKED is not merely discouraged: acknowledging it must not work.
    forced = await client.post(
        f"/api/v1/workflows/{workflow_id}/continue",
        json={"acknowledge_partial": True},
        headers=headers,
    )
    assert forced.status_code == 400
    assert forced.json()["error"]["code"] == "context_discovery_blocked"

    detail = (await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)).json()
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["planning"]["run_id"] is None, "Planning must never have started"
    assert detail["current_stage"] == "planning"


async def test_planning_requires_acknowledge_partial_when_context_discovery_is_partial(
    client: AsyncClient,
) -> None:
    """The full PARTIAL contract, end to end: a Jira-linked request resolves
    a real work item (so "Design documentation retrieved" becomes a
    recommended-but-unmet signal — Confluence is never connected in this
    test environment) while everything else Planning strictly needs is
    present, so Context Discovery finishes at PARTIAL, not READY or BLOCKED.

    Regression test for the exact contract the frontend's "Continue anyway"
    button depends on: a bare `/continue` must be refused with 409
    `context_discovery_partial` (never silently ignored, never a 500), and
    the identical call with `acknowledge_partial: true` must succeed and
    actually queue Planning.
    """
    from app.agents._contract import Evidence
    from app.context_pipeline.models import ProviderCapability, ResolvedArtifact

    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    artifact = ResolvedArtifact(
        provider="jira",
        capability=ProviderCapability.ISSUE_TRACKER,
        reference=None,
        title="Duplicate records in SCD2 merge",
        text="Duplicate records in SCD2 merge during concurrent writes. Repo: auth-service.",
        evidence=Evidence(
            kind="tool_call", reference="jira:fetch_work_item:PROJ-1", summary="Retrieved PROJ-1."
        ),
    )

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        _discovery_graph_patch(),
        patch(
            "app.context_pipeline.reasoning.investigators.JiraProvider.resolve",
            new=AsyncMock(return_value=artifact),
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows",
            json={"title": "Prepare plan for JIRA : PROJ-1"},
            headers=headers,
        )
        workflow_id = create_response.json()["workflow_id"]
        detail = await _poll_workflow_stage_until_terminal(
            client, workflow_id, "context_discovery", headers
        )

    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["context_discovery"]["status"] == "completed"

    # Bare continue: refused, 409, with the documented machine-readable code
    # — never silently ignored and never a generic/500 error.
    refused = await client.post(
        f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
    )
    assert refused.status_code == 409
    body = refused.json()
    assert body["error"]["code"] == "context_discovery_partial"
    assert "confidence" in body["error"]["message"].lower()

    # Nothing was queued by the refused attempt.
    detail = (await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)).json()
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["planning"]["run_id"] is None
    assert detail["current_stage"] == "planning"

    # Acknowledging proceeds — the exact payload the frontend's "Continue
    # anyway" button sends.
    with patch(
        "app.agents.planning.agent._call_llm",
        new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
    ):
        acknowledged = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue",
            json={"acknowledge_partial": True},
            headers=headers,
        )
        assert acknowledged.status_code == 202, acknowledged.text
        assert acknowledged.json()["stage"] == "planning"

        detail = await _poll_workflow_stage_until_terminal(client, workflow_id, "planning", headers)
    stages_by_name = {s["stage"]: s for s in detail["stages"]}
    assert stages_by_name["planning"]["run_id"] is not None
    assert stages_by_name["planning"]["status"] == "completed"


def _ambiguous_graph() -> ToolResult:
    """Two repositories, each owning an identically-named component, so
    relevance ranking genuinely cannot separate them and Context Discovery has
    to ask which one to use."""
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [
                {"name": "payment-service", "id": "repo-1"},
                {"name": "billing-service", "id": "repo-2"},
            ],
            "components": [
                {"name": "RetryHandler", "repository": "payment-service", "type": "service"},
                {"name": "RetryHandler", "repository": "billing-service", "type": "service"},
            ],
            "kafka_topics": [],
            "context_text": "two candidates",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "2 indexed repositories",
            "_traverse_summary": "2 components",
        },
        summary="Queried the architecture graph.",
    )


async def test_clarification_round_trip_unblocks_the_workflow(client: AsyncClient) -> None:
    """The full pause -> answer -> verify -> continue path, end to end.

    Regression test for a bug that made the whole clarification feature a dead
    end: answering successfully advanced `current_stage` but left
    `workflow.status` at "awaiting_clarification", so `/continue` refused
    forever with "answer the pending clarification question first" for a
    question that no longer existed. Nothing covered this path — it was only
    found by driving the real UI.
    """
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
            new=AsyncMock(return_value=_ambiguous_graph()),
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows",
            json={"title": "Add exponential backoff to the retry handler"},
            headers=headers,
        )
        workflow_id = create_response.json()["workflow_id"]

        # Discovery must pause rather than guess between the two repositories.
        detail = await _poll_workflow_stage_until_terminal(
            client, workflow_id, "context_discovery", headers
        )
        assert detail["status"] == "awaiting_clarification"
        question = detail["pending_clarification"]
        assert question is not None
        assert question["question_id"] == "gap_repository"
        # Real candidate values only — never a UI instruction.
        assert set(question["options"]) == {"payment-service", "billing-service"}
        assert question["investigated"], "the question must show what was tried first"

        # Planning must stay gated while the question is open.
        blocked = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert blocked.status_code == 400
        assert blocked.json()["error"]["code"] == "workflow_terminal"

        answer = await client.post(
            f"/api/v1/workflows/{workflow_id}/clarify",
            json={"question_id": "gap_repository", "answer": "billing-service"},
            headers=headers,
        )
        assert answer.status_code == 202, answer.text

        # Wait for the resumed run to actually finish, not merely for the
        # transient "resumed" status the clarify endpoint sets.
        detail = await _poll_workflow_until(
            client,
            workflow_id,
            headers,
            lambda d: d["current_stage"] == "planning",
        )

    # The answer was verified against the graph, so discovery is now READY and
    # the workflow is runnable again — not stuck claiming it needs input.
    assert detail["status"] != "awaiting_clarification"
    assert detail["pending_clarification"] is None
    assert detail["current_stage"] == "planning"

    with patch(
        "app.agents.planning.agent._call_llm",
        new=AsyncMock(return_value=_PLANNING_LLM_RESPONSE),
    ):
        proceed = await client.post(
            f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
        )
        assert proceed.status_code == 202, proceed.text
        assert proceed.json()["stage"] == "planning"


async def test_an_unverifiable_answer_re_asks_instead_of_being_accepted(
    client: AsyncClient,
) -> None:
    """An answer naming a repository the graph does not contain must not be
    accepted. Discovery re-asks, acknowledging the failed answer, and Planning
    stays gated — the hallucination path a UI instruction label used to take."""
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
        patch(
            "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
            new=AsyncMock(return_value=_ambiguous_graph()),
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows",
            json={"title": "Add exponential backoff to the retry handler"},
            headers=headers,
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "context_discovery", headers)

        answered = await client.post(
            f"/api/v1/workflows/{workflow_id}/clarify",
            json={"question_id": "gap_repository", "answer": "Select a repository"},
            headers=headers,
        )
        assert answered.status_code == 202, answered.text

        # Paused again, on a *re-ask* — so wait for the second question rather
        # than for the status to change (it stays awaiting_clarification).
        detail = await _poll_workflow_until(
            client,
            workflow_id,
            headers,
            lambda d: (
                (d.get("pending_clarification") or {}).get("why", "").startswith("I couldn't find")
            ),
        )

    # Still paused, re-asking — and the reason names the answer that failed.
    assert detail["status"] == "awaiting_clarification"
    question = detail["pending_clarification"]
    assert question is not None
    assert "Select a repository" in question["why"]
    assert set(question["options"]) == {"payment-service", "billing-service"}


async def test_override_cannot_forge_the_readiness_verdict_over_http(
    client: AsyncClient,
) -> None:
    """The override endpoint merges its payload over the stage result, so an
    unrestricted one let a client set `readiness: "READY"` and walk straight past
    the BLOCKED gate. Exercised over real HTTP because that is how it was
    reachable."""
    token = await _register_unique_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Empty graph -> BLOCKED.
    with (
        patch("app.tools.implementations.neo4j_tool.get_driver", return_value=MagicMock()),
        patch(
            "app.tools.implementations.neo4j_tool.Neo4jGraphRepository", return_value=MagicMock()
        ),
    ):
        create_response = await client.post(
            "/api/v1/workflows", json={"title": "Implement JWT auth"}, headers=headers
        )
        workflow_id = create_response.json()["workflow_id"]
        await _poll_workflow_stage_until_terminal(client, workflow_id, "context_discovery", headers)

    forged = await client.patch(
        f"/api/v1/workflows/{workflow_id}/stages/context_discovery/override",
        json={"override": {"readiness": "READY", "confidence": 1.0}},
        headers=headers,
    )
    assert forged.status_code == 400
    assert forged.json()["error"]["code"] == "field_not_overridable"

    facts = await client.patch(
        f"/api/v1/workflows/{workflow_id}/stages/context_discovery/override",
        json={"override": {"graph_components": [{"name": "TotallyFakeComponent"}]}},
        headers=headers,
    )
    assert facts.status_code == 400

    # A legitimate prose correction is still accepted...
    allowed = await client.patch(
        f"/api/v1/workflows/{workflow_id}/stages/context_discovery/override",
        json={"override": {"graph_context_text": "a human's correction"}},
        headers=headers,
    )
    assert allowed.status_code == 200

    # ...and the gate still refuses, because the verdict was never editable.
    blocked = await client.post(
        f"/api/v1/workflows/{workflow_id}/continue", json={}, headers=headers
    )
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "context_discovery_blocked"
