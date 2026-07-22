# ADR 0007: Architecture discovery engine (repository indexer + Neo4j graph)

## Status
Accepted

## Context
The ask: given a repository, deterministically discover its architecture — REST controllers/endpoints, Feign clients, Kafka producers/consumers, Spring services, and Maven dependencies — and persist the result as a graph, without any AI/LLM involvement. Scope for this phase is explicitly narrow: Java + Spring Boot (Maven) only, with an extension point for other languages later.

This is the first feature in the project that parses arbitrary third-party source code and the first that writes to Neo4j rather than Postgres.

## Decisions

**Tree-sitter over JavaParser.** The backend is Python; JavaParser is a JVM library with no usable Python bindings, so using it would mean shelling out to a JVM subprocess per file (slow, an extra runtime dependency in every container image, and a much larger surface for things to go wrong). `tree-sitter` + `tree-sitter-java` ships prebuilt grammar wheels, parses in-process, and produces a concrete syntax tree that's more than sufficient for the annotation- and structure-based matching this phase needs (no type resolution or semantic analysis required).

**Fully deterministic — no heuristics beyond literal matching.** Every extractor (`app/indexer/extractors/`) only ever reads literal annotation arguments (`@GetMapping("/orders")`), enum constants, and structural facts (is this class a `@RestController`, does this field's declared type start with `KafkaTemplate`). A `KafkaTemplate.send(topic, ...)` call is only recorded when `topic` is a literal string; a variable or method-call argument is silently skipped rather than guessed at (see `KafkaProducerUsage`'s docstring) — the alternative would require real data-flow analysis, out of scope, and guessing would violate "everything should be deterministic."

**One `ILanguageParser` interface, one registry.** `app/indexer/parsers/base.py` defines a single-method interface (`parse(repo_root) -> ArchitectureModel`); `app/indexer/parsers/registry.py` maps `DetectedLanguage` → parser instance. Adding a second language later (Gradle-based Java, a different ecosystem entirely) means a new parser class and one new registry entry — no change to `language_detector.py`, `indexing_service.py`, or the graph builder, which all operate on the language-agnostic `ArchitectureModel`/`GraphPayload` shapes.

**Detection is narrow on purpose.** `detect_language` only recognizes a root-level `pom.xml` mentioning `spring-boot`. Multi-module Maven projects where the root `pom.xml` is a bare aggregator (Spring Boot only in a child module) are not detected in this phase — a real limitation, not an oversight; broadening it is a natural next step once single-module support is proven out.

**Neo4j via the official async driver, behind `IGraphRepository`.** `app/graph/interfaces.py` is graph-store-agnostic (`replace_repository_graph`, `get_full_graph`, `get_nodes_by_label`, `has_graph`); `Neo4jGraphRepository` is the only concrete implementation. The indexer never imports Neo4j directly — it builds a generic `GraphPayload` (`app/graph/models.py`) and hands it to the interface, so a non-Neo4j graph store would only mean a new class here.

**Every node and relationship type is an explicit allowlist**, checked before Cypher label/relationship-type interpolation (`_ALLOWED_LABELS`/`_ALLOWED_REL_TYPES` in `neo4j_repository.py`). Cypher can't parameterize label or relationship-type names the way it can values, so they have to be string-interpolated — safe here specifically because both sets are fixed and internally controlled, never derived from request input. Writing an unlisted label/type raises `ValueError` rather than silently succeeding.

**Every node ID is namespaced `f"{repository_id}:{kind}:{key}"`.** Re-indexing the same repository always produces the same IDs, so `replace_repository_graph`'s `MERGE`-based writes correctly upsert in place, and IDs never collide across repositories sharing the same Neo4j database.

**A generic `Component` fallback for classes with no Spring stereotype.** A class that sends/receives Kafka messages but isn't itself a `@Controller`/`@Service`/`@FeignClient` (e.g. a bare `@Component`, or an unannotated helper) still needs a node to attach the `PRODUCES_TO`/`CONSUMES_FROM` edge to. `graph/builder.py`'s `_owning_component_id` creates a generic `["Component"]` node for exactly this case, so Kafka usage is never silently dropped just because its owning class wasn't independently discovered by another extractor.

**FastAPI `BackgroundTasks` stands in for a real task queue.** `POST /repositories/{id}/index` returns `202` immediately with a `pending` `IndexingJob` row and schedules the actual clone→parse→build→persist pipeline via `BackgroundTasks`. This is explicitly a stand-in, not a permanent choice — no retry, no distributed execution, no visibility beyond one process. A real deployment should replace this with Celery, arq, or similar; the worker function (`app/indexer/workers/index_worker.py`) is already isolated behind its own module specifically so that swap doesn't touch the router or the indexing service itself.

**One `IndexingJob` row per trigger, guarding against concurrent runs.** `POST /repositories/{id}/index` returns `409 Conflict` if a `pending` or `running` job already exists for that repository — a lightweight guard against two indexing runs racing each other's Neo4j writes, without needing a distributed lock.

## Scope boundaries (explicitly not built)

- Gradle projects, or multi-module Maven where the root `pom.xml` doesn't itself mention Spring Boot.
- Cross-file call-graph / usage analysis (e.g. "which controller calls which service method") — only structural discovery within each file.
- Non-literal Kafka topic resolution (topics passed as variables or computed values).
- Incremental re-indexing — every run fully replaces the prior graph (`DETACH DELETE` then rewrite), rather than diffing.
- A real distributed task queue (see the `BackgroundTasks` decision above).

## Verification strategy

Every layer was verified against real infrastructure, not mocks:
- `tests/unit/indexer/` — the tree-sitter extractors and `pom.xml` parser against inline Java/XML fixtures, using the exact same grammar (`tree_sitter_java`) production code uses.
- `tests/fixtures/spring_boot_sample/` — a real, hand-written Spring Boot mini-project (a controller, a service, a Feign client, a Kafka producer and consumer, and a `pom.xml` with both direct dependencies and a `dependencyManagement` block that must be excluded) exercised end-to-end by `SpringBootJavaParser`.
- `tests/integration/test_repository_cloner.py` — a real `git clone` subprocess against a real local git repository (no mocked filesystem or git client).
- `tests/integration/test_indexing_pipeline.py` — the full `index_repository` pipeline (clone → parse → build → persist) against a real Neo4j instance, asserting on the graph actually read back.
- `tests/integration/test_indexing_api.py` — all four API endpoints through real HTTP requests, a real committed Postgres user/repository, and the real background-task worker; covers the happy path, the 409-conflict guard, cross-user 404s, and a job correctly ending up `failed` for an unsupported repository.

CI now runs a Neo4j service container (`neo4j:5-community`, matching `docker/docker-compose.yml`) alongside the existing Postgres one so these tests run on every push.

## Consequences
- Only Java + Spring Boot (Maven, single-module) repositories can be indexed today; anything else returns `422 unsupported_repository`.
- Indexing runs in-process via `BackgroundTasks` — acceptable for a single-instance deployment, but doesn't survive a process restart mid-run (the job would be left `running` forever) and doesn't scale beyond one worker. Replacing it with a real queue is the natural next step, not a redesign.
- The graph is fully replaced on every re-index; there's no history of how a repository's architecture changed over time.
