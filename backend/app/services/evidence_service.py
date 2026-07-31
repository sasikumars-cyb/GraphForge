"""`EvidenceService` — Architecture v2.1 §2.2 `Evidence`.

"Any observed fact, from any source... recorded once, append-only." No
update or delete method exists here at all — matching
`EvidenceRepository`'s own enforcement.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.evidence import Evidence
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.session_repository import SessionRepository
from app.services.timeline_service import TimelineService

_VALID_EVIDENCE_KINDS = (
    "retrieved",
    "code_change",
    "verification_result",
    "production_learning",
)


class EvidenceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._evidence_repo = EvidenceRepository(db)
        self._session_repo = SessionRepository(db)
        self._timeline = TimelineService(db)

    async def record(
        self,
        session_id: uuid.UUID,
        *,
        participant_id: uuid.UUID,
        evidence_kind: str,
        summary: str,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> Evidence:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        if evidence_kind not in _VALID_EVIDENCE_KINDS:
            raise ConflictError(
                f"'{evidence_kind}' is not a valid evidence_kind. Architecture v2.1 §2.2 "
                f"defines: {', '.join(_VALID_EVIDENCE_KINDS)}."
            )

        evidence = Evidence(
            session_id=session_id,
            participant_id=participant_id,
            evidence_kind=evidence_kind,
            summary=summary,
            source=source,
            payload=payload or {},
        )
        await self._evidence_repo.add(evidence)
        await self._timeline.append(
            session_id=session_id,
            participant_id=participant_id,
            kind="evidence_recorded",
            summary=f"Evidence recorded ({evidence_kind}, from {source}): {summary}",
            artifact_id=evidence.id,
        )
        await self._db.commit()
        await self._db.refresh(evidence)
        return evidence

    async def get(self, evidence_id: uuid.UUID) -> Evidence:
        evidence = await self._evidence_repo.get(evidence_id)
        if evidence is None:
            raise NotFoundError(f"Evidence {evidence_id} not found.")
        return evidence

    async def list_page(
        self, session_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Evidence], int]:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        return await self._evidence_repo.list_page(session_id, limit=limit, offset=offset)
