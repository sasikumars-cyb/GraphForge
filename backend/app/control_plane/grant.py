"""Authorization Grant — Cap §7.

A first-class object, separate from both the ActionProposal and the
Action. This module defines the in-memory shape and the three-state
crash-safe lifecycle (§11 of the sequencing review, incorporated into
Cap §7.1 by reference in this phase's instructions); durable persistence
as Engineering State events is `ControlPlane`'s responsibility
(`app.control_plane.control_plane`), not this module's — this module has
no database access, matching every other Phase 2/3 data-model module's
convention.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# Cap §7: "carry a bounded TTL." No Capability in this phase declares its
# own preferred TTL, so a single conservative system default applies to
# every Grant this phase issues.
DEFAULT_GRANT_TTL_SECONDS = 60


def hash_action_parameters(parameters: dict[str, Any]) -> str:
    """Phase 3 exit-audit correction #2: a canonical content hash of the
    exact `Action.parameters` a Grant was issued for, bound onto the
    Grant at issuance (`AuthorizationGrant.action_parameters_hash`) and
    re-computed at consumption for comparison. `action_id`/`capability_id`/
    `capability_version` alone do not protect against a caller supplying
    the correct ids but MODIFIED parameters at consumption time — the
    audit's own finding. Mirrors `app.control_plane.human_approval.
    HumanApprovalRecord.content_hash` and `app.control_plane.policy.
    PolicyVersion.version_id`'s existing canonical-JSON-then-SHA-256
    convention exactly; no new hashing scheme introduced."""
    canonical = json.dumps(parameters, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GrantState(StrEnum):
    """The three-state crash-safe lifecycle. `CONSUMING` exists so a
    process crash between "decided to dispatch" and "dispatch actually
    ran" is detectable and recoverable rather than silently looking like
    an unconsumed, still-usable Grant (double-execution risk) or a
    consumed-with-no-record Grant (silent loss) — the real failure mode
    the adversarial sequencing review flagged and required a third state
    to close."""

    GRANTED = "granted"
    CONSUMING = "consuming"
    CONSUMED = "consumed"


class GrantLifecycleError(ValueError):
    """Raised on any attempted transition that is not GRANTED→CONSUMING or
    CONSUMING→CONSUMED — Cap §7: "never be reused... never be inherited."
    """


@dataclass(frozen=True, slots=True)
class AuthorizationGrant:
    """Cap §7's full binding: references exactly one Action, one
    Capability+version, one Policy Version, carries the Safety Validity
    result and its inputs, and a bounded TTL. Frozen — a state transition
    produces a NEW `AuthorizationGrant` (via `consuming()`/`consumed()`
    below) rather than mutating this one, so "the Grant as issued" always
    remains inspectable even after it moves through the lifecycle.
    """

    grant_id: uuid.UUID
    action_id: uuid.UUID
    capability_id: str
    capability_version: int
    # Phase 3 exit-audit correction #2 — see `hash_action_parameters`'s own
    # docstring. Part of the Grant's binding alongside action_id/
    # capability_id/capability_version, not a separate concept: together
    # these four fields are "exactly which Action, with exactly which
    # parameters, this Grant authorizes."
    action_parameters_hash: str
    policy_version_id: str
    scope: str
    safety_validity_result: str  # SafetyValidityResult.reason, denormalized for the payload.
    safety_validity_valid: bool
    novelty: str  # "known" | "novel" — matches events.validate_authorization_granted.
    human_approval_content_hash: str | None
    issued_at: datetime
    ttl_seconds: int
    state: GrantState = GrantState.GRANTED

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("AuthorizationGrant.ttl_seconds must be positive.")
        if self.novelty not in {"known", "novel"}:
            raise ValueError("AuthorizationGrant.novelty must be 'known' or 'novel'.")

    @property
    def expires_at(self) -> datetime:
        """§7.1: "Expiry is derived ... MUST NOT be separately persisted."
        Computed on every access, never cached, never stored as its own
        field."""
        from datetime import timedelta

        return self.issued_at + timedelta(seconds=self.ttl_seconds)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now if now is not None else datetime.now(UTC)
        return current >= self.expires_at

    def consuming(self) -> AuthorizationGrant:
        """GRANTED → CONSUMING. Raises if not currently GRANTED — a Grant
        can only begin being consumed once."""
        if self.state != GrantState.GRANTED:
            raise GrantLifecycleError(
                f"Grant {self.grant_id} cannot transition to CONSUMING from "
                f"state {self.state.value!r}; only GRANTED->CONSUMING is valid."
            )
        return _replace_state(self, GrantState.CONSUMING)

    def consumed(self) -> AuthorizationGrant:
        """CONSUMING → CONSUMED. Terminal — Cap §7: "be consumed on
        dispatch, and thereby be permanently unusable again." No further
        transition exists from CONSUMED."""
        if self.state != GrantState.CONSUMING:
            raise GrantLifecycleError(
                f"Grant {self.grant_id} cannot transition to CONSUMED from "
                f"state {self.state.value!r}; only CONSUMING->CONSUMED is valid."
            )
        return _replace_state(self, GrantState.CONSUMED)

    def is_usable(self, *, now: datetime | None = None) -> bool:
        """A Grant may be dispatched iff it is GRANTED (not yet
        consuming/consumed) and not expired. This is the ONLY predicate
        `ControlPlane` consults before beginning consumption — everything
        else (Policy re-confirmation, Safety Validity re-evaluation) is
        evaluated independently at the same moment, never inferred from
        this."""
        return self.state == GrantState.GRANTED and not self.is_expired(now=now)


def _replace_state(grant: AuthorizationGrant, new_state: GrantState) -> AuthorizationGrant:
    return AuthorizationGrant(
        grant_id=grant.grant_id,
        action_id=grant.action_id,
        capability_id=grant.capability_id,
        capability_version=grant.capability_version,
        action_parameters_hash=grant.action_parameters_hash,
        policy_version_id=grant.policy_version_id,
        scope=grant.scope,
        safety_validity_result=grant.safety_validity_result,
        safety_validity_valid=grant.safety_validity_valid,
        novelty=grant.novelty,
        human_approval_content_hash=grant.human_approval_content_hash,
        issued_at=grant.issued_at,
        ttl_seconds=grant.ttl_seconds,
        state=new_state,
    )


__all__ = [
    "AuthorizationGrant",
    "DEFAULT_GRANT_TTL_SECONDS",
    "GrantLifecycleError",
    "GrantState",
    "hash_action_parameters",
]
