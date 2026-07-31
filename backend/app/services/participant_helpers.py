"""Shared `Participant` lookup/creation helpers — Architecture v2.1 §2.2.

Not its own named service: RFC-001's Implementation Requirements name
eight services (Session, Understanding, Belief, Evidence, Recommendation,
Contradiction, Decision, Timeline), not a ninth for Participant. Every
other service needs one of these two small operations, so they live here
as plain functions rather than being duplicated per service or promoted
to a concept the architecture review never asked for.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.participant import AGENT_ROLES, Participant
from app.models.user import User


async def get_or_create_human_participant(db: AsyncSession, user: User) -> Participant:
    """A Human Participant is a thin wrapper around a real `User` row —
    created lazily on first use rather than at signup, since not every
    User ever participates in an Engineering Session."""
    stmt = select(Participant).where(
        Participant.kind == "human", Participant.user_id == user.id
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    participant = Participant(kind="human", display_name=user.full_name, user_id=user.id)
    db.add(participant)
    await db.flush()
    return participant


async def get_or_create_agent_participant(db: AsyncSession, agent_role: str) -> Participant:
    """One Participant row per agent role in the fixed roster
    (`app.models.participant.AGENT_ROLES`) — a runtime instance, not a
    per-invocation identity, since Architecture v2.1 §2.2 treats an Agent
    Participant as durable ("Durability: durable as an identity;
    participation in any given Session is session-scoped")."""
    if agent_role not in AGENT_ROLES:
        raise ConflictError(
            f"'{agent_role}' is not a registered agent role. Architecture v2.1 §5 defines a "
            f"fixed roster: {', '.join(AGENT_ROLES)}."
        )

    stmt = select(Participant).where(
        Participant.kind == "agent", Participant.agent_role == agent_role
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    participant = Participant(
        kind="agent", display_name=agent_role.replace("_", " ").title(), agent_role=agent_role
    )
    db.add(participant)
    await db.flush()
    return participant


async def require_participant(db: AsyncSession, participant_id: uuid.UUID) -> Participant:
    participant = await db.get(Participant, participant_id)
    if participant is None:
        raise NotFoundError(f"Participant {participant_id} not found.")
    return participant
