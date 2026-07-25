"""Agent Runs API — POST to create a run, GET to list/retrieve.

Matches API_CONTRACTS.md: POST /api/v1/agent-runs → 202 Accepted,
GET /api/v1/agent-runs/{run_id}, GET /api/v1/agent-runs (paginated).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.title_generation import generate_title
from app.api.v1.dependencies import get_current_user
from app.context.resolvers.freetext import resolve as resolve_freetext
from app.core.config import get_settings
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
    title: str | None = None
    provider: str | None = None
    user: str | None = None
    repository: str | None = None
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
    title: str | None = None
    provider: str | None = None
    user: str | None = None
    repository: str | None = None
    model: str | None = None
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


class CancelRunResponse(BaseModel):
    run_id: str
    status: str


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


def _repository_from_run(run: Run) -> str | None:
    """Derive the repository(ies) consulted by a run from its steps' results.

    Not a stored column — computed from the same `repositories_consulted`
    field the freeform agents (Planning/Development/Testing) already emit,
    matching this codebase's established derived-not-stored pattern.
    """
    repos: list[str] = []
    for step in run.steps:
        for repo in (step.result or {}).get("repositories_consulted") or []:
            if repo not in repos:
                repos.append(repo)
    return ", ".join(repos) if repos else None


async def _resolve_user_names(db: AsyncSession, runs: list[Run]) -> dict[uuid.UUID, str]:
    """Batch-lookup User.full_name for every distinct user_id present in `runs`."""
    ids = {r.user_id for r in runs if r.user_id is not None}
    if not ids:
        return {}
    result = await db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {row.id: row.full_name for row in result.all()}


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
    """Create an agent run and schedule its execution in the background.

    Resolves subject_reference to a Subject, selects the agent for the
    given goal, and commits a "queued" Run row before returning. The
    actual agent execution is dispatched to run detached from this
    request (see app.orchestrator.background_execution) so it survives
    the client disconnecting, navigating away, or refreshing — poll
    GET /agent-runs/{run_id} for progress and the eventual result.
    """
    from app.orchestrator.background_execution import schedule_run_execution
    from app.orchestrator.run_coordinator import RunCoordinator

    # Resolve subject
    ref = body.subject_reference.strip()
    if ref.startswith("freetext:") or not ref.startswith(("pr:", "jira:", "http")):
        subject = resolve_freetext(
            ref.removeprefix("freetext:") if ref.startswith("freetext:") else ref
        )
    else:
        # Future: resolve PR URLs, Jira keys, etc.
        subject = resolve_freetext(ref)

    selector = AgentSelector(global_registry)
    coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)

    try:
        run, agent_id, _agent = await coordinator.create_pending_run(
            subject=subject, goal=body.goal, model=body.model
        )
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

    run.title = await generate_title(subject.display_name)
    run.provider = get_settings().ai_provider
    run.user_id = user.id
    await db.commit()

    schedule_run_execution(
        run_id=run.id,
        subject=subject,
        goal=body.goal,
        model=body.model,
        extras=None,
        agent_id=agent_id,
        registry=global_registry,
    )

    return CreateRunResponse(
        run_id=str(run.id),
        status=run.status,
        subject=_subject_from_run(run),
        goal=run.goal,
    )


@router.post("/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_run_endpoint(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CancelRunResponse:
    """Request cancellation of an in-flight run.

    Best-effort: asyncio.Task.cancel() raises CancelledError at the run's
    next await point, it doesn't stop it instantly. If the run has already
    reached a terminal status, this is a no-op that just reports it.
    """
    from app.orchestrator.background_execution import cancel_run as cancel_background_run

    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid run_id: {run_id}") from exc

    result = await db.execute(select(Run).where(Run.id == rid))
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"Run '{run_id}' not found.")

    if run.status in ("completed", "failed"):
        return CancelRunResponse(run_id=str(run.id), status=run.status)

    if cancel_background_run(rid):
        run.status = "failed"
        run.error_message = "Cancelled by user."
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()

    return CancelRunResponse(run_id=str(run.id), status=run.status)


@router.delete("/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a run and its steps.

    If the run is still executing (queued/running), it's cancelled first
    (best-effort, same as POST /{run_id}/cancel) so the background task
    doesn't keep writing to a row that's about to disappear, then deleted
    either way — the user asked to remove it, not to be blocked pending a
    separate cancel step. AgentStep rows cascade via the relationship's
    `delete-orphan` (see app.models.run.Run.steps).
    """
    from app.orchestrator.background_execution import cancel_run as cancel_background_run

    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid run_id: {run_id}") from exc

    result = await db.execute(select(Run).where(Run.id == rid))
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"Run '{run_id}' not found.")

    if run.status in ("queued", "running"):
        cancel_background_run(rid)

    await db.delete(run)
    await db.commit()


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

    result = await db.execute(select(Run).options(selectinload(Run.steps)).where(Run.id == rid))
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"Run '{run_id}' not found.")

    user_names = await _resolve_user_names(db, [run])

    return RunDetailResponse(
        run_id=str(run.id),
        goal=run.goal,
        status=run.status,
        subject=_subject_from_run(run),
        title=run.title,
        provider=run.provider,
        user=user_names.get(run.user_id) if run.user_id else None,
        repository=_repository_from_run(run),
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

    user_names = await _resolve_user_names(db, list(runs))

    items = []
    for run in runs:
        # Get the best confidence score from steps
        best_confidence = None
        for step in run.steps:
            if step.confidence_score is not None:
                if best_confidence is None or step.confidence_score > best_confidence:
                    best_confidence = step.confidence_score

        items.append(
            RunListItem(
                run_id=str(run.id),
                goal=run.goal,
                status=run.status,
                subject=_subject_from_run(run),
                title=run.title,
                provider=run.provider,
                user=user_names.get(run.user_id) if run.user_id else None,
                repository=_repository_from_run(run),
                model=run.model,
                started_at=_iso(run.started_at),
                completed_at=_iso(run.completed_at),
                created_at=_iso(run.created_at),
                confidence_score=best_confidence,
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
                workflow_stage=run.workflow_stage,
            )
        )

    return RunListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=(page * page_size) < total,
    )
