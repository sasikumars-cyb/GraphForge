"""End-to-end composition test for ADR 0018's core lifecycle:

    EvidencePack -> HypothesisGenerator -> Hypothesis
                 -> KnowledgeValidator -> ValidationResult
                 -> ConfidenceEngine -> KnowledgeRelationship

This is deliberately the only test in RFC-01 that wires all six contracts
together. Every other test in `test_contracts.py` verifies one contract in
isolation; this one exists to prove the *seams* between them are usable as
specified — that a real generator/validator/engine triple, implementing
nothing but the abstract ports, can carry one fact from raw evidence to a
promoted `KnowledgeRelationship` without any contract needing a shape it
doesn't already have.

The generator/validator/engine below are minimal, in-test doubles — RFC-01
introduces no production implementation of any of them (that begins at
RFC-02). They exist solely to demonstrate composition, not to model real
extraction/validation logic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.contracts.confidence import (
    ConfidenceEngine,
    ConfidenceModel,
    ConfidenceState,
)
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.hypothesis import Hypothesis, HypothesisGenerator
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult

_RUN_ID = "run-lifecycle-1"
_PACK_ID = "pack-lifecycle-1"


def _provenance(generator: GeneratorIdentity) -> Provenance:
    return Provenance(
        generator=generator,
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        pack_id=_PACK_ID,
        pack_version="v1",
        run_id=_RUN_ID,
    )


class _FeignLikeUrlGenerator(HypothesisGenerator):
    """Stands in for a real generator (e.g. a deterministic parser adapter,
    RFC-02): proposes CALLS_SERVICE whenever a code-sourced URL literal
    evidence item names another repository."""

    identity = GeneratorIdentity(
        kind="deterministic", name="url_literal_generator", version="1.0.0"
    )
    consumes = frozenset({"code"})

    async def generate(self, pack: EngineeringEvidencePack) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        for item in pack.items:
            if item.source_type != "code" or item.kind != "url_literal":
                continue
            if "payments-service" not in item.raw_value:
                continue
            hypotheses.append(
                Hypothesis(
                    id=f"hyp:{item.id}:calls_service:payments-service",
                    relationship_type="CALLS_SERVICE",
                    source_entity=f"{pack.repository_id}:orders-service",
                    target_entity="payments-service",
                    evidence_refs=(item.id,),
                    explanation=f"URL literal {item.raw_value!r} names payments-service.",
                    provenance=_provenance(self.identity),
                    generator_confidence=0.7,
                )
            )
        return hypotheses


class _TargetRepositoryExistsValidator(KnowledgeValidator):
    """Stands in for a real validator (RFC-03): confirms a CALLS_SERVICE
    hypothesis whose target_entity matches a repository name known to the
    (fake, in-test) account being indexed."""

    name = "target_repository_exists"
    applies_to = frozenset({"CALLS_SERVICE"})

    def __init__(self, known_repository_names: frozenset[str]) -> None:
        self._known_repository_names = known_repository_names

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        if hypothesis.target_entity in self._known_repository_names:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="confirms",
                evidence_used=hypothesis.evidence_refs,
                source_type="metadata",
                evidence_reliability_tier=2,
                explanation=f"{hypothesis.target_entity!r} is a known indexed repository.",
                provenance=_provenance(identity),
            )
        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="no_signal",
            evidence_used=(),
            source_type="metadata",
            evidence_reliability_tier=0,
            explanation=f"{hypothesis.target_entity!r} is not a known indexed repository.",
            provenance=_provenance(identity),
        )


class _MinimalConfidenceEngine(ConfidenceEngine):
    """Stands in for the real engine (RFC-03): a deliberately small
    version of ADR 0018's stated formula — one confirming source type is
    enough to reach LIKELY, any contradiction rejects outright. Not the
    production formula; just enough to prove the seam works end to end."""

    FORMULA_VERSION = "lifecycle-test-v1"

    def aggregate(
        self, prior: ConfidenceModel | None, new_result: ValidationResult
    ) -> ConfidenceModel:
        contradiction_count = prior.contradiction_count if prior else 0
        confirming_source_types = prior.confirming_source_types if prior else frozenset()
        max_reliability_tier = prior.max_confirming_reliability_tier if prior else 0

        if new_result.verdict == "contradicts":
            contradiction_count += 1
            state = ConfidenceState.REJECTED
        elif new_result.verdict == "confirms":
            confirming_source_types = confirming_source_types | {new_result.source_type}
            max_reliability_tier = max(max_reliability_tier, new_result.evidence_reliability_tier)
            state = (
                ConfidenceState.LIKELY if contradiction_count == 0 else ConfidenceState.CONFLICTING
            )
        else:
            state = prior.state if prior else ConfidenceState.CANDIDATE

        return ConfidenceModel(
            state=state,
            distinct_confirming_source_types=len(confirming_source_types),
            confirming_source_types=confirming_source_types,
            max_confirming_reliability_tier=max_reliability_tier,
            contradiction_count=contradiction_count,
            computed_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
            formula_version=self.FORMULA_VERSION,
        )


class TestFullLifecycleComposition:
    async def test_evidence_to_promoted_relationship(self):
        # 1. Evidence: a single URL-literal fact extracted from source code.
        evidence_item = EvidenceItem(
            id="evidence:orders-service/client.py:12",
            kind="url_literal",
            source_type="code",
            reliability_tier=1,
            reference=EvidenceReference(
                repository_id="orders-service",
                source_type="code",
                locator="client.py",
                line=12,
            ),
            raw_value="https://payments-service.internal/api/charge",
            provenance=_provenance(
                GeneratorIdentity(kind="deterministic", name="url_extractor", version="1.0.0")
            ),
        )
        pack = EngineeringEvidencePack(
            id=_PACK_ID,
            repository_id="orders-service",
            commit_sha="abc123",
            schema_version="v1",
            items=(evidence_item,),
        )

        # 2. Hypothesis: the generator proposes CALLS_SERVICE from that evidence.
        generator = _FeignLikeUrlGenerator()
        hypotheses = await generator.generate(pack)
        assert len(hypotheses) == 1
        hypothesis = hypotheses[0]
        assert hypothesis.relationship_type == "CALLS_SERVICE"
        assert hypothesis.evidence_refs == (evidence_item.id,)

        # 3. Validation: the validator confirms the target is a known repository.
        validator = _TargetRepositoryExistsValidator(
            known_repository_names=frozenset({"payments-service"})
        )
        validation_result = await validator.validate(hypothesis, pack)
        assert validation_result.verdict == "confirms"
        assert validation_result.hypothesis_id == hypothesis.id

        # 4. Confidence: the engine folds the validation result into a state.
        engine = _MinimalConfidenceEngine()
        confidence = engine.aggregate(None, validation_result)
        assert confidence.state == ConfidenceState.LIKELY
        assert confidence.distinct_confirming_source_types == 1
        assert confidence.contradiction_count == 0

        # 5. Knowledge: a KnowledgeRelationship is promoted, carrying full
        #    traceability back to the hypothesis and generator that produced it.
        relationship = KnowledgeRelationship(
            id=f"rel:{hypothesis.source_entity}:{hypothesis.relationship_type}:{hypothesis.target_entity}",
            relationship_type=hypothesis.relationship_type,
            source_entity=hypothesis.source_entity,
            target_entity=hypothesis.target_entity,
            confidence=confidence,
            hypothesis_ids=(hypothesis.id,),
            provenance=(hypothesis.provenance,),
        )

        assert relationship.confidence.state == ConfidenceState.LIKELY
        assert relationship.hypothesis_ids == (hypothesis.id,)
        # Explainability, end to end: from the promoted relationship, the
        # exact evidence item that started this chain is still reachable.
        assert relationship.provenance[0].pack_id == pack.id
        traced_hypothesis = hypothesis
        assert traced_hypothesis.evidence_refs[0] == evidence_item.id

    async def test_contradiction_rejects_regardless_of_prior_confirmation(self):
        """Monotonicity in the reject direction (ADR 0018): once a
        contradiction is seen, the aggregated state must reflect it, even
        if an earlier result had confirmed the same hypothesis."""
        confirming_provenance = _provenance(
            GeneratorIdentity(kind="deterministic", name="validator_a", version="1.0.0")
        )
        contradicting_provenance = _provenance(
            GeneratorIdentity(kind="deterministic", name="validator_b", version="1.0.0")
        )
        confirms = ValidationResult(
            hypothesis_id="hyp-1",
            validator_name="validator_a",
            verdict="confirms",
            evidence_used=("evidence-1",),
            source_type="code",
            evidence_reliability_tier=3,
            explanation="confirmed",
            provenance=confirming_provenance,
        )
        contradicts = ValidationResult(
            hypothesis_id="hyp-1",
            validator_name="validator_b",
            verdict="contradicts",
            evidence_used=("evidence-2",),
            source_type="metadata",
            evidence_reliability_tier=2,
            explanation="target repository does not exist",
            provenance=contradicting_provenance,
        )

        engine = _MinimalConfidenceEngine()
        after_confirm = engine.aggregate(None, confirms)
        assert after_confirm.state == ConfidenceState.LIKELY

        after_contradiction = engine.aggregate(after_confirm, contradicts)
        assert after_contradiction.state == ConfidenceState.REJECTED
        assert after_contradiction.contradiction_count == 1
