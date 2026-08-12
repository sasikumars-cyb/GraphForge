# GraphForge Architecture Diagrams — Index

**Purpose.** This directory documents the architecture of GraphForge **as it is
implemented today**, derived by reading the codebase (not by reading its own
aspirational docs, ADRs, or product-vision documents, though those were
consulted for terminology and cross-checked against code). It is a companion
to (not a replacement for) `docs/graphforge/ARCHITECTURE.md` and the ADRs in
`docs/adr/`, which describe intent and history; these documents describe
what the code actually does, with a file/line trail for every claim.

**Scope discipline.** Nothing here proposes changes, fills gaps, or
"cleans up" the architecture. Where the evidence was ambiguous or a
relationship could not be directly confirmed by reading code, it is marked
**"uncertain / requires verification"** rather than guessed.

**How to read these diagrams.** All diagrams are Mermaid, renderable directly
in GitHub/most Markdown viewers. Each document has three sections:
1. The diagram(s).
2. A short explanation of what it shows.
3. A **Sources** list of the exact files that support it, and a **Confirmed
   vs. Uncertain** callout where relevant.

## Documents

| # | Document | Covers |
|---|----------|--------|
| 1 | [01-system-context.md](01-system-context.md) | GraphForge and every external system it talks to |
| 2 | [02-high-level-architecture.md](02-high-level-architecture.md) | Frontend / Backend / Persistence / Integrations, one diagram |
| 3 | [03-backend-architecture.md](03-backend-architecture.md) | `backend/app/*` package map and internal dependencies |
| 4 | [04-frontend-architecture.md](04-frontend-architecture.md) | React app structure, routing, state, API layer |
| 5 | [05-ai-agent-architecture.md](05-ai-agent-architecture.md) | Agent catalog, orchestrator, AI provider layer, tools |
| 6 | [06-indexing-knowledge-architecture.md](06-indexing-knowledge-architecture.md) | Repo ingestion → parsing → graph write → retrieval |
| 7 | [07-dependency-graph-architecture.md](07-dependency-graph-architecture.md) | Code entity → graph entity mapping, Neo4j schema |
| 8 | [08-end-to-end-data-flow.md](08-end-to-end-data-flow.md) | Request → API → agent → retrieval → graph/DB → AI → response |
| 9 | [09-workflow-architecture.md](09-workflow-architecture.md) | Workflow stage machines and state transitions |
| 10 | [10-integration-architecture.md](10-integration-architecture.md) | GitHub / Jira / Confluence / Google Drive / TestRail / AI providers |
| 11 | [11-deployment-runtime-architecture.md](11-deployment-runtime-architecture.md) | Docker Compose topologies, processes, env config |
| 12 | [12-key-sequence-diagrams.md](12-key-sequence-diagrams.md) | PR webhook → impact analysis; Planning agent run; repository indexing; Ask flow |

## Repository areas inspected

- `backend/app/` — all 24 subpackages (`agents`, `ai`, `analysis`, `api`,
  `context`, `context_pipeline`, `core`, `database`, `decision`, `graph`,
  `indexer`, `integrations`, `investigation_intelligence`, `knowledge`,
  `knowledge_engine`, `learning_engine`, `mappers`, `models`, `orchestrator`,
  `repositories`, `schemas`, `services`, `tools`, `utils`) — ~600 Python files.
- `frontend/src/` — `app`, `pages`, `components`, `lib/api`, `hooks`, `theme`.
- `docker/` — all compose files, `Dockerfile`s, `Caddyfile`, `nginx.conf`.
- `backend/alembic/` — migration history (used only to corroborate the
  Postgres schema, not reproduced in full).
- `graphforge-validation/` — the project's own architecture-validation
  harness (used as corroborating evidence for the indexing/graph pipeline,
  not treated as authoritative on its own).

## Method

For each diagram, the entry points were read first (`backend/app/main.py`,
`frontend/src/app/router.tsx`, `frontend/src/app/App.tsx`,
`api/v1/routers/__init__.py`), then traced outward through actual imports —
`agents/setup.py` and `tools/setup.py` (registration points),
`orchestrator/worker.py` / `run_coordinator.py` (execution), `graph/session.py`
/ `database/session.py` (persistence), `ai/config/resolver.py` /
`ai/providers/factory.py` (provider selection), `indexer/services/indexing_service.py`
/ `indexer/graph/builder.py` (ingestion → graph), and `knowledge/registry.py`
/ `tools/setup.py` (external integrations). Relationships are drawn only
where an import, a function call, a SQL/Cypher query, or a registered handler
was found in the code.
