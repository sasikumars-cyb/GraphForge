"""`ContradictionService` — Architecture v2.1 §2.2 `Contradiction` (Δ
v2.1: N-ary, with a deterministic ownership rule).

Two invariants enforced here, not by a database constraint, because both
require a query (not just a column check) to validate:

1. **N-ary minimum.** "References two or more conflicting Engineering
   Artifacts" (§2.2) — `detect()` rejects fewer than two parties.
2. **Aggregate consistency / ownership.** "Always the narrowest scope
   containing every disputing party" (§2.2, Δ v2.1, resolving finding 4).
   RFC-001 has no Mission aggregate, so the rule collapses to: every
   party must belong to the *same* Engineering Session as the
   Contradiction itself — a party from another Session would mean the
   Contradiction's real scope is wider than "session," which this RFC
   cannot yet express (see RFC-001.md, Known Limitations). `detect()`
   verifies this by loading each party's own `session_id` and comparing
   it, not by trusting the caller.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.contradiction import Contradiction
from app.models.engineering_artifact import EngineeringArtifact
from app.repositories.contradiction_repository import ContradictionRepository
from app.repositories.session_repository import SessionRepository
from app.services.timeline_service import TimelineService

_MIN_PARTIES = 2


class ContradictionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._contradiction_repo = ContradictionRepository(db)
        self._session_repo = SessionRepository(db)
        self._timeline = TimelineService(db)

    async def detect(
        self,
        session_id: uuid.UUID,
        *,
        participant_id: uuid.UUID,
        description: str,
        party_artifact_ids: list[uuid.UUID],
    ) -> Contradiction:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")

        unique_party_ids = list(dict.fromkeys(party_artifact_ids))
        if len(unique_party_ids) < _MIN_PARTIES:
            raise ConflictError(
                f"A Contradiction requires at least {_MIN_PARTIES} distinct disputing "
                f"Engineering Artifacts (Architecture v2.1 §2.2, N-ary) — got "
                f"{len(unique_party_ids)}."
            )

        for artifact_id in unique_party_ids:
            artifact = await self._db.get(EngineeringArtifact, artifact_id)
            if artifact is None:
                raise NotFoundError(f"Engineering Artifact {artifact_id} not found.")
            if artifact.session_id != session_id:
                # Aggregate-consistency check — see module docstring.
                raise ConflictError(
                    f"Engineering Artifact {artifact_id} belongs to Session "
                    f"{artifact.session_id}, not {session_id}. A Contradiction spanning "
                    "artifacts from different Sessions would need Mission-scoped ownership "
                    "(Architecture v2.1 §2.2's 'narrowest common scope' rule), which is out "
                    "of scope for RFC-001 — see RFC-001.md."
                )

        contradiction = Contradiction(
            session_id=session_id,
            participant_id=participant_id,
            description=description,
            status="detected",
            owner_scope="session",
        )
        await self._contradiction_repo.add(contradiction, unique_party_ids)
        await self._timeline.append(
            session_id=session_id,
            participant_id=participant_id,
            kind="contradiction_detected",
            summary=f"Contradiction detected among {len(unique_party_ids)} artifact(s): "
            f"{description}",
            artifact_id=contradiction.id,
        )
        await self._db.commit()
        return await self.get(contradiction.id)

    async def resolve(
        self,
        contradiction_id: uuid.UUID,
        *,
        participant_id: uuid.UUID,
        resolution_note: str,
        resolved_by_decision_id: uuid.UUID | None = None,
    ) -> Contradiction:
        contradiction = await self.get(contradiction_id)
        if contradiction.status == "resolved":
            raise ConflictError(f"Contradiction {contradiction_id} is already resolved.")

        contradiction.status = "resolved"
        contradiction.resolution_note = resolution_note
        contradiction.resolved_by_decision_id = resolved_by_decision_id
        await self._timeline.append(
            session_id=contradiction.session_id,
            participant_id=participant_id,
            kind="contradiction_resolved",
            summary=f"Contradiction resolved: {resolution_note}",
            artifact_id=contradiction.id,
        )
        await self._db.commit()
        return await self.get(contradiction_id)

    async def mark_unresolved(
        self, contradiction_id: uuid.UUID, *, participant_id: uuid.UUID, note: str
    ) -> Contradiction:
        """Architecture v2.1 §2.2: "...or documented as an open, unresolved
        disagreement if it can't be [resolved]." Distinct from `resolve`:
        this is an honest terminal state, not a resolution."""
        contradiction = await self.get(contradiction_id)
        if contradiction.status == "resolved":
            raise ConflictError(f"Contradiction {contradiction_id} is already resolved.")

        contradiction.status = "unresolved"
        contradiction.resolution_note = note
        await self._timeline.append(
            session_id=contradiction.session_id,
            participant_id=participant_id,
            kind="contradiction_marked_unresolved",
            summary=f"Contradiction documented as unresolved: {note}",
            artifact_id=contradiction.id,
        )
        await self._db.commit()
        return await self.get(contradiction_id)

    async def get(self, contradiction_id: uuid.UUID) -> Contradiction:
        contradiction = await self._contradiction_repo.get(contradiction_id)
        if contradiction is None:
            raise NotFoundError(f"Contradiction {contradiction_id} not found.")
        return contradiction

    async def list_page(
        self,
        session_id: uuid.UUID,
        *,
        unresolved_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Contradiction], int]:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        return await self._contradiction_repo.list_page(
            session_id, unresolved_only=unresolved_only, limit=limit, offset=offset
        )

    async def list_for_artifact(self, artifact_id: uuid.UUID) -> list[Contradiction]:
        return await self._contradiction_repo.list_by_artifact(artifact_id)
