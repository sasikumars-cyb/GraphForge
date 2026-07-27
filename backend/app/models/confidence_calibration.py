"""The `confidence_calibrations` table — one row per (AgentStep, human
decision) pair, recorded the moment a human approves or rejects a
workflow's blueprint.

Exists because a confidence_score with nothing checking it against a real
outcome is decorative — ROADMAP.md's risk register calls this out
explicitly as a blocker: "Confidence calibration tracking is not optional
past Phase 2." A human's approve/reject decision is the one real-world
signal already being captured (workflow.status) that a confidence score
can be checked against: if "high confidence" plans get rejected as often
as "low confidence" ones, the score isn't meaning what it claims to.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ConfidenceCalibration(Base):
    __tablename__ = "confidence_calibrations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)

    # "approved" | "rejected" — the human decision this score is being
    # checked against. Workflow-level only (the /approve, /reject
    # endpoints) since that's the one decision every Planning workflow
    # reaches, unlike per-stage gates which don't have a persisted
    # accept/reject of their own today.
    decision: Mapped[str] = mapped_column(String(16), nullable=False)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
