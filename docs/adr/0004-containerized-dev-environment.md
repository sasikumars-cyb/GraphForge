# ADR 0004: Containerized development environment

## Status
Accepted

## Context
The project needed to be fully containerized — Dockerfiles for backend, frontend, and Postgres, with hot reload, startable with one command — rather than the previous setup where only Postgres ran in Docker and the backend/frontend ran as native processes (`scripts/dev.sh`).

## Decision

**One Dockerfile per service, with a `dev` build stage added alongside the existing production stages**, rather than separate `Dockerfile.dev` files. `backend/Dockerfile` and `frontend/Dockerfile` each now have three stages: `dev` (used by local development), and `builder`/`runtime` (used for the production-style build). A single file per service keeps base-image and dependency-version decisions in one place instead of two files drifting apart.

**Two Compose files, not one file with profiles or an override file:**

- `docker/docker-compose.yml` — the new default. Postgres + backend (`target: dev`, `uvicorn --reload`) + frontend (`target: dev`, Vite dev server). Both `backend/` and `frontend/` are bind-mounted into their containers for hot reload.
- `docker/docker-compose.prod.yml` — unchanged in spirit from the original compose file: Postgres + backend (`target: runtime`, no reload) + frontend (`target: runtime`, static build served by Nginx).

A base-file-plus-`docker-compose.override.yml` approach was considered (Compose auto-merges an override file with the base when both sit in the current directory), but it depends on invoking `docker compose` from that exact directory with no explicit `-f` — fragile given `scripts/*.sh` invoke Compose with an absolute `-f` path from the repo root. Two fully explicit files are more files but zero ambiguity about which one is running.

**Anonymous volumes to mask host-specific directories.** Both dev-mode services bind-mount their entire source directory (`../backend:/app`, `../frontend:/app`) for hot reload, then add a second volume entry for the one subdirectory that must come from the image, not the host: `/app/.venv` for the backend, `/app/node_modules` for the frontend. Without this, the bind mount would shadow the image's own installed dependencies with whatever (possibly absent, possibly platform-incompatible) copy exists on the host.

**Vite's dev server binds all interfaces (`server.host: true`).** Vite defaults to binding only `localhost`, which is unreachable from outside its container even with the port published. This is set in `vite.config.ts` itself (not just a Docker-only CLI flag) so native `npm run dev` and the containerized dev server behave identically.

**Explicit Compose project names** (`graphforge-dev`, `graphforge-prod`) on both files. Compose derives a default project name from the containing directory, and both files live in `docker/` — without an explicit name they'd share one project, and therefore one Postgres volume and one network, letting the dev and prod stacks silently collide.

**Update (added with authentication, ADR 0005): a shared `docker-entrypoint.sh` runs `alembic upgrade head` before starting uvicorn, in both the `dev` and `runtime` stages.** Once the `users` table existed, a fresh `./scripts/docker-dev.sh` would otherwise boot successfully but fail on the first `/auth/register` call with "relation users does not exist" — the container ran, the app just had no schema yet. Auto-migrating on startup is standard for a single-instance setup like this; it would need reconsidering (a separate migration job, not baked into app startup) if this ever ran with multiple replicas racing to migrate at once.

## Consequences
- `./scripts/docker-dev.sh` is the one command that starts the full stack with hot reload; `./scripts/docker-prod.sh` is the equivalent for the production-style build. Both are thin wrappers around an explicit `docker compose -f ... up --build`.
- The previous native-process path (`scripts/setup.sh` + `scripts/dev.sh`, Postgres-only in Docker) still works unchanged and remains documented in `docs/setup.md` as Option B, for anyone who prefers running Python/Node directly over containers.
- Two Compose files and two extra Dockerfile stages are more surface area than a single file, in exchange for zero ambiguity about which stack (dev vs. prod-style) a given command starts.
