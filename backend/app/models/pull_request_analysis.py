"""The `pull_request_analyses` table — one row per pull request, holding
its most recently computed deterministic impact analysis (Phase 7).
`pull_request_id` is unique: re-running `POST /pull-requests/{id}/analyze`
replaces the row rather than keeping history, matching how re-indexing
(`app.models.indexing_job`) always replaces the prior graph rather than
diffing against it.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PullRequestAnalysis(Base):
    __tablename__ = "pull_request_analyses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # "LOW" | "MEDIUM" | "HIGH" - see app.analysis.services.risk_classifier.
    risk: Mapped[str] = mapped_column(String(10), nullable=False)

    # Each JSON column is a list[dict] shaped like app.schemas.analysis's
    # ImpactedNodeResponse (directly/indirectly_impacted_services,
    # impacted_apis, impacted_topics, impacted_libraries) or
    # DependencyPathResponse (dependency_paths) - written directly from
    # app.analysis.models.impact.ImpactAnalysisResult via dataclasses.asdict.
    directly_impacted_services: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    indirectly_impacted_services: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    impacted_apis: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    impacted_topics: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    impacted_libraries: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    dependency_paths: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
