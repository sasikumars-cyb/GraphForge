"""Capability layer — Phase 2 of the frozen implementation sequencing
plan.

`docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md` §1: "A
Capability is a registered, versioned declaration of one kind of effect
or observation the system is able to perform... It declares what is
possible and what permission that possibility requires; it never
declares that any particular use is permitted." That governance/
authorization half — the Control Plane — is explicitly NOT built here.
This package implements only:

- `model.py` — the `CapabilityVersion` data shape (§3 of that contract),
  immutable once registered, and the `ReversibilityClass`/`SideEffectClass`/
  `RiskClass`/`IsolationRequirement`/`CapabilityKind` closed vocabularies
  it's built from.
- `registry.py` — `CapabilityRegistry`: registration, lookup, version
  resolution, Tool-binding validation, the external-write freeze (still
  active until Phase 8), and the recursive-compensation guard (Capabilities
  contract §3.1). **It has no `execute`/`dispatch` method — see its own
  module docstring for why that absence is deliberate, not an oversight.**
- `setup.py` — the sole module permitted to call
  `CapabilityRegistry.register()`, mirroring the existing
  `app.agents.setup`/`app.tools.setup` convention exactly.

**Explicitly NOT here, per the Phase 2 stop condition** — each belongs to
a later, separately-authorized phase: ActionProposal, Policy evaluation,
Authorization Grants, Safety Validity, Compensation Reservations,
Workspace lifecycle, Independent Verification, Observation
classification, Goal Satisfied, Replanning. `app.control_plane` (the
Phase 0 boundary marker) remains untouched and still implements nothing —
Phase 3 fills it in using the Capability metadata this package makes
available, not the other way around.

`Capability` here is one of at least five pre-existing, unrelated uses of
that word already documented in
`CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md` §0.1 — see that section
before assuming any other "Capability"-named thing in this repository is
related to this package.
"""

from __future__ import annotations
