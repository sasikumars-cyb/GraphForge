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

from unittest.mock import AsyncMock, MagicMock

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


def _make_manifest(agent_id: str, goal: str) -> AgentManifest:
    return AgentManifest(
        agent_id=agent_id,
        purpose=f"Test {agent_id}.",
        goals=frozenset({goal}),
        accepted_subject_types=frozenset({"freetext"}),
        cost_class="cheap",
    )


def _build_coordinator(
    agent_id: str = "planning",
    goal: str = "plan_freeform",
    agent_run_side_effect: Exception | None = None,
    register_agent: bool = True,
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
        registry.register(_make_manifest(agent_id, goal), mock_agent)

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

    mock_db.commit.assert_called_once()


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

    mock_db.commit.assert_called_once()


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
