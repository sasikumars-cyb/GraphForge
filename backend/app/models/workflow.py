"""The `workflows` table — one row per SDLC workflow.

A Workflow groups a sequence of agent Runs into a coherent engineering
lifecycle: Planning → Development → Testing → Review.  Each Run within
the workflow references its stage and optionally the previous run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.run import Run


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    title: Mapped[str] = mapped_column(String(512), nullable=False)

    # "planning" | "development" | "testing" | "review" | "completed"
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="planning")

    # "in_progress" | "completed"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")

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
