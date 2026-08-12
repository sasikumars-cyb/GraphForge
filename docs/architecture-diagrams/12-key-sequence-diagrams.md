# 12. Key Sequence Diagrams

## 12.1 GitHub webhook → pull request sync → deterministic impact analysis

```mermaid
sequenceDiagram
    participant GH as GitHub.com
    participant WH as api/v1/routers/webhooks.py
    participant WS as services/webhook_service.py
    participant PGpr as PostgreSQL (pull_requests)
    participant Client as Client (later, on demand)
    participant IAR as api/v1/routers/impact.py
    participant IAE as analysis/engine/impact_analysis_engine.py
    participant VCS as GitHubVersionControlProvider
    participant IGR as analysis/graph/neo4j_impact_reader.py
    participant NEO as Neo4j

    GH->>WH: POST /webhooks/github<br/>X-Hub-Signature-256, pull_request event
    WH->>WS: verify_signature(raw_body, header, secret)
    WS-->>WH: valid (HMAC-SHA256 compare_digest)
    WH->>WS: handle_pull_request_event(payload)
    WS->>PGpr: upsert PullRequest (state, title, ...)
    Note over WS: Metadata only — no diff fetch,<br/>no risk scoring yet.

    Client->>IAR: POST /pull-requests/{id}/analyze
    IAR->>IAE: analyze_pull_request(pull_request_id)
    IAE->>PGpr: get PullRequest, get Repository
    IAE->>IAE: GraphHealthService.for_repository()<br/>(must be indexed — else 422)
    IAE->>VCS: list_changed_files(owner, repo, pull_number)
    VCS->>GH: GET /repos/{owner}/{repo}/pulls/{n}/files
    GH-->>VCS: changed file paths
    IAE->>IGR: find_nodes_by_file_paths(repository_id, paths)
    IGR->>NEO: Cypher MATCH by file_path
    IAE->>IGR: find_downstream_apis / find_downstream_topics /<br/>find_same_repository_topic_peers /<br/>find_cross_repository_topic_peers /<br/>find_cross_repository_service_callers
    IGR->>NEO: Cypher traversals (multi-hop)
    IAE->>IAE: classify_risk() (deterministic)
    IAE->>PGpr: persist PullRequestAnalysis (upsert)
    IAE-->>IAR: PullRequestAnalysis
    IAR-->>Client: JSON response
```

## 12.2 Planning Agent run (standalone Workspace request)

```mermaid
sequenceDiagram
    participant FE as Frontend (PlanningPage)
    participant Router as api/v1/routers/agent_runs.py
    participant RC as RunCoordinator
    participant Sel as AgentSelector
    participant PF as preflight.py
    participant PA as agents/planning/agent.py::PlanningAgent
    participant CD as context_pipeline/reasoning/engine.py::discover()
    participant NEO as Neo4j
    participant LLM as agents/llm.py::invoke_llm_json()
    participant Provider as Resolved LLM provider
    participant PG as PostgreSQL

    FE->>Router: POST /agent-runs {goal: "plan_freeform", subject}
    Router->>RC: create_pending_run(subject, "plan_freeform")
    RC->>Sel: select("plan_freeform") -> "planning"
    RC->>PG: insert Run(status="queued")
    Router->>RC: execute_run(run, "planning", PlanningAgent, ...)
    RC->>PG: commit Run/AgentStep(status="running")
    RC->>PF: check_llm_provider_configured() + check_neo4j_reachable()
    RC->>PA: agent.run(AgentContext{subject, extras: {graph_repository, db, ...}})
    PA->>CD: discover(SessionContext) — standalone path<br/>(same engine a workflow's context_discovery<br/>stage would otherwise have already run)
    CD->>NEO: graph queries (investigators)
    CD-->>PA: EvidencePackage (facts + gaps)
    PA->>PA: verification.py — deterministic check of<br/>graph facts vs. what will be claimed
    PA->>LLM: invoke_llm_json(prompt, stage="planning")
    LLM->>Provider: resolve() -> ProviderSpec.build().generate()
    Provider-->>LLM: JSON completion
    LLM->>PG: persist LLMInvocation row (ADR 0012)
    LLM-->>PA: parsed PlanningResult
    PA-->>RC: AgentOutput(confidence, evidence[], result)
    RC->>PG: commit Run/AgentStep(status="completed")
    RC-->>Router: Run
    Router-->>FE: JSON (run_id, status)
    FE->>Router: GET /agent-runs/{id} (poll)
    Router-->>FE: Run (status="completed", result)
```

## 12.3 Repository indexing (full path)

```mermaid
sequenceDiagram
    participant Client
    participant Router as api/v1/routers/repositories.py
    participant IS as indexer/services/indexing_service.py
    participant Cloner as scanner/repository_cloner.py
    participant Detector as scanner/language_detector.py
    participant Parser as parsers/{java,python}/*
    participant Builder as indexer/graph/builder.py
    participant NeoRepo as graph/neo4j_repository.py
    participant NEO as Neo4j
    participant Shadow as hypotheses/shadow_runner.py
    participant PG as PostgreSQL
    participant Linker as indexer/graph/cross_repo_linker.py

    Client->>Router: POST /repositories/{id}/index
    Router->>IS: run_indexing(db, repository)
    IS->>IS: _attempt_incremental_index() -> None (first index)
    IS->>IS: index_repository(...)
    IS->>Cloner: clone_repository(html_url, ref, token)
    Cloner-->>IS: repo_path (temp dir, auto-cleanup)
    IS->>Detector: detect_language(repo_path)
    Detector-->>IS: DetectedLanguage
    IS->>Parser: get_parser(language).parse(repo_path)
    Parser-->>IS: ArchitectureModel
    IS->>IS: extract_repository_evidence(repo_path)<br/>(before clone is discarded)
    IS->>Builder: build_graph(repository_id, model)
    Builder-->>IS: GraphPayload
    IS->>NeoRepo: replace_repository_graph(repository_id, graph)
    NeoRepo->>NEO: Cypher MERGE nodes/edges,<br/>DETACH DELETE stale ones
    IS->>Shadow: run_shadow_hypothesis_generation(...)
    Shadow->>PG: persist Beliefs/Hypotheses/Evidence<br/>(shadow-mode, never affects the real graph)
    IS->>PG: repository.last_indexed_commit_sha/language/at
    IS->>Linker: relink_account(graph_repository, db, user_id)
    Linker->>PG: advisory lock (per-account, serializes concurrent relinks)
    Linker->>NEO: recompute cross-repository edges<br/>for every repository the account owns
    IS-->>Router: IndexingSummary (counts)
    Router-->>Client: JSON response
```

## 12.4 `POST /ask` — deterministic single-shot grounding

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant Router as api/v1/routers/ask.py
    participant Ground as services/ask_grounding.py::ground()
    participant Repo as models.Repository (Postgres)
    participant Impact as impact_analysis_service.py::compute_blast_radius()
    participant DepQ as dependency_query_service.py
    participant NeoRepo as graph/neo4j_repository.py
    participant NEO as Neo4j

    FE->>Router: POST /ask {question}
    Router->>Router: get_current_user (JWT)
    Router->>Ground: ground(db, user_id, question)
    Ground->>Ground: classify: _IMPACT_PATTERN match?<br/>(impact checked before dependency)
    Ground->>Repo: resolve target repository<br/>(text_relevance term matching)
    alt impact question
        Ground->>Impact: compute_blast_radius(entity_reference)
        Impact->>NeoRepo: traverse relationships
        NeoRepo->>NEO: Cypher query
        Impact-->>Ground: BlastRadius (confidence-aware,<br/>via relationship_lookup.fetch_with_confidence)
    else dependency question
        Ground->>DepQ: query dependencies
        DepQ->>NeoRepo: traverse
        NeoRepo->>NEO: Cypher query
        DepQ-->>Ground: QueryResult
    end
    Ground-->>Router: AskResponse (fact/derived fields only,<br/>no LLM call anywhere in this path)
    Router-->>FE: JSON
```

## Sources

- `backend/app/api/v1/routers/webhooks.py`, `services/webhook_service.py`
  (full reads).
- `backend/app/analysis/engine/impact_analysis_engine.py` (full read).
- `backend/app/orchestrator/run_coordinator.py` (full read).
- `backend/app/agents/planning/agent.py` (header + import list read).
- `backend/app/context_pipeline/reasoning/engine.py` (header read).
- `backend/app/agents/llm.py` — `invoke_llm_json` (referenced from
  `planning/agent.py`'s imports and `ai/config/resolver.py`).
- `backend/app/indexer/services/indexing_service.py` (full read).
- `backend/app/indexer/graph/cross_repo_linker.py` (docstring, referenced
  from `indexing_service.py`).
- `backend/app/services/ask_grounding.py` (header + classification regex
  read).
- `backend/app/api/v1/routers/{impact,repositories,ask,agent_runs}.py`.
