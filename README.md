# ChangeGuard

ChangeGuard turns an AI-generated development plan into a **visible, reasoned impact analysis**: a dependency graph of the affected services, a UML/sequence view of the change's call path, and an AI-grounded explanation of what breaks and why — before the change ships.

> **Status:** JWT authentication, a real GitHub integration (connect an account, list/select repositories, receive pull request webhooks), a deterministic architecture discovery engine (clone a Java/Spring Boot repo, parse it with tree-sitter, persist the discovered controllers/services/Feign clients/Kafka usage/dependencies as a graph in Neo4j), and deterministic pull request impact analysis (map a PR's changed files to that graph, traverse it, and return a risk level plus every directly/indirectly impacted service, API, Kafka topic, and dependency) are implemented. The dashboard pages still run on mock data, no AI/LLM reasoning exists anywhere yet, and login-via-GitHub (as opposed to connecting one) is still just an interface + stub routes. See [`docs/architecture/overview.md`](docs/architecture/overview.md) for what's here and what's deliberately not.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS, React Router |
| Backend | Python, FastAPI, SQLAlchemy (async), Pydantic |
| Database | PostgreSQL |
| Graph store | Neo4j (architecture graph — repository indexer output) |
| Integrations | GitHub (OAuth connect, repo selection, PR webhook, PR changed-file listing) |
| Future | Jira integration, AI/LLM-backed analysis engine, login-via-GitHub |

## Project layout

```
changeguard/
  frontend/   React + TypeScript SPA
  backend/    FastAPI service (api / services / models / schemas / database / core / graph / ai / integrations / indexer / analysis)
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

Prefer running Python/Node natively instead of in containers? See [`docs/setup.md`](docs/setup.md) for that path (`scripts/setup.sh` + `scripts/dev.sh`), plus a production-style build (`scripts/docker-prod.sh`) and the fully manual, non-scripted version of each. See [`docs/architecture/overview.md`](docs/architecture/overview.md) for the backend's layering and where the remaining future integrations (Jira, AI engine) plug in.

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
