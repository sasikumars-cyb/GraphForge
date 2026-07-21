"""FastAPI routers for API v1."""

from fastapi import APIRouter

from app.api.v1.routers.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)

__all__ = ["api_router"]
