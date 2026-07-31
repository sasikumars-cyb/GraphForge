"""The `engineering_artifacts` table — Architecture v2.1 §2.2/§6
`Engineering Artifact`, the abstract supertype of Belief, Hypothesis,
Evidence, Decision, Recommendation, and Contradiction.

Modeled as SQLAlchemy joined-table inheritance: this table carries the
five fields every subtype shares (id, session owner, authoring
Participant, retention state, creation time — see §6's rule that
ownership/traceability are "defined exactly once at the supertype"), and
each concrete subtype's own table shares its primary key as a foreign key
back here (`Belief.id`, `Evidence.id`, ... each reference
`engineering_artifacts.id`). This is the real relational expression of
"every domain object a Participant can reference has one identity
contract, no exceptions" (Architecture v2.1 §2.2, Δ v2.1) — a single
table, one row per artifact regardless of concrete type, is what lets
`Contradiction` reference "two or more disputing Engineering Artifacts"
(§2.2) with one real foreign key (`contradiction_parties.artifact_id` →
`engineering_artifacts.id`) instead of a different join table per pair of
concrete types.

`EngineeringArtifact` itself is never instantiated directly — only through
one of its `polymorphic_identity` subclasses in belief.py / evidence.py /
decision.py / contradiction.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.engineering_session import EngineeringSession
    from app.models.participant import Participant

# Architecture v2.1 §2.2: "active | legal_hold | redacted" — resolution of
# review finding 1 (retention/legal-hold is a field on Engineering
# Artifact, not a Policy instantiation).
RetentionState = Literal["active", "legal_hold", "redacted"]

ArtifactKind = Literal[
    "belief", "hypothesis", "evidence", "decision", "recommendation", "contradiction"
]


class EngineeringArtifact(Base):
    __tablename__ = "engineering_artifacts"
    __table_args__ = (
        CheckConstraint(
            "retention_state IN ('active', 'legal_hold', 'redacted')",
            name="ck_engineering_artifacts_retention_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The polymorphic discriminator — set automatically by SQLAlchemy from
    # each subclass's `polymorphic_identity`, never assigned by hand.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    participant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("participants.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    retention_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[EngineeringSession] = relationship(
        "EngineeringSession", back_populates="artifacts"
    )
    participant: Mapped[Participant] = relationship("Participant")

    __mapper_args__ = {
        "polymorphic_identity": "engineering_artifact",
        "polymorphic_on": "kind",
    }
