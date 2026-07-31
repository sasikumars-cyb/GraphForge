"""Data access for `Decision` — Architecture v2.1 §2.2. No update method:
"a Decision, once committed, is never edited; a change of mind is a new
Decision that supersedes it, with the old one kept for history" — see
`DecisionService.supersede`, which only ever adds a new row and points the
old one's `superseded_by_decision_id` at it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import Decision


class DecisionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, decision: Decision) -> Decision:
        self._db.add(decision)
        await self._db.flush()
        return decision

    async def get(self, decision_id: uuid.UUID) -> Decision | None:
        return await self._db.get(Decision, decision_id)

    async def list_page(
        self, session_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Decision], int]:
        count_stmt = select(func.count()).select_from(Decision).where(
            Decision.session_id == session_id
        )
        total = (await self._db.execute(count_stmt)).scalar_one()

        stmt = (
            select(Decision)
            .where(Decision.session_id == session_id)
            .order_by(Decision.created_at)
            .limit(limit)
            .offset(offset)
        )
        items = list((await self._db.execute(stmt)).scalars().all())
        return items, total
