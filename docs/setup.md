# Running locally

Three ways to run ChangeGuard locally, from least to most setup required. All three give you the same app; pick based on what you already have installed.

## Option A — fully containerized, one command (recommended)

Requires only Docker (Docker Desktop, or Docker Engine + the Compose plugin) — no local Python or Node install needed.

```bash
./scripts/docker-dev.sh
```

This builds and starts all four services with hot reload:

- Frontend (Vite dev server, HMR): `http://localhost:5173`
- Backend (uvicorn `--reload`): `http://localhost:8000`, docs at `http://localhost:8000/docs`
- Postgres: `localhost:5432`
- Neo4j (architecture graph store): bolt on `localhost:7687`, browser UI at `http://localhost:7474` (user `neo4j`, password `changeguard-dev` — see `docker/docker-compose.yml`)

Both `backend/` and `frontend/` are bind-mounted into their containers, so edits on the host take effect immediately — no rebuild, no restart. Stop with `Ctrl+C`; add `-d` inside the script (or run the underlying `docker compose ... up --build -d` directly) to run detached.

Equivalent raw command, if you'd rather not use the script:

```bash
docker compose -f docker/docker-compose.yml up --build
```

## Option B — native processes, Postgres in Docker

Faster iteration if you already have Python and Node installed and prefer running them directly rather than in containers.

Prerequisites: Python 3.12+, Node.js 20+, Docker (for Postgres only).

```bash
./scripts/setup.sh   # copies .env.example files, installs dependencies
./scripts/dev.sh      # starts Postgres in Docker, backend and frontend as native processes
```

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`, docs at `http://localhost:8000/docs`
- Postgres: `localhost:5432`
- Neo4j: bolt on `localhost:7687`, browser UI at `http://localhost:7474`

### Manual, non-scripted version of Option B

**Database + graph store:**

```bash
docker compose -f docker/docker-compose.yml up -d db neo4j
```

**Backend:**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # adjust DATABASE_URL, NEO4J_URI etc. if needed
alembic upgrade head    # creates the users/repositories/indexing_jobs tables etc.
uvicorn app.main:app --reload
```

Indexing also needs `git` on `PATH` (used to shallow-clone repositories) — already present on most dev machines and installed in both Docker images.

Backend quality checks:

```bash
ruff check .
black --check .
mypy app
pytest
```

**Frontend:**

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Frontend quality checks:

```bash
npm run lint
npm run format:check
npx tsc -b
npm run test
npm run build
```

## Option C — production-style build

Closest to how this would actually be deployed: backend runs without `--reload`, frontend is a static build served by Nginx (which proxies `/api` to the backend). No hot reload, no bind mounts — this rebuilds the image on every change, which is the point: it's for testing the real build, not for day-to-day development.

```bash
./scripts/docker-prod.sh
# or: docker compose -f docker/docker-compose.prod.yml up --build
```

- Frontend: `http://localhost:8080`
- Backend: `http://localhost:8000`
- Postgres: `localhost:5432`
- Neo4j: bolt on `localhost:7687`, browser UI at `http://localhost:7474`

Options A and C use distinct Compose project names (`changeguard-dev` / `changeguard-prod`), so their volumes and networks never collide — you can run either one without tearing down the other first.

## Logging in

There's no sign-up page yet — the frontend only has a login page, by design (see [ADR 0005](adr/0005-authentication.md)). Create a test account once via the API, then log in normally at `http://localhost:5173`:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "a-password-at-least-8-chars", "full_name": "Your Name"}'
```

Or use Swagger UI at `http://localhost:8000/docs` → `POST /auth/register` → "Try it out".

`JWT_SECRET_KEY` has an insecure default (`dev-only-insecure-secret-change-me`) so login works with zero config locally — see `backend/.env.example`. Any real deployment must override it with a long random value (`openssl rand -hex 32`).

GitHub OAuth ("Continue with GitHub" on the login page) is intentionally disabled — `GET /api/v1/auth/github/login` returns `501 not_implemented` until a provider is registered. See ADR 0005 for the extension point. This is a *different* thing from "Connect GitHub" in Settings, below — see ADR 0006.

## Connecting GitHub

"Connect GitHub" in Settings links a GitHub account for repo access (it is not how you log in — see above). It's disabled by default; enabling it needs your own GitHub OAuth App.

**Use a personal GitHub account and a personal OAuth App — never a company-owned one.** This applies regardless of what you're testing with.

1. On GitHub: **Settings → Developer settings → OAuth Apps → New OAuth App.**
   - Homepage URL: `http://localhost:5173`
   - Authorization callback URL: `http://localhost:8000/api/v1/github/callback` (must match `GITHUB_OAUTH_REDIRECT_URI` exactly)
2. Copy the generated Client ID, and generate a Client Secret.
3. Set them in `backend/.env`:
   ```bash
   GITHUB_CLIENT_ID=<your client id>
   GITHUB_CLIENT_SECRET=<your client secret>
   ```
4. Restart the backend. In the app: **Settings → Integrations → Connect**, authorize on GitHub, and you'll land back on Settings with your repositories listed — check the ones you want tracked and **Save selection**.

### Receiving pull request webhooks

Webhook registration on the GitHub repo itself is a manual step (not automated — see [ADR 0006](adr/0006-github-integration.md)). GitHub also needs to reach your endpoint over the public internet, so `localhost` alone won't work — use a tunnel (e.g. `ngrok http 8000`) during local testing, or a real deployed URL.

1. Set a shared secret in `backend/.env`:
   ```bash
   GITHUB_WEBHOOK_SECRET=<any random string, e.g. `openssl rand -hex 32`>
   ```
2. On the GitHub repo (one you selected above, and again, your own — not a company repo): **Settings → Webhooks → Add webhook.**
   - Payload URL: `https://<your-tunnel-or-deployed-host>/api/v1/webhooks/github`
   - Content type: `application/json`
   - Secret: the same value as `GITHUB_WEBHOOK_SECRET`
   - Events: select **"Pull requests"** only (not "Send me everything")
3. GitHub sends a `ping` event immediately when the webhook is created — a `200 {"status": "pong"}` response there confirms it's wired up correctly. From then on, opening/updating/closing/merging a PR on that repo will show up via `GET /api/v1/repositories/{id}/pull-requests`.

## Indexing a repository's architecture

Discovers a tracked repository's Java/Spring Boot architecture (controllers, endpoints, services, Feign clients, Kafka producers/consumers, Maven dependencies) and stores it as a graph in Neo4j — deterministic, no AI involved. See [ADR 0007](adr/0007-architecture-discovery-engine.md) for the full design; only Java + Spring Boot (single-module Maven) repositories are supported today.

Requires a repository already tracked via "Connect GitHub" above (or any repository the backend process can reach and `git clone` — private repos use the stored, encrypted GitHub access token automatically).

Via Swagger UI (`http://localhost:8000/docs`), authenticated as the tracked repository's owner:

1. `POST /api/v1/repositories/{id}/index` — returns `202` immediately with a `pending` `IndexingJob`; the actual clone/parse/persist pipeline runs in the background. Returns `409` if a job is already `pending`/`running` for that repository, or `422 unsupported_repository` once it determines the repo isn't Java/Spring Boot.
2. `GET /api/v1/repositories/{id}/graph` — the full discovered graph (nodes + edges).
3. `GET /api/v1/repositories/{id}/services` — just the discovered components (controllers, services, Feign clients, and any other class involved in Kafka messaging).
4. `GET /api/v1/repositories/{id}/dependencies` — just the direct Maven dependencies.

You can also inspect the graph directly in Neo4j's browser UI at `http://localhost:7474` (user `neo4j`, password `changeguard-dev` for local dev) with a query like:

```cypher
MATCH (n {repository_id: "<repository-id-from-the-API>"}) RETURN n
```
