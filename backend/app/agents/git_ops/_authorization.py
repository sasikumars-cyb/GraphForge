"""KAN-28 — the external-write authorization audit, made durable in code.

`ARCHITECTURE.md`'s Security Considerations state that an agent action
reaching an external system (posting a GitHub comment, committing code,
opening a PR) "must be classed and gated as such, not treated as an
implicit side effect of running the agent." This module is the answer to
"is that actually true today" for every agent that currently writes
outside GraphForge — not a new enforcement mechanism, since investigating
the real call graph found none was missing.

## The full inventory (grepped across `app/agents/` and
## `app/context_pipeline/` at audit time; re-run the same search if this
## module's `WRITE_GOALS` set below ever looks stale)

- **`create_branch`, `commit_changes`, `create_pull_request`**
  (`app.agents.git_ops`) — real GitHub writes (a branch, a commit, a pull
  request). Covered by this module.
- **Jira** — `JiraIssueTrackerAdapter.create_issue`
  (`app.tools.implementations.jira_tool`) raises `NotImplementedError`
  ("Jira issue creation is not implemented (read-only today)"). Nothing
  to gate; this module intentionally does not mention Jira further, and
  should be revisited when that changes (see KAN-43).
  Every other Jira/Confluence code path (`JiraInvestigator`,
  `ConfluenceInvestigator`, `confluence_context.py`,
  `DependencyQueryAgent`/`RepositoryProfileService` narrative prompts) is
  read-only: fetches an issue/page for context, never posts back.
- **`run_tests`** (`app.agents.git_ops.run_tests_agent`) — reads GitHub
  Check Runs. Not a write; deliberately excluded from `WRITE_GOALS` (see
  its manifest's own comment).

## Why the three write agents are already structurally gated

Each of `create_branch_agent.py`/`commit_changes_agent.py`/
`create_pull_request_agent.py` requires `context.extras["workflow"]` to be
populated with real, completed prior-stage results (`generate_code`,
`create_branch`, ...) before it will do anything — this was written as a
data dependency (the agent needs a commit message, a branch name, ...),
but it also happens to be the only thing standing between "agent is
invoked" and "GitHub is written to." The question this audit had to
answer honestly: can that context ever be supplied by anything other than
a legitimately human-authorized run?

Two independent facts, both verified directly against the code (not
assumed), say no:

1. **`workflow_service.create_workflow`** refuses to create an
   `auto_execution`-typed workflow (the only workflow type whose stage
   runner ever calls these agents) unless `source_workflow_id` references
   a Planning workflow with `status == "approved"` — and the only path to
   that status is the real, authenticated `POST /workflows/{id}/approve`
   endpoint (`workflow_service.approve_workflow`). This is the actual
   "explicit permission" gate: a human approves a blueprint before any
   execution workflow referencing it can even be created.
2. **`POST /agent-runs`** (`agent_runs.create_run`) — the one other way to
   start any agent, including these three, by `goal` — can only attach a
   workflow-shaped `extras["workflow"]` via `planning_run_id`, and
   `_load_standalone_planning_context` restricts that to
   `_PLANNING_CONTEXT_SUPPORTED_GOALS` (`develop_change_plan`,
   `plan_tests`) — none of `WRITE_GOALS` below. A direct
   `POST /agent-runs` call with `goal="create_pull_request"` (or the
   other two) always runs with `extras["workflow"]` absent, and each
   agent's own guard raises immediately, before any GitHub call. There is
   no request shape that reaches `vcs.create_pull_request`/
   `vcs.create_commit`/`vcs.create_branch` outside the approved-blueprint
   path today.

`tests/unit/ai/test_manifest_dependency_integrity.py` keeps this honest
going forward: every manifest declaring `DEPENDENCY_GITHUB_WRITE` in
`required_dependencies` must also declare
`requires_external_write_authorization=True` (see `AgentManifest` in
`app.agents._contract`), so a future write-capable agent can't add the
infrastructure dependency without also declaring — and therefore being
checked for — the authorization property this module documents.
"""

from __future__ import annotations

WRITE_GOALS: frozenset[str] = frozenset({"create_branch", "commit_changes", "create_pull_request"})
