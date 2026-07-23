"""Workflow service — manages SDLC workflow lifecycle.

Handles workflow creation, stage transitions, context propagation
from previous runs, and run linking.  Uses the existing RunCoordinator
for actual agent execution — this layer adds only the sequencing logic.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.run import Run
from app.models.workflow import Workflow

logger = logging.getLogger(__name__)

# --- Stage definitions ---

STAGES = ("planning", "development", "testing", "review")

STAGE_GOALS: dict[str, str] = {
    "planning": "plan_freeform",
    "development": "develop_change_plan",
    "testing": "plan_tests",
    "review": "review_pr",
}

STAGE_LABELS: dict[str, str] = {
    "planning": "Planning",
    "development": "Development",
    "testing": "Testing",
    "review": "Review",
}


def next_stage(current: str) -> str | None:
    """Return the next SDLC stage, or None if already at the end."""
    try:
        idx = STAGES.index(current)
    except ValueError:
        return None
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


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

    # Key fields that provide useful context
    for key in (
        "affected_components",
        "affected_repositories",
        "repositories_consulted",
        "kafka_topics_involved",
        "risk_considerations",
        "recommendations",
    ):
        if items := result.get(key):
            if isinstance(items, list) and items:
                parts.append(f"{key.replace('_', ' ').title()}: {', '.join(str(i) for i in items[:10])}")

    return "\n".join(parts)


async def create_workflow(
    db: AsyncSession,
    title: str,
) -> Workflow:
    """Create a new SDLC workflow starting at the planning stage."""
    workflow = Workflow(
        id=uuid.uuid4(),
        title=title,
        current_stage="planning",
        status="in_progress",
    )
    db.add(workflow)
    await db.flush()

    logger.info("workflow_created id=%s title=%s", str(workflow.id), title)
    return workflow


async def get_workflow(
    db: AsyncSession,
    workflow_id: uuid.UUID,
) -> Workflow:
    """Fetch a workflow with all linked runs eagerly loaded."""
    result = await db.execute(
        select(Workflow)
        .options(selectinload(Workflow.runs).selectinload(Run.steps))
        .where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise NotFoundError(f"Workflow '{workflow_id}' not found.")
    return workflow


async def list_workflows(
    db: AsyncSession,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Workflow], int]:
    """List workflows with pagination, optionally filtered by status."""
    from sqlalchemy import func as sa_func

    query = select(Workflow)
    count_query = select(sa_func.count(Workflow.id))

    if status:
        query = query.where(Workflow.status == status)
        count_query = count_query.where(Workflow.status == status)

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
) -> str:
    """Build enriched subject_reference for the next stage.

    Prepends summaries from all completed previous stages to the
    original engineering request so the next agent has full context.
    """
    parts: list[str] = [original_request]

    # Find all completed runs in this workflow, ordered by creation
    for run in sorted(workflow.runs, key=lambda r: r.created_at):
        if run.status == "completed" and run.workflow_stage != target_stage:
            summary = _summarize_previous_output(run)
            if summary:
                parts.append(f"\n--- Context from {STAGE_LABELS.get(run.workflow_stage or '', run.workflow_stage or '')} stage (run {run.id}) ---\n{summary}")

    return "\n".join(parts)


async def advance_workflow(
    db: AsyncSession,
    workflow: Workflow,
    completed_run: Run,
) -> None:
    """Advance the workflow to the next stage after a run completes."""
    if completed_run.status != "completed":
        return

    current = completed_run.workflow_stage
    if not current:
        return

    nxt = next_stage(current)
    if nxt is None:
        workflow.status = "completed"
        workflow.current_stage = "completed"
    else:
        workflow.current_stage = nxt

    workflow.updated_at = datetime.now(timezone.utc)
    await db.flush()

    logger.info(
        "workflow_advanced id=%s from=%s to=%s",
        str(workflow.id),
        current,
        workflow.current_stage,
    )
