"""Validator plugin registry — same shape as `app.indexer.graph
.cross_repo_linker.CROSS_REPO_LINK_RULES`: a tuple of plugins, not an
if-chain, so adding a validator is one more registry entry, not a rewrite
of the dispatcher.

Validators exist across two domains as of RFC-03A
(`app.knowledge_engine.validators.cross_repo` for cross-repository
relationships, `.deterministic_structural` for one-repository
relationships), which is what justifies a registry now — unlike
`shadow_runner.py`'s deliberately-deferred `GeneratorRegistry` (ADR 0018's
RFC-02B note), there is no "only one implementation" situation here to
defer against.

RFC-03A isolation fix: one validator's exception must never abort
validation of the rest — the same resilience pattern `shadow_runner
.run_shadow_hypothesis_generation` already established for generator
failures (log and continue, never propagate). Before this fix,
`run_validators` had no such guard — a single validator's exception
propagated out of the whole call, silently discarding every other
validator's result for that hypothesis. Verified absent by direct code
inspection during the RFC-03A audit before this change.

Parallel execution (ADR 0018's validator-architecture RFC): applicable
validators run concurrently via `asyncio.gather`, not sequentially — a
real win once several validators apply to the same hypothesis (e.g. the
Frontier generator's capability hypotheses, checked by up to four
`EvidenceKeywordValidator` instances at once). Still provably
deterministic despite the concurrency: every `KnowledgeValidator.validate`
is a pure read of `pack` (frozen, never mutated), and results are
reassembled in the *fixed* order `applicable` was built in — never
"whichever finished first" — so the returned list, and everything the
confidence engine folds from it, is identical to what strict sequential
execution would have produced. `return_exceptions=True` preserves the
exact same isolation guarantee this docstring's first paragraph
describes, just without needing a `try`/`except` per iteration.

`ALL_VALIDATORS` — the aggregate registry every generator's hypotheses
are checked against by default (see `shadow_runner.to_knowledge_relationship
`'s own default parameter). Safe to combine unconditionally: a validator
only ever evaluates a hypothesis whose `relationship_type` is in its own
`applies_to` (this function's whole point), so merging validator families
that recognize disjoint relationship-type vocabularies changes nothing
about which validators fire for any existing hypothesis — deterministic-
generator and cross-repository hypotheses are dispatched to exactly the
same validators as before, and only the Frontier generator's (currently
unvalidated) capability hypotheses gain any new coverage.
"""

from __future__ import annotations

import asyncio
import logging

from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult
from app.knowledge_engine.validators.cross_repo import CROSS_REPO_VALIDATORS
from app.knowledge_engine.validators.deterministic_structural import (
    DETERMINISTIC_STRUCTURAL_VALIDATORS,
)
from app.knowledge_engine.validators.evidence_keyword import EVIDENCE_KEYWORD_VALIDATORS
from app.knowledge_engine.validators.generic_structural import GENERIC_STRUCTURAL_VALIDATORS

logger = logging.getLogger(__name__)

ALL_VALIDATORS: tuple[KnowledgeValidator, ...] = (
    *DETERMINISTIC_STRUCTURAL_VALIDATORS,
    *CROSS_REPO_VALIDATORS,
    *EVIDENCE_KEYWORD_VALIDATORS,
    *GENERIC_STRUCTURAL_VALIDATORS,
)


async def run_validators(
    hypothesis: Hypothesis,
    pack: EngineeringEvidencePack,
    validators: tuple[KnowledgeValidator, ...],
) -> list[ValidationResult]:
    """Every registered validator whose `applies_to` includes this
    hypothesis's `relationship_type`, run against it concurrently — the
    O(1) fast-filtering ADR 0018 requires (a validator never evaluates a
    hypothesis type it didn't register for), applied before any `await`.

    A validator that raises is logged and skipped — its result is simply
    absent from the returned list, exactly as if it had never been
    registered for this hypothesis. Every other validator still runs;
    nothing downstream (indexing, other hypotheses, other validators)
    observes the failure except this one log line. See this module's own
    docstring for why concurrent execution here is still deterministic."""
    applicable = [v for v in validators if hypothesis.relationship_type in v.applies_to]
    if not applicable:
        return []

    outcomes = await asyncio.gather(
        *(validator.validate(hypothesis, pack) for validator in applicable),
        return_exceptions=True,
    )

    results: list[ValidationResult] = []
    for validator, outcome in zip(applicable, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.exception(
                "validator_failed validator_name=%s hypothesis_id=%s relationship_type=%s",
                validator.name,
                hypothesis.id,
                hypothesis.relationship_type,
                exc_info=outcome,
            )
            continue
        results.append(outcome)
    return results
