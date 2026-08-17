"""Engineering State — Phase 1 (event substrate + deterministic
materialization only).

This package implements the minimum real substrate
`docs/graphforge/ENGINEERING_STATE_ARCHITECTURE.md` requires, and nothing
past it. It is deliberately narrow:

- `events.py` — the closed Phase 1 event vocabulary and per-type payload
  shape validation (§8 of the contract: "meaningful domain events", not
  every database write turned into one).
- `materialize.py` — the pure, deterministic fold from an ordered event
  list to a `MaterializedEngineeringState` (§9: "the materialized
  Engineering State at any historical point in time MUST be exactly
  reconstructable by folding the event log up to that point").

Explicitly NOT here yet, per the Phase 1 stop condition — each belongs to
a later, separately-authorized phase:

- ActionProposal, Capability, Control Plane, Policy, Authorization Grants
  (Capabilities & Control Plane contract — Phase 2/3).
- Workspace, Execution, Independent Verification, Observation
  *classification* (as opposed to the raw `ObservationRecorded` event
  this phase does record) (Phase 4–6).
- Replanning, Goal-Satisfied evaluation (Phase 7).
- Multi-Role ownership/leases beyond the append-time concurrency
  guarantee `app.repositories.engineering_event_repository` already
  provides (Phase 9).

Persistence lives in `app.models.engineering_event.EngineeringEvent` (the
row shape) and `app.repositories.engineering_event_repository` (the only
way to append or read one) — not in this package, matching this
repository's existing convention of models/repositories as their own
layer, e.g. `app.repositories.engineering_memory_repository`.
"""

from __future__ import annotations
