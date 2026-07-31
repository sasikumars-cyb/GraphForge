"""Data access for `Recommendation` — Architecture v2.1 §2.2."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import Recommendation


class RecommendationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, recommendation: Recommendation) -> Recommendation:
        self._db.add(recommendation)
        await self._db.flush()
        return recommendation

    async def get(self, recommendation_id: uuid.UUID) -> Recommendation | None:
        return await self._db.get(Recommendation, recommendation_id)

    async def list_open(self, session_id: uuid.UUID) -> list[Recommendation]:
        """Recommendations still "proposed" — Architecture v2.1 §2.2: "one
        current Recommendation per open question under ordinary
        operation." Used by `RecommendationService` to detect a competing
        Recommendation targeting the same Belief/Contradiction (finding 5's
        resolution — see that service)."""
        stmt = (
            select(Recommendation)
            .where(Recommendation.session_id == session_id, Recommendation.status == "proposed")
            .order_by(Recommendation.created_at)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_for_target_belief(
        self, session_id: uuid.UUID, target_belief_id: uuid.UUID
    ) -> list[Recommendation]:
        stmt = select(Recommendation).where(
            Recommendation.session_id == session_id,
            Recommendation.target_belief_id == target_belief_id,
            Recommendation.status == "proposed",
        )
        return list((await self._db.execute(stmt)).scalars().all())
