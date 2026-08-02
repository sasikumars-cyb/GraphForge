"""Provenance — answers "where did this come from" for every persisted
object in the Engineering Intelligence Platform (ADR 0018).

`GeneratorIdentity` and `Provenance` are the two types every other contract
in this package embeds, directly or via composition. Kept in their own
module (rather than folded into `hypothesis.py`) because `EvidenceItem`
(evidence.py) needs `Provenance` too, and evidence.py must not depend on
hypothesis.py — provenance is the shared leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

GeneratorKind = Literal["deterministic", "rule", "llm", "runtime", "docs", "infra"]


@dataclass(frozen=True)
class GeneratorIdentity:
    """Identifies exactly which producer created something, pinned to a
    specific version — never "latest". This is what makes reproducibility
    checkable: given the same evidence and the same `GeneratorIdentity`
    (same `kind`, `name`, and `version`), the same output is expected.

    `kind` is the coarse category ADR 0018's confidence formula counts
    distinct source types over; `name`/`version` are the specific
    implementation (e.g. `name="spring_boot_java_parser"`,
    `version="1.0.0"`, or `name="claude-sonnet-5"`,
    `version="2026-08-01"` for an LLM generator, where "version" pins the
    model/prompt combination that produced the output).
    """

    kind: GeneratorKind
    name: str
    version: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("GeneratorIdentity.name must not be empty")
        if not self.version.strip():
            raise ValueError("GeneratorIdentity.version must not be empty")


@dataclass(frozen=True)
class Provenance:
    """Attached to every `EvidenceItem`, `Hypothesis`, and `ValidationResult`
    — the mechanism that makes "every relationship must be explainable"
    (ADR 0018) a checkable property rather than an aspiration: given any
    persisted object, its `Provenance` names exactly which generator
    produced it, when, against which specific evidence pack, and which
    indexing run it belongs to.

    `pack_id` and `pack_version` are both required and answer different
    questions: `pack_id` locates the exact `EngineeringEvidencePack`
    instance this was computed from (what replay/debugging needs — "show
    me the evidence this came from"); `pack_version` records that pack's
    `schema_version` (what compatibility checking needs — "could this
    disagree with something computed under a different evidence-kind
    vocabulary"). Two packs for different repositories, or the same
    repository at different commits, can share a `pack_version` while
    being different packs — `pack_version` alone cannot locate the pack a
    given result actually came from.
    """

    generator: GeneratorIdentity
    produced_at: datetime
    pack_id: str
    pack_version: str
    run_id: str

    def __post_init__(self) -> None:
        if not self.pack_id.strip():
            raise ValueError("Provenance.pack_id must not be empty")
        if not self.pack_version.strip():
            raise ValueError("Provenance.pack_version must not be empty")
        if not self.run_id.strip():
            raise ValueError("Provenance.run_id must not be empty")
