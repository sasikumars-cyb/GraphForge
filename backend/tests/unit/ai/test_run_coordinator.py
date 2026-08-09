"""Unit tests for RunCoordinator — P1-2.

Covers:
- Successful execution path
- Invalid goal (selector raises)
- Agent registered in selector but missing from registry
- Agent raises during execution
- Persistence: Run + AgentStep created, committed
- Status transitions: queued → running → completed / failed
- Commit behavior: commit called exactly once on each path
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import (
    AgentContext,
    AgentManifest,
    AgentOutput,
    Confidence,
    Evidence,
    Subject,
)
from app.core.exceptions import NotFoundError, SubjectTypeMismatchError
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.orchestrator.preflight import PreFlightCheckFailed
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.run_coordinator import RunCoordinator
from app.orchestrator.selector import AgentSelector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subject() -> Subject:
    return Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name="Test task",
    )


def _make_output(agent_id: str = "planning") -> AgentOutput:
    return AgentOutput(
        agent_id=agent_id,
        subject_id="freetext:abc123",
        confidence=Confidence(score=0.85, reasoning="Good evidence."),
        evidence=[
            Evidence(
                kind="graph_traversal",
                reference="traverse_architecture_graph",
                summary="3 components.",
            ),
            Evidence(kind="tool_call", reference="get_indexed_repositories", summary="2 repos."),
        ],
        result={"executive_summary": "A plan."},
        prompt_version="1.0",
    )


def _make_manifest(
    agent_id: str, goal: str, required_dependencies: frozenset[str] = frozenset()
) -> AgentManifest:
    return AgentManifest(
        agent_id=agent_id,
        purpose=f"Test {agent_id}.",
        goals=frozenset({goal}),
        accepted_subject_types=frozenset({"freetext"}),
        cost_class="cheap",
        required_dependencies=required_dependencies,
    )


def _build_coordinator(
    agent_id: str = "planning",
    goal: str = "plan_freeform",
    agent_run_side_effect: Exception | None = None,
    register_agent: bool = True,
    required_dependencies: frozenset[str] = frozenset(),
) -> tuple[RunCoordinator, AsyncMock, AsyncMock]:
    """Build a RunCoordinator with a mocked db, registry, and selector.

    Returns (coordinator, mock_db, mock_agent).
    """
    mock_db = AsyncMock()
    # flush and commit are async
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    # add is sync
    mock_db.add = MagicMock()

    registry = AgentRegistry()
    mock_agent = AsyncMock()

    if agent_run_side_effect:
        mock_agent.run = AsyncMock(side_effect=agent_run_side_effect)
    else:
        mock_agent.run = AsyncMock(return_value=_make_output(agent_id))

    if register_agent:
        registry.register(_make_manifest(agent_id, goal, required_dependencies), mock_agent)

    selector = AgentSelector(registry)
    coordinator = RunCoordinator(db=mock_db, registry=registry, selector=selector)
    return coordinator, mock_db, mock_agent


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_execution_returns_completed_run() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    run = await coordinator.execute(subject, "plan_freeform")

    assert run.status == "completed"
    assert run.goal == "plan_freeform"
    assert run.subject_id == "freetext:abc123"
    assert run.completed_at is not None
    assert run.error_message is None


@pytest.mark.asyncio
async def test_successful_execution_calls_agent_run() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    await coordinator.execute(subject, "plan_freeform")

    mock_agent.run.assert_called_once()
    context: AgentContext = mock_agent.run.call_args[0][0]
    assert context.subject.subject_id == "freetext:abc123"
    assert context.goal == "plan_freeform"
    assert context.extras["db"] is mock_db


@pytest.mark.asyncio
async def test_successful_execution_persists_step() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    await coordinator.execute(subject, "plan_freeform")

    # db.add should be called at least twice: once for Run, once for AgentStep
    assert mock_db.add.call_count >= 2


@pytest.mark.asyncio
async def test_successful_execution_commits_once() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    await coordinator.execute(subject, "plan_freeform")

    # Two commits: the running-transition commit (so a poller on a
    # different DB session can observe it before the agent finishes) and
    # the terminal completed/failed commit.
    assert mock_db.commit.call_count == 2


@pytest.mark.asyncio
async def test_successful_step_has_correct_fields() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    await coordinator.execute(subject, "plan_freeform")

    # The step is the second object added (after the Run)
    step = mock_db.add.call_args_list[1][0][0]
    assert step.agent_id == "planning"
    assert step.status == "completed"
    assert step.confidence_score == 0.85
    assert len(step.evidence) == 2
    assert step.latency_ms is not None
    assert step.latency_ms >= 0
    assert step.completed_at is not None


# ---------------------------------------------------------------------------
# Invalid goal (selector raises)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_goal_marks_run_as_failed() -> None:
    coordinator, mock_db, _ = _build_coordinator()
    subject = _make_subject()

    with pytest.raises(NotFoundError):
        await coordinator.execute(subject, "nonexistent_goal")

    # The run was added first
    run = mock_db.add.call_args_list[0][0][0]
    assert run.status == "failed"
    assert run.error_message is not None
    assert "nonexistent_goal" in run.error_message


@pytest.mark.asyncio
async def test_invalid_goal_commits_before_raising() -> None:
    coordinator, mock_db, _ = _build_coordinator()
    subject = _make_subject()

    with pytest.raises(NotFoundError):
        await coordinator.execute(subject, "nonexistent_goal")

    mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Agent missing from registry (selector returns agent_id but registry
# doesn't have it — divergence scenario)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_agent_marks_run_as_failed() -> None:
    """Selector finds a goal but the registry doesn't have the agent.

    Since the Selector now derives its mapping from a registry, this only
    happens if RunCoordinator is constructed with a *different* registry
    instance than the one the Selector was built with — a defensive path,
    exercised here by deliberately mismatching them.
    """
    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    registry_with_agent = AgentRegistry()
    registry_with_agent.register(_make_manifest("planning", "plan_freeform"), AsyncMock())
    selector = AgentSelector(registry_with_agent)

    empty_registry = AgentRegistry()
    coordinator = RunCoordinator(db=mock_db, registry=empty_registry, selector=selector)
    subject = _make_subject()

    with pytest.raises(NotFoundError, match="not found in registry"):
        await coordinator.execute(subject, "plan_freeform")

    run = mock_db.add.call_args_list[0][0][0]
    assert run.status == "failed"
    mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Manifest enforcement — accepted_subject_types (Part 2 / Part 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subject_type_mismatch_fails_deterministically_without_invoking_agent() -> None:
    """A subject whose subject_type the target agent's manifest doesn't
    accept must be rejected by the dispatcher itself — the agent's run()
    (and therefore any LLM call inside it) must never be invoked."""
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = Subject(
        subject_id="pull_request:42",
        subject_type="pull_request",  # manifest only accepts "freetext"
        display_name="A PR",
    )

    with pytest.raises(SubjectTypeMismatchError, match="does not accept subject_type"):
        await coordinator.execute(subject, "plan_freeform")

    mock_agent.run.assert_not_called()
    run = mock_db.add.call_args_list[0][0][0]
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_subject_type_match_proceeds_normally() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()  # subject_type="freetext", matches manifest

    run = await coordinator.execute(subject, "plan_freeform")

    assert run.status == "completed"
    mock_agent.run.assert_called_once()


# ---------------------------------------------------------------------------
# Agent raises during execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_exception_marks_run_as_failed() -> None:
    coordinator, mock_db, _ = _build_coordinator(
        agent_run_side_effect=RuntimeError("LLM API key missing")
    )
    subject = _make_subject()

    with pytest.raises(RuntimeError, match="LLM API key missing"):
        await coordinator.execute(subject, "plan_freeform")

    run = mock_db.add.call_args_list[0][0][0]
    assert run.status == "failed"
    assert "LLM API key missing" in run.error_message


@pytest.mark.asyncio
async def test_agent_exception_marks_step_as_failed() -> None:
    coordinator, mock_db, _ = _build_coordinator(
        agent_run_side_effect=RuntimeError("Connection refused")
    )
    subject = _make_subject()

    with pytest.raises(RuntimeError):
        await coordinator.execute(subject, "plan_freeform")

    step = mock_db.add.call_args_list[1][0][0]
    assert step.status == "failed"
    assert step.error_message == "Connection refused"
    assert step.latency_ms is not None


@pytest.mark.asyncio
async def test_agent_exception_commits_before_raising() -> None:
    coordinator, mock_db, _ = _build_coordinator(agent_run_side_effect=RuntimeError("fail"))
    subject = _make_subject()

    with pytest.raises(RuntimeError):
        await coordinator.execute(subject, "plan_freeform")

    # Running-transition commit, then the failure commit.
    assert mock_db.commit.call_count == 2


# ---------------------------------------------------------------------------
# Pre-flight validation (architecture audit Weakness #1) — a missing LLM
# provider credential must fail the run *before* agent.run() is ever
# called, not mid-execution.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_failure_never_calls_agent_run() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    with (
        patch(
            "app.orchestrator.run_coordinator.check_llm_provider_configured",
            return_value="No API key is configured for the 'OpenAI' provider (stage 'planning').",
        ),
        pytest.raises(PreFlightCheckFailed, match="Pre-flight check failed"),
    ):
        await coordinator.execute(subject, "plan_freeform")

    mock_agent.run.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_failure_marks_run_and_step_failed() -> None:
    coordinator, mock_db, _ = _build_coordinator()
    subject = _make_subject()

    with (
        patch(
            "app.orchestrator.run_coordinator.check_llm_provider_configured",
            return_value="No API key is configured for the 'OpenAI' provider (stage 'planning').",
        ),
        pytest.raises(PreFlightCheckFailed),
    ):
        await coordinator.execute(subject, "plan_freeform")

    run = mock_db.add.call_args_list[0][0][0]
    step = mock_db.add.call_args_list[1][0][0]
    assert run.status == "failed"
    assert "Pre-flight check failed" in run.error_message
    assert "No API key is configured" in run.error_message
    assert step.status == "failed"
    assert step.error_message == run.error_message
    # Running-transition commit, then the preflight-failure commit.
    assert mock_db.commit.call_count == 2


@pytest.mark.asyncio
async def test_preflight_failure_asks_the_same_question_the_agent_would() -> None:
    """The check must be called with (agent_id, run.workflow_stage) — the
    same precedence `app.agents.llm.stage_for` uses — so a pre-flight
    rejection is never based on a different stage than the one the agent's
    own LLM call would actually resolve under."""
    coordinator, mock_db, _ = _build_coordinator(agent_id="planning", goal="plan_freeform")
    subject = _make_subject()

    with patch(
        "app.orchestrator.run_coordinator.check_llm_provider_configured", return_value=None
    ) as mock_check:
        run = await coordinator.execute(subject, "plan_freeform")

    assert run.status == "completed"
    mock_check.assert_called_once()
    called_agent_id, called_stage = mock_check.call_args[0]
    assert called_agent_id == "planning"
    assert called_stage == run.workflow_stage


@pytest.mark.asyncio
async def test_preflight_pass_proceeds_to_agent_run_normally() -> None:
    """A configured provider (the normal case for every existing test in
    this file, none of which mock the pre-flight check) must not change
    any existing behavior — this makes that assumption explicit rather
    than only inferred from every other test in this file still passing."""
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    with patch("app.orchestrator.run_coordinator.check_llm_provider_configured", return_value=None):
        run = await coordinator.execute(subject, "plan_freeform")

    assert run.status == "completed"
    mock_agent.run.assert_called_once()


@pytest.mark.asyncio
async def test_preflight_check_raising_still_fails_the_run_cleanly() -> None:
    """Regression test: `check_llm_provider_configured` calls `resolve()`,
    which is NOT guaranteed exception-free (`require_provider_spec()`
    raises `UnsupportedProviderError` for a stale/invalid stored provider
    key — see `app.ai.providers.registry`). An earlier version of this
    pre-flight gate called it *before* the try/except that wraps
    agent.run(), so this exact exception escaped `execute_run` entirely,
    bypassing `_fail_step`/`_fail_run`, and (confirmed live against real
    Postgres through the actual `background_execution` wrapper) the run
    silently reverted to its pre-execution status with no error message at
    all, instead of being marked "failed". The check must now live inside
    the same try/except that already handles any other agent.run() failure."""
    coordinator, mock_db, mock_agent = _build_coordinator()
    subject = _make_subject()

    with (
        patch(
            "app.orchestrator.run_coordinator.check_llm_provider_configured",
            side_effect=RuntimeError("Unknown AI provider: 'deprecated-vendor'."),
        ),
        pytest.raises(RuntimeError, match="deprecated-vendor"),
    ):
        await coordinator.execute(subject, "plan_freeform")

    mock_agent.run.assert_not_called()
    run = mock_db.add.call_args_list[0][0][0]
    step = mock_db.add.call_args_list[1][0][0]
    assert run.status == "failed"
    assert "deprecated-vendor" in run.error_message
    assert step.status == "failed"
    assert step.error_message == run.error_message
    # Running-transition commit, then the preflight-failure commit.
    assert mock_db.commit.call_count == 2


@pytest.mark.asyncio
async def test_resume_preflight_check_raising_still_fails_the_run_cleanly() -> None:
    """Same regression as above, for `resume_step` — the paused-resume
    entry point has the identical structure and had the identical bug."""
    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="context_discovery", goal="discover_context"
    )
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:abc123",
        subject_type="freetext",
        goal="discover_context",
        status="awaiting_input",
    )
    step = AgentStep(
        id=uuid.uuid4(), run_id=run.id, agent_id="context_discovery", status="awaiting_input"
    )

    with (
        patch(
            "app.orchestrator.run_coordinator.check_llm_provider_configured",
            side_effect=RuntimeError("Unknown AI provider: 'deprecated-vendor'."),
        ),
        pytest.raises(RuntimeError, match="deprecated-vendor"),
    ):
        await coordinator.resume_step(
            run, step, "context_discovery", mock_agent, _make_subject(), "discover_context"
        )

    mock_agent.run.assert_not_called()
    assert run.status == "failed"
    assert step.status == "failed"
    assert "deprecated-vendor" in run.error_message
    assert step.error_message == run.error_message


# ---------------------------------------------------------------------------
# WARNING-severity pre-flight (ADR 0011, PR3) — collect_preflight_warnings +
# record_preflight_warnings integrated into the same pre-flight lifecycle,
# never affecting BLOCKING behavior.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_environment_produces_no_warnings() -> None:
    """Backward compatibility: an agent declaring no required_dependencies
    (every manifest's state before this PR, and still the default) must
    behave exactly as before — this uses the *real*
    `collect_preflight_warnings`, unmocked, to prove it never even touches
    the db for such an agent."""
    coordinator, mock_db, mock_agent = _build_coordinator()  # no required_dependencies
    subject = _make_subject()

    run = await coordinator.execute(subject, "plan_freeform")

    assert run.status == "completed"
    step = mock_db.add.call_args_list[1][0][0]
    assert step.preflight_warnings in ([], None)


@pytest.mark.asyncio
async def test_warning_check_result_is_persisted_onto_step() -> None:
    from app.orchestrator.preflight import PreflightWarning

    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="create_branch",
        goal="create_branch",
        required_dependencies=frozenset({"github_write"}),
    )
    subject = _make_subject()

    warning = PreflightWarning(
        code="github_write_available",
        dependency="GitHub",
        message="No GitHub connection found.",
        checked_at="2026-07-31T00:00:00Z",
    )
    with patch(
        "app.orchestrator.run_coordinator.collect_preflight_warnings",
        AsyncMock(return_value=[warning]),
    ):
        run = await coordinator.execute(subject, "create_branch")

    assert run.status == "completed"
    mock_agent.run.assert_called_once()  # a WARNING never blocks execution
    step = mock_db.add.call_args_list[1][0][0]
    assert step.preflight_warnings == [
        {
            "code": "github_write_available",
            "dependency": "GitHub",
            "message": "No GitHub connection found.",
            "checked_at": "2026-07-31T00:00:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_multiple_warnings_all_persisted_in_order() -> None:
    from app.orchestrator.preflight import PreflightWarning

    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="create_branch",
        goal="create_branch",
        required_dependencies=frozenset({"github_write"}),
    )
    subject = _make_subject()

    warnings = [
        PreflightWarning(code="first", dependency="A", message="m1", checked_at="t0"),
        PreflightWarning(code="second", dependency="B", message="m2", checked_at="t1"),
    ]
    with patch(
        "app.orchestrator.run_coordinator.collect_preflight_warnings",
        AsyncMock(return_value=warnings),
    ):
        await coordinator.execute(subject, "create_branch")

    step = mock_db.add.call_args_list[1][0][0]
    assert [w["code"] for w in step.preflight_warnings] == ["first", "second"]


@pytest.mark.asyncio
async def test_warning_collection_called_with_manifest_db_and_user_id() -> None:
    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="create_branch",
        goal="create_branch",
        required_dependencies=frozenset({"github_write"}),
    )
    subject = _make_subject()

    with patch(
        "app.orchestrator.run_coordinator.collect_preflight_warnings",
        AsyncMock(return_value=[]),
    ) as mock_collect:
        run = await coordinator.execute(subject, "create_branch")

    mock_collect.assert_called_once()
    called_manifest, called_db, called_user_id = mock_collect.call_args[0]
    assert called_manifest.agent_id == "create_branch"
    assert called_db is mock_db
    assert called_user_id == run.user_id


@pytest.mark.asyncio
async def test_blocking_failure_never_collects_warnings() -> None:
    """BLOCKING short-circuits before WARNING collection is ever reached —
    proven by making collect_preflight_warnings raise if called at all."""
    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="create_branch",
        goal="create_branch",
        required_dependencies=frozenset({"github_write"}),
    )
    subject = _make_subject()

    with (
        patch(
            "app.orchestrator.run_coordinator.check_llm_provider_configured",
            return_value="No API key configured.",
        ),
        patch(
            "app.orchestrator.run_coordinator.collect_preflight_warnings",
            AsyncMock(side_effect=AssertionError("must not be called")),
        ),
        pytest.raises(PreFlightCheckFailed),
    ):
        await coordinator.execute(subject, "create_branch")

    mock_agent.run.assert_not_called()


@pytest.mark.asyncio
async def test_blocking_pass_and_warning_present_together() -> None:
    """The BLOCKING check passing and a WARNING firing are independent —
    both can be true for the same step, and the run still completes."""
    from app.orchestrator.preflight import PreflightWarning

    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="create_branch",
        goal="create_branch",
        required_dependencies=frozenset({"github_write"}),
    )
    subject = _make_subject()

    with (
        patch("app.orchestrator.run_coordinator.check_llm_provider_configured", return_value=None),
        patch(
            "app.orchestrator.run_coordinator.collect_preflight_warnings",
            AsyncMock(
                return_value=[
                    PreflightWarning(
                        code="github_write_available",
                        dependency="GitHub",
                        message="No GitHub connection found.",
                        checked_at="t0",
                    )
                ]
            ),
        ),
    ):
        run = await coordinator.execute(subject, "create_branch")

    assert run.status == "completed"
    mock_agent.run.assert_called_once()
    step = mock_db.add.call_args_list[1][0][0]
    assert len(step.preflight_warnings) == 1


@pytest.mark.asyncio
async def test_warning_persistence_does_not_add_an_extra_commit() -> None:
    """Transaction ownership: recording a warning must not introduce any
    new commit beyond RunCoordinator's normal two (running-transition,
    terminal) — the warning rides along in the terminal commit."""
    from app.orchestrator.preflight import PreflightWarning

    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="create_branch",
        goal="create_branch",
        required_dependencies=frozenset({"github_write"}),
    )
    subject = _make_subject()

    with patch(
        "app.orchestrator.run_coordinator.collect_preflight_warnings",
        AsyncMock(
            return_value=[PreflightWarning(code="c", dependency="d", message="m", checked_at="t")]
        ),
    ):
        await coordinator.execute(subject, "create_branch")

    assert mock_db.commit.call_count == 2


@pytest.mark.asyncio
async def test_resume_step_also_collects_and_persists_warnings() -> None:
    from app.orchestrator.preflight import PreflightWarning

    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="create_branch",
        goal="create_branch",
        required_dependencies=frozenset({"github_write"}),
    )
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:abc123",
        subject_type="freetext",
        goal="create_branch",
        status="awaiting_input",
    )
    step = AgentStep(
        id=uuid.uuid4(), run_id=run.id, agent_id="create_branch", status="awaiting_input"
    )

    with patch(
        "app.orchestrator.run_coordinator.collect_preflight_warnings",
        AsyncMock(
            return_value=[
                PreflightWarning(
                    code="github_write_available",
                    dependency="GitHub",
                    message="No GitHub connection found.",
                    checked_at="t0",
                )
            ]
        ),
    ):
        await coordinator.resume_step(
            run, step, "create_branch", mock_agent, _make_subject(), "create_branch"
        )

    assert step.preflight_warnings == [
        {
            "code": "github_write_available",
            "dependency": "GitHub",
            "message": "No GitHub connection found.",
            "checked_at": "t0",
        }
    ]


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_transitions_success() -> None:
    """Run goes queued → running → completed on success."""
    statuses: list[str] = []
    coordinator, mock_db, _ = _build_coordinator()
    subject = _make_subject()

    # Capture status at each flush/commit
    async def capture_flush(*a, **kw):
        run = mock_db.add.call_args_list[0][0][0]
        statuses.append(run.status)

    async def capture_commit(*a, **kw):
        run = mock_db.add.call_args_list[0][0][0]
        statuses.append(run.status)

    mock_db.flush = AsyncMock(side_effect=capture_flush)
    mock_db.commit = AsyncMock(side_effect=capture_commit)

    await coordinator.execute(subject, "plan_freeform")

    # queued (first flush) → running (second flush) → completed (commit)
    assert statuses[0] == "queued"
    assert statuses[1] == "running"
    assert statuses[-1] == "completed"


@pytest.mark.asyncio
async def test_status_transitions_agent_failure() -> None:
    """Run goes queued → running → failed on agent exception."""
    coordinator, mock_db, _ = _build_coordinator(agent_run_side_effect=RuntimeError("boom"))
    subject = _make_subject()

    with pytest.raises(RuntimeError):
        await coordinator.execute(subject, "plan_freeform")

    run = mock_db.add.call_args_list[0][0][0]
    assert run.status == "failed"
    assert run.completed_at is not None


# ---------------------------------------------------------------------------
# Pause/resume (awaiting_input) — reasoning-driven Context Discovery
# ---------------------------------------------------------------------------


def _make_awaiting_input_output(agent_id: str = "context_discovery") -> AgentOutput:
    return AgentOutput(
        agent_id=agent_id,
        subject_id="freetext:abc123",
        confidence=Confidence(score=0.3, reasoning="Blocked on a clarification question."),
        evidence=[
            Evidence(kind="tool_call", reference="neo4j_graph", summary="Queried the graph.")
        ],
        result={"readiness": "BLOCKED", "unresolved_questions": [{"question_id": "q1"}]},
        prompt_version="2.0",
        awaiting_input=True,
        pending_question={"question_id": "q1", "question": "Which repo?"},
    )


@pytest.mark.asyncio
async def test_execute_run_awaiting_input_does_not_complete() -> None:
    """An agent that sets awaiting_input=True must leave the run/step at
    'awaiting_input', never 'completed' — completing would let Planning
    read a paused, mid-reasoning result via get_stage_result()."""
    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="context_discovery", goal="discover_context"
    )
    mock_agent.run = AsyncMock(return_value=_make_awaiting_input_output())
    subject = _make_subject()

    run = await coordinator.execute(subject, "discover_context")

    assert run.status == "awaiting_input"
    assert run.completed_at is None
    step = mock_db.add.call_args_list[1][0][0]
    assert step.status == "awaiting_input"
    assert step.completed_at is None
    assert step.result["readiness"] == "BLOCKED"
    # Running-transition commit, then the awaiting_input commit.
    assert mock_db.commit.call_count == 2


@pytest.mark.asyncio
async def test_resume_step_completes_on_non_paused_output() -> None:
    """resume_step reuses the existing AgentStep row (no new one created)
    and applies the same completed/awaiting_input branch as execute_run."""
    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    registry = AgentRegistry()
    manifest = _make_manifest("context_discovery", "discover_context")
    mock_agent = AsyncMock()
    mock_agent.run = AsyncMock(return_value=_make_output("context_discovery"))
    registry.register(manifest, mock_agent)

    coordinator = RunCoordinator(db=mock_db, registry=registry, selector=None)

    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name="Test task",
        goal="discover_context",
        status="awaiting_input",
    )
    step = AgentStep(
        id=uuid.uuid4(), run_id=run.id, agent_id="context_discovery", status="awaiting_input"
    )

    resumed = await coordinator.resume_step(
        run,
        step,
        "context_discovery",
        mock_agent,
        _make_subject(),
        "discover_context",
        extras={
            "resume": {"working_context": {}, "answer": {"question_id": "q1", "answer": "yes"}}
        },
    )

    assert resumed.status == "completed"
    assert step.status == "completed"
    # No second AgentStep was created for the resume — db.add is only
    # called by db mocking elsewhere in this test file's setup, never here.
    mock_db.add.assert_not_called()
    mock_agent.run.assert_awaited_once()
    call_extras = mock_agent.run.await_args[0][0].extras
    assert call_extras["resume"]["answer"]["question_id"] == "q1"


@pytest.mark.asyncio
async def test_resume_step_stays_paused_when_output_awaiting_input_again() -> None:
    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    registry = AgentRegistry()
    registry.register(_make_manifest("context_discovery", "discover_context"), AsyncMock())
    mock_agent = AsyncMock()
    mock_agent.run = AsyncMock(return_value=_make_awaiting_input_output())

    coordinator = RunCoordinator(db=mock_db, registry=registry, selector=None)
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name="Test task",
        goal="discover_context",
        status="awaiting_input",
    )
    step = AgentStep(
        id=uuid.uuid4(), run_id=run.id, agent_id="context_discovery", status="awaiting_input"
    )

    resumed = await coordinator.resume_step(
        run,
        step,
        "context_discovery",
        mock_agent,
        _make_subject(),
        "discover_context",
        extras={"resume": {"working_context": {}, "answer": {"question_id": "q1", "answer": "no"}}},
    )

    assert resumed.status == "awaiting_input"
    assert step.status == "awaiting_input"
    assert step.completed_at is None


# ---------------------------------------------------------------------------
# P0-1 regression — a persistence failure must never leave a Run silently
# stuck at "running"/"awaiting_input" (real production incident: a `set`
# inside `AgentOutput.result` reached `json.dumps` and raised `TypeError`,
# which cascaded into a `PendingRollbackError` that escaped every handler —
# see `RunCoordinator._commit_or_fail`'s own docstring for the full
# mechanism this now guards against).
# ---------------------------------------------------------------------------


def _flaky_commit_raising_on_call(n: int, error: Exception) -> AsyncMock:
    """An `AsyncMock` standing in for `db.commit` that raises `error` on
    exactly the `n`-th call (1-indexed) and succeeds on every other call —
    the shape every test below needs to land the failure on a specific
    commit in the sequence (the running-transition commit must always
    succeed; only the result-persisting commit should fail)."""
    calls = {"count": 0}

    async def _commit() -> None:
        calls["count"] += 1
        if calls["count"] == n:
            raise error

    return AsyncMock(side_effect=_commit)


def _flaky_commit_failing_calls(failing: set[int], error: Exception) -> AsyncMock:
    """Like `_flaky_commit_raising_on_call`, but raises on every call number
    in `failing` (1-indexed) rather than just one — for modeling a commit
    that keeps failing across a rollback-and-retry until the poisoned data
    is actually discarded (real SQLAlchemy semantics a plain call-counting
    mock can't reproduce on its own; the test asserts the *contract*, not
    a faithful dirty-state simulation)."""
    calls = {"count": 0}

    async def _commit() -> None:
        calls["count"] += 1
        if calls["count"] in failing:
            raise error

    return AsyncMock(side_effect=_commit)


@pytest.mark.asyncio
async def test_a_serialization_failure_on_completion_marks_the_run_failed_not_stuck() -> None:
    """The exact reported incident, reproduced directly: the commit that
    would persist a completed run's result raises `TypeError` (a `set`
    reached `json.dumps`). The run must end up explicitly `failed`, with a
    real `error_message` and `completed_at` — never left at "running"."""
    coordinator, mock_db, mock_agent = _build_coordinator()
    mock_db.commit = _flaky_commit_raising_on_call(
        2, TypeError("Object of type set is not JSON serializable")
    )
    mock_db.rollback = AsyncMock()

    run = await coordinator.execute(_make_subject(), "plan_freeform")

    assert run.status == "failed"
    assert run.error_message is not None
    assert "Object of type set is not JSON serializable" in run.error_message
    assert run.completed_at is not None
    mock_db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_serialization_failure_while_pausing_for_clarification_marks_the_run_failed() -> (
    None
):
    """Same guarantee, on the pause path specifically — this is the exact
    branch the real incident happened on: Context Discovery correctly
    reaching `awaiting_input`, then failing to persist that paused state.
    The honest outcome is `failed`, not a run silently left claiming to
    be "awaiting_input" (or "running") while actually abandoned."""
    coordinator, mock_db, mock_agent = _build_coordinator(
        agent_id="context_discovery", goal="discover_context"
    )
    mock_agent.run = AsyncMock(return_value=_make_awaiting_input_output())
    mock_db.commit = _flaky_commit_raising_on_call(
        2, TypeError("Object of type set is not JSON serializable")
    )
    mock_db.rollback = AsyncMock()

    run = await coordinator.execute(_make_subject(), "discover_context")

    assert run.status == "failed"
    assert run.error_message is not None
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_a_serialization_failure_on_resume_marks_the_run_failed_not_stuck() -> None:
    """Same guarantee again, on `resume_step` — a human just answered a
    clarification question; the re-investigation completes, and *that*
    persistence attempt fails. Must not silently strand the run at
    "running" with the human's answer lost and no way to tell."""
    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = _flaky_commit_raising_on_call(
        2, TypeError("Object of type set is not JSON serializable")
    )
    mock_db.rollback = AsyncMock()

    registry = AgentRegistry()
    registry.register(_make_manifest("context_discovery", "discover_context"), AsyncMock())
    mock_agent = AsyncMock()
    mock_agent.run = AsyncMock(return_value=_make_output("context_discovery"))

    coordinator = RunCoordinator(db=mock_db, registry=registry, selector=None)
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name="Test task",
        goal="discover_context",
        status="awaiting_input",
    )
    step = AgentStep(
        id=uuid.uuid4(), run_id=run.id, agent_id="context_discovery", status="awaiting_input"
    )

    resumed = await coordinator.resume_step(
        run,
        step,
        "context_discovery",
        mock_agent,
        _make_subject(),
        "discover_context",
        extras={"resume": {"working_context": {}, "answer": {"question_id": "q1", "answer": "x"}}},
    )

    assert resumed.status == "failed"
    assert resumed.error_message is not None
    assert resumed.completed_at is not None


@pytest.mark.asyncio
async def test_a_failing_on_pre_commit_hook_that_recovers_keeps_the_runs_own_result() -> None:
    """`on_pre_commit`'s own commit fails, but the fallback plain commit of
    `run`'s already-set fields (still "completed", from `_apply_agent_
    output`) succeeds — meaning the run's own result genuinely was fine;
    only the hook's side effects (e.g. workflow advancement) were lost.
    This is `_commit_with_hook`'s own documented, pre-existing contract
    ("a bookkeeping bug can never cost the run its own recorded outcome")
    and must keep holding: the run stays `completed`, not incorrectly
    downgraded to `failed` for a problem that wasn't its own."""
    coordinator, mock_db, mock_agent = _build_coordinator()
    # Call 1: running-transition commit (succeeds). Call 2: the hook's own
    # commit (fails). Call 3: `_commit_or_fail`'s fallback plain commit of
    # `run` alone (succeeds — nothing about `run` itself was ever bad).
    mock_db.commit = _flaky_commit_raising_on_call(
        2, TypeError("Object of type set is not JSON serializable")
    )
    mock_db.rollback = AsyncMock()

    async def on_pre_commit(db, run) -> None:  # noqa: ANN001 - matches Callable[[AsyncSession, Run], Awaitable[None]]
        await db.commit()

    run, agent_id, agent = await coordinator.create_pending_run(_make_subject(), "plan_freeform")
    run = await coordinator.execute_run(
        run,
        agent_id,
        agent,
        _make_subject(),
        "plan_freeform",
        on_pre_commit=on_pre_commit,
    )

    assert run.status == "completed"
    assert run.error_message is None


@pytest.mark.asyncio
async def test_a_failing_on_pre_commit_hook_whose_data_stays_bad_still_marks_the_run_failed() -> (
    None
):
    """The real production shape: `on_pre_commit` (the workflow-stage
    finalizer) shares this coordinator's session, so its failed commit and
    `_commit_or_fail`'s own first retry both attempt to flush the *same*
    poisoned `step.result` — both fail, until the rollback that precedes
    the second retry actually discards it. The run must still end up
    `failed`, never left at `completed`/`running` with the bad result
    quietly re-attempted forever."""
    coordinator, mock_db, mock_agent = _build_coordinator()
    # Call 1: running-transition (succeeds). Calls 2-3: the hook's commit,
    # then `_commit_or_fail`'s first plain-retry — both still see the bad
    # data and fail. Call 4: the retry *after* `rollback()` — the poisoned
    # state is gone, this one succeeds, and `_commit_or_fail` has already
    # set status="failed" before making it.
    mock_db.commit = _flaky_commit_failing_calls(
        {2, 3}, TypeError("Object of type set is not JSON serializable")
    )
    mock_db.rollback = AsyncMock()

    async def on_pre_commit(db, run) -> None:  # noqa: ANN001 - matches Callable[[AsyncSession, Run], Awaitable[None]]
        await db.commit()

    run, agent_id, agent = await coordinator.create_pending_run(_make_subject(), "plan_freeform")
    run = await coordinator.execute_run(
        run,
        agent_id,
        agent,
        _make_subject(),
        "plan_freeform",
        on_pre_commit=on_pre_commit,
    )

    assert run.status == "failed"
    assert run.error_message is not None
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_when_even_the_retry_commit_fails_a_fresh_session_forces_the_run_failed() -> None:
    """Both the original commit *and* the rollback-and-retry commit fail —
    the session is unrecoverable. `_force_fail_run` must still land the
    run at `failed` through a brand new, independent session rather than
    leaving it stuck."""
    coordinator, mock_db, mock_agent = _build_coordinator()
    # Call 1 (running-transition) succeeds; every commit from call 2
    # onward (the result-persisting commit, and its rollback-and-retry)
    # keeps failing — the session is unrecoverable.
    mock_db.commit = _flaky_commit_failing_calls(
        {2, 3, 4, 5}, TypeError("Object of type set is not JSON serializable")
    )
    mock_db.rollback = AsyncMock()

    fresh_db = AsyncMock()
    fresh_db.execute = AsyncMock()
    fresh_db.commit = AsyncMock()
    fresh_session_cm = AsyncMock()
    fresh_session_cm.__aenter__ = AsyncMock(return_value=fresh_db)
    fresh_session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.database.session.AsyncSessionLocal", return_value=fresh_session_cm
    ):
        run = await coordinator.execute(_make_subject(), "plan_freeform")

    # The original (poisoned) session's `run` object never got its status
    # written successfully — this test's real assertion is that the fresh
    # session's UPDATE was actually issued and committed, not that the
    # in-memory `run` object reflects it (it can't, in this scenario).
    fresh_db.execute.assert_awaited_once()
    fresh_db.commit.assert_awaited_once()
    assert run.status != "completed"
