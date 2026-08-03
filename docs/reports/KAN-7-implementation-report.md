# KAN-7 Implementation Report — Cross-Repository Reasoning / Graph Parity Gaps

**Epic:** [KAN-7](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-7)
**Date:** 2026-08-03
**Status:** Complete (3 of 4 gaps closed; 1 mitigated with an honest interim fix, follow-up scoped)

## Summary

Before writing any code, each of the epic's four child tickets was
re-verified against the current codebase rather than assumed correct from
the original due-diligence review. That verification found the codebase
had moved since the review was written: a `graphforge-validation/docs/`
gap-closure sprint (dated the same day) had already fixed the Kafka and
Feign detection gaps, and the cross-repository traversal fix for the
*newer* Engineering Intelligence Service Layer. What remained genuinely
open was narrower and more precise than the original tickets assumed:

- The **legacy** PR-analysis pipeline (`app/analysis/` — what actually
  backs `POST /pull-requests/{id}/analyze`, the real dashboard-facing
  endpoint) had never had `CALLS_SERVICE` (Feign) cross-repository
  traversal *at all* — a distinct, still-open gap from the one already
  fixed in the newer pipeline.
- Dependency Query's downstream-consumer count is undercounted for a
  structural, partition-scoping reason distinct from the traversal-filter
  bug — closing it fully requires cross-partition search or a reverse
  index, which was deliberately not attempted in this pass to avoid
  guessing at multi-tenancy/account-scoping semantics it would need to get
  right.

## Jira IDs

| Ticket | Outcome |
|---|---|
| [KAN-19](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-19) — Impact Analysis cannot cross repository boundaries | **Fixed.** Added `CALLS_SERVICE` cross-repository traversal to the legacy `ImpactAnalysisEngine` pipeline. The Engineering Intelligence Service Layer's own version of this gap was already closed prior to this session. |
| [KAN-20](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-20) — Feign cross-repository name matching | **Verified already closed.** `cross_repo_linker.py::_normalize` already strips a trailing language/runtime tag. No code change needed; documentation reconciled. |
| [KAN-21](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-21) — Kafka topic detection | **Verified already closed.** Constant/wrapper-delegation resolution and a Python extractor already exist. No code change needed; documentation reconciled. |
| [KAN-22](https://cybage-team-n8wdf7c7.atlassian.net/browse/KAN-22) — Dependency Query counts are intra-repository noise | **Interim mitigation shipped**, full fix scoped as follow-up. `downstream_consumers` is now explicitly labeled as single-repository-scoped in both the API response and UI, rather than presented as an authoritative count. |

## Files changed

**Backend — KAN-19 (legacy cross-repository impact traversal)**
- `backend/app/analysis/graph/interfaces.py` — new abstract method `find_cross_repository_service_callers`
- `backend/app/analysis/graph/neo4j_impact_reader.py` — Cypher implementation
- `backend/app/analysis/engine/impact_analysis_engine.py` — wired into `analyze_pull_request`, guarded by the same "only query when a Component actually changed" pattern already used for topic peers
- `backend/app/analysis/services/dependency_path_builder.py` — new hops rendered as human-readable dependency paths
- `backend/tests/integration/test_impact_analysis_engine.py` — two new integration tests (positive case with a hand-built caller repository + `CALLS_SERVICE` edge; negative case confirming the lookup is skipped when nothing changed)

**Backend — KAN-22 (Dependency Query interim caveat)**
- `backend/app/agents/dependency_query/renderer.py` — `downstream_consumers_scope` / `downstream_consumers_caveat` fields, always present
- `backend/tests/unit/agents/dependency_query/test_renderer.py` — new test asserting the caveat is present in both the empty and non-empty case

**Frontend — KAN-22**
- `frontend/src/pages/DependencyQueryPage.tsx` — renders the caveat under the Downstream Consumers card

**Documentation**
- `docs/handbook/09_VALIDATION_FRAMEWORK.md` — Known Gaps section rewritten to reflect closed/open status accurately, with the legacy-pipeline distinction spelled out
- `docs/handbook/16_REALITY_CHECK.md` — "Partially implemented" table and "Known gaps" section updated to match; closing summary paragraph corrected

## Design decisions

1. **Repository-granularity, not component-granularity, for the new traversal.** `CALLS_SERVICE` edges only ever connect two `Repository` nodes (by construction of `cross_repo_linker.py`) — there is no finer-grained data to traverse. `find_cross_repository_service_callers` is guarded to only run when at least one `Component` actually changed (`direct_service_nodes`), mirroring the existing guard pattern for Kafka topic peers (`topic_ids`), so a whole-repository-level impact statement is only ever shown when something in that repository genuinely changed.
2. **Reused the existing `TraversalHop`/`dependency_path_builder` shapes** rather than inventing a parallel result type — the new hops flow through the exact same `_two_step_path` rendering path `api_hops` already uses, keeping `ImpactAnalysisResult`'s JSON shape unchanged for existing consumers.
3. **Chose an honest interim fix over a risky full fix for KAN-22.** The fully correct fix (cross-partition dependency search) requires deciding how "all repositories in scope" is determined — almost certainly account/organization-scoped, which is exactly the kind of multi-tenancy boundary this session's own due-diligence review flagged as *unverified* elsewhere in the backlog (KAN-33). Building a new cross-partition query without that verification first would risk introducing a real cross-tenant data exposure, not just a wrong count. The interim fix (explicit, honest labeling) closes the "user might trust a wrong number" harm today; the full fix is correctly sequenced behind KAN-33 as a follow-up, not guessed at here.
4. **Did not touch `risk_classifier.py`.** A cross-repository caller existing doesn't change the acceptance criteria for KAN-19 (visibility of blast radius, not a risk-level change), and the existing Controller/Service risk tier already covers the same class of change. Changing risk thresholds was out of scope and not requested.

## Environment note (read before trusting the test run)

This sandbox has **no outbound access to Docker Hub / container registries** (organization policy denial, confirmed via the proxy status endpoint, not retried per instructions) and **no Neo4j package** reachable through the OS package manager. Real Neo4j could not be started here.

What *was* verified directly, in this environment:
- Postgres installed natively (`apt-get install postgresql`) and migrated with `alembic upgrade head` — real database, not mocked.
- `ruff`, `black`, `mypy` — clean on every changed file.
- Full non-Neo4j-dependent backend suite: **1875 passed**, 23 pre-existing failures (all `test_run_coordinator.py`, all due to no LLM provider API key configured in this sandbox — confirmed unrelated to this change, file untouched).
- Full frontend suite: **403 passed** (48 files), `tsc -b --noEmit` clean, `oxlint` clean (pre-existing warnings only, different files), production `vite build` succeeds.
- Python `ast.parse` + live import of every changed module succeeded; the interface/implementation contract was verified by `mypy` (an unimplemented abstract method would have failed type-checking, not just at runtime).

What was **not** executed directly: the two new Neo4j-backed integration tests (`test_feign_caller_repository_is_indirectly_impacted`,
`test_analysis_without_component_change_skips_service_caller_lookup`) and the pre-existing `test_impact_analysis_engine.py` suite. The repository's own CI (`.github/workflows/ci.yml`) runs real Postgres and Neo4j service containers on every push and will execute these on the next push/PR — that is the verification path for the Neo4j-dependent assertions, not a substitute skipped here by choice.

## Risks

- **Unverified against live Neo4j in this session.** The Cypher in `find_cross_repository_service_callers` was written by direct structural analogy to the adjacent, already-tested `find_cross_repository_topic_peers` method (same driver, same session pattern, same property-match style) and reviewed line-by-line, but has not been executed against a real graph here. Flag for a human/CI check on first push.
- **KAN-22 is a mitigation, not a fix.** `downstream_consumers` will continue to undercount until the follow-up (cross-partition search) ships. This is now honestly surfaced rather than hidden, which was the ticket's stated bar for "done," but a stakeholder reading only the API response schema (not the UI) should be told about the new `downstream_consumers_scope` field.
- **`DependencyQueryPage.tsx` has no pre-existing test file.** The new caveat rendering was verified by full-suite pass (no regressions) and manual code review against the existing empty-state pattern in the same file, but has no dedicated component test — this was a pre-existing gap on this page, not one introduced here.

## Remaining work (follow-up tickets, not started)

- **KAN-22 full fix** — cross-partition/reverse-index search for downstream consumers, sequenced behind KAN-33 (multi-tenant isolation audit) per the design decision above.
- **`docs/handbook/12_DIFFICULT_QUESTIONS.md`** still cites the Feign gap as "biggest technical debt" — now stale. Left untouched as it's in scope for KAN-13 (Documentation Integrity epic), not KAN-7.
- A dedicated component test for `DependencyQueryPage.tsx` (pre-existing gap, noted for KAN-12 epic's frontend test-coverage work).
