"""`ControlPlane` — the real authorization boundary, Cap §5/§6.

The exact path this phase implements:

    Reasoning -> ActionProposal -> ControlPlane -> Conformance -> Capability
    -> Scope -> Policy -> Safety Validity -> Authorization -> Authorization
    Grant -> ToolExecutor -> Tool

Nothing outside this class may issue an `AuthorizationGrant` or append an
`Authorization*` Engineering Event — enforced structurally by this being
the only module that imports both `app.control_plane.grant` and
`app.repositories.engineering_event_repository` for that purpose (see
`tests/unit/architecture/test_control_plane_authorization_boundary.py`).

**Proposal Conformant and Action Eligible are NOT persisted as their own
Engineering Event types.** They are properties re-derivable at any time
from the ActionProposal/Action objects themselves plus the Capability
Registry and Policy — nothing about them is itself irreproducible state
that would be lost if not durably logged (unlike a Grant's issuance,
which records a specific, otherwise-unrecoverable authorization decision
made at a specific moment). When either check fails, the SPECIFIC
`DenialStage` is what matters for forensic reconstruction, and that is
captured on `AuthorizationDenied` events' `denial_stage` field instead of
inventing five additional never-independently-useful event types. This is
the resolution to the instructions' own §22 vs §6 tension, reasoned
through explicitly rather than silently — see the Phase 3 final report.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from app.capabilities.model import CapabilityVersion
from app.capabilities.registry import CapabilityRegistry
from app.control_plane.grant import (
    DEFAULT_GRANT_TTL_SECONDS,
    AuthorizationGrant,
    GrantState,
    hash_action_parameters,
)
from app.control_plane.human_approval import HumanApprovalRecord, is_approval_still_valid_for_scope
from app.control_plane.model import (
    Action,
    ActionProposal,
    ConformanceResult,
    DenialStage,
    EligibilityResult,
)
from app.control_plane.policy import PolicyStore
from app.control_plane.safety import evaluate_safety_validity
from app.engineering_state.events import (
    AUTHORIZATION_CONSUMED,
    AUTHORIZATION_CONSUMING,
    AUTHORIZATION_DENIED,
    AUTHORIZATION_GRANTED,
    AUTHORIZATION_INVALIDATED,
    OBSERVATION_RECORDED,
)
from app.repositories.engineering_event_repository import EngineeringEventRepository
from app.tools.executor import ToolExecutor
from app.tools.interfaces import ToolInput

logger = logging.getLogger(__name__)

_CONTROL_PLANE_ACTOR = "control_plane"


class CapabilityGapError(ValueError):
    """Cap §11: a distinct terminal outcome, never a Policy denial, never
    silently substituted or degraded. Raised, not returned as a
    `ConformanceResult`, because a gap is not "this proposal is
    non-conformant" — it is "no Capability exists to evaluate against,"
    a categorically different situation the caller must not confuse with
    a routine denial."""


@dataclass(frozen=True, slots=True)
class ActionExecutionResult:
    """Cap §8 states 4-6, collapsed into one return value for this
    phase's single-Capability scope: whether the Tool dispatched
    (`execution_started`, always True once this object exists — construction
    only happens after consumption), whether it reached a determinate
    outcome (`outcome`), and the id of the `ObservationRecorded` event
    that durably captured the raw result."""

    action_id: uuid.UUID
    grant_id: uuid.UUID
    outcome: str  # "completed" | "outcome_unknown"
    tool_success: bool | None
    observation_event_id: uuid.UUID


class ControlPlane:
    """One instance per request/task context — mirrors `ToolExecutor`'s
    own per-run construction convention. Holds no cross-request state of
    its own; every fact it needs (Grants, Policy, prior events) is either
    passed in or read fresh from `event_repository`."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry,
        tool_executor: ToolExecutor,
        policy_store: PolicyStore,
        event_repository: EngineeringEventRepository,
    ) -> None:
        self._capabilities = capability_registry
        self._tool_executor = tool_executor
        self._policy = policy_store
        self._events = event_repository

    # ------------------------------------------------------------------
    # Proposal-level: Conformance (Cap §6, first phase)
    # ------------------------------------------------------------------

    def check_conformance(self, proposal: ActionProposal) -> ConformanceResult:
        """Structural + Capability-coverage + scope + Prediction
        admissibility, evaluated once for the whole proposal. Cap §6:
        "necessary, never sufficient" — a conformant result grants
        nothing; `authorize_action` below never reads this result as
        authorization.
        """
        for action in proposal.actions:
            capability = self._capabilities.get(action.capability_id, action.capability_version)
            if capability is None:
                return ConformanceResult(
                    proposal_id=proposal.proposal_id,
                    conformant=False,
                    denial_stage=DenialStage.CAPABILITY_GAP,
                    denial_reason=(
                        f"No registered Capability {action.capability_id!r} "
                        f"v{action.capability_version} covers this Action."
                    ),
                )

            scope_result = self._check_scope(action, capability)
            if scope_result is not None:
                return ConformanceResult(
                    proposal_id=proposal.proposal_id,
                    conformant=False,
                    denial_stage=DenialStage.SCOPE_VIOLATION,
                    denial_reason=scope_result,
                )

            prediction_result = self._check_prediction_admissibility(action, capability)
            if prediction_result is not None:
                return ConformanceResult(
                    proposal_id=proposal.proposal_id,
                    conformant=False,
                    denial_stage=DenialStage.PREDICTION_INADMISSIBLE,
                    denial_reason=prediction_result,
                )

        return ConformanceResult(
            proposal_id=proposal.proposal_id,
            conformant=True,
            denial_stage=None,
            denial_reason=None,
        )

    @staticmethod
    def _check_scope(action: Action, capability: CapabilityVersion) -> str | None:
        """Cap §6 "Scope Validation." Phase 3 scope-checking mechanism:
        every Action's `parameters` MUST declare no field the Capability's
        `input_schema` does not name — a mechanically-checkable structural
        bound, not (yet) a full resource-pattern scope language. Returns a
        denial reason string, or `None` if in-scope."""
        unknown_params = set(action.parameters) - set(capability.input_schema)
        if unknown_params:
            return (
                f"Action parameters {sorted(unknown_params)} are not declared "
                f"in {capability.capability_id} v{capability.version}'s input_schema "
                f"{sorted(capability.input_schema)} — scope exceeds the Capability's "
                "declared ceiling."
            )
        return None

    @staticmethod
    def _check_prediction_admissibility(
        action: Action, capability: CapabilityVersion
    ) -> str | None:
        """Cap §13 requirements 1-4, mechanically checked. Requirement 5
        (necessary-condition relationship) is declared, not checked here —
        `Prediction.necessary_condition_rationale`'s non-emptiness is
        already enforced at construction (`model.py`); this method never
        re-derives its truth."""
        prediction = action.prediction
        if prediction.target_observable not in capability.output_schema:
            return (
                f"Prediction.target_observable={prediction.target_observable!r} is "
                f"not present in {capability.capability_id} v{capability.version}'s "
                f"declared output_schema {sorted(capability.output_schema)}."
            )
        return None

    # ------------------------------------------------------------------
    # Per-Action: Eligibility (Cap §6/§8 state 2)
    # ------------------------------------------------------------------

    def check_eligibility(
        self,
        action: Action,
        *,
        budget_available: bool,
        lease_held: bool,
        prior_action_halted: bool,
        preconditions_hold: bool,
    ) -> EligibilityResult:
        """Cap §8 state 2 — "nothing structurally blocks it," never
        permission. `action.expected_artifact_identity` is always `None`
        for this phase's representative Capability
        (`produces_artifact=False` on both ends), so the artifact-identity
        precondition trivially passes and is not separately parameterized
        here — a future artifact-producing Capability's Eligibility check
        would extend this with a real identity comparison.
        """
        if prior_action_halted:
            return EligibilityResult(
                action_id=action.action_id,
                eligible=False,
                denial_stage=DenialStage.PRECONDITION_INVALIDATED,
                denial_reason="a prior Action in this composition halted.",
            )
        if not preconditions_hold:
            return EligibilityResult(
                action_id=action.action_id,
                eligible=False,
                denial_stage=DenialStage.PRECONDITION_INVALIDATED,
                denial_reason="preconditions no longer hold given prior Observations.",
            )
        if not budget_available:
            return EligibilityResult(
                action_id=action.action_id,
                eligible=False,
                denial_stage=DenialStage.BUDGET_EXHAUSTED,
                denial_reason="no budget remains for this Action.",
            )
        if not lease_held:
            return EligibilityResult(
                action_id=action.action_id,
                eligible=False,
                denial_stage=DenialStage.LEASE_CONFLICT,
                denial_reason="the required lease is not held.",
            )
        return EligibilityResult(
            action_id=action.action_id, eligible=True, denial_stage=None, denial_reason=None
        )

    # ------------------------------------------------------------------
    # Final gate + Grant issuance + consumption + dispatch
    # ------------------------------------------------------------------

    async def authorize_and_execute(
        self,
        *,
        task_id: uuid.UUID,
        action: Action,
        human_approval: HumanApprovalRecord | None,
        lease_conflicts: bool = False,
        emergency_policy_active: bool = False,
    ) -> ActionExecutionResult:
        """Runs the final gate, issues a Grant, consumes it, dispatches
        the Tool, and records the Observation. `AuthorizationConsuming` is
        committed durably (see `_consume_and_dispatch`'s own docstring,
        Phase 3 exit-audit correction #1) BEFORE the Tool is dispatched,
        so a crash during or after dispatch leaves a real, queryable
        `AuthorizationConsuming` event with no matching
        `AuthorizationConsumed` — the durable evidence a future
        reconciliation mechanism needs. That reconciliation sweep itself
        (automatically detecting and resolving an orphaned Consuming
        event on restart) is still out of scope for this phase and
        remains a named limitation — this correction makes the orphan
        detectable, not self-healing.

        Raises `AuthorizationDeniedError` if any final-gate check fails —
        the denial itself is durably recorded via an `AuthorizationDenied`
        event before the exception is raised, so a denial is never lost
        even though the caller sees an exception rather than a return
        value.
        """
        capability = self._capabilities.get(action.capability_id, action.capability_version)
        if capability is None:
            await self._record_denial(
                task_id=task_id,
                action=action,
                capability_id=action.capability_id,
                stage=DenialStage.CAPABILITY_GAP,
                reason="Capability no longer resolvable at the final gate.",
            )
            raise CapabilityGapError(action.capability_id)

        policy_decision = self._policy.evaluate(capability.capability_id)
        if not policy_decision.allowed:
            await self._record_denial(
                task_id=task_id,
                action=action,
                capability_id=capability.capability_id,
                stage=DenialStage.POLICY_DENIAL,
                reason=policy_decision.reason,
            )
            raise AuthorizationDeniedError(policy_decision.reason)

        if capability.required_authorization != "none":
            if human_approval is None:
                await self._record_denial(
                    task_id=task_id,
                    action=action,
                    capability_id=capability.capability_id,
                    stage=DenialStage.AWAITING_HUMAN,
                    reason=(
                        f"{capability.capability_id} requires "
                        f"{capability.required_authorization!r}; no Human Approval "
                        "record was supplied."
                    ),
                )
                raise AuthorizationDeniedError("awaiting human approval")
            if not is_approval_still_valid_for_scope(
                human_approval, current_scope={"capability_id": capability.capability_id}
            ):
                await self._record_denial(
                    task_id=task_id,
                    action=action,
                    capability_id=capability.capability_id,
                    stage=DenialStage.AWAITING_HUMAN,
                    reason="Human Approval content hash no longer matches the current scope.",
                )
                raise AuthorizationDeniedError("stale human approval")

        safety_result = evaluate_safety_validity(
            capability_id=capability.capability_id,
            side_effect_class=capability.side_effect_class.value,
            risk_class=capability.risk_class.value,
            lease_conflicts=lease_conflicts,
            emergency_policy_active=emergency_policy_active,
        )
        if not safety_result.valid:
            await self._record_denial(
                task_id=task_id,
                action=action,
                capability_id=capability.capability_id,
                stage=DenialStage.STALE_SAFETY_VALIDITY,
                reason=safety_result.reason,
            )
            raise AuthorizationDeniedError(safety_result.reason)

        grant, granted_event_id = await self._issue_grant(
            task_id=task_id,
            action=action,
            capability=capability,
            policy_version_id=policy_decision.policy_version_id,
            safety_valid=safety_result.valid,
            safety_reason=safety_result.reason,
            human_approval=human_approval,
        )

        return await self._consume_and_dispatch(
            task_id=task_id, action=action, grant=grant, granted_event_id=granted_event_id
        )

    async def _issue_grant(
        self,
        *,
        task_id: uuid.UUID,
        action: Action,
        capability: CapabilityVersion,
        policy_version_id: str,
        safety_valid: bool,
        safety_reason: str,
        human_approval: HumanApprovalRecord | None,
    ) -> tuple[AuthorizationGrant, uuid.UUID]:
        """Returns `(grant, granted_event_id)`. These are deliberately TWO
        distinct identifiers: `grant.grant_id` is a business identifier
        chosen here and recorded in the `AuthorizationGranted` payload's
        own `grant_id` field; `granted_event_id` is that event's real,
        database-assigned `EngineeringEvent.id`. Every LATER event that
        causally references this Grant (`AuthorizationConsuming`,
        `AuthorizationConsumed`, `AuthorizationInvalidated`) MUST set both
        `causation_event_id` and its payload's `grant_event_id` field to
        `granted_event_id` — never to `grant.grant_id` — because
        `EngineeringEventRepository`'s causal-reference validation
        resolves `causation_event_id` against real
        `EngineeringEvent` rows, not against an application-level id
        embedded in a payload.
        """
        grant_id = uuid.uuid4()
        issued_at = datetime.now(UTC)
        action_parameters_hash = hash_action_parameters(action.parameters)
        grant = AuthorizationGrant(
            grant_id=grant_id,
            action_id=action.action_id,
            capability_id=capability.capability_id,
            capability_version=capability.version,
            action_parameters_hash=action_parameters_hash,
            policy_version_id=policy_version_id,
            scope=capability.scope_ceiling,
            safety_validity_result=safety_reason,
            safety_validity_valid=safety_valid,
            novelty="known",
            human_approval_content_hash=(
                human_approval.content_hash if human_approval is not None else None
            ),
            issued_at=issued_at,
            ttl_seconds=DEFAULT_GRANT_TTL_SECONDS,
        )
        granted_event = await self._events.append(
            task_id=task_id,
            event_type=AUTHORIZATION_GRANTED,
            payload={
                "grant_id": str(grant_id),
                "action_id": str(action.action_id),
                "capability_id": capability.capability_id,
                "capability_version": capability.version,
                # Not required by `validate_authorization_granted` (an
                # additive, non-breaking field) — recorded so the durable
                # event alone carries what correction #2 binds, matching
                # §7.1's "sufficient for forensic reconstruction."
                "action_parameters_hash": action_parameters_hash,
                "policy_version_id": policy_version_id,
                "scope": capability.scope_ceiling,
                "safety_validity_result": {"valid": safety_valid, "reason": safety_reason},
                "novelty": grant.novelty,
                "issued_at": issued_at.isoformat(),
                "ttl_seconds": grant.ttl_seconds,
                "human_approval_id": (
                    human_approval.approval_id if human_approval is not None else None
                ),
            },
            actor=_CONTROL_PLANE_ACTOR,
        )
        return grant, granted_event.id

    async def _consume_and_dispatch(
        self,
        *,
        task_id: uuid.UUID,
        action: Action,
        grant: AuthorizationGrant,
        granted_event_id: uuid.UUID,
    ) -> ActionExecutionResult:
        """Explicit reuse of the Phase 1 advisory-lock pattern: this
        method acquires `pg_advisory_xact_lock` on `task_id` itself,
        BEFORE checking Grant usability, so a concurrent consumption
        attempt for the same task serializes here rather than racing.
        `pg_advisory_xact_lock` is safely re-acquirable by the same
        session within the same transaction (Postgres advisory xact locks
        are per-session-reentrant, not a strict mutex that would
        self-deadlock) — `EngineeringEventRepository.append()` below
        acquires the identical lock again internally, which is a no-op
        given it's already held by this session in this transaction, not
        a second, competing acquisition.

        Phase 3 exit-audit corrections #2 and #3, both applied here:

        #2 — Grant/Action identity binding, checked first, before the
        lock is even taken: a purely in-memory comparison of what
        `grant` actually authorizes against what `action`/`grant` claim
        to be consuming together. No database access is needed for this
        check, so it costs nothing to fail fast on a mismatched pair
        before touching the lock or the event log at all.

        #3 — durable consumption-uniqueness, checked from a fresh
        database read (`list_for_task`, not `grant.state`) after the
        lock is held, immediately before `AuthorizationConsuming` is
        appended. This is the check the original implementation's
        docstring claimed but never actually performed: two
        independently reconstructed `AuthorizationGrant` objects, each
        believing itself `GRANTED` because each was built from the same
        persisted `AuthorizationGranted` event without knowledge of the
        other's progress, must not both be allowed to begin consumption.
        """
        if grant.action_id != action.action_id:
            await self._record_denial(
                task_id=task_id,
                action=action,
                capability_id=action.capability_id,
                stage=DenialStage.SCOPE_VIOLATION,
                reason=(
                    f"Grant {grant.grant_id} authorizes action_id="
                    f"{grant.action_id}, not the supplied action_id="
                    f"{action.action_id} — refusing to consume a Grant for "
                    "a different Action."
                ),
            )
            raise AuthorizationDeniedError(
                f"Grant {grant.grant_id} does not authorize action_id={action.action_id}."
            )
        if grant.capability_id != action.capability_id:
            await self._record_denial(
                task_id=task_id,
                action=action,
                capability_id=action.capability_id,
                stage=DenialStage.SCOPE_VIOLATION,
                reason=(
                    f"Grant {grant.grant_id} authorizes capability_id="
                    f"{grant.capability_id!r}, not the supplied "
                    f"capability_id={action.capability_id!r}."
                ),
            )
            raise AuthorizationDeniedError(
                f"Grant {grant.grant_id} does not authorize capability_id={action.capability_id!r}."
            )
        if grant.capability_version != action.capability_version:
            await self._record_denial(
                task_id=task_id,
                action=action,
                capability_id=action.capability_id,
                stage=DenialStage.SCOPE_VIOLATION,
                reason=(
                    f"Grant {grant.grant_id} authorizes capability_version="
                    f"{grant.capability_version}, not the supplied "
                    f"capability_version={action.capability_version}."
                ),
            )
            raise AuthorizationDeniedError(
                f"Grant {grant.grant_id} does not authorize capability_version="
                f"{action.capability_version}."
            )
        if grant.action_parameters_hash != hash_action_parameters(action.parameters):
            await self._record_denial(
                task_id=task_id,
                action=action,
                capability_id=action.capability_id,
                stage=DenialStage.SCOPE_VIOLATION,
                reason=(
                    f"Grant {grant.grant_id}'s authorized parameters do not "
                    "match the parameters on the Action supplied for "
                    "consumption — the Action was modified after "
                    "authorization."
                ),
            )
            raise AuthorizationDeniedError(
                f"Grant {grant.grant_id}'s authorized parameters do not match "
                "the supplied Action's current parameters."
            )

        await self._events._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:task_id)::bigint)"),
            {"task_id": str(task_id)},
        )

        if grant.state != GrantState.GRANTED:
            raise AuthorizationDeniedError(
                f"Grant {grant.grant_id} is not in GRANTED state (currently "
                f"{grant.state.value!r}) — cannot consume."
            )
        if grant.is_expired():
            await self._record_invalidation(
                task_id=task_id,
                action=action,
                grant=grant,
                granted_event_id=granted_event_id,
                reason="Grant expired before consumption.",
            )
            raise AuthorizationDeniedError(f"Grant {grant.grant_id} expired.")

        # Correction #3 — a real database existence check, not a trust in
        # the in-memory `grant.state` above (which two independently
        # reconstructed Grant objects would both report as GRANTED). Reuses
        # `list_for_task` — no new repository method needed, matching
        # `EngineeringEventRepository`'s existing "only two methods"
        # convention — filtered in Python for any event already causally
        # chained to this Grant's issuance.
        already_progressed_types = {
            AUTHORIZATION_CONSUMING,
            AUTHORIZATION_CONSUMED,
            AUTHORIZATION_INVALIDATED,
        }
        existing_task_events = await self._events.list_for_task(task_id)
        if any(
            e.causation_event_id == granted_event_id and e.event_type in already_progressed_types
            for e in existing_task_events
        ):
            raise AuthorizationDeniedError(
                f"Grant {grant.grant_id} (event {granted_event_id}) already has a "
                "recorded Consuming/Consumed/Invalidated event — refusing a second "
                "concurrent or repeated consumption attempt."
            )

        consuming_grant = grant.consuming()
        consuming_event = await self._events.append(
            task_id=task_id,
            event_type=AUTHORIZATION_CONSUMING,
            payload={
                "grant_event_id": str(granted_event_id),
                "action_id": str(action.action_id),
            },
            actor=_CONTROL_PLANE_ACTOR,
            causation_event_id=granted_event_id,
        )

        # Phase 3 exit-audit correction #1 — the one intentional commit
        # this class performs. `EngineeringEventRepository`/every other
        # Phase 1-3 repository deliberately never commits, leaving the
        # caller/transaction owner in control (see that repository's own
        # docstring) — that convention is preserved everywhere else in
        # this method and this class. This ONE boundary is the deliberate
        # exception: `AuthorizationConsuming` durably recording "dispatch
        # is about to happen" is worthless as a crash-safety signal if it
        # can still be rolled back by a failure that occurs AFTER the
        # real, external Tool dispatch below. Committing here — after
        # Consuming is appended, before `self._tool_executor.execute(...)`
        # is ever called — is what makes the three-state Grant lifecycle
        # (`GRANTED -> CONSUMING -> CONSUMED`) actually crash-safe rather
        # than merely a shape: a crash during or after dispatch now
        # leaves a durably committed `AuthorizationConsuming` event with
        # no matching `AuthorizationConsumed`, which is exactly the
        # ambiguous state a future reconciliation mechanism needs to find
        # and resolve (not built in this phase — see the Phase 3 exit
        # audit's crash-window finding and the limitations table).
        # Deliberately NOT a commit-after-every-event pattern: nothing
        # else in this method commits, because nothing else sits on the
        # boundary between "durable enough to matter" and "about to
        # trigger an irreversible external effect."
        await self._events._db.commit()

        # `action.parameters` mirrors the bound Capability's `input_schema`
        # shape exactly (`{"query": str, "parameters": dict}` for
        # `query_knowledge_graph`) — it is NOT itself `ToolInput.parameters`.
        # Passing it through unmapped would silently duplicate `query`
        # inside `parameters` and drop the real nested parameters (e.g.
        # `db`/`user_id` the Neo4j tool requires) wherever an Action's
        # nested `parameters` dict actually carries them.
        tool_input = ToolInput(
            query=str(action.parameters.get("query", "")),
            parameters=dict(action.parameters.get("parameters") or {}),
        )
        bound_capability = self._capabilities.get(action.capability_id, action.capability_version)
        assert bound_capability is not None  # already resolved at the final gate above.
        tool_result = await self._tool_executor.execute(bound_capability.tool_id, tool_input)

        await self._events.append(
            task_id=task_id,
            event_type=AUTHORIZATION_CONSUMED,
            payload={
                "grant_event_id": str(granted_event_id),
                "consuming_event_id": str(consuming_event.id),
                "action_id": str(action.action_id),
                "tool_id": tool_result.tool_id,
                "dispatch_started_at": datetime.now(UTC).isoformat(),
            },
            actor=_CONTROL_PLANE_ACTOR,
            causation_event_id=consuming_event.id,
        )
        # Validated for its side effect (raises GrantLifecycleError if
        # misordered); the resulting CONSUMED grant object is not itself
        # persisted anywhere further — the AuthorizationConsumed event just
        # appended above is CONSUMED's durable record (§7.1).
        consuming_grant.consumed()

        # Cap §8 state 5: "Execution Completed" requires a DETERMINATE
        # outcome; `ActionOutcomeUnknown` is a distinct terminal state for
        # indeterminacy (e.g. a dispatch that times out with the remote
        # side effect's actual fate unknown), not merely "success/failure
        # ambiguous." `ToolExecutor.execute()` never raises and always
        # returns a `ToolResult` with `success` explicitly set to `True`
        # or `False` — including its own timeout/exception branches,
        # which set `success=False` with a populated `error`, not `None`.
        # So under this phase's synchronous, in-process dispatch model,
        # every dispatch reaches a determinate outcome by construction;
        # `outcome_unknown` has no real path to occur yet. Recorded
        # honestly as a limitation, not silently implied as "handled": a
        # future asynchronous/detached dispatch model (a long-running Tool
        # invocation surviving past this request) would need a real
        # source of indeterminacy this phase's Tool layer doesn't have.
        # `ToolResult.success` is a non-optional `bool` — there is no
        # third value to branch on today, so `outcome` is unconditionally
        # "completed" here; the field exists on `ActionExecutionResult`
        # so that future source of indeterminacy has somewhere to report.
        outcome = "completed"
        observation_event = await self._events.append(
            task_id=task_id,
            event_type=OBSERVATION_RECORDED,
            payload={
                # `raw_result`/`capability` are Phase 1's required shape
                # (`validate_observation_recorded`) — deliberately unchanged
                # by Phase 3, which only adds a NEW producer of this
                # existing event type, not a new shape for it.
                "raw_result": {
                    "success": tool_result.success,
                    "summary": tool_result.data.get("summary") if tool_result.data else None,
                    "error": tool_result.error,
                },
                "capability": action.capability_id,
                "action_id": str(action.action_id),
                "tool_id": tool_result.tool_id,
                "success": tool_result.success,
                "grant_id": str(grant.grant_id),
            },
            actor=_CONTROL_PLANE_ACTOR,
        )

        return ActionExecutionResult(
            action_id=action.action_id,
            grant_id=grant.grant_id,
            outcome=outcome,
            tool_success=tool_result.success,
            observation_event_id=observation_event.id,
        )

    async def _record_denial(
        self,
        *,
        task_id: uuid.UUID,
        action: Action | None,
        capability_id: str | None,
        stage: DenialStage,
        reason: str,
    ) -> None:
        await self._events.append(
            task_id=task_id,
            event_type=AUTHORIZATION_DENIED,
            payload={
                "denial_stage": stage.value,
                "reason": reason,
                "action_id": str(action.action_id) if action is not None else None,
                "capability_id": capability_id,
            },
            actor=_CONTROL_PLANE_ACTOR,
        )

    async def _record_invalidation(
        self,
        *,
        task_id: uuid.UUID,
        action: Action,
        grant: AuthorizationGrant,
        granted_event_id: uuid.UUID,
        reason: str,
    ) -> None:
        await self._events.append(
            task_id=task_id,
            event_type=AUTHORIZATION_INVALIDATED,
            payload={
                "grant_event_id": str(granted_event_id),
                "action_id": str(action.action_id),
                "reason": reason,
                "invalidated_by_policy_version_id": self._policy.combined_version_signature(),
            },
            actor=_CONTROL_PLANE_ACTOR,
            causation_event_id=granted_event_id,
        )


class AuthorizationDeniedError(RuntimeError):
    """Raised by `authorize_and_execute` on any final-gate failure. The
    denial is always durably recorded (`AuthorizationDenied` event)
    before this is raised — callers must not treat "no return value" as
    "nothing happened"; the Engineering State is the record of what did.
    """


__all__ = [
    "ActionExecutionResult",
    "AuthorizationDeniedError",
    "CapabilityGapError",
    "ControlPlane",
]
