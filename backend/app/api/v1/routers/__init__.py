"""FastAPI routers for API v1."""

from fastapi import APIRouter

from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.github import router as github_router
from app.api.v1.routers.health import router as health_router
from app.api.v1.routers.oauth import router as oauth_router
from app.api.v1.routers.pull_requests import router as pull_requests_router
from app.api.v1.routers.repositories import router as repositories_router
from app.api.v1.routers.webhooks import router as webhooks_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(oauth_router)
api_router.include_router(github_router)
api_router.include_router(repositories_router)
api_router.include_router(pull_requests_router)
api_router.include_router(webhooks_router)

__all__ = ["api_router"]
