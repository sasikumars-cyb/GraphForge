"""The ONLY relationship-lookup helper in the Engineering Intelligence
Service Layer. Every service that needs current relationships plus their
confidence/explanation (`RepositoryProfileService`, `ImpactAnalysisService`,
`ArchitectureInsightService`) calls `fetch_with_confidence` — none of them
re-implements the "fetch relationships, read `.confidence_state`/
`.explanation` off each" loop that the approved design's audit found
independently latent in three places.

Explanations are read straight off `KnowledgeRelationshipRecord.explanation`
(already persisted JSON, via `app.knowledge_engine.serialization
.explanation_from_dict`) — never re-derived by calling
`explain_confidence` again. `explain_confidence` takes a `ConfidenceModel`
plus the `ValidationResult`s that produced it, neither of which this
layer has (or should reconstruct); the persisted explanation is the
single source of truth for "why", exactly as ADR 0018 intends.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.knowledge_engine.serialization import explanation_from_dict
from app.services.engineering_intelligence.contracts import RelationshipInsight


async def fetch_with_confidence(
    db: AsyncSession, repository_id: uuid.UUID
) -> tuple[RelationshipInsight, ...]:
    """Every current (latest-version) relationship for `repository_id`,
    reduced to `RelationshipInsight`. Ordered by `relationship_key` so
    output is deterministic regardless of the repository's insertion
    order."""
    memory = EngineeringMemoryService(db)
    records = await memory.get_current_relationships(repository_id)

    insights = [
        RelationshipInsight(
            relationship_key=record.relationship_key,
            relationship_type=record.relationship_type,
            source_entity=record.source_entity,
            target_entity=record.target_entity,
            confidence_state=record.confidence_state,
            explanation=(explanation_from_dict(record.explanation) if record.explanation else None),
        )
        for record in records
    ]
    return tuple(sorted(insights, key=lambda insight: insight.relationship_key))
