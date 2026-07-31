"""The `recommendations` and `decisions` tables — Architecture v2.1 §2.2
`Recommendation` and `Decision`.

Table creation order matters here and is deliberate: `recommendations` is
defined (and created) before `decisions`, because `Decision.recommendation_id`
references it — the "Proposed (as a Recommendation) -> committed" lifecycle
from §2.2 is a real, enforced foreign key, not just documentation.

`Recommendation.target_contradiction_id` is deliberately a plain, unconstrained
UUID column, not a foreign key — `Contradiction` (contradiction.py) is defined
*after* this module and itself references `decisions.id`
(`resolved_by_decision_id`), which would make `recommendations -> contradictions
-> decisions -> recommendations` a genuine circular table dependency. Rather
than split table creation across multiple ALTER-TABLE-added-constraint
migration steps for a reference that's read-only/display purposes within
RFC-001, the same "documented, unenforced reference" pattern already used for
`EngineeringSession.mission_id` and `TimelineEntry.artifact_id` is reused here.
`target_belief_id` has no such conflict and is a real, enforced foreign key.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.engineering_artifact import EngineeringArtifact

RecommendationStatus = Literal["proposed", "accepted", "declined", "superseded"]
DecisionKind = Literal["planning_strategy", "review"]


class Recommendation(EngineeringArtifact):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_artifacts.id", ondelete="CASCADE"), primary_key=True
    )

    statement: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed", server_default="proposed"
    )

    target_belief_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beliefs.id", ondelete="SET NULL"), nullable=True
    )

    # Unconstrained by design — see module docstring.
    target_contradiction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    __mapper_args__ = {"polymorphic_identity": "recommendation"}


class Decision(EngineeringArtifact):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_artifacts.id", ondelete="CASCADE"), primary_key=True
    )

    decision_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    statement: Mapped[str] = mapped_column(Text, nullable=False)

    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    # Architecture v2.1 §2.2: "always attributable to exactly one
    # committing Participant, even when many contributed evidence." This
    # is distinct from the base `EngineeringArtifact.participant_id`
    # (which, for a Decision row, is whoever *proposed* the Recommendation
    # it commits — usually an Agent) — commit authority is always
    # separately named here, and is never an Agent Participant (enforced
    # by DecisionService, not a DB constraint, since that rule depends on
    # Policy — out of RFC-001 scope; see RFC-001.md).
    committed_by_participant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("participants.id", ondelete="RESTRICT"), nullable=False
    )

    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True
    )

    # Self-referential — Architecture v2.1 §2.2: "a change of mind is a new
    # Decision that supersedes it, with the old one kept for history."
    superseded_by_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True
    )

    __mapper_args__ = {"polymorphic_identity": "decision"}
