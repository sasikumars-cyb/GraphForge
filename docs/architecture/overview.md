# Architecture overview

## Backend

```
backend/app/
  api/            FastAPI routers (versioned: api/v1/)
  services/       Business logic — auth_service, github_service, webhook_service
  models/         SQLAlchemy ORM models — user, github_connection, repository, pull_request
  schemas/        Pydantic request/response schemas
  database/       Async engine, session factory, declarative base
  core/           Settings, logging, exceptions/handlers, JWT + password hashing + token encryption
  utils/          Shared stateless helpers — empty until something needs one
  integrations/   github.py (real GitHub OAuth + repo listing) - Jira still interfaces.py only
  graph/          Architecture graph domain — Neo4jGraphRepository (real) behind IGraphRepository
  ai/             AI analysis engine — NOT implemented; interfaces.py only
  indexer/        Codebase → graph indexing pipeline (Java + Spring Boot only) — real, deterministic
    scanner/        Language detection + git cloning
    parsers/java/    tree-sitter-based Spring Boot parser + pom.xml parser
    extractors/      One module per discovery category (controllers, services, feign, kafka)
    graph/           ArchitectureModel → generic GraphPayload
    services/        Orchestrates clone → parse → build → persist
    workers/         BackgroundTasks entrypoint, tracks IndexingJob status
```

Dependency direction: `api` → `services` → (`models`, `schemas`, and the interfaces in `graph`/`ai`/`integrations`). Nothing in `services` imports a concrete class from `graph`, `ai`, or `integrations` directly — only their `interfaces.py` contracts — so a Neo4j-backed graph store, a GitHub client, a Jira client, or an LLM-backed analysis engine can each be added later as a new file in its module with zero change to any service that will depend on it.

### Where the future integrations plug in

| Module | Interface | Status |
|---|---|---|
| `graph/` | `IGraphRepository` | **Real, working** — `Neo4jGraphRepository` in `graph/neo4j_repository.py` |
| `ai/` | `IAnalysisEngine` | Not built — future LLM-backed change impact reasoning |
| `integrations/` | `IOAuthProvider` (+ `list_repositories`) | **Real, working** — `GitHubOAuthProvider` in `integrations/github.py` |
| `integrations/` | `IVersionControlProvider` | Not built — future diff-reading for AI analysis |
| `integrations/` | `IIssueTrackerProvider` | Not built — future Jira client |
| `indexer/` | `ILanguageParser` | **Real, working** — `SpringBootJavaParser` (Java + Spring Boot/Maven only); registered in `indexer/parsers/registry.py` as the extension point for future languages |

### Authentication

Local (email/password) auth is fully implemented: `POST /auth/register`, `POST /auth/login` (JSON body, returns a JWT), and `GET /auth/me` (protected). `core/security.py` handles password hashing (bcrypt) and JWT encode/decode (PyJWT, HS256); `api/v1/dependencies.get_current_user` is the dependency any protected route uses.

Login-via-GitHub is prepared but not implemented: `GET /auth/github/login` / `GET /auth/github/callback` return `501 not_implemented`. See [ADR 0005](../adr/0005-authentication.md) — including why login uses JSON bodies instead of the OAuth2 form convention, and why the `User` model's `hashed_password` is nullable.

### GitHub integration ("Connect GitHub")

A *separate* use case from login-via-GitHub above: a locally-authenticated user links a GitHub account for repo access. `GitHubOAuthProvider` (`integrations/github.py`) is a real implementation of `IOAuthProvider`, calling actual `github.com`/`api.github.com` endpoints. Flow: `GET /github/connect` (JWT, returns an authorization URL with a signed 10-minute `state`) → browser navigates to GitHub → `GET /github/callback` (no JWT; decodes `state` to find the user, exchanges the code, stores the access token encrypted via `core/crypto.py`) → redirects to `/settings?github=connected`.

`GET /github/repositories` lists live from GitHub (not persisted); `POST /repositories` replaces the tracked set with exactly what's submitted, storing metadata in the `repositories` table (one row per user per repo — the same public repo tracked by two users gets two rows).

`POST /webhooks/github` receives GitHub webhook deliveries — HMAC-SHA256 signature-verified (`GITHUB_WEBHOOK_SECRET`), not JWT-authenticated, since GitHub is the caller. Handles `ping` (acked immediately) and `pull_request` (upserted into `pull_requests`, metadata only — no diff content, no risk scoring). One event fans out to every tracked `Repository` row matching that GitHub repo. Webhook registration on the GitHub side is manual, not automated — see `docs/setup.md`.

See [ADR 0006](../adr/0006-github-integration.md) for the full reasoning, including why this doesn't reuse the login-stub's dependency getter, and how it was verified without a live GitHub account (mocked provider methods for OAuth, real hand-signed payloads for the webhook).

### Architecture discovery engine (the repository indexer)

Given a tracked repository, deterministically discovers its Java/Spring Boot architecture and persists it as a graph — no AI/LLM involved anywhere in this pipeline. Flow: `POST /repositories/{id}/index` creates a `pending` `IndexingJob` row, schedules the pipeline via FastAPI `BackgroundTasks`, and returns `202` immediately (`409` if a job is already `pending`/`running` for that repository).

The pipeline itself (`indexer/services/indexing_service.py`): shallow-`git clone` the repo to a temp dir (`indexer/scanner/repository_cloner.py`, always cleaned up) → detect the language (`indexer/scanner/language_detector.py` — a root `pom.xml` mentioning `spring-boot`, nothing else recognized yet) → parse every `.java` file with `tree-sitter` (`indexer/parsers/java/spring_boot_parser.py`), running one extractor per discovery category (`indexer/extractors/`: controllers/endpoints, services, Feign clients, Kafka producers/consumers) plus `pom.xml`'s direct Maven dependencies → merge into an `ArchitectureModel` → turn that into a generic `GraphPayload` (`indexer/graph/builder.py`) → persist via `Neo4jGraphRepository`, fully replacing any prior graph for that repository.

`GET /repositories/{id}/graph` returns the full graph; `GET .../services` and `GET .../dependencies` filter to `Component`- and `MavenDependency`-labelled nodes respectively. All four endpoints enforce the same repository-ownership check as the existing pull-request endpoints.

See [ADR 0007](../adr/0007-architecture-discovery-engine.md) for the full reasoning — why tree-sitter over JavaParser, the deterministic-only (literal-values-only) parsing philosophy, the Neo4j label/relationship-type allowlist, the node-ID namespacing scheme, and the explicit scope boundaries (Gradle, multi-module Maven, cross-file call graphs, non-literal Kafka topics, and `BackgroundTasks` as a stand-in for a real task queue).

### Error handling

`core/exceptions.py` defines `AppError` (and `NotFoundError`, `ConflictError` as examples) — the only exceptions a service or router should raise for an expected failure. `core/error_handlers.py` registers three FastAPI exception handlers, most-specific first:

1. `AppError` → the exception's own status code and error code, logged at `WARNING`.
2. `RequestValidationError` → `422`, logged at `INFO`.
3. `Exception` (catch-all) → `500` with a generic message, logged at `ERROR` with a full traceback. The original exception message never reaches the client.

Every error response has the same JSON shape: `{"error": {"code": "...", "message": "..."}}`. See `backend/tests/integration/test_error_handling.py` for the tests proving this, including that the FastAPI app is built with `debug=False` specifically so this handler chain runs consistently rather than being bypassed by Starlette's own HTML debug page.

### Database & configuration

`database/session.py` creates an async SQLAlchemy engine (asyncpg) and a session factory exposed as a FastAPI dependency (`get_db_session`). `graph/session.py` creates the analogous singleton for Neo4j — an `AsyncDriver` returned by `get_driver()`. `core/config.py` is the only module allowed to read environment variables directly (`Settings`, via `pydantic-settings`), cached with `lru_cache` so parsing happens once.

### API & Swagger

`api/v1/routers/` aggregates versioned routers into `api_router`, mounted in `main.py` under `settings.api_v1_prefix` (`/api/v1`). FastAPI serves Swagger UI at `/docs`, ReDoc at `/redoc`, and the raw schema at `/openapi.json` automatically — no extra wiring beyond the `title`/`description`/`openapi_tags` metadata in `main.py`.

`GET /api/v1/health` reports process liveness only (no DB check) — see `api/v1/routers/health.py`.

## Frontend

```
frontend/src/
  app/          App shell + router config + AuthContext/AuthProvider + useAuth
  pages/        Route-level components, including LoginPage
  features/     Empty — reserved for feature-sliced modules once there are features
  components/   Shared UI components (layout/RequireAuth.tsx is the route guard;
                 GitHubIntegrationCard.tsx is the connect/list/select UI)
  lib/          API client (client.ts, api/auth.ts, api/github.ts) + mock data for
                 the dashboard pages
  hooks/ types/ Shared hooks and TypeScript types
```

`AuthProvider` (in `app/AuthContext.tsx`) holds the JWT (persisted in `localStorage`, and now exposed via `useAuth().token` for any authenticated call beyond auth itself) and the current user, fetched via `GET /auth/me` whenever a token is present. `RequireAuth` wraps every route except `/login` and redirects to it if there's no authenticated user.

`GitHubIntegrationCard` (rendered on the Settings page) is the second real backend integration on the frontend: connection status, the "Connect" button (fetches an authorization URL, then does a top-level `window.location` navigation to it), the repository checklist, and Save/Disconnect actions. It also consumes the `?github=connected|error` query param the backend's OAuth callback redirects with.

The Dashboard, Pull Requests, Architecture, and Reports pages still render from `lib/mock/*` — GitHub connect/select is real, but nothing yet reads the persisted `repositories`/`pull_requests` data back into those pages. No data-fetching library (TanStack Query, etc.) has been added — `lib/api/client.ts`'s `apiFetch` is enough for the current handful of calls; revisit once there are many more.

## See also

- [ADR 0001: Clean architecture (superseded)](../adr/0001-clean-architecture.md)
- [ADR 0002: Technology stack](../adr/0002-tech-stack.md)
- [ADR 0003: Backend folder structure](../adr/0003-backend-folder-structure.md)
- [ADR 0004: Containerized development environment](../adr/0004-containerized-dev-environment.md)
- [ADR 0005: Authentication](../adr/0005-authentication.md)
- [ADR 0006: GitHub integration](../adr/0006-github-integration.md)
- [ADR 0007: Architecture discovery engine](../adr/0007-architecture-discovery-engine.md)
- [Setup guide](../setup.md)
