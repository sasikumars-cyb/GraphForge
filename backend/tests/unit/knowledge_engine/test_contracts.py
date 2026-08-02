"""Tests for app.knowledge_engine.contracts — ADR 0018 RFC-01.

RFC-01 introduces the core contracts only, with no consumers yet (ADR 0018:
"No other package imports them yet"). These tests verify exactly what RFC-01
promises: the dataclasses are immutable, their invariants are enforced at
construction, and the ABC ports (`HypothesisGenerator`, `KnowledgeValidator`,
`ConfidenceEngine`) can be implemented and invoked. Nothing here touches a
database, Neo4j, or any existing GraphForge component — RFC-01 is a leaf
package by design.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from app.knowledge_engine.contracts.confidence import (
    ConfidenceEngine,
    ConfidenceModel,
    ConfidenceState,
)
from app.knowledge_engine.contracts.correction import CorrectionSource, UserCorrection
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.hypothesis import Hypothesis, HypothesisGenerator
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import (
    KnowledgeValidator,
    ValidationResult,
)


def _provenance(**overrides: object) -> Provenance:
    defaults: dict[str, object] = dict(
        generator=GeneratorIdentity(kind="deterministic", name="test_generator", version="1.0.0"),
        produced_at=datetime(2026, 8, 1, 12, 0, 0),
        pack_id="pack-1",
        pack_version="v1",
        run_id="run-123",
    )
    defaults.update(overrides)
    return Provenance(**defaults)  # type: ignore[arg-type]


def _evidence_reference(**overrides: object) -> EvidenceReference:
    defaults: dict[str, object] = dict(
        repository_id="repo-1",
        source_type="code",
        locator="src/app.py",
        line=10,
    )
    defaults.update(overrides)
    return EvidenceReference(**defaults)  # type: ignore[arg-type]


def _evidence_item(**overrides: object) -> EvidenceItem:
    defaults: dict[str, object] = dict(
        id="evidence-1",
        kind="url_literal",
        source_type="code",
        reliability_tier=1,
        reference=_evidence_reference(),
        raw_value="https://orders-service/api",
        provenance=_provenance(),
    )
    defaults.update(overrides)
    return EvidenceItem(**defaults)  # type: ignore[arg-type]


def _hypothesis(**overrides: object) -> Hypothesis:
    defaults: dict[str, object] = dict(
        id="hypothesis-1",
        relationship_type="CALLS_SERVICE",
        source_entity="repo-1:orders-service",
        target_entity="repo-2:payments-service",
        evidence_refs=("evidence-1",),
        explanation="A URL literal points at the payments service.",
        provenance=_provenance(),
    )
    defaults.update(overrides)
    return Hypothesis(**defaults)  # type: ignore[arg-type]


class TestGeneratorIdentity:
    def test_valid_construction(self):
        identity = GeneratorIdentity(kind="llm", name="claude-sonnet-5", version="2026-08-01")
        assert identity.kind == "llm"

    def test_frozen(self):
        identity = GeneratorIdentity(kind="deterministic", name="parser", version="1.0.0")
        with pytest.raises(FrozenInstanceError):
            identity.name = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("field_name", ["name", "version"])
    def test_rejects_empty_fields(self, field_name):
        kwargs = dict(kind="deterministic", name="parser", version="1.0.0")
        kwargs[field_name] = "   "
        with pytest.raises(ValueError):
            GeneratorIdentity(**kwargs)  # type: ignore[arg-type]


class TestProvenance:
    def test_valid_construction(self):
        provenance = _provenance()
        assert provenance.run_id == "run-123"

    def test_frozen(self):
        provenance = _provenance()
        with pytest.raises(FrozenInstanceError):
            provenance.run_id = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("field_name", ["pack_id", "pack_version", "run_id"])
    def test_rejects_empty_fields(self, field_name):
        with pytest.raises(ValueError):
            _provenance(**{field_name: ""})

    def test_pack_id_and_pack_version_are_independent(self):
        """Two provenance records can share a pack_version (same evidence-
        kind vocabulary) while pointing at different pack instances — the
        gap this field closes: pack_version alone can't locate the pack a
        result actually came from."""
        first = _provenance(pack_id="pack-1", pack_version="v1")
        second = _provenance(pack_id="pack-2", pack_version="v1")
        assert first.pack_version == second.pack_version
        assert first.pack_id != second.pack_id


class TestEvidenceReference:
    def test_valid_construction(self):
        ref = _evidence_reference()
        assert ref.locator == "src/app.py"

    @pytest.mark.parametrize("field_name", ["repository_id", "source_type", "locator"])
    def test_rejects_empty_required_fields(self, field_name):
        with pytest.raises(ValueError):
            _evidence_reference(**{field_name: ""})

    def test_optional_fields_default_to_none(self):
        ref = EvidenceReference(repository_id="repo-1", source_type="docs", locator="README.md")
        assert ref.line is None
        assert ref.key is None
        assert ref.commit_sha is None


class TestEvidenceItem:
    def test_valid_construction(self):
        item = _evidence_item()
        assert item.kind == "url_literal"

    def test_frozen(self):
        item = _evidence_item()
        with pytest.raises(FrozenInstanceError):
            item.raw_value = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("field_name", ["id", "kind", "source_type"])
    def test_rejects_empty_fields(self, field_name):
        with pytest.raises(ValueError):
            _evidence_item(**{field_name: ""})

    def test_rejects_negative_reliability_tier(self):
        with pytest.raises(ValueError):
            _evidence_item(reliability_tier=-1)


class TestEngineeringEvidencePack:
    def test_valid_full_pack(self):
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            items=(_evidence_item(),),
        )
        assert pack.is_delta is False
        assert pack.base_pack_id is None
        assert len(pack.items) == 1

    def test_valid_delta_pack_requires_base_pack_id(self):
        pack = EngineeringEvidencePack(
            id="pack-2",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            is_delta=True,
            base_pack_id="pack-1",
        )
        assert pack.base_pack_id == "pack-1"

    def test_delta_pack_without_base_pack_id_is_rejected(self):
        with pytest.raises(ValueError):
            EngineeringEvidencePack(
                id="pack-2",
                repository_id="repo-1",
                commit_sha="abc123",
                schema_version="v1",
                is_delta=True,
            )

    @pytest.mark.parametrize("field_name", ["id", "repository_id", "commit_sha", "schema_version"])
    def test_rejects_empty_required_fields(self, field_name):
        kwargs = dict(id="pack-1", repository_id="repo-1", commit_sha="abc123", schema_version="v1")
        kwargs[field_name] = ""
        with pytest.raises(ValueError):
            EngineeringEvidencePack(**kwargs)  # type: ignore[arg-type]

    def test_empty_pack_is_valid(self):
        """An evidence pack with zero items is legitimate — a repository
        with nothing extractable yet (e.g. an empty repo) is a real state,
        not an error."""
        pack = EngineeringEvidencePack(
            id="pack-empty", repository_id="repo-1", commit_sha="abc123", schema_version="v1"
        )
        assert pack.items == ()


class TestHypothesis:
    def test_valid_construction(self):
        hypothesis = _hypothesis()
        assert hypothesis.relationship_type == "CALLS_SERVICE"

    def test_frozen(self):
        hypothesis = _hypothesis()
        with pytest.raises(FrozenInstanceError):
            hypothesis.explanation = "other"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field_name", ["id", "relationship_type", "source_entity", "target_entity"]
    )
    def test_rejects_empty_string_fields(self, field_name):
        with pytest.raises(ValueError):
            _hypothesis(**{field_name: ""})

    def test_rejects_empty_evidence_refs(self):
        """The load-bearing invariant from ADR 0018: 'graph relationships
        are never created without evidence' — enforced at the earliest
        possible point, hypothesis construction itself."""
        with pytest.raises(ValueError, match="evidence_refs"):
            _hypothesis(evidence_refs=())

    def test_generator_confidence_defaults_to_none(self):
        hypothesis = _hypothesis()
        assert hypothesis.generator_confidence is None

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_rejects_out_of_range_generator_confidence(self, confidence):
        with pytest.raises(ValueError):
            _hypothesis(generator_confidence=confidence)

    @pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
    def test_accepts_in_range_generator_confidence(self, confidence):
        hypothesis = _hypothesis(generator_confidence=confidence)
        assert hypothesis.generator_confidence == confidence


class TestHypothesisGenerator:
    """A concrete implementation must exist and be invocable — ADR 0018's
    implementation principle: 'every new interface should have at least
    one real implementation immediately.' RFC-01 has no production
    generator yet, so this minimal in-test double is what proves the port
    is actually implementable, not just declared."""

    @pytest.mark.asyncio
    async def test_concrete_implementation_is_invocable(self):
        class _StubGenerator(HypothesisGenerator):
            identity = GeneratorIdentity(kind="deterministic", name="stub", version="1.0.0")
            consumes = frozenset({"code"})

            async def generate(self, pack: EngineeringEvidencePack) -> list[Hypothesis]:
                if not pack.items:
                    return []
                return [_hypothesis()]

        generator = _StubGenerator()
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            items=(_evidence_item(),),
        )
        hypotheses = await generator.generate(pack)
        assert len(hypotheses) == 1

    def test_cannot_instantiate_abstract_class_directly(self):
        with pytest.raises(TypeError):
            HypothesisGenerator()  # type: ignore[abstract]


class TestValidationResult:
    def _result(self, **overrides: object) -> ValidationResult:
        defaults: dict[str, object] = dict(
            hypothesis_id="hypothesis-1",
            validator_name="feign_target_exists",
            verdict="confirms",
            evidence_used=("evidence-1",),
            source_type="code",
            evidence_reliability_tier=3,
            explanation="Target name matches an indexed repository.",
            provenance=_provenance(),
        )
        defaults.update(overrides)
        return ValidationResult(**defaults)  # type: ignore[arg-type]

    def test_valid_construction(self):
        result = self._result()
        assert result.verdict == "confirms"

    def test_confirms_requires_evidence_used(self):
        with pytest.raises(ValueError):
            self._result(verdict="confirms", evidence_used=())

    def test_contradicts_requires_evidence_used(self):
        with pytest.raises(ValueError):
            self._result(verdict="contradicts", evidence_used=())

    def test_no_signal_allows_empty_evidence_used(self):
        result = self._result(verdict="no_signal", evidence_used=(), evidence_reliability_tier=0)
        assert result.evidence_used == ()

    @pytest.mark.parametrize("field_name", ["hypothesis_id", "validator_name", "source_type"])
    def test_rejects_empty_fields(self, field_name):
        with pytest.raises(ValueError):
            self._result(**{field_name: ""})

    def test_rejects_negative_reliability_tier(self):
        with pytest.raises(ValueError):
            self._result(evidence_reliability_tier=-1)

    def test_confirms_with_zero_reliability_tier_is_rejected(self):
        """RFC-03's gap fix: a verdict grounded in real evidence
        (evidence_used non-empty) must carry that evidence's reliability —
        zero would be indistinguishable from 'no evidence to grade'."""
        with pytest.raises(ValueError, match="evidence_reliability_tier"):
            self._result(
                verdict="confirms", evidence_used=("evidence-1",), evidence_reliability_tier=0
            )

    def test_no_signal_with_zero_reliability_tier_is_valid(self):
        result = self._result(verdict="no_signal", evidence_used=(), evidence_reliability_tier=0)
        assert result.evidence_reliability_tier == 0


class TestKnowledgeValidator:
    @pytest.mark.asyncio
    async def test_concrete_implementation_is_invocable(self):
        class _StubValidator(KnowledgeValidator):
            name = "stub_validator"
            applies_to = frozenset({"CALLS_SERVICE"})

            async def validate(
                self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
            ) -> ValidationResult:
                return ValidationResult(
                    hypothesis_id=hypothesis.id,
                    validator_name=self.name,
                    verdict="no_signal",
                    evidence_used=(),
                    source_type="code",
                    evidence_reliability_tier=0,
                    explanation="stub",
                    provenance=_provenance(),
                )

        validator = _StubValidator()
        result = await validator.validate(
            _hypothesis(),
            EngineeringEvidencePack(
                id="pack-1", repository_id="repo-1", commit_sha="abc123", schema_version="v1"
            ),
        )
        assert result.verdict == "no_signal"

    def test_cannot_instantiate_abstract_class_directly(self):
        with pytest.raises(TypeError):
            KnowledgeValidator()  # type: ignore[abstract]


class TestConfidenceModel:
    def _model(self, **overrides: object) -> ConfidenceModel:
        defaults: dict[str, object] = dict(
            state=ConfidenceState.CANDIDATE,
            distinct_confirming_source_types=0,
            confirming_source_types=frozenset(),
            max_confirming_reliability_tier=0,
            contradiction_count=0,
            computed_at=datetime(2026, 8, 1, 12, 0, 0),
            formula_version="v1",
        )
        defaults.update(overrides)
        return ConfidenceModel(**defaults)  # type: ignore[arg-type]

    def test_valid_construction(self):
        model = self._model()
        assert model.state == ConfidenceState.CANDIDATE

    def test_rejects_empty_formula_version(self):
        with pytest.raises(ValueError):
            self._model(formula_version="")

    @pytest.mark.parametrize(
        "field_name",
        [
            "distinct_confirming_source_types",
            "contradiction_count",
            "max_confirming_reliability_tier",
        ],
    )
    def test_rejects_negative_counters(self, field_name):
        with pytest.raises(ValueError):
            self._model(**{field_name: -1})

    def test_rejects_nonzero_tier_with_no_confirmations(self):
        with pytest.raises(ValueError, match="max_confirming_reliability_tier"):
            self._model(
                distinct_confirming_source_types=0,
                confirming_source_types=frozenset(),
                max_confirming_reliability_tier=3,
            )

    def test_all_states_are_constructible(self):
        for state in ConfidenceState:
            model = self._model(state=state)
            assert model.state == state

    def test_rejects_count_and_set_drift(self):
        """The gap this field closes: the count and the set it summarizes
        must never disagree."""
        with pytest.raises(ValueError, match="confirming_source_types"):
            self._model(
                distinct_confirming_source_types=2, confirming_source_types=frozenset({"code"})
            )

    def test_accepts_matching_count_and_set(self):
        model = self._model(
            distinct_confirming_source_types=2,
            confirming_source_types=frozenset({"code", "metadata"}),
        )
        assert model.distinct_confirming_source_types == len(model.confirming_source_types)


class TestConfidenceEngine:
    def test_concrete_implementation_is_invocable(self):
        class _StubEngine(ConfidenceEngine):
            def aggregate(
                self, prior: ConfidenceModel | None, new_result: ValidationResult
            ) -> ConfidenceModel:
                if new_result.verdict == "contradicts":
                    return ConfidenceModel(
                        state=ConfidenceState.REJECTED,
                        distinct_confirming_source_types=0,
                        confirming_source_types=frozenset(),
                        max_confirming_reliability_tier=0,
                        contradiction_count=(prior.contradiction_count if prior else 0) + 1,
                        computed_at=datetime(2026, 8, 1, 12, 0, 0),
                        formula_version="v1",
                    )
                return ConfidenceModel(
                    state=ConfidenceState.LIKELY,
                    distinct_confirming_source_types=1,
                    confirming_source_types=frozenset({new_result.source_type}),
                    max_confirming_reliability_tier=new_result.evidence_reliability_tier,
                    contradiction_count=0,
                    computed_at=datetime(2026, 8, 1, 12, 0, 0),
                    formula_version="v1",
                )

        engine = _StubEngine()
        result = ValidationResult(
            hypothesis_id="hypothesis-1",
            validator_name="stub_validator",
            verdict="confirms",
            evidence_used=("evidence-1",),
            source_type="code",
            evidence_reliability_tier=3,
            explanation="stub",
            provenance=_provenance(),
        )
        model = engine.aggregate(None, result)
        assert model.state == ConfidenceState.LIKELY

    def test_cannot_instantiate_abstract_class_directly(self):
        with pytest.raises(TypeError):
            ConfidenceEngine()  # type: ignore[abstract]


class TestKnowledgeRelationship:
    def _relationship(self, **overrides: object) -> KnowledgeRelationship:
        defaults: dict[str, object] = dict(
            id="relationship-1",
            relationship_type="CALLS_SERVICE",
            source_entity="repo-1:orders-service",
            target_entity="repo-2:payments-service",
            confidence=ConfidenceModel(
                state=ConfidenceState.VERIFIED,
                distinct_confirming_source_types=2,
                confirming_source_types=frozenset({"code", "metadata"}),
                max_confirming_reliability_tier=3,
                contradiction_count=0,
                computed_at=datetime(2026, 8, 1, 12, 0, 0),
                formula_version="v1",
            ),
            hypothesis_ids=("hypothesis-1",),
            provenance=(_provenance(),),
        )
        defaults.update(overrides)
        return KnowledgeRelationship(**defaults)  # type: ignore[arg-type]

    def test_valid_construction(self):
        relationship = self._relationship()
        assert relationship.confidence.state == ConfidenceState.VERIFIED

    def test_rejects_empty_hypothesis_ids(self):
        with pytest.raises(ValueError, match="hypothesis_ids"):
            self._relationship(hypothesis_ids=())

    def test_rejects_empty_provenance(self):
        with pytest.raises(ValueError, match="provenance"):
            self._relationship(provenance=())

    def test_multiple_hypotheses_and_provenance_are_supported(self):
        """A relationship can be multiply attested — e.g. a deterministic
        parser and an LLM independently converging on the same fact."""
        relationship = self._relationship(
            hypothesis_ids=("hypothesis-1", "hypothesis-2"),
            provenance=(_provenance(), _provenance(run_id="run-456")),
        )
        assert len(relationship.hypothesis_ids) == 2
        assert len(relationship.provenance) == 2


class TestCorrectionSource:
    def test_valid_human_source(self):
        source = CorrectionSource(kind="human", identity="user-1", trust_level=1.0)
        assert source.trust_level == 1.0

    def test_valid_agent_source(self):
        source = CorrectionSource(kind="agent", identity="agent-reviewer", trust_level=0.4)
        assert source.trust_level == 0.4

    def test_rejects_empty_identity(self):
        with pytest.raises(ValueError):
            CorrectionSource(kind="human", identity="", trust_level=1.0)

    @pytest.mark.parametrize("trust_level", [-0.1, 1.1])
    def test_rejects_out_of_range_trust_level(self, trust_level):
        with pytest.raises(ValueError):
            CorrectionSource(kind="human", identity="user-1", trust_level=trust_level)


class TestUserCorrection:
    def _correction(self, **overrides: object) -> UserCorrection:
        defaults: dict[str, object] = dict(
            id="correction-1",
            relationship_id="relationship-1",
            source=CorrectionSource(kind="human", identity="user-1", trust_level=1.0),
            corrected_state=ConfidenceState.REJECTED,
            reason="This service was decommissioned last quarter.",
            created_at=datetime(2026, 8, 1, 12, 0, 0),
        )
        defaults.update(overrides)
        return UserCorrection(**defaults)  # type: ignore[arg-type]

    def test_valid_construction(self):
        correction = self._correction()
        assert correction.corrected_state == ConfidenceState.REJECTED

    def test_corrected_state_none_means_outright_rejection(self):
        correction = self._correction(corrected_state=None)
        assert correction.corrected_state is None

    @pytest.mark.parametrize("field_name", ["id", "relationship_id", "reason"])
    def test_rejects_empty_fields(self, field_name):
        with pytest.raises(ValueError):
            self._correction(**{field_name: ""})

    def test_agent_correction_does_not_default_to_full_trust(self):
        """ADR 0018: an agent-sourced correction is never an unconditional
        override — this contract doesn't grant it trust_level=1.0 by
        default the way a human source conventionally would be configured
        by its caller; the caller must decide the agent's trust level
        explicitly."""
        agent_source = CorrectionSource(kind="agent", identity="agent-reviewer", trust_level=0.3)
        correction = self._correction(source=agent_source)
        assert correction.source.trust_level < 1.0
