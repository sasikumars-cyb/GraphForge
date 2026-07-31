"""Data access for `EngineeringSession` — Architecture v2.1 §2.2, the
aggregate root RFC-001 implements.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engineering_session import EngineeringSession


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, session: EngineeringSession) -> EngineeringSession:
        self._db.add(session)
        await self._db.flush()
        return session

    async def get(self, session_id: uuid.UUID) -> EngineeringSession | None:
        return await self._db.get(EngineeringSession, session_id)

    async def list_page(
        self, *, status: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[EngineeringSession], int]:
        """Returns (items, total_count) — the shape every paginated RFC-001
        endpoint needs (`GET /sessions`'s total is required to render "page
        N of M" without a second round trip)."""
        stmt = select(EngineeringSession)
        count_stmt = select(func.count()).select_from(EngineeringSession)
        if status is not None:
            stmt = stmt.where(EngineeringSession.status == status)
            count_stmt = count_stmt.where(EngineeringSession.status == status)

        total = (await self._db.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(EngineeringSession.created_at.desc()).limit(limit).offset(offset)
        items = list((await self._db.execute(stmt)).scalars().all())
        return items, total
