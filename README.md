# GraphForge

GraphForge turns an AI-generated development plan into a **visible, reasoned impact analysis**: a dependency graph of the affected services, a UML/sequence view of the change's call path, and an AI-grounded explanation of what breaks and why — before the change ships.

> **Status:** GraphForge has grown well beyond the description below — an Agent Orchestrator, 12+ registered agents, a five-stage Knowledge Engine, and Engineering Memory are all implemented and tested, alongside the original deterministic core (JWT auth, real GitHub integration, tree-sitter-based architecture discovery, and deterministic PR impact analysis). For an honest, evidence-cited account of what's real, what's partial, and what's still a documented gap, see [`docs/handbook/16_REALITY_CHECK.md`](docs/handbook/16_REALITY_CHECK.md) — the current source of truth. [`docs/architecture/overview.md`](docs/architecture/overview.md) documents the original deterministic backend in detail and is explicitly marked historical for anything beyond it.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS, React Router |
| Backend | Python, FastAPI, SQLAlchemy (async), Pydantic |
| Database | PostgreSQL |
| Graph store | Neo4j (architecture graph — repository indexer output) |
| Integrations | GitHub (OAuth connect, repo selection, PR webhook, PR changed-file listing, login-via-GitHub), Jira (read-only search/enrichment — see [`docs/handbook/16_REALITY_CHECK.md`](docs/handbook/16_REALITY_CHECK.md)) |
| AI / Agents | Agent Orchestrator, 12+ registered agents, five-stage Knowledge Engine, Engineering Memory — see [`docs/handbook/16_REALITY_CHECK.md`](docs/handbook/16_REALITY_CHECK.md) |
| Roadmap | Confluence integration, Jira/Confluence Entry Resolvers — see [`docs/handbook/16_REALITY_CHECK.md`](docs/handbook/16_REALITY_CHECK.md) § Roadmap |

## Project layout

```
graphforge/
  frontend/   React + TypeScript SPA
  backend/    FastAPI service (api / services / models / schemas / database / core / graph / ai /
              integrations / indexer / analysis / agents / orchestrator / knowledge_engine /
              context_pipeline / learning_engine)
  docs/       Architecture notes and Architecture Decision Records (ADRs)
  docker/     Compose orchestration, Nginx config, DB init scripts
  scripts/    Local dev convenience scripts
```

## Getting started

Requires only Docker — no local Python or Node install needed:

```bash
./scripts/docker-dev.sh
```

One command starts everything with hot reload: Postgres, Neo4j (bolt on `7687`, browser UI at `http://localhost:7474`), backend (`uvicorn --reload`) at `http://localhost:8000` (docs at `/docs`), and frontend (Vite dev server, HMR) at `http://localhost:5173`. Both `backend/` and `frontend/` are bind-mounted into their containers, so edits on the host apply immediately — no rebuild needed.

There's no sign-up page yet — create a test account with `curl -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application/json" -d '{"email": "you@example.com", "password": "a-password-at-least-8-chars", "full_name": "Your Name"}'` (or via Swagger at `/docs`), then log in at `http://localhost:5173`. See [`docs/setup.md`](docs/setup.md#logging-in) for details.

Prefer running Python/Node natively instead of in containers? See [`docs/setup.md`](docs/setup.md) for that path (`scripts/setup.sh` + `scripts/dev.sh`), plus a production-style build (`scripts/docker-prod.sh`) and the fully manual, non-scripted version of each. See [`docs/architecture/overview.md`](docs/architecture/overview.md) for the original deterministic backend's layering, and [`docs/graphforge/ARCHITECTURE.md`](docs/graphforge/ARCHITECTURE.md) for where the remaining future integrations (Confluence, and the Jira/Confluence Entry Resolvers) plug in — Jira (read-only) and login-via-GitHub are already implemented.

## Development scripts

| Script | Purpose |
|---|---|
| `scripts/docker-dev.sh` | **One command, full stack, hot reload — start here** |
| `scripts/docker-prod.sh` | Production-style build (Nginx, no reload) |
| `scripts/setup.sh` | First-time environment setup for native (non-Docker) development |
| `scripts/dev.sh` | Run the full stack natively (Postgres in Docker, backend/frontend as local processes) |
| `scripts/lint.sh` | Lint + format-check both services |
| `scripts/test.sh` | Run backend and frontend test suites |

## License

Not yet decided — internal hackathon project.
