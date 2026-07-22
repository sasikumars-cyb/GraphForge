# ChangeGuard — Demo Environment Assessment and Implementation Plan

> Prepared: 2026-07-22  
> Audience: Any developer cloning the repository and reproducing the demo from scratch.

---

## 1. Operating System

**Primary recommendation: Ubuntu 24.04 LTS**

- Docker Engine, Python 3.12, Node.js 22, and the `tree-sitter` native wheel all work without friction.
- Most CI/CD systems (GitHub Actions, GitLab CI) target this OS, so what works locally matches the pipeline exactly.
- The backend Dockerfile is built on `python:3.12-slim` (Debian-based), so file-permission and binary-compatibility issues are minimised when debugging containers.

**Alternatives:**

| OS | Status | Notes |
|----|--------|-------|
| macOS 14+ (Apple Silicon or Intel) | Fully supported | Docker Desktop required; `tree-sitter-java` wheels ship for `arm64` and `x86_64`. `brew` satisfies all tool prerequisites. |
| Windows 11 (WSL2 + Ubuntu 24.04) | Supported with caveats | Run everything inside the WSL2 Ubuntu instance, not PowerShell. Docker Desktop must be configured to use the WSL2 backend. Bind-mount performance inside WSL2 is acceptable but slower than native Linux. |

**Linux-specific advantage:** On Linux, Docker runs natively (no hypervisor), so hot-reload latency for both the backend (`uvicorn --reload`) and the frontend (Vite HMR) is noticeably faster than on macOS or WSL2. The `git` shell-out the indexer performs to clone repositories is also faster on Linux.

---

## 2. Development Tools

### Docker and Docker Compose

| Attribute | Value |
|-----------|-------|
| **Version** | Docker Engine 27+ / Docker Desktop 4.30+ |
| **Compose plugin** | v2.27+ (the `docker compose` subcommand, not the legacy `docker-compose` binary) |
| **Why** | The one-command dev stack (`docker-dev.sh`) runs all four services (PostgreSQL, Neo4j, backend, frontend) inside containers. This is the path of least resistance for a demo — no local Python or Node.js install required. |
| **Download** | https://docs.docker.com/get-docker/ |

**Verify:**
```bash
docker --version       # Docker version 27.x.x
docker compose version # Docker Compose version v2.27.x
```

---

### Git

| Attribute | Value |
|-----------|-------|
| **Version** | 2.40+ |
| **Why** | Required for two separate reasons: (1) cloning this repository itself; (2) the backend indexer (`app/indexer/scanner/repository_cloner.py`) shells out to `git` to shallow-clone target repositories during indexing. `git` must be on `PATH` both on the host (for cloning the project) and inside the backend container (already installed in the `Dockerfile`). |
| **Download** | https://git-scm.com/downloads |

**Verify:**
```bash
git --version   # git version 2.40.x
```

---

### Python

| Attribute | Value |
|-----------|-------|
| **Version** | 3.12 (exact — specified in `pyproject.toml` as `requires-python = ">=3.12"` and as the base image `python:3.12-slim`) |
| **Why** | Only required for Option B (native processes). Not needed for the fully-containerised Option A. |
| **Download** | https://www.python.org/downloads/ or via `pyenv` |

**Verify:**
```bash
python3 --version   # Python 3.12.x
```

---

### uv (recommended for native dev)

| Attribute | Value |
|-----------|-------|
| **Version** | Latest stable (0.4+) |
| **Why** | Not strictly required — `pip install -e ".[dev]"` works fine — but `uv` is dramatically faster for dependency resolution if you are doing native dev (Option B). The project uses `hatchling` as the build backend, which both `uv` and `pip` support. |
| **Download** | https://docs.astral.sh/uv/getting-started/installation/ |

---

### Node.js

| Attribute | Value |
|-----------|-------|
| **Version** | 22 LTS (the `frontend/Dockerfile` uses `node:22-alpine`) |
| **Why** | Only required for Option B (native frontend). Vite 8.x and TypeScript 6.x require Node.js 20+. |
| **Download** | https://nodejs.org/ or via `nvm` |

**Verify:**
```bash
node --version   # v22.x.x
npm --version    # 10.x.x
```

---

### VS Code

| Attribute | Value |
|-----------|-------|
| **Version** | 1.90+ |
| **Why** | Recommended IDE; see Section 8 for extensions. |
| **Download** | https://code.visualstudio.com/ |

---

**Tools NOT required:**

- Java / JDK — ChangeGuard *analyses* Java repositories via tree-sitter (pure Python, no JVM). No Java runtime is needed on the developer's machine.
- Maven — same reason.
- PostgreSQL client — the database runs fully inside Docker. `psql` is useful for debugging but not required for the demo.
- Neo4j Desktop — Neo4j runs inside Docker. The Neo4j Browser is accessible at `http://localhost:7474`.

---

## 3. Runtime Components

### PostgreSQL

| Attribute | Value |
|-----------|-------|
| **Image** | `postgres:16-alpine` |
| **Purpose** | Relational store for users, GitHub connections, repositories, pull requests, indexing jobs, and both deterministic and AI analysis results. |
| **Ports** | `5432` (host → container) |
| **Startup** | `docker compose -f docker/docker-compose.yml up db` |
| **Health check** | `pg_isready -U changeguard -d changeguard` (built into `docker-compose.yml`, interval 5 s, 10 retries) |
| **Credentials (dev)** | user `changeguard`, password `changeguard`, database `changeguard` |
| **Migrations** | Applied automatically by `alembic upgrade head` inside the backend container on every startup (see `docker-entrypoint.sh`) |

---

### Neo4j

| Attribute | Value |
|-----------|-------|
| **Image** | `neo4j:5-community` |
| **Purpose** | Architecture graph store. Holds nodes (Controller, Service, FeignClient, KafkaTopic, MavenDependency, Component) and relationships (EXPOSES, CALLS, PRODUCES_TO, CONSUMES_FROM, DEPENDS_ON) discovered by the indexer. Used by both the deterministic impact analysis engine (Phase 7) and the graph endpoint. |
| **Ports** | `7474` (HTTP / Browser UI), `7687` (Bolt — what the backend connects to) |
| **Startup** | `docker compose -f docker/docker-compose.yml up neo4j` |
| **Health check** | `wget -q --spider http://localhost:7474` (built into `docker-compose.yml`, interval 5 s, 20 retries) |
| **Credentials (dev)** | user `neo4j`, password `changeguard-dev` |
| **Browser UI** | `http://localhost:7474` — useful to inspect the graph after indexing |

---

### Backend API

| Attribute | Value |
|-----------|-------|
| **Runtime** | Python 3.12 + uvicorn |
| **Purpose** | All business logic: auth, GitHub OAuth, repository management, indexing pipeline, deterministic impact analysis, AI-enriched analysis. |
| **Ports** | `8000` |
| **Startup** | `docker compose -f docker/docker-compose.yml up backend` (depends on `db` and `neo4j` being healthy) |
| **Health check** | `GET http://localhost:8000/api/v1/health` — returns `{"status":"ok"}` |
| **Swagger UI** | `http://localhost:8000/docs` |
| **ReDoc** | `http://localhost:8000/redoc` |
| **Hot reload** | `uvicorn --reload` — source bind-mounted; edits on the host take effect immediately without a container restart |
| **Migrations** | `alembic upgrade head` runs before uvicorn starts (see `docker-entrypoint.sh`) |

---

### Frontend

| Attribute | Value |
|-----------|-------|
| **Runtime** | Node.js 22 + Vite 8 dev server |
| **Purpose** | React SPA — dashboard, pull requests list, repositories, architecture view, reports, settings, GitHub connect flow. |
| **Ports** | `5173` |
| **Startup** | `docker compose -f docker/docker-compose.yml up frontend` (depends on `backend`) |
| **Health check** | `GET http://localhost:5173` returns the HTML shell |
| **Hot reload** | Vite HMR — source bind-mounted; edits on the host take effect in the browser without a container restart |

---

### One-command startup (recommended)

```bash
git clone <repo>
cd changeguard
./scripts/docker-dev.sh
```

This starts all four services in the correct dependency order and streams their combined logs to the terminal.

---

## 4. Libraries and Frameworks

### Backend

| Library / Framework | Version | Where Used |
|---------------------|---------|-----------|
| **FastAPI** | ≥0.115 | HTTP framework. All API routes under `app/api/v1/`. Provides automatic OpenAPI schema, dependency injection, and request validation. |
| **Uvicorn** | ≥0.32 | ASGI server. Dev mode: `--reload`. Production: single process, no reload. |
| **Pydantic v2** | ≥2.9 | Request/response schemas (`app/schemas/`), settings (`app/core/config.py`), AI output contract (`app/ai/schemas/`). All model validation is Pydantic-native. |
| **pydantic-settings** | ≥2.5 | `Settings` class reads from environment variables and `.env` file. The only place in the codebase that touches `os.environ`. |
| **SQLAlchemy (async)** | ≥2.0 | ORM for all relational models (`app/models/`). Uses the `asyncio` extension with `asyncpg`. |
| **asyncpg** | ≥0.30 | Native async PostgreSQL driver. Used by SQLAlchemy's async engine. |
| **Alembic** | ≥1.13 | Database migration tool. Migrations live in `backend/alembic/versions/`. Applied automatically at container startup. |
| **PyJWT** | ≥2.9 | JWT encode/decode for login sessions (`app/core/security.py`) and the stateless OAuth `state` token. |
| **bcrypt** | ≥4.2 | Password hashing for local (email/password) auth. |
| **httpx** | ≥0.27 | Async HTTP client used by the GitHub integration (`app/integrations/github.py`) and the OpenAI provider (`app/ai/providers/openai_provider.py`). |
| **cryptography** | ≥43 | Fernet symmetric encryption for GitHub access tokens stored at rest (`app/core/crypto.py`). |
| **neo4j (Python driver)** | ≥5.24 | Official async Neo4j driver. Used by `app/graph/neo4j_repository.py` (write) and `app/analysis/graph/neo4j_impact_reader.py` (read). |
| **tree-sitter** | ≥0.23 | Language-agnostic incremental parser framework. Core parsing engine used by the indexer. |
| **tree-sitter-java** | ≥0.23 | Java grammar wheel for tree-sitter. Used by `app/indexer/parsers/java/spring_boot_parser.py` to parse `.java` files and extract Spring Boot annotations, Kafka usage, and Feign clients. |

**Dev-only:**

| Library | Version | Purpose |
|---------|---------|---------|
| **pytest** | ≥8.3 | Test runner. `tests/unit/` and `tests/integration/`. |
| **pytest-asyncio** | ≥0.24 | Async test support. All async fixtures and tests use `asyncio_mode = "auto"`. |
| **ruff** | ≥0.7 | Linter (replaces flake8/isort/pyupgrade). |
| **black** | ≥24.10 | Formatter, 100-char line length. |
| **mypy** | ≥1.13 | Static type checker in `--strict` mode with `pydantic.mypy` plugin. |

---

### Frontend

| Library / Framework | Version | Where Used |
|---------------------|---------|-----------|
| **React 19** | ^19.2.7 | UI component library. All pages and components. |
| **react-dom** | ^19.2.7 | DOM renderer. |
| **react-router-dom v7** | ^7.18.1 | Client-side routing. Routes defined in `src/app/router.tsx`. `RequireAuth` wrapper guards protected routes. |
| **TypeScript 6** | ~6.0.2 | Static typing for all `.tsx`/`.ts` files. `tsconfig.app.json` targets `ES2022` with `strict: true`. |
| **Vite 8** | ^8.1.1 | Dev server + production bundler. Configured in `vite.config.ts`. |
| **Tailwind CSS v4** | ^4.3.3 | Utility-first CSS. Integrated via the `@tailwindcss/vite` plugin (no `postcss` config required). |
| **lucide-react** | ^1.25.0 | Icon library. Used throughout for `Network`, `FolderGit2`, `Download`, and navigation icons. |
| **Vitest** | ^4.1.10 | Test runner (Vite-native). `jsdom` environment for component tests. |
| **@testing-library/react** | ^16.3.2 | React component testing utilities. |

**Note:** The frontend currently renders **mock data only**. The `src/lib/api/` directory contains API client stubs (`auth.ts`, `github.ts`, `client.ts`) but the pages (`PullRequestsPage`, `RepositoriesPage`, `ArchitecturePage`, `ReportsPage`) all import from `src/lib/mock/` rather than making live API calls. The interactive graph visualisation on `ArchitecturePage` is a placeholder. See Section 12 (Missing Components) for details.

---

### Parsing

| Library | Version | Where Used |
|---------|---------|-----------|
| **tree-sitter** | ≥0.23 | Core CST-based parser framework. |
| **tree-sitter-java** | ≥0.23 | Java grammar. Parses Spring Boot annotation arguments, class structures, and Kafka usage. No JVM required. |

**Currently supported languages:** Java + Spring Boot (Maven, single-module projects with a root-level `pom.xml` that references `spring-boot`).

**Not yet supported:** Kotlin, Gradle, multi-module Maven, Python, Node.js, Go.

---

### AI

| Service | Model | Where Used |
|---------|-------|-----------|
| **OpenAI API** | `gpt-4o` (default; configurable via `OPENAI_MODEL`) | `app/ai/providers/openai_provider.py`. Takes the deterministic `ImpactAnalysisResult` from Phase 7, builds a structured prompt from `app/ai/prompts/` Markdown templates, calls the Chat Completions API with `response_format={"type":"json_object"}`, and validates the response against `AIAnalysisResult` (Pydantic v2). Produces: executive summary, breaking-change detection, migration advice, reviewer suggestions, regression test recommendations. |

The provider is abstracted behind `ILLMProvider`; the factory (`app/ai/providers/factory.py`) supports future Claude, Gemini, or Ollama providers without changing any business logic.

---

### Database

| Database | Driver | Purpose |
|----------|--------|---------|
| **PostgreSQL 16** | asyncpg | All relational state: users, auth, GitHub connections, repositories, pull requests, indexing jobs, `PullRequestAnalysis` (deterministic results), `PullRequestAIAnalysis` (LLM results). |
| **Neo4j 5 Community** | neo4j Python async driver | Architecture graph: nodes and relationships discovered by the indexer. Queried by the impact analysis engine and the graph API endpoints. |

---

## 5. Licenses

| Software | License | Cost |
|----------|---------|------|
| **Python 3.12** | PSF License (BSD-style) | Free |
| **FastAPI** | MIT | Free |
| **Uvicorn** | BSD-3-Clause | Free |
| **Pydantic v2** | MIT | Free |
| **SQLAlchemy** | MIT | Free |
| **asyncpg** | Apache 2.0 | Free |
| **Alembic** | MIT | Free |
| **PyJWT** | MIT | Free |
| **bcrypt** | Apache 2.0 | Free |
| **httpx** | BSD-3-Clause | Free |
| **cryptography** | Apache 2.0 / BSD | Free |
| **neo4j Python driver** | Apache 2.0 | Free |
| **tree-sitter** | MIT | Free |
| **tree-sitter-java** | MIT | Free |
| **React 19** | MIT | Free |
| **react-router-dom v7** | MIT | Free |
| **TypeScript** | Apache 2.0 | Free |
| **Vite** | MIT | Free |
| **Tailwind CSS** | MIT | Free |
| **lucide-react** | ISC | Free |
| **Vitest** | MIT | Free |
| **Node.js** | MIT | Free |
| **Git** | GPL-2.0 | Free |
| **Docker Engine** | Apache 2.0 | Free |
| **Docker Desktop** | Docker Subscription Service Agreement | **Free for personal/small-team use; paid for larger organisations** (>250 employees or >$10M revenue). For a demo, Docker Engine (Linux) or Docker Desktop (personal) is free. |
| **PostgreSQL 16** | PostgreSQL License (BSD-style) | Free |
| **Neo4j 5 Community** | GPL-3.0 | Free (Community edition used here). Neo4j Enterprise requires a commercial licence. |
| **OpenAI API** | OpenAI Terms of Service | **Pay-per-use**. Requires a funded OpenAI account. For the demo, AI analysis is optional — the deterministic analysis and graph work without an API key. |
| **GitHub** | GitHub Terms of Service | Free for public/personal repos. OAuth App creation is free. |
| **VS Code** | MIT | Free |

**Summary of cost items for the demo:**

- **OpenAI API key** — the only paid component. All other software is free or free for development use.
- **Docker Desktop** — free for personal use; use Docker Engine on Linux to avoid any licensing ambiguity in a corporate setting.

---

## 6. Accounts Required

### GitHub OAuth App

| Attribute | Value |
|-----------|-------|
| **Required for** | "Connect GitHub" flow — linking a GitHub account to ChangeGuard so it can list repositories and receive webhook events. |
| **Demo necessity** | **Required** if you want to demonstrate repository connection and PR ingestion via webhook. **Not needed** if you demo only the indexer (which clones via unauthenticated git) and the mock frontend. |
| **How to create** | GitHub → Settings → Developer settings → OAuth Apps → New OAuth App. Homepage URL: `http://localhost:8000`. Callback URL: `http://localhost:8000/api/v1/github/callback`. |
| **Credentials needed** | `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` → set in `backend/.env` |

### GitHub (repository access)

| Attribute | Value |
|-----------|-------|
| **Required for** | Indexing a repository (the indexer calls `git clone`). |
| **Demo necessity** | **Required** — but only a GitHub account that owns or has read access to the target demo repository. Unauthenticated `git clone` works for public repositories. |

### GitHub Webhook Secret

| Attribute | Value |
|-----------|-------|
| **Required for** | Receiving PR events from GitHub via webhook. |
| **Demo necessity** | **Required** if demonstrating live PR ingestion. **Not needed** if you seed PR data manually (e.g. via the API directly). |
| **How to configure** | GitHub → Repository → Settings → Webhooks → Add webhook. Set the Payload URL to your public-facing `ngrok` tunnel or deploy URL + `/api/v1/webhooks/github`. Copy the secret → `GITHUB_WEBHOOK_SECRET` in `.env`. |

### OpenAI API Key

| Attribute | Value |
|-----------|-------|
| **Required for** | `POST /pull-requests/{id}/ai-analysis` — AI-enriched analysis. |
| **Demo necessity** | **Optional**. All deterministic phases (indexing, impact analysis, graph) work without it. |
| **How to obtain** | https://platform.openai.com/api-keys |
| **Env var** | `OPENAI_API_KEY` in `backend/.env` |

### Docker Hub

| Attribute | Value |
|-----------|-------|
| **Required for** | Pulling `postgres:16-alpine` and `neo4j:5-community` images. |
| **Demo necessity** | Images are pulled automatically by Docker Compose. Anonymous pulls have rate limits (100/6 h per IP). For reliable demo setup, log in with a free Docker Hub account: `docker login`. |

### Neo4j Aura

**Not required.** The demo uses Neo4j Community running in Docker. Neo4j Aura is a managed cloud offering and is not needed for a local demo.

---

## 7. Hardware Requirements

| Resource | Minimum | Recommended | Notes |
|----------|---------|-------------|-------|
| **CPU** | 4 cores | 8 cores | Neo4j and PostgreSQL both benefit from parallel I/O. The indexer's tree-sitter parsing is single-threaded but fast. |
| **RAM** | 8 GB | 16 GB | Neo4j JVM requires ~512 MB by default; PostgreSQL ~256 MB; backend and frontend containers together ~256 MB. 8 GB is tight; 16 GB leaves comfortable headroom. |
| **Disk** | 10 GB free | 20 GB free | Docker images (~2 GB total), Neo4j data volume, PostgreSQL data volume, cloned repositories (temporarily in `/tmp` during indexing, cleaned up after each run). |
| **Network** | Broadband | — | Required to pull Docker images on first run (~1.5 GB total), and to call the GitHub API and OpenAI API during the demo. |
| **GPU** | None | None | All AI calls go to OpenAI's API; no local GPU inference. |

**Apple Silicon (M1/M2/M3):** All images (`python:3.12-slim`, `postgres:16-alpine`, `neo4j:5-community`, `node:22-alpine`) publish multi-arch manifests. Docker Desktop on Apple Silicon pulls `arm64` variants automatically. No Rosetta emulation needed.

---

## 8. IDE and Extensions

### VS Code Extensions (Recommended)

| Extension | Extension ID | Purpose |
|-----------|-------------|---------|
| **Python** | `ms-python.python` | Python language support, IntelliSense, import management |
| **Pylance** | `ms-python.vscode-pylance` | Fast type-checking and autocompletion for the backend |
| **Ruff** | `charliermarsh.ruff` | Inline linting and formatting for the backend (matches `pyproject.toml` config) |
| **ESLint** | `dbaeumer.vscode-eslint` | JavaScript/TypeScript linting for the frontend |
| **Prettier** | `esbenp.prettier-vscode` | Frontend code formatting (matches `package.json` script) |
| **Tailwind CSS IntelliSense** | `bradlc.vscode-tailwindcss` | Autocomplete for Tailwind utility classes |
| **Docker** | `ms-azuretools.vscode-docker` | Docker Compose file support, container management |
| **REST Client** | `humao.rest-client` | Useful for exercising the API directly from VS Code (alternative to Swagger UI) |
| **GitLens** | `eamodio.gitlens` | Enhanced Git history and blame annotations |
| **Neo4j for VS Code** | `neo4j-extensions.neo4j-for-vscode` | Cypher syntax highlighting and query runner against the local Neo4j instance |

### Tree-sitter Tooling

No VS Code extension is required for tree-sitter itself — it is a Python library (`tree-sitter`, `tree-sitter-java`) and runs entirely in the backend process. There is no CLI or IDE integration needed for the demo.

If you want to inspect the CST produced by tree-sitter for debugging purposes, the `tree-sitter` CLI (https://tree-sitter.github.io/tree-sitter/using-parsers/1-getting-started.html) can be installed separately but is not required.

---

## 9. Demo Repositories

The demo requires at least one Java/Spring Boot repository to index. The following repositories should be created (or existing public repos reused):

### Repository 1 — `demo-order-service`

| Attribute | Value |
|-----------|-------|
| **Purpose** | Primary demo target. Simulates a Spring Boot microservice in an e-commerce system. |
| **Technologies** | Java 21, Spring Boot 3.x (Maven), Kafka producer/consumer, Feign client, REST controllers |
| **Required structure** | Root-level `pom.xml` referencing `spring-boot-starter-parent`; at least one `@RestController`, one `@Service`, one `@FeignClient`, one Kafka producer (`KafkaTemplate`), one Kafka consumer (`@KafkaListener`) |
| **Expected graph nodes** | Controller nodes (endpoints), Service nodes, FeignClient node, KafkaTopic nodes, MavenDependency nodes |
| **Expected graph relationships** | `EXPOSES`, `CALLS`, `PRODUCES_TO`, `CONSUMES_FROM`, `DEPENDS_ON` |

### Repository 2 — `demo-payment-service`

| Attribute | Value |
|-----------|-------|
| **Purpose** | Cross-service dependency target. The `order-service` FeignClient declares a call to this service. |
| **Technologies** | Java 21, Spring Boot 3.x (Maven), Kafka consumer |
| **Required structure** | Root-level `pom.xml`; `@RestController` exposing an endpoint the order-service Feign client targets; Kafka consumer on the same topic the order-service produces to |
| **Expected graph nodes** | Controller, Service, KafkaTopic (consumer side) |
| **Expected graph relationships** | `EXPOSES`, `CONSUMES_FROM` — cross-repo Kafka coupling demonstrable via `Neo4jImpactGraphReader.find_cross_repository_topic_peers` |

### Repository 3 — `demo-shared-library` (optional)

| Attribute | Value |
|-----------|-------|
| **Purpose** | Demonstrates the HIGH risk path — a `pom.xml` change in a shared library. |
| **Technologies** | Java 21, Maven library (no Spring Boot required) |
| **Required structure** | A `pom.xml` with a groupId/artifactId that other services declare as a `<dependency>` |
| **Demo value** | Indexing a PR that touches this repo's `pom.xml` triggers the HIGH risk classification |

**Note:** These repositories do not need to compile or run. The ChangeGuard indexer only parses source files with tree-sitter — it never invokes the Java compiler or Maven build. They need valid `.java` syntax and a valid `pom.xml`, nothing more.

---

## 10. Demo Architecture

```
┌─────────────────────────────────────────────────────┐
│                     Developer's Browser              │
│                   http://localhost:5173               │
└─────────────────────┬───────────────────────────────┘
                       │ HTTP (React SPA)
                       ▼
┌─────────────────────────────────────────────────────┐
│              React 19 Frontend (Vite dev)            │
│  Pages: Dashboard, Pull Requests, Repositories,      │
│         Architecture, Reports, Settings              │
│  Auth: JWT stored in React context (RequireAuth)     │
│  Currently: mock data + API client stubs wired up    │
└─────────────────────┬───────────────────────────────┘
                       │ HTTP/JSON  VITE_API_BASE_URL
                       │ (port 8000)
                       ▼
┌─────────────────────────────────────────────────────┐
│          FastAPI Backend (uvicorn --reload)          │
│  /api/v1/auth         — register, login (JWT)        │
│  /api/v1/github       — OAuth connect flow           │
│  /api/v1/repositories — track/list repositories     │
│  /api/v1/repositories/{id}/index  — trigger indexer │
│  /api/v1/repositories/{id}/graph  — read graph       │
│  /api/v1/pull-requests/{id}/analyze  — Phase 7      │
│  /api/v1/pull-requests/{id}/ai-analysis — Phase 8   │
│  /api/v1/webhooks/github  — inbound PR events        │
│  /api/v1/health           — liveness check           │
└────┬───────────────┬──────────────────┬─────────────┘
     │               │                  │
     │ asyncpg       │ neo4j Bolt       │ httpx
     ▼               ▼                  ▼
┌──────────┐  ┌──────────────┐  ┌──────────────────────┐
│PostgreSQL│  │   Neo4j 5    │  │   External APIs       │
│  port    │  │  Community   │  │                       │
│  5432    │  │  port 7687   │  │ GitHub API            │
│          │  │  port 7474   │  │  (OAuth, repo list,   │
│ Tables:  │  │  (Browser)   │  │   PR changed files,   │
│ users    │  │              │  │   webhook events)     │
│ repos    │  │ Graph nodes: │  │                       │
│ prs      │  │ Controller   │  │ OpenAI API            │
│ jobs     │  │ Service      │  │  (gpt-4o via Chat     │
│ analyses │  │ FeignClient  │  │   Completions,        │
└──────────┘  │ KafkaTopic   │  │   json_object mode)   │
              │ Component    │  └──────────────────────┘
              │ MavenDep     │
              │              │
              │ Relationships│
              │ EXPOSES      │
              │ CALLS        │
              │ PRODUCES_TO  │
              │ CONSUMES_FROM│
              │ DEPENDS_ON   │
              └──────┬───────┘
                     │ populated by
                     ▼
┌─────────────────────────────────────────────────────┐
│               Architecture Indexer                   │
│  (runs inside Backend via FastAPI BackgroundTasks)   │
│                                                      │
│  1. git clone (shallow) → /tmp/changeguard-indexer   │
│  2. Language detect: root pom.xml + spring-boot?     │
│  3. tree-sitter parse every .java file               │
│     Extractors:                                      │
│       controllers  → @RestController / @GetMapping   │
│       services     → @Service                        │
│       feign_clients → @FeignClient                   │
│       kafka        → KafkaTemplate / @KafkaListener  │
│     pom_parser     → <dependency> entries            │
│  4. Build GraphPayload                               │
│  5. Neo4j MERGE (DETACH DELETE + rewrite)            │
│  6. Update IndexingJob status → completed/failed     │
└──────────────────────────────────────────────────────┘
              ▲
              │ git clone (shell out)
              ▼
┌─────────────────────────────────────────────────────┐
│           Local / Remote Git Repositories            │
│   demo-order-service     (Java / Spring Boot)        │
│   demo-payment-service   (Java / Spring Boot)        │
│   demo-shared-library    (Java / Maven)  [optional]  │
└─────────────────────────────────────────────────────┘
```

---

## 11. Demo Flow

The following is the exact sequence of steps to run the demo from a cold start.

### Pre-demo setup (do once)

1. **Clone the repository**
   ```bash
   git clone <changeguard-repo-url>
   cd changeguard
   ```

2. **Copy environment template** (if `.env` files are not already present)
   ```bash
   cp backend/.env.example backend/.env
   # Edit backend/.env to add:
   #   GITHUB_CLIENT_ID=<your-oauth-app-client-id>
   #   GITHUB_CLIENT_SECRET=<your-oauth-app-client-secret>
   #   GITHUB_WEBHOOK_SECRET=<your-webhook-secret>
   #   OPENAI_API_KEY=<your-openai-key>   # optional — skip if not demoing AI
   ```

3. **Create the demo repositories on GitHub** — `demo-order-service` and `demo-payment-service` with Spring Boot source files (see Section 9).

4. **Configure the GitHub webhook** on `demo-order-service` pointing to your backend URL + `/api/v1/webhooks/github` with the same secret as `GITHUB_WEBHOOK_SECRET`.

---

### Demo steps

**Step 1 — Start all services**
```bash
./scripts/docker-dev.sh
```
Wait until the backend prints `Application startup complete` and Neo4j prints `Started.`

**Step 2 — Verify health**
```
GET http://localhost:8000/api/v1/health   → {"status":"ok"}
http://localhost:7474                     → Neo4j Browser login page
http://localhost:5173                     → ChangeGuard login page
```

**Step 3 — Register a user and log in**

Via Swagger UI (`http://localhost:8000/docs`) or the frontend:
```
POST /api/v1/auth/register   {"email":"demo@example.com","password":"Demo1234!"}
POST /api/v1/auth/login      {"email":"demo@example.com","password":"Demo1234!"}
# Save the returned JWT access_token
```

**Step 4 — Connect GitHub**

Navigate to `http://localhost:5173/settings` → "Connect GitHub" → complete the OAuth flow → returns to `/settings?github=connected`.

**Step 5 — Select repositories to track**

Via Swagger UI (`/docs`):
```
GET  /api/v1/github/repositories   → lists your GitHub repos
POST /api/v1/repositories          → body: list of repos to track
```
Confirm `demo-order-service` and `demo-payment-service` appear.

**Step 6 — Index repositories**

```
POST /api/v1/repositories/{id}/index   (for demo-order-service)
POST /api/v1/repositories/{id}/index   (for demo-payment-service)
```
Both return `202`. Poll `GET /api/v1/repositories/{id}` until `indexing_status` is `completed`.

**Step 7 — View the architecture graph**

```
GET /api/v1/repositories/{id}/graph
GET /api/v1/repositories/{id}/services
GET /api/v1/repositories/{id}/dependencies
```
Open `http://localhost:7474` and run:
```cypher
MATCH (n) RETURN n LIMIT 100
```
to visualise the full graph in the Neo4j Browser.

On the frontend, navigate to `http://localhost:5173/architecture` — currently shows a table of nodes from mock data. (Live wiring is a missing component — see Section 12.)

**Step 8 — Simulate a pull request event**

Either:
- Open a real pull request on `demo-order-service` on GitHub (if the webhook is configured) and let the webhook deliver the event automatically.
- Or seed manually via Swagger UI: `POST /api/v1/pull-requests` (if this endpoint exists — see Section 12).

**Step 9 — Run deterministic impact analysis**

```
POST /api/v1/pull-requests/{id}/analyze
GET  /api/v1/pull-requests/{id}/analysis
```
Show the response: `risk_level`, `directly_impacted_nodes`, `downstream_nodes`, `dependency_paths`.

Explain risk classification rules:
- HIGH → pom.xml change, Kafka topic change, or FeignClient change
- MEDIUM → Controller or Service change
- LOW → changed files not mapped to any graph node

**Step 10 — Run AI-enriched analysis** (requires `OPENAI_API_KEY`)

```
POST /api/v1/pull-requests/{id}/ai-analysis
GET  /api/v1/pull-requests/{id}/ai-analysis
```
Show the response: `executive_summary`, `breaking_changes`, `migration_advice`, `reviewer_suggestions`, `regression_test_recommendations`.

**Step 11 — Demonstrate cross-service Kafka impact**

Show that indexing `demo-payment-service` separately and then re-running analysis on a Kafka-producing PR in `demo-order-service` surfaces `demo-payment-service` as a downstream impacted service via topic-name matching.

**Step 12 — Release Coordination Plan narrative**

Using the AI analysis response, narrate how a tech lead would use this data:
- Which services need to be tested before merging?
- What breaking changes must be communicated?
- Who should review the PR (reviewer suggestions)?
- What regression tests should be written?

---

## 12. Missing Components

The following gaps exist in the current implementation and must be addressed before a complete end-to-end demo is possible.

### Critical — blocks the demo

| # | Gap | Location | Impact |
|---|-----|----------|--------|
| 1 | **Frontend pages render mock data only.** `PullRequestsPage`, `RepositoriesPage`, `ArchitecturePage`, and `ReportsPage` all import from `src/lib/mock/` rather than calling the live API. The `src/lib/api/` stubs exist but are not wired to any page. | `frontend/src/pages/` | The frontend shows static sample data regardless of what the backend has indexed or analysed. |
| 2 | **No interactive graph visualisation.** `ArchitecturePage` has a placeholder (`<Network />` icon + "Graph visualization will render here") where the dependency graph should appear. No graph rendering library (React Flow, Cytoscape, D3, vis.js) is installed. | `frontend/src/pages/ArchitecturePage.tsx` | Cannot visually demonstrate the architecture graph in the browser. |
| 3 | **No local repository registration endpoint / UI.** Currently, a repository is only tracked after the full GitHub OAuth → Connect → Select flow. There is no way to register a local bare git repository (one that lives on the developer's machine, not on GitHub) for indexing. The indexer calls `git clone <github_url>`; it has no code path for `git clone file:///path/to/local/repo`. | `app/indexer/scanner/repository_cloner.py`, `app/api/v1/routers/repositories.py` | Demo cannot use a local Git repository as the indexing target — GitHub connection is mandatory for any indexing. |
| 4 | **Pull request seeding requires a live GitHub webhook or manual DB insert.** There is no admin API to manually create a `PullRequest` row for demo purposes. Without a configured webhook receiving a real `pull_request` event, the PR table is empty and neither analysis endpoint can be exercised. | `app/api/v1/routers/pull_requests.py`, `app/api/v1/routers/webhooks.py` | Analysis cannot be demonstrated without a live GitHub webhook or a seeding mechanism. |

### Important — degrades the demo

| # | Gap | Location | Impact |
|---|-----|----------|--------|
| 5 | **`ArchitecturePage` and `RepositoriesPage` do not call `GET /repositories/{id}/graph` or `GET /repositories`.** Even if a user has indexed a repository, the frontend pages show no indication of it. | `frontend/src/pages/` | No visual confirmation that indexing succeeded. |
| 6 | **The Settings page does not show GitHub connection status dynamically.** The current `SettingsPage` is either static or shows mock data; it should call `GET /github/connection` to show the real connection state. | `frontend/src/pages/SettingsPage.tsx` | The GitHub connect button is visible but its state is not tied to the backend. |
| 7 | **No `POST /api/v1/repositories` response to frontend `RepositoriesPage`.** After connecting GitHub and selecting repos, the frontend does not update `RepositoriesPage` to show the newly tracked repositories from the live API. | `frontend/src/pages/RepositoriesPage.tsx` | Repository list always shows mock data. |
| 8 | **`IVersionControlProvider.get_diff` is not implemented.** The `list_changed_files` method is real; `get_diff` (full diff content) remains unimplemented in `integrations/github.py`. The AI context builder currently works without it, but full diff-based context enrichment is a documented gap. | `app/integrations/github.py` | AI analysis context is based on changed file paths and graph data, not diff content. Acceptable for Phase 8 scope but limits AI analysis quality. |
| 9 | **Login-via-GitHub returns 501.** `GET /auth/github/login` and `GET /auth/github/callback` are stubs. Only email/password login works. | `app/api/v1/routers/auth.py` | Minor for the demo — email/password login is fully functional. |
| 10 | **No sample data fixtures or seed script.** There is no `scripts/seed.py` or similar that populates the DB and Neo4j with representative data for a quick demo without going through all setup steps. | `scripts/` | Every demo requires a complete setup run from scratch. |

### Not blocking — documented limitations

| # | Limitation | Notes |
|---|-----------|-------|
| 11 | Only Java + Spring Boot (Maven, single-module) repositories are indexed. | Documented in ADR 0007. Any non-Java repo returns `422 unsupported_repository`. |
| 12 | Cross-repository REST/Feign correlation is not built. | FeignClient-to-Controller mapping across repo boundaries requires a "service identity" concept not yet implemented. Documented in ADR 0008. |
| 13 | PR changed files are paginated at 100 per PR. | GitHub API default — a PR touching >100 files has only its first 100 analysed. |
| 14 | Indexing uses `BackgroundTasks`, not a real task queue. | A process restart mid-index leaves the job stuck in `running`. Acceptable for demo; not production-ready. |

---

## 13. Risks

### R1 — OpenAI API key not available or has no credits

**Probability:** Medium  
**Impact:** AI analysis (Step 10) cannot be demonstrated.  
**Mitigation:** Verify the key and account balance at `https://platform.openai.com/usage` before the demo. Alternatively, skip Step 10 and focus the demo on deterministic analysis and the graph, which require no external API.

---

### R2 — GitHub OAuth App not configured

**Probability:** Medium  
**Impact:** Cannot connect GitHub; cannot track repositories; cannot receive PR webhooks; indexing cannot be triggered via the normal flow.  
**Mitigation:** Create a personal GitHub OAuth App in advance (takes 2 minutes — see Section 6). If the OAuth App cannot be set up in time, use the API directly (Swagger UI + manually insert a repository row) to bypass the OAuth flow and still demonstrate indexing and analysis.

---

### R3 — GitHub webhook not reachable from the internet

**Probability:** High (local dev machine without a public IP)  
**Impact:** Live PR events from GitHub cannot be delivered to `localhost:8000`.  
**Mitigation:** Use `ngrok` (`ngrok http 8000`) to expose the local backend during the demo. Configure the webhook on GitHub to use the ngrok URL. Alternatively, seed PR data manually to bypass the webhook entirely.

---

### R4 — Demo repository is not recognised by the indexer

**Probability:** Medium  
**Impact:** `POST /repositories/{id}/index` returns `422 unsupported_repository`.  
**Mitigation:** The indexer only recognises a root-level `pom.xml` that contains the string `spring-boot`. Verify this is present in the demo repository before the demo. Test indexing in a dry run the day before.

---

### R5 — Docker image pull fails or is slow

**Probability:** Low (stable images), Medium (on poor conference WiFi)  
**Impact:** `docker compose up --build` stalls or fails on image pull.  
**Mitigation:** Run `docker compose pull` and `docker compose build` the day before the demo on a reliable connection. Both images (`postgres:16-alpine`, `neo4j:5-community`) will then be cached locally and the demo will start offline.

---

### R6 — Neo4j container takes too long to become healthy

**Probability:** Low  
**Impact:** The backend container restarts repeatedly waiting for Neo4j (20 health-check retries × 5 s = up to 100 s).  
**Mitigation:** Start services 2–3 minutes before beginning the demo walkthrough. The `depends_on` health-check logic in `docker-compose.yml` handles this automatically — no manual intervention needed.

---

### R7 — Frontend shows mock data — audience may not notice real API integration

**Probability:** High (the frontend is disconnected from the API)  
**Impact:** The demo visually works but does not prove end-to-end integration.  
**Mitigation:** In the demo narrative, explicitly demonstrate the backend via Swagger UI (`/docs`) for the analysis steps, and use the frontend only for the UI shell and navigation. Frame the disconnected frontend as the next implementation step (Section 12, items 1–3).

---

### R8 — Port conflicts on the demo machine

**Probability:** Low–Medium  
**Impact:** `docker compose up` fails if 5432, 7474, 7687, 8000, or 5173 are already bound.  
**Mitigation:** Run `lsof -i :5432 -i :7474 -i :7687 -i :8000 -i :5173` before starting. Stop any conflicting process or adjust the port mappings in `docker-compose.yml`.

---

## 14. Final Checklist

Complete every item in this checklist and verify the result before running the demo.

### Tooling

- [ ] Docker Engine 27+ or Docker Desktop 4.30+ installed and running (`docker ps`)
- [ ] Docker Compose plugin v2+ available (`docker compose version`)
- [ ] Git 2.40+ installed (`git --version`)
- [ ] *(Option B only)* Python 3.12 installed and on PATH (`python3 --version`)
- [ ] *(Option B only)* Node.js 22 installed and on PATH (`node --version`)

### Repository

- [ ] ChangeGuard repository cloned locally
- [ ] `backend/.env` file exists (copied from `.env.example` or created manually)
- [ ] Required environment variables set in `backend/.env`:
  - `GITHUB_CLIENT_ID`
  - `GITHUB_CLIENT_SECRET`
  - `GITHUB_WEBHOOK_SECRET`
  - `OPENAI_API_KEY` *(optional — only needed for AI analysis)*
  - `JWT_SECRET_KEY` *(change from insecure default for any non-trivial demo)*
  - `TOKEN_ENCRYPTION_KEY` *(change from insecure default)*

### Demo Repositories

- [ ] `demo-order-service` GitHub repository created with:
  - [ ] Root-level `pom.xml` referencing `spring-boot-starter-parent`
  - [ ] At least one `@RestController` with `@GetMapping` or `@PostMapping`
  - [ ] At least one `@Service`-annotated class
  - [ ] At least one `@FeignClient`-annotated interface
  - [ ] At least one `KafkaTemplate.send("topic-name", ...)` call (literal topic string)
  - [ ] At least one `@KafkaListener(topics = "topic-name")` annotated method
- [ ] `demo-payment-service` GitHub repository created with:
  - [ ] Root-level `pom.xml` referencing `spring-boot-starter-parent`
  - [ ] A `@KafkaListener` consuming the same topic name as `demo-order-service` produces to
- [ ] Both repositories accessible to the GitHub account linked to the demo OAuth App

### GitHub OAuth App

- [ ] Personal GitHub OAuth App created
- [ ] Homepage URL: `http://localhost:8000`
- [ ] Callback URL: `http://localhost:8000/api/v1/github/callback`
- [ ] `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` copied to `backend/.env`

### GitHub Webhook

- [ ] Webhook configured on `demo-order-service` (or via ngrok tunnel)
- [ ] Payload URL: `<your-public-url>/api/v1/webhooks/github`
- [ ] Content type: `application/json`
- [ ] Secret: matches `GITHUB_WEBHOOK_SECRET` in `backend/.env`
- [ ] Events: `Pull requests`

### Networking

- [ ] Ports 5432, 7474, 7687, 8000, 5173 are free on the demo machine (`lsof -i :<port>`)
- [ ] *(If demoing webhooks)* `ngrok http 8000` running and ngrok URL noted

### Services

- [ ] `./scripts/docker-dev.sh` completes without errors
- [ ] `GET http://localhost:8000/api/v1/health` returns `{"status":"ok"}`
- [ ] Neo4j Browser loads at `http://localhost:7474`
- [ ] Frontend loads at `http://localhost:5173`
- [ ] All four Docker containers show as healthy (`docker compose ps`)

### Docker image cache (run the day before)

- [ ] `docker compose -f docker/docker-compose.yml pull` — pre-pull `postgres:16-alpine` and `neo4j:5-community`
- [ ] `docker compose -f docker/docker-compose.yml build` — pre-build backend and frontend images

### End-to-end smoke test (run the day before)

- [ ] Register a test user via `POST /api/v1/auth/register`
- [ ] Log in and receive a JWT via `POST /api/v1/auth/login`
- [ ] Connect GitHub via the OAuth flow
- [ ] Track `demo-order-service` via `POST /api/v1/repositories`
- [ ] Trigger indexing via `POST /api/v1/repositories/{id}/index`
- [ ] Confirm indexing completes (`indexing_status: completed`)
- [ ] Retrieve the graph via `GET /api/v1/repositories/{id}/graph` — confirm nodes are present
- [ ] Trigger a pull request event (open a real PR or seed manually)
- [ ] Run `POST /api/v1/pull-requests/{id}/analyze` — confirm a result is returned
- [ ] *(If AI key configured)* Run `POST /api/v1/pull-requests/{id}/ai-analysis` — confirm a result is returned

### Known demo script adjustments (given missing components)

- [ ] Use Swagger UI (`/docs`) for all API demonstration steps — the frontend is not wired to the live API yet
- [ ] Use Neo4j Browser (`http://localhost:7474`) for graph visualisation — the frontend `ArchitecturePage` shows a placeholder
- [ ] Explicitly state in the narrative that frontend–API wiring is the next implementation step
