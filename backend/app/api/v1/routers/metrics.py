"""Metrics/Reports endpoint — aggregated activity data for the in-app
Reports dashboard. Backs the same sections `scripts/generate_report.py`
produces as a standalone HTML file, so the page no longer requires
running that script manually.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.graph.session import get_driver
from app.models.user import User
from app.schemas.metrics import MetricsReportResponse, WorkflowLLMUsageResponse
from app.services.metrics_service import get_metrics_report, get_workflow_llm_usage

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/report", response_model=MetricsReportResponse, summary="Reports dashboard data")
async def get_report(
    scope: Literal["user", "global"] = Query(
        "user", description="'user' scopes to the caller's own data; 'global' is org-wide."
    ),
    window_days: int = Query(30, ge=1, le=365, description="Size of the daily cost/token series."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> MetricsReportResponse:
    return await get_metrics_report(
        db=db,
        driver=get_driver(),
        user_id=current_user.id,
        scope=scope,
        window_days=window_days,
    )


@router.get(
    "/workflows/{workflow_id}/llm-usage",
    response_model=WorkflowLLMUsageResponse,
    summary="Per-stage LLM usage (model, tokens, cost, latency, call count) for one workflow",
)
async def get_workflow_llm_usage_endpoint(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> WorkflowLLMUsageResponse:
    try:
        wid = uuid.UUID(workflow_id)
    except ValueError as exc:
        raise NotFoundError(f"Invalid workflow_id: {workflow_id}") from exc
    return await get_workflow_llm_usage(db, wid, current_user.id)
