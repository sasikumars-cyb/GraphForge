"""Validation — the deterministic, pluggable check every `Hypothesis` must
pass through before it can influence graph-facing confidence (ADR 0018:
"every relationship passes through exactly one validation pipeline").

A `KnowledgeValidator` is a pure function of `(Hypothesis,
EngineeringEvidencePack)` — no network I/O beyond re-checking evidence
already present in the pack, and never a call to an LLM (ADR 0018's
implementation principles: "a validator that itself asks an LLM 'does this
seem right' isn't validating, it's generating a second, uncoordinated
hypothesis").
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import Provenance

Verdict = Literal["confirms", "contradicts", "no_signal"]


@dataclass(frozen=True)
class ValidationResult:
    """One validator's opinion on one hypothesis. `evidence_used` cites
    which `EvidenceItem` id(s) grounded this verdict — required to be
    non-empty for `confirms`/`contradicts` (an opinion with a definite
    verdict must point at *something*); may be empty only for `no_signal`,
    where by definition nothing in the pack was relevant.

    `source_type` records which evidence class grounded this result (not
    which validator produced it) — this is what a later `ConfidenceEngine`
    counts distinct instances of, per ADR 0018's confidence formula
    ("distinct confirming source types", not raw confirmation count).

    `evidence_reliability_tier` — added in RFC-03, not part of RFC-01's
    original shape — is the reliability tier (see `EvidenceItem
    .reliability_tier`) of the evidence this verdict is grounded in (the
    validator's own summary, computed from the `evidence_used` items it
    already read from the pack — no extra I/O). This closes a real gap:
    "distinct confirming source types" alone cannot distinguish a literal
    annotation match (e.g. `@FeignClient(name=...)`, this codebase's
    highest-trust deterministic signal) from a same-shape but weaker match
    (e.g. a dependency coordinate happening to share a repository's name) —
    both would count as exactly one confirming source type and collapse to
    the same confidence state, which is wrong: `cross_repo_linker.py`
    already treats these as differently trustworthy today ("structural" vs
    "heuristic"), and a `ConfidenceEngine` needs this field to preserve
    that distinction rather than flattening it away. Required to be `> 0`
    whenever `evidence_used` is non-empty (i.e. for `confirms`/
    `contradicts`); `0` only for a `no_signal` verdict with no evidence to
    grade.
    """

    hypothesis_id: str
    validator_name: str
    verdict: Verdict
    evidence_used: tuple[str, ...]
    source_type: str
    evidence_reliability_tier: int
    explanation: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.hypothesis_id.strip():
            raise ValueError("ValidationResult.hypothesis_id must not be empty")
        if not self.validator_name.strip():
            raise ValueError("ValidationResult.validator_name must not be empty")
        if not self.source_type.strip():
            raise ValueError("ValidationResult.source_type must not be empty")
        if self.verdict != "no_signal" and not self.evidence_used:
            raise ValueError(
                f"ValidationResult with verdict={self.verdict!r} must cite at least one "
                "EvidenceItem id in evidence_used — only 'no_signal' may cite none"
            )
        if self.evidence_reliability_tier < 0:
            raise ValueError("ValidationResult.evidence_reliability_tier must not be negative")
        if self.evidence_used and self.evidence_reliability_tier == 0:
            raise ValueError(
                "ValidationResult.evidence_reliability_tier must be > 0 whenever "
                "evidence_used is non-empty — a verdict grounded in real evidence "
                "must carry that evidence's reliability, not the 'no evidence' default"
            )


class KnowledgeValidator(ABC):
    """Port every validator plugin implements. `applies_to` filters which
    `Hypothesis.relationship_type` values this validator has an opinion on
    — the mechanism that lets a deterministic-generator hypothesis clear a
    trivial, narrowly-scoped validator in O(1) rather than the full
    validator suite, without requiring a second pipeline (ADR 0018: "one
    mental model, one code path, cheap cases stay cheap by filtering which
    validators apply, not by forking the pipeline").
    """

    name: str
    applies_to: frozenset[str]

    @abstractmethod
    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        """Pure evaluation of one hypothesis against the evidence pack it
        was generated from (or a superset of it). Must not raise for
        "found nothing to confirm or contradict" — return a `no_signal`
        result instead; raising is reserved for genuine validator failure."""
        raise NotImplementedError
