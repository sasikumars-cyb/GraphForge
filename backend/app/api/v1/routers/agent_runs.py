"""Agent Runs API — POST to create a run, GET to list/retrieve.

Matches API_CONTRACTS.md: POST /api/v1/agent-runs → 202 Accepted,
GET /api/v1/agent-runs/{run_id}, GET /api/v1/agent-runs (paginated).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.dependencies import get_current_user
from app.context.resolvers.freetext import resolve as resolve_freetext
from app.core.exceptions import AppError, NotFoundError
from app.database.session import get_db_session
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.models.user import User
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import AgentSelector

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


# ---------------------------------------------------------------------------
# Request / Response DTOs
# ---------------------------------------------------------------------------


class CreateRunRequest(BaseModel):
    subject_reference: str = Field(..., min_length=1, max_length=512)
    goal: str = Field(..., min_length=1, max_length=128)
    model: str | None = None


class SubjectResponse(BaseModel):
    subject_id: str
    subject_type: str
    display_name: str


class CreateRunResponse(BaseModel):
    run_id: str
    status: str
    subject: SubjectResponse
    goal: str


class EvidenceResponse(BaseModel):
    kind: str
    reference: str
    summary: str


class ConfidenceResponse(BaseModel):
    score: float | None
    reasoning: str


class StepResponse(BaseModel):
    step_id: str
    agent_id: str
    status: str
    confidence: ConfidenceResponse
    evidence: list[EvidenceResponse]
    result: dict[str, object]
    prompt_version: str
    output_ref: str | None
    error_message: str | None
    latency_ms: int | None
    created_at: str | None
    completed_at: str | None


class RunDetailResponse(BaseModel):
    run_id: str
    goal: str
    status: str
    subject: SubjectResponse
    model: str | None
    error_message: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    steps: list[StepResponse]
    workflow_id: str | None = None
    workflow_stage: str | None = None
    previous_run_id: str | None = None


class RunListItem(BaseModel):
    run_id: str
    goal: str
    status: str
    subject: SubjectResponse
    started_at: str | None
    completed_at: str | None
    created_at: str
    workflow_id: str | None = None
    workflow_stage: str | None = None
    confidence_score: float | None


class RunListResponse(BaseModel):
    items: list[RunListItem]
    page: int
    page_size: int
    total: int
    has_more: bool


class AgentManifestResponse(BaseModel):
    agent_id: str
    purpose: str
    goals: list[str]
    cost_class: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _subject_from_run(run: Run) -> SubjectResponse:
    return SubjectResponse(
        subject_id=run.subject_id,
        subject_type=run.subject_type,
        display_name=run.display_name,
    )


def _iso(dt: object) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _step_response(step: AgentStep) -> StepResponse:
    evidence = [
        EvidenceResponse(
            kind=e.get("kind", ""),
            reference=e.get("reference", ""),
            summary=e.get("summary", ""),
        )
        for e in (step.evidence or [])
    ]
    return StepResponse(
        step_id=str(step.id),
        agent_id=step.agent_id,
        status=step.status,
        confidence=ConfidenceResponse(
            score=step.confidence_score,
            reasoning=step.confidence_reasoning or "",
        ),
        evidence=evidence,
        result=step.result or {},
        prompt_version=step.prompt_version,
        output_ref=step.output_ref,
        error_message=step.error_message,
        latency_ms=step.latency_ms,
        created_at=_iso(step.created_at),
        completed_at=_iso(step.completed_at),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/agents/manifests", response_model=list[AgentManifestResponse])
async def list_agents(
    user: User = Depends(get_current_user),
) -> list[AgentManifestResponse]:
    """List all registered agent manifests."""
    return [
        AgentManifestResponse(
            agent_id=m.agent_id,
            purpose=m.purpose,
            goals=sorted(m.goals),
            cost_class=m.cost_class,
        )
        for m in global_registry.all_manifests()
    ]


@router.post("", status_code=202, response_model=CreateRunResponse)
async def create_run(
    body: CreateRunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CreateRunResponse:
    """Create and execute an agent run.

    Resolves subject_reference to a Subject, selects the agent for the
    given goal, runs it, and persists the result. Returns 202 with the
    run_id and initial status.
    """
    from app.orchestrator.run_coordinator import RunCoordinator

    # Resolve subject
    ref = body.subject_reference.strip()
    if ref.startswith("freetext:") or not ref.startswith(("pr:", "jira:", "http")):
        subject = resolve_freetext(ref.removeprefix("freetext:") if ref.startswith("freetext:") else ref)
    else:
        # Future: resolve PR URLs, Jira keys, etc.
        subject = resolve_freetext(ref)

    selector = AgentSelector(global_registry)
    coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)

    try:
        run = await coordinator.execute(subject=subject, goal=body.goal, model=body.model)
    except NotFoundError:
        raise
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            f"Agent execution failed: {exc}",
            status_code=500,
            error_code="agent_execution_error",
        ) from exc

    return CreateRunResponse(
        run_id=str(run.id),
        status=run.status,
        subject=_subject_from_run(run),
        goal=run.goal,
    )


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RunDetailResponse:
    """Get full details of a single agent run including all steps."""
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid run_id: {run_id}") from exc

    result = await db.execute(
        select(Run).options(selectinload(Run.steps)).where(Run.id == rid)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"Run '{run_id}' not found.")

    return RunDetailResponse(
        run_id=str(run.id),
        goal=run.goal,
        status=run.status,
        subject=_subject_from_run(run),
        model=run.model,
        error_message=run.error_message,
        started_at=_iso(run.started_at),
        completed_at=_iso(run.completed_at),
        created_at=_iso(run.created_at),
        steps=[_step_response(s) for s in run.steps],
        workflow_id=str(run.workflow_id) if run.workflow_id else None,
        workflow_stage=run.workflow_stage,
        previous_run_id=str(run.previous_run_id) if run.previous_run_id else None,
    )


@router.get("", response_model=RunListResponse)
async def list_runs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    goal: str | None = None,
    status: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
) -> RunListResponse:
    """List agent runs with pagination and optional filtering."""
    query = select(Run)
    count_query = select(func.count(Run.id))

    if goal:
        query = query.where(Run.goal == goal)
        count_query = count_query.where(Run.goal == goal)
    if status:
        query = query.where(Run.status == status)
        count_query = count_query.where(Run.status == status)
    if subject_type:
        query = query.where(Run.subject_type == subject_type)
        count_query = count_query.where(Run.subject_type == subject_type)
    if subject_id:
        query = query.where(Run.subject_id == subject_id)
        count_query = count_query.where(Run.subject_id == subject_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Run.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    query = query.options(selectinload(Run.steps))

    result = await db.execute(query)
    runs = result.scalars().all()

    items = []
    for run in runs:
        # Get the best confidence score from steps
        best_confidence = None
        for step in run.steps:
            if step.confidence_score is not None:
                if best_confidence is None or step.confidence_score > best_confidence:
                    best_confidence = step.confidence_score

        items.append(RunListItem(
            run_id=str(run.id),
            goal=run.goal,
            status=run.status,
            subject=_subject_from_run(run),
            started_at=_iso(run.started_at),
            completed_at=_iso(run.completed_at),
            created_at=_iso(run.created_at),
            confidence_score=best_confidence,
            workflow_id=str(run.workflow_id) if run.workflow_id else None,
            workflow_stage=run.workflow_stage,
        ))

    return RunListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=(page * page_size) < total,
    )
