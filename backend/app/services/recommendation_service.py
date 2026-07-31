"""`RecommendationService` — Architecture v2.1 §2.2 `Recommendation`.

Implements the review's finding-5 resolution verbatim: "when two Agent
Participants independently propose different Recommendations for the
same open question, that disagreement is recorded as a Contradiction
between the two Recommendation artifacts — the same mechanism, no new
one" (§11). `propose()` is the only place this check happens; it is not
optional and cannot be bypassed by a caller.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.decision import Recommendation
from app.repositories.recommendation_repository import RecommendationRepository
from app.repositories.session_repository import SessionRepository
from app.services.contradiction_service import ContradictionService
from app.services.timeline_service import TimelineService


class RecommendationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._recommendation_repo = RecommendationRepository(db)
        self._session_repo = SessionRepository(db)
        self._timeline = TimelineService(db)
        self._contradictions = ContradictionService(db)

    async def propose(
        self,
        session_id: uuid.UUID,
        *,
        participant_id: uuid.UUID,
        statement: str,
        target_belief_id: uuid.UUID | None = None,
        target_contradiction_id: uuid.UUID | None = None,
    ) -> Recommendation:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")

        recommendation = Recommendation(
            session_id=session_id,
            participant_id=participant_id,
            statement=statement,
            status="proposed",
            target_belief_id=target_belief_id,
            target_contradiction_id=target_contradiction_id,
        )
        await self._recommendation_repo.add(recommendation)
        await self._db.flush()

        # Finding 5's resolution — see module docstring. Only checked when
        # this Recommendation targets a specific Belief: a Contradiction
        # needs two identifiable parties, and "the same open question" is
        # only unambiguous when both Recommendations name the same target.
        conflict_note = ""
        if target_belief_id is not None:
            competing = [
                r
                for r in await self._recommendation_repo.list_for_target_belief(
                    session_id, target_belief_id
                )
                if r.id != recommendation.id and r.statement != statement
            ]
            if competing:
                other = competing[0]
                contradiction = await self._contradictions.detect(
                    session_id,
                    participant_id=participant_id,
                    description=(
                        f"Competing recommendations for the same Belief: "
                        f'"{other.statement}" vs. "{statement}".'
                    ),
                    party_artifact_ids=[recommendation.id, other.id],
                )
                conflict_note = (
                    f" (conflicts with an existing recommendation — "
                    f"see Contradiction {contradiction.id})"
                )

        await self._timeline.append(
            session_id=session_id,
            participant_id=participant_id,
            kind="recommendation_proposed",
            summary=f"Recommendation proposed: {statement}{conflict_note}",
            artifact_id=recommendation.id,
        )
        await self._db.commit()
        await self._db.refresh(recommendation)
        return recommendation

    async def accept(
        self, recommendation_id: uuid.UUID, *, participant_id: uuid.UUID
    ) -> Recommendation:
        """Marks the Recommendation accepted. Deliberately does NOT create
        a Decision — Architecture v2.1 §5: "every agent can only propose;
        only a Human Participant... may commit." Turning an accepted
        Recommendation into a real Decision is `DecisionService.
        commit_from_recommendation`'s job, which separately enforces that
        boundary (see that service).
        """
        recommendation = await self.get(recommendation_id)
        if recommendation.status != "proposed":
            raise ConflictError(
                f"Recommendation {recommendation_id} is {recommendation.status}, not proposed."
            )

        recommendation.status = "accepted"
        await self._timeline.append(
            session_id=recommendation.session_id,
            participant_id=participant_id,
            kind="recommendation_accepted",
            summary=f"Recommendation accepted: {recommendation.statement}",
            artifact_id=recommendation.id,
        )
        await self._db.commit()
        await self._db.refresh(recommendation)
        return recommendation

    async def decline(
        self, recommendation_id: uuid.UUID, *, participant_id: uuid.UUID, reason: str
    ) -> Recommendation:
        recommendation = await self.get(recommendation_id)
        if recommendation.status != "proposed":
            raise ConflictError(
                f"Recommendation {recommendation_id} is {recommendation.status}, not proposed."
            )

        recommendation.status = "declined"
        await self._timeline.append(
            session_id=recommendation.session_id,
            participant_id=participant_id,
            kind="recommendation_declined",
            summary=f"Recommendation declined: {reason}",
            artifact_id=recommendation.id,
        )
        await self._db.commit()
        await self._db.refresh(recommendation)
        return recommendation

    async def get(self, recommendation_id: uuid.UUID) -> Recommendation:
        recommendation = await self._recommendation_repo.get(recommendation_id)
        if recommendation is None:
            raise NotFoundError(f"Recommendation {recommendation_id} not found.")
        return recommendation

    async def list_open(self, session_id: uuid.UUID) -> list[Recommendation]:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        return await self._recommendation_repo.list_open(session_id)
