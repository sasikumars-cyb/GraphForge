"""Adversarial scenario 9 (Phase 2 instructions): "Capability metadata
claims reversible while Tool performs irreversible behavior."

This is NOT mechanically checkable by `CapabilityRegistry` — the
registry has no way to inspect what a Tool's `execute()` actually does at
runtime, only what its author declared. Per the Phase 2 instructions'
own guidance ("Where runtime enforcement belongs to Phase 3, the Phase 2
test may assert that the missing authorization boundary is explicit
rather than faking it"), this test proves the gap is honest and
documented, not silently assumed solved — the same discipline
`tests/unit/engineering_state/test_events.py::
test_observation_recorded_has_no_classification_field` already
established for a structurally identical situation in Phase 1.

Independent Verification (Phase 6) is the frozen contract's actual answer
to this gap (Cap §15: the verifier re-derives its own evidence rather
than trusting a generator's — or, here, a registrant's — claim) — not
something Phase 2's registry can or should attempt to substitute for.
"""

from __future__ import annotations

import inspect

from app.capabilities.registry import CapabilityRegistry


def test_registry_has_no_mechanism_to_verify_declared_metadata_against_tool_behavior() -> None:
    """Structural proof of the gap's honesty: no method on
    CapabilityRegistry inspects, runs, or verifies a bound Tool's actual
    behavior against its Capability's declared metadata (reversibility,
    side_effect_class, risk_class, ...). Registration only checks the
    METADATA's own internal shape (CapabilityVersion.__post_init__) and
    that the referenced Tool exists — never that the Tool's real behavior
    matches what was declared about it."""
    verification_like_names = {
        name
        for name, _ in inspect.getmembers(CapabilityRegistry)
        if not name.startswith("_")
        and any(w in name.lower() for w in ("verify", "validate_behavior", "check_tool"))
    }
    assert verification_like_names == set(), (
        f"CapabilityRegistry unexpectedly has verification-shaped "
        f"method(s): {verification_like_names}. If this is real "
        "behavior-verification, it belongs to Independent Verification "
        "(Phase 6), not the registry — rename or relocate it; do not let "
        "the registry quietly start pretending to close this gap."
    )
