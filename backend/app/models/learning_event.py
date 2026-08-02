"""The `learning_events` table — ADR 0018 RFC-07 (Learning & Feedback
Engine).

A separate append-only store from Engineering Memory by design (the
RFC's own architectural rule: "Learning data is a separate append-only
store... Never modify historical KnowledgeRelationships"). This table is
never written to by `app.knowledge_engine`/`app.indexer`, and nothing in
`app.knowledge_engine`/`app.indexer` ever reads from it — the dependency
runs one way, feedback observing knowledge, never knowledge depending on
feedback.

`sequence` mirrors `KnowledgeRelationshipRecord.sequence` exactly, for
the same reason (see that model's own module docstring): a Postgres
`GENERATED ALWAYS AS IDENTITY` column is the only ordering guarantee that
survives multiple events landing in the same transaction with an
identical `created_at`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class LearningEventRecord(Base):
    __tablename__ = "learning_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    sequence: Mapped[int] = mapped_column(
        Integer, Identity(always=True), nullable=False, unique=True
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Nullable — a `missing_relationship` event has neither.
    relationship_key: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    relationship_type: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    generator_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence_state_at_event: Mapped[str | None] = mapped_column(String(32), nullable=True)

    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    source_trust_level: Mapped[float] = mapped_column(Float, nullable=False)

    detail: Mapped[str] = mapped_column(Text, nullable=False)

    event_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
