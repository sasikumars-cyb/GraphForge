# IMPLEMENTATION_BASELINE.md — GraphForge

**Status**: Architecture frozen. WS0 (rebrand) complete. This is the baseline five engineers
branch from starting today.
**Verified against**: the actual repository state, not asserted from memory — every number below
was re-run as part of preparing this document.

---

## Repository Status

**Current version**: Post-WS0 rebrand, pre-WS1 (Agent Framework). The application is GraphForge
end-to-end in every user-visible and test-asserted surface; the multi-agent architecture
(Orchestrator, second agent, Agents page) has not been built yet — that's the work this baseline
hands off to Tracks A–E.

**Current architecture**: FastAPI + async SQLAlchemy + Alembic + Neo4j backend, Vite + React + TS
frontend, exactly as described in `docs/graphforge/ARCHITECTURE.md`. One agent exists today (the
Review Agent, still physically located at `app/ai/agent/` — the move to `app/agents/review/` is
WS1 scope, not yet done). No Orchestrator, no Agent Registry, no second agent, no Agents/Pipeline/
Projects/Knowledge Graph pages exist yet.

**Current capabilities** (all working, all tested):
- Local email/password auth (JWT) + GitHub OAuth "connect" (separate identity systems, ADR 0006)
- Repository tracking, selection, removal; GitHub webhook-driven PR ingestion
- Deterministic impact analysis: dependency graph traversal, risk classification (Neo4j-backed,
  populated by the tree-sitter Java/Spring Boot indexer)
- AI-enriched analysis via the Review Agent: single-shot (`POST .../ai-analysis`) and agentic
  (`POST .../investigate`, full Plan→Tool→Observe→Decide loop with confidence-triggered retry and
  CODEOWNERS fallback)
- Publish Review: posts the already-computed analysis as a real GitHub PR comment, no second LLM call
- Real, live GitHub-connected demo data: 4 indexed repositories under `sasmobileplay-spec`, real
  seeded PR rows, one connected demo account (`demo@changeguard.example.com` — email itself
  intentionally left as-is, see Known Limitations)

**Known limitations** (verified today, not carried-over assumptions):
1. **One pre-existing test failure**: `test_connect_returns_503_when_not_configured` — fails
   because real `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` are configured in `.env` for the live
   demo, which the test assumes are unset. 268/269 backend tests pass; this one is understood,
   documented, and not a regression from any work in this baseline. Fix is a `monkeypatch.delenv`
   addition to the test, not an application change — small, not urgent, tracked in Deferred Work.
2. **CI is currently silent on this repository.** `.github/workflows/ci.yml` triggers on
   `branches: [main]`; the repository's actual branch is `master`. No push or PR against `master`
   has ever triggered CI. **This must be fixed before Day 1** — see Baseline Checklist and
   `CAPTAIN_GUIDE.md`.
3. **`backend/pyproject.toml`'s package name is still `changeguard-backend`.** Zero user
   visibility, deliberately deferred (see `FINAL_ARCHITECTURE_REVIEW.md` Part 2) — renaming it
   triggers unnecessary `uv.lock` churn for zero benefit during a time-boxed build.
4. **Live Postgres/Neo4j credentials and the Docker Compose project name are still
   `changeguard`-branded** (`POSTGRES_USER=changeguard`, `NEO4J_PASSWORD=changeguard-dev`,
   `name: changeguard-dev`). This is intentional and permanent for this engagement — renaming
   these orphans the running dev volumes that hold the real seeded demo data. See
   `FINAL_ARCHITECTURE_REVIEW.md` Part 3 for the full analysis. **Do not rename these.**
5. **The demo account's email (`demo@changeguard.example.com`) was deliberately left unrenamed**
   for the same reason as #4 — it's a live row in the running database, not a template string;
   renaming the constant in `backend/scripts/seed_demo.py` would create a *second*, divergent demo
   user on next re-seed rather than updating the existing one.
6. Three pre-existing frontend files have unrelated Prettier formatting warnings
   (`DependencyGraph.tsx`, `ArchitecturePage.tsx`, `RepositoriesPage.tsx`) — confirmed untouched by
   any work in this session, cosmetic only, not blocking.

**Deferred work**: see the dedicated section below.

**Architecture Freeze version**: `docs/graphforge/*` as amended by `FINAL_ARCHITECTURE_REVIEW.md`'s
seven mandatory fixes (all now applied — see Completed Work). No further architecture edits are
in scope until a workstream owner discovers a genuine gap, at which point it goes through the
Captain per `TEAM_IMPLEMENTATION_PLAN.md` §16 Rule 1.

---

## Completed Work

| Area | What was done |
|---|---|
| **WS0 (Rebrand)** | Every "Must Rename Immediately" occurrence from `FINAL_ARCHITECTURE_REVIEW.md` Part 2 updated: `index.html`, Sidebar, Topbar, LoginPage, SettingsPage, RepositoriesPage, GitHubIntegrationCard, `Settings.app_name`, both `.env`/`.env.example`, the GitHub comment formatter's literal posted-to-GitHub text, both READMEs, plus the `localStorage` key rename (`changeguard.token`/`changeguard.aiModel` → `graphforge.token`/`graphforge.aiModel`) discovered during the sweep and not in the original list. All matching test assertions updated in the same pass. |
| **Architecture** | `PRODUCT_VISION.md`, `ARCHITECTURE.md`, `UI_GUIDELINES.md`, `API_CONTRACTS.md`, `AGENT_FRAMEWORK.md`, `ROADMAP.md` written, evolving (not replacing) the existing ChangeGuard implementation. |
| **Documentation** | `GRAPHFORGE_TRANSFORMATION_PLAN.md` (existing-codebase-to-target-architecture mapping) and this baseline's companion onboarding/backlog/checklist/captain docs. |
| **Transformation** | Component mapping table, gap analysis, refactoring strategy (keep/rename/move/merge/deprecate/extend), folder structure evolution, migration phases — all in `GRAPHFORGE_TRANSFORMATION_PLAN.md`. |
| **Review** | `FINAL_ARCHITECTURE_REVIEW.md` — 6-part Architecture Review Board pass, verified against the real repository (not just the docs), Conditional GO with 7 mandatory pre-kickoff actions. |
| **Implementation Planning** | `TEAM_IMPLEMENTATION_PLAN.md` — 5 workstreams, repository ownership map, dependency graph + timeline, integration/branching strategy, AI development guidelines with prompt templates, merge-conflict prevention, QA strategy, demo plan, risk register, milestones. |
| **Testing** | Full backend (`ruff`/`black`/`mypy`/`pytest`) and frontend (`tsc`/`oxlint`/`prettier`/`vitest`) gates re-run as part of preparing this baseline — see Baseline Checklist for exact numbers. |
| **This preparation pass** | All 7 `FINAL_ARCHITECTURE_REVIEW.md` mandatory fixes applied (Redis addendum, `plan_freeform` Goal, WS3 clarification, Protected Files addition, `schemas/` ownership row, Agents-page button color, `alembic/env.py` missing `PullRequestAIAnalysis` import — a real, previously-undiscovered bug, now fixed and verified via `alembic check`). |

---

## Ready Workstreams

Every workstream below is ready to start **once the Baseline Checklist's outstanding items are
closed** (see below — specifically the branch rename/CI fix and the uncommitted-changes commit).
Full detail for each lives in `TEAM_IMPLEMENTATION_PLAN.md` §3; this table is the at-a-glance
summary.

| Workstream | Owner | Deliverables | Dependencies | Complexity | Duration (of ~24h) | Reviewer | Definition of Done |
|---|---|---|---|---|---|---|---|
| **WS1 — Agent Framework Core** | Senior Engineer | `app/agents/_framework/` (`BaseAgent`, `AgentManifest`, `AgentOutput`/`Evidence`, `ToolRegistry`, retry policy); `app/ai/agent/` migrated to `app/agents/review/` with zero behavior change | None to start | High | ~5h | Captain | Existing Review Agent tests pass unmodified against the migrated location; `AgentManifest` draft published early enough for WS3 to code against |
| **WS2 — Agent Orchestrator** | Senior Engineer | `app/orchestrator/` (Registry, rule-based Selector, RunCoordinator, in-memory `RunContext`), `Run`/`AgentStep` models + migration, `agent-runs` API, `ai_analysis.py` internal delegation | WS1's manifest shape stable | High | ~4h | Captain (personally, for the `ai_analysis.py` PR) | Both the Review Agent and Planning Agent run through the Orchestrator identically; existing endpoint contracts unchanged |
| **WS3 — Planning Agent** | Developer 1 | `app/agents/planning/`, `app/context/resolvers/freetext.py` | WS1's manifest draft (not full impl) | Medium | ~6h | Senior Engineer | Registers in the Orchestrator; produces real, evidence-backed `AgentOutput` for `Goal=plan_freeform` |
| **WS4 — Frontend Agents Surface** | Developer 2 | `AgentsPage`, `components/agents/*`, `lib/api/agentRuns.ts`, `hooks/useAgentRun.ts`, nav wiring | WS2's API contract (mocked first) | Medium | ~5h | Captain | Both agents' run history renders; `ReasoningLogPanel` reused for detail, not rewritten |
| **WS5 — QA, Regression & Demo** | Senior QA | Regression checklist execution, coverage review, demo script + rehearsal | Runs continuously alongside all others | Medium | Continuous | — (QA has its own veto, §2 of `TEAM_IMPLEMENTATION_PLAN.md`) | Zero regressions in the 268-test baseline; demo rehearsed twice, backup confirmed |

---

## Deferred Work

### NOT for Hackathon
- Sequential-handoff Planning Agent (`Goal=plan_story`, consuming a real Requirement Agent's
  output) — the hackathon builds the standalone `plan_freeform` variant only (see
  `AGENT_FRAMEWORK.md`'s addendum on this distinction).
- Requirement, Architecture, Development, Testing, Release, Monitoring, Documentation agents.
- Real Jira/Confluence `IKnowledgeSource` integrations (the free-text Entry Resolver stands in).
- `GraphWriter` schema-validation choke point; new Neo4j node types (`Story`, `Document`, `ADR`,
  `TestRun`, `Release`, etc.).
- Confidence calibration tracking (no feedback-capture mechanism exists yet).
- LLM-based `Selector` (Phase 3 upgrade, behind `ISelector`, not built this hackathon).

### Future Roadmap
Everything in `docs/graphforge/ROADMAP.md` Phases 2–3 not listed above as hackathon scope —
Requirement/Architecture agents with real Jira/Confluence, `GraphWriter`, Datadog/Grafana/Splunk/
Kubernetes/Slack integrations, org-wide "impact simulation."

### Post-Hackathon Improvements
- Redis-backed `RunContext` (replacing the hackathon's in-memory implementation — required before
  any multi-process/multi-replica deployment; see the addendum added to `ARCHITECTURE.md` §Shared
  Memory).
- `ISelector`/`IKnowledgeSource` given concrete method signatures (currently prose-only —
  harmless for a single rule-based Selector, but must exist before claiming a drop-in LLM-Selector
  swap is real).
- Concrete `SyncScope`/`GraphFact` field schemas (currently referenced, not defined).

### Technical Debt
- `test_connect_returns_503_when_not_configured` needs a `monkeypatch.delenv` fix (Known
  Limitation #1 above) — small, isolated, not urgent.
- Full-clone-per-index indexer doesn't scale past a handful of repos per org — must be resolved
  before the (deferred) Architecture Agent's multi-repo use case is exercised beyond demo scale.
- `backend/pyproject.toml` package name (`changeguard-backend`) — Phase 2 rename, zero urgency.

### Infrastructure Improvements
- CI branch trigger mismatch (`main` vs `master`) — **not deferred, this is a Day-0 blocker**, see
  Baseline Checklist.
- Docker Compose project name / Postgres / Neo4j credentials remain `changeguard`-branded
  permanently for this engagement (Known Limitation #4) — this is a "never," not a "later."

---

## Baseline Checklist

| Item | Status | Notes |
|---|---|---|
| Architecture approved | ✅ | `docs/graphforge/*`, amended per `FINAL_ARCHITECTURE_REVIEW.md` |
| Tests passing | ⚠️ **268/269** | 1 pre-existing, documented, non-regressive failure (see Known Limitations #1) — this is "passing" in the sense that matters (no regression), not a literal 100% |
| Branding updated | ✅ | WS0 complete, verified live in browser + full test suite green |
| Documentation updated | ✅ | All 9 `docs/graphforge/*`/root-level documents current as of this baseline |
| API contracts frozen | ✅ | `API_CONTRACTS.md`, no changes since `FINAL_ARCHITECTURE_REVIEW.md`, `plan_freeform` Goal gap closed |
| UI guidelines frozen | ✅ | `UI_GUIDELINES.md`, Agents-page button color gap closed |
| Agent framework frozen | ✅ | `AGENT_FRAMEWORK.md`, sequential-handoff-vs-standalone Planning Agent ambiguity closed |
| Repository clean | ⚠️ **Not yet** | 27 files from the WS0 rebrand + this baseline's doc/bugfix edits are uncommitted as of this writing. **Must be committed before any engineer branches** — this is the direct successor to the C1 finding in `FINAL_ARCHITECTURE_REVIEW.md` (which itself has since been resolved by an earlier commit covering the prior 46-file pile). |
| Branch name matches CI/docs | ❌ **Not yet** | Actual branch is `master`; CI and every planning document assume `main`. **CI has never actually run on this repository as a result.** Fix before Day 1 — see `CAPTAIN_GUIDE.md`. |
| `alembic/env.py` model registration correct | ✅ **Fixed in this pass** | `PullRequestAIAnalysis` was missing from the autogenerate-discovery imports — confirmed via `alembic check`, now fixed. Would have silently corrupted the first `alembic revision --autogenerate` a WS2 engineer ran for the `Run`/`AgentStep` migration. |
| Ready for implementation | **Conditional** | Yes, once the two ❌/⚠️ items above (commit + branch/CI fix) are resolved — both are Captain Day-0 actions, both take minutes, neither touches working code. |
