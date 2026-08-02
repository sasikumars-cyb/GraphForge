"""ADR 0018 — Confidence Explainability: `explain_confidence`,
`render_explanation_text`. Pure unit tests, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.confidence.default_engine import (
    HIGH_RELIABILITY_TIER,
    MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED,
)
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import ValidationResult
from app.knowledge_engine.explainability import explain_confidence, render_explanation_text


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:1",
        pack_version="v1",
        run_id="pack:1",
    )


def _result(source_type: str, verdict: str, tier: int = 1) -> ValidationResult:
    return ValidationResult(
        hypothesis_id="hyp:1",
        validator_name=f"{source_type}_validator",
        verdict=verdict,  # type: ignore[arg-type]
        evidence_used=("evidence:1",) if verdict != "no_signal" else (),
        source_type=source_type,
        evidence_reliability_tier=tier if verdict != "no_signal" else 0,
        explanation="test",
        provenance=_provenance(),
    )


def _model(
    state: ConfidenceState,
    confirming: frozenset[str] = frozenset(),
    tier: int = 0,
    contradictions: int = 0,
) -> ConfidenceModel:
    return ConfidenceModel(
        state=state,
        distinct_confirming_source_types=len(confirming),
        confirming_source_types=confirming,
        max_confirming_reliability_tier=tier,
        contradiction_count=contradictions,
        computed_at=datetime.now(UTC),
        formula_version="v1",
    )


def test_no_results_yields_candidate_explanation() -> None:
    explanation = explain_confidence(_model(ConfidenceState.CANDIDATE), [])

    assert explanation.confirming_domains == ()
    assert explanation.strongest_domain is None
    assert explanation.contradicting_domains == ()
    assert "No validator confirmed" in explanation.why_confidence_increased


def test_single_confirmation_identifies_domain_and_strongest() -> None:
    results = [_result("repository_manifest", "confirms", tier=1)]
    model = _model(ConfidenceState.LIKELY, frozenset({"repository_manifest"}), tier=1)

    explanation = explain_confidence(model, results)

    assert explanation.confirming_domains == ("repository_manifest",)
    assert explanation.strongest_domain == "repository_manifest"


def test_multiple_confirmations_pick_highest_tier_as_strongest_with_alphabetical_tiebreak() -> None:
    results = [
        _result("repository_manifest", "confirms", tier=1),
        _result("dependency_declaration", "confirms", tier=3),
        _result("code_annotation_literal", "confirms", tier=3),
    ]
    model = _model(
        ConfidenceState.VERIFIED,
        frozenset({"repository_manifest", "dependency_declaration", "code_annotation_literal"}),
        tier=3,
    )

    explanation = explain_confidence(model, results)

    # Two domains tie at tier 3 -- alphabetically first wins, deterministically.
    assert explanation.strongest_domain == "code_annotation_literal"
    assert explanation.confirming_domains == (
        "code_annotation_literal",
        "dependency_declaration",
        "repository_manifest",
    )


def test_contradiction_recorded_separately_from_confirmation() -> None:
    results = [
        _result("repository_manifest", "confirms", tier=1),
        _result("repository_configuration", "contradicts", tier=1),
    ]
    model = _model(
        ConfidenceState.CONFLICTING, frozenset({"repository_manifest"}), tier=1, contradictions=1
    )

    explanation = explain_confidence(model, results)

    assert explanation.contradicting_domains == ("repository_configuration",)
    assert "contradicting evidence exist" in explanation.why_confidence_limited


def test_why_limited_names_the_verified_threshold_for_highly_likely() -> None:
    results = [_result("code_annotation_literal", "confirms", tier=HIGH_RELIABILITY_TIER)]
    model = _model(
        ConfidenceState.HIGHLY_LIKELY,
        frozenset({"code_annotation_literal"}),
        tier=HIGH_RELIABILITY_TIER,
    )

    explanation = explain_confidence(model, results)

    assert str(HIGH_RELIABILITY_TIER) in explanation.why_confidence_limited
    assert str(MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED) in explanation.why_confidence_limited


def test_verified_state_has_no_recommendations() -> None:
    results = [
        _result("a_source", "confirms", tier=3),
        _result("b_source", "confirms", tier=3),
    ]
    model = _model(ConfidenceState.VERIFIED, frozenset({"a_source", "b_source"}), tier=3)

    explanation = explain_confidence(model, results)

    assert explanation.recommendations == ()


def test_no_signal_domains_become_recommendations() -> None:
    results = [
        _result("repository_manifest", "confirms", tier=1),
        _result("repository_documentation", "no_signal"),
        _result("repository_configuration", "no_signal"),
    ]
    model = _model(ConfidenceState.LIKELY, frozenset({"repository_manifest"}), tier=1)

    explanation = explain_confidence(model, results)

    assert len(explanation.recommendations) == 2
    assert any("Documentation" in r for r in explanation.recommendations)
    assert any("Configuration" in r for r in explanation.recommendations)


def test_deterministic_across_repeated_calls() -> None:
    results = [
        _result("repository_manifest", "confirms", tier=1),
        _result("repository_documentation", "confirms", tier=1),
        _result("repository_configuration", "no_signal"),
    ]
    model = _model(
        ConfidenceState.VERIFIED,
        frozenset({"repository_manifest", "repository_documentation"}),
        tier=1,
    )

    first = explain_confidence(model, results)
    second = explain_confidence(model, list(reversed(results)))

    assert first == second


def test_duplicate_source_type_across_two_results_does_not_double_count_domain() -> None:
    results = [
        _result("repository_manifest", "confirms", tier=1),
        _result("repository_manifest", "confirms", tier=1),
    ]
    model = _model(ConfidenceState.LIKELY, frozenset({"repository_manifest"}), tier=1)

    explanation = explain_confidence(model, results)

    assert explanation.confirming_domains == ("repository_manifest",)


def test_render_explanation_text_shows_checkmarks_for_confirmations() -> None:
    results = [
        _result("repository_manifest", "confirms", tier=1),
        _result("repository_documentation", "confirms", tier=1),
    ]
    model = _model(
        ConfidenceState.VERIFIED,
        frozenset({"repository_manifest", "repository_documentation"}),
        tier=1,
    )

    text = render_explanation_text(explain_confidence(model, results))

    assert text.startswith("Verified because:")
    assert "✓ Manifest confirms" in text
    assert "✓ Documentation confirms" in text


def test_render_explanation_text_shows_cross_marks_for_contradictions() -> None:
    results = [_result("repository_configuration", "contradicts", tier=1)]
    model = _model(ConfidenceState.REJECTED, frozenset(), tier=0, contradictions=1)

    text = render_explanation_text(explain_confidence(model, results))

    assert "✗ Configuration contradicts" in text


def test_unrecognized_source_type_still_renders_a_readable_label() -> None:
    results = [_result("some_future_domain", "confirms", tier=1)]
    model = _model(ConfidenceState.LIKELY, frozenset({"some_future_domain"}), tier=1)

    text = render_explanation_text(explain_confidence(model, results))

    assert "Some Future Domain" in text
