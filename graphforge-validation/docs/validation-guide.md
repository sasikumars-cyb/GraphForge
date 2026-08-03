# GraphForge Regression Validation Framework — Guide

This is the permanent acceptance test suite for GraphForge's Engineering
Intelligence Platform. It runs the 24-repository validation ecosystem
(`graphforge-validation-suite/`) through GraphForge's real APIs and
checks the result against a captured expected state. If GraphForge
changes behavior — a new parser, a fixed matcher, a changed confidence
formula — this is what should catch it.

## What this framework is not

It does not reimplement any GraphForge logic. Every fact it asserts on
comes from one of:

- GraphForge's own REST API (`lib/client.py` — repositories, graph,
  parity, agent runs)
- GraphForge's own Engineering Memory service, called directly in-process
  (`lib/memory.py` — `EngineeringMemoryService`, the same class
  `app/api/v1/routers/*.py` uses)

No Cypher, no manual SQL, no relationship-matching or confidence logic
lives in this framework. GraphForge is a black box; this only asks it
questions and compares the answers to `validation/expected_*.yaml`.

## Running it

```bash
cd graphforge-validation
pip install -r requirements.txt   # httpx, pyyaml — everything else comes from backend's own venv
python scripts/run_validation.py
```

Requires:

- GraphForge's backend running and reachable at `GRAPHFORGE_API_URL`
  (default `http://localhost:8000/api/v1`)
- Postgres reachable at `DATABASE_URL` (default matches the project's
  local dev `docker-compose` — port 5433)
- The 24-repository validation suite already indexed under the account
  identified by `GRAPHFORGE_USER_ID`
- A configured LLM provider (`AI_PROVIDER` + its API key in the
  backend's `.env`) — Validations 3–5 make real agent runs, which make
  real LLM calls

If `AI_PROVIDER=bedrock`, a Validation 3 failure whose evidence says
`"Narrative could not be generated (The security token included in the
request is expired)"` is an expired AWS STS session token, not a
GraphForge or framework defect — refresh credentials and re-run.
Deterministic fields (dependencies, databases, hop counts, blast radius)
are unaffected by an LLM failure like this; only the narrative/keyword
checks in Validation 3 will show it, since GraphForge's own agents
degrade gracefully to their deterministic fallback text rather than
failing the run (see `app/agents/frontier/prompt_builder.py`).

This script must run with GraphForge's own backend package importable
(`lib/bootstrap.py` adds `../backend` to `sys.path` automatically) — run
it from a Python environment that has the backend's dependencies
installed (its poetry venv, most simply: `poetry run --directory
../backend python scripts/run_validation.py` from this directory, or
activate that venv first).

Output: `reports/validation_results_<timestamp>.json`,
`reports/validation_report_<timestamp>.html`, and `reports/latest.{json,html}`
overwritten every run. Exit code is `0` if every gating validation
(everything except the informational Validation 9, Performance) passed,
`1` otherwise — wire this into CI as the acceptance gate.

## The ten validations

| # | Name | What it checks | Script |
|---|---|---|---|
| 1 | Repository Graph | Node/edge/endpoint/dependency counts, Kafka topics, Feign clients, per supported repo; unsupported repos still correctly rejected | `compare_relationships.py` |
| 2 | Cross-Repository Relationships | CALLS_SERVICE/SHARES_TOPIC/DEPENDS_ON_REPOSITORY edges, PASS/MISSING/UNEXPECTED | `compare_relationships.py` |
| 3 | Repository Understanding Agent | Live agent run; deterministic fields exact, narrative fields by keyword | `compare_agents.py` |
| 4 | Dependency Query Agent | Live agent run; direct/reverse dependency counts, confidence breakdown | `compare_agents.py` |
| 5 | Impact Analysis Agent | Live agent run; blast radius, hop count, confidence, affected repos | `compare_agents.py` |
| 6 | Engineering Memory | Relationships persisted, confidence/explanation/provenance present, no duplicate "current" rows, append-only history | `run_validation.py` |
| 7 | Parity | Live legacy-vs-materialized comparison via `GET /repositories/{id}/parity`, fails under 99% similarity | `compare_relationships.py` |
| 8 | Frontier Generator | Confidence-state distribution (candidate/likely/highly_likely/verified/...) per repo | `compare_relationships.py` |
| 9 | Performance | Indexing/parity/agent-run timing table (informational — never gates the overall result) | `run_validation.py` |
| 10 | Overall Score | Weighted rollup of 1–8 into a health score; PASS iff no gating validation is FAIL | `run_validation.py` |

## Updating the fixtures

The five `validation/expected_*.yaml` files are the only files that
should need editing when GraphForge legitimately changes. To recapture
them after a real change:

1. Re-index the 24 repositories (or the affected ones).
2. Pull fresh ground truth the same way this framework's authors did —
   call `GET /repositories/{id}/graph` for node/edge/endpoint/dependency
   facts, `GET /repositories/cross-repository-edges` for cross-repo
   edges, `EngineeringMemoryService.get_current_relationships` for
   confidence-state distributions, and
   `ImpactAnalysisService.compute_blast_radius` directly for blast-radius
   ground truth (bypassing the LLM narrative layer, which is
   non-deterministic by design — see below).
3. Update the relevant YAML file(s) with the new numbers. Each file has
   a header comment explaining what's deterministic (exact-match) vs.
   narrative (keyword-match).
4. Re-run `run_validation.py` and confirm PASS.

Do not "fix" a FAIL by editing the 24 fixture repositories themselves —
they're frozen test fixtures (per the suite's own constraints). A FAIL
means either GraphForge changed, or this framework's fixtures are stale;
it should never mean "go edit the repos to match what the fixture
expected."

### Why narrative fields use keyword matching, not exact text

`purpose`, `business_capability`, and similar fields are LLM output —
same prompt, same evidence, can still phrase things differently between
runs or provider versions. Asserting exact text would make every run
flaky for reasons that have nothing to do with GraphForge's correctness.
Keyword presence (`expected_repository_profiles.yaml`'s
`*_keywords` lists) checks that the LLM said *something* recognizably
about the right topic, without pinning down its wording — the same
"compare semantic content, not exact ids" principle the RFC applied to
node ids, extended to narrative text.

## Closed Gaps (2026-08-03 gap-closure sprint)

These four were discovered by building this framework against a
realistic, intentionally polyglot, intentionally well-abstracted
24-repository suite, and were closed in a follow-up sprint once the
framework proved they were real. Kept here (not deleted) as the
historical record of what this framework is *for*: it caught real,
verified gaps in GraphForge's own indexing/reasoning pipeline, not suite
bugs, and the fixtures below reflect the closed state.

### 1. Kafka topic detection (closed)

`app/indexer/extractors/kafka.py` (Java) now resolves a same-class
`static final String TOPIC = "..."` constant, and a producer delegated
through the shared SDK's `EventPublisher` (not just a raw
`KafkaTemplate`). A new Python extractor,
`app/indexer/extractors/python/kafka.py`, detects the same delegation
pattern through `shared_python_sdk.kafka_client.EventProducer`/
`EventConsumer`, with the same one-level constant resolution. Both skip
test files (`classify_is_test`'s path signal) — an early version without
that check fabricated a `SHARES_TOPIC` relationship from
`shared-python-sdk` to every real topic user, sourced entirely from
`shared-python-sdk/tests/test_kafka_client.py`'s own unit test.

`payment.completed`, `inventory.updated`, and `order.created` are all
now real `KafkaTopic` nodes with real `PRODUCES_TO`/`CONSUMES_FROM`
edges. See `validation/expected_repository_profiles.yaml`.

### 2. Feign cross-repository name matching (closed)

`app/indexer/graph/cross_repo_linker.py::_normalize` now also strips a
trailing language/runtime tag (`-python`/`-java`/`-go`/`-node`/etc.)
before the existing `-service`/`-client`/`-api` suffix strip — so a
`@FeignClient(name = "inventory-service")` correctly matches a
repository literally named `inventory-service-python`. Still full
equality after stripping, never a substring match; verified against
every repository name in the suite with no false-collision.
`shipping-service-java -CALLS_SERVICE-> inventory-service-python` is now
a real edge. See `validation/expected_relationships.yaml`.

### 3. Impact Analysis never actually left the seed repository (closed)

`IGraphRepository.get_neighborhood`'s Cypher (`app/graph/
neo4j_repository.py`) filtered **both** endpoints of every traversed
edge to the seed's own `repository_id` — a cross-repository edge, by
definition, has a target node in a different repository, so it could
never satisfy that filter regardless of which edge types
`ImpactAnalysisService._IMPACT_EDGE_TYPES` nominally listed. Fixed by
dropping the redundant property filter on the far endpoint in both the
node-discovery and induced-subgraph passes — the edge *type* already
enforces the repository boundary correctly, since a cross-repository rel
type only ever connects two Repository nodes, by construction of
`cross_repo_linker.py`. `SHARES_TOPIC` was also added to
`_IMPACT_EDGE_TYPES`, since that's the edge type the RFC's own
validation target is actually reached through.
`payment-service-java` now correctly impacts `inventory-service-python`,
`shipping-service-java`, and `notification-service-python` (plus other
real, 2-hop-reachable repositories). See
`validation/expected_impact_analysis.yaml`.

### 4. Cross-repository edges "never persisted to Engineering Memory" — partially misdiagnosed, two real bugs found instead (closed)

The original framing (see git history) was that `cross_repo_linker`'s
edges were written to Neo4j only, never to `KnowledgeRelationship`.
Investigation found this was **already false** — `cross_repo_memory.py`
(ADR 0018 RFC-05) already persisted every cross-repository hypothesis
correctly; it simply had nothing to persist while Gaps 1 and 2 above
were open (zero Kafka topics, zero Feign matches → zero candidate
hypotheses). Two different, real bugs were actually causing the
observed symptom (Validation 7 Parity FAILs):

- **`get_full_graph`'s Cypher excluded cross-repository edges from the
  "legacy" side of every parity comparison.** Same shape as Gap 3: the
  `OPTIONAL MATCH`'s target required `m.repository_id = $repository_id`.
  Fixed the same way — the edge type already enforces the boundary; the
  extra node-property filter only blocked the real case. (`m` itself is
  deliberately still excluded from the returned *node* set for a
  cross-repository edge — only its id is needed for `target_id`, and the
  Materializer doesn't materialize a foreign Repository node either;
  including it would have manufactured a node-parity mismatch instead of
  closing one.)
- **Evidence-pack staleness under repeated relinking.** Every account
  relink (`relink_account`, called once per `run_indexing`) re-persists
  a fresh cross-repository evidence pack for every repository pair with
  a hypothesis — by design, so each pack reflects current state. But
  `Materializer._latest_single_repo_pack` and
  `RepositoryProfileService.get_profile`'s narrative lookup both found
  "the latest pack" by fetching the top-N most-recent packs
  (`list_evidence_packs`, default `limit=50`) and filtering out
  cross-repo packs *client-side* — so after enough relinks, a
  repository's own single-repo pack could be pushed entirely outside
  that window, starving both reads of the row they actually wanted (in
  the worst case, `materialize_repository_graph` returned an
  all-but-empty payload for a repository with dozens of real
  Neo4j nodes). Fixed by adding `exclude_commit_sha` to
  `EngineeringMemoryRepository.list_evidence_packs`/
  `EngineeringMemoryService.list_evidence_packs`, filtering the
  `"n/a-cross-repo"` sentinel at the SQL level in both call sites,
  instead of truncate-then-filter.

**Still open, deliberately not touched by this sprint** (see next
section): Engineering Memory has no mechanism to *retract* a
relationship once its evidence disappears, and the Materializer doesn't
dedupe two evidence-pack items sharing an identical
`(source, type, target, properties)` triple the way Neo4j's `MERGE`
does. Both are real, but neither is "cross-repo edges never persisted" —
that specific claim is now false.

### 5. Parity (raised from 4/14 to 13/14 supported repos at 100%, 1 at 98.94%)

A direct consequence of closing gaps 1–4: every repo whose only parity
difference was a Kafka/Feign/dependency-driven cross-repository edge is
now at 100%. The one remaining repo (`shared-python-sdk`, 98.94%) fails
for a genuinely separate, pre-existing reason — see "Duplicate-edge
materialization fidelity" below, not touched by this sprint's five named
gaps.

## Known Gaps (still open)

### Evidence retraction

Engineering Memory has no way to mark a previously-confirmed
relationship as no longer valid once its underlying evidence disappears
— `persist_cross_repo_relationships`/`_persist_pair` only ever adds a
new confirming version when a candidate is found; when a pair stops
producing a hypothesis (e.g. because a false-positive detection was
fixed in code, as happened mid-sprint with `shared-python-sdk`'s
test-file leak — see Gap 1 above), the old "current" version simply
never gets superseded. `apply_correction` exists but is explicitly an
audit-trail annotation (`memory_service.py`'s own docstring: "does not
itself mutate any `KnowledgeRelationshipRecord`"), not a retraction
mechanism. The 90 stale rows this produced mid-sprint were cleaned up as
one-time data hygiene (a direct `DELETE`, approved by the user before
running), not a code fix — the underlying gap (no retraction path)
remains open. Designing one (an explicit rejection/supersession version,
or a periodic re-validation pass) is a real RFC on its own, out of this
sprint's "fix only that layer" scope.

### Duplicate-edge materialization fidelity

`shared-python-sdk` has a `tests.test_auth.test_validate_api_key ->
shared_python_sdk.auth.validate_api_key` `CALLS` edge that the live test
source calls twice (two separate assertions). `graph/builder.py` never
dedupes edges (only nodes — see `build_graph`'s own `deduped_nodes`
comment), so this legitimately produces 2 logical edge instances; Neo4j
`MERGE` then collapses them into 1 physical relationship on write, since
they share an identical `(source, type, target)` triple with no
properties to distinguish them. The Materializer's `_single_repo_edges_
from_pack` reads directly from evidence-pack items, which were never
deduped, and reproduces both — so `materialized_count: 2` vs.
`legacy_count: 1` for that one triple. Genuinely pre-existing (this
sprint didn't touch `graph/builder.py`'s Python function-call
extraction, single-repository edges, or the Materializer's single-repo
edge path) and orthogonal to all five named gaps above — confirmed via
`compare_relationships.py`'s parity check, not fixed here.

### Everything from before this sprint, still open

- No `DataTable` node extraction for either language (no parser
  populates `ArchitectureModel`'s table list from a SQLAlchemy
  `__tablename__` or JPA `@Entity`) — `databases: []` everywhere is
  real, current output.
- No Python REST-endpoint (`@app.get`/`@app.post`) extraction —
  `endpoint_count: 0` on every Python repo is real, current output.
- No Python cross-repository REST-call detection at all (Feign is
  Java-only) — `customer-service-python -CALLS_SERVICE->
  order-service-python` is still correctly absent; see
  `known_absent_cross_repository_edges` in `expected_relationships.yaml`.
- `Dependency Query`'s `downstream_consumers_count` is still always 0
  for a distinct, still-open reason (not the one gap 4 above closed):
  `DependencyQueryAgent.build_service_requests` scopes `search()` to
  exactly one repository (`repository_ids=(repository_id,)`), and every
  relationship a repository persists is stored under its own
  `repository_id` partition with itself as `source_entity` (never
  `target_entity`) — so another repository's outgoing edge into this one
  is never read from here, regardless of how it's stored. See
  `expected_dependency_queries.yaml`'s header comment for the full
  reasoning.

None of the above are asserted as bugs to silently work around — they're
asserted as the honest current baseline in `validation/expected_*.yaml`,
so this framework does its job (catch *changes*) instead of either
silently passing on inflated expectations or perpetually failing on gaps
nobody chose to fix yet. When any of them closes, update the relevant
fixture file(s) and this guide in the same change.
