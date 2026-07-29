"""Workflow service — manages SDLC workflow lifecycle.

Handles workflow creation, stage transitions, context propagation
from previous runs, and run linking.  Uses the existing RunCoordinator
for actual agent execution — this layer adds only the sequencing logic.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.title_generation import fallback_title
from app.core.exceptions import NotFoundError
from app.models.run import Run
from app.models.workflow import Workflow

logger = logging.getLogger(__name__)

# --- Stage definitions ---
#
# WORKFLOW_TYPE_STAGES is the one thing that differs between workflow
# types — everything else in this module (advance_workflow, build_stage_
# context, the router's continue/approve endpoints) reads a workflow's own
# `workflow_type` to pick its sequence, rather than branching on type
# itself. This is the "shared engine, not two engines" design: one set of
# functions, parameterized by a data table.
#
# "legacy_sdlc" is the exact, frozen 4-stage sequence every workflow used
# before workflow_type existed — the Workflow.workflow_type column defaults
# to it, so pre-existing rows are never reinterpreted. "planning" is the
# new, human-gated, no-repo-writes sequence and is what NewWorkflowPage
# creates by default going forward.

STAGES = ("planning", "development", "testing", "review")

WORKFLOW_TYPE_STAGES: dict[str, tuple[str, ...]] = {
    "legacy_sdlc": STAGES,
    "planning": (
        "context_discovery",
        "planning",
        "development",
        "testing",
        "documentation_planning",
        "engineering_review",
    ),
    "auto_execution": (
        "generate_code",
        "create_branch",
        "commit_changes",
        "run_tests",
        "create_pull_request",
        "ai_pr_review",
    ),
}

# What status the workflow transitions to when its last stage completes.
# "awaiting_approval" gates on human decision; "completed" is terminal.
TERMINAL_BEHAVIOR: dict[str, str] = {
    "planning": "awaiting_approval",
    "auto_execution": "completed",
    "legacy_sdlc": "completed",
}

STAGE_GOALS: dict[str, str] = {
    "context_discovery": "discover_context",
    "planning": "plan_freeform",
    "development": "develop_change_plan",
    "testing": "plan_tests",
    "review": "review_pr",
    "documentation_planning": "plan_documentation",
    "engineering_review": "review_readiness",
    "generate_code": "generate_code",
    "create_branch": "create_branch",
    "commit_changes": "commit_changes",
    "run_tests": "run_tests",
    "create_pull_request": "create_pull_request",
    "ai_pr_review": "review_pr",
}

STAGE_LABELS: dict[str, str] = {
    "context_discovery": "Context Discovery",
    "planning": "Planning",
    "development": "Development",
    "testing": "Testing",
    "review": "Review",
    "documentation_planning": "Documentation Planning",
    "engineering_review": "Engineering Review",
    "generate_code": "Generate Code",
    "create_branch": "Create Branch",
    "commit_changes": "Commit Changes",
    "run_tests": "Run Tests",
    "create_pull_request": "Create Pull Request",
    "ai_pr_review": "AI PR Review",
}


def stage_sequence(workflow_type: str) -> tuple[str, ...]:
    """The ordered stage sequence for a workflow type. Unknown/missing
    types fall back to "legacy_sdlc" so old data is never reinterpreted."""
    return WORKFLOW_TYPE_STAGES.get(workflow_type, STAGES)


def next_stage(current: str, workflow_type: str = "legacy_sdlc") -> str | None:
    """Return the next stage in `workflow_type`'s sequence, or None if
    `current` is already the last stage."""
    sequence = stage_sequence(workflow_type)
    try:
        idx = sequence.index(current)
    except ValueError:
        return None
    return sequence[idx + 1] if idx + 1 < len(sequence) else None


def _summarize_previous_output(run: Run) -> str:
    """Build a concise context summary from a completed run's first step."""
    step = run.steps[0] if run.steps else None
    if not step or not step.result:
        return ""

    result = step.result
    parts: list[str] = []

    # Executive summary is present in all agent outputs
    if summary := result.get("executive_summary"):
        parts.append(f"Previous stage ({run.goal}) summary: {summary}")

    # Key fields that provide useful context for the freetext chain other
    # stages read via context.subject.display_name. Engineering Review no
    # longer depends on this function — it reads full structured results
    # directly via get_stage_result() instead (see engineering_review/
    # agent.py), since this summary's field list and its 256-char
    # downstream truncation in resolve_freetext() were too lossy for a
    # stage whose whole job is judging completeness of prior stages.
    for key in (
        "affected_components",
        "affected_repositories",
        "repositories_consulted",
        "kafka_topics_involved",
        "risk_considerations",
        "recommendations",
        "risks",
        "dependencies",
        "implementation_phases",
        "regression_tests",
        "integration_tests",
        "edge_cases",
    ):
        items = result.get(key)
        if isinstance(items, list) and items:
            parts.append(
                f"{key.replace('_', ' ').title()}: {', '.join(str(i) for i in items[:10])}"
            )

    return "\n".join(parts)


CREATABLE_WORKFLOW_TYPES: frozenset[str] = frozenset(WORKFLOW_TYPE_STAGES.keys() - {"legacy_sdlc"})


async def create_workflow(
    db: AsyncSession,
    title: str,
    workflow_type: str = "planning",
    source_workflow_id: uuid.UUID | None = None,
    parent_workflow_id: uuid.UUID | None = None,
    refinement_note: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Workflow:
    """Create a new workflow starting at its type's first stage.

    `title` here is the user's full engineering objective (the parameter
    name is kept for API-contract stability) — stored verbatim as
    `Workflow.original_prompt`, while `Workflow.title` starts as a
    deterministic truncated placeholder (see `fallback_title`) and is
    replaced with a real, AI-generated short title shortly after this
    call returns, once the caller schedules
    `background_execution.schedule_title_generation` against the
    committed row (see that function's docstring for why the scheduling
    itself doesn't happen in here).

    For auto_execution workflows, `source_workflow_id` must reference an
    existing, approved Planning workflow (the blueprint being executed).

    `parent_workflow_id`/`refinement_note` are the "Refine" flow (distinct
    from auto_execution's source_workflow_id above): the workflow this one
    refines, and the human's own note on what to change. `version` is
    derived from the parent's version, not recomputed by walking the whole
    chain, so it stays O(1) regardless of how many refinements deep this
    goes.
    """
    from app.core.exceptions import AppError

    if workflow_type not in CREATABLE_WORKFLOW_TYPES:
        raise AppError(
            f"Invalid workflow_type '{workflow_type}'. "
            f"Allowed: {sorted(CREATABLE_WORKFLOW_TYPES)}.",
            status_code=400,
            error_code="invalid_workflow_type",
        )

    # --- auto_execution-specific validation ---
    if workflow_type == "auto_execution":
        if source_workflow_id is None:
            raise AppError(
                "auto_execution workflows require a source_workflow_id "
                "referencing an approved Planning blueprint.",
                status_code=400,
                error_code="missing_source_workflow",
            )
        source = await db.get(Workflow, source_workflow_id)
        if source is None or (
            user_id is not None and source.user_id is not None and source.user_id != user_id
        ):
            # Same "don't reveal it exists" NotFoundError treatment as
            # _check_workflow_owned — a source_workflow_id owned by someone
            # else must fail identically to one that doesn't exist at all,
            # otherwise this endpoint becomes an IDOR oracle.
            raise AppError(
                f"Source workflow '{source_workflow_id}' not found.",
                status_code=400,
                error_code="source_workflow_not_found",
            )
        if source.workflow_type != "planning":
            raise AppError(
                f"Source workflow must be a Planning workflow, got '{source.workflow_type}'.",
                status_code=400,
                error_code="source_workflow_wrong_type",
            )
        if source.status != "approved":
            raise AppError(
                f"Source workflow must be approved, current status is '{source.status}'.",
                status_code=400,
                error_code="source_workflow_not_approved",
            )

    # --- refine-specific validation ---
    parent: Workflow | None = None
    if parent_workflow_id is not None:
        try:
            parent = await get_workflow(db, parent_workflow_id, user_id=user_id)
        except NotFoundError as exc:
            raise AppError(
                f"Parent workflow '{parent_workflow_id}' not found.",
                status_code=400,
                error_code="parent_workflow_not_found",
            ) from exc

    # Instant, deterministic placeholder — never blocks workflow creation on
    # an LLM round-trip. The router schedules the real AI title generation
    # as a background task (schedule_title_generation) once this workflow
    # (and the run that follows it) is actually committed; see that
    # function's docstring for why it can't be scheduled from in here.
    generated_title = fallback_title(title)

    workflow = Workflow(
        id=uuid.uuid4(),
        title=generated_title,
        original_prompt=title,
        current_stage=stage_sequence(workflow_type)[0],
        status="in_progress",
        workflow_type=workflow_type,
        source_workflow_id=source_workflow_id,
        parent_workflow_id=parent_workflow_id,
        version=(parent.version + 1) if parent is not None else 1,
        refinement_note=refinement_note,
        user_id=user_id,
    )
    db.add(workflow)
    await db.flush()

    logger.info(
        "workflow_created id=%s title=%s type=%s version=%d parent_id=%s",
        str(workflow.id),
        generated_title,
        workflow_type,
        workflow.version,
        str(parent_workflow_id) if parent_workflow_id else None,
    )
    return workflow


def _check_workflow_owned(workflow: Workflow, user_id: uuid.UUID) -> None:
    """Raise NotFoundError (not Forbidden — don't reveal that a workflow
    owned by someone else exists) unless `workflow` has no recorded owner
    (a row created before Workflow.user_id existed, kept visible to any
    authenticated user rather than becoming permanently inaccessible) or
    the caller *is* its owner."""
    if workflow.user_id is not None and workflow.user_id != user_id:
        raise NotFoundError(f"Workflow '{workflow.id}' not found.")


async def get_workflow(
    db: AsyncSession,
    workflow_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> Workflow:
    """Fetch a workflow with all linked runs eagerly loaded.

    `user_id`, when given, enforces ownership — pass it from every
    user-facing endpoint. It's optional only because internal callers
    (e.g. resolving a `source_workflow_id`/`parent_workflow_id` reference
    while building another workflow) legitimately need to read a workflow
    without applying the *current* request's ownership to it.
    """
    result = await db.execute(
        select(Workflow)
        .options(selectinload(Workflow.runs).selectinload(Run.steps))
        .where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise NotFoundError(f"Workflow '{workflow_id}' not found.")
    if user_id is not None:
        _check_workflow_owned(workflow, user_id)
    return workflow


async def get_workflow_for_update(
    db: AsyncSession,
    workflow_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> Workflow:
    """Fetch a workflow with a row-level lock (FOR UPDATE).

    Use this in mutating endpoints (continue, approve, reject) to prevent
    TOCTOU races where concurrent requests both pass the in-memory
    status/stage checks before either commits. `user_id` enforces
    ownership the same way as `get_workflow` above.
    """
    result = await db.execute(
        select(Workflow)
        .options(selectinload(Workflow.runs).selectinload(Run.steps))
        .where(Workflow.id == workflow_id)
        .with_for_update()
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise NotFoundError(f"Workflow '{workflow_id}' not found.")
    if user_id is not None:
        _check_workflow_owned(workflow, user_id)
    return workflow


async def list_workflows(
    db: AsyncSession,
    status: str | None = None,
    workflow_type: str | None = None,
    page: int = 1,
    page_size: int = 25,
    user_id: uuid.UUID | None = None,
) -> tuple[list[Workflow], int]:
    """List workflows with pagination, optionally filtered by status
    and/or workflow_type.

    `user_id`, when given, restricts results to workflows owned by that
    user plus any legacy row with no recorded owner (see
    Workflow.user_id's docstring) — the same ownership rule
    `_check_workflow_owned` enforces on single-workflow reads/writes.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import or_

    query = select(Workflow)
    count_query = select(sa_func.count(Workflow.id))

    if status:
        query = query.where(Workflow.status == status)
        count_query = count_query.where(Workflow.status == status)
    if workflow_type:
        query = query.where(Workflow.workflow_type == workflow_type)
        count_query = count_query.where(Workflow.workflow_type == workflow_type)
    if user_id is not None:
        ownership = or_(Workflow.user_id == user_id, Workflow.user_id.is_(None))
        query = query.where(ownership)
        count_query = count_query.where(ownership)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        query.options(selectinload(Workflow.runs).selectinload(Run.steps))
        .order_by(Workflow.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(query)
    workflows = list(result.scalars().all())
    return workflows, total


def build_stage_context(
    workflow: Workflow,
    original_request: str,
    target_stage: str,
    source_workflow: Workflow | None = None,
) -> str:
    """Build enriched subject_reference for the next stage.

    Prepends summaries from all completed previous stages to the
    original engineering request so the next agent has full context.

    If `source_workflow` is provided (auto_execution workflows), its
    completed runs are included as upstream blueprint context before the
    current workflow's own runs.
    """
    parts: list[str] = [original_request]

    # Include context from source blueprint (if any)
    if source_workflow is not None:
        for run in sorted(source_workflow.runs, key=lambda r: r.created_at):
            if run.status == "completed":
                summary = _summarize_previous_output(run)
                if summary:
                    stage_label = STAGE_LABELS.get(
                        run.workflow_stage or "", run.workflow_stage or ""
                    )
                    parts.append(
                        f"\n--- Blueprint context from {stage_label} stage "
                        f"(source workflow) ---\n{summary}"
                    )

    # Find all completed runs in this workflow, ordered by creation
    for run in sorted(workflow.runs, key=lambda r: r.created_at):
        if run.status == "completed" and run.workflow_stage != target_stage:
            summary = _summarize_previous_output(run)
            if summary:
                stage_label = STAGE_LABELS.get(run.workflow_stage or "", run.workflow_stage or "")
                parts.append(
                    f"\n--- Context from {stage_label} stage (run {run.id}) ---\n{summary}"
                )

    return "\n".join(parts)


async def advance_workflow(
    db: AsyncSession,
    workflow: Workflow,
    completed_run: Run,
) -> None:
    """Advance the workflow to the next stage after a run completes.

    When a "planning" workflow finishes its last stage, it doesn't
    auto-complete like legacy_sdlc — it gates on human approval instead
    (see the /approve and /reject endpoints). current_stage deliberately
    stays at the stage that just ran (there's nothing to advance to yet,
    and nothing has actually "completed" until a human decides).
    """
    if completed_run.status != "completed":
        return

    current = completed_run.workflow_stage
    if not current:
        return

    nxt = next_stage(current, workflow.workflow_type)
    if nxt is None:
        terminal_status = TERMINAL_BEHAVIOR.get(workflow.workflow_type, "completed")
        workflow.status = terminal_status
        if terminal_status == "completed":
            workflow.current_stage = "completed"
    else:
        workflow.current_stage = nxt

    workflow.updated_at = datetime.now(UTC)
    await db.flush()

    logger.info(
        "workflow_advanced id=%s from=%s to=%s status=%s",
        str(workflow.id),
        current,
        workflow.current_stage,
        workflow.status,
    )


async def finalize_stage_run(db: AsyncSession, workflow_id: uuid.UUID, run: Run) -> Workflow:
    """Advance a workflow based on one of its stage runs, once that run has
    reached a terminal status.

    This is the callback the background-execution path (see
    app.orchestrator.background_execution) invokes after a stage's
    agent finishes running, in its own DB session — the router that
    created the run has already returned a response by then, so this is
    where `current_stage`/`status` actually get advanced now (previously
    this ran inline in the router, right after the now-backgrounded
    `RunCoordinator.execute()` call returned).

    Delegates entirely to `advance_workflow`, which already no-ops if
    `run.status != "completed"` — safe to call even if this fires for a
    run that ended up failing, without the caller needing to branch on
    status first.
    """
    workflow = await get_workflow(db, workflow_id)
    await advance_workflow(db, workflow, run)
    await db.commit()
    return workflow


async def _record_confidence_calibration(
    db: AsyncSession, workflow: Workflow, decision: str
) -> None:
    """One row per completed stage's confidence_score, checked against the
    human decision that just closed out this workflow.

    See app.models.confidence_calibration's docstring for why this exists.
    `workflow.runs`/`.steps` must already be eager-loaded (both
    approve_workflow/reject_workflow's caller uses get_workflow_for_update,
    which does) — this never issues its own query.
    """
    from app.models.confidence_calibration import ConfidenceCalibration

    for run in workflow.runs:
        for step in run.steps:
            if step.confidence_score is None:
                continue
            db.add(
                ConfidenceCalibration(
                    id=uuid.uuid4(),
                    workflow_id=workflow.id,
                    run_id=run.id,
                    agent_id=step.agent_id,
                    confidence_score=step.confidence_score,
                    decision=decision,
                )
            )


async def approve_workflow(db: AsyncSession, workflow: Workflow, user_id: uuid.UUID) -> None:
    """Human approves a completed blueprint — terminal, no further stages
    run automatically (matches Auto Execution being a separate, explicit
    workflow the user creates from this one, not an auto-continuation).
    Records who approved it so the Approved Queue can show a real
    approver, not a guess."""
    workflow.status = "approved"
    workflow.approved_by_user_id = user_id
    workflow.updated_at = datetime.now(UTC)
    await _record_confidence_calibration(db, workflow, "approved")
    await db.flush()
    logger.info("workflow_approved id=%s approved_by=%s", str(workflow.id), str(user_id))


async def reject_workflow(db: AsyncSession, workflow: Workflow) -> None:
    """Human rejects the workflow — terminal, from either the initial
    blueprint-approval gate or a later inter-stage approval gate."""
    workflow.status = "rejected"
    workflow.updated_at = datetime.now(UTC)
    await _record_confidence_calibration(db, workflow, "rejected")
    await db.flush()
    logger.info("workflow_rejected id=%s", str(workflow.id))


async def override_stage_result(
    db: AsyncSession,
    workflow: Workflow,
    stage: str,
    override: dict[str, Any],
    user_id: uuid.UUID,
) -> None:
    """Record a human correction on top of a completed stage's AgentStep,
    without touching the AI's own output (see the human-override design in
    the Context Discovery / Context Explorer architecture review).

    `override` is a *partial* dict — only the fields the human actually
    changed — merged on top of `AgentStep.result` at read time by
    `get_stage_result()` (app.agents.git_ops._artifact_reader), never
    written into `result` itself. This is what keeps confidence
    calibration (app.models.confidence_calibration) checking a real,
    unedited AI output against the human's later approve/reject decision.

    Raises NotFoundError if no completed run exists for `stage` — there is
    nothing to override yet.
    """
    step = None
    for run in sorted(workflow.runs, key=lambda r: r.created_at, reverse=True):
        if run.workflow_stage == stage and run.status == "completed":
            step = run.steps[0] if run.steps else None
            break

    if step is None:
        raise NotFoundError(
            f"No completed '{stage}' stage result exists on this workflow to override."
        )

    step.human_override = override
    step.overridden_by_user_id = user_id
    step.overridden_at = datetime.now(UTC)
    await db.flush()
    logger.info(
        "workflow_stage_result_overridden id=%s stage=%s overridden_by=%s",
        str(workflow.id),
        stage,
        str(user_id),
    )
