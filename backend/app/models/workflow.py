"""The `workflows` table — one row per SDLC workflow.

A Workflow groups a sequence of agent Runs into a coherent engineering
lifecycle: Planning → Development → Testing → Review.  Each Run within
the workflow references its stage and optionally the previous run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.run import Run


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    title: Mapped[str] = mapped_column(String(512), nullable=False)

    # "planning" | "development" | "testing" | "review" | "completed" — meaning
    # depends on workflow_type (see WORKFLOW_TYPE_STAGES in workflow_service.py)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="planning")

    # "in_progress" | "completed" | "awaiting_approval" | "approved" | "rejected"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")

    # Selects which stage sequence this workflow runs — see
    # workflow_service.WORKFLOW_TYPE_STAGES. "legacy_sdlc" is the frozen,
    # untouched 4-stage sequence every workflow used before this column
    # existed; the column default keeps every pre-existing row on it
    # unchanged. "planning" is the new, human-gated, no-repo-writes
    # sequence and is what NewWorkflowPage creates going forward.
    workflow_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="legacy_sdlc", server_default="legacy_sdlc"
    )

    # Set only for a future Auto Execution workflow started "from an
    # approved blueprint" — links back to the Planning workflow whose
    # development-stage output it executes. Unused until that workflow
    # type exists; present now so the column ships with this migration.
    source_workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    runs: Mapped[list[Run]] = relationship(
        "Run",
        back_populates="workflow",
        order_by="Run.created_at",
        foreign_keys="Run.workflow_id",
    )
