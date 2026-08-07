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

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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

    async def prune_evidence_packs(
        self, repository_id: uuid.UUID, *, keep_last_n: int
    ) -> int:
        """Delete evidence-pack rows for `repository_id` beyond the newest
        `keep_last_n` (by `sequence`, not `created_at` — see this table's
        own `sequence` column comment: `now()` is transaction-scoped, so
        `created_at` cannot tell two packs committed in the same
        transaction apart). This is the one KAN-24 retention action this
        repository ever performs — deliberately scoped to
        `engineering_evidence_packs` alone. ADR 0018's own Retention
        section already authorizes it: "`EvidencePack` blobs older than
        the last N successful runs per repository may be archived/
        compacted (they're regenerable by re-extraction against the same
        commit)." `Hypothesis`/`ValidationResult`/confidence-history rows
        (`knowledge_relationships`, `user_corrections` here) are the
        opposite case — the ADR states, in the same section, that they are
        "never compacted or deleted"; no method on this repository deletes
        from either table, and none should be added without a real
        retention-window decision overriding that guarantee (see KAN-24 /
        docs/adr/0019-engineering-memory-retention.md).

        Returns the number of rows deleted — 0 is a legitimate result
        (nothing past the retention count yet), not an error.
        """
        if keep_last_n < 0:
            raise ValueError("keep_last_n must be >= 0")
        stale_ids_stmt = (
            select(EngineeringEvidencePackRecord.id)
            .where(EngineeringEvidencePackRecord.repository_id == repository_id)
            .order_by(EngineeringEvidencePackRecord.sequence.desc())
            .offset(keep_last_n)
        )
        stale_ids = (await self._db.execute(stale_ids_stmt)).scalars().all()
        if not stale_ids:
            return 0
        await self._db.execute(
            delete(EngineeringEvidencePackRecord).where(
                EngineeringEvidencePackRecord.id.in_(stale_ids)
            )
        )
        return len(stale_ids)

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
        row per distinct `relationship_key`.

        Computed in SQL via a `row_number()` window (partitioned by
        `relationship_key`, ordered by `sequence` descending — same
        tiebreaker rationale as before), not by pulling every historical
        version into Python and deduping there. That Python-side approach
        — this method's shape until KAN-24 — read cost that scaled with
        total row count for the repository (every version of every
        relationship, ever), which grows unbounded by ADR 0018 design
        (Hypothesis/ValidationResult/confidence-history rows are never
        compacted or deleted — see `app.repositories.
        engineering_memory_repository`'s own module docstring and ADR
        0018's Consequences). This scales with the *current* relationship
        count instead, which does not grow with re-index frequency the
        same way — the actual "query performance degrades over time"
        fix KAN-24 asked for on this table's highest-traffic read path
        (rebuilding the Neo4j projection).

        Ordered by `sequence`, not `created_at` — see
        `app.models.knowledge_relationship`'s module docstring for the
        real bug (found via a real-Postgres integration test) this avoids:
        `created_at` is transaction-scoped and can be identical across
        rows written in the same commit."""
        row_number = (
            func.row_number()
            .over(
                partition_by=KnowledgeRelationshipRecord.relationship_key,
                order_by=KnowledgeRelationshipRecord.sequence.desc(),
            )
            .label("rn")
        )
        ranked = (
            select(KnowledgeRelationshipRecord, row_number)
            .where(KnowledgeRelationshipRecord.repository_id == repository_id)
            .subquery()
        )
        latest = aliased(KnowledgeRelationshipRecord, ranked)
        stmt = select(latest).where(ranked.c.rn == 1)
        return list((await self._db.execute(stmt)).scalars().all())

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
