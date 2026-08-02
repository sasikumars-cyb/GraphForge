"""Hypothesis — a proposed piece of engineering knowledge, from any
generator (deterministic parser, rule, LLM, future runtime/docs/infra
discovery). Per ADR 0018: "Deterministic parsers and LLMs are simply
different Hypothesis Generators" — this is the one shape all of them
produce, regardless of `GeneratorIdentity.kind`.

A `Hypothesis` never writes to the graph itself (ADR 0018: "nothing writes
directly into the graph") — it is only ever a candidate, consumed by a
`KnowledgeValidator` (validation.py, a later RFC).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance


@dataclass(frozen=True)
class Hypothesis:
    """`relationship_type` is deliberately plain `str`, not a `StrEnum` —
    same open-vocabulary reasoning as `EvidenceItem.kind`
    (see evidence.py's module docstring): dozens of frameworks will invent
    relationship shapes (`OWNS_FEATURE_FLAG`, `READS_SECRET_FROM_VAULT`)
    nobody can enumerate up front.

    `id` is expected to be content-addressed by the generator that creates
    it — a hash of `(generator identity, relationship_type, source_entity,
    target_entity, evidence_refs)` — so the same generator re-run against
    the same evidence pack produces the same id (idempotent), while two
    different generators independently proposing the same fact naturally
    produce two distinct, co-existing hypotheses rather than silently
    merging (agreement between them is a validator-level signal, not
    something collapsed away at this layer).

    `evidence_refs` must be non-empty: ADR 0018's first engineering
    invariant is "graph relationships are never created without evidence",
    and a hypothesis is the earliest point that invariant can be enforced.
    Whether each reference actually *resolves* within a given
    `EngineeringEvidencePack` is a validator-time concern (needs the pack
    to check against) — this contract only enforces the cheaper,
    always-checkable half: the list itself must not be empty.

    `generator_confidence` is explicitly advisory. ADR 0018: "a hypothesis's
    own `generator_confidence` is advisory and never authoritative — the
    `ConfidenceEngine` computes the graph-facing confidence, always, from
    validator results." No code should ever read this field to decide
    whether a hypothesis is trustworthy.
    """

    id: str
    relationship_type: str
    source_entity: str
    target_entity: str
    evidence_refs: tuple[str, ...]
    explanation: str
    provenance: Provenance
    generator_confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Hypothesis.id must not be empty")
        if not self.relationship_type.strip():
            raise ValueError("Hypothesis.relationship_type must not be empty")
        if not self.source_entity.strip():
            raise ValueError("Hypothesis.source_entity must not be empty")
        if not self.target_entity.strip():
            raise ValueError("Hypothesis.target_entity must not be empty")
        if not self.evidence_refs:
            raise ValueError(
                "Hypothesis.evidence_refs must not be empty — "
                "a hypothesis with no cited evidence violates ADR 0018's "
                "'graph relationships are never created without evidence' invariant"
            )
        if self.generator_confidence is not None and not (0.0 <= self.generator_confidence <= 1.0):
            raise ValueError("Hypothesis.generator_confidence must be within [0.0, 1.0]")


class HypothesisGenerator(ABC):
    """Port every hypothesis producer implements — a deterministic parser
    adapter, a rule-based extractor adapter, an LLM-backed generator, or a
    future runtime/docs/infra discovery plugin, all behind this one
    interface (ADR 0018).

    `consumes` declares which `EvidenceItem.source_type` values this
    generator reads, so an incremental delta pack (evidence arriving
    between full index runs) only wakes the generators that actually care
    about the new evidence's source type, per ADR 0018's incremental-
    ingestion event flow.

    Implementations must never write to a graph repository — `generate`'s
    only output is a list of `Hypothesis` objects. Implementations must
    also never let their own failure (a timeout, a crash) propagate in a
    way that blocks another generator's output for the same run — that
    isolation is an orchestration-layer concern (a later RFC), not
    something this interface itself can enforce, but every implementation
    must be written assuming its caller will invoke it independently and
    swallow its failure the same way `app.indexer.services.indexing_service
    .run_indexing` already isolates `relink_account` failures.
    """

    identity: GeneratorIdentity
    consumes: frozenset[str]

    @abstractmethod
    async def generate(self, pack: EngineeringEvidencePack) -> list[Hypothesis]:
        """Given an evidence pack (full or delta), return every hypothesis
        this generator can support from it. Must not raise for "found
        nothing" — an empty list is the correct result; raising is reserved
        for genuine generator failure (unreachable LLM provider, malformed
        pack) that the caller is expected to log and isolate."""
        raise NotImplementedError
