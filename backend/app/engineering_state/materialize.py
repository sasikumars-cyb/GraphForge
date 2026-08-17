"""Deterministic event -> materialized-state folding.

`docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md` §9: "The materialized
Engineering State at any historical point in time MUST be exactly
reconstructable by folding the event log up to that point (State replay,
§9, unconditional)." §16/inv. 20: "Nothing outside the durable event log
may be treated as authoritative."

`fold()` is the whole implementation of that guarantee for Phase 1: a
pure function from an ordered sequence of `EngineeringEvent` rows to a
`MaterializedEngineeringState`. It performs no I/O, reads no clock,
consults no LLM, and reads no mutable legacy status field — its only
input is the event list itself, so the same *ordered* event history
always folds to the same state (verified directly by
`tests/unit/engineering_state/test_materialize.py`, which requires no
database).

**Precise claim, corrected by the Phase 1 Final Correctness Audit — read
carefully, this is not a restatement of the obvious:** `fold()` derives
the canonical processing order from each event's `sequence_number`,
never from the order the caller happened to iterate its input list in.
That is the *entire* content of the "order independence" this module
used to (incorrectly) claim. It does **not** mean, and MUST NOT be
described as meaning, that an arbitrary permutation of a task's actual
causal history — i.e. different `sequence_number` assignments — produces
the same state. `sequence_number` is semantic (ENGINEERING_STATE_
ARCHITECTURE.md §8's "single, total, append-determined order"), and
swapping it for two causally-related events changes the result: a
`GoalUpdated` folded before its `GoalCreated` finds no goal to update and
is silently absorbed into nothing, which is exactly why causal validity
is now enforced upstream, at `EngineeringEventRepository.append()`
(`CausalOrderViolationError`) — before a malformed order can become
durable — rather than here, after the fact. `fold()` itself performs no
causal validation and trusts that the history it's given is already
valid, precisely because the repository is now the sole entry point that
can make it so.

This is deliberately *not* a persisted "current state" table. ES §16
requires the active materialized projection to be scoped to
currently-open items and re-derived, not cached-and-drifted — Phase 1's
correct minimum is to make that re-derivation cheap and correct, not to
build a second store that could itself go stale relative to the log.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.engineering_state.events import (
    AUTHORIZATION_CONSUMED,
    AUTHORIZATION_CONSUMING,
    AUTHORIZATION_DENIED,
    AUTHORIZATION_GRANTED,
    AUTHORIZATION_INVALIDATED,
    BELIEF_RECORDED,
    DECISION_MADE,
    EVIDENCE_RECORDED,
    GOAL_CREATED,
    GOAL_UPDATED,
    OBSERVATION_RECORDED,
    PLAN_CREATED,
    PLAN_STEP_CREATED,
    PLAN_STEP_INVALIDATED,
    WORKSPACE_CREATED,
    WORKSPACE_DESTROYED,
    WORKSPACE_DIAGNOSTIC_HOLD_ENTERED,
    WORKSPACE_LEASE_RENEWED,
    WORKSPACE_WRITE_AUTHORIZATION_REVOKED,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.models.engineering_event import EngineeringEvent


@dataclass(frozen=True)
class GoalRecord:
    event_id: uuid.UUID
    description: str
    postconditions: tuple[str, ...]


@dataclass(frozen=True)
class PlanRecord:
    event_id: uuid.UUID
    goal_event_id: uuid.UUID
    scope: Any
    # Phase 6, ES §11: a durable reference to the specific prior Plan
    # this one supersedes, when this isn't the first Plan for its Goal.
    # `None` for a base Plan. Deliberately NOT a causal parent (that
    # remains `goal_event_id` — a Plan's cause is always its Goal) and
    # deliberately NOT paired with any mutable "authoritative" flag —
    # which Plan is currently eligible is a DERIVED fact, computed by
    # `superseded_plan_event_ids()` below, never stored here.
    supersedes_plan_event_id: uuid.UUID | None = None


@dataclass(frozen=True)
class PlanStepRecord:
    event_id: uuid.UUID
    plan_event_id: uuid.UUID
    description: str
    # Cap §15.1: the pinned, approval-time postcondition — the ONLY
    # value `app.control_plane.verification.VerificationService` ever
    # evaluates against, resolved by `event_id` reference, never
    # supplied by a caller (see `events.validate_plan_step_created`).
    postcondition: str
    # Phase 6, ES §11: the minimum dependency-edge representation —
    # other PlanStepCreated event ids this one depends on. Empty tuple
    # for a PlanStep with no dependencies (the common case). See
    # `transitively_dependent_plan_steps()` below for the one thing
    # this field exists to support.
    depends_on: tuple[uuid.UUID, ...] = ()
    # Phase 6, ES §10/§11: overlaid by a later PlanStepInvalidated event
    # exactly like `AuthorizationGrantRecord.state`/`WorkspaceRecord.
    # state` are overlaid by their own later lifecycle events — never
    # set at PlanStepCreated time (a PlanStep cannot be born invalidated).
    invalidated: bool = False
    invalidation_reason: str | None = None
    invalidating_observation_event_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DecisionRecord:
    event_id: uuid.UUID
    selected_option: Any
    alternatives_considered: tuple[Any, ...]
    decision_maker: str


@dataclass(frozen=True)
class EvidenceRecord:
    event_id: uuid.UUID
    reference: str
    summary: str
    origin_class: str
    source_trust: Any
    capability: str


@dataclass(frozen=True)
class BeliefRecord:
    event_id: uuid.UUID
    proposition: str
    confidence: float
    uncertainty: float
    evidence_sufficiency: str
    qualitative_status: str
    derivation_method: str
    evidence_ids: tuple[Any, ...]


@dataclass(frozen=True)
class ObservationRecord:
    event_id: uuid.UUID
    raw_result: Any
    capability: str
    # Phase 5, all optional/backward-compatible — `None` for every event
    # a pre-Phase-5 producer (e.g. `app.orchestrator.run_coordinator`)
    # already durably wrote, which supplies neither field.
    outcome: str | None = None  # "completed" | "outcome_unknown"
    classification: str | None = None  # Cap §16.2's five-way vocabulary, minus "blocked"
    # The business actor that performed this Observation's underlying
    # Action — distinct from `EngineeringEvent.actor` (always the
    # writer, "control_plane"). Cap §15.2: how a verifier's Observation
    # is distinguished from a generator's. `None` when the producer
    # didn't supply one (every pre-Phase-5 producer).
    actor: str | None = None
    # The PlanStep this Observation's Action belongs to (`Action.
    # plan_step_id`, already an existing field — not new). `None` when
    # absent from the payload.
    plan_step_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AuthorizationGrantRecord:
    """Phase 3: the reconstructed current state of one Authorization
    Grant, folded from its `AuthorizationGranted` event plus whichever of
    `AuthorizationConsuming`/`AuthorizationConsumed`/
    `AuthorizationInvalidated` followed it (Cap §7.1 — the Control Plane
    is the sole author, Engineering State the sole durable home; this is
    that durable home's read side). `grant_event_id` is the
    `AuthorizationGranted` event's own id — the same identifier every
    later event in this Grant's lifecycle references via
    `causation_event_id`, per `app.control_plane.control_plane`'s own
    convention of never using a separately-generated id for this
    purpose."""

    grant_event_id: uuid.UUID
    action_id: uuid.UUID
    capability_id: str
    capability_version: int
    policy_version_id: str
    scope: str
    safety_validity_result: Any
    novelty: str
    issued_at: str
    ttl_seconds: int
    human_approval_id: Any
    state: str  # "granted" | "consuming" | "consumed" | "invalidated"


@dataclass(frozen=True)
class AuthorizationDenialRecord:
    """A denial has no Grant to attach to — it durably records that one
    was never issued, and why (Cap §7.1: "Denials MUST be recorded.").
    """

    event_id: uuid.UUID
    denial_stage: str
    reason: str
    action_id: Any
    capability_id: Any


@dataclass(frozen=True)
class WorkspaceRecord:
    """Phase 4: the reconstructed current state of one Workspace, folded
    from its `WorkspaceCreated` event plus whichever of
    `WorkspaceLeaseRenewed`/`WorkspaceDiagnosticHoldEntered`/
    `WorkspaceWriteAuthorizationRevoked`/`WorkspaceDestroyed` followed it
    (Cap §19). `workspace_event_id` is the `WorkspaceCreated` event's own
    id — the same identifier every later lifecycle event references via
    `causation_event_id`, mirroring `AuthorizationGrantRecord`'s
    identical convention.

    Deliberately reports only what was durably recorded — whether the
    lease is CURRENTLY expired is not decided here (that needs `now`,
    which this module, like the rest of `fold()`, must never read
    implicitly); see `is_reclaimable()` below.
    """

    workspace_event_id: uuid.UUID
    task_id: uuid.UUID
    actor: str
    user_id: uuid.UUID
    execution_context: Any
    physical_location: str
    repository_url: Any
    created_at: str
    max_lifetime_seconds: int
    lease_expires_at: str
    renewal_count: int
    state: str  # "leased" | "diagnostic_hold" | "write_authorization_revoked" | "destroyed"
    diagnostic_hold_expires_at: str | None
    diagnostic_hold_reason: str | None
    destruction_reason: str | None


@dataclass(frozen=True)
class MaterializedEngineeringState:
    """The fold's output — everything a Phase 1 reader can know about one
    task, derived from its event history alone."""

    task_id: uuid.UUID
    goal: GoalRecord | None = None
    plans: tuple[PlanRecord, ...] = field(default_factory=tuple)
    plan_steps: tuple[PlanStepRecord, ...] = field(default_factory=tuple)
    decisions: tuple[DecisionRecord, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceRecord, ...] = field(default_factory=tuple)
    beliefs: tuple[BeliefRecord, ...] = field(default_factory=tuple)
    observations: tuple[ObservationRecord, ...] = field(default_factory=tuple)
    authorization_grants: tuple[AuthorizationGrantRecord, ...] = field(default_factory=tuple)
    authorization_denials: tuple[AuthorizationDenialRecord, ...] = field(default_factory=tuple)
    workspaces: tuple[WorkspaceRecord, ...] = field(default_factory=tuple)
    event_count: int = 0


class MixedTaskEventsError(ValueError):
    """Raised if `fold()` is handed events from more than one task —
    every event belongs to exactly one task's stream (ES §8); folding
    across tasks would silently produce a meaningless composite state."""


def fold(events: Sequence[EngineeringEvent]) -> MaterializedEngineeringState:
    """Reconstruct the current Engineering State for one task from its
    complete event history.

    Robust to the *caller's* list order, never to the events' own causal
    order: `events` is sorted by `sequence_number` before folding, so
    handing this function the same events in a different Python list
    order (e.g. `fold(list(events))` vs. `fold(list(reversed(events)))`)
    produces identical output, because both calls process the identical
    `sequence_number` order internally — proven directly by
    `tests/unit/engineering_state/test_materialize.py::
    test_fold_derives_canonical_order_from_sequence_number_not_list_order`.
    This is NOT a claim that reordering the events' actual
    `sequence_number` values — i.e. a different causal history — would
    produce the same result; see this module's own top docstring for why
    that claim would be wrong, and for where causal validity is actually
    enforced (append time, not here).
    """
    if not events:
        raise ValueError("fold() requires at least one event (task_id is unknown otherwise).")

    task_ids = {e.task_id for e in events}
    if len(task_ids) > 1:
        raise MixedTaskEventsError(
            f"fold() received events from {len(task_ids)} different tasks: {task_ids}. "
            "Fetch one task's events (e.g. via "
            "EngineeringEventRepository.list_for_task) before folding."
        )
    task_id = task_ids.pop()

    ordered = sorted(events, key=lambda e: e.sequence_number)

    goal: GoalRecord | None = None
    plans: list[PlanRecord] = []
    # Phase 6: keyed by the PlanStepCreated event's own id — the same id
    # PlanStepInvalidated causally references — so a later invalidation
    # OVERLAYS the record here rather than appending a second one,
    # mirroring `grants_by_event_id`/`workspaces_by_event_id`'s identical
    # pattern below.
    plan_steps_by_event_id: dict[uuid.UUID, PlanStepRecord] = {}
    decisions: list[DecisionRecord] = []
    evidence: list[EvidenceRecord] = []
    beliefs: list[BeliefRecord] = []
    observations: list[ObservationRecord] = []
    # Keyed by the AuthorizationGranted event's own id — the same id
    # Consuming/Consumed/Invalidated causally reference — so a later
    # lifecycle event REPLACES the record here rather than appending a
    # second one; `authorization_grants` in the final result reflects
    # only current state, one entry per Grant, matching this module's
    # "reconstructable current state," not a raw event dump.
    grants_by_event_id: dict[uuid.UUID, AuthorizationGrantRecord] = {}
    denials: list[AuthorizationDenialRecord] = []
    # Same "keyed by the base event's own id, later events REPLACE the
    # record" pattern as authorization_grants above.
    workspaces_by_event_id: dict[uuid.UUID, WorkspaceRecord] = {}

    for event in ordered:
        payload = event.payload
        if event.event_type == GOAL_CREATED:
            goal = GoalRecord(
                event_id=event.id,
                description=payload["description"],
                postconditions=tuple(payload["postconditions"]),
            )
        elif event.event_type == GOAL_UPDATED:
            # Overlay only the fields this update actually carries, onto
            # whatever the goal currently is — a partial update, never a
            # replacement of fields it didn't mention. If no GoalCreated
            # preceded this (a malformed history), there is nothing to
            # overlay onto; leave `goal` as None rather than fabricate one.
            if goal is not None:
                goal = GoalRecord(
                    event_id=event.id,
                    description=payload.get("description", goal.description),
                    postconditions=tuple(payload.get("postconditions", goal.postconditions)),
                )
        elif event.event_type == PLAN_CREATED:
            supersedes_raw = payload.get("supersedes_plan_event_id")
            plans.append(
                PlanRecord(
                    event_id=event.id,
                    goal_event_id=payload["goal_event_id"],
                    scope=payload["scope"],
                    supersedes_plan_event_id=(
                        uuid.UUID(supersedes_raw) if supersedes_raw is not None else None
                    ),
                )
            )
        elif event.event_type == PLAN_STEP_CREATED:
            depends_on_raw = payload.get("depends_on") or []
            plan_steps_by_event_id[event.id] = PlanStepRecord(
                event_id=event.id,
                plan_event_id=payload["plan_event_id"],
                description=payload["description"],
                postcondition=payload["postcondition"],
                depends_on=tuple(uuid.UUID(str(d)) for d in depends_on_raw),
            )
        elif event.event_type == PLAN_STEP_INVALIDATED:
            plan_step_event_id = uuid.UUID(str(payload["plan_step_event_id"]))
            existing_step = plan_steps_by_event_id.get(plan_step_event_id)
            if existing_step is not None:
                plan_steps_by_event_id[plan_step_event_id] = dataclasses.replace(
                    existing_step,
                    invalidated=True,
                    invalidation_reason=payload["reason"],
                    invalidating_observation_event_id=uuid.UUID(
                        str(payload["contradiction_observation_event_id"])
                    ),
                )
        elif event.event_type == DECISION_MADE:
            decisions.append(
                DecisionRecord(
                    event_id=event.id,
                    selected_option=payload["selected_option"],
                    alternatives_considered=tuple(payload["alternatives_considered"]),
                    decision_maker=payload["decision_maker"],
                )
            )
        elif event.event_type == EVIDENCE_RECORDED:
            evidence.append(
                EvidenceRecord(
                    event_id=event.id,
                    reference=payload["reference"],
                    summary=payload["summary"],
                    origin_class=payload["origin_class"],
                    source_trust=payload["source_trust"],
                    capability=payload["capability"],
                )
            )
        elif event.event_type == BELIEF_RECORDED:
            beliefs.append(
                BeliefRecord(
                    event_id=event.id,
                    proposition=payload["proposition"],
                    confidence=payload["confidence"],
                    uncertainty=payload["uncertainty"],
                    evidence_sufficiency=payload["evidence_sufficiency"],
                    qualitative_status=payload["qualitative_status"],
                    derivation_method=payload["derivation_method"],
                    evidence_ids=tuple(payload["evidence_ids"]),
                )
            )
        elif event.event_type == OBSERVATION_RECORDED:
            plan_step_id_raw = payload.get("plan_step_id")
            observations.append(
                ObservationRecord(
                    event_id=event.id,
                    raw_result=payload["raw_result"],
                    capability=payload["capability"],
                    outcome=payload.get("outcome"),
                    classification=payload.get("classification"),
                    actor=payload.get("actor"),
                    plan_step_id=(
                        uuid.UUID(plan_step_id_raw) if plan_step_id_raw is not None else None
                    ),
                )
            )
        elif event.event_type == AUTHORIZATION_GRANTED:
            grants_by_event_id[event.id] = AuthorizationGrantRecord(
                grant_event_id=event.id,
                action_id=uuid.UUID(payload["action_id"]),
                capability_id=payload["capability_id"],
                capability_version=payload["capability_version"],
                policy_version_id=payload["policy_version_id"],
                scope=payload["scope"],
                safety_validity_result=payload["safety_validity_result"],
                novelty=payload["novelty"],
                issued_at=payload["issued_at"],
                ttl_seconds=payload["ttl_seconds"],
                human_approval_id=payload.get("human_approval_id"),
                state="granted",
            )
        elif event.event_type == AUTHORIZATION_CONSUMING:
            grant_event_id = uuid.UUID(payload["grant_event_id"])
            existing = grants_by_event_id.get(grant_event_id)
            if existing is not None:
                grants_by_event_id[grant_event_id] = _with_state(existing, "consuming")
        elif event.event_type == AUTHORIZATION_CONSUMED:
            grant_event_id = uuid.UUID(payload["grant_event_id"])
            existing = grants_by_event_id.get(grant_event_id)
            if existing is not None:
                grants_by_event_id[grant_event_id] = _with_state(existing, "consumed")
        elif event.event_type == AUTHORIZATION_INVALIDATED:
            grant_event_id = uuid.UUID(payload["grant_event_id"])
            existing = grants_by_event_id.get(grant_event_id)
            if existing is not None:
                grants_by_event_id[grant_event_id] = _with_state(existing, "invalidated")
        elif event.event_type == AUTHORIZATION_DENIED:
            denials.append(
                AuthorizationDenialRecord(
                    event_id=event.id,
                    denial_stage=payload["denial_stage"],
                    reason=payload["reason"],
                    action_id=payload.get("action_id"),
                    capability_id=payload.get("capability_id"),
                )
            )
        elif event.event_type == WORKSPACE_CREATED:
            workspaces_by_event_id[event.id] = WorkspaceRecord(
                workspace_event_id=event.id,
                task_id=uuid.UUID(payload["task_id"]),
                actor=payload["actor"],
                user_id=uuid.UUID(payload["user_id"]),
                execution_context=payload["execution_context"],
                physical_location=payload["physical_location"],
                repository_url=payload.get("repository_url"),
                created_at=payload["created_at"],
                max_lifetime_seconds=payload["max_lifetime_seconds"],
                lease_expires_at=payload["initial_expires_at"],
                renewal_count=0,
                state="leased",
                diagnostic_hold_expires_at=None,
                diagnostic_hold_reason=None,
                destruction_reason=None,
            )
        elif event.event_type == WORKSPACE_LEASE_RENEWED:
            workspace_event_id = uuid.UUID(payload["workspace_event_id"])
            existing_ws = workspaces_by_event_id.get(workspace_event_id)
            if existing_ws is not None:
                workspaces_by_event_id[workspace_event_id] = _with_workspace_fields(
                    existing_ws,
                    lease_expires_at=payload["new_expires_at"],
                    renewal_count=payload["renewal_count"],
                )
        elif event.event_type == WORKSPACE_DIAGNOSTIC_HOLD_ENTERED:
            workspace_event_id = uuid.UUID(payload["workspace_event_id"])
            existing_ws = workspaces_by_event_id.get(workspace_event_id)
            if existing_ws is not None:
                workspaces_by_event_id[workspace_event_id] = _with_workspace_fields(
                    existing_ws,
                    state="diagnostic_hold",
                    diagnostic_hold_reason=payload["reason"],
                    diagnostic_hold_expires_at=payload["hold_expires_at"],
                )
        elif event.event_type == WORKSPACE_WRITE_AUTHORIZATION_REVOKED:
            workspace_event_id = uuid.UUID(payload["workspace_event_id"])
            existing_ws = workspaces_by_event_id.get(workspace_event_id)
            if existing_ws is not None:
                workspaces_by_event_id[workspace_event_id] = _with_workspace_fields(
                    existing_ws, state="write_authorization_revoked"
                )
        elif event.event_type == WORKSPACE_DESTROYED:
            workspace_event_id = uuid.UUID(payload["workspace_event_id"])
            existing_ws = workspaces_by_event_id.get(workspace_event_id)
            if existing_ws is not None:
                workspaces_by_event_id[workspace_event_id] = _with_workspace_fields(
                    existing_ws, state="destroyed", destruction_reason=payload["reason"]
                )
        # No `else` — an unrecognized event_type here would mean the DB
        # CHECK constraint and events.validate_payload() both already let
        # something through that this module doesn't know; that is a
        # defect to surface loudly elsewhere (repository/model tests),
        # not to paper over with a silent skip here.

    return MaterializedEngineeringState(
        task_id=task_id,
        goal=goal,
        plans=tuple(plans),
        plan_steps=tuple(plan_steps_by_event_id.values()),
        decisions=tuple(decisions),
        evidence=tuple(evidence),
        beliefs=tuple(beliefs),
        observations=tuple(observations),
        authorization_grants=tuple(grants_by_event_id.values()),
        authorization_denials=tuple(denials),
        workspaces=tuple(workspaces_by_event_id.values()),
        event_count=len(ordered),
    )


def _with_state(record: AuthorizationGrantRecord, state: str) -> AuthorizationGrantRecord:
    """Cap §2: no single mutable field represents the state ladder — this
    returns a NEW frozen record rather than mutating `record`, so every
    intermediate state, if a caller kept a reference to it, remains
    exactly what it was when observed."""
    return AuthorizationGrantRecord(
        grant_event_id=record.grant_event_id,
        action_id=record.action_id,
        capability_id=record.capability_id,
        capability_version=record.capability_version,
        policy_version_id=record.policy_version_id,
        scope=record.scope,
        safety_validity_result=record.safety_validity_result,
        novelty=record.novelty,
        issued_at=record.issued_at,
        ttl_seconds=record.ttl_seconds,
        human_approval_id=record.human_approval_id,
        state=state,
    )


def _with_workspace_fields(record: WorkspaceRecord, **overrides: Any) -> WorkspaceRecord:
    """Same discipline as `_with_state` above — a NEW frozen record, never
    a mutation, so an earlier observer's reference stays exactly what it
    was."""
    return dataclasses.replace(record, **overrides)


def is_reclaimable(record: WorkspaceRecord, *, now: datetime) -> bool:
    """Whether `record`'s lease is CURRENTLY expired and eligible for
    lease-TTL-expiry reclamation (Cap §19: "Crash -> Reclaimed by
    lease-TTL expiry via an orphan sweep"). Deliberately NOT part of
    `fold()` itself — `fold()` stays a pure function of the event list
    alone, exactly as this module's own top docstring requires ("performs
    no I/O, reads no clock"); this function takes `now` explicitly,
    mirroring `AuthorizationGrant.is_expired(now=...)`'s identical
    precedent from Phase 3.

    Only a `leased` Workspace is reclaimable this way — a Workspace under
    diagnostic hold is protected by its OWN, separate TTL (§19:
    "Expiry MUST NOT destroy a Workspace under an active hold"), and a
    `write_authorization_revoked` or already-`destroyed` Workspace is
    not "expired," it is something else entirely.
    """
    if record.state != "leased":
        return False
    lease_expires_at = datetime.fromisoformat(record.lease_expires_at)
    return now >= lease_expires_at


def is_hold_expired(record: WorkspaceRecord, *, now: datetime) -> bool:
    """Whether `record`'s diagnostic hold has run past its OWN TTL (Cap
    §19: "Retained under diagnostic hold with bounded TTL, then
    destroyed") — a distinct check from `is_reclaimable` above (a
    different trigger, lease-TTL vs. hold-TTL, per the contract's own
    two separate disposition-table rows), found necessary during the
    Phase 4 exit self-audit: the orphan sweep must reconcile BOTH
    expiry mechanisms toward destruction, not only lease expiry."""
    if record.state != "diagnostic_hold" or record.diagnostic_hold_expires_at is None:
        return False
    hold_expires_at = datetime.fromisoformat(record.diagnostic_hold_expires_at)
    return now >= hold_expires_at


def has_unresolved_outcome_unknown(state: MaterializedEngineeringState) -> bool:
    """Phase 5, Cap §16.2 step 2 / ES §9: whether ANY Observation in this
    task's history currently carries `outcome="outcome_unknown"` — a
    prerequisite state that MUST block dependent Actions until
    reconciled (Cap §18.3: reconciliation is a custodial capability of
    the owning Role, deliberately out of this phase's scope — see the
    Phase 5 design audit §12).

    **Named limitation, not a bug:** no mechanism to durably record
    "this outcome_unknown Observation has now been reconciled" exists
    anywhere in this codebase yet (no reconciliation Capability, no
    resolving event type) — so every `outcome_unknown` Observation ever
    recorded for this task is treated as still-unresolved, permanently,
    until a future phase adds a real resolution representation. This
    function must not be read as "checks whether it's CURRENTLY
    unresolved" in any richer sense than "at least one was ever
    recorded and nothing in this codebase can mark it otherwise."

    A caller (e.g. before invoking `ControlPlane.check_eligibility`)
    derives `preconditions_hold=not has_unresolved_outcome_unknown(state)`
    — this function itself performs no I/O, matching `is_reclaimable`/
    `is_hold_expired`'s identical "pure function of already-folded
    state" precedent above.
    """
    return any(obs.outcome == "outcome_unknown" for obs in state.observations)


def superseded_plan_event_ids(state: MaterializedEngineeringState) -> frozenset[uuid.UUID]:
    """Phase 6, ES §11: which Plan(s), among every `PlanCreated` event
    ever recorded for this task, have been superseded by a later Plan
    version — a PURELY DERIVED fact, computed fresh from
    `PlanRecord.supersedes_plan_event_id` every time, never a stored
    "authoritative" flag anywhere (see `PlanRecord`'s own docstring on
    exactly why). A Plan not in this set is currently eligible; nothing
    about "eligible" implies "approved" or "safe to execute under" —
    those remain separate, existing checks (Human Approval, Policy,
    Safety Validity), entirely unmodified by this function.

    Deliberately does NOT implement "latest Plan wins" — a Plan is
    superseded only if SOME other Plan's own `supersedes_plan_event_id`
    durably names it, never merely because it is not the most recently
    created.
    """
    return frozenset(
        plan.supersedes_plan_event_id
        for plan in state.plans
        if plan.supersedes_plan_event_id is not None
    )


def transitively_dependent_plan_steps(
    state: MaterializedEngineeringState, plan_step_event_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """Phase 6, ES §11: "Invalidation MUST propagate only to dependent
    PlanSteps in the DAG, not to the whole Plan by default." Given the
    PlanStep DIRECTLY falsified by a Contradiction, returns every OTHER
    PlanStep in `state.plan_steps` that depends on it — directly or
    transitively, via `PlanStepRecord.depends_on` edges — computed by a
    plain reverse-adjacency BFS over already-folded state. Does NOT
    include `plan_step_event_id` itself (the caller already knows that
    one is invalidated; this answers only "what else becomes invalidated
    as a consequence").

    This is the ENTIRE extent of "DAG" behavior Phase 6 implements — no
    scheduling, no parallel-branch execution, no conditional-branch
    evaluation, no nested-Plan traversal. It exists to support exactly
    one MUST: correct invalidation propagation, nothing broader.
    """
    # Reverse adjacency: for each PlanStep, which OTHER PlanSteps declare
    # it in their own `depends_on` (i.e., which PlanSteps depend ON it).
    dependents_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for step in state.plan_steps:
        for dependency_id in step.depends_on:
            dependents_of.setdefault(dependency_id, []).append(step.event_id)

    visited: set[uuid.UUID] = set()
    frontier = [plan_step_event_id]
    while frontier:
        current = frontier.pop()
        for dependent_id in dependents_of.get(current, ()):
            if dependent_id not in visited:
                visited.add(dependent_id)
                frontier.append(dependent_id)
    return frozenset(visited)


def is_plan_step_invalidated(
    state: MaterializedEngineeringState, plan_step_event_id: uuid.UUID
) -> bool:
    """Phase 6, ES §11: whether `plan_step_event_id` currently carries a
    durable `PlanStepInvalidated` overlay — the minimum derived helper
    needed to ensure an invalidated PlanStep cannot satisfy execution
    preconditions. Mirrors `has_unresolved_outcome_unknown`'s exact
    shape: a caller (e.g. before invoking `ControlPlane.check_eligibility`
    for an Action bound to this PlanStep) derives
    `preconditions_hold=not is_plan_step_invalidated(state, action.plan_step_id)`
    — combined with `has_unresolved_outcome_unknown` via `and`/`or` as
    the caller's own composition, never fused into one mega-helper here.
    """
    return any(
        step.event_id == plan_step_event_id and step.invalidated for step in state.plan_steps
    )
