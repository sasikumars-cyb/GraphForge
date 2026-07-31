"""Data access for `TimelineEntry` — Architecture v2.1 §2.2 `Timeline`:
append-only, never edited or removed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engineering_session import TimelineEntry


class TimelineRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def next_sequence(self, session_id: uuid.UUID) -> int:
        """The next monotonic sequence number for this session. Callers
        must hold a transaction that serializes concurrent turn-appends
        for the same session (see `TimelineService.append`) — this alone
        does not guard against a race between two concurrent appends."""
        current_max = (
            await self._db.execute(
                select(func.max(TimelineEntry.sequence)).where(
                    TimelineEntry.session_id == session_id
                )
            )
        ).scalar_one()
        return (current_max or 0) + 1

    async def add(self, entry: TimelineEntry) -> TimelineEntry:
        self._db.add(entry)
        await self._db.flush()
        return entry

    async def list_page(
        self, session_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[TimelineEntry], int]:
        count_stmt = select(func.count()).select_from(TimelineEntry).where(
            TimelineEntry.session_id == session_id
        )
        total = (await self._db.execute(count_stmt)).scalar_one()

        stmt = (
            select(TimelineEntry)
            .where(TimelineEntry.session_id == session_id)
            .order_by(TimelineEntry.sequence)
            .limit(limit)
            .offset(offset)
        )
        items = list((await self._db.execute(stmt)).scalars().all())
        return items, total
