"""Tests for app.knowledge_engine.confidence.default_engine — ADR 0018
RFC-03's confidence formula, in isolation from any validator or hypothesis.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.confidence.default_engine import DefaultConfidenceEngine
from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import ValidationResult


def _result(*, verdict: str, source_type: str, tier: int) -> ValidationResult:
    provenance = Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        pack_id="pack-1",
        pack_version="v1",
        run_id="pack-1",
    )
    return ValidationResult(
        hypothesis_id="hyp-1",
        validator_name="test_validator",
        verdict=verdict,  # type: ignore[arg-type]
        evidence_used=("evidence-1",) if verdict != "no_signal" else (),
        source_type=source_type,
        evidence_reliability_tier=tier,
        explanation="test",
        provenance=provenance,
    )


class TestDefaultConfidenceEngine:
    def test_no_confirmations_no_contradictions_is_candidate(self):
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(None, _result(verdict="no_signal", source_type="code", tier=0))
        assert model.state == ConfidenceState.CANDIDATE

    def test_single_high_reliability_confirmation_is_highly_likely(self):
        """The generalization of cross_repo_linker's 'structural' label."""
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        assert model.state == ConfidenceState.HIGHLY_LIKELY

    def test_single_low_reliability_confirmation_is_likely(self):
        """The generalization of cross_repo_linker's 'heuristic' label."""
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="dependency_coordinate_name", tier=1)
        )
        assert model.state == ConfidenceState.LIKELY

    def test_two_distinct_high_reliability_confirmations_is_verified(self):
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        model = engine.aggregate(
            model, _result(verdict="confirms", source_type="llm_agreement", tier=3)
        )
        assert model.state == ConfidenceState.VERIFIED

    def test_same_source_type_confirming_twice_does_not_reach_verified(self):
        """Two results from the *same* source_type are not two independent
        confirmations — distinctness, not raw count, drives VERIFIED."""
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        model = engine.aggregate(
            model, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        assert model.state == ConfidenceState.HIGHLY_LIKELY
        assert model.distinct_confirming_source_types == 1

    def test_contradiction_with_no_prior_confirmation_is_rejected(self):
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="contradicts", source_type="metadata", tier=2)
        )
        assert model.state == ConfidenceState.REJECTED

    def test_contradiction_after_confirmation_is_conflicting_not_rejected(self):
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        model = engine.aggregate(
            model, _result(verdict="contradicts", source_type="metadata", tier=2)
        )
        assert model.state == ConfidenceState.CONFLICTING

    def test_confirmation_after_contradiction_never_regresses_out_of_rejected(self):
        """Monotonicity (ADR 0018): once contradicted, a later confirms
        must not silently promote the state back up."""
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="contradicts", source_type="metadata", tier=2)
        )
        assert model.state == ConfidenceState.REJECTED
        model = engine.aggregate(
            model, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        assert model.state == ConfidenceState.CONFLICTING  # not HIGHLY_LIKELY, not VERIFIED

    def test_second_distinct_confirmation_strengthens_even_if_lower_reliability(self):
        """A second, independent confirming source type — even a weaker
        one — is genuine additional corroboration on top of an
        already-strong confirmation, so it strengthens rather than
        downgrades: HIGHLY_LIKELY -> VERIFIED, not the reverse."""
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        assert model.state == ConfidenceState.HIGHLY_LIKELY
        model = engine.aggregate(
            model, _result(verdict="confirms", source_type="dependency_coordinate_name", tier=1)
        )
        assert model.state == ConfidenceState.VERIFIED
        # The strongest evidence ever seen is remembered, not overwritten
        # by a weaker later result.
        assert model.max_confirming_reliability_tier == 3

    def test_high_reliability_confirmation_after_low_reliability_never_regresses_downward(self):
        """The reverse order: a strong confirmation arriving after a weak
        one must promote state, never leave it capped at LIKELY."""
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="dependency_coordinate_name", tier=1)
        )
        assert model.state == ConfidenceState.LIKELY
        model = engine.aggregate(
            model, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        assert model.state == ConfidenceState.VERIFIED

    def test_formula_version_is_stamped(self):
        engine = DefaultConfidenceEngine()
        model = engine.aggregate(
            None, _result(verdict="confirms", source_type="code_annotation_literal", tier=3)
        )
        assert model.formula_version == "v1"
