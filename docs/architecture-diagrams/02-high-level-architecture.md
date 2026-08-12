# 2. High-Level Architecture Diagram

```mermaid
flowchart TB
    subgraph Frontend["Frontend — frontend/src (React 18 + Vite + TypeScript)"]
        FEApp["App.tsx — provider tree:<br/>ErrorBoundary → ThemeProvider →<br/>QueryClientProvider → AuthProvider → AiModelProvider → Router"]
        FEPages["pages/*.tsx — ~40 route pages"]
        FEComponents["components/* — feature UI"]
        FEApiClient["lib/api/*.ts — typed fetch wrapper<br/>+ one module per router"]
        FEApp --> FEPages --> FEComponents
        FEPages --> FEApiClient
    end

    subgraph Backend["Backend — backend/app (FastAPI, single ASGI app)"]
        API["api/v1/routers/* — 30 routers<br/>mounted under /api/v1"]
        Services["services/* — request-scoped<br/>business logic"]
        Orchestrator["orchestrator/* — Registry, Selector,<br/>RunCoordinator, JobQueue, Worker"]
        Agents["agents/* — ~25 agents implementing<br/>the IAgent contract"]
        ContextPipeline["context_pipeline/reasoning/* —<br/>investigation/discovery engine"]
        AILayer["ai/* — provider resolver,<br/>factory, 5 provider adapters"]
        ToolsLayer["tools/* — ToolRegistry + ToolExecutor<br/>+ ITool implementations"]
        Indexer["indexer/* — clone → parse →<br/>build graph pipeline"]
        Analysis["analysis/* — deterministic PR<br/>impact-analysis engine"]
        KnowledgeEngine["knowledge_engine/* — hypothesis<br/>validation, materializer, memory"]

        API --> Services
        API --> Orchestrator
        Orchestrator --> Agents
        Agents --> ContextPipeline
        Agents --> AILayer
        Agents --> ToolsLayer
        Services --> Analysis
        Indexer --> KnowledgeEngine
        API --> Indexer
    end

    subgraph Persistence["Persistence"]
        PG[("PostgreSQL<br/>models/*.py — 30+ tables")]
        NEO[("Neo4j<br/>graph/*.py — architecture graph")]
    end

    subgraph External["External Integrations"]
        VCS["GitHub / local_git<br/>(IVersionControlProvider)"]
        PM["Jira, Confluence"]
        DOC["Google Drive"]
        TEST["TestRail"]
        LLMs["OpenAI, Groq, DeepSeek,<br/>Gemini, Bedrock"]
    end

    FEApiClient -- "fetch, Bearer JWT" --> API

    Services --> PG
    Orchestrator --> PG
    Agents --> PG
    Indexer --> PG

    Analysis --> NEO
    ContextPipeline --> NEO
    Agents -- "via graph_repository<br/>(hop-budgeted)" --> NEO
    Indexer --> NEO

    ToolsLayer --> VCS
    ToolsLayer --> PM
    ToolsLayer --> DOC
    ToolsLayer --> TEST
    AILayer --> LLMs
    Indexer --> VCS
```

## Explanation

The system is a conventional layered monolith with two structurally distinct
"brains" layered on top of it:

1. **A deterministic pipeline** (`indexer/` → `analysis/`) that never calls
   an LLM: clone a repository, parse it with a tree-sitter/AST-based parser,
   build a graph payload, write it to Neo4j, and (separately, on-demand)
   diff a pull request's changed files against that graph to classify risk.
2. **An agentic layer** (`agents/`, `orchestrator/`, `context_pipeline/`,
   `ai/`) where a `RunCoordinator` dispatches one of ~25 registered agents,
   each of which may call an LLM (through the provider-resolution layer in
   `ai/`), read the graph (`graph_repository`, hop-budgeted per agent), and
   call external tools (`tools/`) through a single `ToolExecutor`.

Both layers write to the same two stores: PostgreSQL (`models/`, everything
relational — runs, workflows, users, repositories, evidence, learning
events) and Neo4j (`graph/`, the architecture knowledge graph). The frontend
is a pure SPA that only talks to the backend's REST API — no direct
database or external-system access from the browser.

## Sources

- `backend/app/main.py` — top-level app wiring (`register_agents()`,
  `register_all_tools()`, router mounting).
- `backend/app/api/v1/routers/__init__.py` — router → API surface.
- `backend/app/orchestrator/{registry,selector,run_coordinator,worker,job_queue}.py`.
- `backend/app/agents/setup.py` — full agent registration list.
- `backend/app/tools/setup.py` — full tool registration list.
- `backend/app/ai/providers/factory.py`, `ai/config/resolver.py`.
- `backend/app/indexer/services/indexing_service.py`.
- `backend/app/analysis/engine/impact_analysis_engine.py`.
- `backend/app/graph/session.py`, `database/session.py`.
- `frontend/src/app/App.tsx`, `app/router.tsx`, `lib/api/client.ts`.
