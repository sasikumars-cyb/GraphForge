"""The `participants` table — Architecture v2.1 §2.2 `Participant`.

Unified supertype of Agent and Human, exactly as specified: collaboration,
attribution, and turn ownership are identical mechanisms for both, so one
table (discriminated by `kind`) carries both rather than two parallel
identity models. A Human Participant wraps a real `User` row; an Agent
Participant is a registered capability instance from the fixed roster
Architecture v2.1 §5 names (no free-text agent names — see `AGENT_ROLES`).

RFC-001 scope note: Architecture v2.1 §6 requires every Engineering
Artifact's `retention_state` and provenance to name the authoring
Participant. This table is what every other RFC-001 table's
`participant_id` foreign key points at.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

ParticipantKind = Literal["human", "agent"]

# The fixed agent roster from Architecture v2.1 §5 — deliberately closed,
# not free text: a Participant of kind="agent" must be one of these named
# roles, matching the roster the architecture specifies. Extending this
# list is an architectural decision (a new agent role), not a data-entry
# choice, so it lives here as the single place §5's roster is enforced.
AGENT_ROLES: tuple[str, ...] = (
    "investigator",
    "planner",
    "developer",
    "tester",
    "reviewer",
    "architect",
    "knowledge_curator",
    "production_analyst",
    "mission_coordinator",
)


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'human' AND user_id IS NOT NULL AND agent_role IS NULL) OR "
            "(kind = 'agent' AND agent_role IS NOT NULL AND user_id IS NULL)",
            name="ck_participants_kind_shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Set only for kind="human" — the real person behind this Participant.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    # Set only for kind="agent" — one of AGENT_ROLES. Not a foreign key:
    # the roster is a closed, architecture-defined set (§5), not a
    # separately-persisted lookup table, so validation happens at the
    # service layer against the Python-level constant above.
    agent_role: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
