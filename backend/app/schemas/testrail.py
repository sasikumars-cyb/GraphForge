"""Request/response schemas for the TestRail project list and sync job API."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TestRailProjectResponse(BaseModel):
    """One project from TestRail's live list, overlaid with this project's
    most recent sync status — mirrors AvailableRepository's
    live-list-plus-overlay shape."""

    id: int
    name: str
    last_sync_status: str | None = None
    last_synced_at: datetime | None = None
    case_count: int | None = None


class TestRailSyncRequest(BaseModel):
    """Body for POST /testrail/projects/{id}/sync. `project_name` is
    supplied by the caller (already known from GET /projects) rather than
    re-fetched here, same as RepositorySelection's client-supplies-the-
    metadata-it-already-has convention."""

    project_name: str


class TestRailSyncJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    testrail_project_id: int
    project_name: str
    status: str
    error_message: str | None = None
    result_summary: dict[str, int] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
