"""Data access for `learning_events` (ADR 0018 RFC-07) — same shape as
`app.repositories.engineering_memory_repository.EngineeringMemoryRepository`:
`add_event` only flushes (never commits — the caller/service owns the
transaction boundary), retrieval is plain SQLAlchemy `select()`, no raw
SQL, no update/delete methods at all (append-only by construction: there
is nothing here that could mutate a persisted row).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.learning_event import LearningEventRecord


class LearningEventRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add_event(self, record: LearningEventRecord) -> LearningEventRecord:
        self._db.add(record)
        await self._db.flush()
        return record

    async def list_events(
        self,
        repository_id: uuid.UUID,
        *,
        event_type: str | None = None,
        relationship_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LearningEventRecord]:
        stmt = select(LearningEventRecord).where(LearningEventRecord.repository_id == repository_id)
        if event_type is not None:
            stmt = stmt.where(LearningEventRecord.event_type == event_type)
        if relationship_type is not None:
            stmt = stmt.where(LearningEventRecord.relationship_type == relationship_type)
        stmt = stmt.order_by(LearningEventRecord.sequence.asc()).limit(limit).offset(offset)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_all_events_for_statistics(
        self, repository_id: uuid.UUID
    ) -> list[LearningEventRecord]:
        """Every event for a repository, oldest first — the full input
        `app.learning_engine.aggregation.compute_statistics` needs (trend
        detection depends on chronological, i.e. `sequence`, order)."""
        stmt = (
            select(LearningEventRecord)
            .where(LearningEventRecord.repository_id == repository_id)
            .order_by(LearningEventRecord.sequence.asc())
        )
        return list((await self._db.execute(stmt)).scalars().all())
