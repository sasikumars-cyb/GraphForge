"""FastAPI routers for API v1."""

from fastapi import APIRouter

from app.api.v1.routers.agent_runs import router as agent_runs_router
from app.api.v1.routers.ai_analysis import router as ai_analysis_router
from app.api.v1.routers.ai_workspace import router as ai_workspace_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.calibration import router as calibration_router
from app.api.v1.routers.documentation import router as documentation_router
from app.api.v1.routers.engineering_sessions import router as engineering_sessions_router
from app.api.v1.routers.github import router as github_router
from app.api.v1.routers.google_drive import router as google_drive_router
from app.api.v1.routers.health import router as health_router
from app.api.v1.routers.jira import router as jira_router
from app.api.v1.routers.knowledge import router as knowledge_router
from app.api.v1.routers.metrics import router as metrics_router
from app.api.v1.routers.oauth import router as oauth_router
from app.api.v1.routers.oauth_apps import router as oauth_apps_router
from app.api.v1.routers.pull_requests import router as pull_requests_router
from app.api.v1.routers.reports import router as reports_router
from app.api.v1.routers.repositories import router as repositories_router
from app.api.v1.routers.system import router as system_router
from app.api.v1.routers.test_case_uploads import router as test_case_uploads_router
from app.api.v1.routers.testrail import router as testrail_router
from app.api.v1.routers.tools import router as tools_router
from app.api.v1.routers.webhooks import router as webhooks_router
from app.api.v1.routers.workflows import router as workflows_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(oauth_router)
api_router.include_router(github_router)
api_router.include_router(google_drive_router)
api_router.include_router(oauth_apps_router)
api_router.include_router(repositories_router)
api_router.include_router(pull_requests_router)
api_router.include_router(ai_analysis_router)
api_router.include_router(ai_workspace_router)
api_router.include_router(agent_runs_router)
api_router.include_router(workflows_router)
api_router.include_router(system_router)
api_router.include_router(webhooks_router)
api_router.include_router(tools_router)
api_router.include_router(knowledge_router)
api_router.include_router(calibration_router)
api_router.include_router(jira_router)
api_router.include_router(testrail_router)
api_router.include_router(test_case_uploads_router)
api_router.include_router(engineering_sessions_router)
api_router.include_router(metrics_router)
api_router.include_router(reports_router)
api_router.include_router(documentation_router)

__all__ = ["api_router"]
