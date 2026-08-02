"""The `engineering_evidence_packs` table — ADR 0018 RFC-04.

Persists one compressed blob per `EngineeringEvidencePack` (ADR 0018's
knowledge_engine contract), never one row per `EvidenceItem` — ADR 0018
was explicit about this: normalizing thousands of evidence items into
relational rows is a write-throughput failure waiting to happen; the pack
itself is the unit of storage, its individual items are dereferenced by
decompressing it.

Append-only by convention (no update method exists on the repository that
writes this table — see `EngineeringMemoryRepository`): a new row is
always inserted, never an existing one modified, matching this codebase's
existing precedent (`app.models.evidence.Evidence`/`EvidenceRepository`,
Architecture v2.1 — an unrelated subsystem, but the same append-only
convention). Multiple rows can legitimately share
`(repository_id, commit_sha, schema_version)` — a re-index of an unchanged
commit, or a second generator producing its own pack for the same commit
(RFC-06+) — so that triple is indexed for lookup, never uniquely
constrained.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class EngineeringEvidencePackRecord(Base):
    __tablename__ = "engineering_evidence_packs"
    __table_args__ = (
        Index(
            "ix_engineering_evidence_packs_repo_commit_schema",
            "repository_id",
            "commit_sha",
            "schema_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # The knowledge_engine contract's own content-addressed pack id
    # (`EngineeringEvidencePack.id`) — kept alongside the DB's own surrogate
    # `id` so a pack can be looked up by the identity the pipeline already
    # knows it by, without needing the DB-generated UUID.
    pack_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    is_delta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    base_pack_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Denormalized for cheap metrics/listing without decompressing the blob.
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # gzip-compressed JSON serialization of the full `EngineeringEvidencePack`
    # (every `EvidenceItem`, in full) — see
    # `app.knowledge_engine.memory_service` for the compress/decompress and
    # serialize/deserialize logic. Never queried structurally; retrieved
    # whole and decompressed for replay.
    compressed_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    compression: Mapped[str] = mapped_column(String(16), nullable=False, default="gzip")
    uncompressed_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
