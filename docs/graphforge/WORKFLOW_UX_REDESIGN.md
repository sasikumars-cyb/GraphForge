# GraphForge — Agentic Workflow UX Redesign

**Role**: Principal Product Architect proposal.
**Status**: Design + implementation plan only — no code in this doc's scope. Ready for review before build.
**Constraint honored throughout**: reuse the existing `RunCoordinator`, `AgentRegistry`, `AgentSelector`, Evidence/Confidence model, and `Workflow`/`Run`/`AgentStep` schema. Every backend change proposed below is additive (new nullable columns, new optional request fields, one new small endpoint) — nothing here alters an existing table, breaks an existing response shape, or touches agent internals.

---

## 0. Current State (verified against the actual codebase, not assumed)

- Four agents exist behind a shared `IAgent` protocol, dispatched by `goal` string via `AgentSelector` reading `AgentRegistry` (`app/orchestrator/`).
- A `Workflow` already exists (`app/models/workflow.py`, `app/services/workflow_service.py`, `app/api/v1/routers/workflows.py`): one row groups a **fixed, hardcoded, linear** sequence — `STAGES = ("planning", "development", "testing", "review")` — with no concept of enabling/disabling a stage, reordering, per-stage execution mode, approval gates, or confidence thresholds.
- Today's UX requires **four manual clicks** to complete a workflow (`POST /workflows` + three `POST /workflows/{id}/continue` calls), and the primary nav lists Planning/Development/Testing/Review as coequal, separate destinations alongside Workflows — so the product reads as four tools with a thin wrapper, exactly the problem this redesign is asked to fix.
- Context between stages is a prose summary (`workflow_service.build_stage_context()`), not a structured handoff — reused as-is; not in scope here.
- There is currently **no template concept anywhere** in the codebase (confirmed via search) — Templates are a net-new idea, designed below to require zero new backend concepts (see §5).

---

## 1. UX Redesign Proposal

### 1.1 The core shift

Today: *"Which agent do I want to run?"*
After: *"What do I want to get done?"* → GraphForge decides (or the user configures) which agents run, in what order, with what oversight.

**One objective, entered once.** The user types their engineering objective exactly once, in a single entry point (`/workflows/new`). Every subsequent choice (mode, template, agent set) configures *how* that one objective gets executed — it is never re-typed.

### 1.2 The three creation modes (objective #2)

| Mode | Who it's for | What it configures |
|---|---|---|
| **Full SDLC Workflow** | Default, first-time users, "just run it" | All four stages, automatic execution, no approval gates, default confidence thresholds. Today's existing behavior, made zero-click between stages (§6). |
| **Predefined Workflow Template** | Repeat users with a known shape of work | A named, pre-configured stage plan (e.g. "Quick Fix," "Design Review Only") — see §5. |
| **Custom Workflow** | Advanced users, unusual work | Full control: enable/disable each agent, reorder, per-stage execution mode, approval gates, confidence thresholds, failure behavior — see §4. |

All three modes funnel into the **same underlying mechanism**: a `stage_plan` (§3) attached to one `Workflow` row, executed by the same `RunCoordinator` calls that already exist. Full SDLC and Templates are just *pre-filled* Custom Workflows — there is exactly one execution engine, not three.

---

## 2. Updated Information Architecture

### 2.1 Navigation (replaces the current flat 12-item list)

```
Primary
  ▸ Dashboard
  ▸ Workflows            ← new default landing page after login
  ▸ Run History
  ▸ Pull Requests
  ▸ Repositories
  ▸ Architecture
  ▸ Reports
  ▸ Settings

Individual Agents (Advanced)         ← collapsed section, closed by default
  ▸ Planning
  ▸ Development
  ▸ Testing
  ▸ Review
```

- `Workflows` (not Dashboard) becomes the post-login default route — objective #6.
- The four agent pages (objective #7) remain fully functional, unchanged, reachable — but demoted into a collapsed "Advanced" drawer in the sidebar, sending a clear signal: *the normal path is a Workflow; running one agent alone is an expert escape hatch* (e.g., re-running just Review on an already-analyzed PR without spinning up a whole workflow).
- `Workflows` is no longer a single "new workflow" form — it becomes a real **hub**: a list of past/active workflows (already exists as `GET /workflows`) plus a prominent "New Workflow" action that opens the 3-mode chooser.

### 2.2 Page inventory (new/changed)

| Page | Route | Status |
|---|---|---|
| Workflow Hub | `/workflows` | **New** — list view, replaces landing on Dashboard |
| New Workflow — mode chooser | `/workflows/new` | **Redesigned** — was a single form, becomes 3-mode entry |
| Template Gallery | `/workflows/new/templates` | **New** |
| Custom Workflow Builder | `/workflows/new/custom` | **New** |
| Workflow Execution (pipeline view) | `/workflows/:workflowId` | **Redesigned** — CI/CD-style, see §7 |
| Planning / Development / Testing / Review | `/planning`, `/development`, `/testing`, `/review` | Unchanged, demoted in nav |

---

## 3. Data Model Additions (additive only — see §10 for exact migration)

One new nullable JSON column on the existing `workflows` table:

```python
# app/models/workflow.py — ADD, nothing removed or changed
stage_plan: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
```

Shape of each entry (this is the one new concept the whole redesign hangs off of):

```json
{
  "stage": "planning",
  "goal": "plan_freeform",
  "enabled": true,
  "execution_mode": "automatic",
  "requires_approval": false,
  "confidence_threshold": null,
  "on_failure": "stop"
}
```

- `enabled: false` — the stage is skipped entirely (objective #5, enable/disable).
- Reordering (objective #5) = reordering this array; `workflow_service.next_stage()` reads position in `stage_plan` instead of the global `STAGES` tuple when `stage_plan` is set, falling back to today's fixed 4-stage default when it's `null` (so every existing workflow, and every "Full SDLC" workflow, is unaffected — this is purely additive).
- `execution_mode: "manual" | "automatic"` — drives whether the frontend auto-advances (§6).
- `requires_approval: true` — forces a pause regardless of `execution_mode` (§8).
- `confidence_threshold: 0.0–1.0 | null` — if the completed stage's confidence is below this, force a pause even in automatic mode (§8).
- `on_failure: "stop" | "continue"` — drives whether a failed stage blocks the workflow or is skipped past (§9).

No changes to `Run`, `AgentStep`, `AgentManifest`, `IAgent`, evidence shape, or confidence shape. **Zero backend redesign of the agent framework.**

---

## 4. Custom Workflow Builder UI (agent selection + configuration)

A single-screen builder, one row per agent, in the current registered order by default:

```
┌─────────────────────────────────────────────────────────────────────┐
│  ☑ Planning        [Automatic ▾]  Approval: ☐   Confidence: — %     │
│  ☑ Development     [Automatic ▾]  Approval: ☐   Confidence: 60 %  ⇅ │
│  ☑ Testing         [Manual    ▾]  Approval: ☑   Confidence: —     ⇅ │
│  ☐ Review          [Automatic ▾]  Approval: ☐   Confidence: —     ⇅ │
└─────────────────────────────────────────────────────────────────────┘
   On any stage failure:  ( • ) Stop workflow   ( ) Skip and continue
```

- **Enable/disable**: checkbox per row (objective #5). Disabling Review, for example, produces a Plan → Build → Test workflow with no review gate.
- **Reorder where valid** (objective #5): drag-handle (⇅) on rows *after* the first. The builder does not hard-block unusual orders (nothing in the backend enforces agent interdependencies — confirmed: `RunCoordinator` has no ordering constraints), but the UI applies a **soft warning**, not a hard block: if Testing is moved before Development, or Review before any code-producing stage, show an inline advisory ("Testing usually follows Development — its context will be thinner without a prior build stage") with a "Reorder anyway" affirmance. This keeps the guardrail at the UX layer, matching constraint #10 (no backend enforcement to build).
- **Automatic vs. manual** (objective #5): per-stage dropdown, not a single global toggle — a user can run Planning and Development unattended, then insist on manual approval for Review specifically.
- **Approval checkbox**: independent of execution mode — a stage can be "automatic" but still `requires_approval: true`, meaning it *runs* automatically but the workflow pauses *after* it completes for a human sign-off before the next stage starts.
- **Confidence threshold**: a simple percentage slider/input, optional per stage; left blank (`—`) means "no threshold, never pause for confidence."
- **Global failure policy**: one control at the bottom (`on_failure`), applied to every enabled stage by default — advanced users can override it per-stage later (v2; not required for the MVP builder).

Submitting the builder produces exactly one `stage_plan` array and calls `POST /workflows` once — no new endpoint beyond the additive `stage_plan` field (§10).

---

## 5. Workflow Template System

Templates require **no new backend concept at all** — a template is nothing more than a named, pre-filled `stage_plan`. They live as a small static config on the frontend (`src/lib/workflowTemplates.ts`), not a database table, for the MVP:

```ts
export const WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
  {
    id: "full-delivery",
    name: "Full Delivery",
    description: "Plan, build, test, and review — the complete lifecycle.",
    stagePlan: [ /* all 4 stages, enabled, automatic, no approval */ ],
  },
  {
    id: "quick-fix",
    name: "Quick Fix",
    description: "Skip planning for small, well-understood changes.",
    stagePlan: [ /* development, testing — planning & review disabled */ ],
  },
  {
    id: "design-review-only",
    name: "Design Review Only",
    description: "Get a plan and a peer-style review, without touching code.",
    stagePlan: [ /* planning, review — development & testing disabled */ ],
  },
  {
    id: "guarded-release",
    name: "Guarded Release",
    description: "Full lifecycle with a required human sign-off before Review.",
    stagePlan: [ /* all 4; testing.requires_approval = true */ ],
  },
];
```

The Template Gallery (`/workflows/new/templates`) renders these as cards with a stage-pill preview (e.g. `Development → Testing`), and picking one is equivalent to opening the Custom Builder pre-filled — a user can still tweak a template before submitting, so Templates and Custom are the same screen with different starting state, not two separate implementations.

*Promoting templates to a real backend table (shareable across users/orgs) is a natural Phase 2 — flagged in §11, not required for the redesign to ship.*

---

## 6. Automatic Chaining Design (objective #3, #9)

Today, chaining exists but is **entirely manual** (a human clicks "Continue" between every stage). The redesign makes automatic mode a first-class, zero-backend-change behavior:

1. The Execution page polls `GET /workflows/{id}` every 2s while `status == "in_progress"` (same interval/pattern already used by `useAgentRun`, just applied to the workflow resource).
2. When the poll shows the current stage's `Run.status` flip to `"completed"`:
   - If that stage's `stage_plan` entry has `execution_mode == "automatic"` **and** `requires_approval == false` **and** (no `confidence_threshold` set, or the completed confidence is ≥ threshold) → the frontend immediately calls `POST /workflows/{id}/continue` itself, no user action.
   - Otherwise → the pipeline view shows the next stage as **"Awaiting approval"** (§8) instead of auto-starting.
3. If `execution_mode == "manual"` for the *next* stage, it never auto-starts regardless of the *previous* stage's settings — manual is a property of the stage about to run, approval is a property of the stage that just finished. Both gates must be clear before auto-advance.

This is the entire "automatic chaining" mechanism — a client-side state machine driving the exact same `continueWorkflow()` call a human's click already makes today. No queue, no background worker, no new backend endpoint.

---

## 7. Execution Visualization (objective #8, #9 — the CI/CD-style pipeline view)

Redesigned `/workflows/:workflowId`, modeled directly on GitHub Actions' job graph:

```
 Implement JWT authentication across all services
 ● In progress · Stage 2 of 4 · 43% complete            [ Pause automation ]

 ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
 │ Planning │─────▶│Development│────▶│  Testing  │────▶│  Review  │
 │ ✓ 0:38   │      │ ● running │     │  ⏳ queued │     │ ⏳ queued │
 └──────────┘      └──────────┘      └──────────┘      └──────────┘
  completed          running           queued            queued

 ▾ Development                                    confidence: 0.82 ● high
   ┌─────────────────────────────────────────────────────────────┐
   │ 10:32:01  tool_call        discover_repositories             │
   │           Discovered 3 indexed repositories out of 3 tracked.│
   │ 10:32:02  graph_traversal  discover_components                │
   │           Discovered 12 component(s) and 4 Kafka topic(s)... │
   │ 10:32:04  graph_traversal  traverse_dependencies               │
   │           Traversed 26 edge(s). Found 2 cross-repo coupling(s)│
   │ 10:32:07  llm_reasoning    llm_synthesis                     │
   │           LLM produced implementation blueprint with 4 phases│
   └─────────────────────────────────────────────────────────────┘
   [ View full result JSON ▾ ]
```

Mapped directly to objective #8's checklist, and to data that **already exists** in `GET /workflows/{id}` + `GET /agent-runs/{id}` — nothing here requires new backend fields:

| Requirement | Source (already returned today) |
|---|---|
| Current / completed / running / queued stage | `WorkflowStageResponse.status` per stage, already computed by `_build_stages()` |
| Evidence produced | `AgentStep.evidence` (kind/reference/summary), rendered as log lines |
| Confidence | `AgentStep.confidence_score` + `confidence_reasoning`, already returned |
| Execution logs | **Synthesized from Evidence + timestamps** (§7.1 below) — not real server logs, by design, to avoid a backend change |
| Stage outputs | `AgentStep.result` (the full structured JSON), collapsible, exactly as today's `WorkflowPage` already renders it |
| Total progress | Computed client-side: `completedStages / totalEnabledStages` |

### 7.1 "Execution logs" — an honest scoping note

The agents already emit real `logger.info(...)` lines server-side (`planning_agent_started`, `_step1`, `_step2`, …), but those go to the server process's stdout only — there is no endpoint exposing them today, and building one (log capture + storage + streaming) is a genuine backend addition, not a UI change. The MVP therefore **synthesizes a log-like view from Evidence entries** (already timestamped via `AgentStep.created_at`/`completed_at`/`latency_ms`) — this reads convincingly like a CI log without inventing new backend surface. Real structured log streaming is called out explicitly as a Phase 2/3 item in §11, not required to satisfy objective #8/#9.

### 7.2 Real-time mechanism: polling, not WebSockets

Given constraint #10 ("do not redesign the backend unless absolutely necessary") and the hackathon-scale deployment, the pipeline view polls `GET /workflows/{id}` every 2 seconds while any stage is running or the workflow is auto-advancing — identical in shape to the existing `useAgentRun` hook's polling loop, just pointed at the workflow resource instead of a single run. This delivers a "watching it happen" feel (objective #9) with **zero new backend infrastructure**. A push-based (SSE/WebSocket) upgrade is flagged as optional in §11 — not required for a genuinely CI/CD-like feel at this scale.

---

## 8. Manual Approval Design (objective #5)

Two independent triggers can put a workflow into a new status, `awaiting_approval`:

1. **Explicit**: the *next* stage's `stage_plan.requires_approval == true`.
2. **Confidence-triggered**: the stage that just completed had a `confidence_threshold` set, and its actual confidence fell below it.

While `awaiting_approval`:
- The pipeline view stops polling-driven auto-advance and shows a clear gate: *"Development completed with confidence 0.54 (below your 60% threshold). Review the plan and approve to continue."*
- Two actions: **Approve & Continue** (calls the existing `continueWorkflow()` — no new endpoint) or **Reject** (see error recovery, §9 — functionally a stop).
- The completed stage's full evidence/result is shown inline above the approval prompt, so the approver has everything needed to decide without leaving the page.

No backend change is required for the approval gate *mechanism* itself — `continue` already only fires on an explicit call. What's new is purely: (a) the frontend deciding *whether* to fire it automatically, and (b) the `stage_plan` config that tells it not to.

---

## 9. Error Recovery Design (objective #5, "stop or continue on failure")

Confirmed from the existing codebase: a failed stage **already** leaves `Workflow.current_stage` unchanged (today's `advance_workflow()` only advances on `status == "completed"`), and a fixed bug earlier in this project ensures the failed `Run` is correctly linked and visible in the stage list rather than silently vanishing. This means:

- **"Stop on failure" is already the default, working behavior** — no change needed. The pipeline view just needs to render it clearly: a red node, the error message, and a **"Retry stage"** button that calls the exact same `continueWorkflow()` endpoint again (since `current_stage` never moved past the failed stage, calling continue re-attempts it — confirmed by tracing the existing code, not assumed).
- **"Continue on failure"** is genuinely new and needs one small, additive service change: `advance_workflow()` gains a branch — when the completed run's status is `"failed"` **and** that stage's `stage_plan.on_failure == "continue"`, advance `current_stage` to the next stage anyway (today it only advances on success). This is a few lines in one existing function, not a redesign.
- **Abort**: one new, small, clearly-scoped endpoint, `POST /workflows/{id}/abort`, setting `status = "aborted"`. This is the one genuinely new endpoint in this whole proposal — justified because there is currently no way to represent "the human gave up on this workflow" in the data model at all, and overloading `status = "completed"`/`"failed"` for that would corrupt reporting (Run History, Reports) that reads those fields today.

---

## 10. Backend Changes Required (complete list — everything else is frontend-only)

1. **Migration**: add `workflows.stage_plan JSON NULL` (one column, no backfill needed — `NULL` means "use the existing fixed 4-stage default," so every current workflow keeps working unmodified).
2. **`CreateWorkflowRequest`**: add `stage_plan: list[StagePlanEntry] | None = None` (optional; omitting it reproduces today's exact behavior — Full SDLC mode *is* "omit this field").
3. **`workflow_service`**: `next_stage()`, `STAGE_GOALS` lookups, and `advance_workflow()` read from `workflow.stage_plan` when present, else fall back to the existing global `STAGES`/`STAGE_GOALS` — a few conditional branches in existing functions, not new files.
4. **`advance_workflow()`**: one new branch for `on_failure == "continue"` (§9).
5. **`continue_workflow()`**: read `confidence_threshold`/`requires_approval` from the plan entry that just completed to decide whether to set `status = "awaiting_approval"` instead of leaving it `"in_progress"`.
6. **One new endpoint**: `POST /workflows/{id}/abort` (§9).

Nothing else changes. `RunCoordinator`, `AgentRegistry`, `AgentSelector`, `IAgent`, every agent's `agent.py`/`tools.py`, the Evidence/Confidence model, and every existing endpoint's request/response shape are **untouched** — fully satisfying constraint #10.

---

## 11. Implementation Plan

**Phase 1 — Foundation (backend, small & additive)**
1. Migration: `workflows.stage_plan` column.
2. `CreateWorkflowRequest.stage_plan` (optional field) + `workflow_service` reading it with fallback.
3. `advance_workflow()`'s `on_failure` branch.
4. `continue_workflow()`'s `awaiting_approval` branch (confidence + explicit approval).
5. `POST /workflows/{id}/abort`.
6. Tests: stage_plan-driven custom ordering, on_failure=continue, awaiting_approval triggers (both kinds), abort — all in the existing `tests/integration/test_agent_orchestrator_api.py`/`test_workflow.py` style already established in this codebase.

**Phase 2 — IA and navigation**
1. Nav restructure: promote Workflows, collapse the four agent pages into "Individual Agents (Advanced)".
2. Change the default post-login route to `/workflows`.
3. New Workflow Hub page (`/workflows`) — list view over the existing `GET /workflows`.

**Phase 3 — Creation flow**
1. Mode chooser (`/workflows/new`) — three cards, replacing today's single form.
2. Template Gallery (`/workflows/new/templates`) — static `WORKFLOW_TEMPLATES` config, card grid.
3. Custom Workflow Builder (`/workflows/new/custom`) — agent list, per-stage config, reorder-with-soft-warning, submits one `stage_plan`.

**Phase 4 — Execution experience**
1. Redesigned `/workflows/:workflowId` — pipeline graph header (stage nodes + connecting lines), replacing today's flat stage-button row.
2. Per-stage expandable panel: synthesized log view (Evidence + timestamps), confidence, collapsible full result JSON — largely lifting the existing `EvidencePanel`/`ConfidenceBadge`/result-JSON rendering already built for the current `WorkflowPage`.
3. Polling-driven auto-advance state machine (§6).
4. Approval gate UI (§8): pause banner, Approve/Reject actions.
5. Failure UI (§9): red node, error message, Retry / Skip-and-continue / Abort actions.
6. Total progress bar.

**Phase 5 — Polish & validation**
1. Soft reorder-warning copy for known-unusual stage orders.
2. Empty/loading/error states audited across all new screens (matching the error-surfacing standard already established elsewhere in this app).
3. Full regression pass: existing Full-SDLC workflows (no `stage_plan`) must behave byte-for-byte identically to today — this is the acceptance test for "reused, not redesigned."

**Explicitly deferred (not required to satisfy this redesign's objectives):**
- Real server-side structured log streaming (SSE/WebSocket) — §7.1/§7.2.
- Templates as a shareable backend table rather than a static frontend config — §5.
- Hard backend-enforced stage ordering constraints — §4 (soft UI warning is sufficient).
- Per-stage failure-policy overrides in the builder UI (global-only for MVP) — §4.

---

## 12. Why this satisfies every stated objective

| # | Objective | Where addressed |
|---|---|---|
| 1 | Describe objective once | §1.1, §3 — one `title`, one `stage_plan`, one `Workflow` row |
| 2 | Full SDLC / Template / Custom | §1.2 |
| 3 | Templates chain agents automatically | §5, §6 |
| 4 | Custom agent selection | §4 |
| 5 | Enable/disable, reorder, auto/manual, approval, confidence threshold, stop/continue-on-failure | §4, §6, §8, §9 |
| 6 | Workflow = primary experience | §2.1 (nav + default route) |
| 7 | Agent pages remain, secondary | §2.1 ("Advanced" section) |
| 8 | Stage states, evidence, confidence, logs, outputs, progress | §7 |
| 9 | CI/CD-like real-time feel | §6, §7.2 |
| 10 | Reuse backend, minimal changes | §10 (complete, itemized list — six small changes, nothing else) |
