"""Evidence — the language-agnostic facts every Hypothesis Generator reads
and every relationship must ultimately cite (ADR 0018, "graph relationships
are never created without evidence").

`EvidenceItem.kind` and `EvidenceReference.source_type` are deliberately
plain `str`, not a `StrEnum` — ADR 0018 requires these to be open,
registered vocabularies (declarative registries, same pattern as
`app.knowledge.registry.KnowledgeSourceSpec`), not a closed enum requiring
a schema change per new evidence source or framework. The registry itself
is out of scope for RFC-01 (no producer or consumer exists yet); this
module only fixes the *shape*, not the vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.knowledge_engine.contracts.provenance import Provenance


@dataclass(frozen=True)
class EvidenceReference:
    """Always dereferenceable back to raw source — what makes
    explainability checkable rather than aspirational: given a reference,
    a human (or a validator) can open `locator` and see the fact for
    themselves. `line`/`key`/`commit_sha` are optional because not every
    `source_type` has all three (a Confluence page has no `line`; a
    Kubernetes manifest's fact is keyed, not lined).
    """

    repository_id: str
    source_type: str
    locator: str
    line: int | None = None
    key: str | None = None
    commit_sha: str | None = None

    def __post_init__(self) -> None:
        if not self.repository_id.strip():
            raise ValueError("EvidenceReference.repository_id must not be empty")
        if not self.source_type.strip():
            raise ValueError("EvidenceReference.source_type must not be empty")
        if not self.locator.strip():
            raise ValueError("EvidenceReference.locator must not be empty")


@dataclass(frozen=True)
class EvidenceItem:
    """One atomic, language-agnostic fact — an import, a URL literal, a
    Kubernetes Service definition, a CODEOWNERS entry.

    `id` is expected to be content-addressed (a hash of `kind`, `reference`,
    and `raw_value`) by whatever extractor constructs it, so re-extracting
    the same fact on a later run — or a later delta ingestion, ADR 0018's
    incremental-evidence event flow — produces the same id rather than a
    duplicate. This contract does not compute the hash itself (no extractor
    exists yet in RFC-01); it only requires callers to supply a non-empty
    id, matching this codebase's existing precedent of namespaced,
    deterministic node ids (see `app.indexer.graph.builder`'s
    `f"{repository_id}:{kind}:{key}"` convention, ADR 0007).

    `reliability_tier` is a static property of `kind` (looked up from a
    fixed table elsewhere, not computed per-instance) — e.g. a Kubernetes
    Service manifest is more reliable evidence than a bare string literal.
    Carried on the instance rather than re-derived by every consumer so a
    validator never needs its own copy of the reliability table to reason
    about an item it's holding.
    """

    id: str
    kind: str
    source_type: str
    reliability_tier: int
    reference: EvidenceReference
    raw_value: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("EvidenceItem.id must not be empty")
        if not self.kind.strip():
            raise ValueError("EvidenceItem.kind must not be empty")
        if not self.source_type.strip():
            raise ValueError("EvidenceItem.source_type must not be empty")
        if self.reliability_tier < 0:
            raise ValueError("EvidenceItem.reliability_tier must not be negative")


@dataclass(frozen=True)
class EngineeringEvidencePack:
    """Immutable per version — one per `(repository_id, commit_sha,
    schema_version)` for a full run, or a delta pack (`is_delta=True`,
    `base_pack_id` set) for incremental evidence arriving between full
    index runs (ADR 0018's incremental-ingestion event flow, e.g. runtime
    telemetry or a newly-linked Confluence page).

    `schema_version` is bumped whenever `EvidenceItem.kind`'s vocabulary
    changes in a way that could break an existing consumer — the pack's own
    reproducibility guarantee ("every relationship must be reproducible")
    depends on this being tracked explicitly rather than inferred.
    """

    id: str
    repository_id: str
    commit_sha: str
    schema_version: str
    items: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    produced_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_delta: bool = False
    base_pack_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("EngineeringEvidencePack.id must not be empty")
        if not self.repository_id.strip():
            raise ValueError("EngineeringEvidencePack.repository_id must not be empty")
        if not self.commit_sha.strip():
            raise ValueError("EngineeringEvidencePack.commit_sha must not be empty")
        if not self.schema_version.strip():
            raise ValueError("EngineeringEvidencePack.schema_version must not be empty")
        if self.is_delta and not self.base_pack_id:
            raise ValueError("a delta EngineeringEvidencePack must set base_pack_id")
