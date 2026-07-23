"""Workflow API — SDLC workflow lifecycle management.

POST /workflows                    → Create a workflow and run the first stage
GET  /workflows                    → List workflows (paginated)
GET  /workflows/{workflow_id}      → Get workflow with all stages and runs
POST /workflows/{workflow_id}/continue → Continue to the next SDLC stage
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.context.resolvers.freetext import resolve as resolve_freetext
from app.core.exceptions import AppError, NotFoundError
from app.database.session import get_db_session
from app.models.user import User
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import AgentSelector
from app.services import workflow_service

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class CreateWorkflowRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    model: str | None = None


class ContinueWorkflowRequest(BaseModel):
    model: str | None = None


class WorkflowRunResponse(BaseModel):
    run_id: str
    goal: str
    status: str
    workflow_stage: str | None
    confidence_score: float | None
    started_at: str | None
    completed_at: str | None
    created_at: str


class WorkflowStageResponse(BaseModel):
    stage: str
    label: str
    status: str  # "completed" | "running" | "failed" | "pending"
    run_id: str | None


class WorkflowDetailResponse(BaseModel):
    workflow_id: str
    title: str
    current_stage: str
    status: str
    stages: list[WorkflowStageResponse]
    runs: list[WorkflowRunResponse]
    created_at: str
    updated_at: str


class WorkflowListItem(BaseModel):
    workflow_id: str
    title: str
    current_stage: str
    status: str
    stages: list[WorkflowStageResponse]
    created_at: str
    updated_at: str


class WorkflowListResponse(BaseModel):
    items: list[WorkflowListItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class ContinueWorkflowResponse(BaseModel):
    workflow_id: str
    run_id: str
    stage: str
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: object) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _build_stages(workflow) -> list[WorkflowStageResponse]:
    """Build the stage list from a workflow and its runs."""
    # Map stage → run
    stage_runs: dict[str, object] = {}
    for run in (workflow.runs or []):
        if run.workflow_stage:
            stage_runs[run.workflow_stage] = run

    stages = []
    for stage in workflow_service.STAGES:
        run = stage_runs.get(stage)
        if run is not None:
            stage_status = run.status  # completed/running/failed
        elif stage == workflow.current_stage and workflow.status == "in_progress":
            stage_status = "pending"
        elif workflow_service.STAGES.index(stage) < workflow_service.STAGES.index(workflow.current_stage) if workflow.current_stage in workflow_service.STAGES else False:
            stage_status = "pending"
        else:
            stage_status = "pending"

        stages.append(WorkflowStageResponse(
            stage=stage,
            label=workflow_service.STAGE_LABELS[stage],
            status=stage_status,
            run_id=str(run.id) if run is not None else None,
        ))
    return stages


def _build_run_responses(workflow) -> list[WorkflowRunResponse]:
    items = []
    for run in sorted(workflow.runs or [], key=lambda r: r.created_at):
        best_confidence = None
        for step in (run.steps or []):
            if step.confidence_score is not None:
                if best_confidence is None or step.confidence_score > best_confidence:
                    best_confidence = step.confidence_score

        items.append(WorkflowRunResponse(
            run_id=str(run.id),
            goal=run.goal,
            status=run.status,
            workflow_stage=run.workflow_stage,
            confidence_score=best_confidence,
            started_at=_iso(run.started_at),
            completed_at=_iso(run.completed_at),
            created_at=_iso(run.created_at),
        ))
    return items


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=202, response_model=ContinueWorkflowResponse)
async def create_workflow(
    body: CreateWorkflowRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ContinueWorkflowResponse:
    """Create a new SDLC workflow and execute the first (planning) stage."""
    from app.orchestrator.run_coordinator import RunCoordinator

    workflow = await workflow_service.create_workflow(db, title=body.title)

    # Execute planning stage
    stage = "planning"
    goal = workflow_service.STAGE_GOALS[stage]
    subject = resolve_freetext(body.title)

    selector = AgentSelector(global_registry)
    coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)

    try:
        run = await coordinator.execute(subject=subject, goal=goal, model=body.model)
    except NotFoundError:
        raise
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            f"Workflow stage execution failed: {exc}",
            status_code=500,
            error_code="workflow_execution_error",
        ) from exc

    # Link run to workflow
    run.workflow_id = workflow.id
    run.workflow_stage = stage
    await workflow_service.advance_workflow(db, workflow, run)
    await db.commit()

    return ContinueWorkflowResponse(
        workflow_id=str(workflow.id),
        run_id=str(run.id),
        stage=stage,
        status=run.status,
    )


@router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: str | None = None,
) -> WorkflowListResponse:
    """List workflows with pagination."""
    workflows, total = await workflow_service.list_workflows(
        db, status=status, page=page, page_size=page_size
    )

    items = [
        WorkflowListItem(
            workflow_id=str(w.id),
            title=w.title,
            current_stage=w.current_stage,
            status=w.status,
            stages=_build_stages(w),
            created_at=_iso(w.created_at),
            updated_at=_iso(w.updated_at),
        )
        for w in workflows
    ]

    return WorkflowListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=(page * page_size) < total,
    )


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)
async def get_workflow(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> WorkflowDetailResponse:
    """Get full workflow details with all stages and runs."""
    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow(db, wid)

    return WorkflowDetailResponse(
        workflow_id=str(workflow.id),
        title=workflow.title,
        current_stage=workflow.current_stage,
        status=workflow.status,
        stages=_build_stages(workflow),
        runs=_build_run_responses(workflow),
        created_at=_iso(workflow.created_at),
        updated_at=_iso(workflow.updated_at),
    )


@router.post("/{workflow_id}/continue", status_code=202, response_model=ContinueWorkflowResponse)
async def continue_workflow(
    workflow_id: str,
    body: ContinueWorkflowRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ContinueWorkflowResponse:
    """Continue a workflow to its next SDLC stage.

    Automatically propagates context from all previous stages to the
    next agent — no manual copy-paste required.
    """
    from app.orchestrator.run_coordinator import RunCoordinator

    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow(db, wid)

    if workflow.status == "completed":
        raise AppError(
            "Workflow is already completed.",
            status_code=400,
            error_code="workflow_completed",
        )

    target_stage = workflow.current_stage
    if target_stage not in workflow_service.STAGE_GOALS:
        raise AppError(
            f"No next stage available (current: {workflow.current_stage}).",
            status_code=400,
            error_code="no_next_stage",
        )

    # Check this stage hasn't already been run
    for existing_run in workflow.runs:
        if existing_run.workflow_stage == target_stage and existing_run.status in ("queued", "running", "completed"):
            raise AppError(
                f"Stage '{target_stage}' already has a run.",
                status_code=400,
                error_code="stage_already_run",
            )

    # Find the previous run in this workflow
    previous_run = None
    for run in sorted(workflow.runs, key=lambda r: r.created_at, reverse=True):
        if run.status == "completed":
            previous_run = run
            break

    # Build enriched context from previous stages
    goal = workflow_service.STAGE_GOALS[target_stage]
    enriched_ref = workflow_service.build_stage_context(
        workflow,
        original_request=workflow.title,
        target_stage=target_stage,
    )
    subject = resolve_freetext(enriched_ref)

    selector = AgentSelector(global_registry)
    coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)

    try:
        run = await coordinator.execute(subject=subject, goal=goal, model=body.model)
    except NotFoundError:
        raise
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            f"Workflow stage execution failed: {exc}",
            status_code=500,
            error_code="workflow_execution_error",
        ) from exc

    # Link run to workflow and previous run
    run.workflow_id = workflow.id
    run.workflow_stage = target_stage
    if previous_run:
        run.previous_run_id = previous_run.id

    await workflow_service.advance_workflow(db, workflow, run)
    await db.commit()

    return ContinueWorkflowResponse(
        workflow_id=str(workflow.id),
        run_id=str(run.id),
        stage=target_stage,
        status=run.status,
    )
