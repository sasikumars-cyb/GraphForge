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
    # Nullable: rows written before this column existed have no plan to
    # show. Ephemeral no longer - persisted so `publish-review` can post
    # it without re-invoking the LLM (see app.ai.services.persistence).
    release_coordination_plan: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence_reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # -- General-purpose review fields (nullable: rows written before these
    # columns existed have no review to show for them) -----------------
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    merge_recommendation: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default=None
    )
    findings: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    architecture_observations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    maintainability_observations: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    reliability_observations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    testing_review: Mapped[str] = mapped_column(Text, nullable=False, default="")
    documentation_review: Mapped[str] = mapped_column(Text, nullable=False, default="")
    positive_findings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    suggested_improvements: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # -- Per-category scores (additive) ----------------------------------
    security_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    testing_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    documentation_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    architecture_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    performance_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    maintainability_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # -- Per-file review cards (additive) ---------------------------------
    file_reviews: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
