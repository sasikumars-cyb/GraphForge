"""Agent Runs API — POST to create a run, GET to list/retrieve.

Matches API_CONTRACTS.md: POST /api/v1/agent-runs → 202 Accepted,
GET /api/v1/agent-runs/{run_id}, GET /api/v1/agent-runs (paginated).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.agents.llm import default_stage_for_agent
from app.agents.title_generation import fallback_title
from app.ai.config.resolver import resolve
from app.api.v1.dependencies import get_current_user, require_admin
from app.context.resolvers.freetext import resolve as resolve_freetext
from app.context.resolvers.github import GITHUB_PR_URL_RE as _GITHUB_PR_URL_RE
from app.context.resolvers.github import (
    resolve_pull_request_url as _resolve_pull_request_url_subject,
)
from app.context.resolvers.github import resolve_repository_id as _resolve_repository_subject
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError
from app.core.request_context import set_workflow_context
from app.database.session import get_db_session
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.models.user import User
from app.models.workflow import Workflow
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import GOAL_DEVELOP_CHANGE_PLAN, GOAL_PLAN_TESTS, AgentSelector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


# ---------------------------------------------------------------------------
# Request / Response DTOs
# ---------------------------------------------------------------------------


# Only these two goals ever read `context.extras["workflow"]` via
# get_stage_result(workflow, "planning") — see development/agent.py and
# testing/agent.py. Every other goal (including "plan_freeform" itself,
# and "plan_documentation"/"review_readiness", which read *multiple*
# prior stages and would only ever see this one) would silently ignore
# or misuse a `planning_run_id`, so it's rejected outright rather than
# accepted-and-ignored — see `_load_standalone_planning_context`.
_PLANNING_CONTEXT_SUPPORTED_GOALS = frozenset({GOAL_DEVELOP_CHANGE_PLAN, GOAL_PLAN_TESTS})


class CreateRunRequest(BaseModel):
    subject_reference: str = Field(..., min_length=1, max_length=512)
    goal: str = Field(..., min_length=1, max_length=128)
    model: str | None = None
    planning_run_id: str | None = Field(
        default=None,
        description=(
            "Ground a standalone run in a prior completed Planning run's "
            "result — the same context a Workflow's Development/Testing "
            "stage would read from the Planning stage before it. Optional; "
            "omitting it preserves standalone execution exactly as before. "
            f"Only valid when `goal` is one of: {sorted(_PLANNING_CONTEXT_SUPPORTED_GOALS)} "
            "— every other goal either doesn't consume Planning context "
            "(e.g. `plan_freeform`) or consumes more than just Planning "
            "(e.g. `plan_documentation`, `review_readiness`), and rejects "
            "this field with a 400 rather than silently ignoring it. Must "
            "reference a run with `goal=plan_freeform` and `status=completed` "
            "that the caller owns."
        ),
    )


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
    # What actually happened, distinct from `kind` (see Evidence.status on the
    # agent contract): success / not_found / unavailable / failed. Was omitted
    # here, so a UI could only tell a failed call from a successful one by
    # sniffing the "FAILED: " summary prefix. None for older evidence written
    # before the field existed.
    status: str | None = None


class ConfidenceResponse(BaseModel):
    score: float | None
    reasoning: str


class PreflightWarningResponse(BaseModel):
    """ADR 0011, OD-1 — a WARNING-severity pre-flight result, persisted on
    the step distinct from `evidence` (see StepResponse.human_override's
    own docstring for the analogous provenance distinction)."""

    code: str
    dependency: str
    message: str
    checked_at: str


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
    # A human's correction to this step's result, if any — the partial dict
    # `get_stage_result()` merges over `result` for downstream stages.
    #
    # `result` above stays the AI's own unedited output (that is what confidence
    # calibration checks against), so without surfacing this the UI showed the
    # original text back after a save and the correction looked silently lost.
    # Exposing both lets the UI show the corrected value *and* label it as a
    # human edit rather than as something the agent concluded.
    human_override: dict[str, object] | None = None
    overridden_at: str | None = None
    preflight_warnings: list[PreflightWarningResponse] = []


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
    enabled: bool


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


def _run_ownership_clause(user_id: uuid.UUID) -> ColumnElement[bool]:
    """SQLAlchemy WHERE clause matching a Run this user may access.

    A Run is owned via one of two paths, since standalone runs and
    workflow-stage runs record "who triggered this" differently (see
    Run.user_id's docstring): a standalone run sets `Run.user_id` directly
    at creation (see create_run below); a workflow-stage run never does —
    ownership instead follows the parent Workflow's own `user_id` (see
    Workflow.user_id). A run/workflow with no recorded owner at all (rows
    predating either column) stays visible to any authenticated user
    rather than becoming permanently inaccessible — same rule
    `workflow_service._check_workflow_owned` applies.

    Callers must `outerjoin(Workflow, Run.workflow_id == Workflow.id)`
    before applying this clause, since it references `Workflow.user_id`.
    """
    return or_(
        Run.user_id == user_id,
        and_(Run.workflow_id.is_(None), Run.user_id.is_(None)),
        Workflow.user_id == user_id,
        and_(Run.workflow_id.isnot(None), Workflow.user_id.is_(None)),
    )


async def _get_owned_run(db: AsyncSession, run_id: uuid.UUID, user_id: uuid.UUID) -> Run:
    """Fetch a single Run (with steps eagerly loaded), enforcing the same
    ownership rule as `_run_ownership_clause` — NotFoundError, not
    Forbidden, so this never confirms that a run owned by someone else
    exists."""
    result = await db.execute(
        select(Run)
        .options(selectinload(Run.steps))
        .outerjoin(Workflow, Run.workflow_id == Workflow.id)
        .where(Run.id == run_id, _run_ownership_clause(user_id))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"Run '{run_id}' not found.")
    return run


class _StandalonePlanningRun:
    """Just enough of `Run`'s shape for `get_stage_result()` to treat this
    as a completed "planning"-stage run — a standalone run's real
    `workflow_stage` column is always None (it was never part of a
    Workflow), so that one field is overridden; everything else
    (`status`, `created_at`, `steps`) proxies straight through to the
    real, already-loaded `Run` row. Deliberately not a change to
    `get_stage_result()` or any agent — it's the caller (this router)
    that adapts its input to the existing contract, not the reverse.
    """

    def __init__(self, run: Run) -> None:
        self._run = run
        self.workflow_stage = "planning"

    def __getattr__(self, name: str) -> object:
        return getattr(self._run, name)


class _StandalonePlanningContext:
    """Duck-types the one attribute of `Workflow` that `get_stage_result()`
    reads (`.runs`) — used only when a caller explicitly supplies
    `planning_run_id`; nothing else in `RunCoordinator`, `Workflow`, or
    the agents needs to know this isn't a real workflow."""

    def __init__(self, planning_run: Run) -> None:
        self.runs = [_StandalonePlanningRun(planning_run)]


async def _load_standalone_planning_context(
    db: AsyncSession, user_id: uuid.UUID, goal: str, planning_run_id: str
) -> _StandalonePlanningContext:
    """Validate and wrap a user-supplied `planning_run_id` for Phase 3's
    "ground a standalone Development/Testing run in a prior Planning
    run" capability. Raises AppError/NotFoundError with a clear message
    on anything invalid — never silently ignored, so a stale or
    mistyped id (or a goal this field doesn't apply to) is obvious to
    the caller rather than quietly producing an ungrounded or
    partially-grounded result."""
    if goal not in _PLANNING_CONTEXT_SUPPORTED_GOALS:
        raise AppError(
            f"planning_run_id is not supported for goal '{goal}'. Supported goals: "
            f"{sorted(_PLANNING_CONTEXT_SUPPORTED_GOALS)}.",
            status_code=400,
            error_code="planning_run_id_unsupported_goal",
        )
    try:
        run_uuid = uuid.UUID(planning_run_id)
    except ValueError as exc:
        raise AppError(
            f"Invalid planning_run_id: {planning_run_id}",
            status_code=400,
            error_code="invalid_planning_run_id",
        ) from exc

    run = await _get_owned_run(db, run_uuid, user_id)
    if run.goal != "plan_freeform" or run.status != "completed":
        raise AppError(
            f"Run '{planning_run_id}' is not a completed Planning run "
            f"(goal={run.goal}, status={run.status}).",
            status_code=400,
            error_code="invalid_planning_run_reference",
        )
    return _StandalonePlanningContext(run)


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
            status=e.get("status"),
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
        human_override=getattr(step, "human_override", None),
        overridden_at=_iso(getattr(step, "overridden_at", None)),
        preflight_warnings=[
            PreflightWarningResponse(
                code=w.get("code", ""),
                dependency=w.get("dependency", ""),
                message=w.get("message", ""),
                checked_at=w.get("checked_at", ""),
            )
            for w in (step.preflight_warnings or [])
        ],
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
            enabled=global_registry.is_enabled(m.agent_id),
        )
        for m in global_registry.all_manifests()
    ]


@router.post("/agents/{agent_id}/disable", status_code=204)
async def disable_agent(
    agent_id: str,
    _: User = Depends(require_admin),
) -> None:
    """Runtime kill switch — stop new runs for this agent immediately,
    without a deploy. Any run already in progress finishes normally."""
    if global_registry.get(agent_id) is None:
        raise NotFoundError(f"Agent '{agent_id}' is not registered.")
    global_registry.disable(agent_id)


@router.post("/agents/{agent_id}/enable", status_code=204)
async def enable_agent(
    agent_id: str,
    _: User = Depends(require_admin),
) -> None:
    if global_registry.get(agent_id) is None:
        raise NotFoundError(f"Agent '{agent_id}' is not registered.")
    global_registry.enable(agent_id)


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
    from app.orchestrator.background_execution import (
        schedule_run_execution,
        schedule_title_generation,
    )
    from app.orchestrator.run_coordinator import RunCoordinator

    # Resolve subject
    ref = body.subject_reference.strip()
    if ref.startswith("repo:"):
        # Repository-scoped goals (currently: review_documentation) — the
        # only subject_reference form that needs a DB lookup rather than
        # freetext's pure, I/O-free resolution. Scoped to this user exactly
        # like every other repository read (see GetIndexedRepositoriesTool's
        # own docstring on why an unscoped read here would be cross-tenant).
        subject = await _resolve_repository_subject(db, user.id, ref.removeprefix("repo:"))
    elif _GITHUB_PR_URL_RE.match(ref):
        subject = await _resolve_pull_request_url_subject(db, user.id, ref)
    elif ref.startswith("freetext:") or not ref.startswith(("pr:", "jira:", "http")):
        subject = resolve_freetext(
            ref.removeprefix("freetext:") if ref.startswith("freetext:") else ref
        )
    else:
        # Future: resolve Jira keys, other VCS PR/MR URLs, etc.
        subject = resolve_freetext(ref)

    # Validated before create_pending_run so an invalid reference never
    # leaves a queued Run row behind — see _load_standalone_planning_context.
    standalone_planning_context = (
        await _load_standalone_planning_context(db, user.id, body.goal, body.planning_run_id)
        if body.planning_run_id
        else None
    )

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

    # Instant, deterministic placeholder — never blocks run creation on an
    # LLM round-trip; the real AI title is generated by a background task
    # once this run is committed (see schedule_title_generation below).
    run.title = fallback_title(subject.display_name)
    # Record the provider that will actually serve this run, resolved through
    # the AI configuration layer under the stage this agent runs as — not the
    # raw `AI_PROVIDER` env var, which ignored every stage override, AI
    # Profile, and stored default and so displayed the wrong vendor in Run
    # History whenever any of those were configured. Falls back to the env
    # value only if resolution fails, so a misconfiguration still records
    # something rather than nothing.
    try:
        run.provider = resolve(model=body.model, stage=default_stage_for_agent(agent_id)).key
    except Exception:  # noqa: BLE001 — display metadata must never fail a run
        logger.warning("run_provider_resolution_failed run_id=%s", str(run.id), exc_info=True)
        run.provider = get_settings().ai_provider
    run.user_id = user.id
    await db.commit()

    # Bound before the background task is created, not inside it: a task
    # copies its creator's contextvars context at creation time, so this
    # is what makes the run's own logs carry the same correlation id.
    set_workflow_context(workflow_run_id=str(run.id))

    await schedule_run_execution(
        db=db,
        run_id=run.id,
        subject=subject,
        goal=body.goal,
        model=body.model,
        # `context.extras["workflow"]` is exactly what Development/Testing's
        # existing (unmodified) `get_stage_result(workflow, "planning")` call
        # already reads — this only supplies that key when the caller asked
        # for it, so every existing caller (extras=None) is byte-for-byte
        # unaffected.
        extras={"workflow": standalone_planning_context} if standalone_planning_context else None,
        agent_id=agent_id,
        registry=global_registry,
    )
    schedule_title_generation(run.id, subject.display_name, body.model, model_cls=Run)

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

    run = await _get_owned_run(db, rid, user.id)

    if run.status in ("completed", "failed"):
        return CancelRunResponse(run_id=str(run.id), status=run.status)

    if cancel_background_run(rid):
        run.status = "failed"
        run.error_message = "Cancelled by user."
        run.completed_at = datetime.now(UTC)
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

    run = await _get_owned_run(db, rid, user.id)

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

    run = await _get_owned_run(db, rid, user.id)

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
    query = (
        select(Run)
        .outerjoin(Workflow, Run.workflow_id == Workflow.id)
        .where(_run_ownership_clause(user.id))
    )
    count_query = (
        select(func.count(Run.id))
        .select_from(Run)
        .outerjoin(Workflow, Run.workflow_id == Workflow.id)
        .where(_run_ownership_clause(user.id))
    )

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
            if step.confidence_score is not None and (
                best_confidence is None or step.confidence_score > best_confidence
            ):
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
