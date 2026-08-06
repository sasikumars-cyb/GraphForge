# Section 16 — Reality Check

The single most important section in this handbook. Read this before any
review, demo, or judging session. Nothing here is softened; nothing here
is invented. Every row cites its source.

## Implemented and working

| Capability | Evidence |
|---|---|
| Deterministic Java/Spring Boot + Python indexing into Neo4j (tree-sitter) | ADR 0007; `app/indexer/parsers/` |
| Five-stage Knowledge Engine pipeline (Evidence→Hypothesis→Validation→Confidence→Knowledge) | ADR 0018 RFC-01 through RFC-06D, all marked Implemented with dated approval; real code under `app/knowledge_engine/` |
| Engineering Memory (append-only Postgres log) | ADR 0018 RFC-04, implemented 2026-08-02; `app/repositories/engineering_memory_repository.py` |
| `DefaultConfidenceEngine` — deterministic, incremental, monotonic, parity-tested against `cross_repo_linker.py` | ADR 0018 RFC-03; `app/knowledge_engine/confidence/default_engine.py` |
| Cross-repository relationship persistence into Engineering Memory | ADR 0018 RFC-05, implemented 2026-08-02, with a real concurrency bug found and fixed pre-ship |
| Materializer (Engineering Memory → Neo4j projection), replay-tested, shadow-compared on every real index | ADR 0018 RFC-05B; `app/knowledge_engine/materializer.py`. `app/knowledge_engine/shadow_compare.py` (KAN-16) now compares its projection against the direct write on every indexing run and logs `materializer_shadow_compare_match`/`_mismatch` — real production signal, not yet the live write path (see Partial, below) |
| Frontier LLM Hypothesis Generator, shadow mode, gated off by default | ADR 0018 RFC-06, implemented 2026-08-02, `enable_frontier_llm_generator=False` |
| Evidence-keyword validators promoting LLM hypotheses above `CANDIDATE` | ADR 0018 RFC-06B |
| Confidence explainability, persisted | ADR 0018 RFC-06C |
| Learning/feedback engine + 3 REST endpoints (first human approve/reject capability) | ADR 0018 RFC-06D |
| Context Discovery: deterministic investigation loop, evidence curation/tiering | ADR 0007 lineage, ADR 0014, `app/context_pipeline/` |
| Engineering Understanding synthesis (single grounded LLM call, graceful degradation) | ADR 0015 |
| Mid-loop synthesis checkpoint (bounded to 1 extra LLM call) | ADR 0016 |
| Engineering Session aggregate (Beliefs/Hypotheses/Evidence/Recommendations/Decisions/Contradictions), full REST API, 68 new tests | RFC-001, Status: Implemented |
| Orchestrator (registry, selector, run coordinator, preflight, background execution) | `app/orchestrator/*.py`, confirmed present and referenced by every agent manifest |
| 12+ registered agents, each behind a manifest, most sharing `BaseFrontierAgent` | `app/agents/*/manifest.py` |
| Engineering Intelligence Service Layer (6 services, LLM-free, deterministic ordering) | `app/services/engineering_intelligence/*` |
| 24-repository external validation suite, 10 validations, black-box against real APIs | `graphforge-validation/` |

## Partially implemented (real, but with a named boundary)

| Capability | What works | What doesn't (yet) |
|---|---|---|
| Neo4j as "derived projection" | Materializer exists, passes a real replay test, and (KAN-16) now shadow-compares its projection against the direct write on every real indexing run, logging a structured match/mismatch — the "shadow-write/compare before full cutover" step the cutover ticket's own risk note called for | The materializer still is **not called by any production write path** — `replace_repository_graph`/`replace_cross_repository_edges` are still what actually writes Neo4j today. Cutting the live write path over is deliberately sequenced behind a run of real shadow-compare data (this session couldn't accumulate that data — it only proves the comparison logic is correct, not that it has run against production-scale/production-variety repositories yet). |
| Cross-repository knowledge in Engineering Memory | RFC-05 persists cross-repo relationships from the `cross_repo_linker` rules; Feign naming-convention matching (Gap 2) and Kafka topic detection (Gap 1) are both now closed | — |
| Impact Analysis / blast radius | Computes correctly within one repository **and now correctly crosses repository boundaries** in both the Engineering Intelligence Service Layer and the legacy PR-analysis pipeline (Gap 3 closed, KAN-19) | — |
| Dependency Query | Confidence-aware, evidence-backed search works; `direct_dependencies` is accurate | `downstream_consumers` is still intra-repository-only (Known Gap 4, a distinct partition-scoping limitation) — now explicitly labeled as such in the API/UI (KAN-22) rather than presented as an authoritative count |
| Frontier LLM Generator | Mechanism proven end-to-end (generation → shadow persistence → validator promotion) | Off by default; precision/recall/cost has never been measured against real repositories — explicitly deferred by the RFC that shipped it |
| Context Discovery hypothesis-driven investigation | Generates and challenges competing hypotheses in one post-hoc pass; one bounded mid-loop checkpoint can redirect the rest of a run | No full feedback loop where a hypothesis's own unknowns trigger fresh retrieval every cycle — named directly in ADR 0015's self-review as "the single biggest gap between this implementation and the brief's full ambition" |
| Confidence calibration (prompt-version vs. human-agreement tracking) | `GET /api/v1/calibration/summary` (admin-only) computes real per-agent approval-rate-by-confidence-bucket curves from `ConfidenceCalibration` rows, and now a per-`prompt_version` breakdown joined against `AgentStep` — a version whose approval rate diverges from its agent's overall rate by more than 20 points (with ≥5 decisions) is flagged `flagged_miscalibrated` (KAN-23) | No dashboard consumes it yet — the response types exist in the frontend (`frontend/src/types/calibration.ts`) but are wired into zero components; prompt evolution, health scoring, and org-wide learning remain unbuilt, as RFC-06D always scoped them as later phases |
| Shared Memory / `RunContext` | Functions correctly for a single process | Documented as an in-memory stand-in, not the Redis-backed version the architecture calls for; "required before any multi-process/multi-replica deployment" |
| Entry Resolvers / Context Builder generalization | `freetext` and GitHub (repository id, pull request URL) resolvers both exist and are real, working, tested (`app/context/resolvers/freetext.py`, `app/context/resolvers/github.py`, KAN-27) | Jira and Confluence resolvers remain not built — blocked on a real Jira/Confluence integration existing at all (today's Jira access is read-only) |
| Multi-tenant isolation (KAN-33) | The real tenancy boundary — `user_id`-scoped ownership checks, 404-not-403 to close the IDOR-existence-oracle gap — is now independently verified end-to-end for `workflows.py` (12 tests), `agent_runs.py` (7 tests), the one per-user-sensitive path in `knowledge.py` (5 tests), the Postgres-backed slice of `repositories.py` (4 tests), `pull_requests.py` (pre-existing coverage, verified still current), `learning.py` (7 tests, its full surface), `api_intelligence.py` (3 tests), `documentation.py`'s create-PR endpoint (3 tests), `reports.py` (6 tests, including its intentional ownerless-workflow-is-shared behavior), and `ai_analysis.py` (7 tests — every endpoint gates on `_get_owned_pull_request` before any LLM/Neo4j/GitHub call). 64 tests total, all passing. All 26 routers have now been reviewed (not all needed new tests): `ai_workspace.py`/`oauth_apps.py`/`tools.py` are admin-only shared config with no per-user resource (same pattern as `knowledge.py`'s `KnowledgeConnection`); `github.py`/`google_drive.py`'s connection endpoints are self-referential (operate on the calling JWT's own identity, no path-parameter object id, so there is no cross-user reference to make); `test_case_uploads.py`/`testrail.py` are explicitly documented (`app/models/test_case_upload.py`'s own docstring) as intentionally install-wide shared knowledge, not per-user; `webhooks.py` is HMAC-signature-verified, not JWT-scoped, by design; `oauth.py` (KAN-34, now implemented) creates/looks up a `User` by GitHub-verified email with no cross-user reference either — same self-referential shape as `github.py`'s connection endpoints. `parity.py` is the one router genuinely left unverified — Neo4j-dependent, untestable in this sandbox. | `organization_id` — what `ARCHITECTURE.md` previously described as the enforcement mechanism — **does not exist anywhere in the codebase** (now corrected there); the verified `user_id` mechanism is a per-router convention, not a structural guarantee. The sweep surfaced two real, unresolved findings: `engineering_sessions.py` has **no ownership check at all** (KAN-44, blocked on a product decision — private per-investigator vs. shared team workspace); and `find_cross_repository_topic_peers` (used by `repositories.py`, an AI agent tool, and the impact-analysis engine) has **no tenant filter at the Neo4j query level**, so components from another tenant's repository can leak into a topic-peer response when topic names collide (KAN-45, needs a Neo4j-available environment to fix and verify — unavailable in this sandbox). |

## Intentionally deferred (documented scope boundaries, not gaps)

- **Belief promotion (copy-with-provenance) into a System Model aggregate**
  — RFC-001 §5: explicitly Phase 3, out of scope by design.
- **Mission/Organization aggregates** — RFC-001 §5: `mission_id` reserved
  as an unconstrained column; `Contradiction.owner_scope` constrained to
  only `"session"` until those aggregates exist.
- **Policy as a first-class concept** — RFC-001 §5: retention/legal-hold
  implemented directly on `EngineeringArtifact` instead; Policy itself
  named as Phase 7.
- **`RuntimeValidator`/`OwnershipValidator`/`ApiContractValidator`** — ADR
  0018 RFC-06B: deliberately not built because no evidence source for
  runtime traces, git history, or API contracts exists yet; building a
  validator with nothing to validate against is named directly as the
  kind of speculative infrastructure the platform's own implementation
  rules reject.
- **Multi-provider LLM consensus** — RFC-08, roadmap only.
- **Incremental (delta) evidence ingestion for runtime/docs/infra sources**
  — RFC-09, roadmap only; `is_delta` exists on the contract, unused by any
  shipped source.
- **First non-parser language promoted through the generic pipeline** —
  RFC-07, roadmap only.
- **Personalized PageRank for graph-proximity scoring** — ADR 0014:
  `_proximity_score` is written as a drop-in-replaceable seam; flat
  BFS-distance scoring ships instead, deliberately.
- **Frontend rendering of the tiered Evidence Package / Engineering
  Understanding / Investigation Workspace** — ADR 0014/0015: backend
  fields are stable and computed; no UI surfaces them yet.
- **GitHub duplicate-work search** — ADR 0014: `GitHubTool` has no
  search/list-PRs capability; a named prerequisite, not attempted.
- **LLM-based Selector / natural-language Goal inference** — `ROADMAP.md`
  Phase 3, explicitly isolated behind `ISelector` for a drop-in swap;
  today's Selector is a static rule table.
- **A real distributed task queue for indexing** — ADR 0007: `BackgroundTasks`
  is a named stand-in; doesn't survive a process restart mid-run, doesn't
  scale beyond one worker.
- **Incremental (diff-only) re-indexing** — ADR 0007: every run fully
  replaces the prior graph; no history of architectural change over time
  *at the Neo4j layer* (Engineering Memory now provides this at the
  Postgres layer for what it covers, per ADR 0018).
- **Out-of-process plugin protocol for third-party agents** —
  `ARCHITECTURE.md` § Plugin Architecture: explicitly deferred until real
  third-party/customer-authored-agent demand exists, "not before, to avoid
  building marketplace infrastructure for a market of zero external
  developers."

## Known gaps (current, numbered, root-caused — not hypothetical)

Status as of the KAN-7 gap-closure work (2026-08-03). Full technical
detail in [09_VALIDATION_FRAMEWORK.md](09_VALIDATION_FRAMEWORK.md) and
`graphforge-validation/docs/validation-guide.md`.

1. ~~Kafka topic detection~~ — **closed.** Constant/wrapper-delegation
   resolution added to the Java extractor; a Python extractor now exists.
2. ~~Feign cross-repository name matching~~ — **closed.** Normalization now
   strips a trailing language/runtime tag before the existing suffix
   strip, bridging `<domain>-service-<language>` naming.
3. ~~Impact Analysis structurally cannot leave the seed repository~~ —
   **closed in both impact-analysis pipelines.** The Engineering
   Intelligence Service Layer's `get_neighborhood` no longer filters the
   far endpoint of a cross-repository edge to the seed's own
   `repository_id`. Separately, the legacy Phase 7 pipeline
   (`ImpactAnalysisEngine`, what actually backs `POST
   /pull-requests/{id}/analyze`) had never had `CALLS_SERVICE` traversal
   at all — closed via `find_cross_repository_service_callers` (KAN-19).
4. Dependency Query's `downstream_consumers` count is still intra-repository
   only — a distinct, still-open partition-scoping limitation (not the
   traversal-filter bug Gap 3 was). Mitigated, not yet closed: the API and
   UI now explicitly label this as single-repository-scoped
   (`downstream_consumers_scope`/`downstream_consumers_caveat`, KAN-22)
   rather than presenting an undercounted number as authoritative. Closing
   it for real needs a cross-partition search or reverse index — tracked
   as follow-up work, deliberately not attempted alongside the incremental
   fixes above to avoid guessing at multi-tenancy/account-scoping
   semantics.

## Known technical debt

- `GET .../ai-analysis` still doesn't expose `release_coordination_plan`
  — `ROADMAP.md` Technical Debt, carried forward, folded into a planned
  future migration rather than fixed ad hoc.
- Full-clone-per-index doesn't scale past "a handful of repos per org" —
  `ROADMAP.md`, flagged as a Phase-2 prerequisite for multi-repo
  Architecture Agent use at more than demo scale.
- One pre-existing failing test conflicting with real GitHub credentials
  in the dev `.env` — `ROADMAP.md`, needs an explicit env-unset fixture,
  not a rewrite of the assertion.
- `Recommendation.target_contradiction_id` has no foreign key — RFC-001
  §5: would create a genuine circular table dependency; documented,
  unenforced, read-only by design, same pattern as `EngineeringSession
  .mission_id`.

## Roadmap (not built, explicitly forward-looking)

RFC-07 (first non-parser language), RFC-08 (multi-provider consensus),
RFC-09 (incremental evidence ingestion) — ADR 0018. `ROADMAP.md` Phase 2
(Requirement/Planning Agents formalized as sequential-handoff SDLC stages,
Jira/Confluence Entry Resolvers, `Projects`/`Pipeline` UI) and Phase 3
(Development/Testing/Release Agents, LLM-based Selector) — noting that
several individual capabilities named in these phases (a Planning agent, a
Testing agent) already exist as standalone agents ahead of the phase
sequence that originally proposed them; what's *not* yet built is the
sequential-handoff SDLC pipeline connecting them end-to-end as one
continuous flow.

## The most honest one-paragraph summary

GraphForge's actual, current strength is architectural discipline applied
consistently under real implementation pressure — every non-trivial design
choice in this codebase is written down with its rejected alternatives,
every RFC states its own test evidence and rollback plan, and the
validation suite documents its own findings against itself rather than
hiding them. Its actual, current weakness is coverage: the deterministic
core (Java/Spring Boot, Python) is solid, and cross-repository reasoning —
the specific capability the product's positioning leans on hardest
("cross-system reasoning... code ↔ tickets ↔ docs ↔ releases," per
`PRODUCT_VISION.md`'s competitive table) — had four concrete, numbered
holes; three are now closed (Kafka, Feign naming, and Impact Analysis
crossing repository boundaries in both pipelines), leaving one narrower,
explicitly-labeled gap (Dependency Query's downstream-consumer count). The
Jira/Confluence half of "code ↔ tickets ↔ docs" and the materializer that
proves Neo4j is truly derivable from history has not yet been made the
live write path both remain open. A judge or reviewer who asks to see the
Graph Parity dashboard or the validation
suite's own gap list is asking exactly the right question.
