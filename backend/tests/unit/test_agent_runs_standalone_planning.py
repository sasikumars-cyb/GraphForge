"""Tests for the standalone Development/Testing → prior-Planning-run
grounding capability (CreateRunRequest.planning_run_id).

Covers the pieces added to app.api.v1.routers.agent_runs:
- the duck-typed shim (_StandalonePlanningContext/_StandalonePlanningRun)
  that lets the existing, unmodified get_stage_result() recognize a
  standalone Planning run as if it were a Workflow stage
- _load_standalone_planning_context's validation, including goal-scoping
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.git_ops._artifact_reader import get_stage_result
from app.api.v1.routers.agent_runs import (
    _load_standalone_planning_context,
    _StandalonePlanningContext,
    _StandalonePlanningRun,
)
from app.core.exceptions import AppError, NotFoundError
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.orchestrator.selector import GOAL_DEVELOP_CHANGE_PLAN, GOAL_PLAN_TESTS


def _make_completed_planning_run(result: dict) -> Run:
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:x",
        subject_type="freetext",
        display_name="x",
        goal="plan_freeform",
        status="completed",
        created_at=datetime.now(UTC),
    )
    step = AgentStep(
        id=uuid.uuid4(),
        run_id=run.id,
        agent_id="planning",
        status="completed",
        result=result,
    )
    # `run.steps` is a real relationship — bypassing the ORM session by
    # assigning the list directly is exactly what a duck-typed shim needs
    # to avoid (a lazy-load / MissingGreenlet in async context), so this
    # mirrors how the real code path always has `steps` eagerly loaded.
    run.steps = [step]
    return run


def test_shim_makes_get_stage_result_recognize_a_standalone_run() -> None:
    real_run = _make_completed_planning_run({"executive_summary": "A real plan."})

    context = _StandalonePlanningContext(real_run)
    result = get_stage_result(context, "planning")  # type: ignore[arg-type]

    assert result == {"executive_summary": "A real plan."}


def test_shim_run_proxies_every_attribute_except_workflow_stage() -> None:
    real_run = _make_completed_planning_run({"executive_summary": "A real plan."})
    shim = _StandalonePlanningRun(real_run)

    assert shim.workflow_stage == "planning"  # overridden, not the real (None) column
    assert shim.status == real_run.status
    assert shim.created_at == real_run.created_at
    assert shim.steps == real_run.steps


@pytest.mark.asyncio
@pytest.mark.parametrize("goal", [GOAL_DEVELOP_CHANGE_PLAN, GOAL_PLAN_TESTS])
async def test_load_standalone_planning_context_rejects_invalid_uuid(goal: str) -> None:
    with pytest.raises(AppError) as exc_info:
        await _load_standalone_planning_context(AsyncMock(), uuid.uuid4(), goal, "not-a-uuid")
    assert exc_info.value.error_code == "invalid_planning_run_id"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "goal",
    ["plan_freeform", "plan_documentation", "review_readiness", "review_pr", "generate_code"],
)
async def test_load_standalone_planning_context_rejects_unsupported_goals(goal: str) -> None:
    """Only Development/Testing consume `context.extras["workflow"]` via
    get_stage_result(workflow, "planning") — every other goal is rejected
    before any DB lookup even happens, rather than silently accepted and
    ignored (or, for plan_documentation/review_readiness, partially and
    confusingly applied — see the field's own docstring)."""
    with pytest.raises(AppError) as exc_info:
        await _load_standalone_planning_context(AsyncMock(), uuid.uuid4(), goal, str(uuid.uuid4()))
    assert exc_info.value.error_code == "planning_run_id_unsupported_goal"


@pytest.mark.asyncio
async def test_load_standalone_planning_context_rejects_wrong_goal() -> None:
    wrong_goal_run = _make_completed_planning_run({})
    wrong_goal_run.goal = "develop_change_plan"

    with (
        patch(
            "app.api.v1.routers.agent_runs._get_owned_run",
            new=AsyncMock(return_value=wrong_goal_run),
        ),
        pytest.raises(AppError) as exc_info,
    ):
        await _load_standalone_planning_context(
            AsyncMock(), uuid.uuid4(), GOAL_DEVELOP_CHANGE_PLAN, str(wrong_goal_run.id)
        )
    assert exc_info.value.error_code == "invalid_planning_run_reference"


@pytest.mark.asyncio
async def test_load_standalone_planning_context_rejects_incomplete_run() -> None:
    incomplete_run = _make_completed_planning_run({})
    incomplete_run.status = "running"

    with (
        patch(
            "app.api.v1.routers.agent_runs._get_owned_run",
            new=AsyncMock(return_value=incomplete_run),
        ),
        pytest.raises(AppError) as exc_info,
    ):
        await _load_standalone_planning_context(
            AsyncMock(), uuid.uuid4(), GOAL_PLAN_TESTS, str(incomplete_run.id)
        )
    assert exc_info.value.error_code == "invalid_planning_run_reference"


@pytest.mark.asyncio
@pytest.mark.parametrize("goal", [GOAL_DEVELOP_CHANGE_PLAN, GOAL_PLAN_TESTS])
async def test_load_standalone_planning_context_accepts_a_valid_completed_planning_run(
    goal: str,
) -> None:
    real_run = _make_completed_planning_run({"executive_summary": "A real plan."})

    with patch(
        "app.api.v1.routers.agent_runs._get_owned_run", new=AsyncMock(return_value=real_run)
    ):
        context = await _load_standalone_planning_context(
            AsyncMock(), uuid.uuid4(), goal, str(real_run.id)
        )

    assert get_stage_result(context, "planning") == {"executive_summary": "A real plan."}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_load_standalone_planning_context_propagates_not_found() -> None:
    with (
        patch(
            "app.api.v1.routers.agent_runs._get_owned_run",
            new=AsyncMock(side_effect=NotFoundError("Run 'x' not found.")),
        ),
        pytest.raises(NotFoundError),
    ):
        await _load_standalone_planning_context(
            AsyncMock(), uuid.uuid4(), GOAL_DEVELOP_CHANGE_PLAN, str(uuid.uuid4())
        )
