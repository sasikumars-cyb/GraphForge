"""The `agent_runs` table — one row per Orchestrator run.

A Run is created when `POST /api/v1/agent-runs` is received and updated
as the selected agent(s) execute. The `steps` relationship holds one
AgentStep per agent that executed in this run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.agent_step import AgentStep
    from app.models.workflow import Workflow


class Run(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Resolved subject fields — mirrors the Subject DTO from _contract.py.
    # Indexed: GET /api/v1/agent-runs?subject_id=... is a documented,
    # real filter (e.g. "all runs for this PR/workflow subject").
    subject_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    goal: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # "queued" | "running" | "completed" | "partial" | "failed"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Workflow linkage (nullable — standalone runs have no workflow) ---
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )

    steps: Mapped[list[AgentStep]] = relationship(
        "AgentStep",
        back_populates="run",
        order_by="AgentStep.created_at",
        cascade="all, delete-orphan",
    )
    workflow: Mapped[Workflow | None] = relationship(
        "Workflow",
        back_populates="runs",
        foreign_keys=[workflow_id],
    )
    previous_run: Mapped[Run | None] = relationship(
        "Run",
        remote_side=[id],
        foreign_keys=[previous_run_id],
        uselist=False,
    )
