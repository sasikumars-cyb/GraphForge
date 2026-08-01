"""Executive Report API — structured data for the executive dashboard.

Returns aggregated workflow + run + step + LLM invocation data as
structured JSON suitable for rendering a rich executive dashboard on the
frontend. This complements the existing HTML-only report system by
providing typed, granular data the React UI can chart, filter, and style
with full theme support.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi.responses import HTMLResponse

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.models.agent_step import AgentStep
from app.models.llm_invocation import LLMInvocation
from app.models.run import Run
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_report import WorkflowReport
from app.services.executive_report_renderer import render_executive_html

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# Response DTOs
# ---------------------------------------------------------------------------


class StageMetrics(BaseModel):
    stage: str
    status: str
    duration_ms: int | None = None
    confidence_score: float | None = None
    model: str | None = None
    provider: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: int | None = None
    retry_count: int = 0


class ReviewCategory(BaseModel):
    category: str
    status: str  # "pass" | "warning" | "fail" | "not_evaluated"
    summary: str = ""
    issues: list[str] = Field(default_factory=list)


class RepositoryImpactData(BaseModel):
    repositories_affected: list[str] = Field(default_factory=list)
    files_changed: int = 0
    components_affected: list[str] = Field(default_factory=list)
    dependency_impact: list[str] = Field(default_factory=list)


class RecommendationData(BaseModel):
    merge_readiness: str = "not_evaluated"  # "ready" | "conditional" | "not_ready" | "not_evaluated"
    risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    blocking_items: list[str] = Field(default_factory=list)


class ExecutiveReportData(BaseModel):
    """Full structured data for the executive dashboard."""

    # Executive summary
    workflow_id: str
    workflow_title: str
    original_prompt: str
    workflow_type: str
    status: str
    current_stage: str
    created_at: str
    completed_at: str | None = None
    duration_ms: int | None = None
    approved_by: str | None = None

    # AI cost summary
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_llm_calls: int = 0
    primary_model: str | None = None
    primary_provider: str | None = None

    # Confidence
    overall_confidence: float | None = None

    # Stage breakdown
    stages: list[StageMetrics] = Field(default_factory=list)

    # Repository impact
    repository_impact: RepositoryImpactData = Field(default_factory=RepositoryImpactData)

    # Review results
    review_results: list[ReviewCategory] = Field(default_factory=list)

    # Recommendations
    recommendations: RecommendationData = Field(default_factory=RecommendationData)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: object) -> str | None:
    return dt.isoformat() if dt is not None else None  # type: ignore[union-attr]


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start and end:
        return int((end - start).total_seconds() * 1000)
    return None


def _extract_review_results(step_result: dict) -> list[ReviewCategory]:
    """Extract review category results from an engineering_review step result."""
    categories: list[ReviewCategory] = []
    review_data = step_result.get("review") or step_result.get("reviews") or {}

    # Standard review categories we look for
    category_keys = [
        ("security", "Security"),
        ("architecture", "Architecture"),
        ("testing", "Testing"),
        ("performance", "Performance"),
        ("best_practices", "Best Practices"),
    ]

    if isinstance(review_data, dict):
        for key, label in category_keys:
            section = review_data.get(key)
            if section and isinstance(section, dict):
                categories.append(
                    ReviewCategory(
                        category=label,
                        status=section.get("status", "not_evaluated"),
                        summary=section.get("summary", ""),
                        issues=section.get("issues") or section.get("warnings") or [],
                    )
                )
            else:
                categories.append(
                    ReviewCategory(category=label, status="not_evaluated")
                )
    else:
        # Fallback: populate with not_evaluated
        for _, label in category_keys:
            categories.append(ReviewCategory(category=label, status="not_evaluated"))

    return categories


def _extract_repository_impact(stage_results: dict) -> RepositoryImpactData:
    """Extract repository impact data from workflow stage results."""
    repos: list[str] = []
    files_changed = 0
    components: list[str] = []
    dependencies: list[str] = []

    # From context_discovery
    cd = stage_results.get("context_discovery") or {}
    if isinstance(cd, dict):
        repos = cd.get("ranked_repository_names") or cd.get("repositories") or []
        graph_components = cd.get("graph_components") or []
        components = [c.get("name", "") for c in graph_components if isinstance(c, dict)]

    # From development stage
    dev = stage_results.get("development") or {}
    if isinstance(dev, dict):
        changes = dev.get("changes") or dev.get("file_changes") or []
        if isinstance(changes, list):
            files_changed = len(changes)
        elif isinstance(changes, int):
            files_changed = changes

        deps = dev.get("dependency_changes") or dev.get("dependencies") or []
        if isinstance(deps, list):
            dependencies = [str(d) for d in deps[:10]]

    return RepositoryImpactData(
        repositories_affected=repos[:10],
        files_changed=files_changed,
        components_affected=components[:10],
        dependency_impact=dependencies,
    )


def _extract_recommendations(stage_results: dict) -> RecommendationData:
    """Extract recommendation data from engineering review results."""
    eng_review = stage_results.get("engineering_review") or {}
    if not isinstance(eng_review, dict):
        return RecommendationData()

    readiness = eng_review.get("readiness_verdict") or eng_review.get("merge_readiness") or "not_evaluated"
    # Normalize readiness values
    readiness_lower = readiness.lower()
    if readiness_lower in ("ready", "approved", "pass"):
        merge_readiness = "ready"
    elif readiness_lower in ("conditional", "partial", "warning"):
        merge_readiness = "conditional"
    elif readiness_lower in ("not_ready", "blocked", "fail", "rejected"):
        merge_readiness = "not_ready"
    else:
        merge_readiness = "not_evaluated"

    risks = eng_review.get("risks") or eng_review.get("risk_items") or []
    if isinstance(risks, list):
        risks = [str(r) if not isinstance(r, str) else r for r in risks[:10]]
    else:
        risks = []

    next_actions = eng_review.get("next_actions") or eng_review.get("recommendations") or []
    if isinstance(next_actions, list):
        next_actions = [str(a) if not isinstance(a, str) else a for a in next_actions[:10]]
    else:
        next_actions = []

    blocking = eng_review.get("blocking_items") or eng_review.get("blockers") or []
    if isinstance(blocking, list):
        blocking = [str(b) if not isinstance(b, str) else b for b in blocking[:10]]
    else:
        blocking = []

    return RecommendationData(
        merge_readiness=merge_readiness,
        risks=risks,
        next_actions=next_actions,
        blocking_items=blocking,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{report_id}/executive-data",
    response_model=ExecutiveReportData,
    summary="Fetch structured executive report data for the dashboard",
)
async def get_executive_report_data(
    report_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ExecutiveReportData:
    """Return aggregated workflow, run, step, and LLM invocation data
    structured for the executive dashboard UI."""
    try:
        rid = uuid.UUID(report_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid report_id: {report_id}") from exc

    # Fetch the report and its workflow
    result = await db.execute(
        select(WorkflowReport)
        .options(selectinload(WorkflowReport.workflow))
        .where(WorkflowReport.id == rid)
    )
    report = result.scalar_one_or_none()

    if report is None or not (
        report.workflow is None
        or report.workflow.user_id is None
        or report.workflow.user_id == user.id
    ):
        raise NotFoundError(f"Report '{report_id}' not found.")

    workflow = report.workflow
    if workflow is None:
        raise NotFoundError(f"Workflow for report '{report_id}' not found.")

    # Fetch all runs for this workflow with their steps
    runs_result = await db.execute(
        select(Run)
        .where(Run.workflow_id == workflow.id)
        .options(selectinload(Run.steps))
        .order_by(Run.created_at)
    )
    runs = list(runs_result.scalars().all())

    # Fetch all LLM invocations for these runs
    run_ids = [r.id for r in runs]
    llm_invocations: list[LLMInvocation] = []
    if run_ids:
        llm_result = await db.execute(
            select(LLMInvocation).where(LLMInvocation.run_id.in_(run_ids))
        )
        llm_invocations = list(llm_result.scalars().all())

    # Aggregate LLM metrics
    total_tokens = sum(inv.total_tokens or 0 for inv in llm_invocations)
    total_cost = sum(inv.estimated_cost_usd or 0.0 for inv in llm_invocations)
    total_calls = len(llm_invocations)

    # Determine primary model/provider (most-used)
    model_counts: dict[str, int] = {}
    provider_counts: dict[str, int] = {}
    for inv in llm_invocations:
        if inv.model:
            model_counts[inv.model] = model_counts.get(inv.model, 0) + 1
        if inv.provider:
            provider_counts[inv.provider] = provider_counts.get(inv.provider, 0) + 1

    primary_model = max(model_counts, key=model_counts.get) if model_counts else None  # type: ignore[arg-type]
    primary_provider = max(provider_counts, key=provider_counts.get) if provider_counts else None  # type: ignore[arg-type]

    # Build stage metrics
    stages: list[StageMetrics] = []
    stage_results: dict[str, dict] = {}
    overall_confidence: float | None = None
    confidence_sum = 0.0
    confidence_count = 0

    for run in runs:
        if not run.workflow_stage:
            continue

        # Aggregate step metrics for this run
        run_tokens = 0
        run_cost = 0.0
        run_confidence: float | None = None
        run_model: str | None = run.model
        run_provider: str | None = run.provider

        for step in run.steps or []:
            if step.confidence_score is not None:
                if run_confidence is None or step.confidence_score > run_confidence:
                    run_confidence = step.confidence_score
            # Accumulate step result for extraction
            if step.result:
                stage_results.setdefault(run.workflow_stage, {}).update(step.result)

        # Aggregate LLM invocations for this specific run
        run_invocations = [inv for inv in llm_invocations if inv.run_id == run.id]
        for inv in run_invocations:
            run_tokens += inv.total_tokens or 0
            run_cost += inv.estimated_cost_usd or 0.0

        run_latency = None
        if run_invocations:
            run_latency = sum(inv.latency_ms for inv in run_invocations) // len(run_invocations)

        if run_confidence is not None:
            confidence_sum += run_confidence
            confidence_count += 1

        stages.append(
            StageMetrics(
                stage=run.workflow_stage,
                status=run.status,
                duration_ms=_duration_ms(run.started_at, run.completed_at),
                confidence_score=run_confidence,
                model=run_model,
                provider=run_provider,
                prompt_tokens=sum(inv.prompt_tokens or 0 for inv in run_invocations),
                completion_tokens=sum(inv.completion_tokens or 0 for inv in run_invocations),
                total_tokens=run_tokens,
                estimated_cost_usd=round(run_cost, 6),
                latency_ms=run_latency,
                retry_count=sum(inv.retry_count for inv in run_invocations),
            )
        )

    if confidence_count > 0:
        overall_confidence = round(confidence_sum / confidence_count, 3)

    # Extract review, repository impact, and recommendations from stage results
    review_results = _extract_review_results(stage_results.get("engineering_review") or {})
    repository_impact = _extract_repository_impact(stage_results)
    recommendations = _extract_recommendations(stage_results)

    # Workflow duration
    workflow_completed_at = workflow.updated_at if workflow.status in ("completed", "approved") else None
    duration_ms = _duration_ms(workflow.created_at, workflow_completed_at)

    # Approved by (resolve username if possible)
    approved_by: str | None = None
    if workflow.approved_by_user_id:
        approver = await db.get(User, workflow.approved_by_user_id)
        if approver:
            approved_by = approver.email or str(approver.id)

    return ExecutiveReportData(
        workflow_id=str(workflow.id),
        workflow_title=workflow.title,
        original_prompt=workflow.original_prompt,
        workflow_type=workflow.workflow_type,
        status=workflow.status,
        current_stage=workflow.current_stage,
        created_at=_iso(workflow.created_at) or "",
        completed_at=_iso(workflow_completed_at),
        duration_ms=duration_ms,
        approved_by=approved_by,
        total_tokens=total_tokens,
        total_cost_usd=round(total_cost, 6),
        total_llm_calls=total_calls,
        primary_model=primary_model,
        primary_provider=primary_provider,
        overall_confidence=overall_confidence,
        stages=stages,
        repository_impact=repository_impact,
        review_results=review_results,
        recommendations=recommendations,
    )


@router.get(
    "/{report_id}/executive-html",
    response_class=HTMLResponse,
    summary="Render the executive report as a self-contained HTML dashboard",
)
async def get_executive_report_html(
    report_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Return the executive report rendered as a complete, self-contained HTML
    document suitable for download, sharing, or iframe embedding."""
    # Reuse the data endpoint's logic to get structured data
    data = await get_executive_report_data(report_id, user, db)
    html = render_executive_html(data.model_dump())
    return HTMLResponse(content=html)
