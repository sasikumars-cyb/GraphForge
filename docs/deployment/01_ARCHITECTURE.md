# 01 — System Architecture

## Purpose

This document explains how GraphForge actually works today: what each part of the system does, how the parts talk to each other, and exactly where in the repository that behavior lives. Every claim below is verified against the code, not against product documentation elsewhere in `docs/` (some of which — e.g. `docs/architecture/overview.md` — describes an earlier phase of the product and has drifted from the current implementation; where they disagree, this document follows the code).

## Overview

GraphForge is a single-tenant web application: a React SPA frontend, a FastAPI backend, PostgreSQL as the primary datastore, and Neo4j as a dedicated graph store for codebase architecture. The backend orchestrates multi-stage AI-agent "workflows" (Context Discovery → Planning → Development → Testing → Documentation Planning → Engineering Review) that call out to a pluggable set of LLM providers (Bedrock, OpenAI, Gemini, Groq), and integrates with GitHub (repository access, pull requests, webhooks) and, via a generic MCP-based tool layer, Jira/Confluence.

```mermaid
flowchart LR
    Browser[React SPA<br/>frontend/] -->|"HTTPS, /api/* proxied by Nginx<br/>(prod) or Vite dev server"| API[FastAPI backend<br/>backend/app/main.py]
    API --> PG[(PostgreSQL<br/>all relational state)]
    API --> Neo[(Neo4j<br/>architecture graph)]
    API --> LLM{AI Provider<br/>resolver}
    LLM --> Bedrock[Amazon Bedrock]
    LLM --> OpenAI[OpenAI]
    LLM --> Gemini[Gemini]
    LLM --> Groq[Groq]
    API --> GitHub[GitHub REST API<br/>+ webhooks]
    API --> MCP[MCP tool layer<br/>Jira / Confluence]
```

## Frontend

**Location:** `frontend/src/`

| Concern | Where |
|---|---|
| Routing / app shell | `frontend/src/app/` |
| Route-level pages | `frontend/src/pages/*.tsx` — 21 pages: `ControlCenterPage`, `WorkspacePage`, `PlanningPage`, `DevelopmentPage`, `TestingPage`, `ReviewPage`, `WorkflowPage` (a single workflow's live/detail view), `RunHistoryPage`, `RunDetailPage` (standalone runs), `ApprovedQueuePage`, `RepositoriesPage`/`RepositoryDetailPage`, `ArchitecturePage` (the dependency graph), `PullRequestsPage`/`PullRequestDetailPage`, `ReportsPage`, `SettingsPage`, `LoginPage`, `NotFoundPage` |
| API client | `frontend/src/lib/api/client.ts` — the only module that calls `fetch`; every other `lib/api/*.ts` file (`auth.ts`, `workflows.ts`, `agentRuns.ts`, `ai-providers.ts`, `repositories.ts`, `github.ts`, `jira.ts`, `knowledge.ts`, `calibration.ts`, `tools.ts`, `system.ts`, `analysis.ts`) is a thin, typed wrapper around it |
| Auth state | `AuthProvider`/`useAuth` — holds the JWT in `localStorage`, attaches it as a bearer token to every call, fetches `GET /auth/me` whenever a token is present |
| Route guard | `frontend/src/components/layout/RequireAuth.tsx` — wraps every route except `/login` |
| Theming | `frontend/src/styles/tokens.css` + `frontend/src/styles/themes.css` — semantic CSS custom properties, 5 themes (Light/Dark/Midnight/Modern Blue/High Contrast), switched via `data-theme` on `:root` |

**API base URL**: `frontend/src/lib/api/client.ts` reads `import.meta.env.VITE_API_BASE_URL`, falling back to `http://localhost:8000/api/v1` — this is a **build-time** Vite env var, meaning the production frontend build must be built with the correct value baked in (or the Nginx same-origin proxy pattern below is used instead, which avoids needing an absolute URL at all — see `docs/deployment/03_NETWORKING.md`).

**Production serving**: `frontend/Dockerfile`'s `runtime` stage builds the SPA (`npm run build` → `frontend/dist`) and serves the static output via `nginx:1.27-alpine`. `docker/nginx/nginx.conf` proxies `location /api/` to `http://backend:8000/api/` and falls back every other path to `/index.html` for client-side routing. **This is the pattern the AWS deployment replicates** (ALB path-based routing, see `02_INFRASTRUCTURE.md`) rather than splitting frontend/backend onto different origins.

## Backend

**Location:** `backend/app/`, entrypoint `backend/app/main.py` (FastAPI app factory `create_app()`), served by `uvicorn` per `backend/Dockerfile`.

```
backend/app/
  main.py           App factory, lifespan hook (startup DDL, orphaned-run recovery, shutdown)
  core/             config.py (Settings — the ONLY module reading os.environ), security.py (bcrypt+JWT),
                     crypto.py (Fernet secret-at-rest encryption), exceptions.py, error_handlers.py,
                     logging.py, request_context.py
  api/v1/           routers/ — versioned FastAPI routers (see table below), dependencies.py (get_current_user)
  models/           SQLAlchemy ORM: user, workflow, run (agent_runs table), agent_step, repository,
                     pull_request(_analysis)(_ai_analysis), indexing_job, github_connection,
                     knowledge_connection, ai_profile, ai_provider_config, confidence_calibration
  database/         Async engine + session factory (session.py), declarative Base
  graph/            Neo4j session management (session.py — one process-wide AsyncDriver) +
                     Neo4jGraphRepository (the IGraphRepository implementation)
  ai/               Provider-agnostic LLM layer — interfaces/, providers/ (openai, gemini, bedrock,
                     factory, registry), config/ (resolver.py, store.py — see 13_AI_PROVIDER_CONFIGURATION.md),
                     services/ (ai_analysis_service.py, context_builder.py, prompt_builder.py)
  agents/           Agent framework — _contract.py (Subject DTO), setup.py (agent registration),
                     llm.py, reflection.py, verification.py, confidence.py, title_generation.py
  orchestrator/     run_coordinator.py (Run lifecycle), background_execution.py (async task scheduling,
                     orphaned-run recovery), registry.py (AgentRegistry), selector.py
  services/         workflow_service.py (WORKFLOW_TYPE_STAGES, stage sequencing), auth_service.py,
                     github_service.py, webhook_service.py
  indexer/          Codebase → Neo4j graph pipeline — scanner/ (git clone, language detection),
                     parsers/java/ (tree-sitter Spring Boot), extractors/, graph/ (builder), workers/
  analysis/         Deterministic PR impact analysis — graph/ (Neo4jImpactGraphReader), services/, engine/
  integrations/     github.py (real OAuth + repo/PR listing), local_git.py (demo mode), factory.py, interfaces.py
  tools/            MCP-based generic tool layer for Jira/Confluence — registry.py, executor.py,
                     mcp_support.py, context_builder.py
  schemas/          Pydantic request/response models
```

### API routers (`backend/app/api/v1/routers/`)

| Router | Responsibility |
|---|---|
| `auth.py` | Register/login/me — local email+password |
| `oauth.py` | GitHub login OAuth (stubbed, `501`) |
| `github.py` | "Connect GitHub" OAuth flow, repository listing/selection |
| `webhooks.py` | `POST /webhooks/github` — HMAC-verified GitHub webhook receiver |
| `repositories.py` | Tracked repositories, indexing jobs, graph read endpoints |
| `pull_requests.py` | PR listing/detail |
| `ai_analysis.py` | AI-enriched PR analysis |
| `ai_workspace.py` | AI provider configuration (installation-wide — see `13_AI_PROVIDER_CONFIGURATION.md`) |
| `agent_runs.py` | Standalone agent run creation/status (`POST /agent-runs`) |
| `workflows.py` | Multi-stage workflow creation, approval/reject/continue, replay |
| `calibration.py` | Confidence calibration data |
| `knowledge.py` | Knowledge Connections (Jira/Confluence config, synced to the tool registry) |
| `jira.py` | Jira-specific read endpoints |
| `tools.py` | Tool registry introspection |
| `system.py` | System/status surface (what `ControlCenterPage` reads) |
| `health.py` | `GET /health` — liveness only, see `12_OPERATIONS.md` for the readiness-check gap |

### Startup sequence (`app/main.py`'s `lifespan`)

1. Runs a small set of **idempotent raw DDL statements** directly (`ALTER TABLE users ADD COLUMN IF NOT EXISTS role ...`, `CREATE TABLE IF NOT EXISTS knowledge_connections ...`) — in addition to, not instead of, the Alembic migration chain in `backend/alembic/versions/`. **This is flagged as a required production change in `10_CODE_CHANGES.md`** — safe at one replica, a footgun at N.
2. `recover_orphaned_runs()` (`app/orchestrator/background_execution.py`) — marks any `Run` left `"running"`/`"queued"` by a previous process as `"failed"`. This exists *because* background execution does not survive a process restart (next section).
3. `sync_all_knowledge_connections_to_tools()` — re-activates Jira/Confluence tool registry entries for Knowledge Connections that existed before this restart.
4. On shutdown: closes the Neo4j driver's connection pool and disposes the SQLAlchemy engine's pool.

## Database (PostgreSQL)

Async SQLAlchemy (`asyncpg` driver), `backend/app/database/session.py`. Schema managed by **Alembic** (`backend/alembic/versions/`, 30+ migrations) plus the lifespan DDL noted above. Holds every piece of relational state: users, workflows, runs, agent steps, repositories, pull requests (+ their analyses), indexing jobs, GitHub connections (encrypted tokens), knowledge connections, AI provider config, confidence calibration data.

Local default: `postgresql+asyncpg://graphforge:graphforge@localhost:5432/graphforge` (overridden via `DATABASE_URL`).

## Neo4j

Stores the **architecture/dependency graph** produced by the indexer — components, modules, functions, cross-repository relationships (`app/graph/`, `app/indexer/`). Accessed via the official async Neo4j driver over Bolt, one process-wide `AsyncDriver` (`app/graph/session.py`), basic auth (`neo4j_user`/`neo4j_password`). No clustering — Community Edition, single instance, matching `docker/docker-compose.prod.yml`.

The indexer (`app/indexer/services/indexing_service.py`) shallow-clones a repository to local ephemeral disk (`indexer_clone_root`, default `/tmp/graphforge-indexer`, always cleaned up), detects its language (Java+Spring Boot only today — `app/indexer/scanner/language_detector.py`), parses with `tree-sitter`, extracts controllers/services/Feign clients/Kafka topics/Maven dependencies, and persists the result into Neo4j, replacing any prior graph for that repository.

## AI Providers

See `13_AI_PROVIDER_CONFIGURATION.md` for the full design. Summary: `app/ai/providers/registry.py` declares each provider (`OpenAIProvider`, `GeminiProvider`, `BedrockProvider`, and a Groq path via the OpenAI-compatible interface) as a `ProviderSpec` — no vendor-name `if`/`elif` chains scattered through the app. `app/ai/config/resolver.py` resolves *what to run* (provider, model, temperature, max_tokens) through a precedence chain: explicit argument → stage override/profile → **installation-wide** stored default (`AIProviderConfig`/`AISettings`, `app/ai/config/store.py`) → environment variables (`Settings.ai_provider`, currently defaults to `"openai"`). Bedrock uses `boto3`'s default credential chain — no API key is ever stored for it.

## Workflow Engine

A **Workflow** (`app/models/workflow.py`, table `workflows`) groups a sequence of agent **Runs** (`app/models/run.py`, table `agent_runs`) into one engineering lifecycle. `workflow_type` selects which stage sequence applies (`app/services/workflow_service.py`'s `WORKFLOW_TYPE_STAGES`):

| `workflow_type` | Stages |
|---|---|
| `legacy_sdlc` | the original frozen 4-stage sequence (`STAGES` constant) |
| `planning` (current default — `NewWorkflowPage` creates this) | `context_discovery` → `planning` → `development` → `testing` → `documentation_planning` → `engineering_review` |
| `auto_execution` | `generate_code` → `create_branch` → `commit_changes` → `run_tests` → `create_pull_request` → `ai_pr_review` (future — no code writes/PR-creation is wired to run automatically yet) |

Each stage is human-gated: nothing auto-advances without an explicit `/approve` (or `/continue`) call — see `ApprovalGateBanner`/`WorkflowApprovalBanner` on the frontend, `POST /workflows/{id}/continue`/`/approve`/`/reject` on the backend. A `Run`'s `status` field is `"queued" | "running" | "completed" | "partial" | "failed"` (`app/models/run.py`).

```mermaid
sequenceDiagram
    participant U as User (browser)
    participant FE as Frontend (WorkflowPage)
    participant API as Backend (workflows.py)
    participant RC as RunCoordinator
    participant BG as background_execution.py
    participant DB as PostgreSQL
    participant LLM as AI Provider

    U->>FE: Create workflow (objective text)
    FE->>API: POST /workflows
    API->>RC: create_pending_run() — status="queued"
    RC->>DB: commit Run row
    API->>BG: schedule_run_execution() — asyncio.create_task
    API-->>FE: 201, workflow_id
    BG->>RC: execute_run() — status="running"
    RC->>LLM: resolved provider call (Bedrock/OpenAI/Gemini/Groq)
    LLM-->>RC: result
    RC->>DB: persist AgentStep, status="completed"|"failed"
    loop every 2.5s
        FE->>API: GET /workflows/{id}
        API->>DB: read current state
        API-->>FE: stage status, evidence
    end
    U->>FE: Approve stage
    FE->>API: POST /workflows/{id}/continue
    Note over API,BG: repeats for next stage
```

## Authentication

Local email/password only in production today (GitHub *login* is a `501` stub — do not confuse with GitHub *repository connection*, which is real and separate). `app/core/security.py`: bcrypt password hashing (truncated to 72 bytes before hashing, matching bcrypt's own limit), JWT (`PyJWT`, HS256, `jwt_secret_key`, `sub` claim = user id, default 60-minute expiry via `access_token_expire_minutes`). Stateless — no server-side session store, no cookies; the frontend holds the JWT in `localStorage` and sends it as a bearer token on every call (`app/api/v1/dependencies.py`'s `get_current_user`, via `OAuth2PasswordBearer`).

A JWT can carry a `purpose` claim (e.g. `github_oauth_state`) scoping it to one narrow flow — `get_current_user` rejects any token carrying `purpose` as a general bearer token.

## Background Execution

**The single most important architectural fact for deployment** — covered in depth in `10_CODE_CHANGES.md`. Agent-run execution (`app/orchestrator/background_execution.py`) runs via `asyncio.create_task()` **on the same event loop as the web server** — not a durable queue (no Celery, no SQS). The module's own docstring: *"this does not survive a process restart or scale across multiple worker processes."* `recover_orphaned_runs()` (called from `main.py`'s lifespan on every boot) finds any Run left `running`/`queued` from a previous process and marks it `failed` — it does **not** resume it. The indexer's `app/indexer/workers/index_worker.py` has the identical pattern (FastAPI `BackgroundTasks`) and the identical limitation, by its own comment.

## External Integrations

| Integration | Module | Status |
|---|---|---|
| GitHub OAuth (repo connection) | `app/integrations/github.py` | Real — `GET /github/connect` → GitHub → `GET /github/callback`, tokens encrypted at rest via `app/core/crypto.py` |
| GitHub OAuth (login) | `app/api/v1/routers/oauth.py` | Stubbed, `501` |
| GitHub webhooks | `app/api/v1/routers/webhooks.py` | Real — HMAC-SHA256 verified via `GITHUB_WEBHOOK_SECRET`, handles `ping`/`pull_request` |
| Jira / Confluence | `app/tools/` (generic MCP-based tool layer) + `app/api/v1/routers/jira.py`/`knowledge.py` | Real, via each vendor's hosted MCP server (`github_mcp_default_server_url`, `jira_mcp_default_server_url`, `confluence_mcp_default_server_url` in `Settings`) — falls back to REST for Jira; Confluence's REST path is a permanent stub, so Confluence search only works through MCP |
| Local git (demo mode) | `app/integrations/local_git.py` | `vcs_provider=local_git` — an explicit opt-in for `demo/DEMO_GUIDE.md`'s environment, where "pull requests" are branches on disk instead of real GitHub PRs |

## Component Interaction — full request lifecycle example

```mermaid
flowchart TD
    A[Browser: approve a workflow stage] --> B[POST /api/v1/workflows/id/continue]
    B --> C{JWT valid?<br/>get_current_user}
    C -- no --> D[401 InvalidTokenError]
    C -- yes --> E[workflow_service: validate stage transition]
    E --> F[RunCoordinator.create_pending_run<br/>Run row, status=queued]
    F --> G[background_execution.schedule_run_execution<br/>asyncio.create_task]
    G --> H[Response: 200, run_id]
    G -.async, decoupled from request.-> I[RunCoordinator.execute_run]
    I --> J[app.ai.config.resolver.resolve stage=...]
    J --> K[Provider built via registry.ProviderSpec]
    K --> L[LLM call: Bedrock / OpenAI / Gemini / Groq]
    L --> M[Persist AgentStep + Run status]
    M --> N[(PostgreSQL)]
```

## See also

- `02_INFRASTRUCTURE.md` — how this maps onto AWS services
- `13_AI_PROVIDER_CONFIGURATION.md` — the provider resolution engine in full detail
- `10_CODE_CHANGES.md` — the background-execution durability gap and other required changes
- `docs/adr/` — original architecture decision records (still accurate for the areas they cover: auth, GitHub integration, the indexer, PR impact analysis)
