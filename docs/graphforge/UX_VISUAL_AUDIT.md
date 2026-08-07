# GraphForge — UX & Visual Design Audit

**Scope:** every route in `frontend/src/app/router.tsx`, every shared component in `frontend/src/components/`, the chart primitives, the graph renderer, and the design docs (`UI_GUIDELINES.md`, `ACCESSIBILITY_BASELINE.md`, `WORKFLOW_UX_REDESIGN.md`).

**Method:** first-principles read of the code, not a style pass. Every finding below cites the file it came from. Where I claim something is broken, it is broken in the source, not in the abstract.

**Thesis:** GraphForge's craft is high — the token system is disciplined, the code comments show real design reasoning, the pipeline graph is genuinely good. The gap is not polish. It is that **GraphForge is a graph product that renders its most important outputs as bulleted lists**, and **a trust product that never shows why**. Those two gaps are where nearly all the leverage is.

---

## Executive summary — the five things that matter

| # | Finding | Where | Severity |
|---|---|---|---|
| 1 | **Impact Analysis — the blast-radius feature — has no graph.** It renders `<ul>` of strings and a `<dl>` of counts, in a codebase that already ships `DependencyGraph`. | `pages/ImpactAnalysisPage.tsx:162-295` | Critical |
| 2 | **Evidence is never linked to the claim it supports.** `EvidencePanel` says "every claim is traceable" and then renders an unlinked flat list. There is no `supports`/`contradicts` concept anywhere in the type system. | `components/EvidencePanel.tsx:122`, `types/agent.ts` | Critical |
| 3 | **The dashboard shows no work.** `/` is a platform-status page (providers, connections, version). A user with three workflows awaiting their approval sees zero of them. | `pages/ControlCenterPage.tsx` | Critical |
| 4 | **Confidence is a single number with fabricated inputs.** `PlanningConfidencePanel` hardcodes `met: false` for "Jira connected" and "Confluence connected" regardless of real state. A trust surface that lies is worse than no trust surface. | `components/planning/PlanningConfidencePanel.tsx:56-66` | Critical |
| 5 | **The Architecture page cannot survive 500 repositories.** One HTTP request per repository on mount, 500 `<option>`s in a `<select>`, synchronous dagre on the main thread, and no node search anywhere in the product. | `pages/ArchitecturePage.tsx:108-118`, `237-249` | High |

Two concrete bugs found in passing, both one-line fixes:

- **`ChartLabels` corrupts non-date labels.** `d.label.slice(5)` strips the `YYYY-` prefix from ISO dates — but the same `BarChart` renders repository names in the "Repository Graph" card, so `acme/payments-api` renders as `payments-api`… by accident, and `api` for a 5-char org. `components/charts/SimpleCharts.tsx:198` vs `pages/MetricsPage.tsx:137-144`.
- **"Run Success Rate by Stage" does not show a rate.** `StackedBarChart` scales every bar by the global max *total*, so a stage with 2 runs and 100% failure renders as a shorter bar than a stage with 40 runs and 5% failure. The encoding is volume; the title promises rate. `components/charts/SimpleCharts.tsx:152-187`.

---

# PART 1 — Information Architecture

## 1.1 The dashboard is not a dashboard

**Current experience**
`/` renders "Control Center": four status tiles (Platform / AI Provider / Connections / Knowledge Base), an AI-provider list, a connection list, a knowledge-base count block, and a platform version block. That is the entire page.

**Problem**
Every element is *configuration state*, not *work state*. It answers "is GraphForge plugged in?" — a question a user asks once, on day one. It never answers "what happened while I was away?" or "what is waiting on me?". Meanwhile `ApprovedQueuePage` and the approval-gate phase in `WorkflowPage` mean workflows genuinely can block on a human, and nothing surfaces that anywhere central.

The name compounds it. "Control Center" promises command; the page delivers a health check.

**Recommended**
Split the concern. `/` becomes **Home** — a work surface. Platform status moves to a persistent health pip in the Topbar that expands to today's Control Center content, plus a full page at `/settings/status`.

```
┌────────────────────────────────────────────────────────────────────┐
│  Good morning, Sasi                    ● All systems operational ▾ │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ⚠  WAITING ON YOU (3)                                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ▸ Rate limiting on payment API   Planning ✓ → Development     │  │
│  │   confidence 84% · 12 evidence items      [Review] [Approve]  │  │
│  ├──────────────────────────────────────────────────────────────┤  │
│  │ ▸ Kafka schema migration         ⛔ blocked: context unclear   │  │
│  │   Context Discovery needs 1 answer             [Answer →]     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  IN FLIGHT (2)                                                     │
│  Multi-tenancy refactor  ●━━━━━●━━━━━○─────○   Testing · 1m 40s    │
│  OTel tracing rollout    ●━━━━━◐─────○─────○   Development · 22s   │
│                                                                    │
│  LAST 7 DAYS                                                       │
│  ┌─────────────┬─────────────┬─────────────┬─────────────┐        │
│  │ 14 workflows│ $4.82 spend │ 91% stages  │ 0.81 median │        │
│  │ ▁▂▅▃▇▄▆     │ ▁▁▃▂▅▄▇     │ succeeded   │ confidence  │        │
│  └─────────────┴─────────────┴─────────────┴─────────────┘        │
└────────────────────────────────────────────────────────────────────┘
```

**Why it is better**
It inverts the page from "state of the machine" to "state of my work". The waiting-on-you block is the single highest-value widget in the product: GraphForge's core loop is *AI proposes, human approves*, and today the human has no idea they are the bottleneck. Sparklines on the KPI tiles cost ~20 lines and turn four dead numbers into four trends.

**Implementation notes**
`GET /workflows?status=awaiting_approval,awaiting_clarification` — the phase derivation already exists in `lib/workflowDerived.ts:deriveWorkflowState()`. In-flight rows reuse `PipelineGraph` at a compact size. The KPI strip reuses `useReportsData()`, already fetching everything needed. Health pip reuses `getSystemStatus()` verbatim. **Complexity: M.** **Screens: `/`, Topbar, new `/settings/status`.**

## 1.2 Navigation groups by implementation, not by journey

**Current** (`components/layout/nav-items.ts`)

```
Control Center
Build ▸ AI Workspace · New Workflow · Approved Queue
Monitor ▸ Runs · Metrics
Knowledge ▸ Repositories · Architecture
Administration ▸ Reports · Settings
```

**Problems**

1. **Pull Requests is not in the sidebar at all** — `/pull-requests` and `/pull-requests/:id` are routed and fully built (639 lines) but reachable only by deep link. That is a whole daily-use surface with no front door.
2. **"Reports" is filed under Administration.** It is PR-review evidence packets — engineering output, not admin.
3. **"Metrics" vs "Reports" is unresolvable from the labels.** `MetricsPage.tsx:32-34` needs a code comment to explain the difference to *developers*. Users get no comment.
4. **Twelve capabilities are hidden behind a flat catalog.** `/workspace` is a launcher grid with no state — you cannot see which capabilities you have run, which have fresh results, which are stale. It scales to 30 capabilities (as the comment claims) at the cost of making all 30 equally invisible.
5. **The graph tools are scattered.** Impact Analysis, Dependency Query, Repository Understanding, and Graph Parity are all "ask the architecture graph a question", filed under `/workspace`, while `/architecture` — the graph itself — sits in a different section.

**Recommended**

```
  Home                       ← work, not status

  WORK
    Workflows                ← list + New; Approved Queue becomes a filter
    Pull Requests            ← promoted out of nowhere
    Runs

  UNDERSTAND
    Architecture             ← graph home; Impact / Dependency Query /
                               Repo Understanding / Parity become modes
                               inside it, not sibling pages
    Repositories

  INSIGHTS
    Metrics                  ← rename: "Usage & Cost"
    Review Reports           ← rename: was "Reports"

  Settings
```

**Why it is better**
Four sections map to four questions: *what am I working on*, *what does the system know*, *what is it costing me*, *how is it configured*. The graph tools stop being twelve peers in a drawer and become verbs on one noun — which is also how users think ("show me the impact of *this*", not "open the Impact tool then pick a repo").

**Implementation notes**
Pure `nav-items.ts` + `router.tsx` change with `<Navigate replace>` for old paths — the file already establishes that pattern (`router.tsx:98-103`). Merging the graph tools into Architecture is a larger, separate move (Part 6). **Complexity: S for nav, L for the merge.**

## 1.3 Every page states its title twice

`Topbar.tsx:42` renders an `<h1>` with the page title from `NAV_ITEMS`. Every page then renders its own `<h2>` with the same text (`ControlCenterPage.tsx:100`, `MetricsPage.tsx:42`, `ArchitecturePage.tsx:227`, …). Worse, `Card.tsx:34` also emits `<h2>` — so a card title sits at the same document level as the page title, and the page has no `<h1>` of its own.

**Recommended:** page components emit `<h1>`; `Card` emits `<h3>`; the Topbar shows breadcrumbs instead of a duplicated title. **Complexity: S.** Fixes a real screen-reader outline problem and reclaims ~40px of vertical space on every page.

---

# PART 2 — Missing Visualizations

Ranked by impact. Each is justified by what the user cannot currently see.

## 2.1 Impact Analysis needs a blast-radius graph — this is the single biggest gap

**Current experience** (`ImpactAnalysisPage.tsx:177-295`)

```
┌ Blast Radius Overview ─────────────────────────┐
│ Repositories 4 │ APIs 7 │ High-risk 3 │ Rels 22 │
└────────────────────────────────────────────────┘
┌ Directly Impacted Repositories ────────────────┐
│ • acme/order-service                            │
│ • acme/notification-service                     │
│ • acme/billing-api                              │
└────────────────────────────────────────────────┘
┌ Indirectly Impacted APIs ──────────────────────┐
│ POST /orders/{id}/confirm                       │
│ GET  /billing/invoices                          │  … 7 more
└────────────────────────────────────────────────┘
┌ Confidence Summary ────────────────────────────┐
│ High 12 │ Medium 7 │ Low 3                      │
└────────────────────────────────────────────────┘
```

**Problem**
This is a *graph result* rendered as four disconnected lists. A reader cannot answer the only questions that matter:

- Which repo is impacted *through* which path? (`billing-api` — is that a direct Kafka consumer or three hops downstream?)
- Which of the 22 relationships are the low-confidence ones? The confidence summary is a count with no attachment to any specific edge.
- Where does the blast stop? "Blast radius" is inherently a distance metaphor and there is no distance anywhere on screen.

The word "radius" is in the feature name, the product ships a graph renderer, and the page renders bullets.

**Recommended visualization — concentric blast radius, hops as rings, confidence as edge opacity**

```
┌─ Blast Radius: acme/payment-service ────────────────────────────────┐
│  [Graph] [Paths] [Table]                     ◉ 1 hop ◉ 2 ◯ 3+       │
│                                                                     │
│         ╭────────── 2 hops ──────────╮                              │
│         │   ╭──── 1 hop ────╮        │                              │
│         │   │               │        │                              │
│    ┌────┴───┴──┐  ══════▶ ┌─┴──────┐ │   ┈┈┈▶ ┌──────────┐         │
│    │ ★ payment │           │ order  │ │        │ analytics│         │
│    │  service  │  ══════▶ ┌────────┐ │        └──────────┘         │
│    └───────────┘           │billing │ │          low conf.          │
│         │   │              └────────┘ │                             │
│         │   ╰───────────────╯        │                              │
│         ╰──────────────────────────╯                                │
│                                                                     │
│    ══ high confidence   ── medium   ┈┈ low (dashed)                 │
│    ⚠ 3 high-risk components on the 1-hop ring                       │
├─────────────────────────────────────────────────────────────────────┤
│  Selected: order-service                                    ✕       │
│  Path   payment-service ──PRODUCES_TO──▶ orders.v2                  │
│                         ──CONSUMES_FROM──▶ order-service            │
│  Confidence  high · 4 evidence items                    [Evidence ▾]│
└─────────────────────────────────────────────────────────────────────┘
```

**Why it is better**
Distance becomes spatial (rings = hops), certainty becomes visual weight (opacity/dash = confidence), and the confidence summary stops being an abstract tally and becomes a property you can *see on the specific edge you are worried about*. Selecting a node reveals the concrete path — the answer to "why do you think this is affected", which today is unavailable at any price.

**Implementation notes**
`DependencyGraph` already does click-to-highlight with incoming/outgoing colouring (`DependencyGraph.tsx:411-480`) — extend it with a radial layout instead of dagre LR and a `hopDistance` field. Backend must return edges, not just string lists: `directly_impacted_repositories: string[]` → `impacted: { id, name, hops, confidence, path: Edge[] }[]`. **Complexity: L (backend contract change).** **Screens: `/workspace/impact-analysis`.** *This is the highest-ROI item in the audit.*

## 2.2 Evidence graph — connect claims to the evidence that supports them

**Current** (`EvidencePanel.tsx`) — a collapsed row of kind-chips, expanding to a flat `<ul>` of summaries with an optional `reference`.

**Problem**
The panel's own description reads *"every claim is traceable"* (`EvidencePanel.tsx:122`). Nothing in the UI traces anything. Given `PlanningResultDetails` rendering eight risks and six implementation steps, and an Evidence Trail of nineteen items, there is no mapping between them. And the data model has no notion of *contradicting* evidence at all — only supporting, which means the AI can never visibly show its own doubt.

**Recommended — evidence anchored inline, expandable to a bipartite claim↔evidence view**

```
┌ Risk Considerations ─────────────────────────────────────────────┐
│ ⚠ HIGH   Schema change breaks 2 downstream consumers  ◆◆◆ 3 ▾    │
│          ├ ◆ graph_fact   orders.v2 CONSUMES_FROM order-service  │
│          ├ ◆ graph_fact   orders.v2 CONSUMES_FROM analytics      │
│          └ ◇ llm_reasoning "no versioning strategy in the repo"  │
│                                                    ⊘ 1 contra ▾  │
│          └ ⊘ tool_call    schema-registry: BACKWARD compat set    │
│                                                                   │
│ ⚠ MED    Rollback requires coordinated deploy         ◆ 1 ▾      │
└──────────────────────────────────────────────────────────────────┘
```

**Why it is better**
Trust is per-claim, not per-run. A reviewer who distrusts *one* risk can currently only distrust the whole report. Anchoring evidence to the claim makes doubt surgical. Surfacing contradicting evidence is the strongest trust signal a system like this can send: an AI that shows what argues *against* its own conclusion reads as honest in a way no confidence percentage does.

**Implementation notes**
Add `claim_id?: string` and `stance: "supports" | "contradicts"` to the `Evidence` type; agents already emit evidence in-order per claim, so backfilling `claim_id` is mostly a prompt/schema change. Render inline via a shared `<EvidenceAnchor>`. `EvidencePanel` stays as the full-trail fallback. **Complexity: L.** **Screens: every stage result, PR detail, impact analysis.**

## 2.3 Confidence progression across the pipeline

**Current** — `ConfidenceBadge` shows one number per stage; no view shows how confidence *moved*.

**Problem**
A workflow where confidence went 0.9 → 0.6 → 0.4 is a very different artifact from one that went 0.5 → 0.7 → 0.85, and today they look identical at the end.

**Recommended — sparkline on the workflow header, drill-down to a per-stage waterfall**

```
Confidence   0.62 ──▶ 0.81 ──▶ 0.74 ──▶ 0.88
             Ctx      Plan     Dev      Test
             ▁▁▄▄     ▄▄▇▇     ▄▄▆▆     ▆▆██
                              ↓ −0.07
                      "development found an undocumented dependency"
```

**Why it is better** A drop between stages is the single most actionable signal in the run — it means a later agent found something an earlier one missed. Right now that information exists in the database and is invisible. **Complexity: S** (data already on each `AgentStep`). **Screens: `WorkflowHeader`, `WorkflowPage`.**

## 2.4 Stage execution waterfall

**Current** — `RunHistoryPage.tsx:44-49` computes and prints stage duration as a number (`12.4s`) in a table cell. Nothing charts it.

**Recommended**

```
Context Discovery  ████████ 8.2s          $0.04   ▏ 12k tok
Planning           ─────────██████████████ 22.1s  $0.31   ▏ 48k tok
Development        ──────────────────────████ 6.0s $0.08  ▏ 14k tok
Testing            ──────────────────────────███████ 11.4s $0.12 ▏ 9k
                   └──────────────── 47.7s total · $0.55 ──────────┘
```

**Why it is better** Answers "where does my money and my minute go" in one glance. Today, both cost and latency exist per stage in the metrics API (`cost_by_stage`) but are shown *aggregated across all workflows* — never for the run in front of you. **Complexity: S.** **Screens: `WorkflowPage`, `RunDetailPage`.**

## 2.5 Workflow lineage tree

`NewWorkflowPage` supports `parent_workflow_id` and refinement notes (`WorkflowPage.tsx:558-575`) — so workflows form a version tree. Nothing renders it. A refined workflow shows no link to its parent, and a parent shows no link to its children.

```
Rate limiting on payment API
├─ v1  ✓ approved      conf 0.72   "risk section thin"
└─ v2  ● in progress   conf 0.88   ← you are here
```

**Complexity: S.** **Screens: `WorkflowHeader`, workflow list.**

## 2.6 Others, ranked

| Visualization | Replaces | Why | Cx |
|---|---|---|---|
| **Cost treemap** (workflow → stage → model) | `cost_by_provider` + `cost_by_stage` as two disconnected bar lists | The two current charts cannot answer "which *workflow* burned the budget" — the hierarchy is the question | M |
| **Confidence distribution histogram** | `ConfidenceBadge` per row | Calibration: is the AI mostly 0.8s, or bimodal 0.3/0.95? Bimodal means the confidence signal is real; a single lump means it is decorative | S |
| **PR risk heatmap** (file × risk) | `PullRequestDetailPage` lists | Reviewers triage by "where is the danger", not by file order | M |
| **Repository health matrix** (repo × indexed/graph/staleness/coverage) | `RepositoriesPage` table | 500 repos in a table is unscannable; a matrix makes the red cells pop | M |
| **Sankey: evidence source → conclusion** | nothing | Shows how much of a conclusion rests on the graph vs. on LLM priors — the core "is this grounded?" question | L |
| **Graph diff (before/after index)** | nothing | Re-indexing changes the graph and nobody can see what changed | L |

Deliberately **not** recommended, despite being on the brief: chord diagrams (repo-to-repo relations are sparse and directed — a node-link graph reads better), Gantt (stages are strictly linear; the waterfall in 2.4 covers it without implying parallelism that does not exist), and mini-maps beyond the one already present.

---

# PART 3 — Existing Visualizations

## 3.1 `SimpleCharts` — the weakest surface in the product

`components/charts/SimpleCharts.tsx` (215 lines, no chart library, by design).

| Issue | Detail |
|---|---|
| **No y-axis, no gridlines, no value labels** | You cannot read *any* value without hovering. Line 48 draws a baseline; nothing else. |
| **`preserveAspectRatio="none"` with a `0 0 100 H` viewBox** | X is squashed to 100 user units then stretched. `LineChart` guards the line with `vectorEffect="non-scaling-stroke"` (line 81) but its data points are `<circle r={0.8}>` (line 83) — those render as **ellipses**, wider than tall, at every viewport. `BarChart`'s `rx={0.6}` distorts the same way. |
| **Tooltips are SVG `<title>`** | ~1s browser delay, no keyboard access, no touch access, no styling. On a cost dashboard, the value *is* the content. |
| **`ChartLabels` assumes ISO dates** | `d.label.slice(5)` (line 198). The same `BarChart` renders repository names in the Repository Graph card (`MetricsPage.tsx:137-144`), so repo labels get their first 5 characters silently eaten. **This is a bug.** |
| **`StackedBarChart` shows volume, titled as rate** | Scaled by global max total (line 156), so failure *proportion* is not comparable across stages. **This is a bug** relative to the card title "Run Success Rate by Stage". |
| **Colour-only encoding** | Success/failure segments differ only by fill; counts live in `title` attributes on `<div>`s, which screen readers do not reliably announce. |

**Recommendation.** Keep the no-dependency stance — it is the right call for four chart types — but promote these to real primitives: a proper linear scale with axis ticks, HTML-positioned tooltips (not `<title>`), a `formatLabel` prop instead of `slice(5)`, a `normalize` prop on `StackedBarChart`, and a `<table class="sr-only">` data alternative per chart. Roughly 150 additional lines. **Complexity: M.** This unblocks all of Part 7.

## 3.2 `DependencyGraph` — good, with a hard ceiling

**Works well:** click-to-highlight with incoming/outgoing colour separation (lines 411-480) is a genuinely strong interaction; repository clustering via group nodes (lines 161-205) is the right call for merged graphs; `onlyRenderVisibleElements`; the minimap; theme-aware colours throughout; the legend derived from loaded data rather than hardcoded (`ArchitecturePage.tsx:355-361`) — that comment shows exactly the right instinct.

**Ceilings:**

- **Fixed `height: 480`** (line 483) regardless of graph size. A 2,000-node graph and a 6-node graph get the same viewport.
- **`dagre.layout(g)` runs synchronously on the main thread** (line 62). `onlyRenderVisibleElements` bounds *rendering*, not *layout*. At 10k+ nodes the tab freezes before the first paint.
- **No search.** There is no way to find a node by name anywhere in this component, or in the product.
- **Selection is single-node and non-transitive.** You can see one node's direct neighbours; you cannot trace a path, expand a neighbourhood, or ask "how does A reach B".
- **The truncation escape hatch is circular.** The banner says "narrow with a type filter below" (`ArchitecturePage.tsx:305-308`), but filter options derive from the possibly-truncated load — a limitation the code itself documents at lines 192-196.

Fixes in Part 6.

## 3.3 `PipelineGraph` — the best visualization in the product

`components/workflow/PipelineGraph.tsx`. Seven distinct statuses, each with icon + colour + text label (never colour alone), `aria-current="step"` on the active stage, real `role="list"` semantics, a shimmer that appears only for `running` and not for `queued` — that distinction (documented at lines 32-39) is exactly the kind of precision that builds trust.

**Two gaps:** the nodes carry no *quantity* — no duration, cost, or confidence, all of which exist in the data (fold in 2.4 and 2.3). And `flex-1` per stage means a 6-stage pipeline on a 375px viewport gives each node ~50px; it needs a horizontal-scroll or vertical-stack fallback below `sm`.

## 3.4 `WorkflowTimeline` is dead code

**Correction to an earlier draft of this audit:** I first described this as a live duplicate of `PipelineGraph` rendered on the dashboard. It is not. `WorkflowTimeline` is referenced by **no page** — only by `WorkflowPage.test.tsx`. Its own docstring says the dashboard "used to" nest links inside it, past tense; the dashboard usage was removed and the component was left behind.

It is still worth deleting, for a weaker reason: it handles 4 statuses where `PipelineGraph` handles 7, so if anyone reaches for it again, `queued`/`partial`/`awaiting_input` will silently render as "pending" — misreporting a workflow that needs human input as merely queued.

**Recommendation:** delete the component and its tests. If a compact stage readout is needed later (e.g. the Home page in §1.1), add `variant="compact"` to `PipelineGraph` rather than reviving this. **Complexity: XS.**

## 3.5 `RepositoryOverviewGraph`

Every card carries the literal string `"Inbound dependencies: expand to see"` (`DependencyGraph.tsx:283`) — a permanent placeholder rendered on every node, which is pure noise once there are more than a handful. And unlike `DependencyGraph` it has no `MiniMap`, which is where a minimap would matter most (500 cards). Replace the placeholder with the real inbound count from `allEdgesQuery`, which the page has already fetched (`ArchitecturePage.tsx:132-136`).

---

# PART 4 — AI Explainability

This is where GraphForge's stated premise ("built on trust") and its UI diverge most sharply.

## 4.1 `PlanningConfidencePanel` reports fabricated factors

**Current** (`components/planning/PlanningConfidencePanel.tsx:34-67`)

```tsx
{ label: "Jira connected",       met: false, weight: "medium" },
{ label: "Confluence connected", met: false, weight: "medium" },
```

Both are hardcoded `false`. Jira is a real, implemented, read-only integration (per the README and `lib/api/jira.ts`). A workspace with Jira connected still sees "Jira connected ✗" and a recommendation to connect it. `"Business requirements provided"` is likewise hardcoded `met: true` — it can never be false, so it conveys nothing.

**Problem** This is the panel whose entire purpose is *justifying a confidence number*. If a user notices one factor is wrong, the rational response is to discount the whole score — and by extension every score in the product. A broken explainability surface does more damage than a missing one.

**Recommended** Drive factors from the same `getSystemStatus()` connection data the Control Center already renders, and delete factors the backend cannot evidence. Additionally: the panel presents factors as if they were *inputs to* the score, but the score comes from the agent — the factors are a UI-side reconstruction. Either the backend returns its actual factor weights, or the panel must be relabelled ("What would improve confidence" rather than "Confidence Breakdown"). **Complexity: S.** **Screens: `PlanningPage`, `WorkflowPage`.**

## 4.2 The grounding signal is a 12px footnote

`StageResultDetails.tsx` ends `PlanningResultDetails` with:

```tsx
<div className="text-xs text-fg-muted">
  Graph context: {result.graph_context_used ? "Used architecture graph data" : "No graph data available"}
</div>
```

**Problem** "Did this plan come from your actual codebase, or from the model's priors?" is *the* question that separates GraphForge from pasting a ticket into a chatbot. It is rendered as muted 12px text at the bottom of a long card, below the fold, in the visual weight class of a copyright notice.

**Recommended — a grounding banner at the top of every AI result**

```
┌──────────────────────────────────────────────────────────────────┐
│ 🔗 GROUNDED   Built from your architecture graph                  │
│    3 repositories · 47 graph facts · 12 evidence items            │
│    acme/payment-service · acme/order-service · acme/billing-api   │
└──────────────────────────────────────────────────────────────────┘

  — or, when it is not —

┌──────────────────────────────────────────────────────────────────┐
│ ⚠ UNGROUNDED   No architecture graph was available                │
│    This plan reflects general engineering knowledge, not your     │
│    codebase.  Index a repository to ground it.   [Index now →]    │
└──────────────────────────────────────────────────────────────────┘
```

**Why it is better** It moves the most consequential trust fact from last to first, gives the ungrounded case an honest warning *and* a fix, and makes the grounded case legible as a product differentiator instead of a footnote. **Complexity: S** — data already present (`graph_context_used`, `repositories_consulted`, `evidence.length`). **Screens: every stage result.**

## 4.3 Risks have no severity and no target

`PlanningResultDetails` renders `risk_considerations: string[]` as amber bullets — eight equal-weight strings. `RiskBadge` exists as a component and is not used here. A risk cannot be sorted, filtered, dismissed, or traced to the component it threatens.

**Recommended** Promote to `{ severity, description, affected_component, evidence_ids }`, render sorted by severity with the component as a link into the Architecture graph. Turns a paragraph into a triage queue. **Complexity: M** (schema change).

## 4.4 There is no "why did you conclude this?" affordance anywhere

Across `StageResultDetails`, `InvestigationSummary`, `EngineeringUnderstandingPanel`, and `ImpactAnalysisPage`, every AI conclusion is terminal text. Nothing is clickable-into. The `ReasoningLogPanel` and the `Log` tab in `StageResultPanel` come closest, but they are chronological execution traces — "what the agent did" — not causal explanations — "why this conclusion follows".

**Recommended** A consistent `[Why? ▾]` affordance on every AI-generated claim, expanding to: the evidence anchors (4.2), the graph facts consulted, and the specific prompt/response excerpt from the LLM invocation (already persisted per ADR-0012). One component, used everywhere. **Complexity: M.**

---

# PART 5 — Workflow Visualization

**What works.** `PipelineGraph` answers current/completed/failed/waiting well. `ApprovalGateBanner`, `WorkflowApprovalBanner`, and `ContextClarificationBanner` make human gates explicit. `WorkflowReplayPanel` is a genuinely inspired feature. Collapsing retries into one tab per stage with a `retry N` chip (`WorkflowPage.tsx:284-301`) is thoughtful.

**What does not.**

## 5.1 Banner soup

`WorkflowPage.tsx:358-459` renders up to six mutually-possible banners in fixed source order: error, `partialConfirm`, `ContextExplorerPanel`, `discoveryBlocksPlanning`, `ApprovalGateBanner`, `WorkflowApprovalBanner`. In the failed-plus-stale-view case you get a red error, then a warning confirm, then a large panel, then another red banner — four attention-grabbing blocks stacked, with no ranking of which the user should act on.

**Recommended — one action zone directly beneath the pipeline, with exactly one primary action**

```
┌ ⚠ ACTION NEEDED ────────────────────────────────────────────────┐
│ Context Discovery finished with partial confidence (0.58).       │
│ Planning may guess about: auth flow, retry semantics.            │
│                                                                  │
│              [ Answer 2 questions ]  [ Continue anyway ]         │
│                       primary            secondary               │
│                                                                  │
│ ▾ 1 other notice — this workflow moved on in another tab         │
└──────────────────────────────────────────────────────────────────┘
```

Rank by urgency (blocking > confirm > informational), promote one, collapse the rest. **Complexity: M.** **Screens: `WorkflowPage`.**

## 5.2 Agent ownership is invisible on the pipeline

`STAGE_AGENT_LABEL` maps stage → agent name and is used for the *tabs*, not the pipeline nodes. The pipeline says "Planning"; it never says *which agent* ran it, on which model, at what cost. For a product whose story is "12+ registered agents", the agents are absent from the one view of agent work.

**Recommended:** second line on each pipeline node — agent name + model + duration. **Complexity: S.**

## 5.3 Approval history is not recorded visually

Once approved, the gate banner disappears. Nothing shows *who* approved, *when*, or *what they saw*. For a human-in-the-loop system this is the audit trail, and it evaporates.

**Recommended:** a human-decision marker on the pipeline connector between stages — `👤 approved by sasi · 2h ago`. **Complexity: M** (needs backend approval-event persistence).

## 5.4 Live execution costs 5 requests every 2.5 seconds

`WorkflowPage.tsx:68-72` refetches *every* run's full detail on every poll tick. Four stages = 5 HTTP requests every 2.5s, indefinitely, per open tab. Recommend an `If-Modified-Since`/etag path or SSE; at minimum, refetch only runs whose `updated_at` changed. **Complexity: M.** Not cosmetic — it is a load-bearing scale problem at any real usage.

---

# PART 6 — Architecture Page Redesign (500 repos / 100k nodes / millions of edges)

## 6.1 What breaks today, concretely

| Line | Behaviour | At 500 repos |
|---|---|---|
| `ArchitecturePage.tsx:108-118` | `useQueries` — one `getLatestIndexingJob` per repository, on mount | **500 parallel HTTP requests** on page load |
| `:237-249` | `<select>` with one `<option>` per repo | 500-item native dropdown, no search, unusable |
| `DependencyGraph.tsx:62` | synchronous `dagre.layout()` | main thread blocked; tab freezes |
| `:287-291` | overview renders every repository node at once | 500 React Flow nodes before interaction |
| everywhere | no node search | you cannot find anything by name |
| `:483` | `height: 480` fixed | a 100k-node graph in a 480px box |

The truncation banner is the current mitigation, and it is honest but circular (§3.2).

## 6.2 Redesign: search-first, not browse-first

At 100k nodes, browsing is not a strategy. The page must open on a **query box**, not a graph.

```
┌─ Architecture ────────────────────────────────────────────────────┐
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ 🔍  Find a service, topic, endpoint, table…      ⌘K       │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌ 500 repos · 104,882 nodes · 2.1M relationships ─────────────┐  │
│  │  Services 312 ▓▓▓▓▓▓▓▓  Topics 88 ▓▓  Endpoints 4.1k ▓▓▓▓  │  │
│  │  Tables 902 ▓▓▓  Classes 71k ▓▓▓▓▓▓▓▓▓▓▓▓▓  Modules 28k    │  │
│  │  ⚠ 12 repos not indexed   ⚠ 4 graphs stale (>30d)          │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  DOMAINS                          ← clustered, not 500 cards     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Payments │▶│ Orders   │▶│ Fulfilment│ │ Platform │            │
│  │ 42 repos │ │ 88 repos │ │ 31 repos  │ │ 121 repos│            │
│  │ ●●● 3 hot│ │ ●● 2 hot │ │           │ │ ⚠ 8 stale│            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│                                                                   │
│  START FROM        [ A PR ]  [ A Jira issue ]  [ Impact of… ]    │
└───────────────────────────────────────────────────────────────────┘
```

**Drill path:** `org → domain → repository → module → class`. Each level loads only its own children. You never hold more than ~200 nodes in the renderer.

**Path-finding as a first-class mode** — the query users actually have:

```
┌─ How does A reach B? ────────────────────────────────────────────┐
│  From [ payment-service      ]   To [ analytics-warehouse    ]   │
│                                                                  │
│  3 paths found (shortest 3 hops)                                 │
│  ① payment-service ─PRODUCES_TO▶ payments.v2 ─CONSUMES_FROM▶     │
│      order-service ─WRITES_TO▶ analytics-warehouse    ══ high    │
│  ② … 4 hops via billing-api                           ── medium  │
│  ③ … 6 hops via events.audit                          ┈┈ low     │
└──────────────────────────────────────────────────────────────────┘
```

## 6.3 Layered views

One graph, four lenses — same data, different node-type filters and layouts:

- **Service view** — repos + services + APIs. Layout: force-directed. *"How do our services talk?"*
- **Data view** — topics, tables, datasets. Layout: Sankey-ish left-to-right. *"Where does this data go?"*
- **Code view** — modules, classes, functions. Layout: tree. *"What is inside this repo?"*
- **Risk view** — everything, coloured by change frequency × blast radius. *"What is dangerous?"*

The node-type filter chips (`ArchitecturePage.tsx:311-349`) are already the mechanism — these are named presets over it.

## 6.4 Implementation notes

| Change | Complexity |
|---|---|
| `GET /architecture/summary` — one call returning all repo counts (replaces 500 `useQueries`) | S (backend) |
| `GET /graph/search?q=&types=&limit=` — node search by name | M (backend, Neo4j full-text index) |
| `GET /graph/paths?from=&to=&maxHops=` — path finding | M (backend) |
| Replace `<select>` with a searchable combobox | S |
| Move dagre into a Web Worker; show a skeleton while laying out | M |
| Swap React Flow for a canvas/WebGL renderer above ~2k nodes (`sigma.js`, `cosmograph`); keep React Flow below that for its interaction quality | L |
| Domain clustering — needs a `domain`/`team` property on repositories | M (data model) |
| Responsive height: `min(70vh, …)` instead of `480` | XS |

**The single most valuable item here is node search.** It is currently impossible to find anything by name anywhere in GraphForge, at any scale.

---

# PART 7 — Metrics

`MetricsPage` today: 8 stat cards, cost-by-day bars, tokens-by-day line, cost-by-provider bars, cost-by-stage bars, model usage table, repository components bars, run success stacked bars, recent workflows table.

**Structural problems**

1. **Cost and tokens are plotted with different chart types on the same time axis** (bar vs line, lines 91-104) — the two series users most want to compare, made maximally hard to compare. They should be one dual-axis chart, or a single chart with a toggle.
2. **The 8 stat cards mix levels.** "Workflows", "Agent Runs", "Indexed Repositories", "LLM Calls", "Total Cost", "Total Tokens", "Avg Cost/Workflow", "Avg Tokens/Call" — volume, inventory, spend, and efficiency in one undifferentiated grid, all styled identically. Group them: **Activity | Spend | Quality**.
3. **No time-range control.** `window_days` comes from the hook and is only *displayed* (line 79). No 24h/7d/30d/90d selector.
4. **No trend/delta on any number.** "$4.82" without "↑ 34% vs last week" is not a metric, it is a reading.
5. **"Repository Graph" (components per repo) does not belong here** — that is architecture inventory, not usage. It also triggers the `slice(5)` label bug.
6. **Nothing about quality.** Every chart is volume or cost. Not one measures whether GraphForge is *good*.

**Recommended additions, ranked by decision-value**

| Visualization | Question answered | Data | Cx |
|---|---|---|---|
| **Confidence distribution histogram** | Is the confidence signal real (bimodal) or decorative (one lump)? | `runs.confidence_score` — exists | S |
| **Stage latency p50/p95 box plot** | Which stage is the bottleneck, and how variable is it? | `started_at`/`completed_at` — exists | S |
| **Cost per workflow, trended with a budget line** | Are we getting more or less efficient per unit of work? | `cost_by_day` + workflow count — exists | S |
| **Provider comparison matrix** (cost/1k tok × p95 latency × avg confidence × failure rate) | Should we switch providers? Directly actionable given DeepSeek/Bedrock/etc. are all configured | `model_usage` + run outcomes — mostly exists | M |
| **Approval outcome funnel** (started → completed → approved / rejected / abandoned) | Do humans actually accept what the AI produces? **The single best measure of product value.** | needs approval events | M |
| **Failure taxonomy** (timeout / parse error / graph missing / provider error) | What should we fix first? | `error_message` — needs classification | M |
| **Evidence-per-conclusion trend** | Is grounding improving or degrading over time? | evidence counts — exists | S |
| **Cost treemap** workflow→stage→model | Where did the budget actually go? | exists | M |

**Sequencing note:** the first three are near-free once §3.1's chart primitives land. Do §3.1 first.

---

# PART 8 — Detail Pages

## 8.1 `RepositoryDetailPage`

Missing: **indexing history** (each job's node/edge delta over time — "the graph shrank 40% last Tuesday" is currently undiscoverable), **graph growth sparkline**, **change hotspots**, and a **dependency neighbourhood preview** (a small graph of just this repo's inbound/outbound repository edges — the data is already fetched by `getCrossRepositoryLinks`).

```
┌ Indexing History ────────────────────────────────────────────────┐
│  nodes  ▁▂▃▃▄▄▅▅▅▆▆▇█        1,204  ↑ 6% (30d)                   │
│  edges  ▁▂▂▃▄▄▅▆▆▇▇██        3,881  ↑ 11%                        │
│  ⚠ 14 Mar — node count dropped 38% (parser upgrade)   [diff →]   │
└──────────────────────────────────────────────────────────────────┘
```

## 8.2 `PullRequestDetailPage`

Two "Not analyzed yet." dead ends (lines 532, 620) with no action offered. Missing: a **file-level risk heatmap**, a **before/after impact diff** (what the graph looked like before this PR vs after — the product's whole premise), and a **review timeline** (webhook received → indexed → analyzed → reviewed → published).

## 8.3 `RunDetailPage`

`StageResultPanel`'s tab set (Blueprint | Summary | Evidence | Log | JSON) is well-designed. Missing: **cost/token/latency for this specific run** (present in the aggregate metrics, absent where it is actionable), a **link to the parent workflow's other stages**, and a **comparison to the previous attempt** when `retry N` is present — the retry chip advertises a prior attempt the user then cannot see.

## 8.4 Universal gap: no change history on anything

Not one detail page has a "what changed" view. For a system whose value is *understanding a codebase over time*, every page is a snapshot. A shared `<ChangeTimeline>` component consuming a generic events feed would serve repository, workflow, PR, and architecture-node pages alike. **Complexity: L** (needs backend event sourcing) but it is the highest-leverage architectural investment on this list.

---

# PART 9 — Dashboard by Audience

One home page cannot serve four audiences. Recommend a **role-selectable default view** over the same widget library.

**Developer** (default) — *"what's mine, what's blocked"*
Waiting on you · My in-flight workflows · My recent runs · My PRs under review · Quick action: New Workflow

**Engineering Manager** — *"is the team flowing?"*
Team throughput (workflows/week, trended) · Approval funnel (accepted vs rejected — is the AI useful?) · Stage bottleneck (p95 latency by stage) · Blocked >24h · Confidence trend

**Platform Engineer** — *"is the system healthy?"*
Today's Control Center, promoted: provider health/latency/error rate · Indexing queue depth and failures · Stale graphs (>30d) · Webhook delivery success · Neo4j/Postgres connection health

**Executive** — *"is this worth it?"*
Spend vs budget, trended · Engineer-hours estimated saved (workflows × baseline) · Adoption (active users, repos covered / repos total) · Quality (approval rate) · Coverage map (which domains use GraphForge)

**Implementation:** one `DashboardWidget` registry; role stored in user prefs; every widget already has a data source except the approval funnel. Ship Developer first — it is the only one with no new backend work. **Complexity: M for the framework, S per widget.**

---

# PART 10 — Empty States

**Current inventory** — every empty state in the product is a single grey sentence:

| Location | Text |
|---|---|
| `SimpleCharts.tsx:116,154,212` | "No data to show." (×3) |
| `ArchitecturePage.tsx:284` | "No repositories tracked yet." |
| `ArchitecturePage.tsx:298` | "No graph data yet - index this repository first." |
| `RunHistoryPage.tsx:361` | "No agent runs yet." |
| `MetricsPage.tsx:268` | "No LLM invocations recorded yet." |
| `PullRequestsPage.tsx:64` | "No pull requests yet." |
| `ReportsPage.tsx:196` | "No reports generated yet." |
| `PullRequestDetailPage.tsx:532,620` | "Not analyzed yet." |

Only one does better: `RepositoriesPage.tsx:331` tells you where to go — *"Connect GitHub and select repositories in Settings → Integrations"* — but as plain text, not a link.

**Problem** The empty state is the first thing a new user sees on nearly every page, and it is a dead end every time. For a product whose value is invisible until data flows in, the empty state *is* the onboarding.

**Recommended pattern — every empty state gets: what this is · a preview · one action**

```
┌─ Architecture ────────────────────────────────────────────────────┐
│                                                                    │
│      ┌──────┐         ┌──────┐                                     │
│      │ svc  │ ──────▶ │ topic│         ← greyed sample graph,      │
│      └──────┘         └──────┘            not an empty box         │
│           ╲            ╱                                           │
│            ▼          ▼                                            │
│           ┌────────────┐                                           │
│           │  consumer  │                                           │
│           └────────────┘                                           │
│                                                                    │
│   Your architecture graph appears here                             │
│   GraphForge indexes your repositories and maps every service,     │
│   API, topic, and table — plus how they depend on each other.      │
│                                                                    │
│   [ Connect GitHub ]  [ Try demo data ]  [ How indexing works ↗ ]  │
│      primary             secondary          tertiary               │
└────────────────────────────────────────────────────────────────────┘
```

**Demo mode already half-exists** — `Sidebar.tsx:79` renders *"Sample data — no repositories connected yet."* Formalize it: a `?demo=1` flag that seeds every visualization from a fixture repo. It doubles as the hackathon demo path and as the way a prospective user sees the product before connecting anything.

**Per-page specifics**

| Page | Preview | Primary action |
|---|---|---|
| Architecture | sample 6-node graph | Connect GitHub |
| Runs | sample pipeline | New Workflow |
| Metrics | sample charts, greyed | New Workflow |
| Pull Requests | sample PR row | Enable PR webhook |
| Reports | sample report card | Review a PR |
| PR "Not analyzed yet" | — | **Analyze now** ← currently a dead end on a live PR |

**Complexity: S per page** once one shared `<EmptyState>` component exists. **Highest priority: the three that are also the highest-traffic first-run pages** — Architecture, Runs, Metrics.

---

# PART 11 — Micro UX

## 11.1 Absent entirely

- **No command palette, no keyboard shortcuts.** Grep for key handlers returns only four files, all local (dropdown/typeahead). A power-user product with 20+ routes and zero `⌘K` is leaving a lot of speed on the table. **Recommend: `⌘K` global — jump to page, find repository, find graph node (once §6 lands), start workflow.** **Complexity: M. Very high perceived-quality return.**
- **No toast system.** Every success and failure is an inline banner whose position varies by page; some appear below the fold. Async results (workflow created, run deleted, report published) get no acknowledgement at all in some paths. **Complexity: S.**
- **No table sorting or filtering.** `Table.tsx` has no sort concept. Every list page — Runs, Repositories, PRs, Model Usage, Recent Workflows — is fixed-order. Runs has pagination but no filters (by status, stage, provider, date). **Complexity: M**, and it is the difference between a table you scan and a table you use.

## 11.2 Destructive actions use `window.confirm()`

Three places delete irreversibly through a native browser dialog: `RunHistoryPage.tsx:69` (delete run), `:218` (**delete workflow and all its stage runs**), `RepositoryDetailPage.tsx:152`.

`window.confirm` cannot be styled, cannot show context (which workflow? how many runs? was it approved?), reads as a browser malfunction in a polished product, and blocks the main thread. For deleting an approved workflow with its entire evidence trail, it is meaningfully under-weighted.

**Recommend:** a shared `<ConfirmDialog>` showing exactly what will be destroyed (`"Delete 'Rate limiting on payment API' — 4 runs, 19 evidence items, 1 approved blueprint"`), with type-to-confirm for approved workflows. **Complexity: S.**

## 11.3 Loading states are inconsistent

Four different treatments across four pages: skeleton (`ControlCenterPage:66-70`), skeleton (`WorkflowPage:522-533`), centred text "Loading repositories…" (`ArchitecturePage:279`), centred text "Loading metrics…" (`MetricsPage:72`), and `emptyMessage="Loading…"` reusing the *empty* slot for the *loading* state (`RunHistoryPage:358`, `PullRequestsPage:64`) — which is a category error: "nothing here" and "not yet known" are different facts.

**Recommend:** skeletons everywhere (they preserve layout and prevent shift), never text; never reuse `emptyMessage` for loading. **Complexity: S.**

## 11.4 Smaller items

| Item | Finding | Fix |
|---|---|---|
| Long-run feedback | "This may take up to a minute" is static text (`ImpactAnalysisPage:127`) | Elapsed counter + what the agent is doing now |
| Graph tooltips | Node detail only via label text; no hover card | Hover card with type, properties, neighbour count |
| Context menus | None anywhere | Right-click a graph node → expand / focus / impact / copy id |
| Truncation | `title` attributes used widely for overflow | Real tooltip component; `title` has ~1s delay and no touch support |
| Focus | `focus-ring` class used in only a few places | Apply consistently to every interactive element |
| Breadcrumbs | None; `/workflows/:id` has a "← Back to dashboard" link that points to `/` regardless of where you came from | Real breadcrumbs in the Topbar |
| Motion | No `prefers-reduced-motion` guard on `animate-spin`, the pipeline shimmer, or graph transitions | One media query in `index.css` |

---

# PART 12 — Accessibility

The baseline doc is honest and the token system is genuinely well-built (`no-raw-palette.test.ts` enforcing tokens is excellent discipline). Gaps below are real, not theoretical.

## 12.1 Coverage

3 of ~28 routes have axe assertions (documented in `ACCESSIBILITY_BASELINE.md`). Extending is mechanical. **Priority order:** Architecture, Metrics, Run History, Workspace, Settings.

## 12.2 Charts are unreadable without a mouse

`SimpleCharts` exposes values **only** through SVG `<title>` and `title` attributes. No `role="img"`, no `aria-label`, no data table alternative. A keyboard or screen-reader user gets zero values from any chart in the product. `StackedBarChart` additionally encodes success/failure by colour alone (green/red divs) — a WCAG 1.4.1 failure independent of the tooltip issue.

**Fix:** each chart wrapped with `role="img"` + a summarising `aria-label`, plus an `sr-only` `<table>` of the underlying data. ~15 lines per chart, and the sr-only table doubles as a "view as table" toggle for sighted users. **Complexity: S. High impact.**

## 12.3 The graph is completely inaccessible

React Flow canvases have no keyboard navigation, no focus order, no text alternative. A 100k-node graph has no non-visual representation of any kind. This is not solvable by tweaking — it needs a **parallel table/tree view** of the same data (which sighted users will also want at scale, per Part 6).

**Fix:** a `[Graph] [Table]` toggle on every graph surface, with the table as the canonical accessible representation. **Complexity: M.** Serves accessibility and scale with one build.

## 12.4 Heading structure

Covered in §1.3: no `<h1>` on any page (Topbar owns it, page bodies start at `<h2>`), and `Card` emits `<h2>` making card titles peers of the page title. Breaks heading-navigation for screen-reader users on every page.

## 12.5 Responsive / mobile

| Issue | Where |
|---|---|
| Graph `height: 480` fixed; React Flow pan/zoom fights page scroll on touch | `DependencyGraph.tsx:483` |
| `grid-cols-2` stat cards at 320px → ~140px per card | `MetricsPage.tsx:204` |
| `PipelineGraph` `flex-1` per stage → ~50px per node at 375px with 6 stages | `PipelineGraph.tsx:91` |
| Tables scroll horizontally with no column priority; key columns can be off-screen | `Table.tsx` |
| `<select>` for repositories — degrades badly at scale on mobile | `ArchitecturePage.tsx:237` |

**Recommend:** define mobile intent explicitly. GraphForge is a desktop analysis tool; the honest mobile scope is **read + approve** — Home, workflow status, approve/reject, run results. Graphs get a "best viewed on desktop" affordance plus the table fallback from §12.3, rather than a bad pinch-zoom experience.

## 12.6 Motion & live regions

Only two `aria-live` regions exist in the product (`RunProgress`, `WorkflowReplayPanel`). Workflow stage transitions during the 2.5s poll change the screen silently — a screen-reader user gets no notification that Planning finished. Add a polite live region announcing stage transitions.

**Correction to an earlier draft of this audit:** I wrote that there was no `prefers-reduced-motion` handling anywhere. That was wrong — `index.css` has had a global `@media (prefers-reduced-motion: reduce)` block neutralising all animation and transition durations since before this audit. Motion handling is **already done** and needs no work.

---

# PART 13 — Prioritized Roadmap

## Quick Wins — days, high ratio

| # | Item | Why | Cx | Screens |
|---|---|---|---|---|
| 1 | Fix `ChartLabels.slice(5)` | Corrupts repository names — a bug | XS | Metrics |
| 2 | Normalize `StackedBarChart` | Card claims "rate", chart shows volume — a bug | XS | Metrics |
| 3 | Un-hardcode `PlanningConfidencePanel` factors | A trust surface stating falsehoods | S | Planning, Workflow |
| 4 | Promote grounding to a banner (§4.2) | Moves the #1 trust fact from footnote to headline | S | All stage results |
| 5 | Shared `<EmptyState>` + fix top 3 pages | Empty states are the onboarding | S | Architecture, Runs, Metrics |
| 6 | `<ConfirmDialog>` replacing `window.confirm` | Irreversible deletes deserve better | S | Runs, Repos |
| 7 | Chart `role="img"` + `sr-only` table | Charts currently mouse-only | S | Metrics |
| 8 | Heading hierarchy (`h1`/`h3`) | Breaks SR navigation on every page | S | All |
| 9 | Delete `WorkflowTimeline`, add `PipelineGraph variant="compact"` | Removes a state-misreporting duplicate | S | Home, Workflow |
| 10 | Responsive graph height; `prefers-reduced-motion` | Two media queries | XS | All graphs |

## High Impact — weeks, changes what the product is

| # | Item | Why | Cx | Screens |
|---|---|---|---|---|
| 11 | **Home = work surface** (§1.1) | Users cannot currently see what is waiting on them | M | `/`, Topbar |
| 12 | **Impact Analysis blast-radius graph** (§2.1) | The flagship feature has no visualization | L | Impact Analysis |
| 13 | **Graph node search + `⌘K` palette** (§6.2, §11.1) | You cannot find anything by name, anywhere | M | Global |
| 14 | **Evidence anchored to claims** (§2.2) | Makes "every claim is traceable" true | L | All AI outputs |
| 15 | Rebuild chart primitives — axes, tooltips, scales (§3.1) | Unblocks all of Part 7 | M | Metrics |
| 16 | Architecture `/summary` endpoint (kills 500 requests) | Page is unusable at target scale | S+M | Architecture |
| 17 | Table sorting + filtering | Every list page is fixed-order | M | All lists |
| 18 | Consolidate workflow banners into one action zone (§5.1) | Banner soup at the moment of decision | M | Workflow |
| 19 | Confidence progression + stage waterfall (§2.3, §2.4) | Two charts from data already persisted | S | Workflow, Run |

## Medium Impact — a quarter

| # | Item | Cx |
|---|---|---|
| 20 | Architecture drill-down + domain clustering (§6.2) | L |
| 21 | Path finding between two nodes (§6.2) | M |
| 22 | Graph `[Graph][Table]` toggle — scale + a11y in one (§12.3) | M |
| 23 | Metrics Part 7 additions: confidence distribution, latency p95, provider matrix, approval funnel | M |
| 24 | Role-based dashboards (§9) | M |
| 25 | Risks as structured objects with severity + component links (§4.3) | M |
| 26 | Workflow lineage tree (§2.5) | S |
| 27 | Toast system + consistent skeletons | S |
| 28 | Formalized demo mode (§10) | M |
| 29 | Navigation restructure (§1.2) | S |
| 30 | Poll efficiency — etag/SSE (§5.4) | M |

## Long Term — foundational

| # | Item | Why | Cx |
|---|---|---|---|
| 31 | **Event sourcing → universal `<ChangeTimeline>`** (§8.4) | Every detail page is a snapshot; "understanding over time" is the product premise | L |
| 32 | **Canvas/WebGL graph renderer above ~2k nodes** | React Flow's DOM model cannot reach 100k | L |
| 33 | **Layered architecture views** (service/data/code/risk) (§6.3) | One graph, four questions | L |
| 34 | **Graph diff across indexing runs** | Nobody can see what re-indexing changed | L |
| 35 | **Evidence Sankey — graph facts vs. model priors** (§2.6) | The definitive grounding measure | L |
| 36 | Full WCAG AA audit + VPAT | Enterprise prerequisite | L |

---

## Closing

Three sentences, if only three land:

1. **Draw the graph you already have.** Impact Analysis, the feature that most needs a picture, ships bullets — in a codebase that already contains a competent graph renderer.
2. **Show the work, not just the answer.** Anchor evidence to claims, promote grounding above the fold, and never let an explainability panel state something it has not verified.
3. **Put the user's work on the home page.** GraphForge's loop is AI-proposes / human-approves, and the human currently has no idea they are the bottleneck.

Everything else in this document is downstream of those three.
