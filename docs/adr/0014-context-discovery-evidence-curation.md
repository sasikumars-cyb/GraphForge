# ADR 0014: Context Discovery evidence curation

## Status

Accepted and implemented. Builds directly on ADR 0013 (test/production
grounding) — this ADR's curation stage reuses that ADR's `is_test`/
`confidence` component classification as one of its scoring signals.

## Context

Two prior evaluations of a real Planning run (Jira NPT-29) found that
Context Discovery behaves like a search engine, not an investigator: it
returned 238 raw components with no tiering, no per-item reasoning, no
confidence, and test/production code mixed together. A full architecture
review (`docs/context-discovery-architecture-review` — see the session
transcript; not a separate file) preceded this implementation and
concluded, contrary to the evaluation's framing, that the reasoning
engine, ledger, and capability system are **not** the problem — they are
a genuinely well-built, evidence-first design already. The real bottleneck
is narrow and specific: `projection.build_result()` did
`graph_components: [dict(f.value) for f in ledger.facts_of("component")]`
— every component fact, zero filtering, zero ranking, zero tiering — and
`TraverseArchitectureGraphTool` fetched every Component node of every
indexed repository unconditionally, even once a "scope" action already
knew which single repository the request was about.

The review also found that the graph already has real `CALLS`/`IMPORTS`/
`INHERITS_FROM`/`CONTAINS` edges (written by `indexer/graph/builder.py`),
but nothing ever read them back for ranking, and that no hop-bounded
traversal primitive existed at all — `get_full_graph` loads an entire
repository's graph with no depth bound, and `hop_budget.py`'s own
docstring admits as much.

## Decision

### 1. Retrieval-breadth fix (no new primitive needed)

`TraverseArchitectureGraphTool.execute()` and `Neo4jGraphTool.execute()`'s
cross-repository-edges loop now accept a `repository_filter` — set by
`GraphInvestigator.run()` whenever a "scope"/"verify" action already
knows the repository in question. A genuine "survey" (nothing known yet)
still traverses every indexed repository, which is the one case that
legitimately needs to.

### 2. A genuinely hop-bounded graph primitive

New `IGraphRepository.get_neighborhood(repository_id, seed_node_ids,
edge_types, max_hops)`, implemented in `Neo4jGraphRepository` as one
native variable-length-path Cypher query (bounded to `[1, 5]` hops,
validated before interpolation — Cypher cannot parameterize a
variable-length range, only a literal integer). Cost scales with the
neighborhood actually reachable from the seeds, never with repository
size — the fix for `get_full_graph` having no depth bound at all.
`GraphHopBudgetRepository` charges this as a single call, same as any
other read, so the per-repository budget model is unchanged, just used
more precisely.

### 3. Curation — the new stage

`app.context_pipeline.reasoning.curation` (pure functions, no I/O,
independently unit-testable):

- `select_anchor_ids(components, enriched_text, primary_repository)` —
  which components in the identified primary repository the ticket text
  itself names, by tokenized relevance (exact-token overlap, plus
  partial credit for a real shared prefix ≥5 characters — see the
  "self-review findings" section below for why and its limits).
- `curate(components, neighborhood_nodes, enriched_text,
  target_repositories)` — one composite score per component (relevance +
  graph-proximity + repository-ownership bonus − a flat test-code
  penalty, reusing ADR 0013's `is_test`/`confidence`), ranked once, then
  sliced into budget-bounded tiers by rank: `must_modify` (≤10, the
  anchors themselves), `architecture_dependency` (≤10, graph-proximate or
  relevance-matched neighbors), `reusable_component` (≤10, non-test,
  reuse-shaped naming), `relevant_test` (≤5, test components protecting
  something already in a production tier). Everything else is excluded,
  with the true total preserved (`excluded_count`) — the same
  honest-total convention `projection._findings` already used for the
  human-facing report.
- `render_evidence_package_text(package)` — the prompt-facing rendering:
  tiered sections, each item with its own reason and confidence, empty
  tiers omitted, an explicit statement when nothing scored at all.

`investigators.curate_evidence(state, session)` orchestrates: picks the
primary repository (ADR 0010's candidate resolution, in ranked order),
selects anchors, fetches the bounded neighborhood for that one repository
via `get_neighborhood`, then calls `curate()`. Runs once, in
`engine.investigate()`, right after the investigation loop exits and
before `_conclude()` — not a competing proposed action, a deterministic
post-processing step over whatever the loop already gathered. Degrades
gracefully on any failure (no primary repository yet, no anchor found, a
failed graph read) — never raises, always produces a valid, if smaller,
`EvidencePackage`.

`context_discovery.manifest.CONTEXT_DISCOVERY_MANIFEST.max_graph_hops`
bumped 6 → 7 to account for this one additional per-run graph read.

### 4. Migration — every consumer, same change

`projection.build_result()` gains `evidence_package` (the curated
structure) alongside the existing `graph_components` (kept, unchanged,
as the complete/uncurated debugging view — never read by an agent's
prompt construction again). Planning, Development, and Testing all now
prefer rendering `evidence_package` for their LLM prompt's graph-context
section, falling back to the old `format_graph_context`/raw-component
rendering only when no evidence_package exists (a stored run predating
this change, or one where curation genuinely found nothing). This was a
deliberate correction versus an earlier, less careful proposal that would
have left `graph_components` and `evidence_package` as two permanently
parallel paths — exactly the bug shape a separate evaluation had already
found once (two rendering paths, a fix landing in only one).
Documentation Planning's existing test/production narrative check
(ADR 0013) is unaffected — it correctly needs the *complete* component
pool for existence-checking, not the curated subset.

### 5. Structured Jira extraction

`JiraInvestigator` now keeps the structured fields `JiraProvider` already
resolved (status, issue_type, priority, labels, description) instead of
discarding everything but the combined `context_text`. A new,
deterministic, heading-based parser (`_extract_ticket_sections`) splits
the description into Problem / Business Goal / Acceptance Criteria /
Constraints / Dependencies whenever the ticket's own text uses one of
those headings (either alone on its own line or with content inline
after a colon) — never an LLM summarization. A ticket with no such
structure yields no sections; the full raw text remains available
unchanged. Exposed as `ContextDiscoveryResult.ticket_summary`.

### 6. Confluence prompt

`gather_confluence_context`'s system prompt (an existing, already-good
LLM-driven discover-then-fetch loop anchored on the Jira issue — no
deterministic rewrite needed or attempted here) now explicitly names the
categories a senior engineer looks for (architecture, design decisions,
known limitations, migration strategy, standards/patterns, operational
constraints) and instructs merging overlapping pages into one coherent
statement rather than listing each page separately, while keeping the
existing precision-over-recall instruction ("stop once you have enough").

### Self-review findings (found and fixed during implementation, not left for later)

- **Word-form matching gap**: exact-token relevance matching missed
  common variants a real ticket uses ("dedup" vs. `ExactDeduplicator`,
  "merge" vs. `SCDType2Merger`'s "merger"). Fixed with prefix-based
  partial credit (≥5 shared characters, half an exact match's weight,
  never overriding an exact match). **Residual, documented limitation**:
  a domain abbreviation with no shared prefix at all ("SCD2" vs.
  `SCDType2Merger` — they diverge at the 4th character) still can't be
  connected by any deterministic string method; closing that needs
  semantic/embedding similarity, an explicit, separate trade-off (adds
  new infra, and introduces genuine non-determinism into a codebase whose
  stated precedent — ADR 0007 — is deterministic, no-guessing extraction)
  that this ADR deliberately does not decide unilaterally. A regression
  test (`test_scd2_domain_abbreviation_alone_is_a_documented_known_limitation`)
  asserts today's actual (limited) behavior specifically so a future
  change to this stays a deliberate decision, not a silent one.
- **A repository-name-in-ticket-text bug**: an early version of the
  relevance scorer let a ticket's own "Repo: etl-core" line inflate the
  relevance score of *every* component in that repository (since almost
  every file path contains the package name as a segment). Fixed by
  stripping the target repository's own name tokens from the ticket text
  used for relevance scoring — repository membership is already scored
  on its own merits via the ownership bonus.
- **A real non-determinism bug**: `_primary_repository`'s candidate
  fallback built its candidate pool from a `set`, whose iteration order
  depends on Python's per-process string hash randomization — meaning
  the chosen "primary repository" could silently differ across process
  restarts whenever no ranking covered any candidate. Fixed to preserve
  insertion order (`dict.fromkeys`, not a `set`). Caught by writing a
  test that inserts the same two candidates in both orders and asserts
  the result tracks insertion order, not hash order — a same-process
  repeated-call check alone would not have caught this, since one
  process has one hash seed for its own lifetime.

### What this deliberately does not do

- **Personalized PageRank for graph proximity.** The review recommended
  this as the principled upgrade over flat BFS-distance scoring (captures
  multi-path connectivity, not just hop count). `_proximity_score` is
  written so this is a drop-in replacement (same `[0, 1]` contract, no
  caller needs to change) — not implemented in this pass; BFS distance
  already ships real value and is fully tested. Flagged as the next,
  isolated, independently-shippable improvement.
- **Frontend rendering of the tiered Evidence Package.** `evidence_package`
  is computed and persisted correctly, but the UI (Context Explorer /
  `BlueprintExplorer`) still shows the older, flatter view. This requires
  new props threaded through multiple React components and real browser
  verification — deliberately not attempted in this pass to avoid an
  unverified frontend change; the backend field is stable and ready for
  it.
- **GitHub duplicate-work search.** `GitHubTool` has no search/list-PRs
  capability today (only fetch-by-number); real duplicate-work detection
  needs that as a separate, prerequisite change.
- **Repository-ranking-at-scale for thousands of indexed repositories.**
  `TraverseArchitectureGraphTool`'s "survey" path (nothing known yet)
  still fetches every indexed repository's components to rank them — a
  real, present-day-independent bottleneck the review identified but
  which is out of scope for this pass (it would need a Cypher-side
  aggregation query, not a Python-side loop).

## Files

**New:**
- `backend/app/context_pipeline/reasoning/curation.py`
- `backend/tests/unit/ai/test_curation.py`
- `backend/tests/unit/ai/test_curate_evidence.py`
- `backend/tests/unit/ai/test_traverse_architecture_graph_scoping.py`
- `backend/tests/unit/ai/test_neo4j_tool_repository_filter.py`
- `backend/tests/unit/ai/test_ticket_section_extraction.py`
- `backend/tests/unit/ai/test_jira_investigator_structured_extraction.py`
- `backend/tests/unit/ai/test_confluence_context_prompt.py`
- `backend/tests/unit/ai/test_graph_context_text_from_evidence_package.py`
- `backend/tests/integration/test_neo4j_get_neighborhood.py`

**Modified:**
- `backend/app/graph/interfaces.py`, `neo4j_repository.py`, `hop_budget.py`
  — `get_neighborhood` primitive.
- `backend/app/agents/planning/tools.py` — `repository_filter` on
  `TraverseArchitectureGraphTool`.
- `backend/app/tools/implementations/neo4j_tool.py` — `repository_filter`
  threaded through, cross-repo-edges loop scoped too.
- `backend/app/context_pipeline/reasoning/investigators.py` —
  `repository_filter` wiring, `_extract_ticket_sections`, structured
  `work_item` fact, `curate_evidence` orchestration.
- `backend/app/context_pipeline/reasoning/engine.py` — calls
  `curate_evidence` once after the loop exits.
- `backend/app/context_pipeline/reasoning/projection.py` —
  `evidence_package`, `ticket_summary` fields.
- `backend/app/agents/context_discovery/manifest.py` — `max_graph_hops`
  6 → 7.
- `backend/app/agents/context_discovery/schemas.py` —
  `ContextDiscoveryResult.evidence_package`, `.ticket_summary`.
- `backend/app/agents/planning/agent.py`, `development/agent.py`,
  `testing/agent.py` — prefer `evidence_package` rendering.
- `backend/app/agents/planning/confluence_context.py` — system prompt.

## Migration impact

No database/Neo4j schema migration. `get_neighborhood` is a new read
method against already-indexed data — no re-index required. Every new
result field (`evidence_package`, `ticket_summary`) defaults to `{}` for
rows persisted before this change; nothing reads them as if they were
always present.

## Performance impact

- Retrieval-breadth fix reduces, not increases, Neo4j reads for any
  "scope"/"verify" action once a repository is known (previously O(every
  indexed repo); now O(1)).
- `get_neighborhood` costs one bounded query per run (only when an
  anchor was found) — strictly cheaper than the `get_full_graph`
  alternative the review rejected for this exact reason.
- `curate()` is O(components) in Python, over data already fetched —
  negligible next to the Neo4j round-trip that produced it.

## Test plan / verification performed

- Full backend unit suite: 1228 passed (up from 1173 at the start of this
  change), zero regressions. The 8 errors present throughout are
  pre-existing, unrelated DB-connectivity issues (Postgres port
  mismatch in this sandbox), confirmed unchanged before/after.
- 7 new integration tests against a real running Neo4j instance for
  `get_neighborhood` (hop-distance correctness, undirected traversal,
  induced-subgraph edges, edge-type filtering, multi-seed minimum
  distance, out-of-range rejection, empty-seed short-circuit).
- Regression tests reusing the exact NPT-29 scenario: `SCDType2Merger`
  ranks into `must_modify`; `TestSCDType2Merger` never appears in any
  production tier regardless of how it scores.
- Not performed: a live end-to-end browser re-run of the NPT-29 workflow
  through the actual UI (the unit/integration coverage above directly
  exercises the same logic paths; a full browser re-run was out of scope
  for this pass given the frontend rendering itself is not yet wired to
  show the new field).

## Rollback plan

Every change is additive or an isolated internal swap:
1. Stop calling `curate_evidence` from `engine.investigate()` — every
   other field's shape is unchanged, `graph_components` never changed at
   all.
2. `get_neighborhood` can be left unused (unreferenced) without removing
   it — no consumer breaks.
3. Each agent's "prefer evidence_package" branch is a plain `if` with a
   fallback to the pre-existing rendering — reverting one agent doesn't
   require reverting the others.
4. `repository_filter` defaults to `None` (survey-everything) everywhere
   it's threaded through — omitting it from a call site fully restores
   prior behavior for that call site alone.

## Success metrics

Component count reaching Planning's prompt for the NPT-29 scenario:
238 (unranked) → ≤25 across all tiers (10 + 10 + 10 + 5 budgets), zero
test-classified components in any production tier, every included item
carrying an explicit reason and confidence, full backend suite green.
