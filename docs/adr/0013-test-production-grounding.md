# ADR 0013: Test-vs-production component grounding

## Status

Accepted and implemented.

## Context

### The problem this addresses

An AI quality evaluation of the Planning Workflow (a real run against Jira
ticket NPT-29, "Duplicate records in SCD2 merge during concurrent writes")
found a severe, systemic correctness bug: the Planning agent's executive
summary, all 6 implementation steps, the "Repository Reuse Blueprint"
diagram (marked `"verified": true`, `"confidence": "high"`), and every
downstream stage (Development, Testing, Documentation Planning) referred
to `TestSCDType2Merger` and `TestExactDeduplicator` — real, indexed pytest
test classes in `tests/unit/test_scd2.py`/`tests/unit/test_dedup.py` — as
if they were the production `SCDType2Merger`/`ExactDeduplicator`
implementations those tests exercise. The production classes were never
named anywhere in the 6-stage output.

This was not caught by `app.agents.verification.verify_claims` because
that check answers a different question than the one that mattered here:
"does this claim exist in this run's own evidence" — a test class is
real, indexed evidence, so a claim naming one passes that check every
time. Nothing asked "is the thing being named actually the production
implementation, or just its test double."

### Root cause, verified against real source

Cross-checked against the actual `sasikumars-cyb/etl-core` GitHub
repository (not assumed): `SCDType2Merger` (`src/etl_core/scd/scd_type2.py`)
and `ExactDeduplicator` (`src/etl_core/dedup/exact_dedup.py`) are both
real, indexed components. `app.agents.planning.tools` already had a
partial mitigation — `_is_test_component`/`_TEST_RELEVANCE_FACTOR = 0.3`,
a ranking discount for test-classified components — but it was a
mathematical no-op whenever `relevance_terms` didn't score a component at
all: `0 (no term overlap) * 0.3 (test discount) == 0`, identical to a
production component that also scored 0, so the two compared equal and
fell back to arbitrary Neo4j traversal order. `format_graph_context` also
skipped sorting entirely when `relevance_terms` was empty. Combined with
`_component_budget` capping a repository to as few as 8 prompt entries,
etl-core's test classes filled the entire budget while its production
classes never reached the LLM at all — it had no way to know the real
ones existed.

### Requirements (redesign brief)

1. Every indexed component exposes `is_test`, `component_type`,
   `language`, `repository`, `path`, `symbol_type`, `confidence`.
2. Planning strongly prefers production components; test
   classes/files/mocks/fixtures are negatively weighted unless the task
   is explicitly about tests.
3. A validation layer rejects components that only exist in tests before
   Planning finalizes `affected_components`.
4. Development, Testing, and Documentation Planning independently verify
   referenced components against the architecture graph — they must not
   blindly inherit Planning's claims.
5. Post-generation verification returns structured warnings (not just
   free text) distinguishing nonexistent files/classes/methods from
   test-used-as-production.
6. Regression tests for: test class confusion, production class
   selection, mixed repositories, duplicated names, similarly named test
   classes.

## Decision

### 1. Classification computed once, at index time, and persisted on the graph node

New module `app.indexer.classification` (pure functions, no I/O):

- `classify_is_test(file_path, name) -> (is_test, confidence)` — path
  convention (`tests?/`, `test_*.py`, `src/test/java/...`,
  `conftest.py`) is the strong signal (confidence 0.9–1.0); name shape
  alone (`TestFoo`, `test_foo`, `FooTest`) with no path corroboration is
  weaker evidence (confidence 0.55) — a production class can legitimately
  be named `TestConnectionPool` without being test code itself.
- `symbol_type_for(labels, class_name)` — `class` / `method` / `function`
  / `controller` / `service` / `feign_client` / `module`, finer-grained
  than the existing graph label (distinguishes a bare function from a
  class's method, which today's `Function` label doesn't).
- `production_sibling_name(test_name)` — `TestSCDType2Merger` ->
  `SCDType2Merger`, `test_exact_dedup` -> `exact_dedup`, pure string
  transform, no graph lookup.

`app.indexer.graph.builder` calls `classify()` when creating every
Component-labeled node (Python `Class`/`Function`, Java
`Controller`/`Service`/`FeignClient`, and the generic Kafka-usage
fallback node), adding `is_test`, `confidence`, `symbol_type`,
`component_type` (the existing coarse label, now also stored explicitly
by that name), and `language` to `properties` — purely additive, no
existing property renamed or removed. `repository`/`path`
(`file_path`) were already present on every node; `component_type` is a
same-value alias of the label already exposed via `TraverseArchitectureGraphTool`'s
`type` field, added for exact-name spec compliance and forward clarity.

Properties flow through generically (`SET n += node.properties` in
`neo4j_repository.py`), so this required zero changes to the Neo4j
repository layer or `TraverseArchitectureGraphTool` — every existing
consumer of `graph_components` gains the new fields automatically.

### 2. Ranking: production components always fill the budget before test components

`app.agents.planning.tools.format_graph_context`'s per-repository
component sort now keys primarily on `_is_test_component(c)` (False
sorts first), with the existing relevance-score discount as the
secondary key — not the other way around. This makes the actual failure
mode structurally impossible: a repository's production components are
always listed before any of its test components are even considered,
regardless of whether term-matching scored either of them. `_is_test_component`
now prefers the persisted `is_test` property (falling back to its prior
regex recomputation only for data indexed before this change, so the fix
takes effect immediately for anything indexed going forward without
requiring a forced re-index).

### 3 & 5. Validation + structured warnings: `app.agents.component_grounding`

New shared module, `check_test_used_as_production(claims, components,
task_text) -> (corrected_claims, warnings)`:

- A claim matching at least one *production* component sharing that
  exact name passes through unchanged (existence-checking stays
  `verify_claims`'s job).
- A claim where *every* indexed match is test-classified, and a
  production sibling (`production_sibling_name`) is indexed, is
  **replaced** with the real name — the "reject the test-only claim"
  requirement, implemented as substitution when an unambiguous answer
  exists rather than a silent drop.
- A claim where every match is test-classified and no production sibling
  is indexed is **rejected** — removed from the corrected list — with a
  warning explaining why.
- `is_task_test_related(task_text)` exempts the whole check when the
  task is genuinely about writing/fixing tests (deliberately narrow, same
  heuristic-with-documented-limits style as
  `app.agents.verification.check_entity_mismatch`).

Warnings are a new `ComponentWarning` type (`claim`, `warning_type`,
`message`, `suggested_replacement`) — a pydantic model in
`app.agents._contract` (the shared contract module every agent schema
already imports from) mirroring a plain dataclass of the same shape in
`component_grounding` (kept dependency-free of pydantic/schemas). Every
agent's result schema gets an additive `component_warnings:
list[ComponentWarning]` field alongside its existing, unchanged
`verification_warnings: list[str]`.

### 4. Independent grounding per stage

- **Planning** calls `check_test_used_as_production` on its own
  `affected_components`, before the existing existence check, correcting
  the list in place. The corrected names are what gets persisted — later
  stages reading Planning's stored result already see the fix.
- **Development** independently re-checks `plan.components` and
  `plan.reusable_implementations` against the *same* `graph_components`
  it already reads from Context Discovery — not a re-read of Planning's
  warnings. This is what "do not blindly inherit Planning" means in
  practice: if Planning's own check had a gap, Development's own call
  still catches it, because it re-derives the answer from the graph
  itself rather than trusting Planning's text.
- **Testing** independently re-checks `affected_components`,
  `regression_tests[].component`, and
  `integration_tests[].source_component`/`target_component` the same
  way, and drops an integration test entirely when either endpoint has no
  real production component (a `CALLS` relationship can't be tested
  against a class that doesn't exist).
- **Documentation Planning** has no `affected_components` field of its
  own (its output names documents, not components) and, by design, runs
  no graph traversal — it now reads Context Discovery's already-fetched
  `graph_components` (no new Neo4j query) for exactly one purpose:
  scanning its own narrative text (`required_updates[].reason`,
  `new_documentation[].purpose`, `release_notes_draft`,
  `recommendations`) for indexed component names and independently
  re-checking each one, rather than only ever carrying forward what
  `prior_verification_warnings` already aggregated from earlier stages.
- **Engineering Review** required no code change: it already aggregates
  every prior stage's `verification_warnings` (free text) via its
  existing `_collect_verification_warnings`, and every
  `component_grounding` message is appended into that same list at each
  stage — so Engineering Review sees every correction/rejection for free,
  through the mechanism already in place.

### What this deliberately does not do

- **Does not touch Engineering Review's context-assembly truncation
  bug** found in the same evaluation (it fabricated two blocking issues
  about its own workflow's history, unrelated to test/production
  confusion) — a distinct, already-documented bug, out of scope here.
- **Does not wire the new `component_warnings` into the Workflow Command
  Center UI.** The evaluation separately found that `verification_warnings`
  is computed correctly but never rendered on the actual Workflow page —
  `BlueprintExplorer.tsx` (which renders that page's "Summary" tab) never
  imports `VerificationWarnings.tsx`, unlike the standalone
  `/workspace/planning` etc. pages, which use `StageResultDetails.tsx`
  and do render it. Closing that gap requires new props threaded through
  `BlueprintExplorer`'s call sites (`WorkflowPage.tsx` and others) — a
  real, separate frontend change, not attempted here to avoid an
  under-verified change on top of an already substantial backend one.
  **Recommended as the immediate next step** — without it, this ADR's
  structured warnings are correctly computed but only visible in the
  Evidence tab's raw text and the JSON tab, same blind spot as before for
  the primary Workflow view.
- **Does not add stemming/synonym expansion** to relevance-term matching
  (e.g. "merge" vs. "merger", "dedup" vs. "deduplicator" don't share
  exact tokens) — a separate, lower-priority ranking-quality gap noted in
  the evaluation, unrelated to test/production confusion specifically.

## Files modified

**New:**
- `backend/app/indexer/classification.py` — is_test/symbol_type/production-sibling classification.
- `backend/app/agents/component_grounding.py` — the shared validation check every agent calls.
- `backend/tests/unit/indexer/test_classification.py`
- `backend/tests/unit/ai/test_component_grounding.py`
- `backend/tests/unit/ai/test_planning_tools_ranking.py`

**Modified:**
- `backend/app/indexer/graph/builder.py` — classification properties on every Component node.
- `backend/app/agents/planning/tools.py` — `_is_test_component` prefers persisted property; component-listing sort guarantees production-first.
- `backend/app/agents/_contract.py` — shared `ComponentWarning` pydantic model.
- `backend/app/agents/planning/agent.py`, `planning/schemas.py`
- `backend/app/agents/development/agent.py`, `development/schemas.py`
- `backend/app/agents/testing/agent.py`, `testing/schemas.py` (also fixes duplicate-warning-string bug: `component_claims` could contain the same name several times across `affected_components`/`regression_tests`/`integration_tests`, producing one identical warning per occurrence — now deduplicated before the existence check)
- `backend/app/agents/documentation_planning/agent.py`, `documentation_planning/schemas.py`
- `backend/tests/unit/indexer/test_graph_builder.py` — classification integration tests appended.

## Migration impact

**No database/Neo4j schema migration.** `IGraphRepository.replace_repository_graph`
already fully replaces a repository's graph on every index run (no
incremental diffing) — the new node properties appear automatically the
next time any repository is (re-)indexed, with zero explicit migration
step required.

**Already-indexed repositories, before their next re-index:** their
graph nodes lack `is_test`/`confidence`/`symbol_type`/`component_type`/
`language`. `_is_test_component` in `planning/tools.py` falls back to its
prior regex recomputation whenever the persisted property is absent, so
ranking correctness is unaffected either way — the only difference is
whether the classification came from the graph or was recomputed
on-the-fly, both give the same answer for the common case. The new
`component_grounding` check reads `component.get("is_test")` and treats
a component with no matches at all (which includes one with a missing
`is_test` key, since `_components_by_name` only groups by name — the
`is_test` value itself, if absent, is falsy via `.get("is_test")`,
i.e. `None`, treated as "not test") as passing through unchanged, which
is the safe default (no false rejection of an unclassified component).
**No forced re-index is required for this fix to take effect** — every
newly-indexed or re-indexed repository gets full classification
immediately; older data degrades gracefully to the prior behavior until
its next re-index.

## Performance impact

- **Indexing:** `classify()` is two regex matches and a handful of string
  comparisons per component — negligible against the cost of parsing and
  graph-writing a repository (already O(components) work).
- **Planning/Development/Testing/Documentation Planning:** `check_test_used_as_production`
  is O(claims + components) — one dict build over `components`
  (already held in memory, already iterated at least once by existing
  verification code) and one dict lookup per claim. No new network I/O,
  no new LLM call, no new Neo4j query (Documentation Planning reads
  Context Discovery's *already-fetched* `graph_components`, not a fresh
  traversal).
- **Ranking:** the component-listing sort was already O(n log n) per
  repository; changing the sort key's priority order doesn't change its
  complexity.

## Rollback plan

Every change here is additive or a same-shape refinement of an existing
function's internals — nothing removes a field, changes a public
function's signature in a breaking way, or requires a data migration to
undo:

1. **To fully disable:** revert the four agent files
   (`planning/agent.py`, `development/agent.py`, `testing/agent.py`,
   `documentation_planning/agent.py`) to stop calling
   `check_test_used_as_production` — `verification_warnings` and every
   other existing field continue to work exactly as before (this was
   never a required call; each agent's own try/except and error policy
   is untouched).
2. **Graph node properties** are harmless to leave in place even after a
   full revert — no consumer that existed before this change reads
   `is_test`/`confidence`/`symbol_type` at all, so their presence is
   inert, not a compatibility risk, and doesn't need to be reverted.
3. **Schema fields** (`component_warnings` on each `*Result` model) default
   to an empty list — removing the call sites above simply leaves them
   permanently empty, no schema migration needed to remove the field
   itself if desired later.
4. **Ranking change** (`format_graph_context`'s sort key) is the one
   behavior change with no feature flag — reverting
   `app/agents/planning/tools.py`'s sort key to its pre-change form is a
   single, isolated diff (see the file's own inline comment explaining
   exactly what changed and why) if it ever needs to be undone
   independently of the rest.

No rollback requires touching the database, Neo4j, or any stored
`AgentStep.result` JSON already persisted from before this change — old
and new result shapes coexist (`component_warnings` simply wasn't in the
schema for rows written before this ADR, and pydantic's default empty
list handles reading them back without error).
