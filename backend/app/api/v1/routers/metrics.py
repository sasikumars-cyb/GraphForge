"""Metrics/Reports endpoint — aggregated activity data for the in-app
Reports dashboard. Backs the same sections `scripts/generate_report.py`
produces as a standalone HTML file, so the page no longer requires
running that script manually.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.database.session import get_db_session
from app.graph.session import get_driver
from app.models.user import User
from app.schemas.metrics import MetricsReportResponse
from app.services.metrics_service import get_metrics_report

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
