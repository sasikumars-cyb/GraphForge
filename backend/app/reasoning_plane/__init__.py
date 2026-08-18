"""The Reasoning Plane — Reasoning Engine contract §5.

Phase 7 (minimal integration): the first, deliberately minimal
implementation. Produces exactly one Plan/PlanStep/ActionProposal per
Goal, via one fixed, deterministic rule — architectural proof that the
Phase 1-6 stack can execute end-to-end, not an intelligent planner. See
`app.reasoning_plane.plane.ReasoningPlane`'s own docstring for the full
scope boundary.
"""
