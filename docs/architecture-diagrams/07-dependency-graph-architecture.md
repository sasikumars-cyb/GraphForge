# 7. Dependency Graph Architecture

## 7.1 Code entity → graph entity mapping

```mermaid
flowchart LR
    subgraph ParsedEntities["ArchitectureModel entities (indexer/models/architecture.py)"]
        Controllers["controllers[]<br/>+ endpoints[]"]
        Services["services[]"]
        FeignClients["feign_clients[]"]
        KafkaProd["kafka_producers[]"]
        KafkaCons["kafka_consumers[]"]
        MavenDeps["maven_dependencies[]"]
        PyModules["python_modules[]<br/>+ classes[] + functions[]"]
        PyDeps["python_dependencies[]"]
        SparkRW["spark_table_reads[] /<br/>spark_table_writes[]"]
    end

    subgraph GraphEntities["Neo4j graph entities (graph/models.py: GraphNode/GraphEdge)"]
        NController["(:Component:Controller)"]
        NService["(:Component:Service)"]
        NFeign["(:Component:FeignClient)"]
        NEndpoint["(:Endpoint)"]
        NTopic["(:KafkaTopic)"]
        NDep["(:Dependency)"]
        NModule["(:Component:Module)"]
        NClass["(:Component:Class)"]
        NFunction["(:Component:Function)"]
        NTable["(:DataTable)"]
        NRepo["(:Repository)"]
    end

    Controllers -->|"build_graph()"| NController
    Controllers -->|"per endpoint"| NEndpoint
    NController -->|"EXPOSES"| NEndpoint
    Services --> NService
    FeignClients --> NFeign
    NFeign -->|"CALLS"| NEndpoint
    KafkaProd --> NTopic
    NController -.->|"owner CALLS/PRODUCES_TO"| NTopic
    KafkaCons --> NTopic
    MavenDeps --> NDep
    PyModules --> NModule
    PyModules -->|"classes"| NClass
    PyModules -->|"functions"| NFunction
    NClass -->|"CONTAINS"| NFunction
    NModule -->|"IMPORTS"| NModule
    NFunction -->|"CALLS (unambiguous bare name only)"| NFunction
    PyDeps --> NDep
    SparkRW --> NTable

    NRepo -->|"CONTAINS (every top-level node)"| NController
    NRepo -->|"CONTAINS"| NService
    NRepo -->|"CONTAINS"| NModule
    NRepo -->|"CONTAINS"| NTopic
    NRepo -->|"CONTAINS"| NDep
```

## 7.2 Node identity scheme and idempotency

```mermaid
flowchart TB
    A["Node id = f'{repository_id}:{kind}:{key}'<br/>e.g. '{repo}:controller:{package}.{name}',<br/>'{repo}:function:{qualified_name}',<br/>'{repo}:kafka-topic:{topic}'"]
    A --> B["Re-indexing the same repository always<br/>produces the same ids"]
    B --> C["Neo4j write = Cypher MERGE, not CREATE<br/>(graph/neo4j_repository.py)"]
    C --> D["Idempotent upsert:<br/>safe to run the full pipeline<br/>repeatedly with no duplicate nodes"]
    A --> E["Ids are namespaced per-repository →<br/>never collide across repositories"]
    E --> F["Cross-repository edges are a SEPARATE,<br/>explicit step (cross_repo_linker.relink_account)<br/>— not implied by shared ids"]
```

## 7.3 How graph data relates to PostgreSQL / indexed data

```mermaid
flowchart LR
    PG_Repo[("PostgreSQL:<br/>models.Repository<br/>(id, html_url, owner, name,<br/>last_indexed_commit_sha,<br/>last_indexed_language)")]
    Neo_Repo[("Neo4j:<br/>(:Repository) node, id = '{repository_id}:repository'")]

    PG_Repo -- "repository_id (UUID, string form)<br/>is the join key —<br/>no foreign key at the DB level<br/>(different databases)" --> Neo_Repo

    PG_IdxJob[("PostgreSQL:<br/>models.IndexingJob")] -- "records indexing run outcome" --> PG_Repo
    PG_PRAnalysis[("PostgreSQL:<br/>models.PullRequestAnalysis")] -- "stores impact-analysis result<br/>DERIVED from the Neo4j graph<br/>(denormalized snapshot, not live)" --> Neo_Repo
    PG_EM[("PostgreSQL:<br/>Engineering Memory<br/>(beliefs, hypotheses, evidence)")] -. "shadow-only; diagnostic<br/>correlation, not a live join" .-> Neo_Repo
```

## Explanation

**How code entities become graph entities.** `app/indexer/graph/builder.py`
is the single translation point (its own docstring: "the one place that
knows how a discovered Java entity maps to a graph label and relationship
type"). It is purely a data transform — `ArchitectureModel → GraphPayload`
— with no I/O of its own; persistence happens afterward via
`Neo4jGraphRepository`. Python and Java entities are deliberately mapped
onto the *same* `Component` label family (plus a specific secondary label
like `Class`/`Controller`) so a caller querying the graph cannot tell which
language produced a given `Component` node without reading its
`properties.language` field.

**How relationships are created.** Every relationship in the graph traces
to one specific parse-time fact: a Controller's `@RequestMapping` becomes an
`EXPOSES` edge to an `Endpoint` node; a Feign client's target becomes a
`CALLS` edge to that `Endpoint`; a `@KafkaListener`/producer call becomes
`CONSUMES_FROM`/`PRODUCES_TO` to a `KafkaTopic`; a Python `import` becomes
`IMPORTS`; a function call becomes `CALLS` **only when the callee's bare
name is unambiguous repository-wide** — an ambiguous name (same method name
on two unrelated classes) is deliberately left unresolved rather than
guessed, matching this codebase's stated no-guessing precedent (ADR 0007).

**How Neo4j is used.** As the single graph store for the whole application
— there is no separate "dependency graph" database distinct from the
"architecture knowledge graph"; they are the same Neo4j instance and the
same node/edge set. `graph/neo4j_repository.py` is the only class issuing
Cypher; every reader (agents, the deterministic impact engine, the
Architecture API) goes through it or its narrower siblings
(`analysis/graph/neo4j_impact_reader.py`, `graph/test_case_repository.py`).

**How graph data relates to PostgreSQL/indexed data.** The two stores are
joined only by convention — `repository_id` (a Postgres UUID, stringified)
is embedded in every Neo4j node id for that repository — **not** by any
cross-database foreign key or transaction. `PullRequestAnalysis` rows in
Postgres are a point-in-time, denormalized *snapshot* of a graph traversal
result (recomputed on each impact-analysis request, not kept live-synced).
Engineering Memory (`beliefs`/`hypotheses`/`evidence` tables) is populated
by the shadow hypothesis pipeline described in
[06-indexing-knowledge-architecture.md](06-indexing-knowledge-architecture.md)
and is explicitly diagnostic — it does not feed back into the graph agents
actually query.

## Confirmed vs. Uncertain

- **Confirmed**: node id scheme, MERGE-based idempotency, and every
  relationship type in §7.1's table — read directly from `builder.py`.
- **Confirmed**: `repository_id` as the sole join convention between
  Postgres and Neo4j (no shared foreign-key constraint is possible between
  the two engines) — read from `models/repository.py` and
  `builder.py::_repository_node_id`.
- **Uncertain / requires verification**: whether every `ArchitectureModel`
  field (e.g. `spark_table_reads`/`writes`) is populated for both supported
  languages or Java-only — the extractor call sites inside
  `parsers/java/spring_boot_parser.py` and `parsers/python/python_parser.py`
  were not individually read line-by-line to confirm.

## Sources

- `backend/app/indexer/graph/builder.py` (full read).
- `backend/app/graph/models.py` (full read — `GraphNode`/`GraphEdge`/`GraphPayload`).
- `backend/app/graph/neo4j_repository.py` (Cypher `MERGE` pattern, grep of
  relationship-type usage).
- `backend/app/indexer/models/architecture.py` — `ArchitectureModel` shape.
- `backend/app/models/repository.py`, `models/pull_request_analysis.py`,
  `models/indexing_job.py`.
- `backend/app/knowledge_engine/materializer.py`, `knowledge_engine/shadow_compare.py`.
