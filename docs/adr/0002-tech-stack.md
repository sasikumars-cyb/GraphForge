# ADR 0002: Technology stack

## Status
Accepted

## Context
Stack was specified directly: React, TypeScript, Vite, Tailwind CSS, and React Router on the frontend; Python, FastAPI, SQLAlchemy, and Pydantic on the backend; PostgreSQL as the database. Several implementation choices within that stack still needed deciding.

## Decisions

- **Async SQLAlchemy 2.0 + asyncpg**, not sync SQLAlchemy. FastAPI's performance case depends on non-blocking I/O; a sync driver would block the event loop under load. Alembic is configured for async migrations from the start.
- **`pyproject.toml` (PEP 621)** as the single source of backend project metadata, dependencies, and tool configuration (ruff, black, mypy, pytest), instead of a separate `requirements.txt`.
- **Tailwind CSS v4 via the `@tailwindcss/vite` plugin**, not the older PostCSS-config setup — v4's Vite-native integration is the currently recommended path and removes a config file.
- **oxlint (kept as Vite's current scaffold default) + Prettier**, rather than migrating to a hand-rolled ESLint config. oxlint is the linter the official `create-vite` React+TS template ships with today and already includes React/TypeScript rule sets; Prettier remains the formatter, since oxlint does not format. Revisit if a rule only available in an ESLint plugin (e.g. a specific `jsx-a11y` check) becomes necessary.
- **Vitest + React Testing Library**, the native pairing for a Vite app — shares Vite's transform pipeline, no separate Jest config.
- **Neo4j is not in `docker-compose.yml` yet.** It's listed under "Future" and there is no graph data to store; adding it now would be an idle service with no consumer.

## Consequences
Tooling choices favor whatever the current official scaffolding recommends over a from-scratch configuration, on the reasoning that fighting the framework's own defaults costs more over time than it buys in this case. This decision should be revisited if a specific gap (e.g. a lint rule oxlint doesn't support) is hit in practice.
