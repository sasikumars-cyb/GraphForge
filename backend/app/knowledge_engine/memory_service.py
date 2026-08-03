"""ADR 0018 RFC-04 — `EngineeringMemoryService`: orchestrates persistence
of already-validated, already-confidence-scored knowledge. Every method
here is store/retrieve/apply — no hypothesis generation, no validation, no
confidence aggregation. Those stay exactly where RFC-02/03/03A put them
(`app.indexer.hypotheses`, `app.knowledge_engine.validators`,
`app.knowledge_engine.confidence`); this module only ever receives their
finished output.

Commits happen here (service layer), not in
`app.repositories.engineering_memory_repository.EngineeringMemoryRepository`
(which only flushes) — the same division this codebase already uses
between `app.repositories.session_repository.SessionRepository` (flush
only) and higher-level orchestration that owns the transaction boundary
(e.g. `app.ai.services.persistence.persist_ai_analysis_result`, which
commits directly).
"""

from __future__ import annotations

import gzip
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge_engine.contracts.correction import UserCorrection
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.explanation import ConfidenceExplanation
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.serialization import (
    evidence_pack_from_dict,
    evidence_pack_to_dict,
    explanation_to_dict,
    provenance_to_dict,
)
from app.models.engineering_evidence_pack import EngineeringEvidencePackRecord
from app.models.knowledge_relationship import KnowledgeRelationshipRecord
from app.models.user_correction import UserCorrectionRecord
from app.repositories.engineering_memory_repository import EngineeringMemoryRepository


def relationship_key_for(
    repository_id: uuid.UUID, relationship_type: str, source_entity: str, target_entity: str
) -> str:
    """The deterministic identity a relationship's version history is
    keyed by — independent of confidence or time (module docstring in
    `app.models.knowledge_relationship` explains why)."""
    return f"{repository_id}:{relationship_type}:{source_entity}:{target_entity}"


class EngineeringMemoryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repository = EngineeringMemoryRepository(db)

    # -- Evidence packs -----------------------------------------------

    async def store_evidence_pack(
        self, repository_id: uuid.UUID, pack: EngineeringEvidencePack
    ) -> EngineeringEvidencePackRecord:
        payload = json.dumps(evidence_pack_to_dict(pack), sort_keys=True).encode("utf-8")
        compressed = gzip.compress(payload)
        record = EngineeringEvidencePackRecord(
            pack_id=pack.id,
            repository_id=repository_id,
            commit_sha=pack.commit_sha,
            schema_version=pack.schema_version,
            is_delta=pack.is_delta,
            base_pack_id=pack.base_pack_id,
            item_count=len(pack.items),
            compressed_blob=compressed,
            compression="gzip",
            uncompressed_size_bytes=len(payload),
            produced_at=pack.produced_at,
        )
        stored = await self._repository.add_evidence_pack(record)
        await self._db.commit()
        return stored

    async def list_evidence_packs(
        self, repository_id: uuid.UUID, *, exclude_commit_sha: str | None = None, limit: int = 50
    ) -> list[EngineeringEvidencePackRecord]:
        """Every evidence pack persisted for `repository_id`, most recent
        first — a thin passthrough to the repository layer (ADR 0018
        RFC-06), letting a caller discover which pack ids exist for a
        repository without already knowing one. Returns records, not
        decompressed contracts: callers that need the full pack still go
        through `retrieve_evidence_pack(pack_id)` for that.

        `exclude_commit_sha`, if given, filters at the SQL level rather
        than client-side — needed because a repository's cross-repository
        evidence packs (`commit_sha="n/a-cross-repo"`, stamped by
        `validators/cross_repo.py`) are re-persisted on every account
        relink (once per `run_indexing` call, for every repository pair
        with a hypothesis), so over enough relinks they can outnumber a
        repository's own single-repo packs within any fixed `limit` — a
        caller wanting "the latest single-repo pack" needs the database to
        exclude cross-repo rows before applying `limit`, not after."""
        return await self._repository.list_evidence_packs(
            repository_id, exclude_commit_sha=exclude_commit_sha, limit=limit
        )

    async def retrieve_evidence_pack(self, pack_id: str) -> EngineeringEvidencePack | None:
        """Replay: decompress and deserialize the most recently persisted
        copy of this pack back into the exact contract shape it was
        stored from."""
        record = await self._repository.get_evidence_pack_by_pack_id(pack_id)
        if record is None:
            return None
        payload = gzip.decompress(record.compressed_blob)
        return evidence_pack_from_dict(json.loads(payload))

    # -- Knowledge relationships ----------------------------------------

    def _relationship_to_record(
        self,
        repository_id: uuid.UUID,
        relationship: KnowledgeRelationship,
        explanation: ConfidenceExplanation | None = None,
    ) -> KnowledgeRelationshipRecord:
        confidence = relationship.confidence
        return KnowledgeRelationshipRecord(
            relationship_key=relationship_key_for(
                repository_id,
                relationship.relationship_type,
                relationship.source_entity,
                relationship.target_entity,
            ),
            repository_id=repository_id,
            relationship_type=relationship.relationship_type,
            source_entity=relationship.source_entity,
            target_entity=relationship.target_entity,
            hypothesis_ids=list(relationship.hypothesis_ids),
            confidence_state=confidence.state.value,
            distinct_confirming_source_types=confidence.distinct_confirming_source_types,
            confirming_source_types=sorted(confidence.confirming_source_types),
            max_confirming_reliability_tier=confidence.max_confirming_reliability_tier,
            contradiction_count=confidence.contradiction_count,
            confidence_formula_version=confidence.formula_version,
            confidence_computed_at=confidence.computed_at,
            provenance=[provenance_to_dict(p) for p in relationship.provenance],
            explanation=explanation_to_dict(explanation) if explanation is not None else None,
        )

    async def store_relationship(
        self,
        repository_id: uuid.UUID,
        relationship: KnowledgeRelationship,
        explanation: ConfidenceExplanation | None = None,
    ) -> KnowledgeRelationshipRecord:
        """Always inserts a new version row — never updates a prior one,
        even for the same `relationship_key`. See
        `app.models.knowledge_relationship`'s module docstring. Commits
        immediately — for persisting many relationships from one run, use
        `store_relationships` instead (one commit for the whole batch,
        not one per relationship).

        `explanation` (ADR 0018 Confidence Explainability) is optional and
        additive — every existing caller that omits it gets byte-identical
        behavior to before this parameter existed."""
        record = self._relationship_to_record(repository_id, relationship, explanation)
        stored = await self._repository.add_relationship_version(record)
        await self._db.commit()
        return stored

    async def store_relationships(
        self,
        repository_id: uuid.UUID,
        relationships: list[KnowledgeRelationship],
        explanations: list[ConfidenceExplanation | None] | None = None,
    ) -> list[KnowledgeRelationshipRecord]:
        """Batch form of `store_relationship` — every relationship is
        flushed individually (so each gets its own row/id immediately) but
        the whole batch commits once. A repository's shadow pipeline can
        produce thousands of relationships per run (RFC-03A measured
        4,887 for a 359-file repository); committing once per relationship
        would be needlessly slow for no correctness benefit — this is a
        persistence-efficiency choice, not a reasoning one.

        `explanations`, if given, must be the same length as
        `relationships` and positionally aligned with it — `None` entries
        are allowed (a relationship with no applicable validator has
        nothing to explain). Omitting `explanations` entirely (the default)
        is byte-identical to this method's pre-Explainability behavior."""
        if explanations is not None and len(explanations) != len(relationships):
            raise ValueError(
                "store_relationships: explanations must be the same length as relationships"
            )
        aligned_explanations = explanations or [None] * len(relationships)
        records = [
            self._relationship_to_record(repository_id, relationship, explanation)
            for relationship, explanation in zip(relationships, aligned_explanations, strict=True)
        ]
        stored = []
        for record in records:
            stored.append(await self._repository.add_relationship_version(record))
        await self._db.commit()
        return stored

    async def get_current_relationships(
        self, repository_id: uuid.UUID
    ) -> list[KnowledgeRelationshipRecord]:
        return await self._repository.get_current_relationships(repository_id)

    async def get_relationship_history(
        self,
        repository_id: uuid.UUID,
        relationship_type: str,
        source_entity: str,
        target_entity: str,
    ) -> list[KnowledgeRelationshipRecord]:
        key = relationship_key_for(repository_id, relationship_type, source_entity, target_entity)
        return await self._repository.get_relationship_history(key)

    # -- User corrections ------------------------------------------------

    async def apply_correction(
        self,
        repository_id: uuid.UUID,
        relationship_type: str,
        source_entity: str,
        target_entity: str,
        correction: UserCorrection,
    ) -> UserCorrectionRecord:
        """Records the correction. Does not itself recompute confidence or
        mutate any `KnowledgeRelationshipRecord` — applying a correction's
        *effect* on confidence is validation/confidence-engine work
        (out of this service's scope by design), not a persistence
        concern."""
        key = relationship_key_for(repository_id, relationship_type, source_entity, target_entity)
        record = UserCorrectionRecord(
            relationship_key=key,
            correction_source_kind=correction.source.kind,
            correction_source_identity=correction.source.identity,
            correction_source_trust_level=correction.source.trust_level,
            corrected_state=(
                correction.corrected_state.value if correction.corrected_state else None
            ),
            reason=correction.reason,
            correction_created_at=correction.created_at,
        )
        stored = await self._repository.add_correction(record)
        await self._db.commit()
        return stored

    async def get_corrections(
        self,
        repository_id: uuid.UUID,
        relationship_type: str,
        source_entity: str,
        target_entity: str,
    ) -> list[UserCorrectionRecord]:
        key = relationship_key_for(repository_id, relationship_type, source_entity, target_entity)
        return await self._repository.get_corrections(key)
