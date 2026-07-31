"""ADR 0012 — LLM invocation persistence, end to end against real Postgres.

Every test here exercises the real production execution path
(`app.orchestrator.background_execution._execute_run_task`, the same
function `schedule_run_execution` triggers from `POST /agent-runs`), not
`RunCoordinator`/`persist_llm_invocation` in isolation — the same
discipline this session's regression investigations already established:
persistence bugs at this layer only show up when the real wrapper, the
real session lifecycle, and a fresh independent reader session are all
exercised together.

Only the LLM provider layer (`StageAwareLLMProvider.complete`/`.preview`)
is faked, in each test, to a real, direct patch — everything else (the
agent registry, `RunCoordinator`, Postgres, the `LLMInvocation`/`AgentStep`/
`Run` rows) is real.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents._contract import AgentManifest, AgentOutput, Confidence, Subject
from app.ai.config.resolver import ResolvedProvider
from app.ai.providers.base import LLMResponse
from app.ai.providers.errors import AIProviderRateLimitError
from app.ai.providers.registry import ProviderBuildConfig, get_provider_spec
from app.database.base import Base
from app.database.session import engine
from app.models.agent_step import AgentStep
from app.models.ai_profile import AIProviderUsage
from app.models.llm_invocation import LLMInvocation
from app.models.run import Run
from app.orchestrator.background_execution import _execute_run_task
from app.orchestrator.registry import AgentRegistry

pytestmark = pytest.mark.asyncio

_RealSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

_RESOLVED = ResolvedProvider(
    spec=get_provider_spec("openai"),
    config=ProviderBuildConfig(api_key="k", model="gpt-4o", temperature=0.2, max_tokens=100),
    source="environment",
)


async def _make_run(goal: str = "plan_freeform", status: str = "queued") -> uuid.UUID:
    setup = _RealSession()
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:x",
        subject_type="freetext",
        display_name="x",
        goal=goal,
        status=status,
    )
    setup.add(run)
    await setup.commit()
    run_id = run.id
    await setup.close()
    return run_id


def _make_registry(agent_id: str, goal: str, run_fn) -> AgentRegistry:
    registry = AgentRegistry()
    mock_agent = AsyncMock()
    mock_agent.run = AsyncMock(side_effect=run_fn)
    registry.register(
        AgentManifest(
            agent_id=agent_id,
            purpose="t",
            goals=frozenset({goal}),
            accepted_subject_types=frozenset({"freetext"}),
            cost_class="cheap",
        ),
        mock_agent,
    )
    return registry, mock_agent


async def _invocations_for_run(run_id: uuid.UUID) -> list[LLMInvocation]:
    """Always through a fresh, independent session — never the one that
    made the change — matching this session's established verification
    discipline for exactly this class of persistence bug."""
    reader = _RealSession()
    result = await reader.execute(
        select(LLMInvocation).where(LLMInvocation.run_id == run_id).order_by(LLMInvocation.sequence)
    )
    rows = list(result.scalars().all())
    await reader.close()
    return rows


@contextlib.contextmanager
def _patched_provider(complete_fn):
    """The three patches every test needs: a faked provider `.complete()`,
    a passthrough `.preview()`, and the Neo4j pre-flight check bypassed
    (unrelated to this ADR — see ADR 0011 OD-5: the tool registry is only
    populated as a side effect of importing `app.main`, which these
    persistence tests have no reason to depend on)."""
    with (
        patch("app.agents.llm.StageAwareLLMProvider.complete", new=complete_fn),
        patch("app.agents.llm.StageAwareLLMProvider.preview", return_value=_RESOLVED),
        patch(
            "app.orchestrator.run_coordinator.check_neo4j_reachable",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
async def _schema() -> None:
    await _ensure_schema()


# ---------------------------------------------------------------------------
# Successful invocation
# ---------------------------------------------------------------------------


async def test_successful_invocation_persists_one_row() -> None:
    run_id = await _make_run()

    async def run_fn(context):
        from app.agents.planning.agent import _call_llm

        await _call_llm(user_prompt="hi", model=None, context=context)
        return AgentOutput(
            agent_id="planning",
            subject_id="freetext:x",
            confidence=Confidence(score=0.9, reasoning="ok"),
            evidence=[],
            result={},
            prompt_version="1.0",
        )

    registry, _ = _make_registry("planning", "plan_freeform", run_fn)
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    async def fake_complete(self, **kw):
        self.last_resolved = _RESOLVED
        self.last_retry_count = 0
        return LLMResponse(
            text="{}",
            model_name="gpt-4o",
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )

    with _patched_provider(fake_complete):
        await _execute_run_task(
            run_id, subject, "plan_freeform", None, None, "planning", registry, None
        )

    rows = await _invocations_for_run(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "completed"
    assert row.provider == "openai"
    assert row.model == "gpt-4o"
    assert row.total_tokens == 150
    # gpt-4o is priced: 100 prompt @ $2.50/1M + 50 completion @ $10.00/1M.
    assert row.estimated_cost_usd == pytest.approx(0.00075)
    assert row.finish_reason == "stop"
    assert row.retry_count == 0
    assert row.purpose == "initial"
    assert row.sequence == 0
    assert row.latency_ms is not None and row.latency_ms >= 0
    assert row.started_at is not None
    assert row.finished_at is not None
    assert row.finished_at >= row.started_at


# ---------------------------------------------------------------------------
# Failed invocation — must not be lost
# ---------------------------------------------------------------------------


async def test_failed_invocation_is_persisted_not_lost() -> None:
    run_id = await _make_run()

    async def run_fn(context):
        from app.agents.planning.agent import _call_llm

        try:
            await _call_llm(user_prompt="hi", model=None, context=context)
        finally:
            pass
        raise AssertionError("unreachable — _call_llm should have raised")

    registry, _ = _make_registry("planning", "plan_freeform", run_fn)
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    async def fake_complete(self, **kw):
        self.last_resolved = _RESOLVED
        self.last_retry_count = 2
        raise AIProviderRateLimitError("rate limited for real")

    with _patched_provider(fake_complete):
        await _execute_run_task(
            run_id, subject, "plan_freeform", None, None, "planning", registry, None
        )

    reader = _RealSession()
    run = (await reader.execute(select(Run).where(Run.id == run_id))).scalars().first()
    assert run.status == "failed"
    await reader.close()

    rows = await _invocations_for_run(run_id)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].error is not None and "rate limited" in rows[0].error
    assert rows[0].retry_count == 2
    # No response on a failure path — these must be honestly None, not 0.
    assert rows[0].total_tokens is None
    assert rows[0].estimated_cost_usd is None


# ---------------------------------------------------------------------------
# Retry — represented as an attribute on the successful row, per ADR 0012
# ---------------------------------------------------------------------------


async def test_retry_count_reflects_failed_attempts_before_success() -> None:
    run_id = await _make_run()

    async def run_fn(context):
        from app.agents.planning.agent import _call_llm

        await _call_llm(user_prompt="hi", model=None, context=context)
        return AgentOutput(
            agent_id="planning",
            subject_id="freetext:x",
            confidence=Confidence(score=0.9, reasoning="ok"),
            evidence=[],
            result={},
            prompt_version="1.0",
        )

    registry, _ = _make_registry("planning", "plan_freeform", run_fn)
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    async def fake_complete(self, **kw):
        self.last_resolved = _RESOLVED
        self.last_retry_count = 3  # 3 failed provider attempts before this one succeeded
        return LLMResponse(text="{}", model_name="gpt-4o", prompt_tokens=1, completion_tokens=1)

    with _patched_provider(fake_complete):
        await _execute_run_task(
            run_id, subject, "plan_freeform", None, None, "planning", registry, None
        )

    rows = await _invocations_for_run(run_id)
    assert len(rows) == 1
    assert rows[0].retry_count == 3
    assert rows[0].status == "completed"


# ---------------------------------------------------------------------------
# Provider fallback — the served provider is the one actually recorded
# ---------------------------------------------------------------------------


async def test_provider_fallback_records_the_provider_that_actually_served() -> None:
    """`last_resolved` is set to whichever provider actually served the
    request (including after a fallback hop) — the invocation row must
    reflect that provider, not whichever was configured as primary."""
    run_id = await _make_run()
    fallback_served = ResolvedProvider(
        spec=get_provider_spec("bedrock"),
        config=ProviderBuildConfig(api_key=None, model="claude", temperature=0.2, max_tokens=100),
        source="environment",
    )

    async def run_fn(context):
        from app.agents.planning.agent import _call_llm

        await _call_llm(user_prompt="hi", model=None, context=context)
        return AgentOutput(
            agent_id="planning",
            subject_id="freetext:x",
            confidence=Confidence(score=0.9, reasoning="ok"),
            evidence=[],
            result={},
            prompt_version="1.0",
        )

    registry, _ = _make_registry("planning", "plan_freeform", run_fn)
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    async def fake_complete(self, **kw):
        # Simulates complete_with_fallback resolving to a *different*
        # provider than the primary after a recoverable failure upstream.
        self.last_resolved = fallback_served
        self.last_retry_count = 1
        return LLMResponse(text="{}", model_name="claude", prompt_tokens=5, completion_tokens=5)

    with _patched_provider(fake_complete):
        await _execute_run_task(
            run_id, subject, "plan_freeform", None, None, "planning", registry, None
        )

    rows = await _invocations_for_run(run_id)
    assert len(rows) == 1
    assert rows[0].provider == "bedrock"
    assert rows[0].retry_count == 1


# ---------------------------------------------------------------------------
# Reflection — two independent, distinguishable rows on one AgentStep
# ---------------------------------------------------------------------------


async def test_reflection_produces_two_independent_rows() -> None:
    run_id = await _make_run()

    async def run_fn(context):
        from app.agents.planning.agent import _call_llm

        await _call_llm(
            user_prompt="initial", model=None, context=context, purpose="initial", sequence=0
        )
        await _call_llm(
            user_prompt="refine", model=None, context=context, purpose="reflection", sequence=1
        )
        return AgentOutput(
            agent_id="planning",
            subject_id="freetext:x",
            confidence=Confidence(score=0.9, reasoning="ok"),
            evidence=[],
            result={},
            prompt_version="1.0",
        )

    registry, _ = _make_registry("planning", "plan_freeform", run_fn)
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    async def fake_complete(self, **kw):
        self.last_resolved = _RESOLVED
        self.last_retry_count = 0
        return LLMResponse(text="{}", model_name="gpt-4o", prompt_tokens=1, completion_tokens=1)

    with _patched_provider(fake_complete):
        await _execute_run_task(
            run_id, subject, "plan_freeform", None, None, "planning", registry, None
        )

    rows = await _invocations_for_run(run_id)
    assert len(rows) == 2
    assert [r.purpose for r in rows] == ["initial", "reflection"]
    assert [r.sequence for r in rows] == [0, 1]
    assert rows[0].id != rows[1].id
    # Both belong to the same AgentStep.
    assert rows[0].agent_step_id == rows[1].agent_step_id


# ---------------------------------------------------------------------------
# Multiple agents — the shared pathway works identically for every one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_module,agent_id,goal",
    [
        ("app.agents.development.agent", "development", "develop_change_plan"),
        ("app.agents.testing.agent", "testing", "plan_tests"),
        ("app.agents.engineering_review.agent", "engineering_review", "review_readiness"),
        ("app.agents.code_generation.agent", "code_generation", "generate_code"),
        ("app.agents.documentation_planning.agent", "documentation_planning", "plan_documentation"),
    ],
)
async def test_every_non_planning_agent_persists_an_invocation(
    agent_module, agent_id, goal
) -> None:
    run_id = await _make_run(goal=goal)

    async def run_fn(context):
        import importlib

        mod = importlib.import_module(agent_module)
        await mod._call_llm(user_prompt="hi", model=None, context=context)
        return AgentOutput(
            agent_id=agent_id,
            subject_id="freetext:x",
            confidence=Confidence(score=0.9, reasoning="ok"),
            evidence=[],
            result={},
            prompt_version="1.0",
        )

    registry, _ = _make_registry(agent_id, goal, run_fn)
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    async def fake_complete(self, **kw):
        self.last_resolved = _RESOLVED
        self.last_retry_count = 0
        return LLMResponse(text="{}", model_name="gpt-4o", prompt_tokens=1, completion_tokens=1)

    with _patched_provider(fake_complete):
        await _execute_run_task(run_id, subject, goal, None, None, agent_id, registry, None)

    rows = await _invocations_for_run(run_id)
    assert len(rows) == 1, f"{agent_id} did not persist an invocation row"
    assert rows[0].status == "completed"
    assert rows[0].provider == "openai"


# ---------------------------------------------------------------------------
# AIProviderUsage revival
# ---------------------------------------------------------------------------


async def test_ai_provider_usage_is_updated_from_the_same_call_site() -> None:
    run_id = await _make_run()

    async def run_fn(context):
        from app.agents.planning.agent import _call_llm

        await _call_llm(user_prompt="hi", model=None, context=context)
        return AgentOutput(
            agent_id="planning",
            subject_id="freetext:x",
            confidence=Confidence(score=0.9, reasoning="ok"),
            evidence=[],
            result={},
            prompt_version="1.0",
        )

    registry, _ = _make_registry("planning", "plan_freeform", run_fn)
    subject = Subject(subject_id="freetext:x", subject_type="freetext", display_name="x")

    async def fake_complete(self, **kw):
        self.last_resolved = _RESOLVED
        self.last_retry_count = 0
        return LLMResponse(text="{}", model_name="gpt-4o", prompt_tokens=1, completion_tokens=1)

    with _patched_provider(fake_complete):
        await _execute_run_task(
            run_id, subject, "plan_freeform", None, None, "planning", registry, None
        )

    reader = _RealSession()
    usage = (
        (
            await reader.execute(
                select(AIProviderUsage).where(AIProviderUsage.provider_key == "openai")
            )
        )
        .scalars()
        .first()
    )
    await reader.close()
    assert usage is not None
    assert usage.requests >= 1
    assert usage.successes >= 1


# ---------------------------------------------------------------------------
# Transaction rollback — an exception before commit must not leave a
# partial invocation row visible to any other session.
# ---------------------------------------------------------------------------


async def test_transaction_rollback_leaves_no_partial_invocation_row() -> None:
    """`persist_llm_invocation` only flushes; nothing commits until
    RunCoordinator's own commit. If something *after* the flush but before
    that commit raises and the session is rolled back instead, the flushed
    row must not be visible to a fresh reader."""
    db = _RealSession()
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:x",
        subject_type="freetext",
        display_name="x",
        goal="plan_freeform",
        status="running",
    )
    db.add(run)
    await db.flush()
    step = AgentStep(id=uuid.uuid4(), run_id=run.id, agent_id="planning", status="running")
    db.add(step)
    await db.flush()

    from app.agents.llm import persist_llm_invocation

    await persist_llm_invocation(
        db,
        run_id=run.id,
        agent_step_id=step.id,
        stage="planning",
        purpose="initial",
        sequence=0,
        metadata={
            "provider": "openai",
            "model": "gpt-4o",
            "status": "completed",
            "error": None,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "estimated_cost_usd": 0.0001,
            "finish_reason": "stop",
            "latency_ms": 10,
            "retry_count": 0,
            "started_at": datetime.now(UTC),
            "finished_at": datetime.now(UTC),
        },
        error=None,
    )
    # Flushed, not committed — then the whole transaction is abandoned.
    await db.rollback()
    await db.close()

    reader = _RealSession()
    rows = (
        (await reader.execute(select(LLMInvocation).where(LLMInvocation.run_id == run.id)))
        .scalars()
        .all()
    )
    await reader.close()
    assert rows == [], "a rolled-back invocation must not be visible to another session"


# ---------------------------------------------------------------------------
# Workflow/run deletion behaviour — cascade, per ADR 0012 Relationships
# ---------------------------------------------------------------------------


async def test_deleting_a_run_cascades_to_its_invocation_rows() -> None:
    db = _RealSession()
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:x",
        subject_type="freetext",
        display_name="x",
        goal="plan_freeform",
        status="completed",
    )
    db.add(run)
    await db.flush()
    step = AgentStep(id=uuid.uuid4(), run_id=run.id, agent_id="planning", status="completed")
    db.add(step)
    await db.flush()
    invocation = LLMInvocation(
        agent_step_id=step.id,
        run_id=run.id,
        purpose="initial",
        sequence=0,
        provider="openai",
        model="gpt-4o",
        status="completed",
        latency_ms=10,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(invocation)
    await db.commit()
    invocation_id = invocation.id

    await db.delete(run)
    await db.commit()
    await db.close()

    reader = _RealSession()
    remaining = (
        (await reader.execute(select(LLMInvocation).where(LLMInvocation.id == invocation_id)))
        .scalars()
        .first()
    )
    await reader.close()
    assert remaining is None, "ADR 0012: deleting a Run must cascade-delete its LLMInvocation rows"


# ---------------------------------------------------------------------------
# Append-only guarantee
# ---------------------------------------------------------------------------


async def test_no_code_path_updates_an_existing_invocation_row() -> None:
    """`persist_llm_invocation` always constructs and adds a *new*
    `LLMInvocation` — there is no update/select-then-mutate path. Calling
    it twice for the same agent_step_id must produce two rows, never one
    row mutated twice, even with identical `sequence`/`purpose` (a caller
    bug would be visible as a duplicate row, not silently merged)."""
    db = _RealSession()
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:x",
        subject_type="freetext",
        display_name="x",
        goal="plan_freeform",
        status="running",
    )
    db.add(run)
    await db.flush()
    step = AgentStep(id=uuid.uuid4(), run_id=run.id, agent_id="planning", status="running")
    db.add(step)
    await db.flush()

    from app.agents.llm import persist_llm_invocation

    metadata = {
        "provider": "openai",
        "model": "gpt-4o",
        "status": "completed",
        "error": None,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
        "estimated_cost_usd": 0.0001,
        "finish_reason": "stop",
        "latency_ms": 10,
        "retry_count": 0,
        "started_at": datetime.now(UTC),
        "finished_at": datetime.now(UTC),
    }
    await persist_llm_invocation(
        db,
        run_id=run.id,
        agent_step_id=step.id,
        stage="planning",
        purpose="initial",
        sequence=0,
        metadata=metadata,
        error=None,
    )
    await persist_llm_invocation(
        db,
        run_id=run.id,
        agent_step_id=step.id,
        stage="planning",
        purpose="initial",
        sequence=0,
        metadata=metadata,
        error=None,
    )
    # A real invocation always belongs to a run that eventually reaches a
    # terminal status — leaving this row at "running" would make it
    # (correctly) look orphaned to app.orchestrator.background_execution.
    # recover_orphaned_runs, which several other tests in this suite
    # depend on seeing an exact, uninflated count of real orphaned rows.
    run.status = "completed"
    await db.commit()
    run_id = run.id
    await db.close()

    rows = await _invocations_for_run(run_id)
    assert len(rows) == 2, "two calls must produce two rows, never an update-in-place"

    cleanup = _RealSession()
    run_to_delete = await cleanup.get(Run, run_id)
    if run_to_delete is not None:
        await cleanup.delete(run_to_delete)
        await cleanup.commit()
    await cleanup.close()
    assert rows[0].id != rows[1].id


# ---------------------------------------------------------------------------
# Migration — schema shape, forward/rollback safety
# ---------------------------------------------------------------------------


async def test_llm_invocations_table_has_expected_columns_and_indexes() -> None:
    """Confirms the migration actually shipped the schema ADR 0012
    specifies — a lighter-weight, permanent check than re-running
    `alembic upgrade`/`downgrade` in the test suite itself (verified
    manually, live, against real Postgres during this increment)."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        cols = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'llm_invocations'"
                    )
                )
            )
            .scalars()
            .all()
        )
        indexes = (
            (
                await conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'llm_invocations'")
                )
            )
            .scalars()
            .all()
        )

    expected_columns = {
        "id",
        "agent_step_id",
        "run_id",
        "purpose",
        "sequence",
        "provider",
        "model",
        "stage",
        "status",
        "error",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "finish_reason",
        "latency_ms",
        "retry_count",
        "attempted_providers",
        "started_at",
        "finished_at",
        "created_at",
    }
    assert expected_columns.issubset(set(cols))
    assert "ix_llm_invocations_run_id_started_at" in indexes
    assert "ix_llm_invocations_provider_started_at" in indexes
