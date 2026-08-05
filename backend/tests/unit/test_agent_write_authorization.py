"""KAN-28 — proves the specific claim `app.agents.git_ops._authorization`
makes: a direct `POST /agent-runs` call can never supply a git_ops write
agent with a usable workflow context, so these agents can only ever run
as part of an `auto_execution` workflow sourced from an approved Planning
blueprint (`workflow_service.create_workflow`'s own validation, exercised
end-to-end in `tests/unit/test_workflow.py`).

This file is deliberately narrow: it tests the *routing* claim (can this
goal ever receive a fabricated workflow context through the standalone
API path), not the agents' own internal guards (already covered by each
agent's `test_missing_workflow_raises` in `tests/unit/ai/
test_git_ops_agents.py` and `tests/unit/ai/test_create_pull_request_agent.py`).
Together, the two prove the full chain: no route in → the agent's own
guard rejects it if it somehow did.
"""

from __future__ import annotations

from app.agents.git_ops._authorization import WRITE_GOALS
from app.api.v1.routers.agent_runs import _PLANNING_CONTEXT_SUPPORTED_GOALS


def test_no_write_goal_can_receive_a_standalone_planning_context() -> None:
    """`_load_standalone_planning_context` is the only mechanism
    `POST /agent-runs` has for populating `extras["workflow"]` outside the
    real workflow-stage runner. If any git_ops write goal were ever added
    to `_PLANNING_CONTEXT_SUPPORTED_GOALS`, a direct API call could start
    supplying these agents with a context shaped enough to pass their
    `workflow is None` guard — reopening exactly the bypass this ticket
    confirmed does not exist today."""
    overlap = WRITE_GOALS & _PLANNING_CONTEXT_SUPPORTED_GOALS
    assert not overlap, (
        f"{overlap} can receive a workflow context via POST /agent-runs's "
        "planning_run_id — this would let a direct API call reach a "
        "GitHub-writing agent without going through an approved "
        "auto_execution blueprint. See app.agents.git_ops._authorization."
    )


def test_write_goals_matches_the_manifests_that_declare_authorization() -> None:
    """Keeps the module-level `WRITE_GOALS` constant honest against the
    manifest declarations `test_manifest_dependency_integrity.py` checks —
    two independently-maintained lists describing the same three agents
    must never silently diverge."""
    from app.agents.git_ops.manifests import (
        COMMIT_CHANGES_MANIFEST,
        CREATE_BRANCH_MANIFEST,
        CREATE_PULL_REQUEST_MANIFEST,
        RUN_TESTS_MANIFEST,
    )

    write_manifests = {
        CREATE_BRANCH_MANIFEST,
        COMMIT_CHANGES_MANIFEST,
        CREATE_PULL_REQUEST_MANIFEST,
    }
    for manifest in write_manifests:
        assert manifest.agent_id in WRITE_GOALS
        assert manifest.requires_external_write_authorization is True

    assert RUN_TESTS_MANIFEST.agent_id not in WRITE_GOALS
