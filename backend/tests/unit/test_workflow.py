"""Unit tests for the SDLC Workflow system.

Covers:
- Workflow model creation
- Workflow service (stage transitions, context building)
- Workflow API DTOs
- Run linking
- Context propagation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.run import Run
from app.models.workflow import Workflow
from app.services.workflow_service import (
    STAGES,
    STAGE_GOALS,
    STAGE_LABELS,
    WORKFLOW_TYPE_STAGES,
    _summarize_previous_output,
    build_stage_context,
    next_stage,
    stage_sequence,
)

# ---------------------------------------------------------------------------
# Stage definitions tests
# ---------------------------------------------------------------------------


def test_stages_order() -> None:
    assert STAGES == ("planning", "development", "testing", "review")


def test_stage_goals_mapping() -> None:
    assert STAGE_GOALS["planning"] == "plan_freeform"
    assert STAGE_GOALS["development"] == "develop_change_plan"
    assert STAGE_GOALS["testing"] == "plan_tests"
    assert STAGE_GOALS["review"] == "review_pr"


def test_stage_labels() -> None:
    assert STAGE_LABELS["planning"] == "Planning"
    assert STAGE_LABELS["development"] == "Development"
    assert STAGE_LABELS["testing"] == "Testing"
    assert STAGE_LABELS["review"] == "Review"


# ---------------------------------------------------------------------------
# next_stage tests
# ---------------------------------------------------------------------------


def test_next_stage_planning() -> None:
    assert next_stage("planning") == "development"


def test_next_stage_development() -> None:
    assert next_stage("development") == "testing"


def test_next_stage_testing() -> None:
    assert next_stage("testing") == "review"


def test_next_stage_review_is_none() -> None:
    assert next_stage("review") is None


def test_next_stage_unknown() -> None:
    assert next_stage("unknown") is None


# ---------------------------------------------------------------------------
# workflow_type-aware stage registry tests (shared-engine design)
# ---------------------------------------------------------------------------


def test_legacy_sdlc_sequence_is_exactly_stages() -> None:
    """Backward compatibility: the pre-existing 4-stage sequence is never
    reinterpreted — legacy_sdlc is byte-for-byte the old STAGES tuple."""
    assert WORKFLOW_TYPE_STAGES["legacy_sdlc"] == STAGES
    assert stage_sequence("legacy_sdlc") == ("planning", "development", "testing", "review")


def test_planning_sequence_ends_in_engineering_review_not_review() -> None:
    assert stage_sequence("planning") == (
        "planning",
        "development",
        "testing",
        "engineering_review",
    )
    assert "review" not in stage_sequence("planning")


def test_stage_sequence_unknown_type_falls_back_to_legacy() -> None:
    """A workflow with a not-yet-known workflow_type (or None) is never
    reinterpreted onto the new "planning" stages — falls back to the
    frozen default."""
    assert stage_sequence("some_future_type") == STAGES
    assert stage_sequence(None) == STAGES  # type: ignore[arg-type]


def test_next_stage_respects_workflow_type() -> None:
    assert next_stage("testing", "planning") == "engineering_review"
    assert next_stage("engineering_review", "planning") is None
    # legacy_sdlc (the default) is unaffected — proves the two sequences
    # are genuinely independent, not one leaking into the other.
    assert next_stage("testing", "legacy_sdlc") == "review"


def test_engineering_review_stage_metadata_registered() -> None:
    assert STAGE_GOALS["engineering_review"] == "review_readiness"
    assert STAGE_LABELS["engineering_review"] == "Engineering Review"


# ---------------------------------------------------------------------------
# Context summarization tests
# ---------------------------------------------------------------------------


def _make_run(result: dict | None = None, goal: str = "plan_freeform") -> Run:
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test task",
        goal=goal,
        status="completed",
        created_at=datetime.now(timezone.utc),
    )
    step = MagicMock()
    step.result = result
    step.confidence_score = 0.85
    run.steps = [step]
    return run


def test_summarize_previous_output_empty() -> None:
    run = _make_run(result=None)
    assert _summarize_previous_output(run) == ""


def test_summarize_previous_output_with_summary() -> None:
    run = _make_run(
        result={
            "executive_summary": "Implement JWT auth across all services.",
            "affected_components": ["OrderController", "PaymentService"],
        }
    )
    summary = _summarize_previous_output(run)
    assert "JWT auth" in summary
    assert "OrderController" in summary


def test_summarize_previous_output_multiple_fields() -> None:
    run = _make_run(
        result={
            "executive_summary": "Plan complete.",
            "affected_repositories": ["order-service", "payment-service"],
            "kafka_topics_involved": ["order.created"],
            "risk_considerations": ["Token expiry"],
        }
    )
    summary = _summarize_previous_output(run)
    assert "order-service" in summary
    assert "order.created" in summary
    assert "Token expiry" in summary


def test_summarize_previous_output_no_steps() -> None:
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="plan_freeform",
        status="completed",
        created_at=datetime.now(timezone.utc),
    )
    run.steps = []
    assert _summarize_previous_output(run) == ""


# ---------------------------------------------------------------------------
# build_stage_context tests
# ---------------------------------------------------------------------------


def test_build_stage_context_first_stage() -> None:
    workflow = Workflow(
        id=uuid.uuid4(),
        title="JWT auth",
        current_stage="planning",
        status="in_progress",
    )
    workflow.runs = []
    ctx = build_stage_context(workflow, "JWT auth", "planning")
    assert ctx == "JWT auth"


def test_build_stage_context_second_stage() -> None:
    workflow = Workflow(
        id=uuid.uuid4(),
        title="JWT auth",
        current_stage="development",
        status="in_progress",
    )
    planning_run = _make_run(
        result={"executive_summary": "Plan: implement JWT in all services."},
        goal="plan_freeform",
    )
    planning_run.workflow_stage = "planning"
    planning_run.workflow_id = workflow.id
    workflow.runs = [planning_run]

    ctx = build_stage_context(workflow, "JWT auth", "development")
    assert "JWT auth" in ctx
    assert "Plan: implement JWT in all services." in ctx
    assert "Planning stage" in ctx


def test_build_stage_context_third_stage_accumulates() -> None:
    workflow = Workflow(
        id=uuid.uuid4(),
        title="JWT auth",
        current_stage="testing",
        status="in_progress",
    )
    planning_run = _make_run(
        result={"executive_summary": "Plan: JWT implementation."},
        goal="plan_freeform",
    )
    planning_run.workflow_stage = "planning"
    planning_run.workflow_id = workflow.id
    planning_run.created_at = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)

    dev_run = _make_run(
        result={"executive_summary": "Dev plan: 3 phases, 5 components."},
        goal="develop_change_plan",
    )
    dev_run.workflow_stage = "development"
    dev_run.workflow_id = workflow.id
    dev_run.created_at = datetime(2026, 7, 23, 11, 0, tzinfo=timezone.utc)

    workflow.runs = [planning_run, dev_run]

    ctx = build_stage_context(workflow, "JWT auth", "testing")
    assert "JWT auth" in ctx
    assert "JWT implementation" in ctx
    assert "3 phases, 5 components" in ctx


# ---------------------------------------------------------------------------
# Workflow model tests
# ---------------------------------------------------------------------------


def test_workflow_model_defaults() -> None:
    w = Workflow(
        id=uuid.uuid4(),
        title="Test workflow",
        current_stage="planning",
        status="in_progress",
    )
    assert w.current_stage == "planning"
    assert w.status == "in_progress"


def test_run_model_workflow_fields() -> None:
    wf_id = uuid.uuid4()
    prev_id = uuid.uuid4()
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="plan_freeform",
        status="completed",
        workflow_id=wf_id,
        workflow_stage="planning",
        previous_run_id=prev_id,
    )
    assert run.workflow_id == wf_id
    assert run.workflow_stage == "planning"
    assert run.previous_run_id == prev_id


def test_run_model_workflow_fields_optional() -> None:
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="plan_freeform",
        status="completed",
    )
    assert run.workflow_id is None
    assert run.workflow_stage is None
    assert run.previous_run_id is None


# ---------------------------------------------------------------------------
# Workflow service integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow() -> None:
    from app.services.workflow_service import create_workflow

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    workflow = await create_workflow(mock_db, title="JWT auth implementation")

    assert workflow.title == "JWT auth implementation"
    assert workflow.current_stage == "planning"
    assert workflow.status == "in_progress"
    mock_db.add.assert_called_once()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_advance_workflow_to_development() -> None:
    from app.services.workflow_service import advance_workflow

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()

    workflow = Workflow(
        id=uuid.uuid4(),
        title="Test",
        current_stage="planning",
        status="in_progress",
    )

    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="plan_freeform",
        status="completed",
        workflow_stage="planning",
    )

    await advance_workflow(mock_db, workflow, run)

    assert workflow.current_stage == "development"
    assert workflow.status == "in_progress"


@pytest.mark.asyncio
async def test_advance_workflow_to_completed() -> None:
    from app.services.workflow_service import advance_workflow

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()

    workflow = Workflow(
        id=uuid.uuid4(),
        title="Test",
        current_stage="review",
        status="in_progress",
    )

    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="review_pr",
        status="completed",
        workflow_stage="review",
    )

    await advance_workflow(mock_db, workflow, run)

    assert workflow.current_stage == "completed"
    assert workflow.status == "completed"


@pytest.mark.asyncio
async def test_create_workflow_defaults_to_planning_type() -> None:
    from app.services.workflow_service import create_workflow

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    workflow = await create_workflow(mock_db, title="Add rate limiting")

    assert workflow.workflow_type == "planning"
    assert workflow.current_stage == "planning"


@pytest.mark.asyncio
async def test_create_workflow_rejects_legacy_sdlc_type() -> None:
    """legacy_sdlc is not creatable — it exists only for pre-existing rows."""
    from app.core.exceptions import AppError
    from app.services.workflow_service import create_workflow

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with pytest.raises(AppError) as exc_info:
        await create_workflow(mock_db, title="Old-style run", workflow_type="legacy_sdlc")
    assert exc_info.value.status_code == 400
    assert "invalid_workflow_type" in str(exc_info.value.error_code)


@pytest.mark.asyncio
async def test_create_workflow_rejects_unknown_type() -> None:
    """Arbitrary workflow_type values must be rejected, not silently accepted."""
    from app.core.exceptions import AppError
    from app.services.workflow_service import create_workflow

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with pytest.raises(AppError) as exc_info:
        await create_workflow(mock_db, title="Bad type", workflow_type="foobar")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_advance_planning_workflow_to_awaiting_approval_at_last_stage() -> None:
    """The real bug this exists to fix: a Planning workflow must not
    silently become "completed" the way legacy_sdlc does — it gates on a
    human decision, and current_stage stays put (nothing to advance to)."""
    from app.services.workflow_service import advance_workflow

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()

    workflow = Workflow(
        id=uuid.uuid4(),
        title="Test",
        current_stage="engineering_review",
        status="in_progress",
        workflow_type="planning",
    )
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="review_readiness",
        status="completed",
        workflow_stage="engineering_review",
    )

    await advance_workflow(mock_db, workflow, run)

    assert workflow.status == "awaiting_approval"
    assert workflow.current_stage == "engineering_review"  # unchanged, not "completed"


@pytest.mark.asyncio
async def test_advance_planning_workflow_mid_sequence_still_advances_normally() -> None:
    from app.services.workflow_service import advance_workflow

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()

    workflow = Workflow(
        id=uuid.uuid4(),
        title="Test",
        current_stage="testing",
        status="in_progress",
        workflow_type="planning",
    )
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="plan_tests",
        status="completed",
        workflow_stage="testing",
    )

    await advance_workflow(mock_db, workflow, run)

    assert workflow.current_stage == "engineering_review"
    assert workflow.status == "in_progress"


@pytest.mark.asyncio
async def test_approve_workflow_sets_terminal_status() -> None:
    from app.services.workflow_service import approve_workflow

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    workflow = Workflow(
        id=uuid.uuid4(),
        title="Test",
        current_stage="engineering_review",
        status="awaiting_approval",
        workflow_type="planning",
    )

    await approve_workflow(mock_db, workflow)

    assert workflow.status == "approved"
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_workflow_sets_terminal_status() -> None:
    from app.services.workflow_service import reject_workflow

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    workflow = Workflow(
        id=uuid.uuid4(),
        title="Test",
        current_stage="engineering_review",
        status="awaiting_approval",
        workflow_type="planning",
    )

    await reject_workflow(mock_db, workflow)

    assert workflow.status == "rejected"


@pytest.mark.asyncio
async def test_advance_workflow_no_advance_on_failure() -> None:
    from app.services.workflow_service import advance_workflow

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()

    workflow = Workflow(
        id=uuid.uuid4(),
        title="Test",
        current_stage="planning",
        status="in_progress",
    )

    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="plan_freeform",
        status="failed",
        workflow_stage="planning",
    )

    await advance_workflow(mock_db, workflow, run)

    # Should not advance
    assert workflow.current_stage == "planning"


# ---------------------------------------------------------------------------
# API DTO tests
# ---------------------------------------------------------------------------


def test_workflow_api_create_request_validation() -> None:
    from app.api.v1.routers.workflows import CreateWorkflowRequest

    req = CreateWorkflowRequest(title="JWT auth")
    assert req.title == "JWT auth"
    assert req.model is None


def test_workflow_api_continue_request_validation() -> None:
    from app.api.v1.routers.workflows import ContinueWorkflowRequest

    req = ContinueWorkflowRequest()
    assert req.model is None


def test_workflow_api_detail_response_shape() -> None:
    from app.api.v1.routers.workflows import (
        WorkflowDetailResponse,
        WorkflowStageResponse,
        WorkflowRunResponse,
    )

    stage = WorkflowStageResponse(
        stage="planning",
        label="Planning",
        status="completed",
        run_id="run-uuid",
    )
    run_item = WorkflowRunResponse(
        run_id="run-uuid",
        goal="plan_freeform",
        status="completed",
        workflow_stage="planning",
        confidence_score=0.85,
        started_at="2026-07-23T10:00:00Z",
        completed_at="2026-07-23T10:01:00Z",
        created_at="2026-07-23T10:00:00Z",
    )
    detail = WorkflowDetailResponse(
        workflow_id="wf-uuid",
        title="JWT auth",
        workflow_type="planning",
        current_stage="development",
        status="in_progress",
        stages=[stage],
        runs=[run_item],
        created_at="2026-07-23T10:00:00Z",
        updated_at="2026-07-23T10:01:00Z",
    )
    assert detail.workflow_id == "wf-uuid"
    assert detail.workflow_type == "planning"
    assert detail.stages[0].stage == "planning"
    assert detail.runs[0].confidence_score == 0.85


def test_workflow_api_create_request_defaults_to_planning() -> None:
    from app.api.v1.routers.workflows import CreateWorkflowRequest

    req = CreateWorkflowRequest(title="JWT auth")
    assert req.workflow_type == "planning"


def test_workflow_api_approval_response_shape() -> None:
    from app.api.v1.routers.workflows import WorkflowApprovalResponse

    resp = WorkflowApprovalResponse(workflow_id="wf-uuid", status="approved")
    assert resp.workflow_id == "wf-uuid"
    assert resp.status == "approved"


# ---------------------------------------------------------------------------
# Run API DTO with workflow fields
# ---------------------------------------------------------------------------


def test_run_detail_response_includes_workflow_fields() -> None:
    from app.api.v1.routers.agent_runs import RunDetailResponse, SubjectResponse

    resp = RunDetailResponse(
        run_id="run-uuid",
        goal="plan_freeform",
        status="completed",
        subject=SubjectResponse(
            subject_id="freetext:test", subject_type="freetext", display_name="Test"
        ),
        model=None,
        error_message=None,
        started_at="2026-07-23T10:00:00Z",
        completed_at="2026-07-23T10:01:00Z",
        created_at="2026-07-23T10:00:00Z",
        steps=[],
        workflow_id="wf-uuid",
        workflow_stage="planning",
        previous_run_id="prev-uuid",
    )
    assert resp.workflow_id == "wf-uuid"
    assert resp.workflow_stage == "planning"
    assert resp.previous_run_id == "prev-uuid"


def test_run_list_item_includes_workflow_fields() -> None:
    from app.api.v1.routers.agent_runs import RunListItem, SubjectResponse

    item = RunListItem(
        run_id="run-uuid",
        goal="plan_freeform",
        status="completed",
        subject=SubjectResponse(
            subject_id="freetext:test", subject_type="freetext", display_name="Test"
        ),
        started_at=None,
        completed_at=None,
        created_at="2026-07-23T10:00:00Z",
        confidence_score=0.85,
        workflow_id="wf-uuid",
        workflow_stage="planning",
    )
    assert item.workflow_id == "wf-uuid"


def test_run_list_item_workflow_fields_optional() -> None:
    from app.api.v1.routers.agent_runs import RunListItem, SubjectResponse

    item = RunListItem(
        run_id="run-uuid",
        goal="plan_freeform",
        status="completed",
        subject=SubjectResponse(
            subject_id="freetext:test", subject_type="freetext", display_name="Test"
        ),
        started_at=None,
        completed_at=None,
        created_at="2026-07-23T10:00:00Z",
        confidence_score=None,
    )
    assert item.workflow_id is None
    assert item.workflow_stage is None
