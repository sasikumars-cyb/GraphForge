"""`DecisionService` — Architecture v2.1 §2.2 `Decision`.

Enforces Architecture v2.1 §5's strictest rule: "every agent can only
propose; only a Human Participant... may commit." `commit()` loads the
committing Participant and rejects the call outright if it is of kind
"agent" — this is the one invariant in RFC-001 that most directly
prevents "multi-agent" from silently becoming "an agent approves its own
work."

Phase 7 (Architecture v2.1 §9)'s Delegation Policy — letting a
non-human, policy-scoped Participant commit narrow Decision kinds — is
out of scope for RFC-001 entirely: Policy does not exist as a persisted
concept in this RFC (see RFC-001.md, Known Limitations). `commit()`'s
human-only check is therefore unconditional here, not configurable.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.decision import Decision
from app.repositories.decision_repository import DecisionRepository
from app.repositories.recommendation_repository import RecommendationRepository
from app.repositories.session_repository import SessionRepository
from app.services.participant_helpers import require_participant
from app.services.timeline_service import TimelineService

_VALID_DECISION_KINDS = ("planning_strategy", "review")


class DecisionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._decision_repo = DecisionRepository(db)
        self._recommendation_repo = RecommendationRepository(db)
        self._session_repo = SessionRepository(db)
        self._timeline = TimelineService(db)

    async def commit(
        self,
        session_id: uuid.UUID,
        *,
        committed_by_participant_id: uuid.UUID,
        decision_kind: str,
        statement: str,
        rationale: str,
        recommendation_id: uuid.UUID | None = None,
    ) -> Decision:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        if decision_kind not in _VALID_DECISION_KINDS:
            raise ConflictError(
                f"'{decision_kind}' is not a valid decision_kind. Architecture v2.1 §2.2 "
                f"defines: {', '.join(_VALID_DECISION_KINDS)}."
            )

        committer = await require_participant(self._db, committed_by_participant_id)
        if committer.kind != "human":
            raise ForbiddenError(
                "Only a Human Participant may commit a Decision (Architecture v2.1 §5) — "
                f"Participant {committed_by_participant_id} is an agent "
                f"('{committer.agent_role}'). Delegated, policy-scoped commit authority is "
                "Architecture v2.1 Phase 7 and out of scope for RFC-001."
            )

        recommendation = None
        if recommendation_id is not None:
            recommendation = await self._recommendation_repo.get(recommendation_id)
            if recommendation is None:
                raise NotFoundError(f"Recommendation {recommendation_id} not found.")
            if recommendation.session_id != session_id:
                raise ConflictError(
                    f"Recommendation {recommendation_id} belongs to a different Session."
                )

        decision = Decision(
            session_id=session_id,
            participant_id=committed_by_participant_id,
            committed_by_participant_id=committed_by_participant_id,
            decision_kind=decision_kind,
            statement=statement,
            rationale=rationale,
            recommendation_id=recommendation_id,
        )
        await self._decision_repo.add(decision)

        if recommendation is not None and recommendation.status == "proposed":
            recommendation.status = "accepted"

        await self._timeline.append(
            session_id=session_id,
            participant_id=committed_by_participant_id,
            kind="decision_committed",
            summary=f"Decision committed ({decision_kind}): {statement}",
            artifact_id=decision.id,
        )
        await self._db.commit()
        await self._db.refresh(decision)
        return decision

    async def supersede(
        self,
        decision_id: uuid.UUID,
        *,
        committed_by_participant_id: uuid.UUID,
        statement: str,
        rationale: str,
    ) -> Decision:
        """Architecture v2.1 §2.2: "a change of mind is a new Decision that
        supersedes it, with the old one kept for history." Never mutates
        the old Decision's own content — only its `superseded_by_decision_id`
        pointer."""
        old_decision = await self.get(decision_id)
        committer = await require_participant(self._db, committed_by_participant_id)
        if committer.kind != "human":
            raise ForbiddenError(
                "Only a Human Participant may commit a Decision (Architecture v2.1 §5)."
            )

        new_decision = Decision(
            session_id=old_decision.session_id,
            participant_id=committed_by_participant_id,
            committed_by_participant_id=committed_by_participant_id,
            decision_kind=old_decision.decision_kind,
            statement=statement,
            rationale=rationale,
        )
        await self._decision_repo.add(new_decision)
        old_decision.superseded_by_decision_id = new_decision.id

        await self._timeline.append(
            session_id=old_decision.session_id,
            participant_id=committed_by_participant_id,
            kind="decision_superseded",
            summary=f"Decision superseded: {statement}",
            artifact_id=new_decision.id,
        )
        await self._db.commit()
        await self._db.refresh(new_decision)
        return new_decision

    async def get(self, decision_id: uuid.UUID) -> Decision:
        decision = await self._decision_repo.get(decision_id)
        if decision is None:
            raise NotFoundError(f"Decision {decision_id} not found.")
        return decision

    async def list_page(
        self, session_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Decision], int]:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")
        return await self._decision_repo.list_page(session_id, limit=limit, offset=offset)
