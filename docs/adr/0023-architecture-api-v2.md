# ADR 0023: Architecture API v2 — org-scale summary, drill-down, and lazy loading

## Status

Approved — implementation follows this document directly (no separate
design-then-wait gap this time; scoped tightly enough to build in one
pass, unlike ADR 0022's deliberate pause).

## Context — what's real today, verified against the code

The Architecture page (`frontend/src/pages/ArchitecturePage.tsx`) at real
scale (hundreds of tracked repositories) breaks in one specific,
mechanical way, confirmed by reading the actual call sites rather than
assumed from the earlier UX audit's prose:

1. **N+1 on every page load** — `ArchitecturePage.tsx:109-116` fires a
   `useQueries` fan-out, one `GET /repositories/{id}/index` per tracked
   repository, just to get each repo's `result_summary` counts. There is
   **no bulk query anywhere in the codebase** — confirmed by grep across
   `repositories.py`/`github_service.py`. At 500 repos this is 500
   parallel HTTP requests before the page shows anything.
2. **Node-type filter options are derived client-side from a possibly-
   truncated load** — `ArchitecturePage.tsx:192-197`'s own comment already
   flags this as a "Phase-1 limitation": the filter chip list comes from
   `legendLabelsFor(unfilteredGraphQuery.data.nodes)`, so a type that only
   appears after the first 2,000 nodes (the default `limit`) never becomes
   a filter option at all.
3. **No repository grouping exists.** `Repository`
   (`backend/app/models/repository.py`) has no domain/team column —
   confirmed by reading the model in full. The audit's domain-cluster
   mockup (§6.2) has nothing to group by today.
4. **Single-page graph loading, no cursor.** `GET /repositories/{id}/graph`
   (`repositories.py:316`) already has real bounded/filtered querying
   underneath it (`Neo4jGraphRepository._get_full_graph_bounded`,
   `neo4j_repository.py:230-322` — a genuine two-pass, indexed,
   `ORDER BY n.id LIMIT $limit` query, not a naive fetch-everything-then-
   slice) but has no way to request the *next* page — a repository with
   more nodes than `limit` is simply `truncated=True` with no path
   forward.
5. **One capability already exists that Phase A reuses rather than
   rebuilds**: `get_neighborhood` (`neo4j_repository.py:417-527`) —
   seeded, hop-bounded, undirected traversal, already used by impact
   analysis (`investigators.py:1639`, `graph_traversal.py:58`) — is
   exactly the mechanism a "click a node, load its neighbors" lazy-drill-
   down interaction needs. No new traversal logic required, only a new
   thin route exposing what already exists.
6. **The index that makes all of this affordable at scale already
   exists**: `graph_node_repository_id`
   (`neo4j_repository.py:66-69`, `CREATE INDEX ... FOR (n:GraphNode) ON
   (n.repository_id)`) — every aggregate query this ADR proposes groups or
   filters by `repository_id` first, so this ADR adds no new index
   requirement.

Precedent for the summary-endpoint shape: `GET /calibration/summary`
(`calibration.py:91`) and `GET /investigation-intelligence/summary`
(`investigation_intelligence.py:148`) — both admin-scoped, both querying
directly rather than through a service, both explicit about being
read-only aggregation over already-persisted data. This ADR's summary
endpoint is user-scoped (a user's own tracked repositories, not
admin-wide), the one deliberate deviation from that precedent — see
Decision §1.

## Decision

Four additive, independently-shippable pieces. Nothing existing is
removed or changed in place; `GET /repositories/{id}/graph`'s default
(no `after` cursor) behavior is byte-for-byte unchanged.

### 1. `GET /architecture/summary` — kills the N+1, one call

New router, `backend/app/api/v1/routers/architecture.py`, prefix
`/architecture`. User-scoped (this user's own tracked repositories via
the existing `list_tracked_repositories`), not admin-only — unlike the
calibration/investigation-intelligence precedent, this is data a regular
user needs to load their own Architecture page, not an admin dashboard.

```python
class RepositorySummary(BaseModel):
    repository_id: uuid.UUID
    name: str
    full_name: str
    domain: str | None                    # see §2
    indexing_status: str | None           # None = never indexed
    last_indexed_at: datetime | None
    node_count: int
    node_counts_by_label: dict[str, int]  # excludes the base "GraphNode" label
    is_stale: bool                        # last_indexed_at older than 30 days

class DomainSummary(BaseModel):
    domain: str | None                    # None groups every unassigned repo
    repository_count: int
    node_count: int

class ArchitectureSummaryResponse(BaseModel):
    total_repositories: int
    total_nodes: int
    total_cross_repository_edges: int
    repositories: list[RepositorySummary]
    domains: list[DomainSummary]
    unindexed_count: int
    stale_count: int
```

Two queries total, not N:

- **Postgres**: one query for this user's tracked repositories, joined to
  each one's *latest* `IndexingJob` via a correlated subquery
  (`SELECT DISTINCT ON (repository_id) ... ORDER BY repository_id,
  created_at DESC` — Postgres-specific, matching this codebase's existing
  Postgres-only posture, no cross-DB portability concern here since
  nothing else in this codebase is cross-DB portable either) — replaces
  the N `GET /{id}/index` calls in one round trip.
- **Neo4j**: one aggregate Cypher query across every tracked repository's
  nodes at once, grouped by `repository_id` and label:
  ```cypher
  MATCH (n:GraphNode)
  WHERE n.repository_id IN $repository_ids
  UNWIND [l IN labels(n) WHERE l <> 'GraphNode'] AS label
  RETURN n.repository_id AS repository_id, label, count(*) AS count
  ```
  Uses the existing `graph_node_repository_id` index for the `WHERE`
  filter; the `UNWIND` fans out per-node labels server-side rather than
  requiring N label-specific queries. This is the one new query pattern
  in this ADR — verified against `_ALLOWED_LABELS`
  (`neo4j_common.py:22-43`) that "every label except the base one" is
  exactly the existing per-node-type vocabulary, nothing new to allowlist.

`is_stale`/`stale_count`/`unindexed_count` mirror the audit's own mockup
(§6.2: *"⚠ 12 repos not indexed ⚠ 4 graphs stale (>30d)"*) — the 30-day
threshold is a named constant (`_STALE_THRESHOLD_DAYS = 30`), not
buried in a comparison, so it's one place to tune later if 30 days turns
out wrong in practice.

### 2. Repository grouping — `domain` column

`Repository` gains one nullable column:

```python
domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

Migration only — no backfill, no inference. Every existing repository
starts ungrouped (`domain=None`), surfaced in `/architecture/summary` as
the `domain=None` entry in `domains[]` ("Ungrouped" is a frontend label
choice, not a backend concept). Setting it is a new, minimal endpoint:

```python
@router.patch("/{repository_id}", response_model=RepositoryResponse)
async def update_repository(
    repository_id: uuid.UUID, body: RepositoryUpdateRequest, ...
) -> Repository:
    """body: {domain: str | None} — the only mutable field for now.
    Empty string is rejected (400), not silently treated as null — a
    caller clearing the field sends null explicitly."""
```

**Explicitly out of scope**: automatic domain inference (from repo name,
topics, org structure, or an LLM pass) — no algorithm was requested, and
guessing wrong is worse than leaving a repository visibly ungrouped.
Grouping is manual, via this endpoint, until a real inference need is
scoped separately.

### 3. `GET /repositories/{id}/graph/types` — real per-repo type counts

Fixes the client-side-derived-from-truncated-load problem (§Context
item 2) directly: one aggregate query, same shape as the `/architecture/
summary` Neo4j query above but scoped to one repository:

```cypher
MATCH (n:GraphNode {repository_id: $repository_id})
UNWIND [l IN labels(n) WHERE l <> 'GraphNode'] AS label
RETURN label, count(*) AS count
```

```python
class NodeTypeCountsResponse(BaseModel):
    counts: dict[str, int]   # every label present, real total — never truncated
```

This becomes the frontend's source for filter-chip options and per-type
counts, replacing `legendLabelsFor(unfilteredGraphQuery.data.nodes)` —
a frontend change, not committed by this document, but the endpoint this
ADR adds is what makes that frontend fix possible.

### 4. Cursor pagination on `GET /repositories/{id}/graph`

One new optional query param, `after: str | None` — the last node `id`
from the previous page (nodes are already `ORDER BY n.id`, confirmed at
`neo4j_repository.py:246`, so this is a real keyset cursor, not an
`OFFSET` that degrades at scale). `GraphResponse` gains one field:

```python
class GraphResponse(BaseModel):
    ...
    next_cursor: str | None = None   # pass as `after` to fetch the next page;
                                      # None means this was the last page
```

`_get_full_graph_bounded`'s node-selection query
(`neo4j_repository.py:241-252`) gains one conditional clause:

```python
cursor_filter = "AND n.id > $after" if after else ""
# ... WHERE true {label_filter} {cursor_filter} ORDER BY n.id LIMIT $limit
```

`limit=None, node_types=None, after=None` (every existing caller) is
**byte-for-byte unchanged** — this only activates when a caller
explicitly asks for the next page, exactly the same non-breaking
posture `limit`/`node_types` themselves already established when they
were added.

### 5. `GET /repositories/{id}/graph/nodes/{node_id}/neighbors` — lazy expand

Thin route over the already-existing `get_neighborhood`
(`neo4j_repository.py:417-527`) — no new traversal logic:

```python
@router.get(".../neighbors", response_model=GraphResponse)
async def get_node_neighbors(
    repository_id: uuid.UUID, node_id: str,
    hops: int = Query(1, ge=1, le=5),
    edge_types: list[str] | None = Query(None),
    ...
) -> GraphResponse:
    types = edge_types or sorted(_ALLOWED_REL_TYPES - _CROSS_REPO_REL_TYPES)
    payload = await graph_repository.get_neighborhood(
        str(repository_id), [node_id], types, hops
    )
```

Default `edge_types` (every non-cross-repo type) rather than requiring
the caller to enumerate them — the existing callers
(`investigators.py`/`graph_traversal.py`) always pass a purpose-specific
subset because *they* know what they're traversing for; a UI "click to
expand" interaction usually doesn't, so a sensible default matters here
in a way it didn't for those callers.

## Non-goals (explicitly deferred, not forgotten)

- **Full-text node search** (`GET /graph/search?q=`) — the audit's own
  "single most valuable item" (§6.4), but not part of the seven goals
  this ADR was scoped against. Natural next piece once this ships;
  deliberately not bundled in to keep this ADR's surface reviewable.
- **Path-finding** (`GET /graph/paths?from=&to=`) — same reasoning.
- **Canvas/WebGL renderer, domain-clustered force layout, layered views**
  — all frontend rendering work, explicitly "backend first" per this
  ADR's own mandate. `domains[]` in the summary response gives a frontend
  everything it needs to *start* building clustering; the rendering
  itself is out of scope here.
- **Automatic domain inference** — see §2.
- **Cross-repository edges in `/architecture/summary`'s totals** —
  `total_cross_repository_edges` counts them, but per-repository
  attribution of *which* cross-repo edges touch which domain is left for
  whenever domain-clustered rendering is actually built; premature to
  design the exact shape now.

## Consequences

- `ArchitecturePage.tsx`'s 500-request N+1 becomes 2 backend queries
  (1 Postgres, 1 Neo4j) behind 1 HTTP call — a frontend rewiring, not
  committed by this document, but unblocked by it.
- One migration (`domain` column, nullable, no backfill — zero-risk to
  apply).
- No existing endpoint's default behavior changes. Every addition here is
  either a new route or a new optional parameter with a byte-for-byte
  backward-compatible default.
- `get_neighborhood`'s existing 5-hop ceiling
  (`_MAX_NEIGHBORHOOD_HOPS`) and `get_full_graph`'s existing 10,000-node
  ceiling (`_MAX_FULL_GRAPH_LIMIT`) are inherited unchanged by the new
  routes wrapping them — no new unbounded-query risk introduced.
