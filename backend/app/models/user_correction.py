"""The `user_corrections` table — ADR 0018 RFC-04.

Append-only: a correction is a new fact recorded about a relationship, not
an edit to the relationship's own history rows — ADR 0018's invariant
("even [a human correction] is recorded as a new transition, never a
silent edit to history"). `relationship_key` matches
`KnowledgeRelationshipRecord.relationship_key`, not a foreign key to any
one row (a correction applies to the relationship's identity across all
its versions, not to one specific historical snapshot).

RFC-04 implements human corrections only — `CorrectionSource.kind` is
still stored as free text (matching the `CorrectionKind` contract's
`Literal["human", "agent"]`) so the column doesn't need to change shape
when agent corrections are implemented later; nothing in this RFC ever
writes `kind="agent"`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class UserCorrectionRecord(Base):
    __tablename__ = "user_corrections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    relationship_key: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)

    correction_source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    correction_source_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    correction_source_trust_level: Mapped[float] = mapped_column(Float, nullable=False)

    # None means "reject this relationship entirely" (matching
    # `UserCorrection.corrected_state`'s own contract).
    corrected_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # From the contract's own `UserCorrection.created_at` — when the
    # correction was made, not necessarily when this row was written.
    correction_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
