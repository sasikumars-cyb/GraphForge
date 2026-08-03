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

## Known Gaps

These were discovered *by* building this framework against a realistic,
intentionally polyglot, intentionally well-abstracted 24-repository
suite. They are asserted as the current baseline in the fixtures (not
worked around), and are the most valuable output of this exercise —
each is a real, verified gap in GraphForge's current indexing/reasoning
pipeline, not a suite defect.

### 1. Kafka topic detection

`app/indexer/extractors/kafka.py` only recognizes:

- Consumers: `@KafkaListener(topics = "literal-string")` — a named
  constant (`topics = TOPIC`) is not resolved.
- Producers: a field declared `KafkaTemplate<...>` **and** a
  `.send("topic", ...)` call **in the same class**. Every repo in this
  suite publishes through a shared-SDK wrapper
  (`shared-java-sdk`'s `EventPublisher`, `shared-python-sdk`'s
  `EventProducer`) — a realistic, good abstraction that this heuristic
  can't see through.
- There is no Python Kafka extractor at all yet — `extract_kafka_*` is
  only wired into the Java parser.

Net effect: 0 `KafkaTopic` nodes across all 14 supported repos, despite
a fully wired Kafka producer/consumer architecture across 5 of them.

### 2. Feign cross-repository name matching

`app/indexer/graph/cross_repo_linker.py::_identifier_match` strips only
a trailing `-service`/`-client`/`-api` suffix before comparing a
`@FeignClient(name = "...")` value against another tracked repository's
name. This suite's repos follow a `<domain>-service-<language>`
convention (`inventory-service-python`) — a completely realistic
polyglot naming scheme — which this heuristic can't bridge:
`normalize("inventory-service")` → `"inventory"`,
`normalize("inventory-service-python")` → itself (the suffix regex only
matches at the very end). Net effect: 0 `CALLS_SERVICE` cross-repository
edges, despite a real, working `@FeignClient` pointed at a real,
tracked, indexed target repository.

### 3. Impact Analysis never actually leaves the seed repository

`ImpactAnalysisService._IMPACT_EDGE_TYPES` includes `CALLS_SERVICE` and
`DEPENDS_ON_REPOSITORY`, but the traversal underneath
(`graph_traversal.traverse` → `IGraphRepository.get_neighborhood`) takes
a single `repository_id` and its Cypher query filters **both** endpoints
of every candidate edge to that same `repository_id`. A cross-repository
edge, by definition, has a target node in a different repository, so it
can never satisfy that filter. Blast radius for every repository in this
suite is currently `impacted_repositories = [itself]`, regardless of how
many real `DEPENDS_ON_REPOSITORY` edges exist in Neo4j.

### 4. Dependency Query's "direct dependencies" is intra-repository noise today

`app/agents/dependency_query/renderer.py::_split_by_direction` buckets a
relationship into `direct_dependencies` whenever its `source_entity`
starts with the queried repository's own id prefix. Because
Engineering Memory currently only persists intra-repository structural
relationships (`CONTAINS`/`CALLS`/`DEPENDS_ON`-to-own-manifest-entry —
`cross_repo_linker`'s cross-repository edges are written to Neo4j only,
never to `KnowledgeRelationship`), **every** current relationship's
source matches that prefix. Net effect: `direct_dependencies_count`
equals a repository's *total* relationship count today, and
`downstream_consumers_count` is provably always 0 (see the fixture
file's header comment for why this is a structural certainty, not just
an observation).

**This same root cause also produces the Validation 7 (Parity) FAILs**
for every repo that has an outgoing `DEPENDS_ON_REPOSITORY` edge in
Neo4j: the Parity Engine compares the live Neo4j graph against a graph
*materialized from Engineering Memory* — since that edge was never
persisted to Engineering Memory, materialization can't reproduce it, and
the Parity Engine correctly reports it as an "unexpected edge" on the
live-graph side. Every affected repo lands at 1–2 unexpected edges out
of 35–51 total, which is enough to drop just under the RFC's 99%
threshold (95–99%). This isn't a second, unrelated finding — it's the
same gap surfacing in two different subsystems, and closing gap 4 should
resolve both.

None of these four are asserted as bugs in this document — they're
asserted as the honest current baseline in `validation/expected_*.yaml`,
so this framework does its job (catch *changes*) instead of either
silently passing on inflated expectations or perpetually failing on
gaps nobody chose to fix yet. When any of them closes, update the
relevant fixture file(s) and this guide in the same change.
