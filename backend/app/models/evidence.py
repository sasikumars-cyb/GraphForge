"""The `evidence` table — Architecture v2.1 §2.2 `Evidence`.

"Any observed fact, from any source... one evidentiary model regardless of
source." Modeled as a single joined-table subclass with an
`evidence_kind` discriminator for the four named subtypes (Retrieved
Evidence, Code Change, Verification Result, Production Learning) rather
than four further joined tables — those four differ only in `source` and
the shape of `payload`, not in identity/traceability rules, so a second
inheritance level would be exactly the kind of complexity the frozen
architecture's guiding principles warn against introducing without a real
problem to solve.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import JSON, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.engineering_artifact import EngineeringArtifact

EvidenceKind = Literal["retrieved", "code_change", "verification_result", "production_learning"]


class Evidence(EngineeringArtifact):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("engineering_artifacts.id", ondelete="CASCADE"), primary_key=True
    )

    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Free text naming what produced this Evidence — a tool, a provider, a
    # human, or "runtime" for Production Learning. Never a foreign key:
    # Architecture v2.1 doesn't require the source itself to be a
    # queryable domain object, only that it be named (§2.2, Evidence's
    # own Traceability rule in §6).
    source: Mapped[str] = mapped_column(String(255), nullable=False)

    # Subtype-specific content (a diff's file list, a test run's pass/fail
    # counts, a retrieved artifact's title) — kept as JSON rather than a
    # column per possible shape, since RFC-001 is Session-scoped and this
    # data is never queried structurally within this RFC, only displayed.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __mapper_args__ = {"polymorphic_identity": "evidence"}
