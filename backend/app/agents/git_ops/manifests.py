"""Manifests for the git operations agents."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_GITHUB_WRITE

# ADR 0011, OD-3 — every git_ops agent's static dependency is the GitHub
# write path (`create_git_write_provider()` / a per-user `GitHubConnection`
# OAuth token — see app.orchestrator.preflight's own docstring, "GitHub —
# git_ops execution path"), never LLM (deterministic, no LLM call) or Neo4j
# (max_graph_hops=0 on every manifest below already says so). Declaring it
# here does not make the existing check BLOCKING — that remains OD-2,
# explicitly undecided and out of scope for this declaration.
_GIT_OPS_REQUIRED_DEPENDENCIES = frozenset({DEPENDENCY_GITHUB_WRITE})

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
    required_dependencies=_GIT_OPS_REQUIRED_DEPENDENCIES,
    # KAN-28 — creates a real branch on GitHub. See _authorization.py for
    # why extras["workflow"] can only ever come from an approved auto_execution.
    requires_external_write_authorization=True,
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
    required_dependencies=_GIT_OPS_REQUIRED_DEPENDENCIES,
    # KAN-28 — writes a real commit to GitHub.
    requires_external_write_authorization=True,
)

RUN_TESTS_MANIFEST = AgentManifest(
    agent_id="run_tests",
    purpose=(
        "Observe repository CI status for a commit. Polls GitHub Check Runs "
        "with exponential backoff. Deterministic, idempotent, no LLM."
    ),
    goals=frozenset({"run_tests"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="TestRunInfo",
    required_dependencies=_GIT_OPS_REQUIRED_DEPENDENCIES,
    # KAN-28 — deliberately NOT flagged: this agent only reads CI status
    # (GitHub Check Runs), it never writes. Needs a workflow context for
    # the same data-dependency reason as the others (which commit to poll),
    # not for write authorization.
)

CREATE_PULL_REQUEST_MANIFEST = AgentManifest(
    agent_id="create_pull_request",
    purpose=(
        "Open a GitHub pull request for the execution branch and persist "
        "it as a PullRequest record. Deterministic, idempotent, no LLM."
    ),
    goals=frozenset({"create_pull_request"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="PullRequestInfo",
    required_dependencies=_GIT_OPS_REQUIRED_DEPENDENCIES,
    # KAN-28 — opens a real pull request on GitHub, the most visible write
    # of the four.
    requires_external_write_authorization=True,
)
