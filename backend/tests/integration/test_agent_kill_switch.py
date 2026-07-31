"""Runtime agent kill switch — the Settings -> Agents page.

Covers app.api.v1.routers.agent_runs's /agents/manifests, /agents/{id}
/disable, /agents/{id}/enable, and the actual enforcement in
RunCoordinator.execute() (a run for a disabled agent must 503 with
error code "agent_disabled").
"""

import uuid
from collections.abc import Generator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.orchestrator.registry import global_registry

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def restore_agent_registry_state() -> Generator[None, None, None]:
    """global_registry (app.orchestrator.registry.global_registry) is a
    true process-level singleton, unlike the DB (rolled back per test via
    db_session) - it is NOT reset between tests. Snapshots every agent's
    enabled state before the test and restores it after, so a test that
    disables an agent can never leak into an unrelated test running later
    in the same process, regardless of how this test exits."""
    before = {
        manifest.agent_id: global_registry.is_enabled(manifest.agent_id)
        for manifest in global_registry.all_manifests()
    }
    yield
    for agent_id, was_enabled in before.items():
        if was_enabled:
            global_registry.enable(agent_id)
        else:
            global_registry.disable(agent_id)


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


async def _register_admin(db_client: AsyncClient, db_session: AsyncSession) -> str:
    email = f"admin+{uuid.uuid4()}@example.com"
    token = await _register_and_get_token(db_client, email)
    await _promote_to_admin(db_session, email)
    return token


async def test_manifests_list_includes_registered_agents_with_enabled_status(
    db_client: AsyncClient,
) -> None:
    token = await _register_and_get_token(db_client, f"user+{uuid.uuid4()}@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.get("/api/v1/agent-runs/agents/manifests", headers=headers)

    assert response.status_code == 200
    body = response.json()
    agent_ids = {row["agent_id"] for row in body}
    assert "planning" in agent_ids
    assert all(row["enabled"] is True for row in body), "nothing else should be disabled here"


async def test_non_admin_cannot_disable_or_enable_agents(db_client: AsyncClient) -> None:
    token = await _register_and_get_token(db_client, f"user+{uuid.uuid4()}@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    disable_response = await db_client.post(
        "/api/v1/agent-runs/agents/planning/disable", headers=headers
    )
    enable_response = await db_client.post(
        "/api/v1/agent-runs/agents/planning/enable", headers=headers
    )

    assert disable_response.status_code == 403
    assert enable_response.status_code == 403
    assert global_registry.is_enabled("planning") is True


async def test_disabling_an_unknown_agent_returns_404(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_admin(db_client, db_session)
    headers = {"Authorization": f"Bearer {token}"}

    response = await db_client.post(
        "/api/v1/agent-runs/agents/not-a-real-agent/disable", headers=headers
    )

    assert response.status_code == 404


async def test_admin_can_disable_and_re_enable_an_agent(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_admin(db_client, db_session)
    headers = {"Authorization": f"Bearer {token}"}

    disable_response = await db_client.post(
        "/api/v1/agent-runs/agents/code_generation/disable", headers=headers
    )
    assert disable_response.status_code == 204

    manifests = await db_client.get("/api/v1/agent-runs/agents/manifests", headers=headers)
    disabled_row = next(row for row in manifests.json() if row["agent_id"] == "code_generation")
    assert disabled_row["enabled"] is False

    enable_response = await db_client.post(
        "/api/v1/agent-runs/agents/code_generation/enable", headers=headers
    )
    assert enable_response.status_code == 204

    manifests_after = await db_client.get("/api/v1/agent-runs/agents/manifests", headers=headers)
    enabled_row = next(
        row for row in manifests_after.json() if row["agent_id"] == "code_generation"
    )
    assert enabled_row["enabled"] is True


async def test_starting_a_run_for_a_disabled_agent_is_rejected_with_503(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The actual kill switch, end to end: RunCoordinator.execute() must
    refuse to start a new run for a disabled agent - not just report its
    status as disabled in the manifests list."""
    admin_token = await _register_admin(db_client, db_session)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    disable_response = await db_client.post(
        "/api/v1/agent-runs/agents/code_generation/disable", headers=admin_headers
    )
    assert disable_response.status_code == 204

    run_response = await db_client.post(
        "/api/v1/agent-runs",
        headers=admin_headers,
        json={"subject_reference": "Add a feature", "goal": "generate_code"},
    )

    assert run_response.status_code == 503
    assert run_response.json()["error"]["code"] == "agent_disabled"


async def test_run_is_accepted_again_once_the_agent_is_re_enabled(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /agent-runs returns 202 synchronously (queues the Run row and
    dispatches execution in the background - see create_run's own
    docstring), so the is_enabled() gate must run inside the request
    handler itself, before that 202. A plain 202 here proves the gate was
    actually lifted, not just that the background execution didn't fail
    for some unrelated reason."""
    admin_token = await _register_admin(db_client, db_session)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    await db_client.post("/api/v1/agent-runs/agents/code_generation/disable", headers=admin_headers)
    await db_client.post("/api/v1/agent-runs/agents/code_generation/enable", headers=admin_headers)

    run_response = await db_client.post(
        "/api/v1/agent-runs",
        headers=admin_headers,
        json={"subject_reference": "Add a feature", "goal": "generate_code"},
    )

    assert run_response.status_code == 202
