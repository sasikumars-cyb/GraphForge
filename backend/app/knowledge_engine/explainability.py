"""ADR 0018 — Confidence Explainability: turns an already-computed
`ConfidenceModel` plus the `ValidationResult`s that produced it into a
structured, human-readable `ConfidenceExplanation`. Never computes
confidence itself, never re-derives `ConfidenceState` — `confidence.state`
(from `DefaultConfidenceEngine`, untouched) is read, not recomputed; this
module only explains a decision that already exists, the same "treat
`ConfidenceModel` as the single source of truth" discipline the codebase
now explicitly requires here.

Pure and deterministic by construction: every list this module produces
is built from a `sorted(...)` over real string values (`ValidationResult
.source_type`), never from `frozenset` iteration order (not stable across
Python processes) and never from wall-clock time or randomness. Same
`(confidence, validation_results)` in, same `ConfidenceExplanation` out,
every time — the property `tests/unit/knowledge_engine
/test_explainability.py` proves directly.

Domain labels (`repository_manifest` -> "Manifest", ...) are a small,
open-ended display mapping over `ValidationResult.source_type` — the
existing, already-open evidence-source vocabulary (`EvidenceItem.kind`'s
own module docstring: "open, registered vocabularies... not a closed
enum"). An unrecognized future source type still renders sensibly via the
fallback (`"my_new_domain"` -> "My New Domain"), so a brand-new validator
family never needs a change here to be explainable.
"""

from __future__ import annotations

from app.knowledge_engine.confidence.default_engine import (
    HIGH_RELIABILITY_TIER,
    MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED,
)
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.explanation import ConfidenceExplanation
from app.knowledge_engine.contracts.validation import ValidationResult

_DOMAIN_LABELS: dict[str, str] = {
    "repository_manifest": "Manifest",
    "repository_documentation": "Documentation",
    "repository_configuration": "Configuration",
    "dependency_declaration": "Dependency",
    "code_annotation_literal": "Code annotation",
    "dependency_coordinate_name": "Dependency coordinate",
}


def _domain_label(source_type: str) -> str:
    return _DOMAIN_LABELS.get(source_type, source_type.replace("_", " ").title())


def _strongest_domain(confirms: list[ValidationResult]) -> str | None:
    if not confirms:
        return None
    top_tier = max(result.evidence_reliability_tier for result in confirms)
    top_domains = sorted(
        {result.source_type for result in confirms if result.evidence_reliability_tier == top_tier}
    )
    return top_domains[0]


def _why_confidence_increased(confidence: ConfidenceModel, confirms: list[ValidationResult]) -> str:
    if not confirms:
        return "No validator confirmed this relationship yet."
    domains = ", ".join(_domain_label(d) for d in sorted({r.source_type for r in confirms}))
    return (
        f"Confirmed by {confidence.distinct_confirming_source_types} independent evidence "
        f"domain(s) ({domains}); strongest reliability tier observed was "
        f"{confidence.max_confirming_reliability_tier}."
    )


def _why_confidence_limited(confidence: ConfidenceModel) -> str:
    state = confidence.state
    if state == ConfidenceState.VERIFIED:
        return "Already at the highest attainable state."
    if state == ConfidenceState.REJECTED:
        return "A validator contradicted this relationship before any confirmation was recorded."
    if state == ConfidenceState.CONFLICTING:
        return (
            "Both confirming and contradicting evidence exist — a contradiction blocks "
            "promotion regardless of how many domains confirm."
        )
    if state == ConfidenceState.HIGHLY_LIKELY:
        return (
            f"Reached the high-reliability tier ({HIGH_RELIABILITY_TIER}) but only "
            f"{confidence.distinct_confirming_source_types} distinct domain(s) confirmed — "
            f"VERIFIED requires at least {MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED}."
        )
    if state == ConfidenceState.LIKELY:
        return (
            f"No confirming domain reached the high-reliability tier "
            f"({HIGH_RELIABILITY_TIER}); the highest seen was tier "
            f"{confidence.max_confirming_reliability_tier}."
        )
    return "No validator has confirmed or contradicted this relationship yet."


def _recommendations(
    confidence: ConfidenceModel, no_signal: list[ValidationResult]
) -> tuple[str, ...]:
    if confidence.state == ConfidenceState.VERIFIED:
        return ()
    if confidence.state in (ConfidenceState.REJECTED, ConfidenceState.CONFLICTING):
        return ("Resolve the contradicting evidence before seeking additional confirmations.",)

    checked_domains = sorted({r.source_type for r in no_signal})
    if not checked_domains:
        return (
            "No additional evidence domains were checked against this hypothesis — "
            "broadening evidence collection may surface a confirming signal.",
        )
    return tuple(
        f"{_domain_label(domain)} evidence was checked but did not confirm this relationship "
        "— verify whether it should exist."
        for domain in checked_domains
    )


def explain_confidence(
    confidence: ConfidenceModel, validation_results: list[ValidationResult]
) -> ConfidenceExplanation:
    """The only entry point. `validation_results` should be every result
    that was folded into `confidence` (confirms, contradicts, and
    no_signal alike) — the no_signal ones are what let `recommendations`
    name domains that were actually checked, not guessed at."""
    confirms = [r for r in validation_results if r.verdict == "confirms"]
    contradicts = [r for r in validation_results if r.verdict == "contradicts"]
    no_signal = [r for r in validation_results if r.verdict == "no_signal"]

    return ConfidenceExplanation(
        state=confidence.state,
        confirming_domains=tuple(sorted({r.source_type for r in confirms})),
        strongest_domain=_strongest_domain(confirms),
        contradicting_domains=tuple(sorted({r.source_type for r in contradicts})),
        why_confidence_increased=_why_confidence_increased(confidence, confirms),
        why_confidence_limited=_why_confidence_limited(confidence),
        recommendations=_recommendations(confidence, no_signal),
    )


def render_explanation_text(explanation: ConfidenceExplanation) -> str:
    """The "Verified because: ✓ Manifest confirms ..." display form."""
    state_label = explanation.state.value.replace("_", " ").title()
    lines = [f"{state_label} because:"]
    if not explanation.confirming_domains and not explanation.contradicting_domains:
        lines.append(f"(no evidence yet — {explanation.why_confidence_increased.lower()})")
        return "\n".join(lines)
    for domain in explanation.confirming_domains:
        lines.append(f"✓ {_domain_label(domain)} confirms")
    for domain in explanation.contradicting_domains:
        lines.append(f"✗ {_domain_label(domain)} contradicts")
    return "\n".join(lines)
