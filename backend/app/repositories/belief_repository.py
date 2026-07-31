"""Data access for `Belief` and `Hypothesis` — Architecture v2.1 §2.2.
One repository for both: a Hypothesis's entire reason for existing is that
it resolves into a Belief (§3.2), so the two are never queried or reasoned
about independently of one another.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.belief import Belief, Hypothesis


class BeliefRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add_belief(self, belief: Belief) -> Belief:
        self._db.add(belief)
        await self._db.flush()
        return belief

    async def add_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        self._db.add(hypothesis)
        await self._db.flush()
        return hypothesis

    async def get_belief(self, belief_id: uuid.UUID) -> Belief | None:
        return await self._db.get(Belief, belief_id)

    async def get_hypothesis(self, hypothesis_id: uuid.UUID) -> Hypothesis | None:
        return await self._db.get(Hypothesis, hypothesis_id)

    async def list_beliefs(
        self, session_id: uuid.UUID, *, exclude_retracted: bool = True
    ) -> list[Belief]:
        """The query `UnderstandingService` assembles Working Understanding
        from — see that service's own docstring on why this, not a
        persisted row, is Working Understanding's real source."""
        stmt = select(Belief).where(Belief.session_id == session_id)
        if exclude_retracted:
            stmt = stmt.where(Belief.status != "retracted")
        stmt = stmt.order_by(Belief.created_at)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_hypotheses(
        self, session_id: uuid.UUID, *, unresolved_only: bool = False
    ) -> list[Hypothesis]:
        stmt = select(Hypothesis).where(Hypothesis.session_id == session_id)
        if unresolved_only:
            stmt = stmt.where(Hypothesis.status.in_(("proposed", "strengthening", "weakening")))
        stmt = stmt.order_by(Hypothesis.created_at)
        return list((await self._db.execute(stmt)).scalars().all())
