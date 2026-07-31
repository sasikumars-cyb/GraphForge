"""ADR 0011 — WARNING-producing pre-flight checks (PR3), end to end against
real Postgres.

Exercises the real production execution path
(`app.orchestrator.background_execution._execute_run_task`, the same
function `schedule_run_execution` triggers from `POST /agent-runs` and the
same one `tests/integration/test_llm_invocation_persistence.py` uses for
ADR 0012) with a synthetic agent whose manifest declares
`DEPENDENCY_GITHUB_WRITE` — proving `collect_preflight_warnings` +
`record_preflight_warnings` are correctly wired into `RunCoordinator`'s real
pre-flight lifecycle, not just unit-tested in isolation.

The synthetic agent_id is deliberately not a real registered agent name
(`default_stage_for_agent` resolves `None` for it, and `max_graph_hops=0`),
so both existing BLOCKING checks pass trivially with no LLM/Neo4j mocking
needed — this test is entirely about the WARNING path.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents._contract import AgentManifest, AgentOutput, Confidence, Subject
from app.core.crypto import encrypt_secret
from app.database.base import Base
from app.database.session import engine
from app.models.agent_step import AgentStep
from app.models.github_connection import GitHubConnection
from app.models.run import Run
from app.models.user import User
from app.orchestrator.background_execution import _execute_run_task
from app.orchestrator.preflight import DEPENDENCY_GITHUB_WRITE
from app.orchestrator.registry import AgentRegistry

pytestmark = pytest.mark.asyncio

_RealSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

_AGENT_ID = "test_github_write_agent"
_GOAL = "test_github_write_goal"

# Every real User/Run/AgentStep/GitHubConnection row created here is
# committed — left uncleaned, that's exactly the test-pollution class
# documented for this session's other integration tests (leftover rows
# inflating unrelated exact-count assertions, or just accumulating debt in
# the dev database). Tracked and deleted after each test regardless of
# pass/fail.
_created_user_ids: list[uuid.UUID] = []


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
async def _schema() -> None:
    await _ensure_schema()


@pytest.fixture(autouse=True)
async def _cleanup_created_users():
    _created_user_ids.clear()
    yield
    if not _created_user_ids:
        return
    cleanup = _RealSession()
    for user_id in _created_user_ids:
        for connection in (
            (
                await cleanup.execute(
                    select(GitHubConnection).where(GitHubConnection.user_id == user_id)
                )
            )
            .scalars()
            .all()
        ):
            await cleanup.delete(connection)
        for run in (
            (await cleanup.execute(select(Run).where(Run.user_id == user_id))).scalars().all()
        ):
            for step in (
                (await cleanup.execute(select(AgentStep).where(AgentStep.run_id == run.id)))
                .scalars()
                .all()
            ):
                await cleanup.delete(step)
            await cleanup.delete(run)
        user = (await cleanup.execute(select(User).where(User.id == user_id))).scalars().first()
        if user is not None:
            await cleanup.delete(user)
    await cleanup.commit()
    await cleanup.close()
    _created_user_ids.clear()


async def _make_user() -> uuid.UUID:
    db = _RealSession()
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        full_name="Test User",
        auth_provider="local",
    )
    db.add(user)
    await db.commit()
    user_id = user.id
    await db.close()
    _created_user_ids.append(user_id)
    return user_id


async def _connect_github(user_id: uuid.UUID) -> None:
    db = _RealSession()
    db.add(
        GitHubConnection(
            id=uuid.uuid4(),
            user_id=user_id,
            github_user_id="12345",
            github_username="octocat",
            encrypted_access_token=encrypt_secret("gh-real-token"),
        )
    )
    await db.commit()
    await db.close()


async def _make_run(user_id: uuid.UUID) -> uuid.UUID:
    db = _RealSession()
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:x",
        subject_type="freetext",
        display_name="x",
        goal=_GOAL,
        status="queued",
        user_id=user_id,
    )
    db.add(run)
    await db.commit()
    run_id = run.id
    await db.close()
    return run_id


def _make_registry(required_dependencies: frozenset[str] = frozenset({DEPENDENCY_GITHUB_WRITE})):
    registry = AgentRegistry()
    mock_agent = AsyncMock()

    async def run_fn(context):
        return AgentOutput(
            agent_id=_AGENT_ID,
            subject_id="freetext:x",
            confidence=Confidence(score=1.0, reasoning="deterministic"),
            evidence=[],
            result={},
            prompt_version="1.0",
        )

    mock_agent.run = AsyncMock(side_effect=run_fn)
    registry.register(
        AgentManifest(
            agent_id=_AGENT_ID,
            purpose="t",
            goals=frozenset({_GOAL}),
            accepted_subject_types=frozenset({"freetext"}),
            cost_class="cheap",
            max_graph_hops=0,
            required_dependencies=required_dependencies,
        ),
        mock_agent,
    )
    return registry, mock_agent


async def _step_for_run(run_id: uuid.UUID) -> AgentStep:
    """Fresh, independent session — never the one that made the change."""
    reader = _RealSession()
    result = await reader.execute(select(AgentStep).where(AgentStep.run_id == run_id))
    step = result.scalars().first()
    await reader.close()
    return step


# ---------------------------------------------------------------------------
# Healthy dependency — no GitHub connection needed, no warning produced.
# ---------------------------------------------------------------------------


async def test_healthy_github_connection_produces_no_warning() -> None:
    user_id = await _make_user()
    await _connect_github(user_id)
    run_id = await _make_run(user_id)
    registry, mock_agent = _make_registry()
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    await _execute_run_task(run_id, subject, _GOAL, None, None, _AGENT_ID, registry, None)

    step = await _step_for_run(run_id)
    assert step.status == "completed"
    assert step.preflight_warnings == []
    mock_agent.run.assert_called_once()


# ---------------------------------------------------------------------------
# Unavailable dependency — no GitHub connection at all.
# ---------------------------------------------------------------------------


async def test_unavailable_github_connection_persists_a_warning() -> None:
    user_id = await _make_user()  # never connected
    run_id = await _make_run(user_id)
    registry, mock_agent = _make_registry()
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    await _execute_run_task(run_id, subject, _GOAL, None, None, _AGENT_ID, registry, None)

    step = await _step_for_run(run_id)
    assert step.status == "completed"  # a WARNING never blocks execution
    assert step.preflight_warnings == [
        {
            "code": "github_write_available",
            "dependency": "GitHub",
            "message": (
                "No GitHub connection found. Connect GitHub before running execution workflows."
            ),
            "checked_at": step.preflight_warnings[0]["checked_at"],
        }
    ]
    mock_agent.run.assert_called_once()


# ---------------------------------------------------------------------------
# Dependency filtering / backward compatibility — an agent that doesn't
# declare github_write must never produce (or persist) this warning, even
# with no GitHub connection at all.
# ---------------------------------------------------------------------------


async def test_agent_without_github_write_dependency_never_warns() -> None:
    user_id = await _make_user()  # never connected
    run_id = await _make_run(user_id)
    registry, mock_agent = _make_registry(required_dependencies=frozenset())
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    await _execute_run_task(run_id, subject, _GOAL, None, None, _AGENT_ID, registry, None)

    step = await _step_for_run(run_id)
    assert step.status == "completed"
    assert step.preflight_warnings == []


# ---------------------------------------------------------------------------
# Serialization — the persisted JSON round-trips through the real API
# response mapping exactly as PR1 established.
# ---------------------------------------------------------------------------


async def test_persisted_warning_serializes_through_step_response() -> None:
    from app.api.v1.routers.agent_runs import _step_response

    user_id = await _make_user()
    run_id = await _make_run(user_id)
    registry, mock_agent = _make_registry()
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    await _execute_run_task(run_id, subject, _GOAL, None, None, _AGENT_ID, registry, None)

    step = await _step_for_run(run_id)
    response = _step_response(step)
    assert len(response.preflight_warnings) == 1
    assert response.preflight_warnings[0].code == "github_write_available"
    assert response.preflight_warnings[0].dependency == "GitHub"
