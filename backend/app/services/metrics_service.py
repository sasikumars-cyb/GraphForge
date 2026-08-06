"""Metrics aggregation for the in-app Reports dashboard.

Aggregates the same data `scripts/generate_report.py` queries directly
against Postgres/Neo4j, but through the running app's own DB session and
graph driver, scoped to the requesting user (`scope="user"`) or
unrestricted (`scope="global"`).

Ownership rules mirror `workflow_service.list_workflows`: a workflow (or a
run/LLM invocation attributed to one) is "owned" by a user if its
`user_id` matches or is NULL (legacy rows created before ownership
tracking existed are visible to any authenticated user, never hidden).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from neo4j import AsyncDriver
from sqlalchemy import and_, or_, select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_invocation import LLMInvocation
from app.models.repository import Repository
from app.models.run import Run
from app.models.workflow import Workflow
from app.schemas.metrics import (
    CostByDayPoint,
    MetricsOverview,
    MetricsReportResponse,
    ModelUsage,
    ProviderCost,
    RepositoryComponentCount,
    RunStageOutcome,
    StageCost,
    WorkflowLLMUsageResponse,
    WorkflowStageLLMUsage,
    WorkflowSummary,
)
from app.services import workflow_service

Scope = Literal["user", "global"]


def _workflow_ownership(user_id: uuid.UUID):
    return or_(Workflow.user_id == user_id, Workflow.user_id.is_(None))


def _run_ownership(user_id: uuid.UUID):
    """Run.workflow_id/Run.user_id must already be selectable — caller is
    responsible for outerjoin-ing Run to Workflow first."""
    return or_(
        and_(Run.workflow_id.is_(None), or_(Run.user_id == user_id, Run.user_id.is_(None))),
        and_(Run.workflow_id.isnot(None), _workflow_ownership(user_id)),
    )


async def _repository_scope(
    db: AsyncSession, scope: Scope, user_id: uuid.UUID
) -> list[Repository]:
    query = select(Repository)
    if scope == "user":
        query = query.where(Repository.user_id == user_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def _overview_counts(
    db: AsyncSession, scope: Scope, user_id: uuid.UUID
) -> tuple[int, int, int]:
    """Returns (total_workflows, completed_workflows, completed_runs)."""
    wf_query = select(sa_func.count(Workflow.id))
    wf_completed_query = select(sa_func.count(Workflow.id)).where(Workflow.status == "completed")
    if scope == "user":
        clause = _workflow_ownership(user_id)
        wf_query = wf_query.where(clause)
        wf_completed_query = wf_completed_query.where(clause)

    run_query = (
        select(sa_func.count(Run.id))
        .outerjoin(Workflow, Run.workflow_id == Workflow.id)
        .where(Run.status == "completed")
    )
    if scope == "user":
        run_query = run_query.where(_run_ownership(user_id))

    total_workflows = (await db.execute(wf_query)).scalar() or 0
    completed_workflows = (await db.execute(wf_completed_query)).scalar() or 0
    completed_runs = (await db.execute(run_query)).scalar() or 0
    return total_workflows, completed_workflows, completed_runs


def _llm_base_query(scope: Scope, user_id: uuid.UUID):
    query = select(LLMInvocation).join(Run, LLMInvocation.run_id == Run.id)
    if scope == "user":
        query = query.outerjoin(Workflow, Run.workflow_id == Workflow.id).where(
            _run_ownership(user_id)
        )
    return query


async def _llm_totals(
    db: AsyncSession, scope: Scope, user_id: uuid.UUID
) -> tuple[int, int, float, float]:
    """Returns (total_calls, total_tokens, total_cost_usd, avg_latency_ms)."""
    query = select(
        sa_func.count(LLMInvocation.id),
        sa_func.coalesce(sa_func.sum(LLMInvocation.total_tokens), 0),
        sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0),
        sa_func.coalesce(sa_func.avg(LLMInvocation.latency_ms), 0.0),
    ).select_from(LLMInvocation).join(Run, LLMInvocation.run_id == Run.id)
    if scope == "user":
        query = query.outerjoin(Workflow, Run.workflow_id == Workflow.id).where(
            _run_ownership(user_id)
        )
    row = (await db.execute(query)).one()
    calls, tokens, cost, latency = row
    return int(calls or 0), int(tokens or 0), float(cost or 0.0), round(float(latency or 0.0))


async def _cost_by_day(
    db: AsyncSession, scope: Scope, user_id: uuid.UUID, window_days: int
) -> list[CostByDayPoint]:
    since = datetime.now(UTC) - timedelta(days=window_days)
    day_col = sa_func.date_trunc("day", LLMInvocation.started_at)
    query = (
        select(
            day_col.label("day"),
            sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0),
            sa_func.coalesce(sa_func.sum(LLMInvocation.total_tokens), 0),
        )
        .select_from(LLMInvocation)
        .join(Run, LLMInvocation.run_id == Run.id)
        .where(LLMInvocation.started_at >= since)
        .group_by(day_col)
        .order_by(day_col)
    )
    if scope == "user":
        query = query.outerjoin(Workflow, Run.workflow_id == Workflow.id).where(
            _run_ownership(user_id)
        )
    rows = (await db.execute(query)).all()
    return [
        CostByDayPoint(day=day.date().isoformat(), cost_usd=float(cost or 0), tokens=int(tokens or 0))
        for day, cost, tokens in rows
    ]


async def _cost_by_provider(
    db: AsyncSession, scope: Scope, user_id: uuid.UUID
) -> list[ProviderCost]:
    query = (
        select(
            LLMInvocation.provider,
            sa_func.count(LLMInvocation.id),
            sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0),
            sa_func.coalesce(sa_func.sum(LLMInvocation.total_tokens), 0),
        )
        .select_from(LLMInvocation)
        .join(Run, LLMInvocation.run_id == Run.id)
        .group_by(LLMInvocation.provider)
        .order_by(sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0).desc())
    )
    if scope == "user":
        query = query.outerjoin(Workflow, Run.workflow_id == Workflow.id).where(
            _run_ownership(user_id)
        )
    rows = (await db.execute(query)).all()
    return [
        ProviderCost(provider=provider or "unknown", calls=int(calls), cost_usd=float(cost or 0), tokens=int(tokens or 0))
        for provider, calls, cost, tokens in rows
    ]


async def _cost_by_stage(db: AsyncSession, scope: Scope, user_id: uuid.UUID) -> list[StageCost]:
    stage_col = sa_func.coalesce(LLMInvocation.stage, "unknown")
    query = (
        select(
            stage_col,
            sa_func.count(LLMInvocation.id),
            sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0),
            sa_func.coalesce(sa_func.sum(LLMInvocation.total_tokens), 0),
        )
        .select_from(LLMInvocation)
        .join(Run, LLMInvocation.run_id == Run.id)
        .group_by(stage_col)
        .order_by(sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0).desc())
    )
    if scope == "user":
        query = query.outerjoin(Workflow, Run.workflow_id == Workflow.id).where(
            _run_ownership(user_id)
        )
    rows = (await db.execute(query)).all()
    return [
        StageCost(stage=stage, calls=int(calls), cost_usd=float(cost or 0), tokens=int(tokens or 0))
        for stage, calls, cost, tokens in rows
    ]


async def _model_usage(db: AsyncSession, scope: Scope, user_id: uuid.UUID) -> list[ModelUsage]:
    query = (
        select(
            LLMInvocation.model,
            LLMInvocation.provider,
            sa_func.count(LLMInvocation.id),
            sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0),
        )
        .select_from(LLMInvocation)
        .join(Run, LLMInvocation.run_id == Run.id)
        .group_by(LLMInvocation.model, LLMInvocation.provider)
        .order_by(sa_func.count(LLMInvocation.id).desc())
        .limit(10)
    )
    if scope == "user":
        query = query.outerjoin(Workflow, Run.workflow_id == Workflow.id).where(
            _run_ownership(user_id)
        )
    rows = (await db.execute(query)).all()
    return [
        ModelUsage(model=model, provider=provider, calls=int(calls), cost_usd=float(cost or 0))
        for model, provider, calls, cost in rows
    ]


async def _run_success_by_stage(
    db: AsyncSession, scope: Scope, user_id: uuid.UUID
) -> list[RunStageOutcome]:
    from sqlalchemy import case

    stage_col = sa_func.coalesce(Run.workflow_stage, Run.goal)
    query = (
        select(
            stage_col.label("stage"),
            sa_func.count(Run.id),
            sa_func.sum(case((Run.status == "completed", 1), else_=0)),
            sa_func.sum(case((Run.status == "failed", 1), else_=0)),
        )
        .outerjoin(Workflow, Run.workflow_id == Workflow.id)
        .group_by(stage_col)
        .order_by(sa_func.count(Run.id).desc())
    )
    if scope == "user":
        query = query.where(_run_ownership(user_id))
    rows = (await db.execute(query)).all()
    return [
        RunStageOutcome(stage=stage, total=int(total), succeeded=int(succeeded or 0), failed=int(failed or 0))
        for stage, total, succeeded, failed in rows
    ]


async def _recent_workflows(
    db: AsyncSession, scope: Scope, user_id: uuid.UUID, limit: int = 20
) -> list[WorkflowSummary]:
    cost_col = sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0)
    tokens_col = sa_func.coalesce(sa_func.sum(LLMInvocation.total_tokens), 0)
    query = (
        select(
            Workflow.id,
            Workflow.title,
            Workflow.original_prompt,
            Workflow.status,
            Workflow.current_stage,
            Workflow.workflow_type,
            Workflow.created_at,
            Workflow.updated_at,
            cost_col,
            tokens_col,
        )
        .select_from(Workflow)
        .outerjoin(Run, Run.workflow_id == Workflow.id)
        .outerjoin(LLMInvocation, LLMInvocation.run_id == Run.id)
        .group_by(Workflow.id)
        .order_by(Workflow.created_at.desc())
        .limit(limit)
    )
    if scope == "user":
        query = query.where(_workflow_ownership(user_id))
    rows = (await db.execute(query)).all()
    summaries: list[WorkflowSummary] = []
    for (
        wf_id,
        title,
        original_prompt,
        status,
        current_stage,
        workflow_type,
        created_at,
        updated_at,
        cost,
        tokens,
    ) in rows:
        summaries.append(
            WorkflowSummary(
                id=str(wf_id),
                title=title or (original_prompt or "Untitled")[:80],
                status=status,
                current_stage=current_stage,
                workflow_type=workflow_type,
                created_at=created_at.isoformat(),
                updated_at=updated_at.isoformat(),
                cost_usd=float(cost or 0),
                tokens=int(tokens or 0),
            )
        )
    return summaries


async def _graph_metrics(
    driver: AsyncDriver, repositories: list[Repository], scope: Scope
) -> tuple[list[RepositoryComponentCount], int, int]:
    """Returns (repository_components, total_nodes, total_edges)."""
    id_to_name = {str(r.id): (r.full_name or r.name) for r in repositories}
    repo_ids = list(id_to_name.keys())

    if scope == "user" and not repo_ids:
        # No repositories owned by this user — nothing to query, and an
        # empty `IN` list is invalid Cypher.
        return [], 0, 0

    async with driver.session() as session:
        if scope == "user":
            components_result = await session.run(
                "MATCH (n) WHERE n.repository_id IN $repo_ids "
                "RETURN n.repository_id AS repository_id, count(n) AS components "
                "ORDER BY components DESC",
                repo_ids=repo_ids,
            )
        else:
            components_result = await session.run(
                "MATCH (n) WHERE n.repository_id IS NOT NULL "
                "RETURN n.repository_id AS repository_id, count(n) AS components "
                "ORDER BY components DESC"
            )
        component_records = [record async for record in components_result]

        if scope == "user":
            nodes_result = await session.run(
                "MATCH (n) WHERE n.repository_id IN $repo_ids RETURN count(n) AS nodes",
                repo_ids=repo_ids,
            )
            edges_result = await session.run(
                "MATCH (a)-[r]->(b) WHERE a.repository_id IN $repo_ids RETURN count(r) AS edges",
                repo_ids=repo_ids,
            )
        else:
            nodes_result = await session.run("MATCH (n) RETURN count(n) AS nodes")
            edges_result = await session.run("MATCH ()-[r]->() RETURN count(r) AS edges")

        nodes_record = await nodes_result.single()
        edges_record = await edges_result.single()

    components = [
        RepositoryComponentCount(
            repository_id=record["repository_id"],
            name=id_to_name.get(record["repository_id"], record["repository_id"]),
            components=int(record["components"]),
        )
        for record in component_records
        if scope == "global" or record["repository_id"] in id_to_name
    ]
    total_nodes = int(nodes_record["nodes"]) if nodes_record else 0
    total_edges = int(edges_record["edges"]) if edges_record else 0
    return components, total_nodes, total_edges


async def get_metrics_report(
    db: AsyncSession,
    driver: AsyncDriver,
    user_id: uuid.UUID,
    scope: Scope,
    window_days: int,
) -> MetricsReportResponse:
    repositories = await _repository_scope(db, scope, user_id)
    total_workflows, completed_workflows, completed_runs = await _overview_counts(
        db, scope, user_id
    )
    total_calls, total_tokens, total_cost, avg_latency = await _llm_totals(db, scope, user_id)
    repository_components, total_nodes, total_edges = await _graph_metrics(
        driver, repositories, scope
    )

    overview = MetricsOverview(
        total_workflows=total_workflows,
        completed_workflows=completed_workflows,
        completed_runs=completed_runs,
        total_llm_calls=total_calls,
        total_tokens=total_tokens,
        total_cost_usd=round(total_cost, 4),
        avg_latency_ms=avg_latency,
        indexed_repositories=len(repositories),
        total_graph_nodes=total_nodes,
        total_graph_edges=total_edges,
    )

    return MetricsReportResponse(
        scope=scope,
        generated_at=datetime.now(UTC).isoformat(),
        window_days=window_days,
        overview=overview,
        cost_by_day=await _cost_by_day(db, scope, user_id, window_days),
        cost_by_provider=await _cost_by_provider(db, scope, user_id),
        cost_by_stage=await _cost_by_stage(db, scope, user_id),
        model_usage=await _model_usage(db, scope, user_id),
        run_success_by_stage=await _run_success_by_stage(db, scope, user_id),
        repository_components=repository_components,
        recent_workflows=await _recent_workflows(db, scope, user_id),
    )


async def get_workflow_llm_usage(
    db: AsyncSession, workflow_id: uuid.UUID, user_id: uuid.UUID
) -> WorkflowLLMUsageResponse:
    """Per-stage LLM usage for one workflow - lets a user see which stage
    (Planning, Development, Testing, ...) actually drove that workflow's
    cost/latency, and compare stages against each other, rather than only
    ever seeing the workflow's rolled-up total. `workflow_service.
    get_workflow`'s ownership check (404-not-403) is reused as-is - this
    is the same "own it or it doesn't exist" rule every other per-workflow
    read already enforces."""
    workflow = await workflow_service.get_workflow(db, workflow_id, user_id=user_id)

    stage_col = sa_func.coalesce(Run.workflow_stage, Run.goal)
    totals_query = (
        select(
            stage_col.label("stage"),
            sa_func.count(LLMInvocation.id),
            sa_func.coalesce(sa_func.sum(LLMInvocation.prompt_tokens), 0),
            sa_func.coalesce(sa_func.sum(LLMInvocation.completion_tokens), 0),
            sa_func.coalesce(sa_func.sum(LLMInvocation.total_tokens), 0),
            sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0),
            sa_func.coalesce(sa_func.avg(LLMInvocation.latency_ms), 0.0),
        )
        .select_from(LLMInvocation)
        .join(Run, LLMInvocation.run_id == Run.id)
        .where(Run.workflow_id == workflow_id)
        .group_by(stage_col)
        .order_by(sa_func.coalesce(sa_func.sum(LLMInvocation.estimated_cost_usd), 0.0).desc())
    )
    totals_rows = (await db.execute(totals_query)).all()

    # A separate query for distinct models per stage - a stage can call
    # more than one model (a provider fallback, or more than one agent
    # sharing a `workflow_stage`), and that's exactly the kind of thing
    # this view exists to surface, not average away.
    models_query = (
        select(stage_col.label("stage"), LLMInvocation.model)
        .select_from(LLMInvocation)
        .join(Run, LLMInvocation.run_id == Run.id)
        .where(Run.workflow_id == workflow_id)
        .distinct()
    )
    models_by_stage: dict[str, list[str]] = {}
    for stage, model in (await db.execute(models_query)).all():
        if model:
            models_by_stage.setdefault(stage, []).append(model)

    stages = [
        WorkflowStageLLMUsage(
            stage=stage,
            models=sorted(models_by_stage.get(stage, [])),
            calls=int(calls),
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            total_tokens=int(total_tokens or 0),
            cost_usd=float(cost or 0.0),
            avg_latency_ms=round(float(latency or 0.0)),
        )
        for stage, calls, input_tokens, output_tokens, total_tokens, cost, latency in totals_rows
    ]

    return WorkflowLLMUsageResponse(
        workflow_id=str(workflow.id),
        workflow_title=workflow.title or "Untitled",
        stages=stages,
    )
