# FINAL_ARCHITECTURE_REVIEW.md — GraphForge

**Reviewer role**: Architecture Review Board, final gate before implementation.
**Scope**: Does not redesign the product. Every recommendation either (a) fixes a genuine
ambiguity/contradiction between the eight authoritative documents, (b) flags a concrete
implementation blocker found by cross-checking those documents against the actual running
repository, or (c) reduces near-term risk for five engineers starting tomorrow. Nothing here
proposes new scope.

**Method**: Every finding below was verified against the real repository state (grep, git status,
running Docker containers/volumes, actual file contents) as of this review — not asserted from
memory of having written the source documents. Where a finding could not be verified this way, it
is marked as such.

---

## PART 1 — Architecture Review

### Critical

| # | Finding | Where | Recommendation |
|---|---|---|---|
| C1 | **The repository has no clean, committed baseline to branch from.** `git status --short` shows 46 modified/untracked files on `master` — including the entire Change Investigation Agent enhancement set (CODEOWNERS, retry logic), the Publish Review feature, `ReasoningLogPanel`, and every `docs/graphforge/*`/`GRAPHFORGE_TRANSFORMATION_PLAN.md`/`TEAM_IMPLEMENTATION_PLAN.md` file — none of it committed since `57050b4`. | Repo root, verified via `git status --short` | **This is the actual, literal implementation blocker for tomorrow.** `TEAM_IMPLEMENTATION_PLAN.md` §6 assumes trunk-based development from a clean `main` — five engineers cannot branch from a state that doesn't exist as a commit. **Action before any engineer starts**: commit (or series of logically-grouped commits) the current working tree, verify the full test suite green on that commit, tag it as the hackathon baseline. This is a 30-minute task and must happen before Block 0 of the timeline in `TEAM_IMPLEMENTATION_PLAN.md` §5. |

### High

| # | Finding | Where | Recommendation |
|---|---|---|---|
| H1 | **Shared Memory contradiction between documents.** `ARCHITECTURE.md` § Shared Memory specifies `RunContext` as "Redis-backed." `TEAM_IMPLEMENTATION_PLAN.md` §9/§11/§14 substitutes an in-memory implementation for the hackathon and explains why — but `ARCHITECTURE.md` itself was never updated to reflect this as an accepted, temporary deviation. Anyone reading `ARCHITECTURE.md` alone (which is listed as authoritative) will expect Redis and may add a `redis` service to `docker-compose.yml` unprompted. | `docs/graphforge/ARCHITECTURE.md` § Shared Memory vs. `docs/TEAM_IMPLEMENTATION_PLAN.md` §9, §11, §14 | Add a one-paragraph addendum to `ARCHITECTURE.md` § Shared Memory: "For the initial hackathon implementation, `RunContext` is in-memory, single-process — see `TEAM_IMPLEMENTATION_PLAN.md` for rationale. Redis-backing is required before any multi-process/multi-replica deployment." This is a 5-minute doc edit that closes a real cross-document contradiction. |
| H2 | **No `Goal` value exists for the hackathon's own demo scenario.** `AGENT_FRAMEWORK.md` § How the Orchestrator Chooses Agents lists the closed `Goal` enum as `review_pr`, `clarify_requirement`, `plan_story`, `assess_architecture_impact`. The Planning Agent that `TEAM_IMPLEMENTATION_PLAN.md` WS3 commits to building operates on a **free-text goal with no linked Story** — there is no enum value for this anywhere in any document. Developer 1 and the Senior Engineer will discover this gap on day one, mid-implementation, exactly when it's most expensive to resolve. | `docs/graphforge/AGENT_FRAMEWORK.md` § How the Orchestrator Chooses Agents; `docs/TEAM_IMPLEMENTATION_PLAN.md` WS3 | Define the missing value now, in writing, before coding starts: add `plan_freeform` to the `Goal` enum in `AGENT_FRAMEWORK.md`, mapped to the Planning Agent in the Selector's rule table. Five minutes of doc work now avoids a stalled first Orchestrator/Planning-Agent integration attempt later. |
| H3 | **The hackathon's "Planning Agent" silently redefines the one in the design docs.** Every design doc's sequential-handoff example (`ARCHITECTURE.md` § Sequence Diagrams "Jira story → multi-agent chain", `AGENT_FRAMEWORK.md` § How Agents Collaborate, `ROADMAP.md` Phase 2) describes Planning as consuming **Requirement Agent's output** in the same run. `TEAM_IMPLEMENTATION_PLAN.md` builds Planning as a **standalone** agent triggered directly from free text, skipping Requirement entirely — a reasonable hackathon scope cut, but never reconciled against the docs that define what "Planning Agent" means. Anyone reading `AGENT_FRAMEWORK.md` in isolation will assume Planning always has a Requirement Agent output available in its context, which will be false in the hackathon build. | `docs/graphforge/ARCHITECTURE.md`, `AGENT_FRAMEWORK.md`, `ROADMAP.md` Phase 2 vs. `docs/TEAM_IMPLEMENTATION_PLAN.md` WS3 | Add one sentence to `TEAM_IMPLEMENTATION_PLAN.md` WS3's Scope: "This hackathon's Planning Agent is a standalone-input variant, not the sequential-handoff Planning Agent described in `AGENT_FRAMEWORK.md`/`ROADMAP.md` Phase 2 — that version, consuming a real Requirement Agent's output, remains Phase 2/3 backlog work." This is the difference between an intentional scope cut and a document that quietly contradicts itself. |
| H4 | **Docker Compose project identity is not on the Protected Files list**, but touching it is the single highest-blast-radius mistake available this hackathon (see Part 3 for the full analysis: it would orphan the currently-running dev Postgres/Neo4j volumes containing real seeded GitHub-connected demo data). `TEAM_IMPLEMENTATION_PLAN.md` §4's Protected Files list covers `app/analysis/*`, `app/graph/*`, `app/indexer/*`, `app/integrations/*`, and existing frontend pages — it does not mention `docker/docker-compose*.yml`'s `name:` field or the Postgres/Neo4j credential fields. | `docs/TEAM_IMPLEMENTATION_PLAN.md` §4 Protected Files; verified against running containers (`changeguard-dev-db-1`, `changeguard-dev-neo4j-1`) and volumes (`changeguard-dev_postgres-data`, `changeguard-dev_neo4j-data`) | Add `docker/docker-compose*.yml`'s `name:` field and `POSTGRES_*`/`NEO4J_*` credential values to the explicit Protected Files list in `TEAM_IMPLEMENTATION_PLAN.md` §4, with the one-sentence reason ("renaming these orphans the live dev volumes — see `FINAL_ARCHITECTURE_REVIEW.md` Part 3"). |

### Medium

| # | Finding | Where | Recommendation |
|---|---|---|---|
| M1 | **Referenced-but-undefined types**: `SyncScope` (parameter to `IKnowledgeSource.sync()`) and the concrete field shape of `GraphFact` (beyond `fact_type`/`source`/`written_at` in the ERD) are used throughout `ARCHITECTURE.md` as if their shape is settled. It isn't — no document gives either a field list. | `docs/graphforge/ARCHITECTURE.md` § Knowledge Graph, § Domain Model | Low urgency for this hackathon specifically (GraphWriter and `IKnowledgeSource` implementations are correctly deferred past hackathon scope per `TEAM_IMPLEMENTATION_PLAN.md`). Before Phase 2/3 work begins for real, both types need a concrete field list in `ARCHITECTURE.md` — flag as a pre-Phase-2 documentation task, not a hackathon blocker. |
| M2 | **Orchestrator ceremony may exceed what a 24-hour build actually needs.** `Registry` + `Selector` + `RunCoordinator` as three separate modules (`ARCHITECTURE.md` § Agent Orchestrator, `TEAM_IMPLEMENTATION_PLAN.md` WS2) is the right *target* shape, but for proving "one orchestrator runs two agents" in a hackathon, the internal implementation of each can be genuinely minimal (a dict-keyed lookup for Registry, an if/elif for the Selector) without violating the module boundary. This isn't a design flaw — it's a risk that the team over-invests in scaffolding polish before a single agent successfully runs through it. | `docs/TEAM_IMPLEMENTATION_PLAN.md` WS1/WS2 | Add one sentence to WS2's Scope: "Each module's *first* implementation should be the simplest thing that satisfies its interface — a rule-based `if/elif` Selector is correct for two Goals, not a premature abstraction. Polish only after M3 (two agents registered) is reached." This keeps the module boundary (which is genuinely valuable for Phase 3's later LLM-Selector swap-in) without letting internal ceremony eat the timebox. |
| M3 | **`ISelector` and `IKnowledgeSource` are named as interfaces throughout multiple documents but neither is given a concrete method signature anywhere** beyond prose description. For the hackathon this is harmless (the rule-based Selector doesn't need the interface formalized yet), but it means the "Phase 3: swap in an LLM-based Selector behind the same interface" claim in `ROADMAP.md`/`AGENT_FRAMEWORK.md` is currently unverifiable — there's no interface to swap *behind*. | `docs/graphforge/AGENT_FRAMEWORK.md` § How the Orchestrator Chooses Agents; `ROADMAP.md` Phase 3 | Not a hackathon blocker. Flag for whoever picks up Phase 3: write the actual `ISelector` Protocol/ABC before claiming the swap is drop-in, since an unwritten interface can hide assumptions that only surface when a second implementation is attempted. |
| M4 | **No owner assigned for `backend/app/schemas/` in the repository ownership map**, even though WS2 (new `Run`/`AgentStep` response schemas) and WS3 (Planning Agent's output schema) will both add files there. `TEAM_IMPLEMENTATION_PLAN.md` §4's table has no row for this existing, shared folder. | `docs/TEAM_IMPLEMENTATION_PLAN.md` §4 | Add a row: `backend/app/schemas/` — no single owner (each workstream adds its own new file, e.g. `schemas/orchestrator.py`, `schemas/planning_agent.py`), Captain reviews any edit to an *existing* schema file in this folder. This closes a real gap: right now, two people could plausibly add conflicting files here without either being "wrong" per the ownership table. |
| M5 | **No agreed action-button color for the Agents page's "trigger a run" button.** `UI_GUIDELINES.md`'s existing color table (sky/violet/emerald/rose) was defined entirely in the context of `PullRequestDetailPage`'s PR-scoped action row. The Agents page's new "start a Planning Agent run" action isn't a PR action — it's a new category (triggering *any* agent, not scoped to a PR) that the existing table doesn't unambiguously cover, even though "violet = agentic action" is the closest fit. | `docs/graphforge/UI_GUIDELINES.md` § Color Palette; `docs/TEAM_IMPLEMENTATION_PLAN.md` WS4 | Confirm explicitly (not by default/assumption): the "trigger a run" button on the Agents page uses `violet-600` (agentic action), consistent with "Investigate." This is a 2-minute decision that avoids a mid-hackathon UI bikeshed. |
| M6 | **`API_CONTRACTS.md`'s pagination envelope is defined generically but never shown applied to the specific `GET /agent-runs` example** — the endpoint list just says "paginated, filterable — see Filtering" without a concrete example response matching the general envelope shown earlier in the same document. | `docs/graphforge/API_CONTRACTS.md` § Agent Orchestrator API | Low priority; add one concrete example JSON response for `GET /agent-runs` showing the `items`/`page`/`page_size`/`total`/`has_more` envelope actually populated, so Developer 2 isn't inferring the shape from two separate document sections. |

### Low

| # | Finding | Where | Recommendation |
|---|---|---|---|
| L1 | `AGENT_FRAMEWORK.md`'s Evaluation Metrics table lists "Confidence calibration" with a note that it "requires a feedback-capture mechanism not yet built" — correct and already flagged in `ROADMAP.md`, no new information, but worth confirming it stays explicitly out of hackathon scope (it does, per `TEAM_IMPLEMENTATION_PLAN.md`'s Backlog omission — this is a confirmation, not a new finding). | Cross-document | No action needed; explicitly not in scope, already consistent. |
| L2 | Minor terminology drift: `ARCHITECTURE.md` calls the write choke point `GraphWriter`; `GRAPHFORGE_TRANSFORMATION_PLAN.md` §5/§6 refers to it consistently the same way — no actual drift found on closer check, but the term appears in enough places that a future contributor should grep before assuming a second name exists. | N/A | No action — verified consistent, listed here only because it was checked. |

### Overengineering / Hidden Coupling / Scalability — Summary Assessment

- **Overengineering**: the target architecture (§ARCHITECTURE.md in full) is not overengineered for
  its stated multi-week, multi-phase purpose. The only overengineering risk is time-boxing — see
  M2 above — and it's a sequencing risk, not a design flaw.
- **Hidden coupling**: the one real piece of hidden coupling worth naming is that the Planning
  Agent (WS3) and the Orchestrator's `AgentManifest` contract (WS1) are being built by different
  people against a shape that's still being finalized — `TEAM_IMPLEMENTATION_PLAN.md`'s own Risk
  Register (§14, first row) already names this and mitigates it correctly (publish the draft
  contract early). No new coupling found beyond what's already tracked.
- **Scalability**: real and already acknowledged — the in-memory `RunContext` (H1) does not
  survive a multi-worker deployment; the full-clone-per-index indexer (carried over from
  `GRAPHFORGE_TRANSFORMATION_PLAN.md` Technical Debt) does not scale past a handful of repos.
  Neither blocks the hackathon; both are correctly named as pre-Phase-3 work, not silently ignored.

---

## PART 2 — Repository Rename Review

Every occurrence of "ChangeGuard" (case-insensitive) was located via repository-wide grep,
excluding `node_modules`, `.git`, `__pycache__`, `dist`, and lockfiles. Classified below.

### Must Rename Immediately

User-visible, brand-facing, or cheap-and-in-the-same-sweep. Zero technical risk in any of these.

| File | What's there | Why immediate |
|---|---|---|
| `frontend/index.html` | `<title>`, meta description | Literal browser tab / SEO text — the single most visible branding surface |
| `frontend/src/components/layout/Sidebar.tsx` | Product name label | Rendered on every authenticated page |
| `frontend/src/components/layout/Topbar.tsx` | Fallback page title | Rendered whenever no nav item matches |
| `frontend/src/pages/LoginPage.tsx` (+ `LoginPage.test.tsx`) | "Sign in to ChangeGuard" heading | First thing any user sees |
| `frontend/src/pages/SettingsPage.tsx`, `RepositoriesPage.tsx` | Body copy | User-facing |
| `frontend/src/components/GitHubIntegrationCard.tsx` (+ `.test.tsx`) | Copy | User-facing |
| `backend/app/core/config.py` | `Settings.app_name = "ChangeGuard"` | Backend's own name-of-record; likely surfaces in API metadata/health responses |
| `backend/app/ai/services/github_comment_formatter.py` | `# 🤖 ChangeGuard AI Review` header + `Generated by ChangeGuard AI` footer | **Highest-visibility occurrence in the entire codebase** — this exact text gets posted to real, external GitHub pull requests. Test assertions in `test_github_comment_formatter.py` pin this exact string; update both in the same PR or CI breaks. |
| `README.md`, `frontend/README.md` | Project description | First thing a new contributor reads |
| `docs/project_documentation.md`, `docs/setup.md`, `docs/demo-environment-assessment.md`, `demo/DEMO_GUIDE.md`, `demo/scenarios/02-feign-client-change.md` | Narrative/demo-facing docs | User/demo-facing, cheap to fix, no risk |
| `backend/scripts/seed_demo.py` | Comment/print text | Trivial text change, zero coupling |
| `frontend/src/types/github.ts`, `RiskBadge.tsx`, `frontend/src/app/{AiModelContext,AuthContext}.tsx`, `App.test.tsx`, `backend/app/{__init__.py, integrations/__init__.py, integrations/local_git.py}` | Comments/docstrings only | Zero functional risk; bundle into the same sweep PR since they're trivial diffs, not because they're urgent on their own |
| `backend/.env.example` | Comment/example value | Documentation-only file, not a real credential |

**Why one PR, not several**: every item above is a text change with zero logic risk. Splitting
this across multiple PRs only increases the chance of `main` being in a half-renamed state for
longer than necessary. `TEAM_IMPLEMENTATION_PLAN.md` WS0 already scopes this as a single PR — this
review confirms that scope is correctly sized and lists the definitive file set to include.

### Rename During Phase 2 (Not This Hackathon)

Internal-only, zero user visibility, low urgency — deferring costs nothing and avoids
unnecessary churn during a time-boxed build.

| Item | Why deferred |
|---|---|
| `backend/pyproject.toml`: `name = "changeguard-backend"` | Zero user visibility. Renaming a Python package name can trigger `uv.lock` regeneration overhead — not worth spending hackathon CI minutes on a change nobody will ever see. |
| `backend/app/core/config.py`: `indexer_clone_root` default `/tmp/changeguard-indexer` | An internal temp-directory prefix. Genuinely zero risk to rename, but also zero value until Phase 2 — bundle it with the `pyproject.toml` change then. |

### Never Rename (During This Engagement)

| Item | Why |
|---|---|
| **The currently-running dev Postgres/Neo4j credentials and database names** (`POSTGRES_USER: changeguard`, `POSTGRES_DB: changeguard`, `NEO4J_PASSWORD: changeguard-dev` in `docker/docker-compose.yml`, `docker-compose.prod.yml`, `.github/workflows/ci.yml`, and the matching default in `backend/app/core/config.py`'s `database_url`) | These are **live, in-use values** — the currently running dev stack has real seeded data under these exact credentials (see Part 3 for the full volume analysis). Renaming them requires every engineer to either migrate or destroy-and-recreate their local database. Zero user-facing benefit; real, avoidable operational risk during a demo-critical week. Revisit only as part of a deliberate, scheduled production-hardening pass — never as a "while I'm here" branding sweep. |
| **The Docker Compose project `name:` field** (`changeguard-dev` / `changeguard-prod`) | Full analysis in Part 3 — this is the single highest-blast-radius rename candidate in the entire repository. Do not touch it this hackathon. |
| **Alembic migration file contents** (revision IDs, docstrings, historical `down_revision` chains) | This codebase's own established discipline (confirmed across every migration to date) treats past migrations as immutable history — every schema change to date has been an *additive new* migration, never an edit to an old one. A branding-motivated edit to a historical migration's docstring would be the first violation of that discipline, for zero functional benefit. If a migration docstring mentions "ChangeGuard," leave it — it's an accurate historical record of what was true when it was written. |
| **Public API contract paths/field names** | Verified: **zero occurrences** of the literal string "ChangeGuard" were found in any actual API endpoint path or response field name across the whole backend. This bucket exists as a stated principle (don't ever let branding leak into a contract) rather than because any current cleanup is needed — worth stating explicitly as a passed check, not just an abstract rule. |

---

## PART 3 — Folder Structure Review

**Question**: Should the repository root remain `changeguard/` or become `graphforge/` during the
hackathon?

### What Was Actually Checked (not assumed)

1. **No source code imports the literal folder name.** Backend Python imports are all
   `from app.xxx import ...` (never `from changeguard.xxx import ...`); frontend imports are all
   relative (`../../types/...`). The OS-level directory name is invisible to both language
   toolchains.
2. **All shell scripts resolve paths relative to their own location**
   (`ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"` in every `scripts/*.sh`), never
   a hardcoded absolute or folder-name-dependent path. Confirmed by reading all seven scripts.
3. **`.github/workflows/ci.yml` uses relative `working-directory: backend` / `working-directory:
   frontend`**, not a path incorporating the repo folder name.
4. **The Docker Compose project identity is already decoupled from the folder name** — both
   `docker/docker-compose.yml` and `docker-compose.prod.yml` explicitly pin `name: changeguard-dev`
   / `name: changeguard-prod` at the top level. Compose would otherwise derive the project name
   from the containing folder by default, but this repo already overrides that default. **This
   means renaming the OS folder has zero effect on Docker Compose's project name, container names,
   or volume names** — that's a separate, already-independent risk (see below).
5. **No git remote is configured** (`git remote -v` returns empty) and **no hardcoded
   `github.com/.../changeguard` URL exists anywhere** in the docs. This repository has never been
   pushed to a hosted Git provider under any name yet.
6. **Currently running containers/volumes**, verified live: `changeguard-dev-db-1`,
   `changeguard-dev-neo4j-1`, `changeguard-dev-backend-1`, `changeguard-dev-frontend-1`, and named
   volumes `changeguard-dev_postgres-data` / `changeguard-dev_neo4j-data`, all derived from the
   pinned `name: changeguard-dev`, **not** from the OS folder name.

### Benefits of Renaming the Folder Now

- Cosmetic clarity for anyone opening the project in an editor/terminal.
- Removes a small, recurring "wait, why is this GraphForge code in a folder called changeguard"
  confusion for new contributors.

### Risks of Renaming the Folder Now

- **None found that are specific to the folder rename itself**, given points 1–3 above. This is a
  genuinely low-risk operation *in isolation*.
- **The real risk is conflation**: an engineer renaming the OS folder might reasonably assume they
  should *also* update `docker-compose.yml`'s `name:` field and the Postgres/Neo4j credentials "to
  match" — and **that** action is the dangerous one (see below), not the folder rename itself.

### The Actual High-Risk Action (Distinct From the Folder Rename)

Renaming `docker-compose.yml`'s `name: changeguard-dev` to `name: graphforge-dev` (or renaming the
`POSTGRES_DB`/`POSTGRES_USER` values) causes Docker Compose to treat it as an **entirely new
project**. It will create fresh, empty volumes (`graphforge-dev_postgres-data`,
`graphforge-dev_neo4j-data`) rather than reusing the existing ones. The existing
`changeguard-dev_postgres-data` volume — **verified running right now** — contains:
- The real GitHub-connected demo user (`demo@changeguard.example.com`) and its `GitHubConnection`
  to the real `sasmobileplay-spec` account.
- Four real, indexed repositories and their seeded `PullRequest` rows against real GitHub PR
  numbers.
- 30+ users created by the automated test suite (harmless, but the four real repos and the one
  real GitHub connection are not trivially reproducible without redoing the OAuth App setup and
  re-seeding).
- The full Neo4j dependency graph built from indexing those four repositories.

None of this is lost by renaming the *folder*. All of it is at risk if the Compose project
identity or DB credentials are renamed without an explicit, deliberate volume-migration step.

### Recommendation

**Rename the folder now if desired — it is genuinely free and risk-free, verified above.** Do
**not** rename the Docker Compose project name or database credentials during this hackathon under
any circumstances (this is restated as C1-adjacent guidance and should be added to
`TEAM_IMPLEMENTATION_PLAN.md`'s Protected Files list per finding H4 above). If/when this repository
is first pushed to a hosted Git provider, create the remote repository directly under the name
`graphforge` rather than creating it as `changeguard` and renaming later — this avoids any future
clone-URL/redirect churn entirely, for free, simply by sequencing the one-time action correctly.

**Developer experience impact**: none, either way — no build tool, import, or script in this
repository is sensitive to the OS folder name.

**CI/CD impact**: none — `ci.yml` uses relative working directories throughout.

---

## PART 4 — Merge Conflict Risk: Top 20 Files

Ranked by realistic collision probability given the five workstreams in
`TEAM_IMPLEMENTATION_PLAN.md`, not by raw historical churn alone.

| # | File | Why it's high-risk | Recommended ownership / process |
|---|---|---|---|
| 1 | `backend/app/api/v1/routers/ai_analysis.py` | Highest historical churn file in the repo; WS2's Orchestrator-delegation migration touches it directly | Single, small, isolated PR; Senior Engineer authors, Captain reviews personally (per `TEAM_IMPLEMENTATION_PLAN.md` §4) |
| 2 | `frontend/src/components/layout/nav-items.ts` | Every new page (Agents page) adds one line; tempting for anyone adding UI to touch | Developer 2 only; others request the addition rather than editing directly |
| 3 | `frontend/src/app/router.tsx` | Same reason as #2 | Developer 2 only |
| 4 | `backend/app/main.py` (router mounting) | Every new router (`agent_runs.py`) needs one mount line | Whoever adds the router adds this line in the same PR, announces before merging |
| 5 | `docker/docker-compose.yml` | High-risk if touched at all this hackathon (Part 3) — should see **zero** edits | Protected; any proposed edit requires Captain approval first |
| 6 | `docker/docker-compose.prod.yml` | Same as #5 | Same as #5 |
| 7 | `backend/app/agents/_framework/manifest.py` (new) | WS1 owns it, WS3 codes against it immediately — a contract-stability hotspot even though only one person edits it | Senior Engineer publishes a draft early (per M/High finding above); Developer 1 flags gaps rather than patching it directly |
| 8 | `backend/app/orchestrator/registry.py` (new) | Every new agent (just one this hackathon — Planning) registers here | Senior Engineer owns; Developer 1's registration is a one-line addition reviewed in the same PR as the Planning Agent |
| 9 | `backend/app/schemas/` (existing folder, new files added) | No owner currently assigned (finding M4) — fix before this becomes a real collision | Add explicit ownership row now, per M4's recommendation |
| 10 | `backend/pyproject.toml` | Dependency additions from multiple workstreams could collide on the same lines | Whoever adds a dependency announces it; avoid two people editing this file in the same day without checking first |
| 11 | `frontend/package.json` | Same reasoning as #10 | Same process as #10 |
| 12 | `backend/app/models/__init__.py` (if it re-exports models) | New `Run`/`AgentStep` models need registration here for Alembic autodiscovery, mirroring the existing pattern that already caused an `alembic/env.py` import bug once before (see `GRAPHFORGE_TRANSFORMATION_PLAN.md` §0 test history) | Senior Engineer owns; double-check `alembic/env.py` imports the new models explicitly, since this exact class of bug has happened before in this codebase |
| 13 | `backend/alembic/env.py` | Same reasoning as #12 | Senior Engineer owns the one migration for `Run`/`AgentStep` |
| 14 | `frontend/src/types/analysis.ts` | Already the single most-extended types file this session (per prior feature work) — a new agent-run type addition is likely to land here or in a new `types/agent.ts` | Prefer a **new** `types/agent.ts` file (per `TEAM_IMPLEMENTATION_PLAN.md`'s own folder structure) specifically to avoid piling onto this already-large file |
| 15 | `frontend/src/lib/api/analysis.ts` | Same reasoning as #14 — prefer the new `agentRuns.ts` file, do not extend this one | Developer 2 creates a new file, does not touch this one |
| 16 | `README.md` | Everyone might feel entitled to add a line here during the rebrand sweep | WS0 (Senior QA) owns the rebrand pass exclusively; others don't touch it mid-hackathon |
| 17 | `docs/graphforge/ARCHITECTURE.md` | The H1 addendum (Redis note) and any other doc-fix from this review lands here | Captain merges all doc-reconciliation edits from this review as one PR before Block 1 starts |
| 18 | `docs/TEAM_IMPLEMENTATION_PLAN.md` | Same reasoning as #17 — the H4/M4/M5 fixes all land here | Captain merges alongside #17 |
| 19 | `backend/tests/integration/test_ai_analysis_api.py` | Already the largest, most-extended integration test file (confirmed: extended repeatedly across the last several features) — the Orchestrator delegation migration will need new assertions here | Senior Engineer adds tests in the same PR as the router change (#1), not a separate PR, to keep the two changes atomic |
| 20 | `frontend/src/pages/PullRequestDetailPage.tsx` | Not expected to change this hackathon, but it's the file every new frontend pattern gets copied *from* (button styling, loading states) — a likely source of accidental copy-paste drift if someone "just quickly" tweaks it while referencing it | Frozen per Protected Files; reference it read-only |

**Process recap** (already specified in `TEAM_IMPLEMENTATION_PLAN.md` §6/§11, restated here as the
concrete file-level application): single named reviewer per PR, announce before touching a
Shared-Ownership file, one isolated PR for every file on this list rather than bundling it into a
larger change.

---

## PART 5 — Implementation Readiness Scoring

| Area | Score /10 | Justification |
|---|---|---|
| Architecture | 8 | Deterministic/probabilistic separation and the existing agent loop are genuinely production-grade and already proven; docked for H1 (Redis contradiction) and H3 (Planning Agent redefinition) — both fixable in under an hour of doc work, neither structural. |
| Documentation | 8 | Exceptionally thorough and internally cross-referenced for a hackathon-adjacent effort; docked for M1 (undefined `SyncScope`/`GraphFact` shapes) and the contradictions in Part 1 — real gaps, not fatal ones. |
| API Design | 7 | Strong conventions (versioning, error model, zero-breaking-change discipline) carried forward faithfully from the existing codebase; docked for H2 — the concrete hackathon scenario's own `Goal` value is missing, which is the kind of gap that only surfaces when someone actually tries to build against the contract. |
| Frontend | 8 | Existing component discipline (Card/Table/StatusBadge/RiskBadge, `ReasoningLogPanel`'s accidental agent-agnosticism) is a real asset being correctly reused, not rebuilt; docked slightly for M5 (undecided action color). |
| Backend | 9 | The strongest area of the whole codebase — proven interfaces, proven error handling, proven test discipline (real Postgres/Neo4j, 269 passing tests). Highest confidence score in this review. |
| Knowledge Graph | 7 | Real, populated, genuinely used today; docked for the still-sketch-level `GraphWriter`/`GraphFact` design (correctly deferred, but not yet concrete) and the new node types being additive-on-paper only, not yet schema-validated in code. |
| Agent Framework | 7 | The pattern is proven for *one* agent extremely well; it has never yet run two genuinely different agents through one orchestrator, so "the framework generalizes" is a design claim, not yet an implementation-verified one — that's precisely what this hackathon is for, appropriately scoped. |
| Developer Experience | 8 | Scripts, Docker dev stack, CI, and folder ownership are all genuinely good; docked for C1 (uncommitted baseline — a live DX problem right now) and M2 (ceremony-vs-timebox risk). |
| QA Strategy | 8 | Concrete veto power, a real regression baseline to protect, explicit severity triage — above-average rigor for a hackathon; docked slightly because the Planning Agent's "Prompt Validation" criteria (§12 of `TEAM_IMPLEMENTATION_PLAN.md`) describes an approach but not yet a concrete pass/fail rubric. |
| Hackathon Readiness | 6 | The honest number: strong docs and strong existing code, but a real, unresolved uncommitted-baseline blocker (C1) plus several must-fix-before-kickoff ambiguities (H1–H4) that are each individually cheap but collectively represent real day-one friction if not resolved first. |
| **Overall Readiness** | **7** | Solid, unusually well-documented foundation for a hackathon-scale effort. Not a 9–10 because the findings above are real, not hypothetical — every one was verified against the actual repository. Not a 4–5 because every finding is fixable in minutes to hours, not days, and none requires touching working code. |

---

## PART 6 — Go / No-Go Decision

### Decision: **Conditional GO**

This project is approved for implementation starting tomorrow, **conditioned on the following
mandatory actions being completed before any engineer opens a feature branch** — all of them are
cheap (minutes to at most an hour total), none require redesigning anything, and none touch working
application code.

### Mandatory Actions (must complete before Block 0 of `TEAM_IMPLEMENTATION_PLAN.md` §5)

1. **Commit the current working tree** (finding C1). Run the full existing test suite against
   that commit, confirm green, tag it as the hackathon baseline. Without this, the entire
   trunk-based branching strategy in `TEAM_IMPLEMENTATION_PLAN.md` §6 has no trunk to branch from.
2. **Add the Redis-vs-in-memory addendum to `ARCHITECTURE.md`** (finding H1) — one paragraph.
3. **Add `plan_freeform` to the `Goal` enum in `AGENT_FRAMEWORK.md`**, mapped to the Planning
   Agent (finding H2) — one line in a table plus one Selector rule.
4. **Add one clarifying sentence to `TEAM_IMPLEMENTATION_PLAN.md` WS3** distinguishing the
   hackathon's standalone Planning Agent from the sequential-handoff version in the design docs
   (finding H3) — one sentence.
5. **Add Docker Compose's `name:` field and Postgres/Neo4j credentials to the Protected Files
   list** in `TEAM_IMPLEMENTATION_PLAN.md` §4 (finding H4) — one table row.
6. **Confirm the Agents page's "trigger a run" button uses `violet-600`** (finding M5) — a
   30-second decision, stated explicitly so it isn't re-litigated mid-build.
7. **Add the missing `backend/app/schemas/` ownership row** to `TEAM_IMPLEMENTATION_PLAN.md` §4
   (finding M4) — one table row.

None of these seven actions require a meeting longer than the existing 15-minute morning sync
already scheduled in `TEAM_IMPLEMENTATION_PLAN.md` §7 — they can reasonably all be resolved inside
that same sync, with the Captain making the edits live while the team watches, before anyone
branches.

### What Does Not Need to Change

Everything else: the eight-document architecture, the five-workstream plan, the folder ownership
map, the AI development guidelines, the demo strategy, the risk register, the milestones. This
review found real gaps, not a flawed foundation — the appropriate response is the seven small
fixes above, not a re-plan.
