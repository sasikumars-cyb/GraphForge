# The Architecture Experience, Redesigned From First Principles

**A design exercise, not a build plan.** Nothing here is implemented. Written to
answer one question directly: at 1,000–100,000 nodes per repository, what should
a user actually see, and why is "give them a graph and let them browse it" the
wrong default answer at that scale?

## The core reframing

Architecture Page V2 (just shipped) is a genuinely good **graph explorer**:
search-first landing, real drill-down, lazy loading, node detail panels. But it
is still organized around the graph's own shape — repositories, then nodes,
then a node's neighbors. That's the right tool for one specific job
("I don't know what I'm looking for yet, let me look around") and the wrong
default for almost every other reason someone opens this page.

Nobody opens an architecture tool because they want to look at a graph. They
open it because they have a question:

- *"If I change this, what breaks?"* → **Impact**
- *"What does this depend on, and what depends on it?"* → **Dependency**
- *"Who's responsible for this?"* → **Ownership**
- *"What's dangerous to touch right now?"* → **Risk**
- *"How is this system actually structured?"* → **Architecture**

Only the last one is genuinely served by "hand me the graph and let me
explore." The other four have a specific, answerable question, a specific
starting point (a PR, a service name, a person, "show me the scary stuff"),
and a specific shape the answer wants to take — a ranked list, a tree, an org
chart, a heatmap — that is *not* a force-directed node-and-edge diagram.

**The redesign's core move: one graph, five lenses, each with its own entry
point and its own default visualization — none of which is "browse the raw
graph" except the one lens where that's actually the job.**

## Why "progressive disclosure" means more than "load less at once"

Architecture Page V2's progressive disclosure is real but narrow: it discloses
*fewer nodes at a time*. A from-scratch design disclosure needs a second axis:
*less graph-shaped-ness* at every level until the user has actually asked a
graph-shaped question.

```
Level 0 — Goal            "What are you trying to do?"        Not a graph at all.
Level 1 — Semantic group   Domain / team / risk tier            Not a graph at all.
Level 2 — Named entity     One service, one PR, one owner       A summary card.
Level 3 — Task answer      Ranked list / tree / heatmap         Lens-specific shape.
Level 4 — Raw graph        Nodes and edges, force-directed       Architecture lens only,
                                                                  reached deliberately.
```

A user should be able to get a real, useful answer at Level 2 or 3 for four of
the five lenses and *never see a single ReactFlow node*. The graph is the
data source, not the UI, for everything except open-ended exploration.

## Semantic grouping is what makes 100k nodes navigable at all

Never show a raw node list past a few dozen items. Every level between the
goal and a specific answer groups by one of four pre-computed axes, all
derivable from data the graph (plus the repository/PR/ownership metadata
already in Postgres) already has:

- **Domain / team** — Architecture Page V2's own `domain` field, generalized:
  every lens's Level 1 can filter by it.
- **Architectural layer** — service / data / code (the audit's own "layered
  views" idea, §6.3 of the earlier UX audit) — not everyone needs to see
  `Class`/`Function` nodes to answer "what does this depend on."
- **Risk tier** — change frequency × blast radius × test coverage, computed
  once, read everywhere (this is the Risk lens's entire Level 1, and a filter
  every other lens can apply).
- **Blast-radius tier** — for Impact specifically: direct / one-hop / far.

These aren't new concepts invented per-lens — they're the same four
groupings, reused as the Level-1 vocabulary across all five.

## The five lenses

### Impact — "If I change this, what breaks?"

| Level | What the user sees |
|---|---|
| 0 — Goal | Not a picker at all — this lens is *reached from context*: a PR diff, a commit, or typing a service/file name into a global search. Task-oriented means the entry point lives where the task starts, not on a page the user has to remember exists. |
| 1 — Scope | The changed files/components, already resolved from the PR/commit — grouped by which service they belong to. No graph. |
| 2 — Blast radius | A ranked list, not a diagram: "Direct impact (3)", "One hop away (12)", "Further (47, low confidence)" — each entry is a real component with its own risk tier, owner, and last-changed date, sortable. This is the Impact page's *primary* surface. |
| 3 — Evidence | Click one impacted item → a detail panel (reusing `NodeDetailPanel`'s shape) showing *why* it's impacted: the specific edge/relationship chain, not a fresh graph render. |
| 4 — Graph (opt-in) | "View as graph" reveals the blast-radius subgraph using the *existing* `RepositoryGraphExplorer` neighborhood view, seeded from the changed nodes instead of a manually clicked one — the same component, a different seed. Never the default. |

### Dependency — "What does this depend on / depend on it?"

| Level | What the user sees |
|---|---|
| 0 — Goal | A service picker (search, same box as Architecture Page V2's landing search) or reached from a repository's own detail page. |
| 1 — Direction | Two tabs, not a bidirectional diagram: **Depends on** / **Depended on by** — most real dependency questions are one-directional, and forcing both onto one diagram is exactly the "everything at once" problem this whole redesign exists to avoid. |
| 2 — Tree | A collapsible tree (indentation, not force-directed layout) — "billing-service → payments-lib → stripe-sdk (external)". Depth-limited by default (3 levels), "expand further" per-branch, mirroring the lazy-load-on-demand principle already proven in Architecture Page V2. |
| 3 — Detail | Click a tree node → the same `NodeDetailPanel`. |
| 4 — Graph (opt-in) | "View as graph" — same neighborhood mechanism as Impact, seeded from the picked service, edge-type-filtered to only `DEPENDS_ON`/`IMPORTS`/`CALLS`. |

### Ownership — "Who's responsible for this?"

| Level | What the user sees |
|---|---|
| 0 — Goal | Search a component/service, or browse by team. |
| 1 — Org shape | A team/domain roster — literally the `domains[]` the Architecture summary already returns, rendered as team cards instead of repo-count cards. **Never a graph** — ownership is fundamentally a *mapping*, not a network, and forcing it into node-and-edge form is a category error the current design doesn't make but a naive "everything is a graph view" redesign would. |
| 2 — Team detail | A team's owned repositories/domains, with staleness/health rolled up per team (reusing the summary's own `is_stale`/`unindexed` signals) — "this team owns 12 repos, 2 haven't been touched in 90+ days." |
| 3 — Component detail | One component's owning team + last-changed-by, from existing PR/commit metadata — no new data model needed, this is a presentation lens over data already collected. |
| 4 — Graph | **Doesn't exist for this lens.** Not every lens needs an escape hatch to the raw graph; forcing one in is exactly the "graph visualization first" instinct this document argues against. |

### Risk — "What's dangerous to touch right now?"

| Level | What the user sees |
|---|---|
| 0 — Goal | None — this lens *is* a dashboard, reached directly, no picker. |
| 1 — Heatmap | A ranked table (not a graph): component, change frequency, blast radius, test coverage, computed risk score — sortable, filterable by domain/team. This is deliberately the audit's own deferred item (§4.3, "Risks as structured objects with severity") given a real home instead of staying a free-text field. |
| 2 — Component detail | Same `NodeDetailPanel` shape, now showing *why* the score is high: the three contributing factors, each with its own evidence (last N changes, blast-radius count, coverage %). |
| 3 — Graph (opt-in) | "View as graph" colors the Architecture lens's own graph by risk score instead of node type — a *filter/palette* on the existing view, not a new visualization. |

### Architecture — "How is this system structured?"

This is Architecture Page V2, unchanged in spirit, **reframed as one lens
among five rather than the only entry point**. The one place open-ended,
undirected exploration is the actual job — a new engineer building a mental
model, an architect checking a subsystem's real shape rather than its
documented one. Landing → domain → repository → lazy graph → node detail
panel → neighborhood, exactly as shipped.

## What changes about what was just built — and what doesn't

Nothing shipped is wasted. Every piece becomes shared infrastructure:

- `ArchitectureBreadcrumbs`' `ArchitectureView` union generalizes to a
  per-lens navigation state (same shape: a level, a scope, an optional
  focused node).
- `RepositoryGraphExplorer`'s neighborhood mode is *already* "seed a subgraph
  from a node and show only what's near it" — Impact and Dependency's own
  opt-in graph views are this exact component with a different seed and an
  edge-type filter, not new code.
- `NodeDetailPanel` is already lens-agnostic — it renders whatever properties
  a node carries; Risk and Ownership just need it to render different
  properties (which the graph/Postgres already have).
- `GET /architecture/summary`'s domain grouping is Ownership's Level 1,
  verbatim.

What's new: a risk-score computation (Risk lens, §4.3's deferred item), a
"reached from a PR" entry point (Impact lens — this is the one piece with no
existing analog, since nothing today surfaces architecture context from
inside a PR view), and the tree-shaped (not graph-shaped) rendering for
Dependency.

## Recommended next step

Build **Impact** next, exactly as already planned — but as a lens with its
own ranked-list-first Level 2, not a graph view with an impact filter bolted
on. The blast-radius subgraph view (Level 4) reuses `RepositoryGraphExplorer`
directly; the ranked-list view (Level 2, the *actual* primary surface) is the
one genuinely new piece of UI this needs.
