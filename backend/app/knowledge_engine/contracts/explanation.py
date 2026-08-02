"""`ConfidenceExplanation` — a structured, human-readable account of *why*
a `ConfidenceModel` reached the state it did. Deliberately a separate,
sibling contract to `ConfidenceModel`, not a field added to it: the
explanation is a presentation-layer derivative computed FROM an already-
final `ConfidenceModel` plus the `ValidationResult`s that produced it,
never an input to confidence computation itself (ADR 0018: "confidence is
derived only from ValidationResults" — this contract is derived only from
`ConfidenceModel`/`ValidationResult`, never the reverse).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.knowledge_engine.contracts.confidence import ConfidenceState


@dataclass(frozen=True)
class ConfidenceExplanation:
    """Every field here is deterministically derived — same
    `(ConfidenceModel, ValidationResult​s)` input always produces the exact
    same explanation, byte for byte (see
    `app.knowledge_engine.explainability.explain_confidence`).

    `confirming_domains`/`contradicting_domains` are sorted tuples, not
    sets — iteration order of a `frozenset[str]` is not stable across
    Python processes (hash randomization), and this contract's whole
    purpose is being persisted and compared byte-for-byte later.
    """

    state: ConfidenceState
    confirming_domains: tuple[str, ...]
    strongest_domain: str | None
    contradicting_domains: tuple[str, ...]
    why_confidence_increased: str
    why_confidence_limited: str
    recommendations: tuple[str, ...]

    def __post_init__(self) -> None:
        if list(self.confirming_domains) != sorted(self.confirming_domains):
            raise ValueError("ConfidenceExplanation.confirming_domains must be sorted")
        if list(self.contradicting_domains) != sorted(self.contradicting_domains):
            raise ValueError("ConfidenceExplanation.contradicting_domains must be sorted")
        if (
            self.strongest_domain is not None
            and self.strongest_domain not in self.confirming_domains
        ):
            raise ValueError(
                "ConfidenceExplanation.strongest_domain must be one of confirming_domains"
            )
