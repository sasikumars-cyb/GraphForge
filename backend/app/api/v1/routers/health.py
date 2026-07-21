"""Liveness endpoint.

Deliberately has no dependency on the database or any external service — it
answers "is the process up", which is what Docker Compose / an orchestrator's
healthcheck needs. A separate `/health/ready` (checking DB connectivity) can
be added once there's a database to check.
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
async def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", environment=settings.environment)
