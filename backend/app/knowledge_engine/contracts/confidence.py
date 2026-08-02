"""Confidence — the deterministic aggregation of validator results into a
graph-facing trust state (ADR 0018: "validators determine trust", "the
LLM never decides confidence, the validator computes confidence").

`ConfidenceEngine.aggregate` is incremental, not batch: ADR 0018's event
flow calls it once per `ValidationResult` as it arrives, because
distributed validators report at independent latencies — there is no
"wait for all validators" checkpoint to key a single batch computation on.
It is also required to be monotonic in the direction documented on the
method itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.knowledge_engine.contracts.validation import ValidationResult


class ConfidenceState(StrEnum):
    VERIFIED = "verified"
    HIGHLY_LIKELY = "highly_likely"
    LIKELY = "likely"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class ConfidenceModel:
    """The current trust state for one relationship, plus the counters the
    state was derived from — kept alongside the state itself (not just the
    state) so a later transition can be computed incrementally without
    re-scanning every prior `ValidationResult`.

    `formula_version` pins which version of the aggregation logic produced
    this state — ADR 0018 requires the aggregation function itself to be
    versioned so a formula change is auditable against relationships
    computed under a prior version.

    `confirming_source_types` — added in RFC-03, not part of RFC-01's
    original shape — is the actual *set* of distinct `ValidationResult
    .source_type` values that have confirmed this relationship so far, not
    just their count. This closes a real gap: an incremental `aggregate
    (prior, new_result)` cannot correctly tell whether a newly-arriving
    confirming result's `source_type` is one it has already counted
    without retaining the set itself — a bare integer count cannot answer
    "have I seen this one before." `distinct_confirming_source_types`
    remains present as the derived, at-a-glance count (`== len
    (confirming_source_types)`, enforced below) — most callers only need
    the count, but the engine needs the set to compute the next count
    correctly.

    `max_confirming_reliability_tier` — added alongside
    `confirming_source_types` in RFC-03, for the same reason. `evidence
    _reliability_tier` (see `ValidationResult`) is what actually
    distinguishes a literal annotation match (this codebase's highest-
    trust deterministic signal) from a same-shape but weaker match (e.g. a
    dependency-coordinate name match) — without carrying the highest tier
    seen so far, a `ConfidenceEngine` cannot correctly preserve that
    distinction once a stronger confirmation has already been recorded
    (monotonicity requires remembering the strongest evidence ever seen,
    not just the most recent). `0` means "no confirmation has been
    recorded yet" — consistent with `distinct_confirming_source_types
    == 0`.
    """

    state: ConfidenceState
    distinct_confirming_source_types: int
    confirming_source_types: frozenset[str]
    max_confirming_reliability_tier: int
    contradiction_count: int
    computed_at: datetime
    formula_version: str

    def __post_init__(self) -> None:
        if not self.formula_version.strip():
            raise ValueError("ConfidenceModel.formula_version must not be empty")
        if self.distinct_confirming_source_types < 0:
            raise ValueError(
                "ConfidenceModel.distinct_confirming_source_types must not be negative"
            )
        if self.contradiction_count < 0:
            raise ValueError("ConfidenceModel.contradiction_count must not be negative")
        if self.max_confirming_reliability_tier < 0:
            raise ValueError("ConfidenceModel.max_confirming_reliability_tier must not be negative")
        if len(self.confirming_source_types) != self.distinct_confirming_source_types:
            raise ValueError(
                "ConfidenceModel.distinct_confirming_source_types must equal "
                "len(confirming_source_types) — the count and the set it summarizes "
                "must never drift apart"
            )
        if self.distinct_confirming_source_types == 0 and self.max_confirming_reliability_tier != 0:
            raise ValueError(
                "ConfidenceModel.max_confirming_reliability_tier must be 0 when there are "
                "no confirming source types — there is no evidence to have a tier"
            )


class ConfidenceEngine(ABC):
    """Port the confidence-aggregation logic implements. Pure and
    deterministic (ADR 0018's implementation principles: "same inputs, same
    output, every time").
    """

    @abstractmethod
    def aggregate(
        self, prior: ConfidenceModel | None, new_result: ValidationResult
    ) -> ConfidenceModel:
        """Fold one new `ValidationResult` into the relationship's current
        `ConfidenceModel` (or `None` if this is the first result seen for
        it). Must be monotonic: a new `confirms` result may only strengthen
        `state`; a new `contradicts` result may reduce it toward
        `CONFLICTING`/`REJECTED`; the mere absence of an expected
        validator's report so far must never regress an already-computed
        `state` — silence is not evidence of anything."""
        raise NotImplementedError
