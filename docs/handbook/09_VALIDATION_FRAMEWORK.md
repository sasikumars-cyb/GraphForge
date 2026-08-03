# Section 9 — Validation Framework

Source: `graphforge-validation/docs/validation-guide.md`, in full.

## What it is

The permanent acceptance test suite for GraphForge's Engineering
Intelligence Platform. Runs a 24-repository validation ecosystem through
GraphForge's real APIs and checks the result against captured expected
state (`validation/expected_*.yaml`). Lives in a separate top-level
directory (`graphforge-validation/`), not inside the backend deployable.

## What it explicitly is not

It "does not reimplement any GraphForge logic." Every asserted fact comes
from one of: GraphForge's own REST API (`lib/client.py`), or GraphForge's
own `EngineeringMemoryService` called directly in-process (`lib/memory.py`
— the identical class `app/api/v1/routers/*.py` uses). No Cypher, no
manual SQL, no relationship-matching or confidence logic lives in the
framework itself. GraphForge is treated as a black box; the framework only
asks it questions and diffs the answers against the fixture files.

## The ten validations

| # | Name | What it checks |
|---|---|---|
| 1 | Repository Graph | Node/edge/endpoint/dependency counts, Kafka topics, Feign clients, per supported repo; unsupported repos correctly rejected |
| 2 | Cross-Repository Relationships | `CALLS_SERVICE`/`SHARES_TOPIC`/`DEPENDS_ON_REPOSITORY` edges, PASS/MISSING/UNEXPECTED |
| 3 | Repository Understanding Agent | Live agent run; deterministic fields exact-match, narrative fields keyword-match |
| 4 | Dependency Query Agent | Live agent run; direct/reverse dependency counts, confidence breakdown |
| 5 | Impact Analysis Agent | Live agent run; blast radius, hop count, confidence, affected repos |
| 6 | Engineering Memory | Relationships persisted, confidence/explanation/provenance present, no duplicate "current" rows, append-only history |
| 7 | Parity | Live legacy-vs-materialized comparison via `GET /repositories/{id}/parity`, fails under 99% similarity |
| 8 | Frontier Generator | Confidence-state distribution per repo |
| 9 | Performance | Indexing/parity/agent-run timing (informational — never gates the result) |
| 10 | Overall Score | Weighted rollup of 1–8; PASS iff no gating validation FAILs |

Exit code `0` iff every gating validation (everything but #9) passes — the
stated intent is to wire this into CI as the acceptance gate.

## Why narrative fields use keyword matching, not exact text

`purpose`, `business_capability`, and similar LLM-produced fields can
phrase the same true statement differently across runs or provider
versions. Exact-text assertion would make every run flaky for reasons
unrelated to correctness. Keyword presence checks that the LLM said
*something* recognizably on-topic without pinning wording — explicitly
named as the same "compare semantic content, not exact ids" principle
applied elsewhere to node ids, now extended to narrative text.

## Known Gaps — status as of the 2026-08-03 gap-closure sprint (KAN-7)

These were found *by building this framework against a realistic,
intentionally polyglot, intentionally well-abstracted suite*. Three of the
original four are now closed; see
`graphforge-validation/docs/validation-guide.md`'s "Closed Gaps" section
for the full technical detail on each fix (kept there, not deleted, as the
historical record of what this framework caught).

### Gap 1 — Kafka topic detection (closed)

`app/indexer/extractors/kafka.py` now resolves a same-class constant
reference and a shared-SDK wrapper delegation pattern; a new
`app/indexer/extractors/python/kafka.py` gives Python repos the same
constant-resolution behavior Java always had. Net effect: real
`KafkaTopic` nodes and `PRODUCES_TO`/`CONSUMES_FROM` edges now exist for
every repo in the suite that actually publishes/consumes.

### Gap 2 — Feign cross-repository name matching (closed)

`cross_repo_linker.py::_normalize` now also strips a trailing
language/runtime tag (`-python`/`-java`/`-go`/`-node`) before the existing
`-service`/`-client`/`-api` suffix strip, so a `<domain>-service-<language>`
polyglot naming convention (`inventory-service-python`) correctly matches
a `@FeignClient(name = "inventory-service")`. Still full equality after
stripping, never a substring match.

### Gap 3 — Impact Analysis never leaves the seed repository (closed, two pipelines)

Two independent impact-analysis code paths existed and needed two
independent fixes:

- **`ImpactAnalysisService`** (Engineering Intelligence Service Layer, the
  one this framework's Validation 5 exercises) — `IGraphRepository
  .get_neighborhood`'s Cypher filtered *both* endpoints of every traversed
  edge to the seed's own `repository_id`, which a cross-repository edge
  can never satisfy by definition. Fixed by dropping the redundant filter
  on the far endpoint; the edge *type* already enforces the repository
  boundary correctly, since a cross-repository relationship type only ever
  connects two `Repository` nodes.
- **`ImpactAnalysisEngine`** (the legacy Phase 7 pipeline actually behind
  `POST /pull-requests/{id}/analyze`, not covered by this framework at
  all) had a *different* gap: `IImpactGraphReader` had no traversal method
  for `CALLS_SERVICE` edges whatsoever, so a PR touching a component in a
  repository other repositories call via Feign never showed those callers
  as indirectly impacted. Closed in KAN-19 by adding
  `find_cross_repository_service_callers` and wiring it into
  `ImpactAnalysisEngine.analyze_pull_request` — see
  `tests/integration/test_impact_analysis_engine.py::test_feign_caller_repository_is_indirectly_impacted`.

### Gap 4 — Dependency Query's "downstream consumers" is intra-repository noise (still open, now explicitly surfaced)

`DependencyQueryAgent.build_service_requests` still scopes `search()` to
exactly one repository (`repository_ids=(repository_id,)`), and every
relationship a repository persists is stored under its own
`repository_id` partition with itself as `source_entity` — never
`target_entity` — so another repository's outgoing edge into this one is
never read from here, regardless of how it's actually stored. This is a
distinct root cause from Gap 3 (a partition-scoping limitation, not a
traversal-filter bug) and closing it properly requires either a
cross-partition search or a materialized reverse index — real scope,
deliberately not attempted as part of KAN-7's incremental fixes to avoid
guessing at the account-scoping/multi-tenancy semantics such a change
would need to get right.

**KAN-22 interim mitigation (shipped):** rather than present an
under-counted number as if it were authoritative, `render_dependency_query`
now always returns `downstream_consumers_scope: "single_repository"` and
an explicit `downstream_consumers_caveat` string, surfaced in the
Dependency Query UI directly under the Downstream Consumers list — see
`app/agents/dependency_query/renderer.py` and
`frontend/src/pages/DependencyQueryPage.tsx`. `direct_dependencies` is
unaffected by this gap (a repository's own outgoing edges live in its own
partition) and needs no caveat.

**Gap 4 and Validation 7 (Parity) are related but not identical anymore.**
The specific Parity FAILs Gap 4 used to cause (cross-repository edges
missing from the materialized side) were resolved as part of closing Gaps
1–3, once there were real cross-repository edges for the materializer to
reproduce. The remaining Gap 4 (downstream-consumer undercounting) is a
query-layer limitation on live data, not a legacy-vs-materialized
disagreement — it does not currently cause a Parity FAIL.

**Update discipline unchanged:** when Gap 4 fully closes, this section and
`validation/expected_dependency_queries.yaml` update in the same change —
the same rule that applied to Gaps 1–3.

## How parity mechanics actually work (tying back to the Knowledge Engine)

Validation 7 calls `GET /repositories/{id}/parity`, which runs
`app.knowledge_engine.parity.comparator.compare_graphs` (§
[05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md)) between the live Neo4j
graph and a graph materialized from Engineering Memory — the same
mechanism backing the frontend's Graph Parity dashboard. A failure here is
never "GraphForge is down" — it is specifically "these two independently-
derived views of the same repository disagree," which is exactly the
signal Engineering Memory's append-only design exists to make checkable at
all.

## Updating fixtures — the discipline, stated directly

"Do not 'fix' a FAIL by editing the 24 fixture repositories themselves —
they're frozen test fixtures... A FAIL means either GraphForge changed, or
this framework's fixtures are stale; it should never mean 'go edit the
repos to match what the fixture expected.'" Recapturing a fixture after a
legitimate GraphForge change means re-indexing, pulling fresh ground truth
directly from the same APIs/services the framework itself uses (never
Cypher/SQL), and re-running to confirm PASS.

## Operational notes

- Requires the backend reachable at `GRAPHFORGE_API_URL`, Postgres at
  `DATABASE_URL`, the 24 repos already indexed, and a configured LLM
  provider (Validations 3–5 make real agent runs with real LLM calls).
- A Validation 3 failure citing an expired AWS STS token under
  `AI_PROVIDER=bedrock` is explicitly named as a credentials issue, not a
  GraphForge or framework defect — deterministic fields are unaffected
  since GraphForge's agents degrade gracefully to deterministic fallback
  text on an LLM failure (§ [08_AGENTS.md](08_AGENTS.md) Prompt Builder).
