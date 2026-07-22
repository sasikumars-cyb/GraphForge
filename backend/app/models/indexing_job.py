"""The `indexing_jobs` table — one row per `POST /repositories/{id}/index`
call, tracking the background pipeline's progress. Also what prevents two
indexing runs for the same repository racing each other (see
`app.api.v1.routers.repositories`).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class IndexingJob(Base):
    __tablename__ = "indexing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # "pending" | "running" | "completed" | "failed"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Counts of what was discovered (controllers, endpoints, ...) - only
    # set once status is "completed". See indexing_service._summarize.
    result_summary: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
