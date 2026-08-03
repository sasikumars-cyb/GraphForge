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

## Known Gaps — the four the framework itself surfaced

These were found *by building this framework against a realistic,
intentionally polyglot, intentionally well-abstracted suite*, and are
"asserted as the current baseline in the fixtures (not worked around) — the
most valuable output of this exercise."

### Gap 1 — Kafka topic detection

`app/indexer/extractors/kafka.py` only recognizes: consumers via
`@KafkaListener(topics = "literal-string")` (a named constant is not
resolved), and producers via a field declared `KafkaTemplate<...>` **and**
a `.send("topic", ...)` call **in the same class**. Every repo in the
validation suite publishes through a shared-SDK wrapper — a realistic,
good abstraction the heuristic can't see through. **There is no Python
Kafka extractor at all yet.** Net effect: 0 `KafkaTopic` nodes across all
14 supported repos, despite a fully wired Kafka producer/consumer
architecture across 5 of them.

### Gap 2 — Feign cross-repository name matching

`cross_repo_linker.py::_identifier_match` strips only a trailing
`-service`/`-client`/`-api` suffix before comparing a `@FeignClient(name =
"...")` value against another repository's name. The suite's repos follow
a `<domain>-service-<language>` convention (`inventory-service-python`) —
a realistic polyglot naming scheme the suffix-only regex can't bridge
(`normalize("inventory-service-python")` returns itself unchanged, since
the suffix pattern only matches at the very end). Net effect: 0
`CALLS_SERVICE` cross-repository edges, despite a real, working
`@FeignClient` pointed at a real, tracked, indexed target repository.

### Gap 3 — Impact Analysis never leaves the seed repository

See [07_ENGINEERING_INTELLIGENCE.md](07_ENGINEERING_INTELLIGENCE.md) —
`ImpactAnalysisService._IMPACT_EDGE_TYPES` includes cross-repository edge
types, but the underlying Cypher filters both endpoints of every candidate
edge to the same `repository_id`, which a cross-repository edge can never
satisfy by definition. Blast radius is `[itself]` for every repository in
the suite regardless of real `DEPENDS_ON_REPOSITORY` edges in Neo4j.

### Gap 4 — Dependency Query's "direct dependencies" is intra-repository noise

See [07_ENGINEERING_INTELLIGENCE.md](07_ENGINEERING_INTELLIGENCE.md).
Because Engineering Memory currently persists only intra-repository
structural relationships, `direct_dependencies_count` equals a
repository's total relationship count, and `downstream_consumers_count` is
provably always 0.

**Gaps 4 and 7 (Parity) are one root cause surfacing twice, not two
separate findings.** The Parity Engine compares live Neo4j against a graph
materialized from Engineering Memory; since cross-repository edges were
never persisted to Engineering Memory (only to Neo4j directly), the
materializer can't reproduce them, and Parity correctly reports each as an
"unexpected edge" on the live-graph side — every affected repo lands at
1–2 unexpected edges out of 35–51 total, dropping just under the 99%
threshold (95–99% observed). Closing Gap 4 (persisting cross-repository
relationships into `KnowledgeRelationship`, which RFC-05 already partially
addressed at the linking layer but not yet at the query layer) should
resolve both symptoms.

**None of the four are asserted as bugs in the fixture files** — they are
"the honest current baseline," so the framework does its actual job (catch
*changes*, i.e. regressions) instead of either silently passing on
inflated expectations or perpetually failing on gaps nobody has scheduled
to fix. When any gap closes, the fixture file(s) and the guide update in
the same change — a stated discipline for keeping fixtures from drifting
into either fiction.

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
