"""FastAPI routers for API v1."""

from fastapi import APIRouter

from app.api.v1.routers.agent_runs import router as agent_runs_router
from app.api.v1.routers.ai_analysis import router as ai_analysis_router
from app.api.v1.routers.ai_workspace import router as ai_workspace_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.github import router as github_router
from app.api.v1.routers.health import router as health_router
from app.api.v1.routers.oauth import router as oauth_router
from app.api.v1.routers.pull_requests import router as pull_requests_router
from app.api.v1.routers.repositories import router as repositories_router
from app.api.v1.routers.system import router as system_router
from app.api.v1.routers.webhooks import router as webhooks_router
from app.api.v1.routers.tools import router as tools_router
from app.api.v1.routers.workflows import router as workflows_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(oauth_router)
api_router.include_router(github_router)
api_router.include_router(repositories_router)
api_router.include_router(pull_requests_router)
api_router.include_router(ai_analysis_router)
api_router.include_router(ai_workspace_router)
api_router.include_router(agent_runs_router)
api_router.include_router(workflows_router)
api_router.include_router(system_router)
api_router.include_router(webhooks_router)
api_router.include_router(tools_router)

__all__ = ["api_router"]
