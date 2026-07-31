"""The `contradictions` and `contradiction_parties` tables — Architecture
v2.1 §2.2 `Contradiction` (Δ v2.1: N-ary, with a deterministic ownership
rule).

`ContradictionParty` is the real, enforced expression of "references two
or more conflicting Engineering Artifacts" — a join table against
`engineering_artifacts.id` (not against any one concrete subtype), which
is exactly what the widened `EngineeringArtifact` supertype (§2.2, Δ v2.1)
makes possible: a single foreign key type covers a Belief, a Hypothesis, a
Recommendation, or a Decision as a disputing party, uniformly.

`owner_scope` encodes Architecture v2.1's ownership rule ("always the
narrowest scope containing every disputing party") as a string, but RFC-001
can only ever produce `"session"` — Mission doesn't exist as a table yet
(out of scope; see engineering_session.py's docstring), so the "Mission
scope when a dispute spans sibling Sessions" and "Organization scope"
branches of the rule are not reachable in this RFC. The column exists now,
correctly named and constrained to its one currently-valid value, so a
later RFC introducing Mission-scoped Contradictions is an additive
migration (loosen the CHECK constraint, no column rename).
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.engineering_artifact import EngineeringArtifact

ContradictionStatus = Literal["detected", "investigating", "resolved", "unresolved"]


class Contradiction(EngineeringArtifact):
    __tablename__ = "contradictions"
    __table_args__ = (
        CheckConstraint("owner_scope IN ('session')", name="ck_contradictions_owner_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_artifacts.id", ondelete="CASCADE"), primary_key=True
    )

    description: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="detected", server_default="detected"
    )

    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolved_by_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True
    )

    # See module docstring — always "session" in RFC-001.
    owner_scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="session", server_default="session"
    )

    parties: Mapped[list[ContradictionParty]] = relationship(
        "ContradictionParty",
        back_populates="contradiction",
        cascade="all, delete-orphan",
        # Explicit: `ContradictionParty.artifact_id` also targets
        # `engineering_artifacts.id`, which — since `Contradiction` is
        # itself an `EngineeringArtifact` subtype — SQLAlchemy would
        # otherwise consider a second, ambiguous join path back to this
        # same table (a Contradiction can itself be a disputing party in
        # a different Contradiction). `contradiction_id` is the only
        # column this relationship should ever join on.
        foreign_keys="ContradictionParty.contradiction_id",
    )

    __mapper_args__ = {"polymorphic_identity": "contradiction"}


class ContradictionParty(Base):
    """One disputing party in an N-ary Contradiction — a plain join table,
    not itself an Engineering Artifact (it has no independent identity;
    it's the relationship, same distinction the domain model draws
    between Contradiction, which is one, and this table, which isn't).
    """

    __tablename__ = "contradiction_parties"
    __table_args__ = (
        UniqueConstraint(
            "contradiction_id", "artifact_id", name="uq_contradiction_parties_pair"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    contradiction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("contradictions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # References the shared base table, not any one concrete subtype —
    # this is what makes N-ary, mixed-type disputes (a Belief disputing a
    # Decision disputing a Recommendation, say) a single, uniform foreign
    # key instead of a nullable column per possible artifact type.
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("engineering_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    contradiction: Mapped[Contradiction] = relationship(
        "Contradiction",
        back_populates="parties",
        foreign_keys=[contradiction_id],
    )
