"""The `engineering_sessions` and `timeline_entries` tables — Architecture
v2.1 §2.2 `Engineering Session` and `Timeline`.

`EngineeringSession` is the aggregate root RFC-001 implements: it owns
(composition) its Timeline and every Engineering Artifact created within
it (Belief, Hypothesis, Evidence, Decision, Recommendation, Contradiction —
see `app.models.engineering_artifact`). `TimelineEntry` is Timeline's own
append-only turn log.

RFC-001 scope note (Architecture v2.1 is frozen; this is a documented
scope boundary, not a deviation): `Organization` and `Engineering Mission`
are out of scope for this RFC. `EngineeringSession.mission_id` is a plain
nullable UUID column with no foreign key constraint, reserved for the
Mission aggregate a later RFC introduces — adding the constraint then is
additive, not breaking. `WorkingUnderstanding` has no table of its own: per
Architecture v2.1 §2.2, it is "composed of Beliefs," and RFC-001 models
that literally — `UnderstandingService` assembles it from live `Belief`
rows rather than persisting a redundant, always-derived copy. See
RFC-001.md's Architecture Mapping section for the full reasoning.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.engineering_artifact import EngineeringArtifact
    from app.models.participant import Participant

# Architecture v2.1 §3.1's Session lifecycle, verbatim. Every later state
# can reopen an earlier one (enforced at the service layer, not by a DB
# constraint on state-transition validity — see SessionService).
SESSION_STATUSES: tuple[str, ...] = (
    "orienting",
    "forming_beliefs",
    "investigating",
    "converging",
    "deciding",
    "building",
    "verifying",
    "operating",
    "dormant",
)


class EngineeringSession(Base):
    __tablename__ = "engineering_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    title: Mapped[str] = mapped_column(String(512), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="orienting", server_default="orienting"
    )

    # KAN-44: the human who created this Session — the ownership boundary
    # `SessionService`/`engineering_sessions.py` enforce, matching the
    # `user_id` convention every other user-owned resource in the app
    # (Repository, Workflow, Run) already uses. Nullable, not because a
    # Session can legitimately have no owner going forward (creation always
    # sets it — see `SessionService.create_session`), but because it must
    # tolerate rows that predate this column; a `NULL` here is a "no
    # recorded owner" row, treated by the ownership check as visible to any
    # authenticated user rather than becoming permanently inaccessible to
    # everyone — the same fallback rule `agent_runs.py`'s
    # `_run_ownership_clause` already applies to its own legacy rows.
    # Deliberately a direct column, not derived by joining through
    # `created_by_participant_id -> Participant.user_id` at query time: an
    # agent-authored artifact still has `created_by_participant_id` set,
    # but `Participant.user_id` is only ever non-null for kind="human" (see
    # `Participant`'s own CHECK constraint) — this column names the human
    # owner unambiguously, without every ownership check needing to know
    # that distinction.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Reserved for the Mission aggregate (a later RFC) — see module
    # docstring. No FK constraint yet; deliberately not enforced until
    # Mission exists as a real table.
    mission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    created_by_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    timeline_entries: Mapped[list[TimelineEntry]] = relationship(
        "TimelineEntry",
        back_populates="session",
        order_by="TimelineEntry.sequence",
        cascade="all, delete-orphan",
    )
    artifacts: Mapped[list[EngineeringArtifact]] = relationship(
        "EngineeringArtifact",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class TimelineEntry(Base):
    """One append-only turn — Architecture v2.1 §2.2 `Timeline`: "a turn is
    never edited or removed, only superseded by a later turn." There is no
    UPDATE/DELETE code path for this table at all; the service layer only
    ever inserts.
    """

    __tablename__ = "timeline_entries"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_timeline_entries_session_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Monotonic per-session ordering, assigned by TimelineService — the
    # authoritative turn order Architecture v2.1 §3.3 relies on to
    # reconstruct Working Understanding's history. Not `created_at`: two
    # turns can share a timestamp at DB-timestamp resolution, but never a
    # sequence number.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    participant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("participants.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # e.g. "belief_formed", "hypothesis_proposed", "evidence_recorded",
    # "decision_committed", "recommendation_proposed",
    # "contradiction_detected", "contradiction_resolved", "note" — an open
    # vocabulary (unlike SESSION_STATUSES), since new turn kinds are a
    # normal consequence of new Engineering Artifact activity, not an
    # architectural change.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)

    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Polymorphic reference to the Engineering Artifact this turn created
    # or changed, if any — no FK constraint (it may point at any of the
    # concrete artifact tables), validated at the service layer against
    # `engineering_artifacts.id` instead, which every concrete artifact
    # row shares as its own primary key (see engineering_artifact.py).
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[EngineeringSession] = relationship(
        "EngineeringSession", back_populates="timeline_entries"
    )
    participant: Mapped[Participant] = relationship("Participant")
