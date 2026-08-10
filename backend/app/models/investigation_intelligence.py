"""The `investigation_provider_events` and `investigation_outcomes`
tables — ADR 0021, Investigation Intelligence Phase 1.

Deliberately separate from every Engineering Memory table
(`engineering_evidence_packs`, `knowledge_relationships`,
`user_corrections`) — no foreign key to any of them. See ADR 0021's "Why
this is not Engineering Memory" for the reasoning: this is retrieval and
planning *experience* (which strategy finds answers well), not
engineering *knowledge* (is this graph relationship correct).

Both tables are append-only by convention (no update method exists on
`InvestigationIntelligenceRepository` — a new row is always inserted,
matching the same precedent `EngineeringEvidencePackRecord`/
`TimelineEntry`/`LearningEventRecord` already set in this codebase). A
`sequence` column, not `created_at`, is what orders rows reliably within
a scope — the same fix `KnowledgeRelationshipRecord`/
`EngineeringEvidencePackRecord` already needed for the identical reason:
`now()` is transaction-scoped in Postgres, so two rows written in the
same transaction can share a timestamp.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Identity, Index, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class InvestigationProviderEventRecord(Base):
    """One provider action's outcome, with the planner-decision context
    that produced it folded into the same row. See
    `app.investigation_intelligence.contracts.ProviderOutcomeEvent` for
    the typed shape this table stores — that dataclass, never this ORM
    class, is what crosses the repository boundary."""

    __tablename__ = "investigation_provider_events"
    __table_args__ = (
        Index(
            "ix_investigation_provider_events_scope_capability_provider",
            "scope_type",
            "scope_id",
            "capability",
            "provider",
        ),
        Index("ix_investigation_provider_events_investigation_id", "investigation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Monotonic insertion ordinal — see module docstring. Not the primary
    # key (that stays a plain UUID, matching every other table in this
    # codebase); purely an ordering/pagination column.
    sequence: Mapped[int] = mapped_column(
        Integer, Identity(always=True), nullable=False, unique=True
    )

    investigation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)

    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)

    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    investigation_type: Mapped[str] = mapped_column(String(32), nullable=False)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    action_key: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)

    declared_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    yielded_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False)

    necessity_at_selection: Mapped[str] = mapped_column(String(16), nullable=False)
    base_score_at_selection: Mapped[float] = mapped_column(Float, nullable=False)
    priority_boost_applied: Mapped[float] = mapped_column(Float, nullable=False)
    priority_boost_source: Mapped[str] = mapped_column(String(16), nullable=False)

    confidence_before: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_after: Mapped[float] = mapped_column(Float, nullable=False)

    # See contracts.StateSnapshot — captured in full starting Phase 1 at
    # zero extra retrieval cost, read by nothing in Phase 1. The
    # deliberate mechanism (ADR 0021 §3a) for future policy-learning work
    # to proceed without a schema migration.
    state_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InvestigationOutcomeRecord(Base):
    """One completed-or-crashed investigation's terminal summary. Not
    unique per `investigation_id` — a resumed investigation that reaches
    a new terminal state writes a new row, never updates the first (see
    `contracts.InvestigationOutcomeEvent`'s own docstring)."""

    __tablename__ = "investigation_outcomes"
    __table_args__ = (
        Index("ix_investigation_outcomes_scope", "scope_type", "scope_id"),
        Index("ix_investigation_outcomes_investigation_id", "investigation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sequence: Mapped[int] = mapped_column(
        Integer, Identity(always=True), nullable=False, unique=True
    )

    investigation_id: Mapped[str] = mapped_column(String(128), nullable=False)

    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)

    investigation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cycles_used: Mapped[int] = mapped_column(Integer, nullable=False)
    terminal_outcome: Mapped[str] = mapped_column(String(16), nullable=False)

    # Optional: a FAILED investigation may crash before any assessment
    # ever runs, in which case there is no confidence to report.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_capability_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)

    contradictions_encountered: Mapped[int] = mapped_column(Integer, nullable=False)
    contradictions_resolved: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_boost_source_used: Mapped[bool] = mapped_column(Boolean, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
