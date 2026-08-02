"""ADR 0018 RFC-07 — Learning & Feedback Engine REST API.

This is also where "can users approve/reject a relationship at all"
(Phase 1 audit questions 1-2) gets its first real answer: RFC-04 built
`UserCorrection`/`apply_correction` but never exposed them; this router is
the first caller. `LearningEngineService.submit_feedback` (reused here,
not duplicated) is what actually threads a correction- kind feedback
submission into both `user_corrections` and `learning_events`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource
from app.learning_engine.aggregation import LearningStatistics
from app.learning_engine.contracts.feedback import RelationshipFeedback
from app.learning_engine.service import LearningEngineService
from app.models.learning_event import LearningEventRecord
from app.models.repository import Repository
from app.models.user import User
from app.schemas.learning import (
    LearningEventListResponse,
    LearningEventResponse,
    LearningStatisticsResponse,
    RelationshipFeedbackRequest,
    RepeatedFalsePositiveSignalResponse,
)

router = APIRouter(prefix="/repositories/{repository_id}/learning", tags=["learning"])


async def _get_owned_repository(
    db: AsyncSession, repository_id: uuid.UUID, current_user: User
) -> Repository:
    result = await db.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.user_id == current_user.id
        )
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found.")
    return repository


def _record_to_response(record: LearningEventRecord) -> LearningEventResponse:
    return LearningEventResponse.model_validate(record)


def _statistics_to_response(statistics: LearningStatistics) -> LearningStatisticsResponse:
    return LearningStatisticsResponse(
        repository_id=statistics.repository_id,
        total_events=statistics.total_events,
        counts_by_event_type=statistics.counts_by_event_type,
        counts_by_relationship_type=statistics.counts_by_relationship_type,
        approval_rate=statistics.approval_rate,
        rejection_rate=statistics.rejection_rate,
        repeated_false_positive_signals=[
            RepeatedFalsePositiveSignalResponse(
                relationship_type=signal.relationship_type,
                generator_name=signal.generator_name,
                rejection_count=signal.rejection_count,
            )
            for signal in statistics.repeated_false_positive_signals
        ],
        trend_by_relationship_type=statistics.trend_by_relationship_type,
        computed_at=statistics.computed_at,
    )


@router.post("/feedback", response_model=LearningEventResponse)
async def submit_feedback(
    repository_id: uuid.UUID,
    request: RelationshipFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> LearningEventRecord:
    await _get_owned_repository(db, repository_id, current_user)

    feedback = RelationshipFeedback(
        repository_id=str(repository_id),
        source=CorrectionSource(kind="human", identity=current_user.email, trust_level=1.0),
        kind=request.kind,
        reason=request.reason,
        created_at=datetime.now(UTC),
        relationship_type=request.relationship_type,
        source_entity=request.source_entity,
        target_entity=request.target_entity,
        corrected_state=(
            ConfidenceState(request.corrected_state) if request.corrected_state else None
        ),
    )

    service = LearningEngineService(db)
    return await service.submit_feedback(feedback)


@router.get("/events", response_model=LearningEventListResponse)
async def list_events(
    repository_id: uuid.UUID,
    event_type: str | None = Query(default=None),
    relationship_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> LearningEventListResponse:
    await _get_owned_repository(db, repository_id, current_user)

    service = LearningEngineService(db)
    records = await service.list_events(
        repository_id,
        event_type=event_type,
        relationship_type=relationship_type,
        limit=limit,
        offset=offset,
    )
    return LearningEventListResponse(
        total=len(records),
        limit=limit,
        offset=offset,
        events=[_record_to_response(record) for record in records],
    )


@router.get("/statistics", response_model=LearningStatisticsResponse)
async def get_statistics(
    repository_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> LearningStatisticsResponse:
    await _get_owned_repository(db, repository_id, current_user)

    service = LearningEngineService(db)
    statistics = await service.get_statistics(repository_id)
    return _statistics_to_response(statistics)
