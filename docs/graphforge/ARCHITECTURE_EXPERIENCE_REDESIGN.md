# The Architecture Experience, Redesigned From First Principles

**A design exercise, not a build plan for everything in it.** The Lens
Framework below is design-only; Impact Check (the last section) is what
actually gets built next, and is scoped tightly enough to build for real.

## Product principle (governs every lens below, and everything after it)

**GraphForge is a visualization-first engineering platform.** A user should
understand the state of a system within 5–10 seconds by looking at a visual —
not by reading a paragraph, not by scanning a table first. Text and lists
*support* the visualization (labels, drill-in detail, precise numbers once
the visual has already answered "is this fine or not") — they never replace
it as the primary surface.

This revises the previous draft of this document directly: that draft's
Impact/Dependency/Risk lenses defaulted to a ranked list or tree as the
*primary* surface, with a graph demoted to an opt-in "view as graph" toggle.
That was the wrong call under this principle — a ranked list is something you
read, not something you understand in five seconds. Every lens now leads
with a real, signature visualization; the list/table each lens also has is
support, shown alongside or beneath it, not instead of it.

## Why "progressive disclosure" still matters here

Visualization-first does not mean "one big force-directed graph of
everything, always" — that fails the five-second test even harder than a
list does (a hairball is not understandable at a glance; a hairball is
the *current* Architecture-page-at-scale problem this whole effort exists to
fix). Progressive disclosure is what keeps a visualization actually readable
at 100,000 nodes:

```
Level 0 — Goal        "What are you trying to do?"      Entry point, minimal chrome.
Level 1 — Overview     The lens's signature visualization, pre-aggregated/
                        semantically grouped so it renders a few dozen visual
                        elements, never raw nodes.                     <- the 5-10s moment
Level 2 — Drill-in      Same visualization, expanded/filtered/zoomed into
                        one group — still visual-first.
Level 3 — Detail        Node/edge-level detail panel, evidence, precise
                        numbers — text is appropriate exactly here, once
                        the visual has already established scale/severity.
```

Every level is graph/visual-shaped. What changes level to level is *what the
visualization is showing you* (a pre-aggregated risk treemap at Level 1, an
expanded neighborhood at Level 2), not whether there's a visualization at all.

## Semantic grouping (unchanged from the previous draft, still load-bearing)

Every lens's Level 1 renders a *pre-aggregated* visual, never raw nodes —
grouped by domain/team, architectural layer, or risk tier, the same three
groupings reused across all six lenses below. This is what keeps a treemap
or graph at Level 1 down to a few dozen visual elements instead of 100,000.

## The six lenses (Ownership's grouping added an Engineering Decisions lens per direction)

For each: the signature visualization, the 5–10 second read, the
interactions, and what supporting detail appears once you've already looked.

### Impact — Interactive blast-radius graph with risk overlays

**Visualization**: A radial/concentric graph, not a generic force-directed
one — the changed component(s) at the center, impacted components placed by
*hop distance* (a ring per hop), so distance-from-center is instantly
readable as "how directly is this affected." Node size or fill intensity
overlays the risk score (computed once, read everywhere — see the Risk
lens), so a big, saturated-red node two rings out reads as "further away,
but still dangerous" at a glance, without reading a single number.

**5–10 second read**: "This change has a small, contained blast radius —
four direct dependents, all low-risk (green, small)" vs. "this touches
one thing directly, but that one thing radiates into 40 components, three
of them bright red" — visually distinguishable instantly, which is exactly
the judgment call ("is this change safe to ship") the lens exists to support.

**Interactions**: click a node to re-center the blast radius on it (recursive
drill, not a page navigation); toggle the risk overlay on/off; filter rings
by hop distance (collapse everything past 2 hops); filter by domain; hover a
node for a one-line "why" tooltip; **compare** mode — two blast radii
(e.g. this PR vs. last week's similar change) side by side, same layout,
so relative severity is a visual comparison, not two numbers to subtract.

**Supporting detail** (beside/beneath the graph, synced to selection): a
sortable table — component, hop distance, risk score, owner — that
highlights the graph node when a row is hovered and vice versa. The
`NodeDetailPanel` (already built) opens on click, showing the specific
edge/relationship chain that explains *why* something is impacted.

### Dependency — Expandable dependency graph/tree

**Visualization**: A horizontal layered directed graph (dagre `rankdir:
"LR"`, already the exact layout `DependencyGraph` uses) rooted at the
selected service — visually a tree that can have real graph edges
(shared dependencies converge, not duplicated), collapsed to direct
dependencies only until expanded. Direction (depends-on / depended-on-by)
is a visual flip of the same layout, not a different chart.

**5–10 second read**: "billing-service has a shallow, narrow dependency
tree — 3 direct, all first-party" vs. "this fans out immediately into a
wide, deep tree with an external SDK 4 layers down" — depth and fan-out are
read from the shape, not counted.

**Interactions**: click a node to expand its own children (lazy-loaded,
reusing the cursor-pagination/neighbor-expand mechanism already built);
collapse a subtree; direction toggle; depth cap slider; "trace path"
between two selected nodes, highlighting the connecting edges; search-to-
expand (typing a name auto-expands the path to it).

**Supporting detail**: `NodeDetailPanel` on click; a breadcrumb of the
expand path so far (how you got from the root to wherever you've drilled).

### Ownership — Team ownership map

**Visualization**: A zoomable treemap — area = number of components/repos
a team owns, color = team. Ownership is a partition, not a network, so a
force-directed graph would be the wrong signature visualization here (a
graph implies relationships between the regions, which isn't the point);
a treemap's whole premise — relative size at a glance — is exactly the
question ("who owns how much, and is anything unowned") this lens answers.

**5–10 second read**: "Team Payments' region is visibly the largest single
block; there's a conspicuous grey 'Unowned' block along the bottom that's
bigger than it should be" — proportional area does the work no sentence
needs to.

**Interactions**: click a team's region to zoom in (standard zoomable-
treemap interaction) to that team's own repos as sub-regions; toggle sizing
metric (repo count vs. node count vs. lines of code); recolor by staleness
instead of team (a second lens *on* the same visualization, not a new one);
hover for a tooltip.

**Supporting detail**: beneath the map, the selected region's repo list
with health indicators (`is_stale`/`unindexed`, already computed).

### Risk — Heatmap with hotspots

**Visualization**: A treemap sharing the exact same visual grammar as
Ownership's (area = component size, but now color = risk intensity, a
continuous heat scale rather than categorical team colors) — deliberately
the same chart type as Ownership, different channel mapped to color, so a
user who's learned to read one map instantly knows how to read the other.

**5–10 second read**: "Three small-but-bright-red hotspots stand out
immediately in the checkout flow region; the rest of the map is a calm
blue-green" — severity and concentration are both visible without reading
a single score.

**Interactions**: click a hotspot to see its three contributing factors as
a small breakdown (a compact bar/radar, not a paragraph); adjust factor
weighting live and watch the map recolor (change-frequency-heavy vs.
coverage-heavy view of the same data); filter by domain/team; a time
scrubber to watch hotspots emerge/fade over recent history.

**Supporting detail**: the factor breakdown panel; a "recent changes to
this component" list underneath it.

### Architecture — Layered architecture graph

**Visualization**: What's already shipped (`DependencyGraph` /
`RepositoryGraphExplorer`), extended with real visual layers — horizontal
bands (service / data / code), not a flat mixed-type force layout — the
earlier UX audit's own §6.3 idea, finally given a concrete shape: dagre's
`rankdir` plus a `subgraph`-per-tier grouping (the same clustering box
mechanism `DependencyGraph` already uses for multi-repo grouping, one more
grouping axis).

**5–10 second read**: "Clear three-tier shape, request flow left to right,
no cross-layer shortcuts" vs. "the data layer has direct edges from the
service layer skipping the expected mid-tier" — an architectural smell
that's visible as a shape, not discoverable by reading a list of edges.

**Interactions**: everything already built (lazy load, node click, explore
neighbors, search) plus a layer-tier filter and the risk-overlay toggle
shared with Impact.

**Supporting detail**: `NodeDetailPanel`, legend (already built).

### Engineering Decisions — Decision flow/timeline

Backed by real, existing data — `app/models/decision.py` (`Decision`,
`Recommendation`) already persists this; no new data model, a new lens over
data already collected.

**Visualization**: A horizontal timeline with branching, closer to a git
log graph than a plain list of dates — each decision as a node on the
timeline, connected to the components/domains it touched (a thin line down
into a compressed strip of the Architecture layer beneath it, so a decision
and its blast radius share the same visual space).

**5–10 second read**: "Decision density has spiked in the payments domain
over the last month" — visible as a cluster of nodes on the timeline, not
countable from a list.

**Interactions**: scrub/zoom the timeline; filter by domain/component;
click a decision to see what it touched (lights up in the linked
Architecture strip below); jump from a decision straight into the Impact
lens for the component it affected (a lens-to-lens link, not a dead end).

**Supporting detail**: decision detail card (what/why/who), linked
PRs/commits.

## What changes about what was just built — and what doesn't

Nothing shipped is wasted, and nothing here contradicts it — this document
promotes Architecture Page V2's graph-first mechanics (lazy loading, node
detail panels, neighborhood expansion) to the pattern *every* lens follows,
rather than treating them as one page's own implementation detail:

- `RepositoryGraphExplorer`'s neighborhood mode is already "seed a subgraph
  from a node, expand from there" — Impact's radial view and Dependency's
  tree are this same lazy-expansion mechanism with a different layout and
  seed, not new fetching logic.
- `NodeDetailPanel` is already lens-agnostic.
- `GET /architecture/summary`'s domain grouping is Ownership's Level-1
  partition, verbatim.
- The clustering-box mechanism `DependencyGraph` already uses for multi-repo
  grouping generalizes directly to Architecture's layer bands.

What's new: risk-score computation (shared by Impact's overlay and Risk's
heatmap — computed once), a treemap component (new — no chart library in
the frontend today does this, see Impact Check's own scope note on this),
a radial/concentric layout for blast radius, and the Engineering Decisions
timeline.

---

## Impact Check — what gets built next

Scoped to be genuinely buildable now, not a restatement of the whole lens
above. Two deliberate scope cuts from the full Impact lens description:

- **Real, existing blast-radius computation** (`Neo4jImpactGraphReader`,
  `ImpactAnalysisEngine`, `ImpactAnalysisService` — verified present in the
  codebase, not assumed) is reused as-is for the traversal; this is a
  visualization for data GraphForge can already compute, not a new impact
  algorithm.
- **The risk-score overlay is deferred to the Risk lens's own build.**
  Impact Check ships the radial blast-radius graph and its supporting
  table without a risk-intensity overlay on day one — reusing whatever
  severity signal already exists on an impact hop (if any) rather than
  inventing the full change-frequency × coverage formula now. The overlay
  slot is designed in from the start (a color channel the graph already
  reserves) so adding real risk scoring later is a data change, not a
  visualization rewrite.

Next: read `ImpactAnalysisEngine`/`ImpactAnalysisService`/
`Neo4jImpactGraphReader` in full to confirm exactly what they already
return, then design the radial graph component and its API contract against
that real shape — not against an assumed one.
