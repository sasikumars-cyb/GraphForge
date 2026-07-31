"""`BeliefService` — Architecture v2.1 §2.2 `Belief` and `Hypothesis`,
§3.2's Hypothesis -> Belief lifecycle.

Deliberately has no `promote_belief` method. Architecture v2.1 §2.2 (Δ
v2.1): "a Belief is never itself promoted; only an independent System
Model Entry is, via copy-with-provenance" — System Model is a Phase 3
concept, out of RFC-001's scope entirely (see RFC-001.md, Known
Limitations). The absence of a promotion method here is the actual
enforcement of that boundary: there is structurally no code path in this
RFC that could let a Belief escape its owning Session.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.belief import Belief, Hypothesis
from app.repositories.belief_repository import BeliefRepository
from app.repositories.session_repository import SessionRepository
from app.services.timeline_service import TimelineService

_MIN_CONFIDENCE = 0.0
_MAX_CONFIDENCE = 1.0


def _validate_confidence(confidence: float) -> None:
    if not (_MIN_CONFIDENCE <= confidence <= _MAX_CONFIDENCE):
        raise ConflictError(f"confidence must be between 0 and 1, got {confidence}.")


class BeliefService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._belief_repo = BeliefRepository(db)
        self._session_repo = SessionRepository(db)
        self._timeline = TimelineService(db)

    async def _require_session(self, session_id: uuid.UUID) -> None:
        if await self._session_repo.get(session_id) is None:
            raise NotFoundError(f"Engineering Session {session_id} not found.")

    async def propose_hypothesis(
        self,
        session_id: uuid.UUID,
        *,
        participant_id: uuid.UUID,
        description: str,
        confidence: float = 0.0,
    ) -> Hypothesis:
        await self._require_session(session_id)
        _validate_confidence(confidence)

        hypothesis = Hypothesis(
            session_id=session_id,
            participant_id=participant_id,
            description=description,
            confidence=confidence,
            status="proposed",
        )
        await self._belief_repo.add_hypothesis(hypothesis)
        await self._timeline.append(
            session_id=session_id,
            participant_id=participant_id,
            kind="hypothesis_proposed",
            summary=f"Hypothesis proposed: {description}",
            artifact_id=hypothesis.id,
        )
        await self._db.commit()
        await self._db.refresh(hypothesis)
        return hypothesis

    async def update_hypothesis_confidence(
        self, hypothesis_id: uuid.UUID, *, participant_id: uuid.UUID, confidence: float, status: str
    ) -> Hypothesis:
        """Architecture v2.1 §3.2: Proposed -> Strengthening/Weakening as
        new Evidence arrives. `status` must be one of "proposed",
        "strengthening", "weakening" here — "resolved"/"rejected" only
        happen via `resolve_hypothesis`/`reject_hypothesis` below, which
        also enforce their own required side effects (a resulting Belief,
        or a recorded reason)."""
        _validate_confidence(confidence)
        if status not in ("proposed", "strengthening", "weakening"):
            raise ConflictError(
                "Use resolve_hypothesis()/reject_hypothesis() to move a Hypothesis to "
                "'resolved'/'rejected' — its required side effects can't happen through a bare "
                "status update."
            )
        hypothesis = await self._belief_repo.get_hypothesis(hypothesis_id)
        if hypothesis is None:
            raise NotFoundError(f"Hypothesis {hypothesis_id} not found.")
        if hypothesis.status in ("resolved", "rejected"):
            raise ConflictError(f"Hypothesis {hypothesis_id} is already {hypothesis.status}.")

        hypothesis.confidence = confidence
        hypothesis.status = status
        await self._timeline.append(
            session_id=hypothesis.session_id,
            participant_id=participant_id,
            kind="hypothesis_updated",
            summary=f"Hypothesis moved to {status} (confidence {confidence:.2f}).",
            artifact_id=hypothesis.id,
        )
        await self._db.commit()
        await self._db.refresh(hypothesis)
        return hypothesis

    async def resolve_hypothesis(
        self,
        hypothesis_id: uuid.UUID,
        *,
        participant_id: uuid.UUID,
        belief_statement: str,
        belief_confidence: float,
    ) -> Belief:
        """Architecture v2.1 §3.2: "Resolved --> [*]: becomes a Belief."
        Creates the Belief and points the Hypothesis at it — the
        Hypothesis itself is never deleted, only marked resolved, so the
        reasoning trail (what was considered before arriving here) stays
        intact.
        """
        _validate_confidence(belief_confidence)
        hypothesis = await self._belief_repo.get_hypothesis(hypothesis_id)
        if hypothesis is None:
            raise NotFoundError(f"Hypothesis {hypothesis_id} not found.")
        if hypothesis.status in ("resolved", "rejected"):
            raise ConflictError(f"Hypothesis {hypothesis_id} is already {hypothesis.status}.")

        belief = Belief(
            session_id=hypothesis.session_id,
            participant_id=participant_id,
            statement=belief_statement,
            confidence=belief_confidence,
            status="formed",
        )
        await self._belief_repo.add_belief(belief)
        await self._db.flush()  # assign belief.id before referencing it

        hypothesis.status = "resolved"
        hypothesis.resolved_belief_id = belief.id

        await self._timeline.append(
            session_id=hypothesis.session_id,
            participant_id=participant_id,
            kind="belief_formed",
            summary=f"Belief formed from resolved hypothesis: {belief_statement}",
            artifact_id=belief.id,
        )
        await self._db.commit()
        await self._db.refresh(belief)
        return belief

    async def reject_hypothesis(
        self, hypothesis_id: uuid.UUID, *, participant_id: uuid.UUID, reason: str
    ) -> Hypothesis:
        """Architecture v2.1 §3.2: "Rejected --> [*]: kept as a recorded
        dead end." A real dead end is informative, not deleted."""
        hypothesis = await self._belief_repo.get_hypothesis(hypothesis_id)
        if hypothesis is None:
            raise NotFoundError(f"Hypothesis {hypothesis_id} not found.")
        if hypothesis.status in ("resolved", "rejected"):
            raise ConflictError(f"Hypothesis {hypothesis_id} is already {hypothesis.status}.")

        hypothesis.status = "rejected"
        await self._timeline.append(
            session_id=hypothesis.session_id,
            participant_id=participant_id,
            kind="hypothesis_rejected",
            summary=f"Hypothesis rejected: {reason}",
            artifact_id=hypothesis.id,
        )
        await self._db.commit()
        await self._db.refresh(hypothesis)
        return hypothesis

    async def revise_belief(
        self, belief_id: uuid.UUID, *, participant_id: uuid.UUID, statement: str, confidence: float
    ) -> Belief:
        """Architecture v2.1 §2.2: "Formed -> revised (new Evidence)."
        Mutates the Belief in place — matching Working Understanding's own
        "mutated in place, never versioned" rule (§3.3): the Belief's
        history lives in the Timeline, not in a second copy of the row.
        """
        _validate_confidence(confidence)
        belief = await self._belief_repo.get_belief(belief_id)
        if belief is None:
            raise NotFoundError(f"Belief {belief_id} not found.")
        if belief.status == "retracted":
            raise ConflictError(f"Belief {belief_id} has been retracted and cannot be revised.")

        belief.statement = statement
        belief.confidence = confidence
        belief.status = "revised"
        await self._timeline.append(
            session_id=belief.session_id,
            participant_id=participant_id,
            kind="belief_revised",
            summary=f"Belief revised: {statement}",
            artifact_id=belief.id,
        )
        await self._db.commit()
        await self._db.refresh(belief)
        return belief

    async def retract_belief(
        self, belief_id: uuid.UUID, *, participant_id: uuid.UUID, reason: str
    ) -> Belief:
        """Architecture v2.1 §1: "Nothing is deleted, things go dormant" —
        a retracted Belief is never removed from Working Understanding's
        history, only excluded from its live view (see
        `UnderstandingService`, which filters retracted Beliefs out by
        default)."""
        belief = await self._belief_repo.get_belief(belief_id)
        if belief is None:
            raise NotFoundError(f"Belief {belief_id} not found.")

        belief.status = "retracted"
        await self._timeline.append(
            session_id=belief.session_id,
            participant_id=participant_id,
            kind="belief_retracted",
            summary=f"Belief retracted: {reason}",
            artifact_id=belief.id,
        )
        await self._db.commit()
        await self._db.refresh(belief)
        return belief

    async def get_belief(self, belief_id: uuid.UUID) -> Belief:
        belief = await self._belief_repo.get_belief(belief_id)
        if belief is None:
            raise NotFoundError(f"Belief {belief_id} not found.")
        return belief

    async def get_hypothesis(self, hypothesis_id: uuid.UUID) -> Hypothesis:
        hypothesis = await self._belief_repo.get_hypothesis(hypothesis_id)
        if hypothesis is None:
            raise NotFoundError(f"Hypothesis {hypothesis_id} not found.")
        return hypothesis

    async def list_beliefs(self, session_id: uuid.UUID) -> list[Belief]:
        await self._require_session(session_id)
        return await self._belief_repo.list_beliefs(session_id, exclude_retracted=False)

    async def list_hypotheses(
        self, session_id: uuid.UUID, *, unresolved_only: bool = False
    ) -> list[Hypothesis]:
        await self._require_session(session_id)
        return await self._belief_repo.list_hypotheses(session_id, unresolved_only=unresolved_only)
