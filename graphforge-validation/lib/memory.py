"""Engineering Memory access for Validation 6 (`docs/validation-guide.md`).

No REST endpoint exposes a `KnowledgeRelationship`'s confidence/
explanation/provenance/version-history directly (the agent REST
responses only surface a rendered summary of it) — the only way to check
"is provenance actually being persisted" as a black-box fact is to call
the same read path GraphForge's own code calls:
`EngineeringMemoryService.get_current_relationships` /
`get_relationship_history`. This is GraphForge's real Engineering Memory
API, just at the Python layer instead of HTTP — not a reimplementation
of anything (no Cypher, no manual SQL, no relationship matching).
"""

from __future__ import annotations

import uuid

from lib.bootstrap import ensure_backend_importable
from lib.config import Config

ensure_backend_importable()

from app.database.session import AsyncSessionLocal  # noqa: E402
from app.knowledge_engine.memory_service import EngineeringMemoryService  # noqa: E402
from app.models.knowledge_relationship import KnowledgeRelationshipRecord  # noqa: E402


async def get_current_relationships(repository_id: str) -> list[KnowledgeRelationshipRecord]:
    async with AsyncSessionLocal() as db:
        service = EngineeringMemoryService(db)
        return await service.get_current_relationships(uuid.UUID(repository_id))


async def get_relationship_history(
    repository_id: str, relationship_type: str, source_entity: str, target_entity: str
) -> list[KnowledgeRelationshipRecord]:
    async with AsyncSessionLocal() as db:
        service = EngineeringMemoryService(db)
        return await service.get_relationship_history(
            uuid.UUID(repository_id), relationship_type, source_entity, target_entity
        )


def record_to_dict(record: KnowledgeRelationshipRecord) -> dict:
    return {
        "relationship_key": record.relationship_key,
        "sequence": record.sequence,
        "relationship_type": record.relationship_type,
        "source_entity": record.source_entity,
        "target_entity": record.target_entity,
        "confidence_state": record.confidence_state,
        "has_provenance": bool(record.provenance),
        "provenance_count": len(record.provenance or []),
        "has_explanation": record.explanation is not None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
