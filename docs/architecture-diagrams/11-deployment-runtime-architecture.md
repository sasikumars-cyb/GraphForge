# 11. Deployment / Runtime Architecture

GraphForge ships as Docker Compose stacks; there is no Kubernetes manifest,
Terraform apply target for the app itself, or serverless config in this
repository — `docs/deployment/08_TERRAFORM_STRUCTURE.md` and friends
describe infrastructure documentation, but the actual runnable artifacts
found in-repo are the Dockerfiles + Compose files below.

## 11.1 Dev stack (`docker/docker-compose.yml`, project `graphforge-dev`)

```mermaid
flowchart TB
    subgraph DevNet["Docker network 172.23.0.0/16"]
        DB[("db: postgres:16-alpine<br/>host 5433 → container 5432<br/>healthcheck: pg_isready")]
        NEO4J[("neo4j: neo4j:5-community<br/>host 7474 (Browser) + 7687 (Bolt)<br/>NEO4J_AUTH=neo4j/graphforge-dev")]
        BE["backend: Dockerfile target=dev<br/>uvicorn --reload, host 8000<br/>bind-mounted ../backend source<br/>optional ~/.aws:/root/.aws:ro (Bedrock)"]
        FE["frontend: Dockerfile target=dev<br/>Vite dev server, host 5173<br/>bind-mounted ../frontend source"]
    end
    BE -- "depends_on: service_healthy" --> DB
    BE -- "depends_on: service_healthy" --> NEO4J
    FE -- "depends_on" --> BE
    Browser["Developer's browser"] --> FE
    FE -- "VITE_API_BASE_URL=<br/>http://localhost:8000/api/v1" --> BE
```

## 11.2 Prod-style stack (`docker/docker-compose.prod.yml`, project `graphforge-prod`)

```mermaid
flowchart TB
    subgraph ProdNet["Compose network (default)"]
        DBp[("db: postgres:16-alpine<br/>no host port published<br/>POSTGRES_PASSWORD required, no default")]
        NEOp[("neo4j: neo4j:5-community<br/>no host port published<br/>NEO4J_PASSWORD required, no default")]
        BEp["backend: Dockerfile target=runtime<br/>no --reload, host 8000<br/>JWT_SECRET_KEY / TOKEN_ENCRYPTION_KEY<br/>required, no insecure default accepted<br/>(core/config.py fails fast if unset)"]
        FEp["frontend: Dockerfile target=runtime<br/>static build served by Nginx<br/>host 8080 → container 80<br/>VITE_API_BASE_URL=/api/v1 baked at build time<br/>(same-origin, not localhost)"]
    end
    BEp --> DBp
    BEp --> NEOp
    FEp -- "nginx.conf: /api/ proxy_pass → backend:8000" --> BEp
```

## 11.3 Backend process — single FastAPI app, embedded worker

```mermaid
flowchart TB
    subgraph Process["One backend container/process (uvicorn)"]
        FastAPI["FastAPI ASGI app<br/>(app.main:app)"]
        Lifespan["lifespan() — startup:<br/>1. idempotent ALTER TABLE / CREATE TABLE IF NOT EXISTS<br/>   (lightweight schema patch, not a full migration)<br/>2. reclaim_expired_leases_once()<br/>3. recover_orphaned_runs()<br/>4. sync_all_knowledge_connections_to_tools()<br/>5. store.refresh() — load AI provider config snapshot<br/>6. start Worker().run_forever() as asyncio.Task<br/>7. start run_stale_run_sweep_forever() as asyncio.Task"]
        WorkerTask["Worker.run_forever()<br/>polls JobQueue every 1s,<br/>SELECT...FOR UPDATE SKIP LOCKED claim,<br/>dispatches to registered handler,<br/>each job its own asyncio.create_task"]
        SweepTask["run_stale_run_sweep_forever()<br/>periodic wall-clock backstop for<br/>Runs whose in-process task died silently"]
        FastAPI --> Lifespan
        Lifespan --> WorkerTask
        Lifespan --> SweepTask
    end
    Alembic["backend/alembic/<br/>versioned migrations<br/>(separate, explicit `alembic upgrade` step —<br/>not run automatically at startup)"]
    Process -.-> Alembic
```

## 11.4 Other Compose variants present in the repo

| File | Purpose (per its own header comment) |
|---|---|
| `docker/docker-compose.yml` | One-command local dev: Postgres + backend (hot reload) + frontend (Vite dev server). |
| `docker/docker-compose.prod.yml` | Production-style: no `--reload`, static Nginx-served frontend. |
| `docker/docker-compose.override.yml` | Compose override layer (auto-merged with `docker-compose.yml` by Docker Compose convention). |
| `docker/docker-compose.local-repos.yml` | Adds a bind-mounted host directory for `local_repos_root` (indexing local, non-GitHub repos). |
| `docker/docker-compose.local.yml` | Local-environment variant (not deep-read; name suggests dev-adjacent). |
| `docker/docker-compose.demo.yml` | Demo environment — pairs with `demo/DEMO_GUIDE.md` and `vcs_provider=local_git`. |
| `docker/docker-compose.ec2-demo.yml` | EC2-hosted demo variant, used by `scripts/deploy-ec2-demo.sh`. |

## Explanation

**Deployment topology** is consistently three containers (Postgres, Neo4j,
backend) plus a frontend container that is either a Vite dev server (dev
stack) or an Nginx-served static build (prod stack) — no separate worker
process/container exists in any Compose file found; the background job
worker runs **embedded inside the same FastAPI process** as an
`asyncio.Task` started from the app's `lifespan` context manager. The
`Worker` class's own docstring explicitly notes this doesn't prevent a
future dedicated worker process from also polling the same Postgres queue
concurrently (`SELECT ... FOR UPDATE SKIP LOCKED` is safe for N workers),
but no such second process is configured anywhere in this repo today.

**Startup recovery** runs three sweeps before the app accepts traffic:
reclaiming expired job leases, recovering `Run`s left orphaned by a crashed
prior process, and re-syncing Knowledge Connections into the in-memory Tool
Registry (which does not persist across restarts on its own).

**Configuration** is entirely environment-variable driven
(`core/config.py::Settings`, a single Pydantic `BaseSettings`) — the
codebase's own rule is that this is the *only* place allowed to read
`os.environ`. Production is guarded by a fail-fast validator
(`_reject_insecure_defaults_in_production`) that refuses to start if
`ENVIRONMENT=production` while `jwt_secret_key`/`token_encryption_key`/
`neo4j_password` still hold their public, checked-in dev defaults.

**Database migrations**: `backend/alembic/` contains a full versioned
migration history, run as an explicit separate step — the lifespan hook
only applies small, idempotent `IF NOT EXISTS` patches (e.g. a `role`
column, the `knowledge_connections` table) as a safety net for
environments that haven't run Alembic yet, not as a substitute for it.

## Confirmed vs. Uncertain

- **Confirmed**: all container/service definitions, healthchecks, port
  mappings, and the embedded-worker startup sequence — read directly from
  the Compose files and `backend/app/main.py`.
- **Uncertain / requires verification**: the exact contents of
  `docker-compose.local.yml` and `docker-compose.override.yml` were
  identified by filename/directory listing only, not read in full; their
  role is inferred from naming convention, not confirmed line-by-line.
- **Uncertain**: whether any real (non-Docker-Compose) production
  deployment exists — `docs/deployment/` describes AWS/Terraform-based
  infrastructure extensively, but no Terraform state, CDK, or Kubernetes
  manifest was found in this repository to corroborate it as implemented
  (vs. documented/planned).

## Sources

- `docker/docker-compose.yml`, `docker/docker-compose.prod.yml` (full reads).
- `docker/docker-compose.{local-repos,local,override,demo,ec2-demo}.yml`
  (header/existence only).
- `docker/nginx/nginx.conf`, `docker/Caddyfile`.
- `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage: `dev`/`runtime`
  targets referenced by the Compose files).
- `backend/app/main.py::create_app`/`lifespan` (full read).
- `backend/app/core/config.py` (full read).
- `backend/alembic/` — directory presence, `env.py`, `versions/`.
- `scripts/{docker-dev.sh,docker-prod.sh,deploy-ec2-demo.sh,demo-up.sh,local-repos-up.sh,local-up.sh}` —
  existence only, confirming which Compose file each convenience script
  targets by name.
