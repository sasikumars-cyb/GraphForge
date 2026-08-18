"""The Engineering State event vocabulary and payload shape validation.

`docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md` §8 requires "meaningful
domain events", explicitly warning against "simply turn[ing] every
database update into an event." Phase 1 defined the closed list it
actually needed and deliberately deferred `AuthorizationGranted/Consumed/
Denied/Invalidated` (this module's own prior docstring named them
explicitly as reserved-but-not-yet-implemented, "a later phase adds it
here when it has a real producer").

**Phase 3 identifies, and now implements, exactly that need** — per
Capabilities contract §7.1: "Authorization Grants are durable Engineering
State events... Event classes: AuthorizationGranted, AuthorizationConsumed,
AuthorizationDenied, AuthorizationInvalidated." This is not a speculative
addition; it is the specific extension the frozen contract already
names and Phase 1 already anticipated. One further type,
`AuthorizationConsuming`, is added beyond the bare contract list — this
extension is itself justified, not silently invented: the crash-safety
requirement (Phase 3 instructions §11) needs a state recorded BETWEEN
"granted" and "consumed", *before* dispatch begins, so a crash mid-dispatch
leaves a durable, honest "consuming" record rather than either silently
reusing the Grant or silently losing the fact that dispatch was attempted
— see `app.control_plane.control_plane` for where this is actually used.

Every payload validator below enforces the SHAPE the Engineering State
contract requires — it does not (and, for Phase 1's original set,
cannot) implement the *derivation* logic behind those shapes. Concretely:
`validate_belief` requires a Belief payload to already carry
`confidence`/`uncertainty`/`evidence_sufficiency`/`qualitative_status`/
`derivation_method` (ES §6) — it does not compute those values from cited
Evidence, because that computation is Reasoning Plane logic (Reasoning
Engine contract §5), and no Reasoning Plane exists yet. The same
discipline applies to the new Authorization* validators: they enforce
shape, never derive it.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

# --- The closed vocabulary -------------------------------------------------

GOAL_CREATED = "GoalCreated"
GOAL_UPDATED = "GoalUpdated"
PLAN_CREATED = "PlanCreated"
PLAN_STEP_CREATED = "PlanStepCreated"
# Phase 6, ES §11/§8: the minimum new event type needed to durably mark an
# ALREADY-APPENDED PlanStepCreated as invalidated, without editing it —
# mirrors the Phase 4 Workspace-lifecycle precedent exactly (a base
# creation event plus a satellite lifecycle event causally anchored to
# it), applied to PlanStep instead of Workspace. See this module's own
# `validate_plan_step_invalidated` for why this is the smallest addition
# that satisfies the contract, not a broader Replan event/workflow.
PLAN_STEP_INVALIDATED = "PlanStepInvalidated"
DECISION_MADE = "DecisionMade"
EVIDENCE_RECORDED = "EvidenceRecorded"
BELIEF_RECORDED = "BeliefRecorded"
OBSERVATION_RECORDED = "ObservationRecorded"

# --- Phase 3: the Authorization Grant lifecycle (Cap §7.1) -----------------
AUTHORIZATION_GRANTED = "AuthorizationGranted"
AUTHORIZATION_CONSUMING = "AuthorizationConsuming"
AUTHORIZATION_CONSUMED = "AuthorizationConsumed"
AUTHORIZATION_DENIED = "AuthorizationDenied"
AUTHORIZATION_INVALIDATED = "AuthorizationInvalidated"

# --- Phase 4: Workspace lifecycle (Cap §19) ---------------------------------
#
# Five event types, each independently justified against a distinct durable
# fact §19 requires (design audit ruled out folding these into fewer types,
# and ruled out a sixth for credential-incident/custodial/lease-expiry
# destruction — those are `WorkspaceDestroyed.reason` values, mirroring the
# `AuthorizationDenied.denial_stage` precedent, not separate event types).
WORKSPACE_CREATED = "WorkspaceCreated"
WORKSPACE_LEASE_RENEWED = "WorkspaceLeaseRenewed"
WORKSPACE_DIAGNOSTIC_HOLD_ENTERED = "WorkspaceDiagnosticHoldEntered"
WORKSPACE_WRITE_AUTHORIZATION_REVOKED = "WorkspaceWriteAuthorizationRevoked"
WORKSPACE_DESTROYED = "WorkspaceDestroyed"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        GOAL_CREATED,
        GOAL_UPDATED,
        PLAN_CREATED,
        PLAN_STEP_CREATED,
        PLAN_STEP_INVALIDATED,
        DECISION_MADE,
        EVIDENCE_RECORDED,
        BELIEF_RECORDED,
        OBSERVATION_RECORDED,
        AUTHORIZATION_GRANTED,
        AUTHORIZATION_CONSUMING,
        AUTHORIZATION_CONSUMED,
        AUTHORIZATION_DENIED,
        AUTHORIZATION_INVALIDATED,
        WORKSPACE_CREATED,
        WORKSPACE_LEASE_RENEWED,
        WORKSPACE_DIAGNOSTIC_HOLD_ENTERED,
        WORKSPACE_WRITE_AUTHORIZATION_REVOKED,
        WORKSPACE_DESTROYED,
    }
)

# --- Causal reference requirements (Phase 1 correction) ---------------------
#
# `ENGINEERING_STATE_ARCHITECTURE.md` §8: "every event that arises as a
# consequence of another... MUST reference the causing record(s) by
# identifier." The Phase 1 Final Correctness Audit found this was declared
# (the `causation_event_id` column exists) but never actually required or
# checked — a `GoalUpdated` could be durably appended with no real
# `GoalCreated` behind it, silently dropped by `materialize.fold()` rather
# than rejected. This mapping is the single source of truth for which
# event types have exactly one mandatory causal parent, referenced via
# `causation_event_id`, and what that parent's `event_type` must be —
# enforced by `EngineeringEventRepository.append()`, not by this
# (deliberately I/O-free) module.
#
# `BeliefRecorded` is NOT here: it cites `evidence_ids`, a LIST, not a
# single parent — `causation_event_id` is a singular column and cannot
# represent a multi-parent citation. Its evidence references are validated
# by the repository directly against `payload["evidence_ids"]`, not
# through this single-parent mechanism. Keeping the two mechanisms
# distinct (rather than forcing a list-valued relationship through a
# singular column) is the smaller change, not a new framework.
CAUSAL_REQUIREMENTS: dict[str, frozenset[str]] = {
    GOAL_UPDATED: frozenset({GOAL_CREATED}),
    PLAN_CREATED: frozenset({GOAL_CREATED}),
    PLAN_STEP_CREATED: frozenset({PLAN_CREATED}),
    # Phase 6: PlanStepInvalidated's one mandatory causal parent is the
    # specific PlanStepCreated event it invalidates — mirrors every
    # Workspace lifecycle event's identical "always the base creation
    # event" choice immediately below. `contradiction_observation_event_id`
    # (the OTHER reference this event carries) is validated separately,
    # by `EngineeringEventRepository`, against `OBSERVATION_RECORDED`'s
    # `classification` field — not expressible through this single-parent
    # mechanism, the same reasoning `BeliefRecorded.evidence_ids` already
    # established for why some references need their own check.
    PLAN_STEP_INVALIDATED: frozenset({PLAN_STEP_CREATED}),
    # Phase 3: the Grant lifecycle is itself a causal chain — each stage
    # exists only because the prior one happened. AuthorizationDenied is
    # deliberately NOT here: a denial is a base case (like GoalCreated),
    # not a consequence of anything that must already exist.
    AUTHORIZATION_CONSUMING: frozenset({AUTHORIZATION_GRANTED}),
    AUTHORIZATION_CONSUMED: frozenset({AUTHORIZATION_CONSUMING}),
    AUTHORIZATION_INVALIDATED: frozenset({AUTHORIZATION_GRANTED}),
    # Phase 4: every Workspace lifecycle event after creation causally
    # references WorkspaceCreated directly (not the latest intermediate
    # event) — a Destroyed event, for instance, can follow a leased,
    # held, or write-revoked state, so its one stable causal anchor is
    # always the base WorkspaceCreated, mirroring
    # AUTHORIZATION_INVALIDATED's identical choice to reference
    # AUTHORIZATION_GRANTED directly rather than whatever the most
    # recent intermediate event happened to be.
    WORKSPACE_LEASE_RENEWED: frozenset({WORKSPACE_CREATED}),
    WORKSPACE_DIAGNOSTIC_HOLD_ENTERED: frozenset({WORKSPACE_CREATED}),
    WORKSPACE_WRITE_AUTHORIZATION_REVOKED: frozenset({WORKSPACE_CREATED}),
    WORKSPACE_DESTROYED: frozenset({WORKSPACE_CREATED}),
}

# For the event types in CAUSAL_REQUIREMENTS, the payload field that must
# already carry the SAME id as `causation_event_id` — today's payload
# shapes already carry this reference (e.g. GoalUpdated.goal_event_id);
# this correction does not remove or duplicate that field, it requires
# `causation_event_id` to agree with it, closing the gap where the two
# could silently point at different — or nonexistent — events.
CAUSAL_PAYLOAD_REFERENCE_FIELD: dict[str, str] = {
    GOAL_UPDATED: "goal_event_id",
    PLAN_CREATED: "goal_event_id",
    PLAN_STEP_CREATED: "plan_event_id",
    PLAN_STEP_INVALIDATED: "plan_step_event_id",
    # AUTHORIZATION_CONSUMING's immediate causal parent is the Granted
    # event; AUTHORIZATION_CONSUMED's immediate causal parent is the
    # Consuming event (not Granted directly) — checked against
    # "consuming_event_id" below, not "grant_event_id". Consumed's
    # payload ALSO carries "grant_event_id" (required by its own shape
    # validator) purely for convenient querying back to the originating
    # Grant without walking the causal chain two hops; this module has
    # no database access to cross-check it against the resolved
    # Consuming event's own value — the Control Plane, which constructs
    # both events in the same call and has that context, is responsible
    # for setting it correctly, the same way it's responsible for every
    # other payload field being accurate.
    AUTHORIZATION_CONSUMING: "grant_event_id",
    AUTHORIZATION_CONSUMED: "consuming_event_id",
    AUTHORIZATION_INVALIDATED: "grant_event_id",
    # Phase 4: all four reference the base WorkspaceCreated event, via the
    # same "workspace_event_id" payload field name across all of them —
    # one consistent name, since (unlike Authorization*'s multi-hop
    # chain) every Workspace event's causal parent is always the same
    # single event.
    WORKSPACE_LEASE_RENEWED: "workspace_event_id",
    WORKSPACE_DIAGNOSTIC_HOLD_ENTERED: "workspace_event_id",
    WORKSPACE_WRITE_AUTHORIZATION_REVOKED: "workspace_event_id",
    WORKSPACE_DESTROYED: "workspace_event_id",
}


class InvalidEventPayloadError(ValueError):
    """Raised by the repository's `append()` before anything reaches the
    database — a malformed payload must never become a durable, immutable
    event; catching it here is strictly cheaper than catching it via the
    (also real, but last-resort) database CHECK constraint."""


def _require(payload: dict[str, Any], *keys: str, event_type: str) -> None:
    missing = [k for k in keys if k not in payload]
    if missing:
        raise InvalidEventPayloadError(f"{event_type} payload missing required field(s): {missing}")


# --- Per-event-type shape validation ---------------------------------------
#
# Each function raises InvalidEventPayloadError on a shape violation and
# returns None on success — validation only, no mutation, no derivation.


def validate_goal_created(payload: dict[str, Any]) -> None:
    _require(payload, "description", "postconditions", event_type=GOAL_CREATED)
    if not isinstance(payload["postconditions"], list) or not payload["postconditions"]:
        raise InvalidEventPayloadError(
            f"{GOAL_CREATED}.postconditions must be a non-empty list — a Goal "
            "with no checkable postconditions can never establish Goal "
            "Satisfied (Capabilities contract §17)."
        )
    # Phase 7.3 (ownership fix): OPTIONAL — a Goal created before this field
    # existed has none, and that is a valid, permanently-supported shape
    # (see materialize.py's GoalRecord and get_engineering_task/
    # list_engineering_tasks's ownership check), never something to
    # backfill or require retroactively. Human Intent (ES §13: "the
    # durable, high-level Goal a human wants achieved") is inherently
    # human-scoped in the contract's own vocabulary; this simply records
    # which human. Distinct from ES §14's "Role" (multi-agent execution
    # ownership) — this is the authenticated human requester's identity,
    # never a Role. When present, MUST be a real UUID — fail closed at
    # write time rather than let an unparseable value silently defeat the
    # ownership check at read time.
    user_id = payload.get("user_id")
    if user_id is not None:
        try:
            uuid.UUID(str(user_id))
        except ValueError as exc:
            raise InvalidEventPayloadError(
                f"{GOAL_CREATED}.user_id, when present, must be a valid UUID."
            ) from exc


def validate_goal_updated(payload: dict[str, Any]) -> None:
    _require(payload, "goal_event_id", event_type=GOAL_UPDATED)


def validate_plan_created(payload: dict[str, Any]) -> None:
    _require(payload, "goal_event_id", "scope", event_type=PLAN_CREATED)
    # Phase 6, ES §11: "Any revision after approval MUST produce a new
    # Plan version; the approved version MUST remain retrievable
    # unchanged." `supersedes_plan_event_id` is OPTIONAL — a base/first
    # Plan for a Goal supersedes nothing. When present, it is a durable
    # reference to the specific prior `PlanCreated` event this new Plan
    # version replaces — deliberately NOT the causal parent (that remains
    # `goal_event_id`, unchanged): a Plan's cause is always its Goal;
    # supersession is a second, independent relationship, mirroring
    # `EvidenceRecorded`'s own `supersedes: <EvidenceID>` precedent (ES
    # §4) applied to Plan instead of Evidence. Deliberately does NOT
    # introduce a mutable "authoritative" flag anywhere — which Plan is
    # currently eligible remains a DERIVED fact (see
    # `materialize.superseded_plan_event_ids`), never a stored one.
    supersedes = payload.get("supersedes_plan_event_id")
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
        raise InvalidEventPayloadError(
            f"{PLAN_CREATED}.supersedes_plan_event_id, when present, must be a non-empty str."
        )


def validate_plan_step_created(payload: dict[str, Any]) -> None:
    # Phase 5, Cap §15.1: Independent Verification resolves the PlanStep
    # postcondition it evaluates ONLY by reading this field back from the
    # immutable event log (by `plan_step_event_id` reference) — never by
    # accepting a postcondition value from a caller (see
    # `app.control_plane.verification`). Making it required here, at the
    # single point PlanStepCreated is ever durably appended, is what
    # makes that reference-only retrieval possible: there is no PlanStep
    # without a pinned postcondition to pin it to.
    _require(payload, "plan_event_id", "description", "postcondition", event_type=PLAN_STEP_CREATED)
    if not isinstance(payload["postcondition"], str) or not payload["postcondition"].strip():
        raise InvalidEventPayloadError(
            f"{PLAN_STEP_CREATED}.postcondition must be a non-empty str."
        )
    # Phase 6, ES §11: "Invalidation MUST propagate only to dependent
    # PlanSteps in the DAG, not to the whole Plan by default." This is
    # the minimum dependency-edge representation that makes that MUST
    # satisfiable at all — NOT a full DAG execution model (no
    # scheduling, no parallel/conditional branches; see
    # `app.engineering_state.materialize.transitively_dependent_plan_steps`
    # for the one thing this field exists to support). OPTIONAL — most
    # PlanSteps depend on nothing; defaults to empty, mirroring
    # `BeliefRecorded.evidence_ids`'s own "list, may be empty" shape.
    depends_on = payload.get("depends_on")
    if depends_on is not None and not isinstance(depends_on, list):
        raise InvalidEventPayloadError(
            f"{PLAN_STEP_CREATED}.depends_on, when present, must be a list."
        )


def validate_plan_step_invalidated(payload: dict[str, Any]) -> None:
    # ES §10: "Contradiction... MUST trigger Belief revision, dependent
    # PlanStep invalidation, and Replan. This is the ONLY classification
    # that may trigger Replan." `contradiction_observation_event_id` is
    # what makes this a genuinely EVIDENCED fact, not an arbitrary claim
    # — the repository (not this I/O-free module) additionally verifies
    # it references a real `ObservationRecorded` event, in the same task,
    # whose `classification` is literally `"contradiction"`, closing the
    # same class of gap the Phase 1 causal-order correction closed for
    # every other event type.
    _require(
        payload,
        "plan_step_event_id",
        "contradiction_observation_event_id",
        "reason",
        event_type=PLAN_STEP_INVALIDATED,
    )
    if not isinstance(payload["reason"], str) or not payload["reason"].strip():
        raise InvalidEventPayloadError(f"{PLAN_STEP_INVALIDATED}.reason must be a non-empty str.")


def validate_decision_made(payload: dict[str, Any]) -> None:
    # ES §12: "selected option, alternatives considered (with rejection
    # reasons)... decision maker" — the shape that makes this a Decision
    # record rather than a bare status change.
    _require(
        payload,
        "selected_option",
        "alternatives_considered",
        "decision_maker",
        event_type=DECISION_MADE,
    )
    if not isinstance(payload["alternatives_considered"], list):
        raise InvalidEventPayloadError(f"{DECISION_MADE}.alternatives_considered must be a list.")


def validate_evidence_recorded(payload: dict[str, Any]) -> None:
    # ES §4: provenance, origin_class, source_trust are mandatory on
    # every Evidence item.
    _require(
        payload,
        "reference",
        "summary",
        "origin_class",
        "source_trust",
        "capability",
        event_type=EVIDENCE_RECORDED,
    )
    if payload["origin_class"] not in {"world_fact", "human_directive", "repository_content"}:
        raise InvalidEventPayloadError(
            f"{EVIDENCE_RECORDED}.origin_class must be one of "
            "world_fact/human_directive/repository_content (ES §4)."
        )


def validate_belief_recorded(payload: dict[str, Any]) -> None:
    # ES §6: every one of these fields, together, always — never
    # confidence alone.
    _require(
        payload,
        "proposition",
        "confidence",
        "uncertainty",
        "evidence_sufficiency",
        "qualitative_status",
        "derivation_method",
        "evidence_ids",
        event_type=BELIEF_RECORDED,
    )
    if not 0.0 <= payload["confidence"] <= 1.0:
        raise InvalidEventPayloadError(f"{BELIEF_RECORDED}.confidence must be in [0.0, 1.0].")
    if payload["evidence_sufficiency"] not in {"none", "sparse", "adequate", "strong"}:
        raise InvalidEventPayloadError(
            f"{BELIEF_RECORDED}.evidence_sufficiency must be one of "
            "none/sparse/adequate/strong (ES §6)."
        )
    if payload["qualitative_status"] not in {
        "speculative",
        "corroborated",
        "contested",
        "refuted",
        "verified",
    }:
        raise InvalidEventPayloadError(
            f"{BELIEF_RECORDED}.qualitative_status must be one of "
            "speculative/corroborated/contested/refuted/verified (ES §6)."
        )
    if not isinstance(payload["evidence_ids"], list):
        raise InvalidEventPayloadError(f"{BELIEF_RECORDED}.evidence_ids must be a list.")


_OBSERVATION_OUTCOMES: frozenset[str] = frozenset({"completed", "outcome_unknown"})

# Cap §16.1's five-way vocabulary, minus `blocked` — a Blocked
# Observation never reaches this event type at all (an authorization
# failure never reaches Tool dispatch; it is durably recorded as
# `AuthorizationDenied` instead, see
# `app.control_plane.observation_classification`'s own module docstring).
_OBSERVATION_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"expected", "anomaly", "uncertain_outcome", "contradiction"}
)


def validate_observation_recorded(payload: dict[str, Any]) -> None:
    # `raw_result`/`capability` remain the only REQUIRED fields — Phase 1's
    # original shape, unchanged. `run_coordinator.py`'s existing,
    # independent producer (Phase 0/1) supplies neither `outcome` nor
    # `classification` today; requiring either here would break that real,
    # already-durable producer. Phase 5 adds two OPTIONAL fields instead:
    #
    # `outcome` ("completed" | "outcome_unknown") — Cap §8 state-ladder
    # determinacy, distinct from `success` (Tool-level result, Phase 1/3)
    # and from `classification` (Engineering interpretation, Cap §16.2)
    # — never conflated.
    #
    # `classification` — Cap §16.2's five-way vocabulary, minus
    # `blocked` (see `_OBSERVATION_CLASSIFICATIONS` above). Absent/None
    # means "not yet classified" (e.g. `outcome_unknown` halts evaluation
    # before classification per Cap §16.2 step 2 — see this repo's Phase
    # 5 design audit §8, "ActionOutcomeUnknown is NOT uncertain_outcome").
    _require(payload, "raw_result", "capability", event_type=OBSERVATION_RECORDED)
    outcome = payload.get("outcome")
    if outcome is not None and outcome not in _OBSERVATION_OUTCOMES:
        raise InvalidEventPayloadError(
            f"{OBSERVATION_RECORDED}.outcome must be one of {sorted(_OBSERVATION_OUTCOMES)} "
            "or absent/None."
        )
    classification = payload.get("classification")
    if classification is not None and classification not in _OBSERVATION_CLASSIFICATIONS:
        raise InvalidEventPayloadError(
            f"{OBSERVATION_RECORDED}.classification must be one of "
            f"{sorted(_OBSERVATION_CLASSIFICATIONS)} or absent/None."
        )


def validate_authorization_granted(payload: dict[str, Any]) -> None:
    # Cap §7.1: everything needed to answer "why was this exact Action
    # authorized at 14:32" from the event alone.
    _require(
        payload,
        "grant_id",
        "action_id",
        "capability_id",
        "capability_version",
        "policy_version_id",
        "scope",
        "safety_validity_result",
        "novelty",
        "issued_at",
        "ttl_seconds",
        event_type=AUTHORIZATION_GRANTED,
    )
    if payload["novelty"] not in {"known", "novel"}:
        raise InvalidEventPayloadError(
            f"{AUTHORIZATION_GRANTED}.novelty must be 'known' or 'novel'."
        )
    if not isinstance(payload["ttl_seconds"], int) or payload["ttl_seconds"] <= 0:
        raise InvalidEventPayloadError(
            f"{AUTHORIZATION_GRANTED}.ttl_seconds must be a positive int."
        )


def validate_authorization_consuming(payload: dict[str, Any]) -> None:
    _require(payload, "grant_event_id", "action_id", event_type=AUTHORIZATION_CONSUMING)


def validate_authorization_consumed(payload: dict[str, Any]) -> None:
    # "started", never "succeeded"/"failed" — Cap inv.: authorization is
    # never evidence of what the Tool actually did. That determination is
    # ObservationRecorded's job, a separate event this one never asserts.
    _require(
        payload,
        "grant_event_id",
        "consuming_event_id",
        "action_id",
        "tool_id",
        event_type=AUTHORIZATION_CONSUMED,
    )


# Cap §6: "Denied at any point -> routed to the SPECIFIC failing reason
# ... NEVER a generic failure." Mirrors
# `app.control_plane.model.DenialStage`'s values exactly — this module
# cannot import that one (control_plane depends on engineering_state, not
# the reverse; a cross-import would be a layering violation), so the
# closed vocabulary is duplicated here deliberately, as this module's own
# independent source of truth for what a durably-recorded denial reason
# is allowed to say. Keep the two in sync by hand; a mismatch would be
# caught immediately by either side's own tests failing.
_DENIAL_STAGES: frozenset[str] = frozenset(
    {
        "capability_gap",
        "scope_violation",
        "prediction_inadmissible",
        "policy_denial",
        "constraint_violation",
        "budget_exhausted",
        "stale_safety_validity",
        "lease_conflict",
        "awaiting_human",
        "artifact_identity_mismatch",
        "precondition_invalidated_by_prior_step",
        "malformed_proposal",
    }
)


def validate_authorization_denied(payload: dict[str, Any]) -> None:
    _require(payload, "denial_stage", "reason", event_type=AUTHORIZATION_DENIED)
    if payload["denial_stage"] not in _DENIAL_STAGES:
        raise InvalidEventPayloadError(
            f"{AUTHORIZATION_DENIED}.denial_stage must be one of {sorted(_DENIAL_STAGES)}."
        )


def validate_authorization_invalidated(payload: dict[str, Any]) -> None:
    _require(payload, "grant_event_id", "action_id", "reason", event_type=AUTHORIZATION_INVALIDATED)


def validate_workspace_created(payload: dict[str, Any]) -> None:
    # Cap §19: "A Workspace has an identity, a bound Execution Context,
    # and a bounded, renewable lease." `workspace_id` is the business
    # identifier (distinct from this event's own database id, exactly as
    # `AuthorizationGrant.grant_id` is distinct from `granted_event_id`
    # in Phase 3). `physical_location`/`repository_url` are operational
    # metadata, never credentials (Phase 4 design: credentials are never
    # persisted anywhere in Engineering State).
    _require(
        payload,
        "workspace_id",
        "task_id",
        "actor",
        "user_id",
        "execution_context",
        "physical_location",
        "repository_url",
        "created_at",
        "max_lifetime_seconds",
        "initial_expires_at",
        event_type=WORKSPACE_CREATED,
    )
    if not isinstance(payload["max_lifetime_seconds"], int) or payload["max_lifetime_seconds"] <= 0:
        raise InvalidEventPayloadError(
            f"{WORKSPACE_CREATED}.max_lifetime_seconds must be a positive int."
        )
    if not isinstance(payload["execution_context"], dict):
        raise InvalidEventPayloadError(f"{WORKSPACE_CREATED}.execution_context must be a dict.")


def validate_workspace_lease_renewed(payload: dict[str, Any]) -> None:
    _require(
        payload,
        "workspace_event_id",
        "new_expires_at",
        "renewal_count",
        event_type=WORKSPACE_LEASE_RENEWED,
    )
    if not isinstance(payload["renewal_count"], int) or payload["renewal_count"] < 1:
        raise InvalidEventPayloadError(
            f"{WORKSPACE_LEASE_RENEWED}.renewal_count must be a positive int."
        )


def validate_workspace_diagnostic_hold_entered(payload: dict[str, Any]) -> None:
    _require(
        payload,
        "workspace_event_id",
        "reason",
        "hold_expires_at",
        event_type=WORKSPACE_DIAGNOSTIC_HOLD_ENTERED,
    )


def validate_workspace_write_authorization_revoked(payload: dict[str, Any]) -> None:
    _require(
        payload,
        "workspace_event_id",
        "reason",
        event_type=WORKSPACE_WRITE_AUTHORIZATION_REVOKED,
    )


# Mirrors `AuthorizationDenied.denial_stage`'s existing precedent — one
# event type, one closed discriminating field — rather than five separate
# WorkspaceDestroyed-shaped event types. Duplicated here (not imported)
# for the same layering reason `_DENIAL_STAGES` is duplicated rather than
# imported from `app.control_plane.model.DenialStage`: this module must
# not depend on `app.control_plane`.
_WORKSPACE_DESTRUCTION_REASONS: frozenset[str] = frozenset(
    {
        "completed_success",
        "diagnostic_hold_expired",
        "lease_expired_reclaimed",
        "custodial",
        "credential_incident",
        "creation_failed",
    }
)


def validate_workspace_destroyed(payload: dict[str, Any]) -> None:
    _require(payload, "workspace_event_id", "reason", event_type=WORKSPACE_DESTROYED)
    if payload["reason"] not in _WORKSPACE_DESTRUCTION_REASONS:
        raise InvalidEventPayloadError(
            f"{WORKSPACE_DESTROYED}.reason must be one of {sorted(_WORKSPACE_DESTRUCTION_REASONS)}."
        )


_VALIDATORS: dict[str, Any] = {
    GOAL_CREATED: validate_goal_created,
    GOAL_UPDATED: validate_goal_updated,
    PLAN_CREATED: validate_plan_created,
    PLAN_STEP_CREATED: validate_plan_step_created,
    PLAN_STEP_INVALIDATED: validate_plan_step_invalidated,
    DECISION_MADE: validate_decision_made,
    EVIDENCE_RECORDED: validate_evidence_recorded,
    BELIEF_RECORDED: validate_belief_recorded,
    OBSERVATION_RECORDED: validate_observation_recorded,
    AUTHORIZATION_GRANTED: validate_authorization_granted,
    AUTHORIZATION_CONSUMING: validate_authorization_consuming,
    AUTHORIZATION_CONSUMED: validate_authorization_consumed,
    AUTHORIZATION_DENIED: validate_authorization_denied,
    AUTHORIZATION_INVALIDATED: validate_authorization_invalidated,
    WORKSPACE_CREATED: validate_workspace_created,
    WORKSPACE_LEASE_RENEWED: validate_workspace_lease_renewed,
    WORKSPACE_DIAGNOSTIC_HOLD_ENTERED: validate_workspace_diagnostic_hold_entered,
    WORKSPACE_WRITE_AUTHORIZATION_REVOKED: validate_workspace_write_authorization_revoked,
    WORKSPACE_DESTROYED: validate_workspace_destroyed,
}


def validate_payload(event_type: str, payload: dict[str, Any]) -> None:
    """Dispatch to the event type's own shape validator.

    Raises `InvalidEventPayloadError` for an unrecognized `event_type` too
    — the repository must never insert a payload for a type this module
    doesn't know, even though the database CHECK constraint would also
    catch it; failing here is cheaper and gives a clearer message.
    """
    validator = _VALIDATORS.get(event_type)
    if validator is None:
        raise InvalidEventPayloadError(
            f"Unrecognized event_type {event_type!r}. Valid types: {sorted(EVENT_TYPES)}."
        )
    validator(payload)


EventType = Literal[
    "GoalCreated",
    "GoalUpdated",
    "PlanCreated",
    "PlanStepCreated",
    "PlanStepInvalidated",
    "DecisionMade",
    "EvidenceRecorded",
    "BeliefRecorded",
    "ObservationRecorded",
    "AuthorizationGranted",
    "AuthorizationConsuming",
    "AuthorizationConsumed",
    "AuthorizationDenied",
    "AuthorizationInvalidated",
    "WorkspaceCreated",
    "WorkspaceLeaseRenewed",
    "WorkspaceDiagnosticHoldEntered",
    "WorkspaceWriteAuthorizationRevoked",
    "WorkspaceDestroyed",
]

__all__ = [
    "AUTHORIZATION_CONSUMED",
    "AUTHORIZATION_CONSUMING",
    "AUTHORIZATION_DENIED",
    "AUTHORIZATION_GRANTED",
    "AUTHORIZATION_INVALIDATED",
    "BELIEF_RECORDED",
    "CAUSAL_PAYLOAD_REFERENCE_FIELD",
    "CAUSAL_REQUIREMENTS",
    "DECISION_MADE",
    "EVENT_TYPES",
    "EVIDENCE_RECORDED",
    "GOAL_CREATED",
    "GOAL_UPDATED",
    "OBSERVATION_RECORDED",
    "PLAN_CREATED",
    "PLAN_STEP_CREATED",
    "PLAN_STEP_INVALIDATED",
    "WORKSPACE_CREATED",
    "WORKSPACE_DESTROYED",
    "WORKSPACE_DIAGNOSTIC_HOLD_ENTERED",
    "WORKSPACE_LEASE_RENEWED",
    "WORKSPACE_WRITE_AUTHORIZATION_REVOKED",
    "EventType",
    "InvalidEventPayloadError",
    "validate_payload",
]


def new_event_id() -> uuid.UUID:
    """Thin wrapper kept only so callers never need to `import uuid`
    solely to make an event id — matches this module being the one place
    Phase 1 code goes to construct event-shaped things."""
    return uuid.uuid4()
