# 6. Indexing / Knowledge Architecture

```mermaid
flowchart TB
    Trigger["Trigger:<br/>POST /repositories/{id}/index (API)<br/>or GitHub push webhook<br/>(api/v1/routers/{repositories,webhooks}.py)"]

    RunIndexing["indexer/services/indexing_service.py::run_indexing()<br/>DB-aware entrypoint"]
    Trigger --> RunIndexing

    RunIndexing --> TokenLookup["_get_access_token()<br/>models.GitHubConnection<br/>(decrypted per-user PAT)"]

    RunIndexing --> Decide{"_attempt_incremental_index()<br/>prior commit? safe diff?"}

    Decide -- "yes (KAN-32)" --> Incremental["_index_changed_files()<br/>GitHub Compare/Contents API<br/>(no git clone)"]
    Decide -- "no / fallback" --> Full["index_repository()<br/>full clone + parse + replace"]

    subgraph FullPath["Full indexing path"]
        Clone["scanner/repository_cloner.py::clone_repository()<br/>shallow git clone to indexer_clone_root,<br/>always cleaned up"]
        DetectLang["scanner/language_detector.py::detect_language()<br/>pom.xml+'spring-boot' → JAVA_SPRING_BOOT<br/>pyproject.toml/requirements.txt/... → PYTHON<br/>else UNSUPPORTED"]
        GetParser["parsers/registry.py::get_parser()<br/>JAVA_SPRING_BOOT → SpringBootJavaParser<br/>PYTHON → PythonParser"]
        Extractors["extractors/*.py<br/>controllers, services, feign_clients, kafka,<br/>python/{classes,functions,imports,kafka,spark}"]
        Full --> Clone --> DetectLang --> GetParser --> Extractors
        Extractors --> Model["indexer/models/architecture.py::ArchitectureModel<br/>(language-agnostic parse result)"]
    end

    Incremental --> ModelInc["ArchitectureModel<br/>(scoped to changed files)"]

    Model --> Builder["indexer/graph/builder.py::build_graph()<br/>ArchitectureModel → generic GraphPayload<br/>node id = f'{repo_id}:{kind}:{key}'"]
    ModelInc --> Builder

    Builder --> Neo4jRepo["graph/neo4j_repository.py::Neo4jGraphRepository<br/>replace_repository_graph() /<br/>replace_repository_files_subgraph()<br/>Cypher MERGE (idempotent re-index)"]
    Neo4jRepo --> NEO[("Neo4j")]

    Model --> Evidence["hypotheses/repository_evidence.py::extract_repository_evidence()<br/>README/manifest/config text<br/>(read before clone is discarded)"]
    Evidence --> Shadow["hypotheses/shadow_runner.py::run_shadow_hypothesis_generation()<br/>shadow-mode only, never affects the real graph"]
    Shadow --> Generators["hypotheses/{deterministic_generator,llm_generator}.py<br/>+ generator_registry.py"]
    Generators --> KEValidators["knowledge_engine/validators/*.py<br/>cross_repo, deterministic_structural,<br/>evidence_keyword"]
    KEValidators --> PG_EM[("PostgreSQL —<br/>Engineering Memory<br/>(beliefs, hypotheses, evidence)")]

    Neo4jRepo --> ShadowCompare["knowledge_engine/shadow_compare.py<br/>diagnostic-only comparison of<br/>Materializer projection vs. written graph"]

    RunIndexing --> CrossRepo["indexer/graph/cross_repo_linker.py::relink_account()<br/>recomputes cross-repo edges for the<br/>whole account (Feign clients, shared topics)<br/>advisory-locked, O(N) not O(N²)"]
    CrossRepo --> NEO

    subgraph Retrieval["Retrieval / query flow"]
        AgentsRead["agents/* via context.extras['graph_repository']<br/>(hop-budgeted Neo4jGraphRepository)"]
        AnalysisRead["analysis/graph/neo4j_impact_reader.py<br/>(deterministic PR impact analysis)"]
        ArchAPI["api/v1/routers/architecture.py<br/>get_full_graph / get_neighborhood"]
        AgentsRead --> NEO
        AnalysisRead --> NEO
        ArchAPI --> NEO
    end

    Trigger2["POST /repositories (create/index)"] -.-> Trigger
```

## Explanation

Indexing is entirely deterministic (no LLM in the primary path) except for
one clearly-separated, shadow-only branch. The pipeline is:

1. **Clone** (`repository_cloner.py`) a shallow copy of the repo (or, for
   `KAN-32` incremental runs, fetch only the changed files via GitHub's
   Compare/Contents API — no clone at all).
2. **Detect language** deterministically by file presence/substring checks
   (no ML/heuristic scoring) — currently **Java+Spring Boot (Maven)** and
   **Python** are supported; anything else is `UNSUPPORTED` and the run
   fails with a 422.
3. **Parse** with the matching `ILanguageParser` (`SpringBootJavaParser` or
   `PythonParser`), which runs a set of `extractors/` (controllers,
   services, Feign clients, Kafka producers/consumers, Python classes/
   functions/imports/kafka/spark) to produce a language-agnostic
   `ArchitectureModel`.
4. **Build graph**: `indexer/graph/builder.py::build_graph()` is the single
   place that knows how a parsed entity becomes a Neo4j node/edge. Node ids
   are namespaced `f"{repository_id}:{kind}:{key}"` so re-indexing the same
   repository is idempotent (Cypher `MERGE`, not `CREATE`).
5. **Persist**: `Neo4jGraphRepository.replace_repository_graph()` (full) or
   `.replace_repository_files_subgraph()` (incremental, scoped to changed
   file paths, including a renamed file's old path for deletion).
6. **Cross-repository linking**: `relink_account()` recomputes edges across
   *every* repository the account owns (a Feign client or shared Kafka topic
   may reference a repository indexed earlier), serialized per-account with
   a Postgres advisory lock.
7. **Shadow hypothesis generation** (ADR 0018): read-only evidence
   (README/manifest text) is extracted from the same clone before it's
   discarded, then run through deterministic and (opt-in,
   `enable_frontier_llm_generator`) LLM-based hypothesis generators,
   validated (`knowledge_engine/validators/`), and persisted to Postgres as
   Engineering Memory (`beliefs`, `hypotheses`, `evidence` tables) — **this
   never writes to or reads from the Neo4j graph that agents/analysis
   actually query**; it is explicitly a parallel, diagnostic pipeline.

**Graph schema observed** (node labels / relationship types actually
written by `build_graph()`):

| Node labels | Relationship types |
|---|---|
| `Component` (+ `Controller`\|`Service`\|`FeignClient`\|`Function`\|`Module`\|`Class`), `Endpoint`, `KafkaTopic`, `Dependency`, `DataTable`, `Repository` | `CONTAINS`, `IMPORTS`, `CALLS`, `EXPOSES`, `DEPENDS_ON`, `PRODUCES_TO`, `CONSUMES_FROM`, `READS_FROM`, `WRITES_TO` |

**Retrieval** happens through three independent readers, all against the
same Neo4j store: agents (via a per-run, hop-budgeted
`Neo4jGraphRepository`), the deterministic PR-impact engine (via
`analysis/graph/neo4j_impact_reader.py`, a narrower read-only interface),
and the Architecture API (`get_full_graph`/`get_neighborhood`, paginated).

## Confirmed vs. Uncertain

- **Confirmed**: the full clone→detect→parse→build→persist pipeline,
  the two supported languages, and every node label/edge type listed above
  — read directly from `indexer/graph/builder.py`.
- **Uncertain / requires verification**: the precise set of `extractors/`
  invoked per language (e.g. whether every Python extractor listed under
  `indexer/extractors/python/` is wired into `PythonParser` vs. present but
  unused) was inferred from directory contents and the builder's node types,
  not from a line-by-line read of `parsers/python/python_parser.py`.

## Sources

- `backend/app/indexer/services/indexing_service.py` (full read).
- `backend/app/indexer/graph/builder.py` (full read of node/edge
  construction).
- `backend/app/indexer/scanner/{language_detector,repository_cloner,incremental}.py`.
- `backend/app/indexer/parsers/{base,registry}.py`.
- `backend/app/graph/{models,neo4j_repository,hop_budget,interfaces}.py`.
- `backend/app/indexer/hypotheses/{repository_evidence,shadow_runner,deterministic_generator,llm_generator,generator_registry}.py`.
- `backend/app/knowledge_engine/{shadow_compare,materializer,validators/*}.py`.
- `backend/app/indexer/graph/cross_repo_linker.py`.
- `backend/app/api/v1/routers/architecture.py`.
- `backend/app/analysis/graph/neo4j_impact_reader.py`.
- ADR: `docs/adr/0018-engineering-intelligence-platform.md`,
  `docs/adr/0020-incremental-indexing.md` (consulted for terminology only,
  cross-checked against the code above).
