"""Safety Validity — Cap §5/§6: "authoritative only at the final gate."

Evaluated fresh, every time, at the exact moment of the final authorization
gate — never earlier, never cached, never reused across Actions or across
a retry of the same Action. `ControlPlane` enforces the "only here, only
now" rule structurally: this module exposes exactly one evaluation
function and nowhere else in `app.control_plane` imports it.

Phase 3 scope: a real, minimal evaluator — not a stub that always returns
True. It fails closed on anything it cannot positively confirm, per the
instruction's explicit requirement. It does not yet integrate an external
"kill switch"/incident feed (no such system exists in this codebase yet);
that is recorded as a limitation in the Phase 3 report, not silently
faked as "checked."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SafetyValidityResult:
    """`valid=False` always carries a `reason`. This object, together with
    `SafetyValidityInputs` (model.py), is what Cap §7.1 requires the Grant
    payload to persist — "Safety Validity result AND its evaluated
    inputs," two objects, never collapsed into a single boolean."""

    valid: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("SafetyValidityResult.reason must always be set.")


# Deliberately conservative allowlist: Phase 3's ONLY registered
# Capability, `query_knowledge_graph`, is read-only, low-risk, no
# external write. Anything else fails closed — not because it is
# necessarily unsafe, but because this evaluator has no basis to confirm
# it IS safe, and Cap §6 requires fail-closed on the unknown.
_KNOWN_SAFE_LOW_RISK_CAPABILITIES: frozenset[str] = frozenset({"query_knowledge_graph"})


def evaluate_safety_validity(
    *,
    capability_id: str,
    side_effect_class: str,
    risk_class: str,
    lease_conflicts: bool,
    emergency_policy_active: bool,
    extra_context: dict[str, Any] | None = None,
) -> SafetyValidityResult:
    """Fail-closed Safety Validity evaluation.

    Every branch below either affirmatively confirms safety from a
    concrete, checkable fact, or denies. There is no branch that returns
    `valid=True` from an unexamined default.
    """
    if emergency_policy_active:
        return SafetyValidityResult(
            valid=False,
            reason="emergency policy is active — restriction-only, all Grants denied at the gate.",
        )
    if lease_conflicts:
        return SafetyValidityResult(
            valid=False,
            reason="a lease conflict exists for this Action's target resource.",
        )
    if side_effect_class != "read_only":
        return SafetyValidityResult(
            valid=False,
            reason=(
                f"capability_id={capability_id} has side_effect_class="
                f"{side_effect_class!r}, not 'read_only' — Phase 3 has no "
                "Safety Validity basis to confirm non-read-only effects "
                "are currently safe; failing closed."
            ),
        )
    if risk_class not in {"low"}:
        return SafetyValidityResult(
            valid=False,
            reason=f"capability_id={capability_id} has risk_class={risk_class!r}, not 'low'.",
        )
    if capability_id not in _KNOWN_SAFE_LOW_RISK_CAPABILITIES:
        return SafetyValidityResult(
            valid=False,
            reason=(
                f"capability_id={capability_id} is not on the Phase 3 "
                "known-safe allowlist — fail closed on the unknown."
            ),
        )
    return SafetyValidityResult(
        valid=True,
        reason=(
            f"capability_id={capability_id} is read_only, low-risk, on the "
            "known-safe allowlist, no lease conflict, no emergency policy active."
        ),
    )


__all__ = ["SafetyValidityResult", "evaluate_safety_validity"]
