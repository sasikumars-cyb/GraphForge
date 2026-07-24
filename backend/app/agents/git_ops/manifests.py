"""Manifests for the git operations agents."""

from app.agents._contract import AgentManifest

CREATE_BRANCH_MANIFEST = AgentManifest(
    agent_id="create_branch",
    purpose=(
        "Create a dedicated execution branch on GitHub from the default "
        "branch HEAD. Deterministic, idempotent, no LLM."
    ),
    goals=frozenset({"create_branch"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="BranchInfo",
)

COMMIT_CHANGES_MANIFEST = AgentManifest(
    agent_id="commit_changes",
    purpose=(
        "Commit generated files to the execution branch as a single "
        "atomic commit via the Git Data API. Deterministic, idempotent, no LLM."
    ),
    goals=frozenset({"commit_changes"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="CommitInfo",
)
