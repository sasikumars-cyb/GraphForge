# UI_GUIDELINES.md — GraphForge

> **Historical notice (documentation cleanup pass):** this document was
> written for an earlier product shape — a `Dashboard`/`PullRequestDetail`-
> centric tool with raw Tailwind color classes (`slate-950`, `rose-500`,
> …) and a proposed `Projects`/`Knowledge Graph`/`Agents`/`Pipeline` nav
> that was never built as described. The product has since moved to the
> conversational **AI Workspace** paradigm (Ask GraphForge, Refinement
> Planner, Migration Assistant, Planning/Development/Testing) and a
> **semantic design-token system** (`frontend/src/styles/tokens.css` —
> `bg-surface`, `text-fg-muted`, `text-danger-fg`, etc. — never a raw
> Tailwind color class). The Color Palette and Page Wireframes/Navigation
> sections below are **historical, not current** — removed rather than
> silently left wrong. The Interaction/Loading/Empty/Error/Animation/
> Accessibility/Consistency conventions further down are still
> directionally followed in spirit, but verify exact class names against
> current code before relying on them literally.
>
> **No fully current design-system document exists yet** — that's a real
> documentation gap, not something this cleanup pass fabricated a
> replacement for. For the actual current source of truth, read
> `frontend/src/styles/tokens.css` (colors/tokens) and
> `frontend/src/components/layout/nav-items.ts` (navigation) directly.

Evolves the existing dark-themed Tailwind UI (Card/Table/StatusBadge/RiskBadge, sidebar +
topbar `AppLayout`). No visual reset. New surfaces must be indistinguishable in craft from the
existing pages.

## Design System

- **Framework**: Tailwind utility classes, no component library dependency (existing convention —
  keep it; do not introduce MUI/Chakra/etc.).
- **Base unit**: 4px spacing scale (`gap-1`…`gap-6` as already used). No new spacing scale.
- **Corners**: `rounded-md` for buttons/badges, `rounded-lg` for cards/panels — existing
  convention, keep consistent as new components are added.
- **Elevation**: no drop shadows; surfaces are distinguished by background layer, not a shadow.

**Rule**: color communicates *category of action or risk*, never decoration — expressed today via
semantic tokens (`bg-danger-bg`/`text-danger-fg`, etc. — see `tokens.css`), not raw Tailwind hues.

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
