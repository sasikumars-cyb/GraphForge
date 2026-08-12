# 4. Frontend Architecture

```mermaid
flowchart TB
    main["main.tsx"] --> App["App.tsx"]

    subgraph Providers["Provider tree (App.tsx, outer → inner)"]
        EB["ErrorBoundary<br/>components/layout/ErrorBoundary"]
        Theme["ThemeProvider<br/>theme/ThemeProvider"]
        QC["QueryClientProvider<br/>@tanstack/react-query<br/>(app/queryClient.ts)"]
        Auth["AuthProvider<br/>app/AuthContext.tsx<br/>(JWT in localStorage,<br/>fetchCurrentUser on mount)"]
        AiModel["AiModelProvider<br/>app/AiModelContext.tsx"]
        Router["RouterProvider<br/>app/router.tsx"]
        EB --> Theme --> QC --> Auth --> AiModel --> Router
    end

    App --> Providers

    subgraph Routing["Routing (react-router-dom, app/router.tsx)"]
        RequireAuth["RequireAuth<br/>components/layout/RequireAuth<br/>gate on AuthContext"]
        AppLayout["AppLayout<br/>components/layout/AppLayout"]
        Pages["~40 route pages (pages/*.tsx)"]
        RequireAuth --> AppLayout --> Pages
    end

    Router --> RequireAuth

    subgraph PageGroups["Page groups (by router.tsx section comments)"]
        Build["Build: AI Workspace<br/>WorkspacePage, PlanningPage, DevelopmentPage,<br/>TestingPage, ReviewPage, DocumentationPage,<br/>DocumentationHealthPage, ApiIntelligencePage,<br/>GraphParityPage, RepositoryUnderstandingPage,<br/>ImpactAnalysisPage, DependencyQueryPage,<br/>MigrationAssistantPage, RefinementPlannerPage,<br/>RefinementGraphPage"]
        Workflows["Build: Workflows<br/>NewWorkflowPage, ApprovedQueuePage, WorkflowPage"]
        Monitor["Monitor<br/>RunHistoryPage, RunDetailPage,<br/>MetricsPage, WorkflowLLMUsagePage"]
        KnowledgeG["Knowledge<br/>RepositoriesPage, RepositoryDetailPage,<br/>ArchitecturePage"]
        Admin["Administration<br/>PullRequestsPage, PullRequestDetailPage,<br/>ReportsPage, SettingsPage"]
    end

    Pages --> Build
    Pages --> Workflows
    Pages --> Monitor
    Pages --> KnowledgeG
    Pages --> Admin

    subgraph Components["components/ (feature UI, by directory size)"]
        WorkflowUI["workflow/ (38 files) — largest;<br/>stage views, run cards, blueprint viewers"]
        ReportUI["report/ (14 files)"]
        AgentsUI["agents/ (15 files)"]
        ArchUI["architecture/ (7 files)"]
        SettingsUI["settings/ (8 files)"]
        MCUI["missionControl/ (6 files)"]
        GraphUI["graph/ (5 files) — graph visualizations"]
        LayoutUI["layout/ (8 files)"]
        Other["ask/, blueprint/, charts/, dependency/,<br/>impact/, intelligence/, planning/,<br/>refinement/, runs/"]
    end

    Pages --> Components

    subgraph DataLayer["Data / API layer"]
        Hooks["hooks/*.ts<br/>useAgentRun, useDashboardData,<br/>usePullRequestsData, useReportsData,<br/>useRunHistory"]
        LibApi["lib/api/*.ts — 26 modules,<br/>one per backend router surface"]
        Client["lib/api/client.ts<br/>apiFetch(): fetch() wrapper,<br/>Bearer auth, ApiError,<br/>UNAUTHORIZED_EVENT window event"]
        Hooks --> LibApi --> Client
    end

    Components --> Hooks
    Components --> LibApi
    QC -. "caches query results from" .-> LibApi
    Auth -. "listens for UNAUTHORIZED_EVENT to force logout" .-> Client

    Backend[["Backend REST API<br/>/api/v1/*"]]
    Client -- "fetch(), JSON, Bearer token" --> Backend
```

## Explanation

**State management** is deliberately minimal and split by concern, not a
single global store:
- **Server state / caching**: TanStack Query (`@tanstack/react-query`),
  configured once in `app/queryClient.ts` and provided at the app root.
- **Auth state**: a hand-rolled React Context (`AuthContext.tsx`) holding the
  JWT (persisted to `localStorage` under `graphforge.token`) and the current
  `User`, fetched via `fetchCurrentUser` on mount.
- **AI model selection state**: a second, separate Context
  (`AiModelContext.tsx`).
- **Theme**: `theme/ThemeProvider`.
- No Redux/Zustand/Recoil or other general-purpose global store was found.

**API communication** goes through exactly one primitive: `apiFetch<T>()` in
`lib/api/client.ts` — a thin wrapper over the native `fetch()` API (no axios).
It attaches the JWT as a `Bearer` header, parses the backend's
`{"error": {"code", "message"}}` error shape into a typed `ApiError`, and
dispatches a `window` event (`graphforge:invalid-token`) specifically when
the backend reports `code === "invalid_token"` — `AuthContext` listens for
that event to force a logout, distinguishing "your session is dead" from any
other 401 (e.g. "GitHub isn't connected"). Every one of the 26 files in
`lib/api/` is a thin, typed set of functions built on `apiFetch`, one file
per backend router surface (`workflows.ts` ↔ `api/v1/routers/workflows.py`,
`repositories.ts` ↔ `repositories.py`, etc.) — a 1:1 naming correspondence
confirmed by directly comparing the two directory listings.

**Routing** is a single `react-router-dom` `createBrowserRouter` tree
exported as plain data (`routes`) specifically so tests can reuse the exact
route tree. Auth-gating is one wrapper route (`RequireAuth`) around every
authenticated page; `/login` and `/oauth/callback` sit outside it, and a
catch-all (`*`) renders `NotFoundPage` regardless of auth state.

**Component organization** is feature-based under `components/<feature>/`,
not atomic-design or type-based. `components/workflow/` (38 files) is by far
the largest, matching the workflow system's centrality in the backend
(see [09-workflow-architecture.md](09-workflow-architecture.md)).

## Confirmed vs. Uncertain

- **Confirmed**: provider nesting order, route tree, `apiFetch` behavior,
  and the 1:1 `lib/api/*.ts` ↔ backend-router correspondence (compared file
  lists directly).
- **Uncertain / requires verification**: the *internal* composition of
  individual feature components (e.g. exact prop-drilling vs. context use
  inside `components/workflow/`) was not traced file-by-file given the
  package's size (38 files) — the diagram represents it as one grouped node
  rather than asserting specific internal relationships that weren't
  directly verified.

## Sources

- `frontend/src/main.tsx`, `app/App.tsx`, `app/router.tsx`,
  `app/queryClient.ts`, `app/AuthContext.tsx`, `app/auth-context.ts`,
  `app/AiModelContext.tsx`, `app/ai-model-context.ts`.
- `frontend/src/lib/api/client.ts` (full read).
- `frontend/src/lib/api/*.ts` — directory listing (26 files) cross-checked
  against `backend/app/api/v1/routers/*.py` (30 files; the delta is routers
  with no dedicated frontend module, e.g. `health.py`, `metrics.py` served
  via `hooks/`, `system.py`).
- `frontend/src/components/*/` — directory listing and file counts.
- `frontend/src/pages/*.tsx` — directory listing, cross-referenced against
  `router.tsx`'s own section comments (`// ── Build: AI Workspace ──`, etc.).
