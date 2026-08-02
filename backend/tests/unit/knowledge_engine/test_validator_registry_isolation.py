"""ADR 0018 RFC-03A Part 2 — `run_validators` must isolate one validator's
failure from every other validator, the same resilience guarantee already
proven for generator failures (RFC-02B). Before this fix, one exception
aborted the whole dispatch — verified absent by direct code inspection
during the RFC-03A audit; this test locks the fix in place.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult
from app.knowledge_engine.validators.registry import run_validators


def _pack() -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack-1", repository_id="repo-1", commit_sha="abc123", schema_version="v1"
    )


def _hypothesis() -> Hypothesis:
    provenance = Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        pack_id="pack-1",
        pack_version="v1",
        run_id="pack-1",
    )
    return Hypothesis(
        id="hyp-1",
        relationship_type="CONTAINS",
        source_entity="repo-1:repository",
        target_entity="repo-1:module:app",
        evidence_refs=("evidence-1",),
        explanation="test",
        provenance=provenance,
    )


class _RaisingValidator(KnowledgeValidator):
    name = "raising_validator"
    applies_to = frozenset({"CONTAINS"})

    async def validate(self, hypothesis, pack):  # noqa: ARG002
        raise RuntimeError("simulated validator failure")


class _WorkingValidator(KnowledgeValidator):
    name = "working_validator"
    applies_to = frozenset({"CONTAINS"})

    async def validate(self, hypothesis, pack) -> ValidationResult:  # noqa: ARG002
        provenance = Provenance(
            generator=GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0"),
            produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
            pack_id=pack.id,
            pack_version="v1",
            run_id=pack.id,
        )
        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="confirms",
            evidence_used=("evidence-1",),
            source_type="test",
            evidence_reliability_tier=3,
            explanation="worked fine",
            provenance=provenance,
        )


class TestValidatorFailureIsolation:
    async def test_one_validator_raising_does_not_abort_the_others(self):
        """The exact regression this fixes: a raising validator ordered
        *before* a working one used to prevent the working one's result
        from ever being returned at all."""
        results = await run_validators(
            _hypothesis(), _pack(), (_RaisingValidator(), _WorkingValidator())
        )
        assert len(results) == 1
        assert results[0].validator_name == "working_validator"
        assert results[0].verdict == "confirms"

    async def test_raising_validator_ordered_after_working_one_still_isolated(self):
        results = await run_validators(
            _hypothesis(), _pack(), (_WorkingValidator(), _RaisingValidator())
        )
        assert len(results) == 1
        assert results[0].validator_name == "working_validator"

    async def test_all_validators_raising_returns_empty_not_an_exception(self):
        results = await run_validators(
            _hypothesis(), _pack(), (_RaisingValidator(), _RaisingValidator())
        )
        assert results == []

    async def test_failure_is_logged(self, caplog):
        with caplog.at_level(logging.ERROR, logger="app.knowledge_engine.validators.registry"):
            await run_validators(_hypothesis(), _pack(), (_RaisingValidator(),))

        failure_records = [r for r in caplog.records if "validator_failed" in r.message]
        assert len(failure_records) == 1
        assert "raising_validator" in failure_records[0].message
        assert failure_records[0].exc_info is not None

    async def test_no_validators_apply_returns_empty(self):
        results = await run_validators(_hypothesis(), _pack(), ())
        assert results == []
