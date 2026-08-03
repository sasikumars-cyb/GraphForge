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
| Materializer (Engineering Memory → Neo4j projection), replay-tested | ADR 0018 RFC-05B; `app/knowledge_engine/materializer.py` — **not yet wired into any live write path** (see Partial, below) |
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
| Neo4j as "derived projection" | Materializer exists and passes a real replay test | The materializer is **not called by any production write path** — `replace_repository_graph`/`replace_cross_repository_edges` are still what actually writes Neo4j today. The architectural inversion is proven possible, not yet cut over. |
| Cross-repository knowledge in Engineering Memory | RFC-05 persists cross-repo relationships from the `cross_repo_linker` rules | Feign-based `CALLS_SERVICE` detection has a real naming-convention gap (suite Known Gap 2) that makes this 0 edges for a realistic naming scheme; Kafka `SHARES_TOPIC` detection has a similar literal-only gap (Known Gap 1) |
| Impact Analysis / blast radius | Computes correctly within one repository | Structurally cannot cross repositories today — the traversal filter requires both edge endpoints in the same `repository_id` (suite Known Gap 3) |
| Dependency Query | Confidence-aware, evidence-backed search works | "Direct dependencies" and "downstream consumers" counts are not meaningful yet — see Known Gap 4 |
| Frontier LLM Generator | Mechanism proven end-to-end (generation → shadow persistence → validator promotion) | Off by default; precision/recall/cost has never been measured against real repositories — explicitly deferred by the RFC that shipped it |
| Context Discovery hypothesis-driven investigation | Generates and challenges competing hypotheses in one post-hoc pass; one bounded mid-loop checkpoint can redirect the rest of a run | No full feedback loop where a hypothesis's own unknowns trigger fresh retrieval every cycle — named directly in ADR 0015's self-review as "the single biggest gap between this implementation and the brief's full ambition" |
| Confidence calibration (prompt-version vs. human-agreement tracking) | The Learning Engine now captures the raw feedback data this needs | Calibration itself, prompt evolution, health scoring, org-wide learning — none implemented; explicitly named as reading-ready, not built (RFC-06D) |
| Shared Memory / `RunContext` | Functions correctly for a single process | Documented as an in-memory stand-in, not the Redis-backed version the architecture calls for; "required before any multi-process/multi-replica deployment" |
| Entry Resolvers / Context Builder generalization | A `freetext` resolver exists | `GitHubEntryResolver`/`JiraEntryResolver`/`ConfluenceEntryResolver` described in `ARCHITECTURE.md` are not present in `app/context/resolvers/` as of this audit |

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

All four from the validation suite's own findings, detailed in
[09_VALIDATION_FRAMEWORK.md](09_VALIDATION_FRAMEWORK.md):

1. Kafka topic detection — literal-string-only, no shared-SDK-wrapper
   support, no Python extractor at all.
2. Feign cross-repository name matching — suffix-only normalization can't
   bridge a `<domain>-service-<language>` naming convention.
3. Impact Analysis structurally cannot leave the seed repository (traversal
   filter bug, not a missing feature).
4. Dependency Query's direct/downstream counts are intra-repository noise
   today (same root cause surfaces as Validation 7 Parity failures on
   affected repos).

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
core (Java/Spring Boot, Python) is solid, but cross-repository reasoning —
the specific capability the product's positioning leans on hardest
("cross-system reasoning... code ↔ tickets ↔ docs ↔ releases," per
`PRODUCT_VISION.md`'s competitive table) — has four concrete, numbered
holes today, and the materializer that proves Neo4j is truly derivable
from history has not yet been made the live write path. A judge or
reviewer who asks to see the Graph Parity dashboard or the validation
suite's own gap list is asking exactly the right question.
