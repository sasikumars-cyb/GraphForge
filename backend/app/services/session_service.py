"""`SessionService` — Architecture v2.1 §2.2 `Engineering Session`, the
aggregate root, and §3.1's Session lifecycle.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.engineering_session import SESSION_STATUSES, EngineeringSession
from app.models.user import User
from app.repositories.session_repository import SessionRepository
from app.services.participant_helpers import get_or_create_human_participant
from app.services.timeline_service import TimelineService


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._session_repo = SessionRepository(db)
        self._timeline = TimelineService(db)

    async def create_session(self, *, title: str, created_by: User) -> EngineeringSession:
        """A new Session always starts "orienting" (Architecture v2.1
        §3.1) — there is no way to create one in any other state; skipping
        straight to a later state would assert an investigation happened
        that didn't."""
        participant = await get_or_create_human_participant(self._db, created_by)

        session = EngineeringSession(
            title=title,
            status="orienting",
            created_by_participant_id=participant.id,
            user_id=created_by.id,
        )
        await self._session_repo.add(session)
        await self._timeline.append(
            session_id=session.id,
            participant_id=participant.id,
            kind="session_created",
            summary=f'Session created: "{title}".',
        )
        await self._db.commit()
        await self._db.refresh(session)
        return session

    async def get_session(self, session_id: uuid.UUID, *, user_id: uuid.UUID) -> EngineeringSession:
        """KAN-44: raises the same `NotFoundError` whether `session_id`
        doesn't exist at all or belongs to a different user — a caller can
        never distinguish "no such session" from "not yours" (no existence
        oracle), matching `workflows.py`/`agent_runs.py`'s own 404-not-403
        convention."""
        session = await self._session_repo.get(session_id, user_id=user_id)
        if session is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        return session

    async def list_sessions(
        self, *, user_id: uuid.UUID, status: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[EngineeringSession], int]:
        return await self._session_repo.list_page(
            user_id=user_id, status=status, limit=limit, offset=offset
        )

    async def transition_status(
        self,
        session_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        new_status: str,
        participant_id: uuid.UUID,
        reason: str = "",
    ) -> EngineeringSession:
        """Architecture v2.1 §3.1: "not a pipeline... every later state can
        reopen an earlier one, and dormancy is never terminal." This
        method therefore validates only that `new_status` is a real
        Session state (§3.1's fixed vocabulary) and that the Session
        exists (and is owned by `user_id`) — it deliberately does NOT
        enforce a fixed forward-only transition table, because the
        architecture explicitly forbids exactly that rigidity.
        """
        session = await self.get_session(session_id, user_id=user_id)
        if new_status not in SESSION_STATUSES:
            raise ConflictError(
                f"'{new_status}' is not a valid Session status. Architecture v2.1 §3.1 defines: "
                f"{', '.join(SESSION_STATUSES)}."
            )

        previous_status = session.status
        session.status = new_status
        summary = f"Status changed: {previous_status} -> {new_status}."
        if reason:
            summary += f" {reason}"
        await self._timeline.append(
            session_id=session_id,
            participant_id=participant_id,
            kind="status_changed",
            summary=summary,
        )
        await self._db.commit()
        await self._db.refresh(session)
        return session
