"""Default `ConfidenceEngine` — ADR 0018 RFC-03.

The formula generalizes `app.indexer.graph.cross_repo_linker.py`'s
existing two-tier `structural`/`heuristic` confidence vocabulary (see that
module's `_feign_service_calls`/`_kafka_topic_overlap`/
`_shared_dependency_name` docstrings for the original reasoning this
formula formalizes, not reinvents) into the six-state `ConfidenceState`
model:

- Any contradiction, ever: `REJECTED` if none had confirmed yet,
  `CONFLICTING` if at least one confirmation had already been recorded.
  Never regresses back to a stronger state once contradicted — RFC-01's
  monotonic-reject guarantee.
- No contradictions, at least one confirmation:
  - highest `evidence_reliability_tier` ever confirmed is "high" (see
    `HIGH_RELIABILITY_TIER`) and >= 2 distinct confirming source types ->
    `VERIFIED` (multiple independent, high-reliability confirmations).
  - highest reliability tier ever confirmed is "high" — one confirmation
    is already enough -> `HIGHLY_LIKELY`. This is the direct
    generalization of `cross_repo_linker.py`'s "structural" label: a
    literal `@FeignClient`/Kafka-topic match is this codebase's
    highest-trust deterministic signal, and always was, even as a single
    confirmation.
  - only lower-reliability confirmations ever seen -> `LIKELY`. This is
    the generalization of "heuristic": a dependency-coordinate name match
    is real signal, never promoted to the same trust tier as a literal
    annotation match on a single confirmation alone.
- No confirmations, no contradictions -> `CANDIDATE`.

`HIGH_RELIABILITY_TIER = 3` reuses the exact constant value RFC-02's
`deterministic_generator._DETERMINISTIC_RELIABILITY_TIER` already
established for "a deterministic parser's own literal finding" — the same
class of evidence a literal `@FeignClient`/Kafka-topic match is.

Both threshold constants are public (no leading underscore) specifically
so `app.knowledge_engine.explainability.ConfidenceExplainer` can describe
*why* a state was reached (e.g. "VERIFIED requires >= 2 distinct
high-reliability domains") using the exact values this engine actually
decides with, rather than a second, hand-copied "2" that could silently
drift out of sync with this formula. Visibility only — the aggregation
logic itself (`aggregate`/`_state_for`) is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.contracts.confidence import (
    ConfidenceEngine,
    ConfidenceModel,
    ConfidenceState,
)
from app.knowledge_engine.contracts.validation import ValidationResult

FORMULA_VERSION = "v1"
HIGH_RELIABILITY_TIER = 3
MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED = 2


class DefaultConfidenceEngine(ConfidenceEngine):
    def aggregate(
        self, prior: ConfidenceModel | None, new_result: ValidationResult
    ) -> ConfidenceModel:
        contradiction_count = prior.contradiction_count if prior else 0
        confirming_source_types: frozenset[str] = (
            prior.confirming_source_types if prior else frozenset()
        )
        max_reliability_tier = prior.max_confirming_reliability_tier if prior else 0

        if new_result.verdict == "contradicts":
            contradiction_count += 1
        elif new_result.verdict == "confirms":
            confirming_source_types = confirming_source_types | {new_result.source_type}
            max_reliability_tier = max(max_reliability_tier, new_result.evidence_reliability_tier)

        # Whether *any* confirmation exists at all, evaluated on the
        # up-to-date set (after this event is folded in) — not "had
        # already confirmed before this specific event." CONFLICTING means
        # "both confirming and contradicting evidence exist," which is true
        # regardless of which kind arrived first.
        state = self._state_for(
            contradiction_count=contradiction_count,
            confirming_source_types=confirming_source_types,
            max_reliability_tier=max_reliability_tier,
        )

        return ConfidenceModel(
            state=state,
            distinct_confirming_source_types=len(confirming_source_types),
            confirming_source_types=confirming_source_types,
            max_confirming_reliability_tier=max_reliability_tier,
            contradiction_count=contradiction_count,
            computed_at=datetime.now(UTC),
            formula_version=FORMULA_VERSION,
        )

    @staticmethod
    def _state_for(
        *,
        contradiction_count: int,
        confirming_source_types: frozenset[str],
        max_reliability_tier: int,
    ) -> ConfidenceState:
        if contradiction_count > 0:
            return (
                ConfidenceState.CONFLICTING if confirming_source_types else ConfidenceState.REJECTED
            )
        if not confirming_source_types:
            return ConfidenceState.CANDIDATE
        if max_reliability_tier >= HIGH_RELIABILITY_TIER:
            if len(confirming_source_types) >= MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED:
                return ConfidenceState.VERIFIED
            return ConfidenceState.HIGHLY_LIKELY
        return ConfidenceState.LIKELY
