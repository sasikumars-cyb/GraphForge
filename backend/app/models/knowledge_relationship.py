"""The `knowledge_relationships` table — ADR 0018 RFC-04.

Append-only, versioned log: every insert is a new *version* of a
relationship, never an update to a prior row — "relationship evolution"
and "confidence history" (RFC-04's requirements) are exactly "every row
for this `relationship_key`, ordered by `sequence`." "Current" state is
"the latest row per `relationship_key`" (see
`EngineeringMemoryRepository.get_current_relationships`), not a separate
mutable column anywhere.

Ordering uses `sequence` (a Postgres `GENERATED ALWAYS AS IDENTITY`
column), not `created_at` — found and fixed via a real integration test
against a real Postgres instance, not assumed correct: Postgres's `now()`/
`CURRENT_TIMESTAMP` (what `server_default=func.now()` uses) returns the
*transaction's* start time, not a per-statement time, so two relationship
versions written in the same transaction (e.g. `store_relationships`
persisting a whole indexing run's output in one commit) get an *identical*
`created_at`, making "latest" undefined under a timestamp sort. `sequence`
is monotonic by construction regardless of transaction boundaries — the
correct tiebreaker, not a timestamp with insufficient resolution.
`created_at` is kept for human-readable/audit purposes, just no longer
used for ordering.

`relationship_key` is deterministic — `f"{repository_id}:{relationship_type}
:{source_entity}:{target_entity}"` — independent of confidence or time, so
re-computing the same relationship on a later run always versions the same
key rather than accidentally starting a new history. This is what makes
"relationship evolution" meaningful: the same key's rows over time show a
confidence trajectory, not unrelated one-off facts.

`ConfidenceModel`'s fields are embedded directly as columns (not a
separate joined table) — the same pattern
`app.models.pull_request_ai_analysis.PullRequestAIAnalysis` already uses
for a structured, evolving-shape sub-result. `Provenance` is stored as a
JSON list (one dict per contributing generator, `KnowledgeRelationship
.provenance` is already a tuple for exactly this reason) — full fidelity,
never queried structurally within this RFC's scope.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Identity, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class KnowledgeRelationshipRecord(Base):
    __tablename__ = "knowledge_relationships"
    __table_args__ = (
        Index("ix_knowledge_relationships_key_sequence", "relationship_key", "sequence"),
    )

    # This ROW's own identity — a new one per version, never reused.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Monotonic version ordinal — see module docstring for why this, not
    # `created_at`, is the ordering/tiebreak column.
    sequence: Mapped[int] = mapped_column(
        Integer, Identity(always=True), nullable=False, unique=True
    )

    # The relationship's stable identity across versions — see module
    # docstring. Not a foreign key to anything (there is no separate
    # "relationships" identity table by design — the key is a pure,
    # deterministic function of its own fields, not a generated id).
    relationship_key: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_entity: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_entity: Mapped[str] = mapped_column(String(1024), nullable=False)

    hypothesis_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # -- ConfidenceModel, embedded (ADR 0018's `ConfidenceModel` shape) --
    confidence_state: Mapped[str] = mapped_column(String(32), nullable=False)
    distinct_confirming_source_types: Mapped[int] = mapped_column(Integer, nullable=False)
    confirming_source_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_confirming_reliability_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    contradiction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_formula_version: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # -- Provenance, one dict per contributing generator --
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    # -- ConfidenceExplanation, computed once at validation time (ADR 0018
    # Confidence Explainability) — nullable because it's derived from
    # `ValidationResult`s that exist only transiently during that one
    # reasoning pass; a row persisted before this column existed, or one
    # whose hypothesis had no applicable validator at all, legitimately
    # has none. Never recomputed at read time: `ValidationResult`s aren't
    # persisted anywhere, so re-deriving this later from `confidence_state`
    # alone would be lossy (see `explainability.py`'s own docstring).
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
