"""Minimum Policy abstraction — Phase 3.

Implements exactly enough of Cap §20 to give the authorization pipeline a
real (not faked) Policy to consult: versioned, scoped (system → tenant →
task), most-restrictive-wins intersection, fail-closed on anything
unreadable, default-deny with an explicit seed ALLOW rule.

**Deliberately NOT implemented — do not fake:** cryptographic signing,
m-of-n quorum, or a separate Policy Authority identity distinct from
Task Approver. §20.1's "signed immutable version" and §20.2's "signatures
originate only from human quorum keys" describe a governance mechanism
this phase does not build. `PolicyVersion.content_hash` here is a real
SHA-256 of the rule set (so a version's *content* is verifiably what it
claims to be, and any accidental mutation is detectable) — but
`authoring_authority` is a plain string field, unverified against any
key, and there is no quorum check anywhere in this module. This is
recorded honestly rather than simulated: `PolicyVersion.signed` is
hardcoded to `False`, and every construction site says so in a comment,
so nothing downstream can mistake this for the real governance mechanism.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum


class PolicyError(ValueError):
    """A Policy object was constructed in a shape the model forbids."""


class PolicyScopeLevel(StrEnum):
    SYSTEM = "system"
    TENANT = "tenant"
    TASK = "task"


class PolicyRuleEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One rule: does `effect` apply to `capability_id` at `scope_level`?
    §20.3: "Narrower scopes may only restrict" — enforced by
    `PolicyStore.evaluate`, not by this dataclass (a rule alone can't know
    what a broader scope already said)."""

    capability_id: str
    effect: PolicyRuleEffect
    scope_level: PolicyScopeLevel
    reason: str

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise PolicyError("PolicyRule.capability_id must be non-empty.")
        if not self.reason.strip():
            raise PolicyError("PolicyRule.reason must be non-empty.")


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    """§20.1: "Every Policy evaluation binds to exactly one Policy Version
    identity." `version_id` is the content hash itself — the version
    identity IS the content identity, so two different rule sets can never
    collide under one id, and a Grant recording this id is recording
    exactly what was evaluated, not a mutable pointer to it."""

    rules: tuple[PolicyRule, ...]
    authoring_authority: str
    effective_at: str  # ISO-8601; caller-supplied, this module has no clock.
    supersedes: str | None
    # See module docstring: real content hash, NOT a cryptographic
    # signature. Hardcoded False so nothing downstream can mistake this
    # for §20.1's actual signed-quorum mechanism, which this phase does
    # not implement.
    signed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not self.authoring_authority.strip():
            raise PolicyError("PolicyVersion.authoring_authority must be non-empty.")

    @property
    def version_id(self) -> str:
        canonical = json.dumps(
            [
                {
                    "capability_id": r.capability_id,
                    "effect": r.effect.value,
                    "scope_level": r.scope_level.value,
                    "reason": r.reason,
                }
                for r in self.rules
            ]
            + [self.authoring_authority, self.effective_at, self.supersedes],
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of evaluating one Capability against the active Policy
    stack. `allowed=False` with `reason` set is the normal deny path;
    `PolicyStore.evaluate` never returns a decision without a reason."""

    capability_id: str
    allowed: bool
    policy_version_id: str
    reason: str


class PolicyStore:
    """Holds exactly one active `PolicyVersion` per scope level and
    evaluates the §20.3 intersection.

    Fail-closed (§20.3: "Unreadable or unresolvable Policy = deny"): a
    scope level with no PolicyVersion loaded is simply absent from
    `_versions`, which never permits — the default across ALL levels is
    DENY, only an explicit ALLOW rule at every level that mentions the
    Capability (and no DENY at any level) admits it.
    """

    def __init__(self) -> None:
        self._versions: dict[PolicyScopeLevel, PolicyVersion] = {}

    def load(self, scope_level: PolicyScopeLevel, version: PolicyVersion) -> None:
        """Replace the active version at `scope_level`. §20.4: the caller
        (ControlPlane) is responsible for invalidating outstanding Grants
        when this changes — this store does not do that itself, since it
        has no notion of what Grants exist."""
        self._versions[scope_level] = version

    def active_version_id(self, scope_level: PolicyScopeLevel) -> str | None:
        version = self._versions.get(scope_level)
        return version.version_id if version is not None else None

    def combined_version_signature(self) -> str:
        """A single id representing "all currently-loaded Policy Versions
        together," recorded on a Grant so a later Policy change at ANY
        scope level is detectable (§20.4) without needing three separate
        columns. Deterministic: same loaded set → same signature."""
        parts = [
            f"{level.value}:{version.version_id}"
            for level, version in sorted(self._versions.items(), key=lambda kv: kv[0].value)
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def evaluate(self, capability_id: str) -> PolicyDecision:
        """§20.3 most-restrictive-wins intersection across system → tenant
        → task. A single DENY at any loaded level denies outright. With no
        DENY present, an ALLOW is required at EVERY currently-loaded level
        that has a rule for this Capability; a level with no rule for this
        Capability is silently skipped (absence of an opinion, not a
        DENY) — but if NO level anywhere has an ALLOW, the fail-closed
        default applies and the Capability is denied.
        """
        signature = self.combined_version_signature()
        saw_allow = False
        for level in (PolicyScopeLevel.SYSTEM, PolicyScopeLevel.TENANT, PolicyScopeLevel.TASK):
            version = self._versions.get(level)
            if version is None:
                continue
            matching = [r for r in version.rules if r.capability_id == capability_id]
            deny_rule = next((r for r in matching if r.effect == PolicyRuleEffect.DENY), None)
            if deny_rule is not None:
                return PolicyDecision(
                    capability_id=capability_id,
                    allowed=False,
                    policy_version_id=signature,
                    reason=f"denied at {level.value} scope: {deny_rule.reason}",
                )
            if any(r.effect == PolicyRuleEffect.ALLOW for r in matching):
                saw_allow = True

        if saw_allow:
            return PolicyDecision(
                capability_id=capability_id,
                allowed=True,
                policy_version_id=signature,
                reason="allowed: no DENY at any loaded scope, ALLOW present.",
            )
        return PolicyDecision(
            capability_id=capability_id,
            allowed=False,
            policy_version_id=signature,
            reason=(
                "denied: fail-closed default — no ALLOW rule found for this "
                "capability at any loaded Policy scope."
            ),
        )


def seed_system_policy_allowing(
    capability_id: str, authored_by: str, effective_at: str
) -> PolicyVersion:
    """The one seed Policy this phase ships: an explicit system-scope
    ALLOW for exactly the Phase 3 representative Capability. Everything
    else stays denied by the fail-closed default — Cap §20.3's "narrower
    scopes may only restrict" combined with "no widening operation" means
    this single system-level ALLOW is the only affirmative grant of
    authority anywhere in the Policy stack this phase constructs."""
    rule = PolicyRule(
        capability_id=capability_id,
        effect=PolicyRuleEffect.ALLOW,
        scope_level=PolicyScopeLevel.SYSTEM,
        reason=f"Phase 3 seed policy: {capability_id} is the authorized representative Capability.",
    )
    return PolicyVersion(
        rules=(rule,),
        authoring_authority=authored_by,
        effective_at=effective_at,
        supersedes=None,
    )


__all__ = [
    "PolicyDecision",
    "PolicyError",
    "PolicyRule",
    "PolicyRuleEffect",
    "PolicyScopeLevel",
    "PolicyStore",
    "PolicyVersion",
    "seed_system_policy_allowing",
]
