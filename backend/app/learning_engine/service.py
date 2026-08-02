"""`LearningEngineService` — the one orchestration point between a
submitted `RelationshipFeedback` and two append-only stores:

1. `learning_events` (this RFC) — always written, for every feedback kind.
2. `user_corrections` (RFC-04, via `EngineeringMemoryService.apply_correction`,
   reused unmodified) — written only for the three kinds that assert a new
   relationship state ("approve", "reject", "correct_confidence"). Flags
   ("flag_weak_evidence", "flag_incorrect_explanation",
   "flag_missing_relationship") record a learning signal only — they do
   not assert a corrected state, so there is nothing for RFC-04's
   correction contract to represent.

Both writes happen in the same DB session/transaction the caller passed
in and are committed together here — mirroring
`EngineeringMemoryService.apply_correction`'s own "service layer owns the
commit" convention.

This service never imports `app.knowledge_engine.validators`,
`app.knowledge_engine.confidence`, `app.indexer.hypotheses`, or
`app.knowledge_engine.materializer` — it only reads already-persisted
`KnowledgeRelationshipRecord` rows (for their current confidence state
and provenance) and writes `LearningEventRecord`/`UserCorrectionRecord`
rows. It cannot affect indexing, generation, validation, confidence
computation, or materialization, even by accident — there is no import
path from here into any of them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.correction import UserCorrection
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.learning_engine.aggregation import LearningStatistics, compute_statistics
from app.learning_engine.contracts.feedback import RelationshipFeedback
from app.learning_engine.contracts.learning_event import LearningEvent
from app.learning_engine.engine import build_learning_event
from app.models.learning_event import LearningEventRecord
from app.repositories.learning_repository import LearningEventRepository

_CORRECTION_KINDS = frozenset({"approve", "reject", "correct_confidence"})


def _generator_names_from_provenance(provenance: list[dict]) -> tuple[str, ...]:
    names = []
    for entry in provenance:
        generator = entry.get("generator") if isinstance(entry, dict) else None
        name = generator.get("name") if isinstance(generator, dict) else None
        if name:
            names.append(name)
    return tuple(names)


class LearningEngineService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repository = LearningEventRepository(db)
        self._memory = EngineeringMemoryService(db)

    async def submit_feedback(self, feedback: RelationshipFeedback) -> LearningEventRecord:
        repository_id = uuid.UUID(feedback.repository_id)
        confidence_state_before: str | None = None
        generator_names: tuple[str, ...] = ()

        if feedback.relationship_type and feedback.source_entity and feedback.target_entity:
            history = await self._memory.get_relationship_history(
                repository_id,
                feedback.relationship_type,
                feedback.source_entity,
                feedback.target_entity,
            )
            if history:
                current = history[-1]
                confidence_state_before = current.confidence_state
                generator_names = _generator_names_from_provenance(current.provenance)
            elif feedback.kind in _CORRECTION_KINDS:
                raise NotFoundError(
                    f"No relationship found for {feedback.relationship_type}/"
                    f"{feedback.source_entity}/{feedback.target_entity} — cannot "
                    f"{feedback.kind} a relationship that does not exist"
                )

        event = build_learning_event(
            feedback,
            event_id=str(uuid.uuid4()),
            confidence_state_before=confidence_state_before,
            generator_names=generator_names,
        )

        if feedback.kind in _CORRECTION_KINDS:
            if feedback.kind == "reject":
                corrected_state = None
            elif feedback.kind == "correct_confidence":
                corrected_state = feedback.corrected_state
            else:  # "approve" — endorses the relationship's current state unchanged
                corrected_state = (
                    ConfidenceState(confidence_state_before) if confidence_state_before else None
                )
            correction = UserCorrection(
                id=str(uuid.uuid4()),
                relationship_id=event.relationship_key or "",
                source=feedback.source,
                corrected_state=corrected_state,
                reason=feedback.reason,
                created_at=feedback.created_at,
            )
            assert feedback.relationship_type and feedback.source_entity and feedback.target_entity
            await self._memory.apply_correction(
                repository_id,
                feedback.relationship_type,
                feedback.source_entity,
                feedback.target_entity,
                correction,
            )

        record = _event_to_record(event, repository_id)
        stored = await self._repository.add_event(record)
        await self._db.commit()
        return stored

    async def list_events(
        self,
        repository_id: uuid.UUID,
        *,
        event_type: str | None = None,
        relationship_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LearningEventRecord]:
        return await self._repository.list_events(
            repository_id,
            event_type=event_type,
            relationship_type=relationship_type,
            limit=limit,
            offset=offset,
        )

    async def get_statistics(self, repository_id: uuid.UUID) -> LearningStatistics:
        records = await self._repository.list_all_events_for_statistics(repository_id)
        events = [_record_to_event(record) for record in records]
        return compute_statistics(str(repository_id), events, computed_at=datetime.now(UTC))


def _event_to_record(event: LearningEvent, repository_id: uuid.UUID) -> LearningEventRecord:
    return LearningEventRecord(
        id=uuid.UUID(event.id),
        repository_id=repository_id,
        event_type=event.event_type,
        relationship_key=event.relationship_key,
        relationship_type=event.relationship_type,
        generator_names=list(event.generator_names),
        confidence_state_at_event=event.confidence_state_at_event,
        source_kind=event.source.kind,
        source_identity=event.source.identity,
        source_trust_level=event.source.trust_level,
        detail=event.detail,
        event_created_at=event.created_at,
    )


def _record_to_event(record: LearningEventRecord) -> LearningEvent:
    from app.knowledge_engine.contracts.correction import CorrectionSource

    return LearningEvent(
        id=str(record.id),
        repository_id=str(record.repository_id),
        event_type=record.event_type,  # type: ignore[arg-type]
        source=CorrectionSource(
            kind=record.source_kind,  # type: ignore[arg-type]
            identity=record.source_identity,
            trust_level=record.source_trust_level,
        ),
        detail=record.detail,
        created_at=record.event_created_at,
        relationship_key=record.relationship_key,
        relationship_type=record.relationship_type,
        generator_names=tuple(record.generator_names),
        confidence_state_at_event=record.confidence_state_at_event,
    )
