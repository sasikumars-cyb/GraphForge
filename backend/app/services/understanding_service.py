"""`UnderstandingService` — Architecture v2.1 §2.2 `Working Understanding`.

`WorkingUnderstanding` has no table of its own (see
`app/models/engineering_session.py`'s module docstring): per §2.2, it is
"composed of Beliefs," so this service assembles it, on read, from
whichever Belief rows are currently live for a Session — never from a
persisted, separately-maintained copy that could drift from the Beliefs
that actually back it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.belief import Belief
from app.repositories.belief_repository import BeliefRepository
from app.repositories.session_repository import SessionRepository


@dataclass(frozen=True)
class WorkingUnderstanding:
    """A read-only, point-in-time assembly — never persisted, never
    returned by reference from one call to the next. Recomputing this on
    every read is what Architecture v2.1 §3.3 calls "recompute, don't
    accumulate": there is no cache invalidation problem here because there
    is no cache."""

    session_id: uuid.UUID
    beliefs: list[Belief] = field(default_factory=list)
    overall_confidence: float = 0.0

    @property
    def belief_count(self) -> int:
        return len(self.beliefs)


class UnderstandingService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._belief_repo = BeliefRepository(db)
        self._session_repo = SessionRepository(db)

    async def get_working_understanding(self, session_id: uuid.UUID) -> WorkingUnderstanding:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")

        beliefs = await self._belief_repo.list_beliefs(session_id, exclude_retracted=True)
        overall = sum(b.confidence for b in beliefs) / len(beliefs) if beliefs else 0.0
        return WorkingUnderstanding(
            session_id=session_id, beliefs=beliefs, overall_confidence=round(overall, 4)
        )
