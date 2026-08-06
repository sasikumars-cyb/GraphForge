"""RFC-001 — Engineering Session REST API. Architecture v2.1 §2.2's
aggregate root, exposed as `/sessions/*`.

Every mutating endpoint resolves an authoring Participant from the
request: an `agent_role` field (validated against
`app.models.participant.AGENT_ROLES`) records the artifact as
agent-authored; its absence records it as authored by the calling,
authenticated human. `POST /sessions/{id}/decisions` has no such field —
Architecture v2.1 §5's propose/commit boundary means a Decision's
committer is always the calling human, enforced again, independently, by
`DecisionService.commit` itself.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.models.participant import Participant
from app.models.user import User
from app.schemas.engineering_session import (
    BeliefResponse,
    BeliefRetractRequest,
    BeliefReviseRequest,
    ContradictionCreateRequest,
    ContradictionListResponse,
    ContradictionMarkUnresolvedRequest,
    ContradictionResolveRequest,
    ContradictionResponse,
    DecisionCommitRequest,
    DecisionListResponse,
    DecisionResponse,
    DecisionSupersedeRequest,
    EvidenceCreateRequest,
    EvidenceListResponse,
    EvidenceResponse,
    HypothesisCreateRequest,
    HypothesisRejectRequest,
    HypothesisResolveRequest,
    HypothesisResponse,
    Page,
    RecommendationCreateRequest,
    RecommendationDeclineRequest,
    RecommendationResponse,
    SessionCreateRequest,
    SessionListResponse,
    SessionResponse,
    SessionStatusUpdateRequest,
    TimelineListResponse,
    WorkingUnderstandingResponse,
)
from app.services.belief_service import BeliefService
from app.services.contradiction_service import ContradictionService
from app.services.decision_service import DecisionService
from app.services.evidence_service import EvidenceService
from app.services.participant_helpers import (
    get_or_create_agent_participant,
    get_or_create_human_participant,
)
from app.services.recommendation_service import RecommendationService
from app.services.session_service import SessionService
from app.services.timeline_service import TimelineService
from app.services.understanding_service import UnderstandingService

router = APIRouter(prefix="/sessions", tags=["engineering-sessions"])


async def _author(db: AsyncSession, current_user: User, agent_role: str | None) -> Participant:
    """The authoring Participant for a mutating request — see module
    docstring for the agent_role/human resolution rule."""
    if agent_role is not None:
        return await get_or_create_agent_participant(db, agent_role)
    return await get_or_create_human_participant(db, current_user)


async def _verified_session_owner(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """KAN-44: the ownership gate every `/sessions/{session_id}/...`
    endpoint below depends on in place of a bare `Depends(get_current_user)`
    — FastAPI resolves `session_id` from the same path parameter the
    endpoint itself declares, so this runs before the handler body, and
    raises the same `NotFoundError` `SessionService.get_session` raises for
    "doesn't exist" and "exists but isn't yours" alike (404-not-403 — see
    that method's own docstring). Returns `current_user` so an endpoint
    that already had `current_user: User = Depends(get_current_user)` for
    its own purposes (participant/author resolution) gets both the check
    and the value from one dependency, not two.
    """
    await SessionService(db).get_session(session_id, user_id=current_user.id)
    return current_user


def _require_same_session(artifact_session_id: uuid.UUID, path_session_id: uuid.UUID) -> None:
    """Enforces composition/ownership at the API boundary, not just in the
    domain model: a Hypothesis/Belief/Recommendation/Decision/Contradiction
    reached through `/sessions/{session_id}/...` must actually belong to
    that Session — a URL naming the wrong Session for a real artifact_id
    is treated as "not found" (never silently redirected to the artifact's
    real Session), matching Architecture v2.1's aggregate-ownership rule.
    """
    if artifact_session_id != path_session_id:
        raise NotFoundError(
            f"No such artifact in Session {path_session_id} (it belongs to a different Session)."
        )


# --- Session -----------------------------------------------------------------


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    session = await SessionService(db).create_session(title=body.title, created_by=current_user)
    return SessionResponse.model_validate(session)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionListResponse:
    items, total = await SessionService(db).list_sessions(
        user_id=current_user.id, status=status, limit=limit, offset=offset
    )
    return SessionListResponse(
        items=[SessionResponse.model_validate(s) for s in items],
        page=Page(total=total, limit=limit, offset=offset),
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    session = await SessionService(db).get_session(session_id, user_id=current_user.id)
    return SessionResponse.model_validate(session)


@router.patch("/{session_id}/status", response_model=SessionResponse)
async def update_session_status(
    session_id: uuid.UUID,
    body: SessionStatusUpdateRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    participant = await get_or_create_human_participant(db, current_user)
    session = await SessionService(db).transition_status(
        session_id,
        user_id=current_user.id,
        new_status=body.status,
        participant_id=participant.id,
        reason=body.reason,
    )
    return SessionResponse.model_validate(session)


# --- Timeline ------------------------------------------------------------


@router.get("/{session_id}/timeline", response_model=TimelineListResponse)
async def get_timeline(
    session_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> TimelineListResponse:
    items, total = await TimelineService(db).list_page(session_id, limit=limit, offset=offset)
    return TimelineListResponse(
        items=list(items),  # Pydantic serializes via from_attributes
        page=Page(total=total, limit=limit, offset=offset),
    )


# --- Understanding ---------------------------------------------------------


@router.get("/{session_id}/understanding", response_model=WorkingUnderstandingResponse)
async def get_working_understanding(
    session_id: uuid.UUID,
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> WorkingUnderstandingResponse:
    wu = await UnderstandingService(db).get_working_understanding(session_id)
    return WorkingUnderstandingResponse(
        session_id=wu.session_id,
        beliefs=[BeliefResponse.model_validate(b) for b in wu.beliefs],
        overall_confidence=wu.overall_confidence,
        belief_count=wu.belief_count,
    )


# --- Hypotheses / Beliefs ----------------------------------------------------


@router.post("/{session_id}/hypotheses", response_model=HypothesisResponse, status_code=201)
async def propose_hypothesis(
    session_id: uuid.UUID,
    body: HypothesisCreateRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> HypothesisResponse:
    author = await _author(db, current_user, body.agent_role)
    hypothesis = await BeliefService(db).propose_hypothesis(
        session_id,
        participant_id=author.id,
        description=body.description,
        confidence=body.confidence,
    )
    return HypothesisResponse.model_validate(hypothesis)


@router.get("/{session_id}/hypotheses", response_model=list[HypothesisResponse])
async def list_hypotheses(
    session_id: uuid.UUID,
    unresolved_only: bool = Query(default=False),
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> list[HypothesisResponse]:
    hypotheses = await BeliefService(db).list_hypotheses(
        session_id, unresolved_only=unresolved_only
    )
    return [HypothesisResponse.model_validate(h) for h in hypotheses]


@router.post(
    "/{session_id}/hypotheses/{hypothesis_id}/resolve",
    response_model=BeliefResponse,
    status_code=201,
)
async def resolve_hypothesis(
    session_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    body: HypothesisResolveRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> BeliefResponse:
    belief_service = BeliefService(db)
    existing = await belief_service.get_hypothesis(hypothesis_id)
    _require_same_session(existing.session_id, session_id)
    author = await _author(db, current_user, body.agent_role)
    belief = await belief_service.resolve_hypothesis(
        hypothesis_id,
        participant_id=author.id,
        belief_statement=body.belief_statement,
        belief_confidence=body.belief_confidence,
    )
    return BeliefResponse.model_validate(belief)


@router.post("/{session_id}/hypotheses/{hypothesis_id}/reject", response_model=HypothesisResponse)
async def reject_hypothesis(
    session_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    body: HypothesisRejectRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> HypothesisResponse:
    belief_service = BeliefService(db)
    existing = await belief_service.get_hypothesis(hypothesis_id)
    _require_same_session(existing.session_id, session_id)
    author = await _author(db, current_user, body.agent_role)
    hypothesis = await belief_service.reject_hypothesis(
        hypothesis_id, participant_id=author.id, reason=body.reason
    )
    return HypothesisResponse.model_validate(hypothesis)


@router.get("/{session_id}/beliefs", response_model=list[BeliefResponse])
async def list_beliefs(
    session_id: uuid.UUID,
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> list[BeliefResponse]:
    beliefs = await BeliefService(db).list_beliefs(session_id)
    return [BeliefResponse.model_validate(b) for b in beliefs]


@router.post("/{session_id}/beliefs/{belief_id}/revise", response_model=BeliefResponse)
async def revise_belief(
    session_id: uuid.UUID,
    belief_id: uuid.UUID,
    body: BeliefReviseRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> BeliefResponse:
    belief_service = BeliefService(db)
    existing = await belief_service.get_belief(belief_id)
    _require_same_session(existing.session_id, session_id)
    author = await _author(db, current_user, body.agent_role)
    belief = await belief_service.revise_belief(
        belief_id, participant_id=author.id, statement=body.statement, confidence=body.confidence
    )
    return BeliefResponse.model_validate(belief)


@router.post("/{session_id}/beliefs/{belief_id}/retract", response_model=BeliefResponse)
async def retract_belief(
    session_id: uuid.UUID,
    belief_id: uuid.UUID,
    body: BeliefRetractRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> BeliefResponse:
    belief_service = BeliefService(db)
    existing = await belief_service.get_belief(belief_id)
    _require_same_session(existing.session_id, session_id)
    author = await _author(db, current_user, body.agent_role)
    belief = await belief_service.retract_belief(
        belief_id, participant_id=author.id, reason=body.reason
    )
    return BeliefResponse.model_validate(belief)


# --- Evidence ----------------------------------------------------------------


@router.post("/{session_id}/evidence", response_model=EvidenceResponse, status_code=201)
async def record_evidence(
    session_id: uuid.UUID,
    body: EvidenceCreateRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> EvidenceResponse:
    author = await _author(db, current_user, body.agent_role)
    evidence = await EvidenceService(db).record(
        session_id,
        participant_id=author.id,
        evidence_kind=body.evidence_kind,
        summary=body.summary,
        source=body.source,
        payload=body.payload,
    )
    return EvidenceResponse.model_validate(evidence)


@router.get("/{session_id}/evidence", response_model=EvidenceListResponse)
async def list_evidence(
    session_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> EvidenceListResponse:
    items, total = await EvidenceService(db).list_page(session_id, limit=limit, offset=offset)
    return EvidenceListResponse(
        items=[EvidenceResponse.model_validate(e) for e in items],
        page=Page(total=total, limit=limit, offset=offset),
    )


# --- Recommendations -----------------------------------------------------


@router.post(
    "/{session_id}/recommendations", response_model=RecommendationResponse, status_code=201
)
async def propose_recommendation(
    session_id: uuid.UUID,
    body: RecommendationCreateRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> RecommendationResponse:
    author = await _author(db, current_user, body.agent_role)
    recommendation = await RecommendationService(db).propose(
        session_id,
        participant_id=author.id,
        statement=body.statement,
        target_belief_id=body.target_belief_id,
        target_contradiction_id=body.target_contradiction_id,
    )
    return RecommendationResponse.model_validate(recommendation)


@router.get("/{session_id}/recommendations", response_model=list[RecommendationResponse])
async def list_open_recommendations(
    session_id: uuid.UUID,
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> list[RecommendationResponse]:
    recommendations = await RecommendationService(db).list_open(session_id)
    return [RecommendationResponse.model_validate(r) for r in recommendations]


@router.post(
    "/{session_id}/recommendations/{recommendation_id}/accept",
    response_model=RecommendationResponse,
)
async def accept_recommendation(
    session_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> RecommendationResponse:
    recommendation_service = RecommendationService(db)
    existing = await recommendation_service.get(recommendation_id)
    _require_same_session(existing.session_id, session_id)
    participant = await get_or_create_human_participant(db, current_user)
    recommendation = await recommendation_service.accept(
        recommendation_id, participant_id=participant.id
    )
    return RecommendationResponse.model_validate(recommendation)


@router.post(
    "/{session_id}/recommendations/{recommendation_id}/decline",
    response_model=RecommendationResponse,
)
async def decline_recommendation(
    session_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    body: RecommendationDeclineRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> RecommendationResponse:
    recommendation_service = RecommendationService(db)
    existing = await recommendation_service.get(recommendation_id)
    _require_same_session(existing.session_id, session_id)
    author = await _author(db, current_user, body.agent_role)
    recommendation = await recommendation_service.decline(
        recommendation_id, participant_id=author.id, reason=body.reason
    )
    return RecommendationResponse.model_validate(recommendation)


# --- Decisions ---------------------------------------------------------------


@router.post("/{session_id}/decisions", response_model=DecisionResponse, status_code=201)
async def commit_decision(
    session_id: uuid.UUID,
    body: DecisionCommitRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> DecisionResponse:
    # No agent_role resolution here at all, by design — see module
    # docstring. The committer is always the calling human.
    committer = await get_or_create_human_participant(db, current_user)
    decision = await DecisionService(db).commit(
        session_id,
        committed_by_participant_id=committer.id,
        decision_kind=body.decision_kind,
        statement=body.statement,
        rationale=body.rationale,
        recommendation_id=body.recommendation_id,
    )
    return DecisionResponse.model_validate(decision)


@router.get("/{session_id}/decisions", response_model=DecisionListResponse)
async def list_decisions(
    session_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> DecisionListResponse:
    items, total = await DecisionService(db).list_page(session_id, limit=limit, offset=offset)
    return DecisionListResponse(
        items=[DecisionResponse.model_validate(d) for d in items],
        page=Page(total=total, limit=limit, offset=offset),
    )


@router.post("/{session_id}/decisions/{decision_id}/supersede", response_model=DecisionResponse)
async def supersede_decision(
    session_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: DecisionSupersedeRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> DecisionResponse:
    decision_service = DecisionService(db)
    existing = await decision_service.get(decision_id)
    _require_same_session(existing.session_id, session_id)
    committer = await get_or_create_human_participant(db, current_user)
    decision = await decision_service.supersede(
        decision_id,
        committed_by_participant_id=committer.id,
        statement=body.statement,
        rationale=body.rationale,
    )
    return DecisionResponse.model_validate(decision)


# --- Contradictions ------------------------------------------------------


@router.post("/{session_id}/contradictions", response_model=ContradictionResponse, status_code=201)
async def detect_contradiction(
    session_id: uuid.UUID,
    body: ContradictionCreateRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ContradictionResponse:
    author = await _author(db, current_user, body.agent_role)
    contradiction = await ContradictionService(db).detect(
        session_id,
        participant_id=author.id,
        description=body.description,
        party_artifact_ids=body.party_artifact_ids,
    )
    return ContradictionResponse.model_validate(contradiction)


@router.get("/{session_id}/contradictions", response_model=ContradictionListResponse)
async def list_contradictions(
    session_id: uuid.UUID,
    unresolved_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ContradictionListResponse:
    items, total = await ContradictionService(db).list_page(
        session_id, unresolved_only=unresolved_only, limit=limit, offset=offset
    )
    return ContradictionListResponse(
        items=[ContradictionResponse.model_validate(c) for c in items],
        page=Page(total=total, limit=limit, offset=offset),
    )


@router.post(
    "/{session_id}/contradictions/{contradiction_id}/resolve",
    response_model=ContradictionResponse,
)
async def resolve_contradiction(
    session_id: uuid.UUID,
    contradiction_id: uuid.UUID,
    body: ContradictionResolveRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ContradictionResponse:
    contradiction_service = ContradictionService(db)
    existing = await contradiction_service.get(contradiction_id)
    _require_same_session(existing.session_id, session_id)
    author = await _author(db, current_user, body.agent_role)
    contradiction = await contradiction_service.resolve(
        contradiction_id,
        participant_id=author.id,
        resolution_note=body.resolution_note,
        resolved_by_decision_id=body.resolved_by_decision_id,
    )
    return ContradictionResponse.model_validate(contradiction)


@router.post(
    "/{session_id}/contradictions/{contradiction_id}/mark-unresolved",
    response_model=ContradictionResponse,
)
async def mark_contradiction_unresolved(
    session_id: uuid.UUID,
    contradiction_id: uuid.UUID,
    body: ContradictionMarkUnresolvedRequest,
    current_user: User = Depends(_verified_session_owner),
    db: AsyncSession = Depends(get_db_session),
) -> ContradictionResponse:
    contradiction_service = ContradictionService(db)
    existing = await contradiction_service.get(contradiction_id)
    _require_same_session(existing.session_id, session_id)
    author = await _author(db, current_user, body.agent_role)
    contradiction = await contradiction_service.mark_unresolved(
        contradiction_id, participant_id=author.id, note=body.note
    )
    return ContradictionResponse.model_validate(contradiction)
