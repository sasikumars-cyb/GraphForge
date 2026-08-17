"""Control Plane data model — Phase 3.

Implements the artifacts named by Capabilities contract §6 (the
authorization pipeline) and §8 (the state ladder): `ActionProposal`,
`Action`, `Prediction`, and the outcome objects the pipeline produces
(`ConformanceOutcome`/`ConformanceResult`, `EligibilityOutcome`,
`DenialReason`).

Deliberately data-only. No method here evaluates anything — evaluation
lives in `app.control_plane.control_plane.ControlPlane`, so this module
can be imported (and its shape tested) without pulling in Policy,
Safety Validity, or Engineering State persistence.

Reasoning Engine contract §6: an ActionProposal "MUST NOT contain
execution authority." Nothing in this module grants, computes, or caches
authorization — `Action.is_authorized` does not exist as a field, only
as a fact the Control Plane later establishes via a separate
`AuthorizationGrant` (`app/control_plane/grant.py`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ModelError(ValueError):
    """A Control Plane data object was constructed in a shape the model
    itself forbids — raised at construction time (`__post_init__`), not
    deferred to pipeline evaluation. Distinct from a pipeline DENIAL: this
    is "not even a well-formed candidate," never a routed outcome."""


class DenialStage(StrEnum):
    """The pipeline stage at which an Action or proposal was denied — §6's
    "route to the specific failing reason," never a generic failure.
    Mirrors `app.engineering_state.events.validate_authorization_denied`'s
    closed `denial_stage` vocabulary; kept as one source of truth by the
    Control Plane always writing `DenialStage.value` into that payload
    field, never a free-form string."""

    CAPABILITY_GAP = "capability_gap"
    SCOPE_VIOLATION = "scope_violation"
    PREDICTION_INADMISSIBLE = "prediction_inadmissible"
    POLICY_DENIAL = "policy_denial"
    CONSTRAINT_VIOLATION = "constraint_violation"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STALE_SAFETY_VALIDITY = "stale_safety_validity"
    LEASE_CONFLICT = "lease_conflict"
    AWAITING_HUMAN = "awaiting_human"
    ARTIFACT_IDENTITY_MISMATCH = "artifact_identity_mismatch"
    PRECONDITION_INVALIDATED = "precondition_invalidated_by_prior_step"
    MALFORMED_PROPOSAL = "malformed_proposal"


@dataclass(frozen=True, slots=True)
class Prediction:
    """Cap §13. Requirements 1-4 are mechanically checked at Proposal
    Conformance (`ControlPlane.check_conformance`); requirement 5
    (necessary-condition relationship to the PlanStep postcondition) is a
    declared, NOT mechanically checked, architectural property — recorded
    here for longitudinal calibration, never enforced as a pass/fail gate.
    """

    target_observable: str
    falsification_condition: str
    evaluation_procedure: str
    execution_context: dict[str, Any]
    # Requirement 5 — declared, not checked. A human-readable statement of
    # why this Prediction being false implies the PlanStep postcondition
    # cannot hold. Required to be non-empty (a declaration must exist) but
    # its truth is never mechanically verified by this module.
    necessary_condition_rationale: str

    def __post_init__(self) -> None:
        if not self.target_observable.strip():
            raise ModelError("Prediction.target_observable must be non-empty.")
        if not self.falsification_condition.strip():
            raise ModelError("Prediction.falsification_condition must be non-empty.")
        if not self.evaluation_procedure.strip():
            raise ModelError("Prediction.evaluation_procedure must be non-empty.")
        if not self.necessary_condition_rationale.strip():
            raise ModelError("Prediction.necessary_condition_rationale must be non-empty.")


@dataclass(frozen=True, slots=True)
class Action:
    """One unit of the composition an ActionProposal proposes — §6's
    per-Action dispatch loop operates over these, one Grant each (§7,
    §9: "MUST NOT receive one authorization for its lifetime").

    `action_id` is this Action's own stable identity — generated once at
    proposal construction and never regenerated; a retry is a NEW Action
    with a NEW `action_id` (§7: "a retry ... requires a new Grant"),
    constructed as a new `Action`, never by mutating this one.
    """

    action_id: uuid.UUID
    capability_id: str
    capability_version: int
    parameters: dict[str, Any]
    prediction: Prediction
    plan_step_id: uuid.UUID
    # §14: only set when this Action CONSUMES a prior artifact. None means
    # "no artifact-identity precondition applies" — true for every Action
    # bound to a Capability with produces_artifact=False on both ends.
    expected_artifact_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ModelError("Action.capability_id must be non-empty.")
        if self.capability_version < 1:
            raise ModelError("Action.capability_version must be >= 1.")


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """Reasoning Engine contract §6: inert data, "MUST NOT contain
    execution authority." Produced by the Reasoning Plane, consumed by
    the Control Plane. Composition order is the tuple order of `actions`
    — §9's "in composition order" per-Action dispatch loop iterates it
    positionally, never re-sorts it.
    """

    proposal_id: uuid.UUID
    task_id: uuid.UUID
    goal_id: uuid.UUID
    proposing_role: str
    actions: tuple[Action, ...]
    # The Engineering State snapshot (a specific, addressable point) this
    # proposal was generated against — Cap §6: "Conformance is evaluated
    # against a state snapshot." An event id, not a timestamp: the
    # Control Plane can look up exactly what was known when.
    engineering_state_snapshot_event_id: uuid.UUID | None

    def __post_init__(self) -> None:
        if not self.proposing_role.strip():
            raise ModelError("ActionProposal.proposing_role must be non-empty.")
        if len(self.actions) == 0:
            raise ModelError("ActionProposal.actions must be non-empty.")
        action_ids = [a.action_id for a in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ModelError("ActionProposal.actions must have unique action_id values.")


@dataclass(frozen=True, slots=True)
class ConformanceResult:
    """Outcome of `ControlPlane.check_conformance()` — Cap §8 state 1,
    "Proposal Conformant." A property of the artifact; `conformant=True`
    grants nothing (enforced by `ControlPlane` never reading this object
    as if it were authorization)."""

    proposal_id: uuid.UUID
    conformant: bool
    denial_stage: DenialStage | None
    denial_reason: str | None

    def __post_init__(self) -> None:
        if self.conformant and (self.denial_stage is not None or self.denial_reason is not None):
            raise ModelError("A conformant ConformanceResult must not carry a denial.")
        if not self.conformant and (self.denial_stage is None or self.denial_reason is None):
            raise ModelError("A non-conformant ConformanceResult must carry a denial.")


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    """Outcome of the per-Action Action-Eligible check — Cap §8 state 2.
    "NOT permission. Eligibility means 'nothing structurally blocks it,'
    never 'you may do it.'" `ControlPlane` never treats `eligible=True` as
    a Grant."""

    action_id: uuid.UUID
    eligible: bool
    denial_stage: DenialStage | None
    denial_reason: str | None

    def __post_init__(self) -> None:
        if self.eligible and (self.denial_stage is not None or self.denial_reason is not None):
            raise ModelError("An eligible EligibilityResult must not carry a denial.")
        if not self.eligible and (self.denial_stage is None or self.denial_reason is None):
            raise ModelError("An ineligible EligibilityResult must carry a denial.")


@dataclass(frozen=True, slots=True)
class SafetyValidityInputs:
    """The specific decision inputs Safety Validity was evaluated against
    — Cap §7.1 requires the Grant payload record "Safety Validity result
    AND its evaluated inputs," not just a boolean. Kept separate from
    `app.control_plane.safety.SafetyValidityResult` so the inputs record
    (what was looked at) and the result (what was concluded) are never
    conflated into one mutable field (Cap §2)."""

    policy_version_id: str
    human_approval_content_hash: str | None
    evaluated_at: str  # ISO-8601, set by the caller — this module has no clock.
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "Action",
    "ActionProposal",
    "ConformanceResult",
    "DenialStage",
    "EligibilityResult",
    "ModelError",
    "Prediction",
    "SafetyValidityInputs",
]
