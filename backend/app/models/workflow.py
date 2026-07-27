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

    # AI-generated, concise (5-10 word) title — see
    # app.agents.title_generation.generate_title(). Generated once at
    # creation time from `original_prompt` and persisted; never
    # regenerated. Falls back to a truncated version of the prompt if
    # generation fails, so this is always short even when it isn't a
    # real AI title.
    title: Mapped[str] = mapped_column(Text, nullable=False)

    # The complete, unmodified engineering objective the user submitted —
    # what `title` used to hold before AI title generation existed. TEXT,
    # not VARCHAR(512): routinely a full multi-paragraph brief
    # (NewWorkflowPage's textarea). The real ceiling is
    # CreateWorkflowRequest.title's max_length.
    original_prompt: Mapped[str] = mapped_column(Text, nullable=False)

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

    # Set when this workflow was created via "Refine" on another workflow's
    # blueprint (rather than "New Workflow" from scratch) — links back to
    # the workflow being refined, forming a version chain distinct from
    # source_workflow_id's auto_execution linkage above. `version` is this
    # workflow's 1-indexed position in that chain (1 for anything created
    # from scratch). Both nullable/default-1 so pre-existing rows are
    # unaffected. SET NULL on parent deletion mirrors source_workflow_id's
    # FK behavior — losing the parent row should never retroactively hide
    # that this was a refinement, only the specific link to which one.
    parent_workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")

    # The human's own free-text note on what to change, captured on Refine
    # and threaded into the new workflow's planning prompt (see
    # PlanningAgent) so the next draft actually responds to it instead of
    # regenerating cold from the same objective. Null for anything not
    # created via Refine.
    refinement_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set only when a human has approved this blueprint via /approve — the
    # User whose decision moved this workflow to "approved". Nullable
    # because workflows approved before this column existed, and workflows
    # never approved at all, both have no answer here; SET NULL on user
    # deletion (mirrors source_workflow_id's FK behavior) since losing the
    # User row should never retroactively hide that an approval happened.
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
