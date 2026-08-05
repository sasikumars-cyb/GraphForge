# KAN-9 Implementation Report — Agent Framework & Orchestration Hardening

**Epic:** [KAN-9](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-9)
**Date:** 2026-08-04
**Status:** Partial (2 of 3 tickets resolved; 1 genuinely blocked — see `BLOCKER.md`)

## Summary

This epic closes the gap between the Agent Framework's documented design and its current implementation. Two of the three tickets were investigated, verified against live code, and resolved. The third (KAN-26) requires empirically measuring investigation quality against a real LLM provider — a hard requirement of its own Definition of Done, not something this sandbox (no LLM provider configured) can produce. Rather than guess at a design change to a 2,300-line reasoning engine with a documented cost-blowup risk and no way to verify the fix, it's written up as `BLOCKER.md` and left for an environment that can run the measurement.

The two completed tickets both turned out to be narrower and more concrete than their original framing suggested, once verified against the actual codebase — the same pattern seen in the KAN-7 epic:

- **KAN-28** assumed no permission gate existed for agent actions that write to GitHub. Investigation found a real, structural one already does (an `auto_execution` workflow can only be created from a Planning blueprint with `status == "approved"`, itself only reachable through the authenticated `POST /workflows/{id}/approve` endpoint) — but it was *incidental* (an emergent property of a data dependency), not *declared*. The work was making it declared, enumerable, and tested, not building new enforcement.
- **KAN-27** assumed `GitHubEntryResolver` didn't exist at all. Investigation found the real logic already existed and was tested — just as two private helper functions inside `app.api.v1.routers.agent_runs`, not in the documented `app/context/resolvers/` location `ARCHITECTURE.md` describes. The work was extracting and relocating them, not building a new resolver.

## Jira IDs

| Ticket | Outcome |
|---|---|
| [KAN-28](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-28) — Audit agent actions reaching external systems for permission-gating | **Resolved.** Full inventory documented in code (`app/agents/git_ops/_authorization.py`); `AgentManifest.requires_external_write_authorization` field added and declared on the three real write agents; registry-invariant tests added; a concrete test proves the one alternate API path (`POST /agent-runs`) cannot supply these agents with a usable authorization context. |
| [KAN-27](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-27) — Entry Resolvers documented but missing | **Resolved** (GitHub portion; Jira portion correctly out of scope). `app/context/resolvers/github.py` created, consolidating the previously router-local resolution logic. `ARCHITECTURE.md` and `16_REALITY_CHECK.md` updated with an accurate resolver status table. `JiraEntryResolver` remains not built, per the ticket's own stated dependency on a real Jira integration (KAN-43) not existing yet — correctly not attempted. |
| [KAN-26](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-26) — Context Discovery feedback loop | **Blocked.** See `BLOCKER.md`. Requires measuring investigation quality against a real LLM provider; this sandbox has none configured. |

## Files changed

**KAN-28**
- `backend/app/agents/_contract.py` — new `AgentManifest.requires_external_write_authorization` field
- `backend/app/agents/git_ops/manifests.py` — declares the field on all four git_ops manifests (`True` for the three writers, explicitly `False` with a documented reason for `run_tests`)
- `backend/app/agents/git_ops/_authorization.py` — new; the full external-write inventory and the "why this is already safe" audit finding, made durable in code
- `backend/tests/unit/ai/test_manifest_dependency_integrity.py` — two new invariant tests
- `backend/tests/unit/test_agent_write_authorization.py` — new; proves the standalone `POST /agent-runs` path structurally cannot authorize a git_ops write agent

**KAN-27**
- `backend/app/context/resolvers/github.py` — new; `resolve_repository_id`/`resolve_pull_request_url`, moved verbatim from the router
- `backend/app/api/v1/routers/agent_runs.py` — the two functions and the PR-URL regex removed; now imports and calls the new module; 6 now-unused imports removed
- `backend/tests/unit/test_agent_runs_pr_url_resolution.py` — import and mock-patch targets updated to the new module location
- `docs/graphforge/ARCHITECTURE.md` — Entry Resolver status table added
- `docs/handbook/16_REALITY_CHECK.md` — Entry Resolvers row updated to match

**KAN-26**
- `BLOCKER.md` — new (repo root)

## Design decisions

1. **KAN-28: declare, don't rebuild.** The real protection (approved-blueprint-gated `auto_execution` workflows) already existed and was already safe, verified by reading `workflow_service.py`'s `create_workflow`/`approve_workflow` and tracing exactly how `context.extras["workflow"]` can and cannot be populated. Modifying `RunCoordinator`'s execution path to add a second enforcement layer would have been higher-risk (touches every agent, not just git_ops) for no correctness gain — the ticket's own risk note flags exactly this ("closing a real gate-bypass gap could change existing agent behavior"). Since no real gap existed to close, the safer and more valuable move was making the existing, already-correct property declared and tested, so it can't *silently* regress later.
2. **KAN-28: `run_tests` deliberately excluded from the authorization flag.** It needs the GitHub write credential to authenticate (hence `DEPENDENCY_GITHUB_WRITE` in its manifest) but only reads Check Runs — never writes. A test pins this distinction explicitly (`test_run_tests_is_the_one_git_ops_agent_that_does_not_write`) so the exception can't silently grow to cover an agent that actually does write.
3. **KAN-27: moved code, didn't rewrite it.** Both resolver functions were copied verbatim (docstrings included) from their router-local originals — this is a relocation to close a documentation/discoverability gap, not new resolver logic. Both functions already had solid test coverage; that coverage moved with them rather than being reauthored.
4. **KAN-27: no `IEntryResolver` Protocol introduced.** `ARCHITECTURE.md`'s original sketch describes resolvers as classes behind a shared interface. The codebase's actual, working convention (established by `freetext.py`, followed by the new `github.py`) is a plain typed function. Two working resolvers sharing a similar signature is not yet evidence a formal shared interface is worth its own abstraction — noted explicitly in the updated `ARCHITECTURE.md` rather than silently deviating from the documented design without saying so.
5. **KAN-26 not attempted rather than partially attempted.** A flagged-off, unmeasured implementation would still leave the ticket's actual requirement (a measured, evidence-backed default-on decision) undone, while adding real surface area to a 2,300-line reasoning engine with a documented cost-blowup risk. Writing the blocker up honestly was judged more valuable than shipping something that looks complete but isn't.

## Environment note

Same sandbox limitations as the KAN-7 report apply: no Docker registry access, no Neo4j. All changes in this epic are Neo4j-independent (pure Python/Postgres-backed or doc-only), so this did not block verification the way it did for KAN-7 — every test in this epic's scope ran directly.

- `ruff`, `black`, `mypy` — clean on every changed file (confirmed individually and via whole-module checks: `mypy app/agents`, `mypy app/api/v1/routers/agent_runs.py app/context`)
- Full non-integration backend suite: **1879 passed** (up from 1875 at KAN-7's baseline — the +4 net new tests from this epic), same 23 pre-existing, unrelated `test_run_coordinator.py` failures (missing LLM provider key)
- Full test collection: 2226 tests collected, zero collection errors (confirms the router refactor didn't break any import elsewhere)

## Risks

- **KAN-27's router refactor touched a widely-imported file** (`agent_runs.py`). Mitigated by: running full test collection (2226 tests, no errors) and the full non-integration suite (no new failures) after the change, plus `mypy` confirming no type errors across the router and the new module.
- **KAN-28's audit is a snapshot.** `app/agents/git_ops/_authorization.py`'s docstring says explicitly to re-run the same grep if `WRITE_GOALS` ever looks stale — the registry-invariant tests are the durable enforcement; the docstring is context, not a guarantee that holds itself.
- **KAN-26 remains fully unstarted.** The epic is not complete. This is the one place in this session's work so far where "keep going" genuinely can't proceed without an external input (a configured LLM provider) rather than more engineering time.

## Remaining work

- KAN-26, once an LLM-provider-configured environment is available — see `BLOCKER.md` for the specific recommendation.
- `JiraEntryResolver` (KAN-27's explicitly out-of-scope portion), once KAN-43 (live Jira integration) ships.
- Consider whether `app/agents/documentation/agent.py`'s `resolve_repository_subject` and `app/agents/review_adapter.py`'s `resolve_pr_subject` — the two functions the new GitHub resolver still delegates to — should themselves eventually move into `app/context/resolvers/github.py` for full consolidation. Left alone in this pass since they're each used by their own agent beyond just resolution, and moving them was not needed to close KAN-27.
