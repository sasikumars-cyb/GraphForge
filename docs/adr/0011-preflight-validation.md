# ADR 0011: Pre-flight validation for agent stages

## Status

Accepted. Supersedes no prior ADR. **OD-1** and **OD-3** were resolved on
2026-07-31 (see Open Decisions) — WARNING-severity implementation is no
longer blocked. **OD-2**, **OD-4**, and **OD-5** remain open but are
explicitly non-blocking.

## Context

### The problem this addresses

`RunCoordinator.execute_run()` (and its resume twin, `resume_step()`) is the
single dispatch point through which every agent stage is invoked. Until
recently it set `run.status = "running"`, flushed, and called `agent.run()`
inside a bare `try/except` with no prior verification that the infrastructure
that agent needs is actually available. A missing LLM API key, or an
unreachable Neo4j, surfaced only *after* the stage had started — from the
user's perspective, a stage that appeared to begin normally and then failed
with an internal-sounding error, sometimes after a paid LLM call had already
been made.

The stated product requirement (redesign brief, Part 3) is: *"Planning must
never start if required infrastructure is unavailable. Do not let the user
discover infrastructure failures only after the stage begins."* It further
requires separating **blocking failures** from **warnings**.

### Current pre-flight architecture

`app/orchestrator/preflight.py` exists and implements two checks, both
blocking:

- `check_llm_provider_configured(agent_id, workflow_stage)` — resolves the
  provider that *would* serve this stage via `app.ai.config.resolver.resolve()`
  (pure, synchronous, no network I/O — it reads only the local configuration
  snapshot) and fails when `spec.requires_api_key` is true but no API key is
  configured. Correctly treats credential-free providers (Bedrock's IAM-role
  auth) as configured. The stage key it resolves under mirrors
  `app.agents.llm.stage_for`'s precedence exactly (real `Run.workflow_stage`
  first, then the agent's own default via `default_stage_for_agent`), so the
  check asks the identical question the agent's own LLM call will ask.

- `check_neo4j_reachable(max_graph_hops)` — returns immediately when
  `max_graph_hops <= 0` (the existing `AgentManifest` field that already
  declares whether an agent touches the graph at all, enforced at call time by
  `app.graph.hop_budget`), otherwise performs a real connectivity probe via
  `ToolRegistry.check_health("neo4j_graph")` → `Neo4jGraphTool.health_check()`
  (a live `RETURN 1`), reusing the same health path the Tools admin UI uses.

Both are invoked from `RunCoordinator` **inside** the same `try/except` that
wraps `agent.run()`. That placement is load-bearing and non-obvious: an
earlier version placed the call *before* the try block, which left
`resolve()`'s own exception paths (`require_provider_spec()` raises
`UnsupportedProviderError` for a stale or invalid stored provider key)
unguarded. The exception escaped `RunCoordinator` entirely, bypassing the
`_fail_step`/`_fail_run` persistence, and — because
`background_execution.py`'s wrapper explicitly assumes *"execute_run already
persisted status='failed' and committed on any failure path before
re-raising"* — nothing ever committed. The run silently reverted to
`"queued"` (or `"awaiting_input"` on the resume path) with **no error message
at all**, recoverable only by a server restart, which then attributed it to a
misleading "Interrupted by server restart". This was reproduced live against
real Postgres through the real production wrapper and is now covered by
permanent regression tests. **Any future check must be invoked from inside
that same try block, for the same reason.**

### Existing graceful-degradation patterns

The remaining dependencies — GitHub, Jira, Confluence — are *not* shaped like
the LLM and Neo4j dependencies. Every one of them already has a deliberate,
hand-written "continue without it" path:

- `GitHubInvestigator.run()` records `evidence("unavailable", ...)` and returns
  `yielded=False` when no GitHub account is connected, and
  `evidence("not_found", ...)` when the fetch fails. It never raises.
- `JiraInvestigator.run()` records `evidence("unavailable", "...not found, or
  Jira isn't connected")` and returns `yielded=False`. The `work_item`
  capability simply stays unsatisfied and surfaces as an ordinary readiness gap.
- `gather_confluence_context()` returns `(None, [])` on misconfiguration,
  absent tool-calling support, or LLM failure. Its own docstring states the
  policy explicitly: *"this is optional grounding, not a required step (same
  policy as Jira/GitHub enrichment in planning/agent.py), so it never raises."*

These are not accidents of implementation; they are the intended product
behaviour. A blocking pre-flight check on any of them would convert a
deliberate graceful degradation into a hard failure — a regression, not an
improvement.

### Why further implementation paused

Implementing the remaining checks requires a capability the current
architecture does not have: a **non-blocking outcome**. `preflight.py`'s check
signature is `str | None`, where any non-`None` value is, by construction, a
blocking failure. There is no channel for "Jira is unreachable, this may or
may not matter for this particular request." Adding one requires choosing
where such a warning is persisted and how it reaches the user — a user-visible
contract decision, not an implementation detail. Work stopped rather than make
that choice unilaterally.

A second, narrower question also emerged: the sole genuinely-mandatory
remaining dependency (GitHub for the `git_ops` execution agents) is mandatory
for reasons entirely unrelated to the Context Discovery GitHub path, and
whether pre-flight's remit extends to *deterministic* (non-LLM) agents has
never been decided.

## Decision

Pre-flight validation recognises exactly four outcomes for a dependency.

**1. BLOCKING** — the stage cannot possibly succeed without this dependency,
and that fact is knowable before the stage begins. The run and step are
persisted as `failed` with an actionable message, and `agent.run()` is never
invoked. Reserved for dependencies that are *unconditionally* required by the
dispatched agent, where "unconditionally" means determinable from static
inputs (agent identity, manifest, run row) — never from the content of the
user's request.

**2. WARNING** — the dependency is genuinely unavailable, but the stage may
still succeed, because the code path that uses it degrades gracefully by
design. Execution proceeds. The warning is persisted and surfaced to the user
so a degraded result is explainable rather than mysterious. This severity does
not exist yet; introducing it is the substance of this ADR.

**3. SKIPPED** — a check exists for this dependency but does not apply to this
particular run, determined statically. Example: `check_neo4j_reachable`
returns immediately for an agent whose `max_graph_hops` is `0`. Skipping is
not a silent absence of verification; it is a positive determination that the
dependency is irrelevant to this stage.

**4. NOT APPLICABLE** — no check exists or can meaningfully exist, for a
documented structural reason. Example: PostgreSQL (below). This classification
must always carry its justification; "we didn't get to it" is not a NOT
APPLICABLE, it is an open item.

**Non-negotiable rule.** A dependency may only be classified BLOCKING when its
necessity is determinable from static inputs. Any dependency whose usage
depends on the *content* of the user's request is, at most, a WARNING. This
rule exists specifically to prevent false-positive blocking — e.g. refusing to
start a stage because Jira is unreachable when the request never mentioned
Jira.

## Dependency classification

Evidence for each classification is drawn from the implementation as it exists
at the time of writing; file/line references are given so a reviewer can
verify rather than trust.

### LLM provider

- **Required?** Yes — every LLM-backed agent. `default_stage_for_agent` returns
  `None` only for the deterministic `git_ops` agents, which make no LLM call.
- **Conditional?** No. Determinable from `agent_id` + `Run.workflow_stage`.
- **Existing graceful degradation?** None. A missing provider is a hard failure
  wherever it surfaces.
- **Existing validation?** `resolve()` itself, but it does not raise on a
  missing key — it returns `api_key=None` and defers the failure into the
  provider call inside `agent.run()`.
- **Pre-flight behaviour: BLOCKING.** Implemented.

### Neo4j

- **Required?** Yes, when the agent reads the graph.
- **Conditional?** Yes, but *statically* — via `AgentManifest.max_graph_hops`,
  which is `0` for `code_generation`, `git_ops`, `engineering_review`, and
  `documentation_planning`. This satisfies the non-negotiable rule: the
  condition is a manifest field, not request content.
- **Existing graceful degradation?** None for graph-reading agents; the graph
  is their ground truth.
- **Existing validation?** `Neo4jGraphTool.health_check()`, surfaced through
  the Tools admin UI.
- **Pre-flight behaviour: BLOCKING when `max_graph_hops > 0`, SKIPPED
  otherwise.** Implemented.

### PostgreSQL

- **Required?** Yes, absolutely.
- **Conditional?** No.
- **Existing graceful degradation?** None; nothing works without it.
- **Existing validation?** Implicit and total. By the time either
  `execute_run()` or `resume_step()` reaches pre-flight, a `Run` row (and, in
  `execute_run`, an `AgentStep` row) has already been flushed to Postgres in
  the same session. Reaching the pre-flight call site is itself proof that
  Postgres is reachable.
- **Pre-flight behaviour: NOT APPLICABLE.** A check here could never fire: if
  Postgres were down, the earlier flush would already have raised. Adding one
  would be dead code that implies a guarantee it does not provide.

### GitHub — Context Discovery enrichment path

- **Where:** `GitHubInvestigator` (`app/context_pipeline/reasoning/
  investigators.py`), a member of `default_investigators()`, so it participates
  in every Context Discovery run. It constructs `GitHubProvider(ToolExecutor(
  registry=get_tool_registry()), GitHubTool({...}))` directly, with a per-user
  OAuth token obtained via `get_decrypted_access_token(session.db,
  session.user_id)`.
- **Required?** No.
- **Conditional?** Yes, on request content. `propose()` emits an action only
  when a `reference` fact of type `github_pull_request` or `github_issue`
  exists — i.e. only when the request text names a PR or issue. Most requests
  never trigger it.
- **Existing graceful degradation?** Yes, explicit (see Context above).
- **Existing validation?** None specific to this path.
- **Pre-flight behaviour: WARNING at most.** Blocking is prohibited by the
  non-negotiable rule: whether a GitHub reference exists is discovered *during*
  the run, by `RequestParseInvestigator`, and cannot be known beforehand.

> **Correction of record.** An earlier implementation report asserted that
> "zero agents call the GitHub tool via ToolRegistry." That statement was
> false, and is retracted here. It resulted from a grep for `get_tool("github")`
> which missed the direct-construction pattern above. The *conclusion* (no
> blocking check on this path) is unchanged, but the reasoning in that report
> must not be relied upon.

### GitHub — `git_ops` execution path

- **Where:** `create_branch_agent`, `commit_changes_agent`,
  `create_pull_request_agent`, `run_tests_agent`. These do **not** use
  `ToolRegistry` at all — they obtain a token via `get_decrypted_access_token`
  and reach GitHub through `create_git_write_provider()`
  (`app/integrations/factory.py`), the seam a future GitLab/Bitbucket adapter
  would plug into.
- **Required?** **Yes**, for `auto_execution` workflow stages.
  `create_branch_agent.py` raises `CreateBranchExecutionError("No GitHub
  connection found. Connect GitHub before running execution workflows.")` when
  the token is absent.
- **Conditional?** Statically, on `agent_id` — satisfies the non-negotiable
  rule.
- **Existing graceful degradation?** None, by design; these agents write to a
  real repository.
- **Existing validation?** The agent's own guard, which already fails fast,
  cleanly, and without burning an LLM call.
- **Pre-flight behaviour: BLOCKING is architecturally permissible.** Whether it
  is *worthwhile* is **OD-2** below: the existing failure is already immediate
  and well-messaged, so the marginal benefit is modest.

### Jira

- **Where:** `JiraInvestigator` via `JiraProvider(ToolExecutor(registry=
  get_tool_registry()))`.
- **Required?** No.
- **Conditional?** Yes, on request content — `propose()` emits actions only for
  a `reference` fact of type `jira_issue`, or a human-claimed `work_item` gap.
- **Existing graceful degradation?** Yes, explicit.
- **Existing validation?** `POST /knowledge/connections/{id}/health`
  (`app/api/v1/routers/knowledge.py`) — a real, user-driven "test connection"
  using the connection's own credentials.
- **Pre-flight behaviour: WARNING at most.**

### Confluence

- **Where:** `ConfluenceInvestigator` (Context Discovery) and
  `gather_confluence_context()` (Planning).
- **Required?** No.
- **Conditional?** Yes, and more strongly gated than the others:
  `ConfluenceInvestigator.propose()` returns `[]` unless the documentation
  assessment is both applicable and unsatisfied.
- **Existing graceful degradation?** Yes, explicit and documented as policy.
- **Existing validation?** Same `/knowledge/connections/{id}/health` endpoint.
- **Pre-flight behaviour: WARNING at most.**

### Summary

| Dependency | Required | Conditional | Graceful degradation | Existing validation | Pre-flight behaviour |
|---|---|---|---|---|---|
| LLM provider | Yes | No | No | `resolve()` (does not raise) | **BLOCKING** (implemented) |
| Neo4j | Yes, if graph-reading | Statically, by manifest | No | `Neo4jGraphTool.health_check()` | **BLOCKING** / **SKIPPED** (implemented) |
| PostgreSQL | Yes | No | No | Implicit — row already flushed | **NOT APPLICABLE** |
| GitHub (Context Discovery) | No | By request content | Yes | — | **WARNING** at most |
| GitHub (`git_ops`) | Yes | Statically, by `agent_id` | No | Agent's own fast guard | **BLOCKING** permissible (OD-2) |
| Jira | No | By request content | Yes | Connection health endpoint | **WARNING** at most |
| Confluence | No | By request content | Yes | Connection health endpoint | **WARNING** at most |

## Framework

The framework extends the existing module. It does not replace the two
implemented checks, and it does not alter their semantics.

**Result model.** A check returns either `None` (passed, or not applicable to
this run) or a result carrying: a `severity` of `BLOCKING` or `WARNING`, the
`dependency` name, and a human-readable, actionable `message`. Messages name
the remedy ("Configure a provider in AI Settings"), not just the symptom.

**Check interface.** Each check declares:
- a stable `name`, used in logs and persisted warnings;
- `applies_to(...)` — a *static* predicate over the dispatched agent's
  identity, its manifest, and the run row. It must not consult request
  content. This is where SKIPPED is expressed;
- `run(...)` — performs the verification and returns a result or `None`.

**Registry.** Checks live in a module-level, declarative tuple, following the
pattern this codebase already uses in three places: `CROSS_REPO_LINK_RULES`
(`app/indexer/graph/cross_repo_linker.py`), `LEDGER_RESYNC_HOOKS`
(`app/context_pipeline/reasoning/capabilities.py`), and the `ProviderSpec`
registry (`app/ai/providers/registry.py`). Adding a dependency check is one
tuple entry. **`RunCoordinator` is never modified to add a check.**

**Severity model.** Exactly two severities. There is deliberately no third
("info", "degraded") — every additional level demands a rendering decision and
a user-comprehension cost, and no evidence in this investigation justified one.

**`RunCoordinator` responsibilities.** Precisely three, all orchestration:
1. Invoke the registry, from **inside** the existing `try` block that wraps
   `agent.run()` (see Context — this placement is a correctness requirement,
   not a style preference).
2. If any result is `BLOCKING`, raise `PreFlightCheckFailed`, which the
   existing `except Exception` handler already converts into the correct
   `_fail_step`/`_fail_run`/`_commit_with_hook` sequence. No new failure path
   is introduced.
3. Otherwise, persist any `WARNING` results (mechanism per OD-1) and proceed.

`RunCoordinator` must contain no dependency-specific logic — no mention of
Jira, GitHub, Neo4j, or any provider name. It knows only about severities.
Both `execute_run` and `resume_step` apply the framework identically; a resume
is still the start of LLM work and is gated the same way.

## Warning persistence

Warnings must reach the user, or the WARNING severity is pointless — a warning
nobody sees is equivalent to no check. `AgentStep` has no field for
orchestrator-produced, pre-execution warnings. Three options were considered.

**Option A — a new `AgentStep.preflight_warnings` JSON column.**
Requires an Alembic migration. Cleanly separates orchestrator-produced
warnings from agent-produced content; queryable ("how often is Jira down at
stage start?"); nullable and additive, so every existing row remains valid and
every existing reader is unaffected.

**Option B — append to the existing `AgentStep.evidence` JSON list.**
No migration. Rejected: `evidence` is the agent's own audit trail of what *it*
observed, consumed by the UI as such. Injecting orchestrator-produced entries
conflates two different provenances in one field, and every existing consumer
of `evidence` would silently begin rendering items no agent produced. The
absence of a migration is not worth the loss of a clean provenance boundary.

**Option C — log only.**
No schema change, no UI change. Rejected: it fails the originating product
requirement outright. An operator reading logs is not the user staring at a
degraded result wondering why documentation context is missing.

**Decision: Option A.** It is the only option that satisfies the requirement
(user-visible) while preserving the existing meaning of `evidence`. Its
cost — one additive, nullable column — is the smallest schema change that
can carry a user-visible contract, and it matches the additive-migration
convention this codebase already follows (the same shape ADR 0012 chose for
LLM invocations: a dedicated record type rather than overloading an
existing field with a second provenance). Decided 2026-07-31; see OD-1.

**Warning record schema (refinement, 2026-07-31 — clarifies OD-1, does not
reopen it).** Each entry in `preflight_warnings` is:

```
{code, dependency, message, checked_at}
```

This adds one field, `code`, to what was originally specified
(`{dependency, message, checked_at}`). It is not a new decision: the
**Check interface** section above already commits every check to declaring
"a stable `name`, used in logs and persisted warnings" — `code` is that
existing `name` field, finally carried through into the persisted record it
was always specified to appear in. Omitting it from the original shape was a
gap between the Framework section and the Warning persistence section, not a
deliberate choice; this closes that gap.

- `code` — the check's stable `name` (e.g. `"jira_reachable"`), assigned once
  per check at registry-authoring time, never derived from `message`.
- `dependency` — the human-facing dependency name (e.g. `"Jira"`), unchanged
  from the original shape.
- `message` — unchanged: human-readable and actionable, may be reworded over
  time without breaking anything that keys off `code`.
- `checked_at` — unchanged.

A `severity` field was considered and rejected: every entry in
`preflight_warnings` is, by construction, a WARNING-severity result — a
BLOCKING result never reaches this list at all, it raises `PreFlightCheckFailed`
and aborts the step instead (see Framework, responsibility 2). A field whose
value is constant for every row it will ever hold is not a real distinction;
it would just be dead weight repeating what the ADR's own severity model
(exactly two severities, no third level) already guarantees structurally.
Adding it would also cut against that same severity model by implying a
sub-severity taxonomy is coming, which this ADR explicitly does not adopt.

**Why `code` and not just a richer `dependency`:** a single dependency can
fail in more than one distinguishable way (e.g. `dependency: "jira"` could be
unreachable, or reachable but returning an auth error) — collapsing both into
one `dependency` string loses that distinction. `code` is deliberately
one-per-*check*, not one-per-*dependency*, which is exactly the granularity
`applies_to`/`run` already operate at.

**How this improves each use case:**
- **UI rendering** — the frontend can map `code → {icon, color, i18n key}`
  without parsing or pattern-matching `message`, which is free text and not a
  contract.
- **Filtering/analytics** — "how often does `jira_reachable` fire" is a
  `GROUP BY code` query; today it would require an unreliable `LIKE` on
  `message`, which breaks the moment message wording changes.
- **Localization** — `code` is the stable key a translation table keys off
  of; `message` stays the English (or already-localized-at-write-time)
  fallback. Without `code`, localizing warnings would require parsing
  English prose to recover which check fired.
- **Future extensibility** — a new check register with a new `name` is
  automatically a new `code`, with no schema change and no coordination with
  the frontend beyond adding one more entry to its code→copy table.

**Compatibility.** Purely additive to the JSON shape inside an already-new,
not-yet-implemented column — there is no existing persisted row of this shape
to migrate, since OD-1's column does not exist yet. No Alembic impact beyond
what OD-1 already specified (one nullable, additive column). No change to
`RunCoordinator`'s three responsibilities or to the Check interface's
existing `name` field — this refinement only specifies that the
already-declared `name` is the value written into `code`.

## Consequences

**Positive.**
- Infrastructure failures that are genuinely fatal are caught before a stage
  starts, before any paid LLM call, with an actionable message.
- Graceful degradation is preserved rather than converted into hard failure —
  the *primary* risk this ADR exists to prevent.
- Adding a dependency check becomes a one-entry registry change, matching three
  patterns already established in this codebase.
- Degraded results become explainable ("Confluence was unreachable") instead of
  mysterious.

**Negative.**
- WARNING checks imply live network probes at stage start for dependencies the
  run may never use. See Performance.
- One more concept for contributors to learn, and one more place a check can be
  wrongly classified. Mitigated by the non-negotiable rule, which makes the
  BLOCKING/WARNING decision mechanical rather than judgemental.

**Migration impact.** Only under Option A: a single additive, nullable column.
No backfill. No data transformation.

**Backward compatibility.** Full. Existing rows remain valid; existing readers
are unaffected; the two implemented checks keep their current semantics; a run
that passes all checks behaves exactly as today.

**Operational impact.** A new class of `failed` runs that previously failed
later and less clearly, plus a new persisted-warning signal worth monitoring
(a spike in Jira warnings is a connectivity incident). The blocking-failure
log line is already distinguished (`agent_run_preflight_failed`).

**Performance impact.** Each BLOCKING check that performs I/O adds one
round-trip per applicable stage start; the LLM check adds none (pure config
read). WARNING checks are the real cost: naively implemented, each adds a
network probe to every stage start, including the common case where the
dependency goes unused. **OD-4** covers whether to cache health results, probe
lazily, or read only already-cached health.

## Alternatives considered

**Block on everything.** Rejected on direct evidence. All three remaining
dependencies have deliberate, documented graceful-degradation paths; blocking
would break workflows that currently succeed — a regression. It would also
violate the non-negotiable rule by blocking on request-content-conditional
dependencies, producing false positives (refusing to start because Jira is
down when the request never mentioned Jira).

**Warning-only for everything, including LLM and Neo4j.** Rejected: it fails
the product requirement ("Planning must never start if required infrastructure
is unavailable"). A stage that cannot possibly succeed should not consume the
user's time, and in the LLM case should not consume their money.

**Tool-specific logic inside each check with no shared model.** Rejected: it
produces N bespoke checks with N failure-handling conventions, and no way for
`RunCoordinator` to reason about them uniformly. The registry + severity model
exists precisely so the orchestrator handles severities, not dependencies.

**Special-case branches inside `RunCoordinator`.** Rejected explicitly. It
would put `if dependency == "jira"` logic in the single most critical
execution path in the system — the same path where an unguarded call recently
caused silent state corruption. `RunCoordinator` orchestrates; it must not
implement.

**Pre-flight as a separate pre-dispatch phase (before the `Run` row exists).**
Rejected. It would forfeit the existing, proven failure-persistence path
(`_fail_step`/`_fail_run`/`_commit_with_hook`) and require inventing a second
way to report a failure with no row to attach it to — the exact class of
divergence that caused the silent-corruption defect described in Context.

## Open decisions

**OD-1** and **OD-3** are resolved (2026-07-31); implementation of
WARNING-severity checks and of the manifest-driven `applies_to` pattern is
unblocked. Full reasoning for both lives in this ADR's companion decision
record; see the summaries below plus the updated sections above (Warning
persistence, and Implementation Guidance under each).

**OD-1 (RESOLVED) — Warning persistence mechanism.** **Decision: Option A** —
a new `AgentStep.preflight_warnings` JSON column
(`list[{code: str, dependency: str, message: str, checked_at: str}]` — see
Warning record schema below for `code`, added 2026-07-31 as a clarifying
refinement, default `[]`, nullable=False). Rejected B (conflates
orchestrator-produced entries with
agent-produced `evidence`, breaking provenance for every existing consumer)
and C (invisible to the user; fails the product requirement outright).
Alembic migration: one additive, nullable-default column, no backfill —
identical shape to `AgentStep.human_override`'s own addition. Written by
`RunCoordinator` alongside the existing `_fail_step`/`_commit_with_hook`
flush, never committed independently (same transaction-boundary discipline as
ADR 0012). Read by the UI as a small, always-present list — empty list, not
`null`, so callers never need an existence check.

**OD-2 (non-blocking) — Should the `git_ops` GitHub connection become a
BLOCKING check?** Architecturally permissible. Requires deciding whether
pre-flight's remit covers deterministic agents at all, given that the existing
failure is already immediate and well-messaged. Low value either way; safe to
defer indefinitely. Not resolved by this update — remains open and
non-blocking.

**OD-3 (RESOLVED) — Per-agent dependency declaration.** **Decision: explicit
declaration.** `AgentManifest` gains one new field,
`required_dependencies: frozenset[str] = frozenset()`, populated by
identifier constants defined alongside the checks in `app/orchestrator/
preflight.py` (`DEPENDENCY_LLM = "llm"`, `DEPENDENCY_NEO4J = "neo4j"`,
`DEPENDENCY_GITHUB_WRITE = "github_write"` — a closed, growable set, not free
strings). Every check's `applies_to()` becomes a one-line membership test
against this field instead of bespoke logic per check (today: `default_stage_
for_agent(...) is not None` for LLM, `max_graph_hops > 0` for Neo4j — two
different ad-hoc signals for the same kind of question). `max_graph_hops`
keeps its existing, unrelated job (the graph hop *budget*, enforced by
`app.graph.hop_budget`); it stops doubling as the Neo4j applies_to signal,
but every agent's `required_dependencies` for Neo4j is set consistently with
its own `max_graph_hops` (non-zero implies `DEPENDENCY_NEO4J` is present) —
this is a manifest-authoring convention, not a runtime derivation, so the two
fields can never silently diverge without a manifest-level test catching it.
Rejected implicit discovery (status quo): it works for exactly the two
dependencies it already covers, but generalises to nothing — a third static
dependency (e.g. a future Confluence-write agent) would need its own bespoke
manifest field and its own bespoke `applies_to` predicate, repeating the
problem OD-3 exists to close. Explicit declaration costs one field and one
line per manifest; it is testable in isolation (a manifest fixture asserting
membership, no check execution required) and extends to N future static
dependencies with zero change to `RunCoordinator` or to any existing check.
Scope note: this field is for **statically** determinable dependencies only
(BLOCKING/SKIPPED checks) — Jira and Confluence stay request-content-
conditional per the non-negotiable rule and are never represented here.

**OD-4 (blocking, only for WARNING checks) — Probe freshness and cost.**
Live probe per stage start, cached health with a TTL, or read-only consumption
of health already cached by `ToolRegistry`/the connection-health endpoint?
Determines whether WARNING checks are affordable at all.

**OD-5 (non-blocking, adjacent) — Global tool registration on import.**
`tests/conftest.py` imports `app.main`, whose module-level `app = create_app()`
triggers `register_all_tools()` as an import side effect for the entire pytest
process. Consequence: unit tests transitively depend on real Neo4j
availability once a Neo4j pre-flight check is active. The recommended fix is to
move registration into the lifespan hook (where `recover_orphaned_runs`
already lives), making module import inert — treating the root cause rather
than adding a `ToolRegistry` test mode, which would treat the symptom.
Infrastructure dependence is acceptable for `tests/integration`, not for
`tests/unit`. Tracked here because it is a consequence of this ADR's Neo4j
check, though it is not itself a pre-flight design decision.
