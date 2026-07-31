# ADR 0012: Persistence architecture for LLM invocation metadata

## Status

Proposed. Depends on nothing prior; nothing depends on it yet. Blocks the
"persist observability for every agent" item left open by the Weakness #4
implementation (unified metadata collection), which is otherwise complete
and unaffected by this ADR remaining undecided.

## Executive Summary

Every agent now produces an identical, complete invocation-metadata
dictionary (`app.agents.llm.LLM_INVOCATION_METADATA_KEYS`) — provider,
model, token counts, cost, latency, retry count, finish reason, status,
timestamps. Only Planning persists it, into a single JSON field
(`PlanningResult.llm_trace`) holding exactly one invocation's worth of
data. Two facts already visible in the current implementation prove a
single-value field cannot be the long-term home for this data: (1)
Planning's own reflection pass can call the LLM twice per `AgentStep`, and
the current code already had to *sum* the two calls' metrics together to
fit them into one `LLMTrace` — discarding which call actually failed a
retry, which succeeded on the first attempt, and each call's individual
finish reason; (2) five more agents now produce the same shape of data
with nowhere durable to put it at all.

This ADR recommends a **dedicated `llm_invocations` table**, one row per
LLM call (not per agent step, not per run), append-only, written once with
a complete record rather than created-then-updated. It is populated
directly by `app.agents.llm.invoke_llm_json` — the single existing choke
point every agent already calls through — so no agent-specific persistence
code is needed anywhere.

## Current Repository State

Documented from direct inspection, not assumption, with file references.

**1. `AgentStep` schema** (`app/models/agent_step.py`). One row per
`(run_id, agent_id)`. Already carries `latency_ms` and `retry_count`
columns, added with the comment *"Phase 1 — latency + retry count captured
here; token cost and confidence calibration are Phase 2"* — this ADR is
that Phase 2, and the comment already anticipated a single-invocation
assumption baked into the schema that this ADR must not repeat: a step-level
column can hold exactly one value, but a step can already produce more than
one invocation (see reflection, below). `retry_count` was never written by
anything (confirmed by repo-wide grep) — no LLM signal existed to write
until this session's Weakness #4 work made `retry_count` a real,
computed value on `invoke_llm_json`'s metadata output.

**2. `Run` schema** (`app/models/run.py`). One row per orchestrator run.
`steps: relationship(..., cascade="all, delete-orphan")` — deleting a `Run`
deletes its `AgentStep`s. `Run` itself has no cascade declared from
`Workflow`, but a real `DELETE /workflows/{workflow_id}` endpoint exists
(`app/api/v1/routers/workflows.py:714`), as does `DELETE
/agent-runs/{run_id}` (`app/api/v1/routers/agent_runs.py:555`) — deletion is
not hypothetical.

**3. `LLMTrace` model** (`app/agents/planning/schemas.py`). A Pydantic model
embedded in `PlanningResult`, itself stored inside `AgentStep.result` (a JSON
column). Single-valued: one model, one provider, one prompt/response, one
set of token counts. No other agent's schema has an equivalent field.

**4. `AIProviderUsage` model** (`app/models/ai_profile.py`). A
**rolling-aggregate** table, one row per `provider_key`, with cumulative
counters (`requests`, `successes`, `failures`, `total_latency_ms`,
`rate_limit_events`, etc.) and last-N timestamps. Its own docstring states
the design intent explicitly: *"a lightweight 'is this provider healthy and
how much am I using it' dashboard, not a billing ledger."* Consumed by a
real endpoint (`app/api/v1/routers/ai_workspace.py`'s overview, via
`_usage_dto`) that is wired into the AI Workspace UI. **Critical finding:**
its writer, `app.ai.config.usage.record_outcome`, is defined but has **zero
call sites** anywhere in the codebase (confirmed by repo-wide grep). The AI
Workspace's usage panel has been rendering permanently-empty data since this
table was introduced. This is direct evidence for a specific risk this ADR
must design against: **an aggregate-counter table is only as reliable as
every call site remembering to update it, and this codebase has already
demonstrated that assumption fails in practice.**

**5. Existing execution history UI.** `frontend/src/components/workflow/
ExecutionLogPanel.tsx` renders `llmTrace` (provider, model, latency, token
counts, estimated cost, plus the full prompt/response text) — but only ever
receives it for Planning (`StageResultPanel.tsx` wires
`llmTrace={planningResult?.llm_trace}` and nothing else). No other agent's
result type carries an equivalent field, so no other stage's execution log
shows any of this.

**6. Existing reporting endpoints.** Exactly one: the AI Workspace overview
endpoint's per-provider aggregate (`#4`, above). No per-run, per-step, or
per-invocation drill-down endpoint exists. No cost-over-time, no
retry-rate-over-time, no provider-comparison-over-time endpoint exists.

**7. Existing migrations.** 24 versions in `alembic/versions/`, all
additive in the ones inspected (`e7f8a9b0c1d2` added `agent_runs`/
`agent_steps`; `b8e2d40a91c7` added AI profiles/usage). No migration in this
codebase has ever dropped a column or table. This is a real, observed
convention, not a policy asserted for the first time here.

**8. Existing analytics code.** None beyond the `AIProviderUsage` aggregate
described above (which is unpopulated in practice).

**9. Existing retry storage.** `AgentStep.retry_count`: dead until this
session. `app.ai.config.fallback.complete_with_fallback`'s loop computes the
retry count (as its `index` variable) and, prior to this session's Weakness
#4 work, discarded it. It is now threaded into
`StageAwareLLMProvider.last_retry_count` and, from there, into every
agent's `metadata_out["retry_count"]` — computed, available, not yet
persisted anywhere.

**10. Existing cost storage.** None persisted. `app.ai.providers.pricing.
estimate_cost_usd` is computed transiently at write time into
`LLMTrace.estimated_cost_usd` for Planning only, and nowhere else. The
pricing table itself (`_PRICING_PER_1M`, `app/ai/providers/pricing.py`) is a
hardcoded dict of current list prices, with no versioning.

**Closest existing structural precedent:** `IndexingJob`
(`app/models/indexing_job.py`) — one row per indexing attempt, FK to a
parent (`repository_id`), a status string, a JSON summary, `started_at`/
`finished_at` timestamps. This is the only place in the codebase today that
already solves "many timestamped attempt-records belonging to one parent
entity" — the same shape this ADR needs for LLM invocations. It is a
positive precedent for Option C below, not merely an analogy invented for
this document.

## Architecture Options

### Option A — Columns directly on `AgentStep`

Add `provider`, `model`, `total_tokens`, `estimated_cost_usd`, etc. as new
`AgentStep` columns, populating `latency_ms`/`retry_count` (already present)
alongside them.

- **Simplicity:** highest — no new table, no join.
- **Schema growth:** poor. Every new metric (this ADR's own list already
  has ten fields) is a new column on a table whose primary job is holding
  the agent's actual output, not its telemetry.
- **Extensibility:** poor — adding streaming metrics, per-attempt failover
  detail, or any future signal means another migration on a table already
  under active read/write load from every agent.
- **Querying:** fine for "this step's numbers"; impossible for "every
  invocation this workflow made," since a step can already represent more
  than one invocation.
- **Reporting:** cannot represent reflection's two calls, or any future
  multi-call pattern, without either overwriting one call's data with the
  other's or inventing a second set of "_2" columns per metric.
- **Migration cost:** low per-change, but repeats indefinitely.
- **Verdict: rejected.** Fails the stated goal ("without requiring future
  redesign") on the first concrete case already in the codebase
  (reflection).

### Option B — JSON blob on `AgentStep`

Store the same metadata as a JSON column (e.g. `AgentStep.llm_invocations:
list[dict]`), one list entry per call.

- **Flexibility:** good — arbitrary shape, no migration per new field.
- **Indexing:** poor. Postgres JSON querying (`->>`, `jsonb` operators)
  works but every dashboard/analytics query in the stated goals (cost over
  time, provider comparison, retry rate) becomes a JSON-path aggregation
  instead of a normal `GROUP BY`, and cannot be indexed the way a normal
  column can without a functional/expression index per field the UI ends up
  needing.
- **Analytics:** poor for exactly the same reason — SUM/AVG/COUNT across
  rows of a JSON array embedded in unrelated rows is the wrong tool for
  "cost per day across every run," which is a natural `GROUP BY DATE(...)`
  over rows if those rows are real rows.
- **Evolution:** easy to add fields; hard to query them well once added.
- **Compatibility:** additive, like Option A.
- **Verdict: rejected as the primary store**, for the same underlying
  reason as Option A — it optimizes for "attach data to the step that
  produced it" when the actual requirement is "query and aggregate across
  many invocations," which is what dashboards, cost analytics, and
  provider analytics all are by definition. (This is, structurally, exactly
  what `PlanningResult.llm_trace` already does today, single-valued — the
  problem statement itself.)

### Option C — Dedicated `llm_invocations` table

One row per LLM call, FK to `AgentStep.id`.

- **Normalization:** correct shape for one-to-many — a step can have zero,
  one, or many invocations without schema strain.
- **One-to-many support:** direct — reflection's two calls become two rows
  with a shared `agent_step_id`, distinguished by an ordering/purpose field.
- **Retries:** a `retry_count` column per row (the successful attempt) is
  sufficient for the stated goals (see Architectural Questions, below, for
  why per-failed-attempt rows are explicitly out of scope).
- **Multiple invocations:** the table's entire reason for existing.
- **Provider analytics:** trivial — `GROUP BY provider`, `GROUP BY
  provider, DATE(started_at)`, etc., over real rows.
- **Dashboards:** every goal in the brief (execution timeline, latency
  charts, provider comparison, retry visualization, token usage, cost
  reporting, failure analysis) is a straightforward query over this table;
  none require JSON-path gymnastics.
- **Execution history:** a natural `ORDER BY started_at` per run/step.
- **Storage cost:** one row per LLM call. At the stated worst-case scale
  (below), bounded and predictable — this is the dominant, and only
  material, cost of this option.
- **Joins:** one FK hop to `AgentStep`, matching the existing
  `AgentStep -> Run` FK shape already in the codebase.
- **Migrations:** one additive table-creation migration; no existing table
  changes shape.
- **Verdict: recommended.**

### A fourth option surfaced by repository evidence, not invented here

**Option D — Both C and a maintained aggregate, mirroring
`AIProviderUsage`'s stated intent.** The per-row table (Option C) answers
"what happened," but a rolling aggregate answers "what's the current state"
without scanning history — the exact job `AIProviderUsage` was built for
and never populated. This ADR's Performance Analysis section addresses
whether Option C alone is fast enough at scale to make a maintained
aggregate unnecessary, or whether `AIProviderUsage` should finally be wired
up (from the same one choke point, `invoke_llm_json`) alongside the new
table. This is not a fifth new table — it is completing work already
started and already schema-present in the codebase.

## Comparative Analysis

| Criterion | A: Columns | B: JSON blob | C: Dedicated table |
|---|---|---|---|
| One-to-many (reflection) | Cannot represent | Represents, poorly queryable | Represents natively |
| Query/aggregate for dashboards | N/A (single row) | JSON-path aggregation | Native SQL `GROUP BY` |
| Schema growth per new metric | New column, repeats | None | New column, but isolated to a telemetry-only table |
| Migration risk to existing table | Touches hot `AgentStep` table | Touches hot `AgentStep` table | Isolated — zero touch to `AgentStep`/`Run` |
| Matches an existing codebase precedent | No | No (repeats `llm_trace`'s own problem) | Yes — `IndexingJob` |
| Supports future streaming/failover fields | Poorly | Better, unindexed | Cleanly — new nullable columns on an isolated table |

## Recommended Architecture

**Option C: a dedicated, append-only `llm_invocations` table**, written
exactly once per call — after the call completes, whether it succeeded or
failed — from inside `app.agents.llm._fill_invocation_metadata` (the single
function, introduced in the Weakness #4 increment, that already derives
every field this table needs). No agent gains any new persistence
responsibility; the choke point that already exists is where the write
happens.

`AIProviderUsage` should be revived (Option D) as a second, best-effort
write from the same call site — see Migration Strategy — since it already
has schema, already has a UI consumer, and its only defect is the missing
call. This is explicitly **not** part of this ADR's schema design (no new
columns proposed for it); it is a recommendation to finish wiring an
existing, already-approved table, made here because the evidence for it
(`#4` above) surfaced during this investigation.

## Schema Design

```
llm_invocations
----------------
id                    UUID, PK
agent_step_id         UUID, FK -> agent_steps.id, ON DELETE CASCADE, indexed
run_id                UUID, FK -> agent_runs.id, ON DELETE CASCADE, indexed
                       (denormalized from agent_step_id — see rationale below)

purpose               VARCHAR(32) NOT NULL   -- "initial" | "reflection" | future kinds
sequence              SMALLINT NOT NULL      -- 0 for the first call in a step, 1 for the next, ...

provider              VARCHAR(64) NOT NULL DEFAULT ''
model                 VARCHAR(128) NOT NULL DEFAULT ''
stage                 VARCHAR(64) NULL       -- the resolved stage key (app.agents.llm.stage_for)

status                VARCHAR(16) NOT NULL   -- "completed" | "failed"
error                 TEXT NULL

prompt_tokens         INTEGER NULL
completion_tokens     INTEGER NULL
total_tokens          INTEGER NULL
estimated_cost_usd    DOUBLE PRECISION NULL  -- computed and stored at write time, never recomputed
finish_reason         VARCHAR(32) NULL

latency_ms            INTEGER NOT NULL
retry_count           INTEGER NOT NULL DEFAULT 0
attempted_providers    JSON NULL             -- ordered list of provider keys tried before success
                                              -- (future: populate from complete_with_fallback's
                                              -- attempts list; nullable/absent changes nothing today)

started_at             TIMESTAMPTZ NOT NULL
finished_at             TIMESTAMPTZ NOT NULL

created_at             TIMESTAMPTZ NOT NULL DEFAULT now()   -- row-write time, for archival/retention queries
```

**Why `run_id` is denormalized onto the child row** rather than reached only
via `agent_step_id -> agent_steps.run_id`: every stated analytics goal
("cost per run," "provider comparison across a run") filters by run first.
Requiring a join through `agent_steps` for every such query, when
`agent_steps` itself is a wide table carrying full `result`/`evidence` JSON
payloads, is avoidable index-and-join cost for the single most common query
shape. This is the one deliberate denormalization in this design; everything
else follows normal FK discipline.

**Why `purpose`/`sequence` instead of just relying on row order:**
`created_at`/`started_at` ordering is sufficient in principle, but an
explicit `purpose` (`"initial"` vs `"reflection"`) makes "show me only the
call that actually produced the final answer" a `WHERE` clause instead of a
"last row for this step" query, and generalizes cleanly if a future agent
adds a third kind of bounded LLM call (this codebase already has one
precedent for more than two: `app.agents.reflection.run_with_reflection`'s
own docstring caps reflection at "at most once per call," but the module
exists specifically because bounded-retry-of-an-LLM-call is a real, reused
pattern here, not a one-off).

**Immutability.** A row is written once, complete, after the call finishes
— never created in a `pending` state and later updated. This is simpler
than `IndexingJob`'s two-phase (`pending -> running -> completed`) pattern,
and is possible here specifically because an LLM call is a single
synchronous `await`, not a long-running background process with its own
observable intermediate states. Once written, no code should ever update a
row — matching the immutability discipline ADR 0010 already establishes for
`Fact` records (I4) and the `Inference.withdrawn` "supersede, don't mutate"
pattern, applied here to a different subsystem for the same reason: a
historical record of what happened must not be rewritten by whatever
happens next.

## Relationships

- **One `AgentStep` → zero, one, or many `LLMInvocation` rows.** Zero for
  deterministic agents (git_ops) that never call `invoke_llm_json`. One for
  the common case. Two (today's ceiling) for a step where reflection fired.
- **One `Run` → many `LLMInvocation` rows**, via the denormalized `run_id`.
- **`Workflow` has no direct relationship** — reached transitively via
  `Run.workflow_id`, unchanged from today.
- **Deletion cascade:** `ON DELETE CASCADE` from both `agent_step_id` and
  `run_id`, matching the existing `AgentStep.run_id -> agent_runs.id
  CASCADE` convention exactly. **This is a real trade-off, not a default
  accepted without comment:** deleting a `Run` or `Workflow` (both have
  live `DELETE` endpoints today) destroys its cost/analytics history along
  with it. The alternative — decoupling invocation history from
  `Run`/`Step` lifetime entirely (`ON DELETE SET NULL`, keeping orphaned
  invocation rows for pure analytics purposes) — better serves the "cost
  analytics" and "long-term retention" goals but breaks the reasonable
  expectation that deleting a run deletes what it did, and introduces
  orphaned rows with no owning entity to authorize a user's access to them.
  **Recommendation: CASCADE, matching existing convention**, on the
  reasoning that this codebase has never before treated "record we
  generated" as more durable than "the thing that generated it," and
  introducing that asymmetry here — for cost data specifically — should be
  a deliberate, separate decision if the business actually needs
  survives-deletion financial records (see Open Extensions).

## Architectural Questions

**Can one `AgentStep` have multiple LLM invocations?** Yes, today, via
reflection. The schema is designed around this being normal, not
exceptional.

**Should retries be separate rows or attributes?** Attributes
(`retry_count` + optional `attempted_providers`) on the successful
invocation's row, not one row per failed attempt. Rationale: a failed
attempt within `complete_with_fallback`'s loop produced no response, no
tokens, and no cost — there is little a per-attempt row would let a
dashboard show beyond "which providers were tried," which
`attempted_providers` already captures far more cheaply than a full row
per failure. Per-provider failure-rate analytics (a legitimate goal) is
better served by finishing `AIProviderUsage` (Option D), which already
counts failures at the aggregate level without per-event storage cost.

**How should streamed responses be represented?** Not addressed by this
schema, because streaming does not exist anywhere in the current codebase
(`ILLMProvider`/`LLMResponse` are single-shot; confirmed no streaming
interface exists). Flagged as a Future Extension: a nullable
`is_streamed BOOLEAN` and `chunk_count INTEGER` would be additive columns
if streaming is ever introduced — not designed further here, since
designing for a capability with zero current call sites risks guessing
wrong about what it needs.

**Should reflection invocations be separate?** Yes — this is the schema's
primary justification, addressed above.

**Should provider failover be visible?** Yes, at the level of "which
providers were attempted before the one that succeeded"
(`attempted_providers`), populated from data `complete_with_fallback`
already computes (its local `attempts` list) but does not currently expose
beyond the final `ResolvedProvider`. Not designed as a blocking requirement
of this ADR — the column can ship `NULL`-only until that plumbing is added,
since nothing else depends on it.

**Should invocation metadata be immutable?** Yes — see Schema Design.

**How should costs aggregate?** By SQL aggregation over real rows
(`SUM(estimated_cost_usd) ... GROUP BY ...`), not by a maintained running
counter. This is a direct, evidence-based rejection of the counter approach
`AIProviderUsage` took: that approach silently produced zero data for its
entire lifetime because a single call site was never wired up, and nothing
detected the gap. A per-row table's failure mode for a missed call site is
one missing row in an otherwise-populated table — visible, diagnosable, not
a silent total blackout.

**Should historical pricing be preserved?** Yes — `estimated_cost_usd` is
computed once, at write time, from whatever `app.ai.providers.pricing`
reports *then*, and stored as a fact. It is never recomputed at read time.
This matters concretely: if a model's listed price changes (as they do), a
read-time recomputation would silently restate every historical invocation
at today's price, misrepresenting what was actually believed to have been
spent at the time. Storing a `pricing_snapshot_version` alongside the
computed figure (to know *which* price table version produced it) is a
reasonable Future Extension, not required for correctness of the stored
dollar amount itself.

**How should deleted workflows affect invocation history?** Cascade delete,
per Relationships above, with the trade-off stated explicitly rather than
silently accepted.

## Scalability

Stated scenarios, addressed with arithmetic grounded in the schema above
rather than a hand-wave.

- **100 workflows/day.** A "planning" workflow_type run touches
  context_discovery, planning (up to 2 invocations via reflection),
  development, testing, documentation_planning, engineering_review — six
  LLM-touching agent stages, at most 7 invocations per full workflow. 100
  workflows/day × 7 ≈ 700 rows/day. Trivial for Postgres at any reasonable
  hardware tier; no index or partitioning concern.
- **10,000 workflows/day.** ≈ 70,000 rows/day, ≈ 25.5M rows/year. Still
  comfortably within a single unpartitioned Postgres table's normal
  operating envelope (tables with hundreds of millions of rows are routine
  in production Postgres) provided the right indexes exist (below). This is
  the point at which retention policy, not table architecture, becomes the
  operative constraint.
- **Millions of invocations.** Reached well within a year at the 10,000/day
  scenario. Query performance at this scale depends entirely on index
  coverage for the query shapes the stated goals actually need:
  - `(run_id, started_at)` — execution timeline, per-run drill-down.
  - `(agent_step_id)` — already the FK, indexed by convention (matching
    `AgentStep.run_id`'s existing `index=True`).
  - `(provider, started_at)` — provider comparison over time.
  - `(started_at)` alone — global timeline/retention queries, and a natural
    candidate for range partitioning by month if retention policy (below)
    makes that worthwhile.
- **Long-term retention / archival strategy.** No retention or archival
  mechanism exists anywhere in this codebase today (confirmed by grep) —
  this would be new. Not designed in full here (out of this ADR's schema
  scope), but the schema is deliberately retention-friendly: a single
  append-only table with a `created_at`/`started_at` timestamp is the
  simplest possible shape to (a) range-partition by month if row count
  demands it, or (b) periodically export-and-delete rows older than a
  policy window, without touching `AgentStep`/`Run` at all. **This is an
  explicit Open Extension, not a decision made by this ADR** — the business
  question ("keep invocation history forever, or roll off after N months")
  is a product/compliance decision, not an architectural one.
- **Index strategy, summarized:** the four index shapes above, all standard
  B-tree, no exotic indexing required at any stated scale.
- **Query performance:** every stated UI goal (below) is answerable by one
  indexed query against this table; none require a join fan-out beyond the
  single `agent_step_id`/`run_id` FKs already in the schema.

## UI Impact

Every listed goal maps directly to a query against `llm_invocations`, none
requiring new backend computation beyond SQL aggregation:

- **Execution timeline:** `WHERE run_id = ? ORDER BY started_at` — read
  the full invocation-by-invocation history of a run, including both of
  reflection's calls distinctly (impossible with today's summed
  `LLMTrace`).
- **Latency charts:** `latency_ms` per row, bucketed by `started_at`.
- **Provider comparisons:** `GROUP BY provider` over `latency_ms`,
  `estimated_cost_usd`, `status = 'failed'` counts.
- **Retry visualization:** `WHERE retry_count > 0`, or a histogram of
  `retry_count` values.
- **Token usage:** direct columns, summable per run/day/provider.
- **Cost reporting:** `SUM(estimated_cost_usd)` grouped by any dimension
  above — the concrete capability this whole ADR exists to unlock.
- **Failure analysis:** `WHERE status = 'failed'`, `error` text, grouped by
  `provider`/`stage`.

None of this requires new frontend types beyond what
`LLM_INVOCATION_METADATA_KEYS` already defines — the same field names
already flow from `invoke_llm_json` today; this ADR only adds where they're
stored.

## Migration Strategy

One additive migration: `CREATE TABLE llm_invocations (...)`, with the four
indexes above. No existing table's schema changes. Consistent with every
migration this codebase has shipped to date (`#7`, Current Repository
State).

**Population point:** `app.agents.llm._fill_invocation_metadata` gains one
new responsibility — after filling `metadata_out`, persist a row (this ADR
does not specify the exact call signature; that is implementation, not
architecture, and is explicitly out of scope per the request that produced
this ADR). Every one of the 6 agents already routes through this function;
no agent-specific migration work is needed.

**`AIProviderUsage` revival (Option D):** wiring `record_outcome` into the
same call site is a one-line addition once this ADR's table exists, since
both would be written from the same place with data already in hand. Noted
as recommended follow-through, not part of this ADR's required scope.

## Performance Analysis

Per-call overhead: one `INSERT` into a narrow, index-light table (4
indexes, ~15 scalar columns, no JSON payload beyond the small optional
`attempted_providers` array) — negligible relative to the LLM call itself,
which dominates latency by orders of magnitude (hundreds of milliseconds to
seconds vs. a single-digit-millisecond insert). No read amplification: the
write path never queries this table, only inserts into it. This mirrors
`IndexingJob`'s existing write pattern, already proven at this codebase's
production scale.

## Operational Impact

- A new table to monitor for size/growth, addressed by the retention
  strategy left as an Open Extension.
- No change to any existing table's write pattern, lock behavior, or
  migration risk.
- The AI Workspace's usage panel, if `AIProviderUsage` is revived alongside
  this table, goes from permanently empty to populated — a visible,
  positive operational change with no migration of its own required (the
  table already exists).

## Risks

- **Storage growth is unbounded without a retention policy.** Explicitly
  flagged, not designed away — see Scalability.
- **Cascade-delete removes cost history when a run/workflow is deleted.**
  Explicitly flagged as a trade-off in Relationships, not hidden.
- **A second unwired table.** The single most concrete risk this ADR must
  actively guard against is repeating `AIProviderUsage`'s exact failure
  mode: a well-designed table nobody writes to. Mitigation, by design: this
  table is populated from `_fill_invocation_metadata`, the one function
  every one of the 6 agents already calls through today (verified, not
  assumed) — there is no per-agent opt-in step to forget, unlike
  `record_outcome`, which required every call site that wanted usage
  tracking to remember to call it separately.

## Alternatives Rejected

Options A and B, with reasoning, are in Architecture Options above. One
additional alternative considered and rejected: **extending `AgentStep`'s
existing `evidence` JSON list to carry invocation records** (the same
option ADR 0011 rejected for pre-flight warnings, for the identical
reason) — `evidence` is the agent's own audit trail of what *it* observed;
injecting orchestrator/telemetry-produced rows into it conflates two
different provenances in a field the UI already renders as "the agent's
own evidence." Consistency with that prior decision, not just this ADR's
own reasoning, argues against it.

## Future Extensions

Enabled without redesign, per the stated goal, because each is an additive
column or a new query over the existing table, never a schema change to
`AgentStep`/`Run` or a new join shape:

- Streaming metadata (`is_streamed`, `chunk_count`).
- Per-attempt failover detail beyond `attempted_providers`, if provider
  analytics ever needs finer grain than "which providers were tried."
- `pricing_snapshot_version` for full historical-pricing auditability.
- Range partitioning by `started_at` month, if retention policy or row
  count ever makes it worthwhile — the schema's single timestamp-ordered
  append-only shape is exactly what partitioning by range requires, decided
  later without touching the table's logical schema.
- A dedicated cost/analytics API surface (`GET /analytics/llm-usage?...`),
  entirely additive, built once this table has real data.

## ADR Decision

**Adopt Option C: a dedicated, append-only `llm_invocations` table**,
populated from `app.agents.llm._fill_invocation_metadata`, with `agent_step_id`
and (denormalized) `run_id` foreign keys, `ON DELETE CASCADE` from both,
one row per LLM call including each of reflection's calls separately.
`AIProviderUsage` should be revived from the same call site as a follow-on,
not a prerequisite.

**Not decided here, and requiring separate, explicit sign-off before
implementation:**

- **OD-1 — Retention policy.** How long invocation rows are kept, and
  whether/how they're archived. A product/compliance decision, not an
  architectural one.
- **OD-2 — Cascade vs. decouple on delete.** Whether deleted-run cost data
  should be destroyed (this ADR's default) or preserved for durable
  financial reporting, which would require decoupling invocation rows from
  `Run`/`Step` lifetime and introducing an access-control question for
  orphaned rows.
- **OD-3 — Whether to build the analytics API surface now or defer it.**
  This ADR makes the queries possible; it does not mandate when a
  `/analytics/...` endpoint gets built.

Implementation of this table is out of scope for this ADR, per the request
that produced it, and must not begin until OD-1 and OD-2 are resolved —
both affect the schema's `ON DELETE` behavior and are not safe to default
silently during implementation.
