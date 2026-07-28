"""Workflow API — SDLC workflow lifecycle management.

POST /workflows                    → Create a workflow and run the first stage
GET  /workflows                    → List workflows (paginated)
GET  /workflows/{workflow_id}      → Get workflow with all stages and runs
POST /workflows/{workflow_id}/continue → Continue to the next stage
POST /workflows/{workflow_id}/approve  → Approve a completed Planning blueprint
POST /workflows/{workflow_id}/reject   → Reject a completed Planning blueprint
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.agents._contract import Subject
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.review_adapter import resolve_pr_subject
from app.api.v1.dependencies import get_current_user
from app.context.resolvers.freetext import resolve as resolve_freetext
from app.core.exceptions import AppError, NotFoundError
from app.core.rate_limit import check_rate_limit
from app.database.session import get_db_session
from app.models.run import Run
from app.models.user import User
from app.models.workflow import Workflow
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import AgentSelector
from app.services import workflow_service

if TYPE_CHECKING:
    from app.orchestrator.background_execution import OnComplete

router = APIRouter(prefix="/workflows", tags=["workflows"])

# Every stage start is a real (and, for paid providers, billed) LLM call —
# see app.core.rate_limit's docstring. 10 stage-starts per user per 5
# minutes comfortably covers legitimate iteration (start, retry a failed
# stage, edit and restart) while blocking accidental or scripted spam.
_STAGE_START_RATE_LIMIT = 10
_STAGE_START_RATE_WINDOW_SECONDS = 300.0


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class CreateWorkflowRequest(BaseModel):
    # 8000 chars comfortably fits a full multi-paragraph engineering brief
    # (NewWorkflowPage's textarea invites exactly that) while still
    # bounding request size; the DB column itself is unbounded TEXT.
    title: str = Field(..., min_length=1, max_length=8000)
    model: str | None = None
    # "planning" is the only type NewWorkflowPage lets a user create today —
    # also the module-level default in workflow_service.create_workflow().
    workflow_type: str = "planning"
    # Required for auto_execution — references the approved Planning blueprint.
    source_workflow_id: str | None = None
    # "Refine" — references the workflow this one refines. Distinct from
    # source_workflow_id (auto_execution's link to an approved blueprint):
    # this is a version chain within Planning itself. The refined
    # workflow's completed stage(s) are carried forward as context, same
    # mechanism as source_workflow_id, plus refinement_note below.
    parent_workflow_id: str | None = None
    # The human's own note on what to change in this refinement — threaded
    # into the new workflow's planning prompt so the next draft actually
    # responds to it instead of just regenerating cold from the same title.
    refinement_note: str | None = Field(default=None, max_length=4000)


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
    # The complete, unmodified objective the user submitted — `title` is
    # the AI-generated short version of this. Only on the detail response
    # (not the list item): list rows show the short title, this is for a
    # single workflow's "view original prompt" disclosure.
    original_prompt: str
    workflow_type: str
    current_stage: str
    status: str
    stages: list[WorkflowStageResponse]
    runs: list[WorkflowRunResponse]
    created_at: str
    updated_at: str
    # Resolved display name (not the raw user id) — None if never approved,
    # or approved before this field existed.
    approved_by: str | None = None
    # Version lineage — version=1/parent=None for anything not created via
    # Refine. parent_workflow_id lets the UI link back to what this refines.
    version: int = 1
    parent_workflow_id: str | None = None
    refinement_note: str | None = None


class WorkflowListItem(BaseModel):
    workflow_id: str
    title: str
    workflow_type: str
    current_stage: str
    status: str
    stages: list[WorkflowStageResponse]
    created_at: str
    updated_at: str
    approved_by: str | None = None
    version: int = 1
    parent_workflow_id: str | None = None


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


class WorkflowApprovalResponse(BaseModel):
    workflow_id: str
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: object) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _build_stages(workflow: Workflow) -> list[WorkflowStageResponse]:
    """Build the stage list from a workflow and its runs."""
    # Map stage → run
    stage_runs: dict[str, Run] = {}
    for run in workflow.runs or []:
        if run.workflow_stage:
            stage_runs[run.workflow_stage] = run

    stages = []
    for stage in workflow_service.stage_sequence(workflow.workflow_type):
        matched_run = stage_runs.get(stage)
        # Any stage without a Run is simply "pending" — whether it's the
        # current stage awaiting its first attempt, a future stage not yet
        # reached, or (post-linkage-fix) a stage whose failed attempt was
        # correctly linked and will show up via `matched_run.status` instead.
        stage_status = matched_run.status if matched_run is not None else "pending"

        stages.append(
            WorkflowStageResponse(
                stage=stage,
                label=workflow_service.STAGE_LABELS[stage],
                status=stage_status,
                run_id=str(matched_run.id) if matched_run is not None else None,
            )
        )
    return stages


async def _resolve_approver_names(
    db: AsyncSession, workflows: list[Workflow]
) -> dict[uuid.UUID, str]:
    """Batch-lookup User.full_name for every distinct approved_by_user_id
    present in `workflows` — one query regardless of list size, matching
    this codebase's explicit-query-over-relationship style for this kind
    of cross-reference (no new ORM relationship added to Workflow/User)."""
    ids = {w.approved_by_user_id for w in workflows if w.approved_by_user_id is not None}
    if not ids:
        return {}
    result = await db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {row.id: row.full_name for row in result.all()}


async def _link_failed_run(
    db: AsyncSession,
    workflow: Workflow,
    subject_id: str,
    goal: str,
    stage: str,
) -> None:
    """Link an orphaned failed Run back to its workflow.

    Only reachable now from `RunCoordinator.create_pending_run` failures
    (goal has no registered agent, or the selector itself errors) — that
    method still creates and commits a failed Run before raising, and has
    no notion of "workflow" so never sets workflow_id/workflow_stage
    itself. Once `create_pending_run` succeeds, the router sets
    workflow_id/workflow_stage directly on the Run before it's ever
    dispatched for background execution, so a *later* failure (inside the
    agent itself, in the background task) already carries that linkage —
    this retroactive lookup is no longer needed for that case. Without
    this, a stage that fails at selection time would be invisible in the
    workflow's stage/run list: it'd look like it was never attempted, even
    though it consumed a real run.

    Important for AsyncSession: do not touch `workflow.runs` via direct
    attribute access here. In the create-workflow failure path this
    relationship is not eagerly loaded, and lazy-loading can raise
    MissingGreenlet. Link via explicit FK updates, then sync in-memory
    state only when `runs` is already loaded.
    """
    result = await db.execute(
        select(Run)
        .where(Run.subject_id == subject_id, Run.goal == goal, Run.status == "failed")
        .order_by(Run.created_at.desc())
        .limit(1)
    )
    failed_run = result.scalar_one_or_none()
    if failed_run is None:
        return
    failed_run.workflow_id = workflow.id
    failed_run.workflow_stage = stage
    await db.commit()

    # Keep already-loaded in-memory collections coherent without issuing a
    # relationship refresh query (and without triggering lazy-loading).
    workflow_state = inspect(workflow)
    if "runs" not in workflow_state.unloaded:
        loaded_runs = workflow.runs
        if failed_run not in loaded_runs:
            loaded_runs.append(failed_run)


def _resolve_stage_subject(workflow: Workflow, target_stage: str, enriched_ref: str) -> Subject:
    """Resolve the Subject a stage's agent should run against.

    Every stage but one resolves via the freetext chain built by
    `build_stage_context` — that's the mechanism every existing agent
    (Planning, Development, Testing, Engineering Review, and all of
    Auto Execution's deterministic stages) already expects.

    ai_pr_review is the one exception: it reuses the existing, untouched
    ReviewAgentAdapter, which requires subject_type="pull_request" /
    subject_id="pr:<uuid>" (see `review_adapter._extract_pr_uuid`). That
    uuid is the `pull_requests.id` the create_pull_request stage just
    persisted, read back the same structured way every other execution
    agent reads its prior stage's output — not parsed out of freetext.
    """
    if target_stage != "ai_pr_review":
        return resolve_freetext(enriched_ref)

    pr_result = get_stage_result(workflow, "create_pull_request")
    if not pr_result or not pr_result.get("pull_request_id"):
        raise AppError(
            "No completed create_pull_request result found in workflow.",
            status_code=400,
            error_code="missing_pull_request",
        )
    return resolve_pr_subject(
        uuid.UUID(pr_result["pull_request_id"]),
        display_name=pr_result.get("title", workflow.title),
    )


def _workflow_stage_finalizer(workflow_id: uuid.UUID) -> OnComplete:
    """Build the on_complete callback passed to schedule_run_execution.

    Runs in the background task's own DB session, after the stage's run
    reaches a terminal status — this is where `workflow_service.
    finalize_stage_run` (advance current_stage/status, or no-op on
    failure) now happens, since the router itself returns before the run
    finishes.
    """

    async def _finalize(db: AsyncSession, run: Run) -> None:
        await workflow_service.finalize_stage_run(db, workflow_id, run)

    return _finalize


def _build_run_responses(workflow: Workflow) -> list[WorkflowRunResponse]:
    items = []
    for run in sorted(workflow.runs or [], key=lambda r: r.created_at):
        best_confidence = None
        for step in run.steps or []:
            if step.confidence_score is not None and (
                best_confidence is None or step.confidence_score > best_confidence
            ):
                best_confidence = step.confidence_score

        items.append(
            WorkflowRunResponse(
                run_id=str(run.id),
                goal=run.goal,
                status=run.status,
                workflow_stage=run.workflow_stage,
                confidence_score=best_confidence,
                started_at=_iso(run.started_at),
                completed_at=_iso(run.completed_at),
                created_at=_iso(run.created_at),
            )
        )
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
    """Create a new workflow and schedule its first stage's execution.

    Commits the workflow and a "queued" first-stage Run before returning
    — the stage's agent runs detached from this request (see
    app.orchestrator.background_execution), so it survives the client
    disconnecting, navigating away, or refreshing. Poll GET
    /workflows/{workflow_id} for progress.
    """
    from app.orchestrator.background_execution import schedule_run_execution
    from app.orchestrator.run_coordinator import RunCoordinator

    check_rate_limit(
        f"stage_start:{user.id}",
        max_requests=_STAGE_START_RATE_LIMIT,
        window_seconds=_STAGE_START_RATE_WINDOW_SECONDS,
    )

    # Parse source_workflow_id / parent_workflow_id if provided
    source_wf_id: uuid.UUID | None = None
    if body.source_workflow_id:
        try:
            source_wf_id = uuid.UUID(body.source_workflow_id)
        except ValueError as exc:
            raise AppError(
                f"Invalid source_workflow_id: {body.source_workflow_id}",
                status_code=400,
                error_code="invalid_source_workflow_id",
            ) from exc

    parent_wf_id: uuid.UUID | None = None
    if body.parent_workflow_id:
        try:
            parent_wf_id = uuid.UUID(body.parent_workflow_id)
        except ValueError as exc:
            raise AppError(
                f"Invalid parent_workflow_id: {body.parent_workflow_id}",
                status_code=400,
                error_code="invalid_parent_workflow_id",
            ) from exc

    workflow = await workflow_service.create_workflow(
        db,
        title=body.title,
        workflow_type=body.workflow_type,
        source_workflow_id=source_wf_id,
        parent_workflow_id=parent_wf_id,
        refinement_note=body.refinement_note,
        user_id=user.id,
    )

    # Execute the first stage of this workflow_type's sequence (today,
    # "planning" either way — legacy_sdlc and planning both start there).
    stage = workflow_service.stage_sequence(body.workflow_type)[0]
    goal = workflow_service.STAGE_GOALS[stage]

    # For auto_execution, enrich the first-stage context with the source
    # blueprint's outputs. For a Refine (parent_workflow_id), same
    # mechanism — the parent's completed stage(s) become "source" context
    # — plus the human's own refinement note appended as its own fenced
    # block. For a plain "New Workflow" (neither), the title suffices.
    if workflow.source_workflow_id:
        source_workflow = await workflow_service.get_workflow(db, workflow.source_workflow_id)
        # A freshly created workflow genuinely has zero runs yet — set the
        # relationship's already-known value directly rather than
        # `workflow.runs = []`, which fires SQLAlchemy's "fetch the old
        # collection to diff against" event and triggers a real lazy-load
        # query outside an awaited context (MissingGreenlet under asyncio;
        # this is exactly the hazard _link_failed_run's `inspect(...)
        # .unloaded` check above guards against, just hit from the write
        # side instead of the read side).
        set_committed_value(workflow, "runs", [])
        enriched_ref = workflow_service.build_stage_context(
            workflow,
            original_request=body.title,
            target_stage=stage,
            source_workflow=source_workflow,
        )
        subject = resolve_freetext(enriched_ref)
    elif workflow.parent_workflow_id:
        parent_workflow = await workflow_service.get_workflow(db, workflow.parent_workflow_id)
        # A freshly created workflow genuinely has zero runs yet — set the
        # relationship's already-known value directly rather than
        # `workflow.runs = []`, which fires SQLAlchemy's "fetch the old
        # collection to diff against" event and triggers a real lazy-load
        # query outside an awaited context (MissingGreenlet under asyncio;
        # this is exactly the hazard _link_failed_run's `inspect(...)
        # .unloaded` check above guards against, just hit from the write
        # side instead of the read side).
        set_committed_value(workflow, "runs", [])
        enriched_ref = workflow_service.build_stage_context(
            workflow,
            original_request=body.title,
            target_stage=stage,
            source_workflow=parent_workflow,
        )
        if body.refinement_note:
            enriched_ref += (
                "\n\n--- HUMAN REFINEMENT FEEDBACK (address this in the next draft) ---\n"
                f"{body.refinement_note}\n--- END FEEDBACK ---"
            )
        subject = resolve_freetext(enriched_ref)
    else:
        subject = resolve_freetext(body.title)

    selector = AgentSelector(global_registry)
    coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)

    try:
        run, agent_id, _agent = await coordinator.create_pending_run(
            subject=subject, goal=goal, model=body.model
        )
    except NotFoundError:
        await _link_failed_run(db, workflow, subject.subject_id, goal, stage)
        raise
    except AppError:
        await _link_failed_run(db, workflow, subject.subject_id, goal, stage)
        raise
    except Exception as exc:
        await _link_failed_run(db, workflow, subject.subject_id, goal, stage)
        raise AppError(
            f"Workflow stage execution failed: {exc}",
            status_code=500,
            error_code="workflow_execution_error",
        ) from exc

    # Link run to workflow before it's ever handed to the background task,
    # so a failure there already carries workflow_id/workflow_stage —
    # see _link_failed_run's updated docstring.
    run.workflow_id = workflow.id
    run.workflow_stage = stage
    await db.commit()

    schedule_run_execution(
        run_id=run.id,
        subject=subject,
        goal=goal,
        model=body.model,
        extras={"workflow": workflow, "user_id": user.id},
        agent_id=agent_id,
        registry=global_registry,
        on_complete=_workflow_stage_finalizer(workflow.id),
    )

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
    workflow_type: str | None = None,
) -> WorkflowListResponse:
    """List workflows with pagination."""
    workflows, total = await workflow_service.list_workflows(
        db,
        status=status,
        workflow_type=workflow_type,
        page=page,
        page_size=page_size,
        user_id=user.id,
    )
    approver_names = await _resolve_approver_names(db, workflows)

    items = [
        WorkflowListItem(
            workflow_id=str(w.id),
            title=w.title,
            workflow_type=w.workflow_type,
            current_stage=w.current_stage,
            status=w.status,
            stages=_build_stages(w),
            created_at=_iso(w.created_at),
            updated_at=_iso(w.updated_at),
            approved_by=(
                approver_names.get(w.approved_by_user_id) if w.approved_by_user_id else None
            ),
            version=w.version,
            parent_workflow_id=str(w.parent_workflow_id) if w.parent_workflow_id else None,
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

    workflow = await workflow_service.get_workflow(db, wid, user_id=user.id)
    approver_names = await _resolve_approver_names(db, [workflow])

    return WorkflowDetailResponse(
        workflow_id=str(workflow.id),
        title=workflow.title,
        original_prompt=workflow.original_prompt,
        workflow_type=workflow.workflow_type,
        current_stage=workflow.current_stage,
        status=workflow.status,
        stages=_build_stages(workflow),
        runs=_build_run_responses(workflow),
        created_at=_iso(workflow.created_at),
        updated_at=_iso(workflow.updated_at),
        approved_by=(
            approver_names.get(workflow.approved_by_user_id)
            if workflow.approved_by_user_id
            else None
        ),
        version=workflow.version,
        parent_workflow_id=(
            str(workflow.parent_workflow_id) if workflow.parent_workflow_id else None
        ),
        refinement_note=workflow.refinement_note,
    )


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a workflow along with every stage run it created.

    `Run.workflow_id` is ON DELETE SET NULL, so deleting the Workflow row
    alone would just orphan its runs as standalone entries instead of
    removing them — not what "delete this workflow" means. Each Run is
    deleted explicitly first (cascading its steps via the same
    `delete-orphan` relationship DELETE /agent-runs/{id} uses), cancelling
    any still executing, same as that endpoint.
    """
    from app.orchestrator.background_execution import cancel_run as cancel_background_run

    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await db.get(Workflow, wid)
    if workflow is None:
        raise NotFoundError(f"Workflow '{workflow_id}' not found.")
    if workflow.user_id is not None and workflow.user_id != user.id:
        raise NotFoundError(f"Workflow '{workflow_id}' not found.")

    result = await db.execute(select(Run).where(Run.workflow_id == wid))
    runs = result.scalars().all()
    for run in runs:
        if run.status in ("queued", "running"):
            cancel_background_run(run.id)
        await db.delete(run)

    await db.delete(workflow)
    await db.commit()


@router.post("/{workflow_id}/continue", status_code=202, response_model=ContinueWorkflowResponse)
async def continue_workflow(
    workflow_id: str,
    body: ContinueWorkflowRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ContinueWorkflowResponse:
    """Continue a workflow to its next SDLC stage.

    Automatically propagates context from all previous stages to the
    next agent — no manual copy-paste required. Commits a "queued" Run
    for the next stage and returns immediately; the stage's agent runs
    detached from this request (see app.orchestrator.background_execution),
    so it survives the client disconnecting, navigating away, or
    refreshing. Poll GET /workflows/{workflow_id} for progress.
    """
    from app.orchestrator.background_execution import schedule_run_execution
    from app.orchestrator.run_coordinator import RunCoordinator

    check_rate_limit(
        f"stage_start:{user.id}",
        max_requests=_STAGE_START_RATE_LIMIT,
        window_seconds=_STAGE_START_RATE_WINDOW_SECONDS,
    )

    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow_for_update(db, wid, user_id=user.id)

    if workflow.status in ("completed", "awaiting_approval", "approved", "rejected"):
        raise AppError(
            f"Workflow is {workflow.status.replace('_', ' ')} — no further stages can run.",
            status_code=400,
            error_code="workflow_terminal",
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
        if existing_run.workflow_stage == target_stage and existing_run.status in (
            "queued",
            "running",
            "completed",
        ):
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

    # Load source workflow for cross-workflow context (auto_execution)
    source_workflow = None
    if workflow.source_workflow_id:
        source_workflow = await workflow_service.get_workflow(db, workflow.source_workflow_id)

    enriched_ref = workflow_service.build_stage_context(
        workflow,
        original_request=workflow.title,
        target_stage=target_stage,
        source_workflow=source_workflow,
    )
    subject = _resolve_stage_subject(workflow, target_stage, enriched_ref)

    selector = AgentSelector(global_registry)
    coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)

    try:
        run, agent_id, _agent = await coordinator.create_pending_run(
            subject=subject, goal=goal, model=body.model
        )
    except NotFoundError:
        await _link_failed_run(db, workflow, subject.subject_id, goal, target_stage)
        raise
    except AppError:
        await _link_failed_run(db, workflow, subject.subject_id, goal, target_stage)
        raise
    except Exception as exc:
        await _link_failed_run(db, workflow, subject.subject_id, goal, target_stage)
        raise AppError(
            f"Workflow stage execution failed: {exc}",
            status_code=500,
            error_code="workflow_execution_error",
        ) from exc

    # Link run to workflow and previous run before it's ever handed to the
    # background task — see _link_failed_run's updated docstring.
    run.workflow_id = workflow.id
    run.workflow_stage = target_stage
    if previous_run:
        run.previous_run_id = previous_run.id
    await db.commit()

    schedule_run_execution(
        run_id=run.id,
        subject=subject,
        goal=goal,
        model=body.model,
        extras={"workflow": workflow, "user_id": user.id},
        agent_id=agent_id,
        registry=global_registry,
        on_complete=_workflow_stage_finalizer(workflow.id),
    )

    return ContinueWorkflowResponse(
        workflow_id=str(workflow.id),
        run_id=str(run.id),
        stage=target_stage,
        status=run.status,
    )


@router.post("/{workflow_id}/cancel", response_model=WorkflowApprovalResponse)
async def cancel_workflow_run(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> WorkflowApprovalResponse:
    """Cancel the workflow's currently in-flight stage run, if any.

    Best-effort (see background_execution.cancel_run): marks the run
    failed and leaves the workflow's current_stage where it is, so the
    user can retry the same stage via /continue.
    """
    from app.orchestrator.background_execution import cancel_run as cancel_background_run

    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow_for_update(db, wid, user_id=user.id)

    in_flight_run = next(
        (
            r
            for r in workflow.runs
            if r.workflow_stage == workflow.current_stage and r.status in ("queued", "running")
        ),
        None,
    )
    if in_flight_run is None:
        return WorkflowApprovalResponse(workflow_id=str(workflow.id), status=workflow.status)

    if cancel_background_run(in_flight_run.id):
        in_flight_run.status = "failed"
        in_flight_run.error_message = "Cancelled by user."
        in_flight_run.completed_at = datetime.now(UTC)
        await db.commit()

    return WorkflowApprovalResponse(workflow_id=str(workflow.id), status=workflow.status)


@router.post("/{workflow_id}/approve", response_model=WorkflowApprovalResponse)
async def approve_workflow(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> WorkflowApprovalResponse:
    """Human approves a completed blueprint. Only valid once the workflow
    has finished its last stage and is genuinely awaiting a decision —
    mirrors continue_workflow's own guard style."""
    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow_for_update(db, wid, user_id=user.id)
    if workflow.status != "awaiting_approval":
        raise AppError(
            f"Workflow is not awaiting approval (status: {workflow.status}).",
            status_code=400,
            error_code="workflow_not_awaiting_approval",
        )

    await workflow_service.approve_workflow(db, workflow, user.id)
    await db.commit()
    return WorkflowApprovalResponse(workflow_id=str(workflow.id), status=workflow.status)


@router.post("/{workflow_id}/reject", response_model=WorkflowApprovalResponse)
async def reject_workflow(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> WorkflowApprovalResponse:
    """Human rejects a workflow — either the initial blueprint (status
    "awaiting_approval") or, just as validly, one already past that gate and
    sitting between stages (status "in_progress"): every stage transition
    requires an explicit human approval (see ApprovalGateBanner), and Reject
    there needs to actually stop the workflow, not just hide the banner
    client-side."""
    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow_for_update(db, wid, user_id=user.id)
    if workflow.status not in ("awaiting_approval", "in_progress"):
        raise AppError(
            f"Workflow cannot be rejected in its current state (status: {workflow.status}).",
            status_code=400,
            error_code="workflow_not_rejectable",
        )

    await workflow_service.reject_workflow(db, workflow)
    await db.commit()
    return WorkflowApprovalResponse(workflow_id=str(workflow.id), status=workflow.status)
