"""The ``pull_request_ai_analyses`` table — one row per pull request,
holding its most recently computed AI-enriched analysis result.

``pull_request_id`` is unique: re-running AI analysis replaces the prior
row rather than keeping history, mirroring the deterministic
``pull_request_analyses`` table pattern.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PullRequestAIAnalysis(Base):
    __tablename__ = "pull_request_ai_analyses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    executive_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    breaking_changes: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    migration_advice: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    suggested_reviewers: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    regression_tests: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence_reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
