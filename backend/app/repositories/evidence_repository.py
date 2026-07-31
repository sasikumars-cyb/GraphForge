"""Data access for `Evidence` — Architecture v2.1 §2.2. Append-only:
there is no update method here, matching "recorded once, append-only ...
the record itself never does [change]."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import Evidence


class EvidenceRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, evidence: Evidence) -> Evidence:
        self._db.add(evidence)
        await self._db.flush()
        return evidence

    async def get(self, evidence_id: uuid.UUID) -> Evidence | None:
        return await self._db.get(Evidence, evidence_id)

    async def list_page(
        self, session_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Evidence], int]:
        count_stmt = select(func.count()).select_from(Evidence).where(
            Evidence.session_id == session_id
        )
        total = (await self._db.execute(count_stmt)).scalar_one()

        stmt = (
            select(Evidence)
            .where(Evidence.session_id == session_id)
            .order_by(Evidence.created_at)
            .limit(limit)
            .offset(offset)
        )
        items = list((await self._db.execute(stmt)).scalars().all())
        return items, total
