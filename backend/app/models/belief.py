"""The `beliefs` and `hypotheses` tables — Architecture v2.1 §2.2 `Belief`
and `Hypothesis`.

Both are joined-table subclasses of `EngineeringArtifact` (see that
module's docstring for why). `Hypothesis.resolved_belief_id` is the one
foreign key that encodes the sequential relationship Architecture v2.1
requires: "a Hypothesis is a pending, competing candidate; it resolves
into exactly one Belief (or is rejected)."

`Belief` deliberately has no `promoted_at`/`promoted_to` field — per
Architecture v2.1 §2.2 (Δ v2.1), a Belief is *never itself promoted*; only
an independent `SystemModelEntry` is, via copy-with-provenance. That
object belongs to the System Model aggregate, out of scope for RFC-001
(Architecture v2.1 Phase 3) — see RFC-001.md's Known Limitations.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.engineering_artifact import EngineeringArtifact

BeliefStatus = Literal["formed", "revised", "contradicted", "retracted"]
HypothesisStatus = Literal["proposed", "strengthening", "weakening", "resolved", "rejected"]


class Belief(EngineeringArtifact):
    __tablename__ = "beliefs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_artifacts.id", ondelete="CASCADE"), primary_key=True
    )

    statement: Mapped[str] = mapped_column(Text, nullable=False)

    # [0, 1] — enforced at the Pydantic schema layer (app.schemas), not a
    # DB CHECK constraint, matching this codebase's existing convention of
    # keeping cross-field/range validation in the request/response schema
    # rather than the database (see e.g. Repository's model).
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="formed", server_default="formed"
    )

    __mapper_args__ = {"polymorphic_identity": "belief"}


class Hypothesis(EngineeringArtifact):
    __tablename__ = "hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_artifacts.id", ondelete="CASCADE"), primary_key=True
    )

    description: Mapped[str] = mapped_column(Text, nullable=False)

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed", server_default="proposed"
    )

    # Set only when status == "resolved" — the Belief this Hypothesis
    # became. Architecture v2.1 §3.2: "Resolved --> [*]: becomes a Belief."
    resolved_belief_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beliefs.id", ondelete="SET NULL"), nullable=True
    )

    __mapper_args__ = {"polymorphic_identity": "hypothesis"}
