# UI_GUIDELINES.md — GraphForge

Evolves the existing dark-themed Tailwind UI (Card/Table/StatusBadge/RiskBadge, sidebar +
topbar `AppLayout`). No visual reset. New surfaces must be indistinguishable in craft from the
existing Dashboard/PullRequestDetail pages.

## Design System

- **Framework**: Tailwind utility classes, no component library dependency (existing convention —
  keep it; do not introduce MUI/Chakra/etc.).
- **Base unit**: 4px spacing scale (`gap-1`…`gap-6` as already used). No new spacing scale.
- **Corners**: `rounded-md` for buttons/badges, `rounded-lg` for cards/panels — existing
  convention, keep consistent as new components are added.
- **Elevation**: no drop shadows; surfaces are distinguished by background layer
  (`bg-slate-950` page → `bg-slate-900`/`Card` → `bg-slate-800/60` nested panel → `bg-slate-800/80`
  chip), matching the existing three-layer depth already in `PullRequestDetailPage`.

## Color Palette

| Token | Hex (Tailwind) | Usage |
|---|---|---|
| Background | `slate-950` | Page background |
| Surface | `slate-900` | Card background |
| Surface nested | `slate-800/60`–`slate-800/80` | Panels, chips, list rows |
| Border | `slate-800` | Card/panel borders |
| Text primary | `slate-50`/`slate-200` | Headings, key values |
| Text secondary | `slate-400`/`slate-500` | Labels, metadata, captions |
| Primary action | `sky-600` / hover `sky-500` | Deterministic/AI run actions (existing) |
| Agentic action | `violet-600` / hover `violet-500` | Agent-invoking actions (existing: "Investigate") |
| Publish / external-write action | `emerald-600` / hover `emerald-500` | Actions with an external side effect (existing: "Publish Review") |
| Danger | `rose-500`/`rose-300` on `rose-500/10` bg | Errors, HIGH risk, blocking urgency |
| Warning | `amber` tones | MEDIUM risk, advisory urgency |
| Success | `emerald` tones | LOW risk, completed states |

**Rule**: color communicates *category of action or risk*, never decoration. A new button color
must map to a new action category defined here — do not introduce a color for visual variety.

## Navigation

Existing sidebar retained, extended with the SDLC-continuous surfaces:

```
Dashboard
Pull Requests
Repositories
Architecture
Projects          [NEW — Jira/Confluence-resolved work items]
Knowledge Graph   [NEW — org-wide graph search/explore, generalizes Architecture page]
Agents            [NEW — cross-cutting agent run history/timeline]
Pipeline          [NEW — SDLC-stage board]
Reports
Settings
```

`Architecture` (existing, single-repo dependency view) is retained as a scoped entry point into
the same graph that `Knowledge Graph` exposes org-wide — they share the `DependencyGraph`
component, not a duplicate implementation.

## Page Wireframes

### Dashboard (existing, retained)

```
┌─ Topbar ────────────────────────────────────────────────┐
│ GraphForge            [user] [logout]                    │
├─ Sidebar ─┬───────────────────────────────────────────────┤
│ Dashboard │  Risk summary cards (HIGH/MED/LOW counts)      │
│ ...       │  Recent pull requests table                   │
│           │  Recent agent activity  [NEW strip]            │
└───────────┴───────────────────────────────────────────────┘
```

### Projects (new)

```
┌───────────────────────────────────────────────────────────┐
│ Projects                                    [+ New scope]  │
├───────────────────────────────────────────────────────────┤
│ ┌ Card: ENG-421 ──────────┐ ┌ Card: ENG-430 ─────────────┐│
│ │ Story · Requirement Agent│ │ Story · Planning Agent     ││
│ │ status: clarified         │ │ status: planned            ││
│ │ linked: 2 PRs, 1 ADR       │ │ linked: 0 PRs               ││
│ └───────────────────────────┘ └────────────────────────────┘│
└───────────────────────────────────────────────────────────┘
```

Cards here are the existing `Card` component — same title/description/action-slot shape used on
`PullRequestDetailPage` today, applied to a `Story` subject instead of a `PullRequest`.

### Knowledge Graph (new, generalizes existing Architecture page)

```
┌───────────────────────────────────────────────────────────┐
│ Knowledge Graph        [search: "order.cancelled"    ] 🔍 │
├───────────────────────────────────────────────────────────┤
│                                                             │
│              [ existing DependencyGraph canvas,            │
│                extended node types: Story/Doc/Test/Release]│
│                                                             │
├─ Node detail panel (right, on select) ─────────────────────┤
│ order-service (Repository)                                 │
│ Owners: @alice, @bob        [from CODEOWNERS]               │
│ Recent PRs: #12, #14                                        │
│ ADRs: ADR-0008                                               │
└───────────────────────────────────────────────────────────┘
```

### Agents (new)

```
┌───────────────────────────────────────────────────────────┐
│ Agents                            [Filter: agent, status]  │
├───────────────────────────────────────────────────────────┤
│ Run  Agent        Subject         Confidence  Status  Time │
│ ───  ───────────  ──────────────  ──────────  ──────  ──── │
│ #88  Review       PR #14          88%         done    2m   │
│ #87  Planning     ENG-421         —           running 12s  │
│ #86  Architecture PR #12          71%         done    5m   │
└───────────────────────────────────────────────────────────┘
        ↓ click a row →
┌─ Run #88 detail (existing ReasoningLogPanel pattern) ──────┐
│ Step 1: Plan → goal, plan text                             │
│ Step 2: read_dependency_graph → observation summary         │
│ Step 3: Decide → decision text                              │
│ Evidence: [links to graph nodes, tool outputs]               │
└─────────────────────────────────────────────────────────────┘
```

This reuses the existing `ReasoningLogPanel` component verbatim — it was already agent-agnostic
in its props (`steps: ReasoningStep[]`); the Agents page is its first cross-agent, cross-PR
consumer instead of being embedded only in `PullRequestDetailPage`.

### Pipeline (new)

```
┌───────────────────────────────────────────────────────────┐
│ Pipeline — ENG-421                                          │
├────────┬────────┬────────┬────────┬────────┬────────┬─────┤
│Require │ Plan   │ Design │ Build  │ Review │ Test   │Release│
│  ✓     │  ✓     │  ●     │        │        │        │       │
│clarified│planned │running │        │        │        │       │
└────────┴────────┴────────┴────────┴────────┴────────┴─────┘
```

Each column is a `StageCard` — a thin wrapper around the existing `Card` + `StatusBadge`
components representing one agent's latest run against this Subject. Clicking a column links to
its `Agents` run detail (above).

## Cards

Existing `Card` component contract (title, description, optional action slot, children) is the
canonical container — every new surface (Projects, Pipeline stages, Agent summaries) composes it
rather than introducing a second card primitive.

## Tables

Existing `Table` component (used in Dashboard, PullRequestsPage) is the canonical list view.
Agents run history and Projects list both use it. No new table component.

## Typography

| Level | Class | Usage |
|---|---|---|
| Page heading | `text-xl font-semibold text-slate-50` | `<h2>` per page (existing) |
| Card title | `text-sm font-medium text-slate-200` | Card component internal (existing) |
| Section label | `text-xs font-medium uppercase tracking-wide text-slate-500` | existing convention, e.g. "Directly impacted" |
| Body | `text-sm text-slate-300` | Summaries, descriptions |
| Metadata/caption | `text-xs text-slate-500` | Timestamps, confidence, counts |

## Spacing

Page-level vertical rhythm: `flex flex-col gap-6` between major cards (existing
`PullRequestDetailPage` pattern). Within a card: `gap-4` between sections, `gap-2` between related
items. Do not introduce ad hoc spacing values outside the existing 1/2/3/4/6 gap scale.

## Interaction Guidelines

- Every action that has a real-world side effect (posting a comment, updating Jira, triggering a
  deploy) uses the `emerald` action color and a title tooltip stating the side effect explicitly
  — established by the existing "Publish Review" button; every future agent-triggered write
  follows this exact precedent, not a bespoke confirmation modal per feature.
- Buttons disable during their own in-flight request AND during any other in-flight action in the
  same card that would race it — existing precedent: Run AI / Investigate / Publish Review are
  mutually disabling on `PullRequestDetailPage`. Apply the same rule to any new multi-action card.
- Agent confidence is always rendered as a percentage next to the claim it supports, never as a
  bare adjective ("high confidence") — evidence-over-assertion applies to the UI, not just the
  data model.

## Loading States

- Button-local: label swaps to a present-participle verb ("Running…", "Publishing…",
  "Investigating…") — existing convention, extend verbatim to new actions ("Planning…",
  "Resolving…").
- Page-local: existing `<p className="text-sm text-slate-500">Loading…</p>` placeholder pattern
  for full-page loads (e.g. `PullRequestDetailPage` while `pr` resolves). Reuse, don't reinvent a
  spinner component.
- Long-running agent runs (>2s) show the `Agents` run-detail panel mid-run with steps appearing as
  they complete — never a blank spinner for a multi-step process the user could instead watch
  reason.

## Empty States

Existing convention: a single `text-sm text-slate-500` sentence stating what to do next ("Not
analyzed yet.", "Run AI analysis above to generate a release coordination plan."). Every new
empty state follows this — one sentence, states the action that fills it, no illustration.

## Error States

Existing convention: `rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm
text-rose-300` banner at the top of the page content, populated from the real error message (never
a generic "Something went wrong"). Extend verbatim to all new pages — no per-page bespoke error UI.

## Animations

Minimal by design — this is a dense engineering tool, not a marketing site. Only:
- Disabled/enabled button opacity transition (existing `disabled:opacity-50`, implicit CSS
  transition via Tailwind defaults).
- Agent run steps appearing incrementally on the Agents run-detail panel (see Loading States) —
  a simple fade-in per step, no motion library.
No page transitions, no skeleton shimmer beyond what's explicitly specified above.

## Component Library

| Component | Status | Notes |
|---|---|---|
| `Card` | existing, reused everywhere | |
| `Table` | existing, reused everywhere | |
| `StatusBadge` | existing | risk/urgency/status chips |
| `RiskBadge` | existing | HIGH/MEDIUM/LOW specifically |
| `ReasoningLogPanel` | existing | promoted to shared, cross-agent use |
| `DependencyGraph` | existing | promoted to shared, org-wide Knowledge Graph use |
| `AiModelSelector` | existing | reused wherever an agent action lets the user pick a model |
| `AgentCard` | NEW | one agent run summary — thin `Card` wrapper |
| `ConfidenceBadge` | NEW | percentage chip, color-neutral (confidence is not risk) |
| `EvidencePanel` | NEW | renders an `AgentStep`'s evidence links (graph nodes / tool outputs) |
| `StageCard` / `StageColumn` | NEW | Pipeline board cells, `Card` + `StatusBadge` composition |

New components are added to this table before being written — if a component isn't listed here,
it should not exist yet.

## Accessibility

- All interactive elements are real `<button>`/`<a>` elements (existing convention — no `div
  onClick`).
- Color is never the sole signal: risk/status badges pair color with a text label (existing
  `StatusBadge` pattern) — extend this to every new badge type, including `ConfidenceBadge`.
- Disabled states use the `disabled` attribute (existing), not a visual-only style, so screen
  readers and keyboard nav respect it.
- These conventions now have automated regression coverage (`jest-axe` in the Vitest suite,
  KAN-38) — see `docs/graphforge/ACCESSIBILITY_BASELINE.md` for scope, coverage, and how to add
  an `axe(container)` assertion to a new page's test.

## Responsive Design

Existing `AppLayout` sidebar-collapses-on-mobile pattern is retained. New grid layouts (Projects
cards, Pipeline columns) use the existing `grid-cols-1 sm:grid-cols-3`-style responsive utility
convention already used in `DeterministicPanel`'s APIs/Topics/Libraries row — collapse to a single
column below `sm`, never introduce a bespoke breakpoint.

## Consistency Rules

1. A new page never introduces a new card, table, or badge visual style — it composes the ones
   listed in Component Library.
2. A new action button's color must map to an existing action category (primary/agentic/publish/
   danger) — never a new arbitrary hue.
3. Every new empty/error/loading state matches the copy-and-class pattern above exactly — no
   per-page creative reinterpretation.
4. Any agent-produced claim rendered in the UI must show its confidence and link to its evidence —
   enforced by using `AgentCard`/`EvidencePanel`, never a raw text dump of agent output.
