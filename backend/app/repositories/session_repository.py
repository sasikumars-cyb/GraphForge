"""Data access for `EngineeringSession` — Architecture v2.1 §2.2, the
aggregate root RFC-001 implements.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engineering_session import EngineeringSession


def _ownership_clause(user_id: uuid.UUID) -> ColumnElement[bool]:
    """KAN-44: a Session is visible to the user who created it, or — for a
    row with no recorded owner (predates `user_id`, or was created by an
    agent-only flow with no resolvable human) — to any authenticated user,
    the same "don't make an unowned legacy row permanently inaccessible"
    fallback `agent_runs.py`'s own ownership clause applies."""
    return or_(EngineeringSession.user_id == user_id, EngineeringSession.user_id.is_(None))


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, session: EngineeringSession) -> EngineeringSession:
        self._db.add(session)
        await self._db.flush()
        return session

    async def get(
        self, session_id: uuid.UUID, *, user_id: uuid.UUID | None = None
    ) -> EngineeringSession | None:
        """`user_id` is optional and *not* set by every caller on purpose:
        `SessionService.get_session` (the router's own entry point, KAN-44)
        always passes it, enforcing ownership — `None` both when the id
        doesn't exist and when it belongs to a different user, so a caller
        can never use this to confirm another user's session exists.

        The several sub-resource services (BeliefService, EvidenceService,
        RecommendationService, DecisionService, ContradictionService,
        UnderstandingService, TimelineService) call this with no `user_id`
        at all — they only need "does this session still exist" as a
        precondition for the artifact they're about to touch; the actual
        per-request ownership gate already ran once, at the API boundary,
        before any of them were reached (see `engineering_sessions.py`'s
        `_verified_session_owner` dependency). Re-deriving ownership again
        at every sub-resource call site would duplicate that boundary
        rather than layer on top of it.
        """
        stmt = select(EngineeringSession).where(EngineeringSession.id == session_id)
        if user_id is not None:
            stmt = stmt.where(_ownership_clause(user_id))
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_page(
        self,
        *,
        user_id: uuid.UUID,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[EngineeringSession], int]:
        """Returns (items, total_count) — the shape every paginated RFC-001
        endpoint needs (`GET /sessions`'s total is required to render "page
        N of M" without a second round trip)."""
        stmt = select(EngineeringSession).where(_ownership_clause(user_id))
        count_stmt = (
            select(func.count()).select_from(EngineeringSession).where(_ownership_clause(user_id))
        )
        if status is not None:
            stmt = stmt.where(EngineeringSession.status == status)
            count_stmt = count_stmt.where(EngineeringSession.status == status)

        total = (await self._db.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(EngineeringSession.created_at.desc()).limit(limit).offset(offset)
        items = list((await self._db.execute(stmt)).scalars().all())
        return items, total
