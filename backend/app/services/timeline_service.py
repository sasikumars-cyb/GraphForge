"""`TimelineService` — Architecture v2.1 §2.2 `Timeline`: "the ordered,
append-only record of every turn in a Session."

Every other RFC-001 service calls `TimelineService.append` after each
mutation it makes — this is what turns "a Belief was created" into "the
Session's Timeline shows a turn narrating that a Belief was created,"
which is the actual mechanism behind Architecture v2.1's principle that
Working Understanding's history is fully reconstructable from the
Timeline (§3.3) rather than needing its own audit log.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.engineering_session import TimelineEntry
from app.repositories.session_repository import SessionRepository
from app.repositories.timeline_repository import TimelineRepository


class TimelineService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._timeline_repo = TimelineRepository(db)
        self._session_repo = SessionRepository(db)

    async def append(
        self,
        *,
        session_id: uuid.UUID,
        participant_id: uuid.UUID,
        kind: str,
        summary: str,
        artifact_id: uuid.UUID | None = None,
    ) -> TimelineEntry:
        """Records one turn. Never edits or removes an existing one — see
        `TimelineRepository`'s own docstring. Callers are expected to be
        inside an existing transaction (this only flushes, never commits);
        the calling service commits once, after its own artifact write and
        this call both succeed, so a turn is never recorded for a mutation
        that didn't actually persist.
        """
        session = await self._session_repo.get(session_id)
        if session is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")

        sequence = await self._timeline_repo.next_sequence(session_id)
        entry = TimelineEntry(
            session_id=session_id,
            sequence=sequence,
            participant_id=participant_id,
            kind=kind,
            summary=summary,
            artifact_id=artifact_id,
        )
        return await self._timeline_repo.add(entry)

    async def list_page(
        self, session_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[TimelineEntry], int]:
        session = await self._session_repo.get(session_id)
        if session is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        return await self._timeline_repo.list_page(session_id, limit=limit, offset=offset)
