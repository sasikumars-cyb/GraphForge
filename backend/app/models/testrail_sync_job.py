"""The `testrail_sync_jobs` table — one row per `POST /testrail/projects/
{id}/sync` call, tracking the background sync pipeline's progress. Also
what prevents two syncs for the same TestRail project racing each other
(see `app.api.v1.routers.testrail`).

Keyed directly by `testrail_project_id` rather than a foreign key — unlike
`indexing_jobs` (which FKs to a persisted `repositories` row this app
tracks), there is no persisted "tracked TestRail project" table in this
pass: the live project list always comes straight from TestRail's own API
(see `app.services.testrail_service.list_available_projects`), matching
the "no code linkage, index cases standalone" scope this was built
against.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TestRailSyncJob(Base):
    __tablename__ = "testrail_sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    testrail_project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Denormalized (not read back from TestRail at poll time) so the sync
    # history/status UI has a display name even if the project is later
    # renamed or removed on the TestRail side.
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # "pending" | "running" | "completed" | "failed"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # {"suites": N, "sections": N, "cases": N} once status is "completed" -
    # see testrail_service.run_sync.
    result_summary: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
