"""TestRail project listing and case sync — same trigger-then-poll shape
as repository indexing (app.api.v1.routers.repositories), but keyed
directly by TestRail's own live project id rather than a locally
persisted "tracked project" row (see testrail_sync_job.py's docstring for
why).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import ConflictError, NotFoundError
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.testrail import (
    TestRailProjectResponse,
    TestRailSyncJobResponse,
    TestRailSyncRequest,
)
from app.services.testrail_service import (
    create_sync_job,
    get_latest_sync_job,
    get_pending_or_running_job,
    list_available_projects,
    run_sync_job,
)

router = APIRouter(prefix="/testrail", tags=["testrail"])


@router.get("/projects", response_model=list[TestRailProjectResponse])
async def list_projects(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, object]]:
    """Live list from TestRail, overlaid with each project's most recent
    sync status — TestRail's connection is shared/install-wide (like
    Jira's), so this isn't owned by or filtered to the calling user."""
    return await list_available_projects(db)


@router.post("/projects/{project_id}/sync", response_model=TestRailSyncJobResponse, status_code=202)
async def sync_project(
    project_id: int,
    body: TestRailSyncRequest,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TestRailSyncJobResponse:
    """Schedules a sync and returns immediately — the run itself happens
    in a background task; poll GET .../sync for its status."""
    existing = await get_pending_or_running_job(db, project_id)
    if existing is not None:
        raise ConflictError("A sync is already pending or running for this project.")

    job = await create_sync_job(db, project_id, body.project_name)
    background_tasks.add_task(run_sync_job, job.id)
    return TestRailSyncJobResponse.model_validate(job)


@router.get("/projects/{project_id}/sync", response_model=TestRailSyncJobResponse)
async def get_sync_status(
    project_id: int,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TestRailSyncJobResponse:
    """The most recently created sync job for this project - lets a client
    that just called POST .../sync poll for completion."""
    job = await get_latest_sync_job(db, project_id)
    if job is None:
        raise NotFoundError("No sync has been run for this TestRail project yet.")
    return TestRailSyncJobResponse.model_validate(job)
