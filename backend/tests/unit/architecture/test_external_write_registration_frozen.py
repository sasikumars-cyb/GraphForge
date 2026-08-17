"""Phase 0, guardrail 5 — no NEW `external-write` Capability before
Phase 8.

The frozen implementation dependency plan is explicit: the write chain
(`create_branch`/`commit_changes`/`create_pull_request`) is migrated onto
per-Action Authorization Grants *last*, deliberately, once every primitive
it needs (Capability registry, Control Plane, Grant, Workspace,
Observation classification, Independent Verification, artifact identity,
and — new to that phase — the reversibility/Compensation Reservation
machinery) has been proven on lower-risk Capabilities first. Adding a
*new* external-write surface before then would mean the highest-blast-
radius category of action grows before the machinery that makes it safe
exists.

`app/agents/git_ops/_authorization.py`'s `WRITE_GOALS` frozenset is,
today, the entire real inventory of write-capable agents — its own
docstring documents that this was independently verified by grepping
`app/agents/` and `app/context_pipeline/` at audit time. This test pins
that inventory exactly. It does not touch `_authorization.py` itself
(Phase 0 does not migrate or modify existing Git write behavior).
"""

from __future__ import annotations

from app.agents.git_ops._authorization import WRITE_GOALS

# The exact, frozen set of write-capable goals as of Phase 0. This is not
# an estimate — it is asserted to equal WRITE_GOALS exactly, so any
# addition to that frozenset anywhere in the codebase fails this test
# immediately, forcing an explicit, reviewed update here (and, per the
# sequencing plan, that update should not happen before Phase 8's
# Compensation Reservation and reversibility-taxonomy machinery exists).
FROZEN_WRITE_GOALS: frozenset[str] = frozenset(
    {"create_branch", "commit_changes", "create_pull_request"}
)


def test_write_goals_unchanged_since_phase_0_baseline() -> None:
    assert WRITE_GOALS == FROZEN_WRITE_GOALS, (
        f"app.agents.git_ops._authorization.WRITE_GOALS has changed: "
        f"{sorted(WRITE_GOALS)} != {sorted(FROZEN_WRITE_GOALS)}. Per the "
        "frozen implementation dependency plan, no new external-write "
        "capability may be introduced before Phase 8 (the write-chain "
        "migration), which requires the reversibility taxonomy and "
        "Compensation Reservation machinery this codebase does not yet "
        "have. If this change IS the Phase 8 migration, update "
        "FROZEN_WRITE_GOALS here as part of that explicitly-authorized "
        "work — not as an incidental side effect of an unrelated change."
    )
