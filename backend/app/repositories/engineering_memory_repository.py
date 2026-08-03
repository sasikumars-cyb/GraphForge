"""Data access for ADR 0018's Engineering Memory (RFC-04) — mirrors
`app.repositories.evidence_repository.EvidenceRepository`/
`session_repository.SessionRepository`'s conventions exactly: a plain
class over an injected `AsyncSession`, `add()` uses `db.add()`+`db.flush()`
(never `commit()` — the caller/service owns the transaction boundary),
retrieval uses plain SQLAlchemy `select()`, no raw SQL.

One repository class covering all three RFC-04 tables (evidence packs,
relationship versions, corrections) rather than three separate classes —
they're always used together by the same caller
(`app.knowledge_engine.memory_service.EngineeringMemoryService`), and
splitting them would just mean that service holding three repository
instances instead of one for no benefit.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engineering_evidence_pack import EngineeringEvidencePackRecord
from app.models.knowledge_relationship import KnowledgeRelationshipRecord
from app.models.user_correction import UserCorrectionRecord


class EngineeringMemoryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Evidence packs -----------------------------------------------

    async def add_evidence_pack(
        self, record: EngineeringEvidencePackRecord
    ) -> EngineeringEvidencePackRecord:
        self._db.add(record)
        await self._db.flush()
        return record

    async def get_evidence_pack_by_pack_id(
        self, pack_id: str
    ) -> EngineeringEvidencePackRecord | None:
        """Most recent row for this content-addressed pack id — there can
        be more than one (append-only: re-persisting the same pack is a
        new row, not an upsert), so "the" pack for a given id means its
        latest persisted copy."""
        stmt = (
            select(EngineeringEvidencePackRecord)
            .where(EngineeringEvidencePackRecord.pack_id == pack_id)
            .order_by(EngineeringEvidencePackRecord.created_at.desc())
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalars().first()

    async def list_evidence_packs(
        self,
        repository_id: uuid.UUID,
        *,
        commit_sha: str | None = None,
        exclude_commit_sha: str | None = None,
        schema_version: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EngineeringEvidencePackRecord]:
        stmt = select(EngineeringEvidencePackRecord).where(
            EngineeringEvidencePackRecord.repository_id == repository_id
        )
        if commit_sha is not None:
            stmt = stmt.where(EngineeringEvidencePackRecord.commit_sha == commit_sha)
        if exclude_commit_sha is not None:
            stmt = stmt.where(EngineeringEvidencePackRecord.commit_sha != exclude_commit_sha)
        if schema_version is not None:
            stmt = stmt.where(EngineeringEvidencePackRecord.schema_version == schema_version)
        stmt = (
            stmt.order_by(EngineeringEvidencePackRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    # -- Knowledge relationships (append-only, versioned) --------------

    async def add_relationship_version(
        self, record: KnowledgeRelationshipRecord
    ) -> KnowledgeRelationshipRecord:
        self._db.add(record)
        await self._db.flush()
        return record

    async def get_current_relationships(
        self, repository_id: uuid.UUID
    ) -> list[KnowledgeRelationshipRecord]:
        """The latest version of every relationship for a repository — one
        row per distinct `relationship_key`. Deduped in Python (keep the
        first row seen per key, given the query's own ordering) rather
        than a Postgres-specific `DISTINCT ON`, trading a small amount of
        efficiency at large scale for portability and readability; worth
        revisiting only if this becomes measurably slow, not pre-optimized
        now.

        Ordered by `sequence`, not `created_at` — see
        `app.models.knowledge_relationship`'s module docstring for the
        real bug (found via a real-Postgres integration test) this avoids:
        `created_at` is transaction-scoped and can be identical across
        rows written in the same commit."""
        stmt = (
            select(KnowledgeRelationshipRecord)
            .where(KnowledgeRelationshipRecord.repository_id == repository_id)
            .order_by(
                KnowledgeRelationshipRecord.relationship_key,
                KnowledgeRelationshipRecord.sequence.desc(),
            )
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        latest_by_key: dict[str, KnowledgeRelationshipRecord] = {}
        for row in rows:
            if row.relationship_key not in latest_by_key:
                latest_by_key[row.relationship_key] = row
        return list(latest_by_key.values())

    async def get_relationship_history(
        self, relationship_key: str
    ) -> list[KnowledgeRelationshipRecord]:
        """Every version of one relationship, oldest first — the full
        confidence/state trajectory over time."""
        stmt = (
            select(KnowledgeRelationshipRecord)
            .where(KnowledgeRelationshipRecord.relationship_key == relationship_key)
            .order_by(KnowledgeRelationshipRecord.sequence.asc())
        )
        return list((await self._db.execute(stmt)).scalars().all())

    # -- User corrections ------------------------------------------------

    async def add_correction(self, record: UserCorrectionRecord) -> UserCorrectionRecord:
        self._db.add(record)
        await self._db.flush()
        return record

    async def get_corrections(self, relationship_key: str) -> list[UserCorrectionRecord]:
        stmt = (
            select(UserCorrectionRecord)
            .where(UserCorrectionRecord.relationship_key == relationship_key)
            .order_by(UserCorrectionRecord.correction_created_at.asc())
        )
        return list((await self._db.execute(stmt)).scalars().all())
