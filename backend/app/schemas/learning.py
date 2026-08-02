"""Request/response schemas for ADR 0018 RFC-07's Learning & Feedback
API — mirrors `app.schemas.engineering_session`'s conventions
(`ConfigDict(from_attributes=True)` on every response model, plain
`BaseModel` request bodies)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FeedbackKindLiteral = Literal[
    "approve",
    "reject",
    "correct_confidence",
    "flag_missing_relationship",
    "flag_weak_evidence",
    "flag_incorrect_explanation",
]


class RelationshipFeedbackRequest(BaseModel):
    kind: FeedbackKindLiteral
    reason: str = Field(min_length=1)
    relationship_type: str | None = None
    source_entity: str | None = None
    target_entity: str | None = None
    corrected_state: str | None = None


class LearningEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    event_type: str
    relationship_key: str | None
    relationship_type: str | None
    generator_names: list[str]
    confidence_state_at_event: str | None
    source_kind: str
    source_identity: str
    detail: str
    event_created_at: datetime
    persisted_at: datetime


class LearningEventListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    events: list[LearningEventResponse]


class RepeatedFalsePositiveSignalResponse(BaseModel):
    relationship_type: str
    generator_name: str | None
    rejection_count: int


class LearningStatisticsResponse(BaseModel):
    repository_id: str
    total_events: int
    counts_by_event_type: dict[str, int]
    counts_by_relationship_type: dict[str, dict[str, int]]
    approval_rate: float | None
    rejection_rate: float | None
    repeated_false_positive_signals: list[RepeatedFalsePositiveSignalResponse]
    trend_by_relationship_type: dict[str, str]
    computed_at: datetime
