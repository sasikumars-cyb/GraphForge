"""ADR 0018 — `run_validators`'s parallel execution: proves the concurrent
implementation is still deterministic and still isolates failures, not
just that it "usually works"."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult
from app.knowledge_engine.validators.cross_repo import CROSS_REPO_VALIDATORS
from app.knowledge_engine.validators.deterministic_structural import (
    DETERMINISTIC_STRUCTURAL_VALIDATORS,
)
from app.knowledge_engine.validators.evidence_keyword import EVIDENCE_KEYWORD_VALIDATORS
from app.knowledge_engine.validators.registry import ALL_VALIDATORS, run_validators

pytestmark = pytest.mark.asyncio


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:1",
        pack_version="v1",
        run_id="pack:1",
    )


def _hypothesis(relationship_type: str = "TEST_TYPE") -> Hypothesis:
    return Hypothesis(
        id="hyp:1",
        relationship_type=relationship_type,
        source_entity="a",
        target_entity="b",
        evidence_refs=("evidence:1",),
        explanation="test",
        provenance=_provenance(),
    )


def _pack() -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack:1", repository_id="repo-1", commit_sha="abc", schema_version="v1"
    )


class _DelayedValidator(KnowledgeValidator):
    """Sleeps a caller-chosen duration before returning, so tests can
    assert results come back in `applicable`'s fixed order even when
    validators finish in the opposite order they were scheduled."""

    def __init__(self, name: str, delay: float, verdict: str = "no_signal") -> None:
        self.name = name
        self.applies_to = frozenset({"TEST_TYPE"})
        self._delay = delay
        self._verdict = verdict

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        await asyncio.sleep(self._delay)
        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict=self._verdict,  # type: ignore[arg-type]
            evidence_used=(),
            source_type="test",
            evidence_reliability_tier=0,
            explanation="test",
            provenance=_provenance(),
        )


class _RaisingValidator(KnowledgeValidator):
    def __init__(self, name: str) -> None:
        self.name = name
        self.applies_to = frozenset({"TEST_TYPE"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        raise RuntimeError("simulated validator failure")


async def test_results_preserve_validators_fixed_order_regardless_of_completion_timing() -> None:
    slow_first = _DelayedValidator("slow", delay=0.05)
    fast_second = _DelayedValidator("fast", delay=0.0)

    results = await run_validators(_hypothesis(), _pack(), (slow_first, fast_second))

    assert [r.validator_name for r in results] == ["slow", "fast"]


async def test_one_validator_raising_does_not_discard_the_others() -> None:
    validators = (
        _DelayedValidator("before", delay=0.0),
        _RaisingValidator("boom"),
        _DelayedValidator("after", delay=0.0),
    )

    results = await run_validators(_hypothesis(), _pack(), validators)

    assert [r.validator_name for r in results] == ["before", "after"]


async def test_validators_actually_run_concurrently_not_sequentially() -> None:
    # Three validators each sleeping 0.05s: sequential execution would take
    # >=0.15s; concurrent execution should complete in well under that.
    validators = tuple(_DelayedValidator(f"v{i}", delay=0.05) for i in range(3))

    started = time.monotonic()
    await run_validators(_hypothesis(), _pack(), validators)
    elapsed = time.monotonic() - started

    assert elapsed < 0.12


async def test_repeated_runs_are_identical() -> None:
    validators = (
        _DelayedValidator("a", delay=0.01, verdict="confirms"),
        _DelayedValidator("b", delay=0.0, verdict="no_signal"),
    )
    hypothesis = _hypothesis()
    pack = _pack()

    first = await run_validators(hypothesis, pack, validators)
    second = await run_validators(hypothesis, pack, validators)

    assert [(r.validator_name, r.verdict) for r in first] == [
        (r.validator_name, r.verdict) for r in second
    ]


def test_all_validators_is_the_union_of_every_family() -> None:
    assert set(ALL_VALIDATORS) == set(DETERMINISTIC_STRUCTURAL_VALIDATORS) | set(
        CROSS_REPO_VALIDATORS
    ) | set(EVIDENCE_KEYWORD_VALIDATORS)


async def test_all_validators_parity_for_deterministic_hypothesis() -> None:
    """Regression parity: combining validator families into ALL_VALIDATORS
    must not change which validators fire for an existing relationship
    type — CONTAINS is only ever recognized by deterministic-structural
    validators, never by cross-repo or evidence-keyword ones."""
    hypothesis = _hypothesis("CONTAINS")
    only_deterministic = await run_validators(
        hypothesis, _pack(), DETERMINISTIC_STRUCTURAL_VALIDATORS
    )
    from_all = await run_validators(hypothesis, _pack(), ALL_VALIDATORS)

    assert [r.validator_name for r in only_deterministic] == [r.validator_name for r in from_all]
