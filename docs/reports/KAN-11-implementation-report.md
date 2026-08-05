# KAN-11 Implementation Report — Security & Multi-Tenancy Hardening

**Epic:** [KAN-11](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-11)
**Date:** 2026-08-04
**Status:** KAN-33 sweep complete (all 26 routers reviewed); KAN-35 resolved; KAN-34 deferred pending a product decision

## Summary

This epic set out to verify the multi-tenant isolation claims `ARCHITECTURE.md` makes but the original due-diligence review flagged as unverified. The investigation found something more significant than "unverified": **`organization_id` — the specific mechanism `ARCHITECTURE.md` names as enforcing tenant isolation — does not exist anywhere in the codebase.** Grepped across every model and every Neo4j write path: zero matches. This wasn't a partially-built feature; it was a design-intent placeholder that several other handbook documents had, over time, started citing as if it were already implemented.

The real, working tenancy boundary is `user_id`, enforced per-router via ownership checks. That mechanism is real and has now been independently verified end-to-end across all 26 REST routers — 10 with dedicated HTTP-level cross-user isolation test suites (64 tests total), the remaining 16 verified safe by code-level review (admin-shared config, self-referential-only endpoints, or explicitly documented shared resources) except one (`parity.py`) that remains genuinely unverified because it's Neo4j-dependent and this sandbox has no Neo4j access.

The sweep itself surfaced two real, previously-undocumented findings, both filed as new tickets rather than fixed blind: **KAN-44** (`engineering_sessions.py` has no ownership check anywhere — blocked on a product decision about whether Sessions are private or shared) and **KAN-45** (a Neo4j query with no tenant filter can leak another tenant's component data when Kafka topic names collide — needs a Neo4j-available environment to fix safely).

KAN-35 turned out to already be fully implemented, tested end-to-end, and wired into the investigation agent — the ticket's premise ("`get_diff` remains unimplemented") was stale documentation, not a real gap, the same pattern seen repeatedly across this session's epics.

KAN-34 requires a product decision (ship login-via-GitHub, or delete the dead stub) that only a human can make — asked directly, not guessed at, and left open pending an answer.

## Jira IDs

| Ticket | Outcome |
|---|---|
| [KAN-33](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-33) — Multi-tenant isolation audit | **Sweep complete.** False `organization_id` claim corrected in 5 documents. All 26 routers reviewed; 10 have dedicated HTTP-level cross-user isolation suites (64 tests, all passing); 15 more verified safe by code review (admin-shared config, self-referential endpoints, explicitly-documented shared resources, or signature-verified webhooks); `parity.py` remains unverified (Neo4j-dependent, no Neo4j access in this sandbox). Summary posted as a KAN-33 comment with the full per-router breakdown. |
| [KAN-34](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-34) — Login-via-GitHub dead stub | **Deferred.** Asked the user directly (build vs. remove) via a structured question; not answered before this session continued. Left untouched rather than guessed at. |
| [KAN-35](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-35) — `get_diff` unimplemented | **Verified already implemented.** `GitHubVersionControlProvider.get_diff` is real and already wired into `ReadGitDiffTool`. Stale doc claim corrected; added focused unit coverage for the one untested piece (the truncation boundary). |
| KAN-44 (new, filed this epic) — Engineering Sessions have no ownership check | **Filed, not implemented.** `engineering_sessions.py`'s 27 endpoints have zero `user_id` scoping; `EngineeringSession` has no such column and RFC-001 never states whether Sessions are meant to be private or shared. Blocked on a product decision. |
| KAN-45 (new, filed this epic) — Cross-tenant Neo4j topic-peer leak | **Filed, not implemented.** `find_cross_repository_topic_peers` has no tenant filter at the graph-query level across its 4 call sites. Needs a Neo4j-available environment to fix and verify — this sandbox has none. |

## Files changed

**KAN-33 (across the full sweep)**
- `backend/tests/integration/test_workflows_cross_user_isolation.py` — 12 tests
- `backend/tests/integration/test_agent_runs_cross_user_isolation.py` — 7 tests
- `backend/tests/integration/test_knowledge_connections_cross_user_isolation.py` — 5 tests
- `backend/tests/integration/test_repositories_cross_user_isolation.py` — 4 tests
- `backend/tests/integration/test_learning_cross_user_isolation.py` — 7 tests
- `backend/tests/integration/test_api_intelligence_cross_user_isolation.py` — 3 tests
- `backend/tests/integration/test_documentation_cross_user_isolation.py` — 3 tests
- `backend/tests/integration/test_reports_cross_user_isolation.py` — 6 tests
- `backend/tests/integration/test_ai_analysis_cross_user_isolation.py` — 7 tests
- `docs/graphforge/ARCHITECTURE.md` — 3 corrected sections replacing the false `organization_id` claim
- `docs/handbook/16_REALITY_CHECK.md` — multi-tenant isolation row, updated incrementally across the sweep, now reflecting all 26 routers' final status
- `docs/handbook/12_DIFFICULT_QUESTIONS.md`, `docs/handbook/11_REVIEW_QUESTIONS.md` — corrected

**KAN-35**
- `backend/tests/unit/ai/test_read_git_diff_tool.py` — 4 tests
- `docs/architecture/overview.md` — corrected the `IVersionControlProvider` status row

## Design decisions

1. **Corrected the documentation before writing new enforcement.** A false claim that tenant isolation is enforced is worse than an honest "unverified" — it actively discourages anyone from checking.
2. **Swept routers in order of consequence, not alphabetically.** `workflows.py` (gates real GitHub writes) and `agent_runs.py` (the standalone-planning-context path KAN-9 also touches) went first; routers with no per-user resource at all (admin-shared config, self-referential connection endpoints) were verified last, by code review rather than new tests, once the pattern across the write-heavy routers was well-established.
3. **Didn't write tests asserting unverified intent.** `engineering_sessions.py`'s complete lack of ownership checks could theoretically be intentional (a shared collaborative workspace) or a real gap — RFC-001 doesn't say. Rather than writing tests that assert either interpretation as "correct," this was filed as KAN-44 and left for a product decision.
4. **Didn't attempt a blind multi-call-site Neo4j fix.** KAN-45's root cause (`find_cross_repository_topic_peers` has no tenant filter) has 4 call sites across the router, an AI agent tool, and the impact-analysis engine, each needing the caller's legitimate repository scope threaded in differently. Without a live Neo4j to verify against, guessing at that refactor risked breaking real cross-repository dependency-discovery behavior for a fix that couldn't be tested. Documented thoroughly (including the widened scope, found via a follow-up comment on the ticket) instead.
5. **KAN-34 was asked, not decided.** Every other decision in this session was groundable in code (verify, then act). This one is a genuine product call with no code-derivable answer.
6. **KAN-35: added the one missing test, didn't rebuild what already worked.**

## Environment note

Neo4j unavailable throughout (Docker registry blocked by org policy) — every new test in this epic is Neo4j-free by construction; routers/endpoints that do touch Neo4j (`parity.py`, the graph/architecture endpoints on `repositories.py`, `run_ai_analysis`/`investigate` on `ai_analysis.py`) were either scoped out of the HTTP test (ownership check runs before the Neo4j call, so a 404 test is still valid and safe) or left explicitly unverified (`parity.py`). Postgres dropped multiple times during this session due to sandbox idle gaps between conversation turns — restarted cleanly every time, unrelated to any code change.

- `ruff`, `black`, `mypy` — clean on every changed file across the whole sweep
- Full non-integration backend suite: **1883 passed** throughout (unchanged baseline — this epic added integration coverage only), same 23 pre-existing, unrelated `test_run_coordinator.py` failures
- All 10 new cross-user-isolation suites + `test_calibration_api.py`: **64 passed**, run together with zero cross-file interference

## Risks

- **`parity.py` remains genuinely unverified.** Code review shows `_get_owned_repository` gates its one endpoint, matching the pattern verified everywhere else — but this sandbox cannot prove it end-to-end. Recommend a CI/staging run with real Neo4j access before treating this router as closed.
- **The `user_id` ownership mechanism remains a per-router convention, not a structural guarantee.** Nothing today would stop a new Cypher-touching endpoint from being written without the preceding ownership check.
- **KAN-44 is a live, real gap if the answer turns out to be "private."** Every authenticated user can currently read and mutate every Engineering Session, including another user's evidence, hypotheses, and decisions.
- **KAN-45 is a live, real cross-tenant leak** if any two tenants' repositories happen to use a Kafka topic with the same name.
- **KAN-34 is fully unstarted**, blocked on a product decision.

## Remaining work

- Get a product decision recorded for KAN-44 and KAN-34.
- Fix KAN-45 in a Neo4j-available environment, threading the caller's legitimate repository scope through all 4 call sites.
- Verify `parity.py`'s ownership gating end-to-end once Neo4j is reachable.
- Extract the duplicated `_get_owned_*` pattern into a shared, structurally-required FastAPI dependency, closing the "convention, not structure" gap.
