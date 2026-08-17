"""Phase 0 boundary marker — the Control Plane package reserved by
`docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md`.

**This package implements nothing yet.** It exists solely so the
Tool-execution boundary (§4 of that contract: "A Capability MUST NOT own
authorization... the Control Plane is the sole evaluator") has a real,
importable namespace to be the *sole* exemption from the direct-dispatch
ban enforced by `tests/unit/architecture/test_capability_execution_boundary.py`
— before the Control Plane itself is built.

Implementation phases (per the frozen dependency-order plan; not a fourth
architecture document, just a pointer):

    Phase 0 (this package) — enforcement scaffolding only. Establishes
      that nothing outside `app.control_plane` may import
      `app.tools.executor`'s dispatch entry point, via a CI-blocking test.
      No runtime logic lives here yet.
    Phase 2 — a real `CapabilityRegistry` is added here (or under a
      sibling module this package re-exports), per Capabilities contract
      §3/§10.
    Phase 3 — the actual Control Plane validation pipeline (Capabilities
      contract §6) is implemented here, and the placeholder exemption
      this package currently represents becomes a real, enforced entry
      point (ActionProposal in, Grant out).

Do not add dispatch logic, Capability registration, Policy evaluation, or
Grant issuance to this package until the corresponding phase is
separately authorized. Until then this file's only job is to exist, so
the boundary test in `tests/unit/architecture/` has something concrete to
name as the one legitimate importer of `app.tools.executor`.

See `docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md` for the
normative contract this package will eventually implement. That document
is authoritative; this docstring is a pointer, not a restatement.
"""

from __future__ import annotations
