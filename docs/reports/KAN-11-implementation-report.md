# KAN-11 Implementation Report — Security & Multi-Tenancy Hardening

**Epic:** [KAN-11](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-11)
**Date:** 2026-08-04
**Status:** Partial (2 of 3 tickets substantively resolved; 1 deferred pending a product decision)

## Summary

This epic set out to verify the multi-tenant isolation claims `ARCHITECTURE.md` makes but the original due-diligence review flagged as unverified. The investigation found something more significant than "unverified": **`organization_id` — the specific mechanism `ARCHITECTURE.md` names as enforcing tenant isolation — does not exist anywhere in the codebase.** Grepped across every model and every Neo4j write path: zero matches. This wasn't a partially-built feature; it was a design-intent placeholder that several other handbook documents had, over time, started citing as if it were already implemented.

The real, working tenancy boundary is `user_id`, enforced per-router via ownership checks. That mechanism is real, was code-reviewed, and has now been independently verified end-to-end for the most consequential router in the app — `workflows.py`, whose `approve`/`reject` endpoints are the one authorization gate standing between a request and a real GitHub write (KAN-28). A full 26-router sweep is a larger undertaking than fits in one incremental pass; this session closes the highest-risk slice and leaves the rest as explicitly scoped follow-up, not silently declared done.

KAN-35 turned out to already be fully implemented, tested end-to-end, and wired into the investigation agent — the ticket's premise ("`get_diff` remains unimplemented") was stale documentation, not a real gap, the same pattern seen repeatedly across this session's epics.

KAN-34 requires a product decision (ship login-via-GitHub, or delete the dead stub) that only a human can make — asked directly, not guessed at, and left open pending an answer.

## Jira IDs

| Ticket | Outcome |
|---|---|
| [KAN-33](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-33) — Multi-tenant isolation audit | **Partially resolved.** False `organization_id` claim corrected in 5 documents. Full HTTP-level cross-user isolation test suite written and passing for `workflows.py` (12 tests, including the approve/reject/auto-execution-source paths). Remaining 25 routers not yet swept — tracked as explicit follow-up, not silently assumed safe. |
| [KAN-34](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-34) — Login-via-GitHub dead stub | **Deferred.** Asked the user directly (build vs. remove) via a structured question; not answered before this session continued. Left untouched rather than guessed at — this is exactly the class of decision the mission's "if blocked, don't guess" rule exists for. |
| [KAN-35](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-35) — `get_diff` unimplemented | **Verified already implemented.** `GitHubVersionControlProvider.get_diff` is real (fetches the actual unified diff via GitHub's `.diff` media type) and already wired into `ReadGitDiffTool`, called for non-trivial-risk PRs. Stale doc claim corrected; added focused unit coverage for the one untested piece (the truncation boundary). |

## Files changed

**KAN-33**
- `backend/tests/integration/test_workflows_cross_user_isolation.py` — new; 12 HTTP-level tests proving User B gets 404 (never 403 — closes the IDOR-existence-oracle gap) on every `workflows.py` endpoint when acting on User A's workflow, including the two most consequential: `approve` and the auto_execution `source_workflow_id` reference check
- `docs/graphforge/ARCHITECTURE.md` — 3 corrected sections (Scalability Considerations, Logging, Security Considerations) replacing the false `organization_id` claim with the verified `user_id` mechanism and its actual scope (per-router convention, not yet a structural guarantee)
- `docs/handbook/16_REALITY_CHECK.md` — new row for multi-tenant isolation status
- `docs/handbook/12_DIFFICULT_QUESTIONS.md` — "How does it scale?" answer corrected
- `docs/handbook/11_REVIEW_QUESTIONS.md` — Q101 and Q137 corrected

**KAN-35**
- `backend/tests/unit/ai/test_read_git_diff_tool.py` — new; 4 tests covering `ReadGitDiffTool`'s truncation boundary (under/at/over budget) and its call contract, the one piece of already-shipped `get_diff` functionality with no prior dedicated test
- `docs/architecture/overview.md` — corrected the `IVersionControlProvider` status row

## Design decisions

1. **Corrected the documentation before writing new enforcement.** A false claim that tenant isolation is enforced is worse than an honest "unverified" — it actively discourages anyone from checking. Fixing the record was the first, non-negotiable step, done in the same change as the code verification per the project's own documented discipline ("if you find a discrepancy, trust the code, then fix the discrepancy" — `docs/deployment/README.md`, now explicitly generalized).
2. **Scoped the isolation sweep to `workflows.py`, not all 26 routers.** The ticket's own acceptance criteria ask for full REST-endpoint coverage — a real 13-point undertaking. Given this session's time budget, the highest-value, most defensible slice was chosen deliberately: `workflows.py` is where a cross-tenant leak would be most damaging (it gates real GitHub writes via KAN-28's authorization work), so proving it first, rigorously, beats a shallow pass across all 26 routers. The remaining routers are named explicitly as open work, not folded into an implicit "done."
3. **KAN-34 was asked, not decided.** Every other decision in this session was groundable in code (verify, then act). This one is a genuine product call with no code-derivable answer — the mission's explicit "if blocked, don't guess" instruction was followed literally: asked via a structured question, and left untouched when it went unanswered rather than picking an option to keep moving.
4. **KAN-35: added the one missing test, didn't rebuild what already worked.** `get_diff` and its consuming tool were both real, both already exercised end-to-end via `test_investigation_agent.py`. The only real gap was the truncation boundary's own isolated coverage — a small, precise addition rather than a wholesale "implement get_diff" effort the ticket's original framing implied was needed.

## Environment note

Same as prior epics in this session: Neo4j unavailable (Docker registry blocked by org policy), Postgres available and used for every test in this epic's scope (all workflow-lifecycle and diff-tool tests are Neo4j-free by construction). Postgres itself dropped twice during this session due to sandbox idle gaps between conversation turns — restarted cleanly both times, unrelated to any code change, confirmed via `postgresql-16-main.log`.

- `ruff`, `black`, `mypy` — clean on every changed file
- Full non-integration backend suite: **1883 passed** (up from 1879 at KAN-9's baseline — +4 net new tests from this epic), same 23 pre-existing, unrelated `test_run_coordinator.py` failures
- New integration suite (`test_workflows_cross_user_isolation.py`): **12 passed**
- New unit suite (`test_read_git_diff_tool.py`): **4 passed**

## Risks

- **KAN-33 is not epic-complete.** 25 of 26 routers have not had a dedicated cross-user isolation test written in this pass. The workflows.py sweep found the pattern (ownership check before every operation) is applied consistently there; it is reasonable to expect the same discipline elsewhere given the code-review evidence gathered (6+ routers already have their own local `_get_owned_*` helpers), but "reasonable to expect" is exactly the gap between convention and structure this ticket exists to close. Treat the remaining 25 routers as unverified, not as verified-by-extension.
- **The `user_id` ownership mechanism remains a per-router convention, not a structural guarantee.** Nothing today would stop a new Cypher-touching endpoint from being written without the preceding ownership check — `ARCHITECTURE.md`'s corrected Security Considerations section names this explicitly as the real follow-up (a shared, structurally-required dependency that makes an unscoped query impossible to write).
- **KAN-34 is fully unstarted**, blocked on a product decision.

## Remaining work

- Sweep the remaining 25 routers for cross-user isolation coverage, prioritizing by data sensitivity (repositories/pull_requests already have partial coverage per the pre-existing test suite; `agent_runs.py`, `knowledge.py`, and `engineering_sessions.py` are the next-highest-value targets given they touch the same kind of consequential, mutating actions `workflows.py` does).
- Extract the duplicated `_get_owned_*` pattern into a shared, structurally-required FastAPI dependency, closing the "convention, not structure" gap `ARCHITECTURE.md` now names explicitly.
- KAN-34, once the product decision is made.
