"""Workflow API — SDLC workflow lifecycle management.

POST /workflows                    → Create a workflow and run the first stage
GET  /workflows                    → List workflows (paginated)
GET  /workflows/{workflow_id}      → Get workflow with all stages and runs
POST /workflows/{workflow_id}/continue → Continue to the next stage
POST /workflows/{workflow_id}/clarify  → Answer Context Discovery's pending question
POST /workflows/{workflow_id}/approve  → Approve a completed Planning blueprint
POST /workflows/{workflow_id}/reject   → Reject a completed Planning blueprint
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
from app.context_pipeline.reasoning.curation import EvidencePackage
from app.context_pipeline.reasoning.understanding import EngineeringUnderstanding
from app.core.exceptions import AppError, NotFoundError
from app.core.rate_limit import check_rate_limit
from app.core.request_context import set_workflow_context
from app.database.session import get_db_session
from app.mappers.engineering_understanding_mapper import map_to_dto
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_report import WorkflowReport
from app.orchestrator.registry import global_registry
from app.orchestrator.selector import AgentSelector
from app.schemas.engineering_understanding import (
    CapabilityFactor,
    ComponentProjection,
    DebugBundleDTO,
    EngineeringUnderstandingDTO,
    ProjectionInput,
    TopicProjection,
)
from app.services import workflow_service

if TYPE_CHECKING:
    from app.orchestrator.background_execution import OnComplete

logger = logging.getLogger(__name__)

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
    # Required to advance past a stage whose persisted result reported
    # readiness="PARTIAL" (see workflow_service.STAGE_GOALS' context_discovery
    # entry and the readiness gate in continue_workflow below). Ignored for
    # any other stage transition.
    acknowledge_partial: bool = False


class ClarifyWorkflowRequest(BaseModel):
    question_id: str
    answer: str = Field(..., min_length=1, max_length=2000)


class PendingClarificationResponse(BaseModel):
    question_id: str
    question: str
    why: str
    # Real candidate values only (repository names the graph actually
    # contains). Remediation verbs are never options — see
    # reasoning.memory.ClarificationQuestion.
    options: list[str] = Field(default_factory=list)
    # What discovery already tried before resorting to asking, so the question
    # reads as a last resort rather than a first move.
    investigated: list[str] = Field(default_factory=list)


class OverrideStageResultRequest(BaseModel):
    # A partial dict — only the fields the human actually changed (e.g.
    # {"ranked_repository_names": [...]}) — merged on top of the stage's
    # own AgentStep.result at read time, never overwriting it. See
    # workflow_service.override_stage_result's docstring.
    override: dict[str, Any]
    # When True and stage is context_discovery, triggers a fresh
    # Context Discovery execution using the selected repositories as
    # explicit input — recomputing all investigation results that depend
    # on the repository. Without this, overriding repositories only
    # updates what downstream stages *read* for the repo list, but leaves
    # all evidence/areas/architecture/documentation stale.
    rerun: bool = False


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
    # The single question Context Discovery is waiting on, when
    # status == "awaiting_clarification". None otherwise.
    pending_clarification: PendingClarificationResponse | None = None


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


def _awaiting_input_step(workflow: Workflow, stage: str) -> tuple[Run, Any] | None:
    """The most recent (run, step) pair for `stage` still paused at
    "awaiting_input", or None. There is at most one live paused run per
    stage at a time — `continue_workflow`'s "stage already has a run" check
    prevents a second one from ever being started alongside it."""
    for run in sorted(workflow.runs or [], key=lambda r: r.created_at, reverse=True):
        if run.workflow_stage == stage and run.status == "awaiting_input":
            step = run.steps[0] if run.steps else None
            if step is not None:
                return run, step
    return None


def _pending_clarification(workflow: Workflow) -> PendingClarificationResponse | None:
    if workflow.status != "awaiting_clarification":
        return None
    found = _awaiting_input_step(workflow, "context_discovery")
    if found is None:
        return None
    _run, step = found
    # `unresolved_questions` is populated only when the reasoning engine is
    # actually waiting on an answer (see reasoning.projection.build_result, which
    # writes it from `next_question()`), so its presence is authoritative and
    # needs no second-guessing here.
    #
    # In particular: do NOT skip a question whose id appears in `user_answers`.
    # A gap's question id is stable across rounds by design, so a question that
    # was answered, investigated, and *refuted* comes back with the same id and
    # a reason that acknowledges the failed answer. Filtering on "already answered"
    # made exactly that case disappear from the UI while the run sat paused
    # forever waiting for it.
    questions = (step.result or {}).get("unresolved_questions") or []
    for q in questions:
        if q.get("blocking"):
            return PendingClarificationResponse(
                question_id=q["question_id"],
                question=q["question"],
                why=q.get("why", ""),
                options=q.get("options") or [],
                investigated=q.get("investigated") or [],
            )
    return None


def _gap_explanation(cd_result: dict[str, Any] | None, severity: str) -> str:
    """Render the specific missing context, and its remediation, from
    Context Discovery's own gap list.

    Blocking a user without telling them *which* piece of context is missing
    is the difference between a useful stop and a wall. Discovery already
    knows: each gap carries a summary, the individual unsatisfied signals, and
    the remediation steps that would close it.
    """
    gaps = ((cd_result or {}).get("discovery_report") or {}).get("gaps") or []
    relevant = [g for g in gaps if g.get("severity") == severity and g.get("status") != "verified"]
    if not relevant:
        return "No specific cause was recorded."

    lines: list[str] = []
    for gap in relevant:
        detail = "; ".join(gap.get("missing") or [])
        remediation = ", ".join(gap.get("recommended_action") or [])
        line = gap.get("summary", "")
        if detail:
            line += f" ({detail})"
        if remediation:
            line += f" → {remediation}"
        lines.append(line)
    return " ".join(lines)


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


def _report_finalizer(report_id: uuid.UUID) -> OnComplete:
    """Build the on_complete callback for the report_generation agent's
    run — persists {"title", "html"} from the completed AgentStep's result
    into the WorkflowReport row this run was dispatched for. Runs in the
    background task's own DB session, same pattern as
    _workflow_stage_finalizer above.
    """

    async def _finalize(db: AsyncSession, run: Run) -> None:
        # `on_pre_commit` is expected to commit itself (see run_coordinator.
        # RunCoordinator._commit_with_hook's docstring — it only falls back
        # to its own commit when the hook raises); every branch below that
        # mutates `report` must therefore commit before returning, exactly
        # as workflow_service.finalize_stage_run already does for the stage
        # finalizer above.
        report = await db.get(WorkflowReport, report_id)
        if report is None:
            logger.error("workflow_report_vanished report_id=%s", str(report_id))
            return

        if run.status != "completed":
            report.status = "failed"
            report.error_message = run.error_message or "Report generation run did not complete."
            report.completed_at = datetime.now(UTC)
            await db.commit()
            return

        # Queried directly rather than via run.steps: `run` here is the bare
        # object RunCoordinator itself constructed/fetched for this one run,
        # never eagerly loaded with `.steps` (unlike a Workflow fetched
        # through get_workflow_for_update's selectinload chain, which is
        # what every other .steps access in this codebase relies on) — an
        # un-eager-loaded relationship access on an AsyncSession object
        # raises MissingGreenlet from exactly this call stack (inside an
        # on_pre_commit hook, past the point SQLAlchemy can bridge an
        # implicit lazy-load), not a normal awaited query.
        step_result = await db.execute(
            select(AgentStep).where(AgentStep.run_id == run.id).limit(1)
        )
        step = step_result.scalar_one_or_none()
        result = step.result if step else {}
        html = result.get("html")
        if not html:
            report.status = "failed"
            report.error_message = "Report generation completed with no HTML content."
            report.completed_at = datetime.now(UTC)
            await db.commit()
            return

        report.title = result.get("title") or report.title
        report.html_content = html
        report.status = "completed"
        report.completed_at = datetime.now(UTC)
        await db.commit()

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


def _build_projection_input(
    cd_result: dict[str, Any],
    *,
    debug: bool,
) -> ProjectionInput:
    """Parse persisted ContextDiscoveryResult into a typed ProjectionInput.

    This is the **parsing boundary**: all ``.get()`` calls, raw-dict
    access, and untyped-data handling happen here.  The mapper
    (``map_to_dto``) never sees raw data.
    """
    understanding = EngineeringUnderstanding(
        **cd_result.get("engineering_understanding", {}),
    )
    evidence_package = EvidencePackage(
        **cd_result.get("evidence_package", {}),
    )

    original_request: str = cd_result.get("original_request", "")
    raw_readiness = cd_result.get("readiness", "BLOCKED")
    readiness = (
        raw_readiness
        if raw_readiness in ("READY", "PARTIAL", "BLOCKED")
        else "BLOCKED"
    )
    blocking_reasons: list[str] = cd_result.get("blocking_reasons") or []

    # Typed graph projections from raw dicts
    graph_topics = [
        TopicProjection(name=t["name"])
        for t in cd_result.get("graph_topics") or []
        if t.get("name")
    ]
    graph_components = [
        ComponentProjection(name=c["name"], topic=c.get("topic", ""))
        for c in cd_result.get("graph_components") or []
        if c.get("name")
    ]

    # Extract from discovery_report
    report: dict[str, Any] = cd_result.get("discovery_report") or {}
    breakdown: list[dict[str, Any]] = report.get("confidence_breakdown") or []
    gaps: list[dict[str, Any]] = report.get("gaps") or []

    # Filter not_applicable capabilities — irrelevant to readiness and UX
    capability_factors = [
        CapabilityFactor(
            capability=entry.get("capability", ""),
            label=entry.get("label", ""),
            satisfied=entry.get("satisfied", False),
        )
        for entry in breakdown
        if entry.get("necessity") != "not_applicable"
    ]

    gap_summaries = [
        g["summary"]
        for g in gaps
        if g.get("status") != "verified" and g.get("summary")
    ]
    unavailable_gaps = [
        g["summary"]
        for g in gaps
        if g.get("status") == "unresolvable" and g.get("summary")
    ]

    documentation_status = _derive_documentation_status(breakdown, gaps)
    next_step = _derive_next_step(readiness, blocking_reasons)

    debug_bundle = None
    if debug:
        debug_bundle = DebugBundleDTO(
            investigation_trail=report.get("investigation") or [],
            confidence_breakdown=breakdown,
            findings=report.get("findings") or [],
            gaps=gaps,
            transcript=report.get("transcript") or [],
            graph_components=cd_result.get("graph_components") or [],
            graph_topics=cd_result.get("graph_topics") or [],
            repository_ranking=cd_result.get("ranked_repository_names") or [],
            capability_confidence=cd_result.get("capability_confidence") or {},
            planning_metadata=cd_result.get("planning_metadata") or {},
            working_memory=cd_result.get("working_memory") or {},
            assumptions=cd_result.get("assumptions") or [],
            evidence_package_raw=cd_result.get("evidence_package") or {},
        )

    return ProjectionInput(
        understanding=understanding,
        evidence_package=evidence_package,
        original_request=original_request,
        readiness=readiness,
        blocking_reasons=blocking_reasons,
        graph_topics=graph_topics,
        graph_components=graph_components,
        capability_factors=capability_factors,
        gap_summaries=gap_summaries,
        unavailable_gaps=unavailable_gaps,
        documentation_status=documentation_status,
        next_step=next_step,
        debug_bundle=debug_bundle,
    )


def _derive_documentation_status(
    breakdown: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> str:
    """Derive documentation status from capability assessment and gaps.

    Presentation-time workflow decision — belongs in the endpoint (caller),
    not the mapper.
    """
    doc_factor = next(
        (e for e in breakdown if e.get("capability") == "documentation"),
        None,
    )
    if doc_factor is None:
        return "Documentation status unknown."
    if doc_factor.get("satisfied", False):
        return "Documentation requirements satisfied."
    doc_gaps = [
        g["summary"]
        for g in gaps
        if g.get("capability") == "documentation"
        and g.get("status") != "verified"
        and g.get("summary")
    ]
    if doc_gaps:
        return "; ".join(doc_gaps)
    return "Documentation requirements not yet satisfied."


def _derive_next_step(readiness: str, blocking_reasons: list[str]) -> str:
    """Derive next-step guidance from readiness and blocking reasons.

    Presentation-time workflow decision — belongs in the endpoint (caller),
    not the mapper.
    """
    if readiness == "READY":
        return "Context is ready to proceed to planning."
    if blocking_reasons:
        return "Resolve blocking issues: " + "; ".join(blocking_reasons)
    return "Continue context discovery to gather more information."


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
    from app.orchestrator.background_execution import (
        schedule_run_execution,
        schedule_title_generation,
    )
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

    # Execute the first stage of this workflow_type's sequence — "planning"
    # workflows now start at "context_discovery" (see WORKFLOW_TYPE_STAGES),
    # legacy_sdlc still starts at "planning" directly, unchanged.
    stage = workflow_service.stage_sequence(body.workflow_type)[0]
    goal = workflow_service.STAGE_GOALS[stage]

    # For auto_execution, enrich the first-stage context with the source
    # blueprint's outputs. For a Refine (parent_workflow_id), same
    # mechanism — the parent's completed stage(s) become "source" context
    # — plus the human's own refinement note appended as its own fenced
    # block. For a plain "New Workflow" (neither), the title suffices.
    # Threaded into extras below (in addition to shaping `subject`) so
    # deterministic verification (e.g. code_generation's repository scope
    # check) can read the source blueprint's structured stage results, not
    # just the flattened text `enriched_ref` folds it into.
    source_workflow: Workflow | None = None

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

    # Bound before either background task is created, not inside them: a
    # task copies its creator's contextvars context at creation time, so
    # this is what makes both tasks' logs carry the same correlation ids
    # as the request that scheduled them.
    set_workflow_context(workflow_id=str(workflow.id), workflow_run_id=str(run.id))

    schedule_run_execution(
        run_id=run.id,
        subject=subject,
        goal=goal,
        model=body.model,
        extras={"workflow": workflow, "user_id": user.id, "source_workflow": source_workflow},
        agent_id=agent_id,
        registry=global_registry,
        on_complete=_workflow_stage_finalizer(workflow.id),
    )
    # Real AI title generation, off the request's critical path — the
    # workflow already carries a deterministic placeholder title (see
    # workflow_service.create_workflow) and is durable as of the commit
    # just above, which is what makes it safe for a separate background
    # session to fetch it by id.
    schedule_title_generation(workflow.id, body.title, body.model)

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
        pending_clarification=_pending_clarification(workflow),
    )


@router.get(
    "/{workflow_id}/understanding",
    response_model=EngineeringUnderstandingDTO,
)
async def get_understanding(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    debug: bool = Query(False),
) -> EngineeringUnderstandingDTO:
    """Read-time projection of Context Discovery results as an Engineering
    Understanding DTO.

    The endpoint owns all parsing of persisted data — the mapper
    (``map_to_dto``) receives only typed models and performs pure
    transformation.  No new data is written.  Existing endpoints and
    Planning behavior are unchanged.
    """
    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow(db, wid, user_id=user.id)

    cd_result = get_stage_result(workflow, "context_discovery")
    if cd_result is None:
        raise NotFoundError(
            "No completed context discovery result for this workflow.",
        )

    projection_input = _build_projection_input(cd_result, debug=debug)
    return map_to_dto(projection_input, include_debug=debug)


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

    if workflow.status in (
        "completed",
        "awaiting_approval",
        "approved",
        "rejected",
        "awaiting_clarification",
    ):
        detail = (
            "Answer the pending clarification question first (POST .../clarify)."
            if workflow.status == "awaiting_clarification"
            else "no further stages can run."
        )
        raise AppError(
            f"Workflow is {workflow.status.replace('_', ' ')} — {detail}",
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

    # Readiness gate: Planning may only start once Context Discovery is
    # READY, or the human has explicitly acknowledged a PARTIAL result.
    # BLOCKED can never be pushed past.
    #
    # Both messages are built from the discovery report's own gap list rather
    # than from a generic fallback string: a user being stopped needs to know
    # which specific piece of context is missing and what to do about it, and
    # discovery already computed exactly that (see
    # reasoning.projection.build_discovery_report).
    if target_stage == "planning":
        cd_result = get_stage_result(workflow, "context_discovery")
        readiness = (cd_result or {}).get("readiness", "READY")
        if readiness == "BLOCKED":
            raise AppError(
                "Context Discovery could not establish enough context to plan. "
                + _gap_explanation(cd_result, "blocking"),
                status_code=400,
                error_code="context_discovery_blocked",
            )
        if readiness == "PARTIAL" and not body.acknowledge_partial:
            confidence = (cd_result or {}).get("confidence", 0.0)
            # No "resend with acknowledge_partial=true" here: this message is
            # rendered verbatim to the user, who is looking at a "Continue
            # anyway" button — telling them to resend an HTTP field is
            # developer-facing noise. The machine-readable contract is the
            # `error_code` plus the documented `acknowledge_partial` field on
            # ContinueWorkflowRequest.
            raise AppError(
                f"Context Discovery reached {confidence:.0%} confidence, but some optional "
                "context is missing. " + _gap_explanation(cd_result, "advisory"),
                status_code=409,
                error_code="context_discovery_partial",
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

    # `workflow.original_prompt` — the full verbatim request — not
    # `workflow.title` (an AI-generated 5-10 word summary, see
    # models/workflow.py's docstring on each field). Every later stage's
    # prompt was being built from the short title instead of the actual
    # brief; `create_workflow`'s own build_stage_context calls above
    # already use the full text (`body.title`, which — despite the name —
    # is the full engineering objective on that request schema).
    enriched_ref = workflow_service.build_stage_context(
        workflow,
        original_request=workflow.original_prompt,
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

    set_workflow_context(workflow_id=str(workflow.id), workflow_run_id=str(run.id))

    schedule_run_execution(
        run_id=run.id,
        subject=subject,
        goal=goal,
        model=body.model,
        extras={"workflow": workflow, "user_id": user.id, "source_workflow": source_workflow},
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


@router.post("/{workflow_id}/clarify", status_code=202, response_model=ContinueWorkflowResponse)
async def clarify_workflow(
    workflow_id: str,
    body: ClarifyWorkflowRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ContinueWorkflowResponse:
    """Answer Context Discovery's pending clarification question and resume
    it from where it paused (see `RunCoordinator.resume_step` and
    `reasoning_loop.resume_discovery`). Backgrounded exactly like /continue —
    a real LLM call, so it must survive the client disconnecting.

    Only Context Discovery can be paused today (see the reasoning-driven
    Context Discovery design) — this endpoint is written generically enough
    (it resumes whatever stage is actually paused) for a future stage to
    reuse the same mechanism.
    """
    from app.orchestrator.background_execution import schedule_resume_execution

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

    if workflow.status != "awaiting_clarification":
        # Reached when the question was already answered — commonly a second
        # browser tab, or a double-submit. The message is written for a person:
        # leaking the internal status token ("status: in_progress") told the user
        # nothing they could act on.
        raise AppError(
            "This workflow is not waiting on an answer — the question may already "
            "have been answered.",
            status_code=409,
            error_code="not_awaiting_clarification",
        )

    found = _awaiting_input_step(workflow, "context_discovery")
    if found is None:
        raise AppError(
            "No paused Context Discovery step found to resume.",
            status_code=409,
            error_code="no_paused_step",
        )
    run, step = found

    result = step.result or {}
    questions = result.get("unresolved_questions") or []
    current = next(
        (q for q in questions if q.get("question_id") == body.question_id and q.get("blocking")),
        None,
    )
    if current is None:
        raise AppError(
            "That question is no longer pending — it may have already been answered.",
            status_code=409,
            error_code="stale_question",
        )

    if global_registry.get(step.agent_id) is None:
        raise AppError(
            f"Agent '{step.agent_id}' is no longer registered.",
            status_code=500,
            error_code="agent_not_found",
        )

    enriched_ref = workflow_service.build_stage_context(
        workflow,
        original_request=workflow.original_prompt,
        target_stage="context_discovery",
    )
    subject = _resolve_stage_subject(workflow, "context_discovery", enriched_ref)

    step.status = "running"
    run.status = "running"
    # The pause is over the moment the answer is accepted. Leaving the workflow
    # at "awaiting_clarification" while the resumed run executes made the header
    # badge read "Needs Your Input" with no banner to act on — the answer looked
    # like it had been dropped. The finalizer will set the real terminal status
    # (advance, pause again for a follow-up question, or fail).
    workflow.status = "in_progress"
    await db.commit()

    set_workflow_context(workflow_id=str(workflow.id), workflow_run_id=str(run.id))

    schedule_resume_execution(
        run_id=run.id,
        step_id=step.id,
        subject=subject,
        goal=run.goal,
        model=run.model,
        extras={
            "user_id": user.id,
            "resume": {
                "working_context": result,
                "answer": {"question_id": body.question_id, "answer": body.answer},
            },
        },
        agent_id=step.agent_id,
        registry=global_registry,
        on_complete=_workflow_stage_finalizer(workflow.id),
    )

    return ContinueWorkflowResponse(
        workflow_id=str(workflow.id),
        run_id=str(run.id),
        stage="context_discovery",
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


@router.patch("/{workflow_id}/stages/{stage}/override", response_model=WorkflowApprovalResponse)
async def override_stage_result(
    workflow_id: str,
    stage: str,
    body: OverrideStageResultRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> WorkflowApprovalResponse:
    """Human correction on a completed stage's result — the mechanism
    behind Context Explorer's review/edit UI (see the Context Discovery /
    Context Explorer architecture review). Reuses the existing approval
    lifecycle entirely: this only ever adjusts what the *next* stage will
    read via get_stage_result() when the human then approves/continues
    exactly as they already do for any other stage transition. It is not
    itself a stage transition, a new workflow status, or a new approval
    mechanism — the workflow's `current_stage`/`status` are untouched.

    Named generically (`{stage}/override`), not `context-discovery/
    override` specifically, since nothing about the mechanism is
    Context-Discovery-only — any stage whose downstream consumer reads it
    via get_stage_result() can be corrected the same way.
    """
    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc

    workflow = await workflow_service.get_workflow_for_update(db, wid, user_id=user.id)

    if body.rerun and stage == "context_discovery":
        # Re-run Context Discovery with the selected repositories as explicit
        # input. This recomputes ALL investigation results — relevant areas,
        # architecture, documentation, engineering synthesis — using the
        # correct repository, fixing the stale-data issue caused by overriding
        # repositories without re-investigating.
        from app.orchestrator.background_execution import schedule_run_execution
        from app.orchestrator.run_coordinator import RunCoordinator

        check_rate_limit(
            f"stage_start:{user.id}",
            max_requests=_STAGE_START_RATE_LIMIT,
            window_seconds=_STAGE_START_RATE_WINDOW_SECONDS,
        )

        # Extract selected repository names from the override payload.
        repos_payload = body.override.get("repositories", [])
        if not isinstance(repos_payload, list) or not repos_payload:
            raise AppError(
                "rerun=true requires a non-empty 'repositories' list in override.",
                status_code=400,
                error_code="rerun_missing_repositories",
            )
        explicit_repo_names = [
            r["name"] if isinstance(r, dict) else r
            for r in repos_payload
            if (isinstance(r, dict) and r.get("selected", True)) or isinstance(r, str)
        ]
        if not explicit_repo_names:
            raise AppError(
                "At least one repository must be selected for re-run.",
                status_code=400,
                error_code="rerun_no_selected_repositories",
            )

        # Reset workflow to re-run context_discovery.
        workflow.current_stage = "context_discovery"
        workflow.status = "in_progress"
        workflow.updated_at = datetime.now(UTC)

        # Create and schedule the new run (same execution path as
        # continue_workflow, reusing the existing agent and coordinator).
        goal = workflow_service.STAGE_GOALS["context_discovery"]
        subject = resolve_freetext(workflow.original_prompt)

        selector = AgentSelector(global_registry)
        coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)
        run, agent_id, _agent = await coordinator.create_pending_run(
            subject=subject, goal=goal, model=None
        )
        run.workflow_id = workflow.id
        run.workflow_stage = "context_discovery"
        await db.commit()

        set_workflow_context(workflow_id=str(workflow.id), workflow_run_id=str(run.id))

        schedule_run_execution(
            run_id=run.id,
            subject=subject,
            goal=goal,
            model=None,
            extras={
                "workflow": workflow,
                "user_id": user.id,
                "explicit_repositories": explicit_repo_names,
            },
            agent_id=agent_id,
            registry=global_registry,
            on_complete=_workflow_stage_finalizer(workflow.id),
        )

        logger.info(
            "context_discovery_rerun_scheduled workflow_id=%s repos=%s",
            workflow_id,
            explicit_repo_names,
        )
        return WorkflowApprovalResponse(workflow_id=str(workflow.id), status=workflow.status)

    await workflow_service.override_stage_result(db, workflow, stage, body.override, user.id)
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

    # Kick off the high-level HTML report — see app.agents.report_generation.
    # Best-effort: a failure here must never block the approval itself
    # (already flushed above in the same transaction), so it's logged,
    # not raised, and the report row is marked "failed" rather than left
    # "pending" forever if run creation itself never succeeds.
    report = WorkflowReport(workflow_id=workflow.id, title=workflow.title, status="pending")
    db.add(report)
    try:
        from app.orchestrator.background_execution import schedule_run_execution
        from app.orchestrator.run_coordinator import RunCoordinator

        subject = resolve_freetext(workflow.title)
        selector = AgentSelector(global_registry)
        coordinator = RunCoordinator(db=db, registry=global_registry, selector=selector)
        run, agent_id, _agent = await coordinator.create_pending_run(
            subject=subject, goal="generate_report"
        )
        run.workflow_id = workflow.id
        report.run_id = run.id
        await db.commit()

        schedule_run_execution(
            run_id=run.id,
            subject=subject,
            goal="generate_report",
            model=None,
            extras={"workflow": workflow, "user_id": user.id},
            agent_id=agent_id,
            registry=global_registry,
            on_complete=_report_finalizer(report.id),
        )
    except Exception as exc:
        logger.exception(
            "workflow_report_dispatch_failed workflow_id=%s — approval itself is unaffected",
            str(workflow.id),
        )
        report.status = "failed"
        report.error_message = str(exc)
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
