# ADR 0017: The Investigation Planner — an explicit, deterministic task graph

## Status

Accepted.

## Context

ADR 0016 gave engineering understanding a real lever over investigation:
`capability_priority`, a bare `dict[str, float]` derived from the LLM's own
`information_gain_estimates`/`contradictions`, consulted by
`engine._select` to break ties within a necessity tier. That ADR's own
self-review named the honest gap: it was one implicit number per
capability, with no persistent record of *what* the investigation intended
to learn, in what order, why, or what actually happened when a task
completed. This phase's brief asks for something more legible: "the
planner — not the capability loop — becomes the central controller," an
explicit **Investigation Task Graph**, and a different graph shape per
engineering-problem type (a bug is not investigated the same way as a
migration).

The instruction was explicit, again, that retrieval, the capability
framework, the deterministic engine loop, the evidence package, hop-
bounded traversal, composite scoring, `EngineeringUnderstanding`,
`InvestigationWorkspace`, mid-loop updates, hypothesis/contradiction
tracking, and `capability_priority` itself are the reused baseline — this
phase adds a layer above them, it does not touch their internals.

## Decision

### 1. A new, self-contained module: `reasoning/investigation_planner.py`

Pure, deterministic Python. No LLM call, no I/O, no dependency on
`understanding.py` at runtime (a `TYPE_CHECKING`-only import of
`Contradiction` avoids a circular dependency, since `understanding.py`
imports *this* module to use it). This mirrors ADR 0016's own commitment
that action selection stays reproducible and testable — the planner reads
signals the LLM already produced; it does not ask a model to plan.

### 2. Engineering strategy classification decides the graph's shape

`classify_engineering_strategy(text)` — a deterministic, ordered keyword
match (same discipline as `app.indexer.classification`'s `is_test`
detection, per ADR 0007's no-guessing precedent) over eight categories
(`bug`, `feature`, `migration`, `performance`, `architecture`, `security`,
`refactoring`, `data`), defaulting to `feature`. `seed_tasks(strategy)`
returns a genuinely different initial graph per category — not the same
four tasks reordered. Concretely: a `bug` investigation prioritizes
tracing the actual code path over documentation (`trace_implementation`,
gain 0.9, above `check_documentation`, gain 0.3); a `migration`
investigation prioritizes dependency mapping above everything
(`map_dependencies`, gain 0.95); a `feature` investigation prioritizes
finding what already exists to reuse (`find_reusable_components`, gain
0.85) over reading documentation; an `architecture` investigation is the
one strategy where documentation *outranks* tracing current code (0.85 vs.
implicit), because violating a deliberate past decision is that
strategy's biggest risk. Every seed task's `reason_for_creation` states
this reasoning explicitly, not just its priority number.

### 3. `InvestigationTask` — the explicit node the brief asked for

`task_id`, `purpose`, `required_capability` (one of the four real
capabilities, or `""` — an honest gap, e.g. "validate tests" has no
test-execution capability to run yet, recorded rather than silently
dropped), `dependencies`, `expected_information_gain`, `status`
(`pending`/`in_progress`/`done`/`skipped`/`rejected`),
`confidence`/`confidence_at_creation`/`actual_information_gain`, and
`reason_for_creation`. The confidence pair makes "did this investigation
actually pay off" inspectable after the fact — `actual_information_gain`
is set only on completion, as `assessment.score - confidence_at_creation`,
answering this brief's own self-reflection question ("which investigation
produced the highest value? which produced none?") by reading the graph,
not by re-deriving it.

### 4. `refresh_task_graph` — deterministic lifecycle, contradictions spawn work

A pure function of `(tasks, assessments, contradictions)`. Completes a
pending task once its capability's assessment is `satisfied` (recording
`actual_information_gain`); never reopens a completed one even if a later
assessment somehow scores lower (a completed investigation's value doesn't
retroactively disappear). For every **unresolved** contradiction not
already covered, it spawns a new task — contradictions are first-class
investigation objects that generate new work, not just a number added to
a priority dict (this is the concrete improvement over ADR 0016's coarser
mechanism, which could only ever boost a fixed capability with no visible
task behind it). Idempotent by construction: the spawned task's id is
`resolve_contradiction_<sha256(description)[:10]>`, a pure function of the
contradiction's own text, so refreshing twice with the same unresolved
contradiction never duplicates the task.

### 5. `select_next_task` / `priority_boost_from_tasks` / `plan_priority_boost`

`select_next_task` picks the single highest-expected-gain task among
pending, dependency-satisfied ones — the planner's literal answer to "what
is the most valuable thing I can learn next?" `priority_boost_from_tasks`
derives the same per-capability boost `capability_priority` (ADR 0016)
always produced, but now traceable to a specific, inspectable task rather
than an opaque LLM number. `plan_priority_boost` combines both signals via
per-capability `max` (never sum, so agreement isn't double-counted) — the
value that lands in `state.derived["investigation_priority"]`, exactly
where `engine._select` already reads it. **Zero changes to `engine.py`**
were required for this integration; the wiring is entirely inside
`understanding.py`, which now computes the combined value instead of the
bare `capability_priority(workspace)` it computed before.

### 6. The graph is planned even when synthesis degrades

`_advance_investigation_graph` runs in all three branches of
`synthesize_engineering_understanding` (the empty-ledger short-circuit,
the LLM-success path, and the LLM-failure degrade path) — since it costs
nothing (pure Python), there's no reason to skip planning just because the
LLM call did or didn't happen. This means `investigation_priority` is
always populated from at least the deterministic seed graph, even on a
completely fresh run with zero evidence gathered yet.

### 7. The graph is code-authored state, like `investigation_history`

`InvestigationWorkspace.investigation_graph`/`engineering_strategy` are
carried forward from the previous round and refreshed, never regenerated
by the LLM — the same principle ADR 0016 established for
`investigation_history`: a plan's own structure should not depend on a
model remembering to preserve it correctly across rounds.

## Self-review

The mission's own self-review questions, answered honestly:

- **Does the planner truly own the investigation?** It owns *priority*
  within the existing necessity-tiered selection — exactly as much as ADR
  0016's mechanism did, now made explicit and inspectable. It does not
  own *termination*: `engine.py`'s capability/gap-driven readiness gate is
  unchanged and remains the sole authority on when discovery stops (see
  "What this deliberately does not do" below) — the brief's literal
  wording ("the planner — not the capability loop — decides when
  investigation ends") is not fully realized; only capability
  *prioritization* moved to the planner, not stopping.
- **Can new evidence completely change the plan?** Within the existing
  bound — yes for priority (a newly-satisfied capability retires a task;
  a newly-discovered contradiction spawns one), but the seed graph itself,
  once classified, does not get re-classified mid-run even if later
  evidence suggests the strategy guess was wrong (e.g. a ticket that reads
  as "feature" turns out, once the graph is read, to really be a
  migration). Re-classifying mid-run was considered and deliberately not
  built: it would mean discarding task completion/gain history built
  under the old classification, which conflicts with the "recompute
  understanding, but never regenerate the model" instruction. Flagged
  here as a real, not-yet-solved case rather than silently assumed away.
- **Can low-value investigations be abandoned?** Partially: a task can be
  marked `skipped`/`rejected` in the schema, but nothing in this phase
  actually transitions a task to either status — only `pending -> done`
  is implemented. A genuinely low-expected-gain task that never becomes
  "ready" (blocked on a dependency that never completes) simply never
  gets selected, which is a soft form of abandonment, not an active one.
- **Can contradictions generate new work?** Yes, concretely and
  idempotently (Decision §4) — this is the clearest, most literal
  improvement this phase delivers over ADR 0016.
- **Would this resemble how a Principal Engineer investigates an
  unfamiliar codebase?** More than before, in one specific way: a
  Principal Engineer's investigation plan visibly differs by problem type
  (a bug gets traced, a migration gets mapped) — this phase makes that
  literally true and testable
  (`test_seed_tasks_produce_a_genuinely_different_graph_per_strategy`).
  The honest limit is everything named above: this is a smarter
  *prioritizer* with a legible, inspectable plan behind it, not yet a
  planner that can re-classify its own strategy mid-investigation or
  actively prune tasks it decides aren't worth finishing.

## What this deliberately does not do

- Does not change `engine.py`'s stopping condition. Readiness
  (`BLOCKED`/`PARTIAL`/`READY`) is still computed exclusively from the
  capability/gap system (ADR 0010/0014); the planner's task graph
  influences *which* action runs next, never *whether* the run continues.
  "Investigation graph exhausted" is visible in the graph itself
  (`select_next_task` returning `None`) but is not wired to any stopping
  decision.
- Does not re-classify engineering strategy mid-run — classified once,
  from the first available request/ticket text, and kept for the rest of
  the run (see Self-review).
- Does not implement `skipped`/`rejected` task transitions — the schema
  supports them; nothing produces them yet.
- Does not implement hypothesis merge/split as distinct operations — still
  only status transitions (`supported`/`rejected`/`unknown`), tracked
  across rounds since ADR 0016.
- Does not add a fifth real capability — `required_capability=""` tasks
  (e.g. `validate_tests`) remain honestly unanswerable rather than mapped
  onto the nearest existing capability.

## Files

**New**
- `backend/app/context_pipeline/reasoning/investigation_planner.py` —
  `InvestigationTask`, `classify_engineering_strategy`, `seed_tasks`,
  `refresh_task_graph`, `select_next_task`, `priority_boost_from_tasks`,
  `plan_priority_boost`.
- `backend/tests/unit/ai/test_investigation_planner.py` — 17 tests:
  strategy classification per category and default, per-strategy graph
  distinctness, dependency-aware/gain-ranked task selection, completion
  lifecycle (including "never reopens a done task"), contradiction
  spawning and its idempotency, priority boost derivation and combination.

**Modified**
- `backend/app/context_pipeline/reasoning/understanding.py` —
  `InvestigationWorkspace` gains `investigation_graph`/
  `engineering_strategy`; new `_advance_investigation_graph` helper called
  from all three branches of `synthesize_engineering_understanding`;
  `investigation_priority` now comes from `plan_priority_boost` (workspace
  signal ∪ graph signal) instead of the bare `capability_priority`;
  `_history_entry` narrates the planner's selected next task.
- `backend/tests/unit/ai/test_understanding.py` — two existing assertions
  updated to reflect that `investigation_priority` is now always populated
  (from the seeded task graph) rather than empty on a fresh run.

## Test plan

- 17 new deterministic tests in `test_investigation_planner.py` (listed
  above).
- `test_understanding.py`: full suite re-verified against the new
  integration (14/14 passing, two assertions updated to match the now-
  richer, correct behavior rather than papering over it).
- `test_context_reasoning_engine.py`,
  `test_planning_engineering_understanding_prompt.py`: unchanged, still
  passing — confirms zero changes were needed to `engine.py` or the
  Planning agent for this integration.
- Full backend unit suite: run after this milestone, same pass count
  expected as ADR 0016's baseline (1250 passed, 8 pre-existing unrelated
  errors) plus 17 new tests.

## Migration / performance / rollback

- **Migration**: fully additive. `InvestigationWorkspace`'s two new fields
  default to `[]`/`""`; a persisted workspace from before this ADR
  round-trips unchanged (classified fresh on its first post-upgrade
  synthesis call, same as a brand-new run).
- **Performance**: zero additional LLM calls — the entire planner is pure
  Python, computed inside the same synthesis call ADR 0015/0016 already
  pay for.
- **Rollback**: revert `state.derived["investigation_priority"] =
  plan_priority_boost(capability_priority(workspace), graph)` back to
  `capability_priority(workspace)` alone in `understanding.py`; the
  `investigation_planner` module and the two new workspace fields can be
  left in place inert (nothing else reads them) or deleted together.
