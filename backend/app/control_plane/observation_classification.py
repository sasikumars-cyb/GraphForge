"""Deterministic Observation classification — Capabilities contract §16.

Control-Plane-owned, per Cap §16.1: "The Control Plane owns
Classification. It MUST be a deterministic, reproducible function: the
same inputs MUST yield the same class, and all inputs MUST be recorded
so any classification can be independently re-run and challenged."

This module implements ONLY steps 2-6 of Cap §16.2's fixed evaluation
order. Step 1 ("Authorization / dispatch / grant failure -> Blocked") is
deliberately NOT implemented here — it is already fully covered by the
existing `AuthorizationDenied` event (Phase 3): an authorization failure
never reaches Tool dispatch, so `classify_observation` is never even
called for that case (see `app.control_plane.control_plane.
_consume_and_dispatch`, the only real caller — it constructs this
module's inputs strictly AFTER a Tool has actually run). Duplicating
step 1 here would be dead code with no path to ever execute it.

Deliberately no database access, no clock read, no I/O — every input is
an explicit argument on `ClassificationInputs`, gathered by the caller
BEFORE the `ObservationRecorded` event is constructed (events are
immutable; classification cannot be computed after the fact and patched
in). This mirrors `app.control_plane.safety.evaluate_safety_validity`'s
existing "pure decision function taking explicit inputs" precedent
exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["completed", "outcome_unknown"]
PredictionResult = Literal["true", "false", "inconclusive"]

# Cap §16.1's five-way vocabulary, minus "blocked" (see this module's
# own docstring on why step 1 is never reached here). `None` is a valid
# return value distinct from all four of these — see `classify_observation`.
Classification = Literal["expected", "anomaly", "uncertain_outcome", "contradiction"]


@dataclass(frozen=True, slots=True)
class ClassificationInputs:
    """Every input Cap §16.2 steps 2-6 need. `outcome` is the same
    value the Cap §8 state ladder already assigns
    (`ActionExecutionResult.outcome`) — this module does not
    independently determine determinacy, only classifies from it.
    """

    outcome: Outcome
    # Step 3: a transport/dispatch-layer failure signature, distinct
    # from a logically-false Prediction. For this phase's single
    # registered Capability (`query_knowledge_graph`, dispatched
    # synchronously through `ToolExecutor`, which never raises and
    # always returns a determinate `ToolResult.success` — see
    # `ControlPlane._consume_and_dispatch`'s own docstring), the only
    # source of "the Action didn't really run" is `ToolResult.success
    # is False`; the caller computes this, this module only classifies it.
    infrastructure_failure: bool
    # Step 4: whether the Execution Context the Action actually ran
    # under differs from the one the Plan assumed. `query_knowledge_graph`
    # declares `execution_context_requirements=()` (no requirement at
    # all — see `app.capabilities.setup`), so this is structurally
    # always False for this phase's representative Capability; the field
    # exists so a future Capability with real requirements has
    # somewhere to report a mismatch.
    execution_context_mismatch: bool
    # Steps 5-6: the pinned Prediction's own evaluated result.
    prediction_result: PredictionResult


def classify_observation(inputs: ClassificationInputs) -> Classification | None:
    """Cap §16.2 steps 2-6, evaluated in the contract's exact, normative
    order — "a different order yields a different class for the same
    Observation."

    Returns `None` in exactly two cases, neither of which is a member of
    the five-way vocabulary:

    - `outcome == "outcome_unknown"` (step 2): Cap §16.2 names this its
      OWN determination, not a class — collapsing it into
      `uncertain_outcome` would be wrong (see this repo's Phase 5 design
      audit §8: "Do NOT assume outcome_unknown -> uncertain_outcome
      unless the contract explicitly says so" — it does not).
      `ActionOutcomeUnknown` is a PREREQUISITE state requiring
      reconciliation (Cap §18.3) before classification can even be
      attempted; evaluation halts here.
    - `execution_context_mismatch` (step 4): Cap §16.2 says this routes
      "to context re-check" — explicitly "*not* Contradiction" and,
      just as pointedly, not any other member of the five-way
      vocabulary either. No context re-check mechanism exists in this
      codebase (out of scope, unreachable for `query_knowledge_graph`
      today per this module's own `ClassificationInputs` docstring) —
      evaluation halts here too, rather than inventing a class the
      contract does not name for this case.
    """
    # Step 2.
    if inputs.outcome == "outcome_unknown":
        return None

    # Step 3.
    if inputs.infrastructure_failure:
        return "anomaly"

    # Step 4.
    if inputs.execution_context_mismatch:
        return None

    # Steps 5-6.
    if inputs.prediction_result == "true":
        return "expected"
    if inputs.prediction_result == "false":
        return "contradiction"
    return "uncertain_outcome"


__all__ = ["ClassificationInputs", "Classification", "Outcome", "PredictionResult", "classify_observation"]
