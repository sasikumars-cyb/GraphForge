"""The `workflow_reports` table — one row per generated high-level report.

A WorkflowReport is created (status="pending") the moment a human approves
a Planning workflow's blueprint, then filled in by the report_generation
agent running in the background (see app.agents.report_generation and the
dispatch in api/v1/routers/workflows.py's approve_workflow). Reports is a
read-only surface: nothing ever edits a report's content once generated,
only regenerates a new row (kept, not overwritten, so past reports stay
exactly what they were when a decision was made against them).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.run import Run
    from app.models.workflow import Workflow


class WorkflowReport(Base):
    __tablename__ = "workflow_reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The report_generation agent's own Run — nullable because the row is
    # created (status="pending") before RunCoordinator has resolved/created
    # it; set as soon as that happens. Kept for audit/observability
    # linkage (LLM cost, evidence, confidence) via the same Run/AgentStep
    # trail every other agent output already has - a report is not a
    # special case, just another agent's output rendered differently.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # "pending" | "completed" | "failed" - see module docstring.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    # Self-contained HTML (inline CSS, no external resources) - rendered
    # client-side inside a sandboxed iframe, never trusted as same-origin
    # markup (see frontend/src/pages/ReportsPage.tsx). Null until status
    # moves to "completed".
    #
    # Report V2 Phase 2 (ADR 0024): kept only as a plain-text/legacy
    # fallback (a short LLM-authored summary, never the primary rendering
    # path) — the frontend's real Reports page renders `view_model`
    # below through real deterministic components, not this HTML blob.
    # Never null-but-view_model-present or vice versa in a Phase-2-era
    # row; both are set together by the report_generation agent.
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # `app.agents.report_generation.view_model.ReportViewModel`, serialized
    # (dataclasses.asdict + enum .value coercion — see
    # `view_model.to_json_dict`). The authoritative, deterministic
    # representation of the report — every structural decision (which
    # hypotheses appear, what synthesis/verification badges show, whether
    # a section is available/degraded/unavailable) was already made
    # before this JSON was produced; nothing downstream reinterprets it.
    # Null for any report generated before this column existed.
    view_model: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[Workflow] = relationship("Workflow")
    run: Mapped[Run | None] = relationship("Run")
