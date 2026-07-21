# Architecture overview

## Backend

```
backend/app/
  api/            FastAPI routers (versioned: api/v1/)
  services/       Business logic / use cases — empty, no logic implemented yet
  models/         SQLAlchemy ORM models — empty, nothing persisted yet
  schemas/        Pydantic request/response schemas
  database/       Async engine, session factory, declarative base
  core/           Settings, logging, exception hierarchy, exception handlers
  utils/          Shared stateless helpers — empty until something needs one
  integrations/   GitHub / Jira adapters — NOT implemented; interfaces.py only
  graph/          Dependency graph domain (Postgres today, Neo4j later) — interfaces.py only
  ai/             AI analysis engine — NOT implemented; interfaces.py only
  indexer/        Codebase → graph indexing pipeline — not implemented yet
```

Dependency direction: `api` → `services` → (`models`, `schemas`, and the interfaces in `graph`/`ai`/`integrations`). Nothing in `services` imports a concrete class from `graph`, `ai`, or `integrations` directly — only their `interfaces.py` contracts — so a Neo4j-backed graph store, a GitHub client, a Jira client, or an LLM-backed analysis engine can each be added later as a new file in its module with zero change to any service that will depend on it.

### Where the four future integrations plug in

| Module | Interface today | Future adapter |
|---|---|---|
| `graph/` | `IGraphRepository` | Neo4j-backed dependency graph store |
| `ai/` | `IAnalysisEngine` | LLM-backed change impact reasoning |
| `integrations/` | `IVersionControlProvider`, `IIssueTrackerProvider` | GitHub client, Jira client — **not built yet, by design** |
| `indexer/` | — | The pipeline that reads a codebase (via `integrations`) and writes graph data (via `graph`) — designed once both of those exist |

### Error handling

`core/exceptions.py` defines `AppError` (and `NotFoundError`, `ConflictError` as examples) — the only exceptions a service or router should raise for an expected failure. `core/error_handlers.py` registers three FastAPI exception handlers, most-specific first:

1. `AppError` → the exception's own status code and error code, logged at `WARNING`.
2. `RequestValidationError` → `422`, logged at `INFO`.
3. `Exception` (catch-all) → `500` with a generic message, logged at `ERROR` with a full traceback. The original exception message never reaches the client.

Every error response has the same JSON shape: `{"error": {"code": "...", "message": "..."}}`. See `backend/tests/integration/test_error_handling.py` for the tests proving this, including that the FastAPI app is built with `debug=False` specifically so this handler chain runs consistently rather than being bypassed by Starlette's own HTML debug page.

### Database & configuration

`database/session.py` creates an async SQLAlchemy engine (asyncpg) and a session factory exposed as a FastAPI dependency (`get_db_session`). `core/config.py` is the only module allowed to read environment variables directly (`Settings`, via `pydantic-settings`), cached with `lru_cache` so parsing happens once.

### API & Swagger

`api/v1/routers/` aggregates versioned routers into `api_router`, mounted in `main.py` under `settings.api_v1_prefix` (`/api/v1`). FastAPI serves Swagger UI at `/docs`, ReDoc at `/redoc`, and the raw schema at `/openapi.json` automatically — no extra wiring beyond the `title`/`description`/`openapi_tags` metadata in `main.py`.

Currently one endpoint exists: `GET /api/v1/health`, which reports process liveness only (no DB check) — see `api/v1/routers/health.py`.

## Frontend

```
frontend/src/
  app/          App shell + router config
  pages/        Route-level components
  features/     Empty — reserved for feature-sliced modules once there are features
  components/   Shared UI components
  lib/          Cross-cutting utilities (currently just the API base URL)
  hooks/ types/ Shared hooks and TypeScript types
```

No data-fetching library (TanStack Query, etc.) has been added yet — there are no real endpoints to call beyond `/health`. It gets added alongside the first real API integration, not before.

## See also

- [ADR 0001: Clean architecture (superseded)](../adr/0001-clean-architecture.md)
- [ADR 0002: Technology stack](../adr/0002-tech-stack.md)
- [ADR 0003: Backend folder structure](../adr/0003-backend-folder-structure.md)
- [Setup guide](../setup.md)
