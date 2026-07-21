# ChangeGuard

ChangeGuard turns an AI-generated development plan into a **visible, reasoned impact analysis**: a dependency graph of the affected services, a UML/sequence view of the change's call path, and an AI-grounded explanation of what breaks and why — before the change ships.

> **Status:** backend scaffold with a working health endpoint, Swagger docs, database/config/logging/error-handling wired up. No business logic (change analysis, GitHub/Jira/Neo4j/AI integrations) has been implemented yet — see [`docs/architecture/overview.md`](docs/architecture/overview.md) for what's here and what's deliberately not.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS, React Router |
| Backend | Python, FastAPI, SQLAlchemy (async), Pydantic |
| Database | PostgreSQL |
| Future | Neo4j (graph storage), GitHub integration, Jira integration, AI analysis engine |

## Project layout

```
changeguard/
  frontend/   React + TypeScript SPA
  backend/    FastAPI service (api / services / models / schemas / database / core / graph / ai / integrations / indexer)
  docs/       Architecture notes and Architecture Decision Records (ADRs)
  docker/     Compose orchestration, Nginx config, DB init scripts
  scripts/    Local dev convenience scripts
```

## Getting started

Prerequisites: Docker & Docker Compose, Node.js 20+, Python 3.12+.

```bash
./scripts/setup.sh   # copies .env.example files, installs dependencies
./scripts/dev.sh      # starts Postgres, backend (http://localhost:8000), frontend (http://localhost:5173)
```

Backend interactive API docs: `http://localhost:8000/docs`.

See [`docs/setup.md`](docs/setup.md) for the manual, non-scripted setup path, and [`docs/architecture/overview.md`](docs/architecture/overview.md) for the clean-architecture layering and where the future integrations (Neo4j, GitHub, Jira, AI engine) plug in.

## Development scripts

| Script | Purpose |
|---|---|
| `scripts/setup.sh` | First-time environment setup |
| `scripts/dev.sh` | Run the full stack locally |
| `scripts/lint.sh` | Lint + format-check both services |
| `scripts/test.sh` | Run backend and frontend test suites |

## License

Not yet decided — internal hackathon project.
