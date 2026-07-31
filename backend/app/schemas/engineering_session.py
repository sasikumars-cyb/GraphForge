"""Request/response schemas for RFC-001's Engineering Session REST API —
Architecture v2.1 §2.2.

Every mutating request accepts an optional `agent_role` (one of
`app.models.participant.AGENT_ROLES`): when present, the artifact is
recorded as authored by that Agent Participant; when absent, it's
recorded as authored by the calling Human Participant (the authenticated
user). `DecisionCommitRequest` has no such field at all — Architecture
v2.1 §5's propose/commit boundary means a Decision's committer is always
the calling human, never negotiable via the request body (see
`DecisionService.commit`, which independently re-enforces this
server-side regardless of what a client sends).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# --- Shared ------------------------------------------------------------


class Page(BaseModel):
    """Generic pagination envelope every list endpoint returns."""

    total: int
    limit: int
    offset: int


class ParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    display_name: str
    agent_role: str | None = None


# --- Session -------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)


class SessionStatusUpdateRequest(BaseModel):
    status: str
    reason: str = ""


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: str
    mission_id: uuid.UUID | None
    created_by_participant_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    page: Page


# --- Timeline --------------------------------------------------------------


class TimelineEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    sequence: int
    participant_id: uuid.UUID
    kind: str
    summary: str
    artifact_id: uuid.UUID | None
    created_at: datetime


class TimelineListResponse(BaseModel):
    items: list[TimelineEntryResponse]
    page: Page


# --- Belief / Hypothesis ----------------------------------------------------


class HypothesisCreateRequest(BaseModel):
    agent_role: str | None = None
    description: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class HypothesisResolveRequest(BaseModel):
    agent_role: str | None = None
    belief_statement: str = Field(min_length=1)
    belief_confidence: float = Field(ge=0.0, le=1.0)


class HypothesisRejectRequest(BaseModel):
    agent_role: str | None = None
    reason: str = Field(min_length=1)


class HypothesisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    participant_id: uuid.UUID
    description: str
    confidence: float
    status: str
    resolved_belief_id: uuid.UUID | None
    created_at: datetime


class BeliefReviseRequest(BaseModel):
    agent_role: str | None = None
    statement: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class BeliefRetractRequest(BaseModel):
    agent_role: str | None = None
    reason: str = Field(min_length=1)


class BeliefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    participant_id: uuid.UUID
    statement: str
    confidence: float
    status: str
    created_at: datetime


class WorkingUnderstandingResponse(BaseModel):
    session_id: uuid.UUID
    beliefs: list[BeliefResponse]
    overall_confidence: float
    belief_count: int


# --- Evidence ----------------------------------------------------------------


class EvidenceCreateRequest(BaseModel):
    agent_role: str | None = None
    evidence_kind: str
    summary: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=255)
    payload: dict = Field(default_factory=dict)


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    participant_id: uuid.UUID
    evidence_kind: str
    summary: str
    source: str
    payload: dict
    created_at: datetime


class EvidenceListResponse(BaseModel):
    items: list[EvidenceResponse]
    page: Page


# --- Recommendation ------------------------------------------------------


class RecommendationCreateRequest(BaseModel):
    agent_role: str | None = None
    statement: str = Field(min_length=1)
    target_belief_id: uuid.UUID | None = None
    target_contradiction_id: uuid.UUID | None = None


class RecommendationDeclineRequest(BaseModel):
    agent_role: str | None = None
    reason: str = Field(min_length=1)


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    participant_id: uuid.UUID
    statement: str
    status: str
    target_belief_id: uuid.UUID | None
    target_contradiction_id: uuid.UUID | None
    created_at: datetime


# --- Decision --------------------------------------------------------------


class DecisionCommitRequest(BaseModel):
    decision_kind: str
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    recommendation_id: uuid.UUID | None = None


class DecisionSupersedeRequest(BaseModel):
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class DecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    decision_kind: str
    statement: str
    rationale: str
    committed_by_participant_id: uuid.UUID
    recommendation_id: uuid.UUID | None
    superseded_by_decision_id: uuid.UUID | None
    created_at: datetime


class DecisionListResponse(BaseModel):
    items: list[DecisionResponse]
    page: Page


# --- Contradiction -----------------------------------------------------------


class ContradictionCreateRequest(BaseModel):
    agent_role: str | None = None
    description: str = Field(min_length=1)
    party_artifact_ids: list[uuid.UUID] = Field(min_length=2)


class ContradictionResolveRequest(BaseModel):
    agent_role: str | None = None
    resolution_note: str = Field(min_length=1)
    resolved_by_decision_id: uuid.UUID | None = None


class ContradictionMarkUnresolvedRequest(BaseModel):
    agent_role: str | None = None
    note: str = Field(min_length=1)


class ContradictionPartyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: uuid.UUID


class ContradictionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    description: str
    status: str
    resolution_note: str | None
    resolved_by_decision_id: uuid.UUID | None
    owner_scope: str
    created_at: datetime
    parties: list[ContradictionPartyResponse] = Field(default_factory=list)


class ContradictionListResponse(BaseModel):
    items: list[ContradictionResponse]
    page: Page
