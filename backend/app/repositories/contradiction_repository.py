"""Data access for `Contradiction` and `ContradictionParty` — Architecture
v2.1 §2.2 (Δ v2.1: N-ary). A Contradiction is never queried without its
parties — a Contradiction with fewer than two parties is not a valid
domain object (see `ContradictionService.detect`, which is the only
writer and enforces the minimum at the point of creation).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.contradiction import Contradiction, ContradictionParty


class ContradictionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self, contradiction: Contradiction, party_artifact_ids: list[uuid.UUID]
    ) -> Contradiction:
        self._db.add(contradiction)
        await self._db.flush()  # assigns contradiction.id
        for artifact_id in party_artifact_ids:
            self._db.add(
                ContradictionParty(contradiction_id=contradiction.id, artifact_id=artifact_id)
            )
        await self._db.flush()
        return contradiction

    async def get(self, contradiction_id: uuid.UUID) -> Contradiction | None:
        stmt = (
            select(Contradiction)
            .where(Contradiction.id == contradiction_id)
            .options(selectinload(Contradiction.parties))
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def list_page(
        self,
        session_id: uuid.UUID,
        *,
        unresolved_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Contradiction], int]:
        base = select(Contradiction).where(Contradiction.session_id == session_id)
        count_base = (
            select(func.count())
            .select_from(Contradiction)
            .where(Contradiction.session_id == session_id)
        )
        if unresolved_only:
            base = base.where(Contradiction.status.in_(("detected", "investigating")))
            count_base = count_base.where(Contradiction.status.in_(("detected", "investigating")))

        total = (await self._db.execute(count_base)).scalar_one()
        stmt = (
            base.options(selectinload(Contradiction.parties))
            .order_by(Contradiction.created_at)
            .limit(limit)
            .offset(offset)
        )
        items = list((await self._db.execute(stmt)).scalars().all())
        return items, total

    async def list_by_artifact(self, artifact_id: uuid.UUID) -> list[Contradiction]:
        """Every Contradiction a given Engineering Artifact is a party
        to — used by `ContradictionService.detect` to check "does a
        Contradiction between exactly this pair already exist" before
        recording a new one."""
        stmt = (
            select(Contradiction)
            .join(ContradictionParty, ContradictionParty.contradiction_id == Contradiction.id)
            .where(ContradictionParty.artifact_id == artifact_id)
            .options(selectinload(Contradiction.parties))
        )
        return list((await self._db.execute(stmt)).scalars().all())
