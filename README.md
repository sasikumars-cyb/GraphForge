# GraphForge

**AI Engineering Intelligence Platform.** GraphForge connects an
organization's repositories, dependencies, and work items into one real,
queryable Knowledge Graph — then lets you investigate, plan, and refine
engineering work conversationally against it, with every claim traceable
back to a graph fact, a tool call, or a real Jira issue.

## Why it exists

Every engineering organization already contains the answer to "what will
break if I ship this," "what does this actually depend on," and "what
work is this requirement really going to take" — it's just scattered
across repositories, Jira, and institutional memory, and reconstructing
it by hand takes longer than the change itself. GraphForge indexes that
knowledge once into a real dependency graph, then answers those questions
from it directly — deterministically where the graph already has the
answer, with an LLM only where genuine judgment is required, and always
labeled which is which.

## Core capabilities

- **Ask GraphForge** — conversational engineering intelligence: ask a
  question in plain English, get an answer grounded in real repositories
  and dependency relationships, not a generic model guess.
- **Impact / dependency analysis** — blast-radius and dependency graphs
  computed deterministically from the indexed Knowledge Graph.
- **Migration Assistant** — describe a technology migration in plain
  English; GraphForge finds every real repository it actually touches and
  risk-grades the plan by how connected each one is.
- **Refinement Planner** — turn a real Jira issue or a pasted requirement
  into an Epic/Stories/Tasks/Spikes breakdown, grounded in the real code
  it references, with genuine uncertainty flagged as a spike rather than
  guessed at.
- **Planning / Development / Testing workflows** — architecture-grounded
  implementation plans, PR review, and test-strategy generation, each
  backed by verifiable evidence rather than an unexplained AI assertion.
- **Provenance model** — every claim in the UI is tagged Fact, Derived,
  AI Insight, or Recommendation, so it's always clear what was computed,
  what was fetched, and what was proposed.

## Architecture overview

Python/FastAPI backend, async SQLAlchemy over Postgres for relational
state, Neo4j as the Knowledge Graph store, and a React/TypeScript/Vite
frontend. An Agent Orchestrator dispatches a registry of specialized
agents (Review, Planning, Development, Testing, …) against that graph; a
separate `ConversationService` drives the conversational surfaces (Ask
GraphForge, Migration Assistant, Refinement Planner) on the same
underlying graph and Engineering Memory. For the full picture, see
[Documentation](#documentation) below — in particular
[`docs/graphforge/ARCHITECTURE.md`](docs/graphforge/ARCHITECTURE.md) and
[`docs/handbook/16_REALITY_CHECK.md`](docs/handbook/16_REALITY_CHECK.md)
(what's real, what's partial, what's a documented gap — read this one
first if you want the unvarnished current state).

## Getting started

Requires only Docker — no local Python or Node install needed:

```bash
./scripts/docker-dev.sh
```

One command starts everything with hot reload: Postgres, Neo4j (bolt on
`7687`, browser UI at `http://localhost:7474`), backend
(`uvicorn --reload`) at `http://localhost:8000` (docs at `/docs`), and
frontend (Vite dev server, HMR) at `http://localhost:5173`. Both
`backend/` and `frontend/` are bind-mounted into their containers, so
edits on the host apply immediately — no rebuild needed.

There's no sign-up page yet — create a test account with
`curl -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application/json" -d '{"email": "you@example.com", "password": "a-password-at-least-8-chars", "full_name": "Your Name"}'`
(or via Swagger at `/docs`), then log in at `http://localhost:5173`. See
[`docs/setup.md`](docs/setup.md#logging-in) for details, including the
native (non-Docker) and production-style build paths.

## Development

| Script | Purpose |
|---|---|
| `scripts/docker-dev.sh` | **One command, full stack, hot reload — start here** |
| `scripts/docker-prod.sh` | Production-style build (Nginx, no reload) |
| `scripts/setup.sh` | First-time environment setup for native (non-Docker) development |
| `scripts/dev.sh` | Run the full stack natively (Postgres in Docker, backend/frontend as local processes) |
| `scripts/lint.sh` | Lint + format-check both services |
| `scripts/test.sh` | Run backend and frontend test suites |

See [`docs/DEVELOPER_ONBOARDING.md`](docs/DEVELOPER_ONBOARDING.md) for
the fast-path contributor guide (project layout, testing discipline,
conventions to follow).

## Project layout

```
graphforge/
  frontend/   React + TypeScript SPA
  backend/    FastAPI service (api / services / models / schemas / database / core / graph / ai /
              integrations / indexer / analysis / agents / orchestrator / knowledge_engine /
              context_pipeline / learning_engine)
  docs/       Architecture notes, ADRs, deployment, and presentation/handbook reference material
  docker/     Compose orchestration, Nginx config, DB init scripts
  scripts/    Local dev convenience scripts
```

## Documentation

Canonical, current documentation lives under `docs/graphforge/`:

| Document | Covers |
|---|---|
| [`PRODUCT_VISION.md`](docs/graphforge/PRODUCT_VISION.md) | Why GraphForge exists, guiding principles |
| [`ARCHITECTURE.md`](docs/graphforge/ARCHITECTURE.md) | System architecture, Agent Orchestrator, Conversational AI Workspace |
| [`API_CONTRACTS.md`](docs/graphforge/API_CONTRACTS.md) | API surface, as-built vs. design-spec status per section |
| [`AGENT_FRAMEWORK.md`](docs/graphforge/AGENT_FRAMEWORK.md) | Agent contract, extensibility, evaluation metrics |
| [`UI_GUIDELINES.md`](docs/graphforge/UI_GUIDELINES.md) | Design conventions (see its own currency notice) |
| [`ACCESSIBILITY_BASELINE.md`](docs/graphforge/ACCESSIBILITY_BASELINE.md) | Automated a11y regression coverage |
| [`ROADMAP.md`](docs/graphforge/ROADMAP.md) | Original phased build-out plan, Technical Debt, Risk Register |

Also useful:

- [`docs/adr/`](docs/adr/) — Architecture Decision Records, in order, including superseded ones marked as such.
- [`docs/deployment/`](docs/deployment/) — AWS production deployment, the single source of truth for shipping GraphForge.
- [`docs/handbook/`](docs/handbook/) and [`docs/presentation/`](docs/presentation/) — deep-dive reference and demo material, including [`docs/handbook/16_REALITY_CHECK.md`](docs/handbook/16_REALITY_CHECK.md) (what's real vs. partial vs. a gap, read first) and [`docs/presentation/GRAPHFORGE_DEMO_PLAYBOOK.md`](docs/presentation/GRAPHFORGE_DEMO_PLAYBOOK.md) (copy/paste live demo scenarios against real connected data).
- [`docs/setup.md`](docs/setup.md), [`docs/DEVELOPER_ONBOARDING.md`](docs/DEVELOPER_ONBOARDING.md), [`docs/theming.md`](docs/theming.md), [`docs/bedrock-setup.md`](docs/bedrock-setup.md) — setup, onboarding, theming, and AI provider configuration.
- [`graphforge-validation/`](graphforge-validation/README.md) — the permanent regression validation framework, run against a 24-repository fixture suite.

## License

Not yet decided — internal hackathon project.
