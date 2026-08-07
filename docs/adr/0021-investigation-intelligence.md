# ADR 0021: Investigation Intelligence — cross-investigation retrieval and planning experience

## Status

Proposed. Phase 1 scope only (see below) — later phases are named but not committed by this ADR.

## Context

A runtime behavioral audit of Context Discovery (traced execution, not
architecture — every claim verified against a real call site or a real
log line from an actual run) found the reasoning loop genuinely
sophisticated: adaptive action selection, dynamic provider fallback,
self-evaluation, budget-aware iteration, confidence-based stopping, and
contradiction-driven re-retrieval are all real and wired (`engine.py`'s
`investigate()` loop, `_select()`, `capability_priority()`,
`state.derived["investigation_priority"]`). One capability was verified
**absent**: nothing persists across investigations. Every run starts
cold. Concretely observed tonight: the identical Confluence MCP
permission failure (`"You don't have permission to connect via API
token"`) was re-discovered, from zero, in every one of several separate
investigations against the same Atlassian site — the same four MCP calls,
the same four failures, the same fallback to REST, rediscovered each
time at the same cost.

This ADR is deliberately **not** "add another planner, retrieval engine,
or provider." Sections 1–10 of the audit are already real. The one
missing piece is specifically: *does this investigation's outcome make
the next investigation cheaper, faster, or more accurate?* Today: no.

### Why this is not Engineering Memory

Engineering Memory (`app/learning_engine/`, `app/repositories/
engineering_memory_repository.py`, `app/models/engineering_evidence_
pack.py`, `app/models/user_correction.py`) was read in full before
writing this design, not assumed from its name. It answers a different
question: *is this specific architecture-graph relationship correct*
(`UserCorrectionRecord`/`LearningEvent`, keyed by `relationship_key`/
`relationship_type`; `EngineeringEvidencePackRecord`, keyed by
`(repository_id, commit_sha, schema_version)`, populated by the code
indexer). Every real caller of that system is indexer/relationship
code (`indexer/graph/cross_repo_memory.py`, `indexer/hypotheses/
shadow_runner.py`, `services/engineering_intelligence/relationship_
lookup.py`). Grepped: zero callers from `context_pipeline/` or
`agents/context_discovery/`.

Investigation Intelligence answers a different question again: *which
retrieval strategy finds answers well, for this repository/source, and
is the planner's own strategy choice getting better over time.*
Engineering Memory stores engineering knowledge. Investigation
Intelligence stores retrieval and planning **experience**. Different
domain, different tables, different service, no foreign keys between
them, new sibling package (`app/investigation_intelligence/`, mirroring
`app/learning_engine/`'s own package shape) — not an extension of the
existing one.

One thing *is* reused directly, deliberately: `CorrectionSource`
(`knowledge_engine/contracts/correction.py`) — the identity/trust-level
shape `LearningEvent` itself already reuses rather than redefining "who
is telling us this and how much do we trust them." A human correcting an
investigation's claim is the same kind of fact as a human correcting a
graph relationship; only the target differs.

## Decision

### 1. The planner never touches a table. `InvestigationIntelligenceService` is the only interface.

```python
# app/investigation_intelligence/service.py

class InvestigationIntelligenceService:
    def __init__(self, db: AsyncSession) -> None: ...

    # --- writes (append-only, mirrors Recorder.evidence()'s own event shape) ---
    async def record_provider_outcome(self, event: ProviderOutcomeEvent) -> None: ...
    async def record_investigation_outcome(self, outcome: InvestigationOutcomeEvent) -> None: ...

    # --- reads: typed dataclasses out, never an ORM row, never a raw query ---
    async def provider_effectiveness(
        self, *, scope: InvestigationScope, capability: str
    ) -> list[ProviderEffectiveness]: ...

    async def recent_repeated_failure(
        self, *, scope: InvestigationScope, provider: str, capability: str, within: timedelta
    ) -> ProviderOutcomeEvent | None: ...
```

`engine.py` never imports `app/investigation_intelligence/models.py` or
touches `AsyncSession` for this purpose at all — it receives an already-
constructed `InvestigationIntelligenceService` (or `None`) through
`SessionContext`, the same object `investigator.run()` already carries
(no new parameter threading). This mirrors the layering the audit already
found and verified real: `engine.py` doesn't import `investigation_
planner.py` directly either — it goes through `understanding.py`'s own
interface. Same discipline, same reason: the consumer depends on a
narrow contract, never on another module's internals.

### 2. Scope is not always "repository" — this is the one place last message's draft was wrong

Tonight's own Confluence saga is the counter-example: MCP-vs-REST
effectiveness for documentation is a property of the **Atlassian site**
(`KnowledgeConnection.id` — one connection is one Confluence/Jira
deployment), not of any GitHub repository. Architecture/source-control
effectiveness genuinely is repository-scoped. Forcing everything onto
`repository_id` would silently misattribute Confluence learning to
whichever repository happened to be in context, which is simply false —
the MCP permission gate is an org-wide Atlassian fact, true for every
repository a workflow might mention.

```python
@dataclass(frozen=True)
class InvestigationScope:
    scope_type: Literal["repository", "knowledge_connection"]
    scope_id: str
```

Every write and read takes a scope built by whichever investigator/
provider produced the event — a Confluence event scopes to its
`KnowledgeConnection.id`; a graph-traversal event scopes to
`repository_id`. The service itself is scope-agnostic; it never assumes
which axis a given capability uses.

### 3. What gets captured per provider outcome — richer than "did it fail"

```python
@dataclass(frozen=True)
class ProviderOutcomeEvent:
    investigation_id: str
    cycle_number: int                    # explicit, not inferred from created_at — see below
    scope: InvestigationScope
    capability: str                      # work_item | repository | architecture | documentation
    investigation_type: EngineeringStrategy  # reused from investigation_planner.py, not redefined
    provider: str
    action_key: str
    outcome: Literal["success", "not_found", "unavailable", "failed"]
    declared_cost: int                   # action.cost — what the planner believed at decision time
    latency_ms: int                      # measured — for later recalibrating declared_cost against reality
    yielded_evidence: bool
    # --- planner-decision fields, folded into the same row rather than a parallel table ---
    # `_select()` picks exactly one action per cycle, which runs and produces exactly one
    # outcome — 1:1 in the real loop, verified in the audit. A second table joined 1:1 against
    # this one would only ever be queried together with it, so it is the same row.
    necessity_at_selection: Literal["required", "recommended"]
    base_score_at_selection: float          # CapabilityAssessment.score before this action
    priority_boost_applied: float
    priority_boost_source: Literal["live_llm", "memory_seeded", "both", "none"]
    confidence_before: float
    confidence_after: float                 # confidence_after - confidence_before = the
                                             # deterministic "confidence improvement" signal —
                                             # computed, never LLM-asserted, matching
                                             # CapabilityAssessment.score's own existing rule
    # Stored as JSONB (see below for why); exposed as a typed value everywhere in Python —
    # nothing outside the repository's serialize/deserialize boundary ever sees a raw dict,
    # matching how every other typed contract in this codebase already behaves.
    state_snapshot: StateSnapshot
    created_at: datetime


@dataclass(frozen=True)
class CandidateScore:
    """One action `_select()` considered but did not necessarily choose —
    what makes the winner's context (the typed fields above) reconstructable
    as a ranking rather than a single opaque outcome."""

    provider: str
    action_key: str
    capability: str
    necessity: Literal["required", "recommended"]
    score: float
    cost: int


@dataclass(frozen=True)
class StateSnapshot:
    """The whole decision context at one `_select()` call — not just the
    targeted capability's own state. `version` follows the exact precedent
    `EngineeringEvidencePackRecord.schema_version` already set for this
    codebase's one other compressed/structured blob column: the field
    that lets the *shape inside* the JSONB evolve without a migration,
    while the column itself never changes. `InvestigationIntelligenceService`
    is responsible for tolerating an older `version` on read — returning
    `None` for any field a given version predates, never raising — the
    same tolerance `get_evidence_pack_by_pack_id` already has to have for
    `schema_version`."""

    version: int  # CURRENT_SNAPSHOT_VERSION = 1 at Phase 1 ship
    candidates_considered: tuple[CandidateScore, ...]
    all_capability_scores: dict[str, float]
    open_contradictions: int
```

**"Usefulness"** is deliberately *not* a stored field the way the design
brief listed it. Storing an LLM's opinion of its own usefulness would
violate the one rule this whole audit confirmed the codebase actually
holds itself to (`CapabilityAssessment.score`: *"computed... never set
independently"*). Instead usefulness is **derived at read time** from two
already-captured, already-deterministic facts: `yielded_evidence` and
`confidence_after - confidence_before`. `provider_effectiveness()`
computes it; nothing upstream invents a number.

**"Cycles saved"** is not a raw field either — no single event knows
what *would* have happened without it. It is a derived, comparative
metric `InvestigationOutcomeEvent` (below) makes possible: compare actual
`cycles_used` for memory-seeded investigations against the historical
baseline for structurally similar ones. A Phase-1-honest metric, not
invented, but explicitly a *later* aggregate, not a Phase-1 column.

### 3a. Policy-readiness check — verified, not assumed

Before committing to the schema above, it was checked directly against
one question: *could a future policy-learning system (a contextual
bandit, a learned ranking function, anything beyond today's fixed
formula) actually be trained from this, or would it hit a wall requiring
a migration?* Four gaps were found and closed by the fields above, each
because a real future need was traced to a missing signal, not
speculatively:

- **Only the winner was recorded.** Any future preference/ranking
  learning needs to know what else was available and its score — without
  it, there is no way to ever reconstruct "was this a close call," which
  is the basis of counterfactual policy evaluation. Closed by
  `state_snapshot.candidates_considered` — free to capture, since
  `_candidate_actions()` already computes the full set one line before
  `_select()` runs.
- **No explicit step ordering.** `TimelineEntry`'s own docstring already
  documents why this codebase learned not to rely on `created_at` for
  ordering (`func.now()` is transaction-scoped; two rows in one
  transaction can tie) — the same lesson applies here. Closed by
  `cycle_number`.
- **State was capability-local, not investigation-wide.** The typed
  fields describe only the targeted capability's own score; a real
  strategy-learning system needs the full picture (other capabilities'
  scores, open contradictions) to ever learn cross-capability behavior,
  not just single-capability provider preference. Closed by
  `state_snapshot.all_capability_scores`/`open_contradictions`.
- **Declared cost and measured latency were conflated.** `action.cost`
  (`investigation.py:88`, what `_select()` actually reasons with) is a
  different signal from measured `latency_ms` — a future policy needs
  both, to eventually recalibrate whether the declared cost was
  realistic. Closed by adding `declared_cost` alongside the existing
  `latency_ms`.

`state_snapshot` is deliberately JSONB, not more typed columns: this is
the actual mechanism that makes "no schema changes needed later" true.
New keys can be added to what's captured inside the blob as future needs
are identified, without a migration; the strongly-typed fields around it
stay exactly as small as Phase 1's own heuristic and effectiveness
aggregates require. Phase 1 **captures** `state_snapshot` fully — it
costs nothing extra to gather at the existing call site — but does not
**read** it anywhere. No policy logic is implied or scheduled by storing
it; this section exists to show the ceiling is not artificially low, not
to commit to reaching it.

### 4. What gets captured per completed investigation — the root record

```python
@dataclass(frozen=True)
class InvestigationOutcomeEvent:
    investigation_id: str
    scope: InvestigationScope
    investigation_type: EngineeringStrategy
    cycles_used: int
    # Superset of memory.Readiness ("READY" | "PARTIAL" | "BLOCKED"), +FAILED — see below
    # for why FAILED cannot be represented by Readiness alone and needs a different write path.
    terminal_outcome: Literal["READY", "PARTIAL", "BLOCKED", "FAILED"]
    # The single objective success proxy the policy work in 3a needs — a real, already-computed
    # scalar (agent.py's own `_confidence_for(state).score`, confirmed distinct from any one
    # capability's own score: it is what tonight's `confidence=0.86` log line actually was).
    # Optional because a FAILED investigation may crash before any assessment ever runs.
    confidence: float | None
    final_capability_scores: dict[str, float]   # per-capability detail, alongside the scalar above
    contradictions_encountered: int
    contradictions_resolved: int
    priority_boost_source_used: bool   # did memory influence this run at all
    created_at: datetime
```

**Write integration point, corrected from the first draft.** `investigate()`'s
clean exit is *not* the only place this needs to be written from — it is
the only place that can produce `READY`/`PARTIAL`/`BLOCKED`, but a crash
never reaches it at all. Checked directly (not assumed): `context_discovery/
agent.py` has **no** outer `try`/`except` around its call into `discover()`/
`resume()` today — an unhandled exception there propagates straight past
Investigation Intelligence entirely, all the way to the orchestrator (the
same layer `test_run_coordinator.py::test_agent_exception_marks_run_as_
failed` already exercises, confirming this is real, exercised behavior,
not a hypothetical). So the write for the `FAILED` case has to happen
at that outer call site, not inside `engine.py`:

```python
try:
    state = await discover(...)   # or resume(...)
except Exception:
    if intelligence is not None:
        await intelligence.record_investigation_outcome(
            InvestigationOutcomeEvent(..., terminal_outcome="FAILED", confidence=None, ...)
        )
    raise   # re-raised unchanged — Investigation Intelligence is a side effect,
            # never swallows or alters the real error path the orchestrator relies on
```

The `READY`/`PARTIAL`/`BLOCKED` write stays exactly where designed
originally: at `investigate()`'s normal exit, immediately after the
verified-real final `synthesize_engineering_understanding()` call.

### 5. Time decay — a read-time weight, never a write that mutates history

Consistent with the one convention every persisted-history table in this
codebase already follows without exception (`TimelineEntry`,
`LearningEvent`, `UserCorrectionRecord`, `EngineeringEvidencePackRecord`
— all insert-only, never updated): **no row is ever pruned or rewritten
to reflect decay.** Decay is applied by the service at query time:

```python
weight = exp(-age_days / HALF_LIFE_DAYS)   # HALF_LIFE_DAYS = 30, configurable
```

`provider_effectiveness()` computes a recency-weighted success rate and
weighted-average latency/confidence-improvement over events in a bounded
window (e.g. the most recent 200 events per `(scope, capability,
provider)`, or the last 90 days, whichever is smaller) — bounded
deliberately, not a full-table scan on every planning cycle, the same
retention-consciousness KAN-24 already had to add to Engineering Memory
after the fact (`prune_evidence_packs`). This system gets that
discipline from day one instead of as a Phase 2 patch.

This also directly satisfies "repositories and documentation evolve": a
stale MCP-blocked verdict from 60 days ago (two half-lives) contributes
roughly a quarter the weight of one from today, so if the org toggle
ever does get fixed, the planner adapts back within a few real
investigations rather than needing a manual reset.

### 6. Repository-/scope-specific learning falls out of (2) + (3) for free

No separate mechanism needed: `provider_effectiveness(scope=..., capability=...)`
scoped to one repository or one `KnowledgeConnection` *is* "repository A
prefers Confluence, repository B prefers GitHub" — it's the same query,
scope substituted. No new concept to build.

### 7. Planner integration — Phase 1's only touchpoint, and it's a heuristic, not a decision

Exactly one call site changes, and it feeds a pipe the audit already
verified is real and consumed: `state.derived["investigation_priority"]`
(`engine.py:295-296`, read every cycle by `_select()`). Before cycle 1,
if an `InvestigationIntelligenceService` is available:

```python
memory_boost = await intelligence.repository_provider_preference(scope, capability)
# capped, additive, small — a hint riding alongside the live-LLM signal,
# never large enough to override it outright
state.derived["investigation_priority"] = {
    k: min(1.0, v + memory_boost.get(k, 0.0) * 0.15)  # hard cap: ±0.15
    for k, v in (state.derived.get("investigation_priority") or {}).items()
}
```

This is the entire Phase 1 planner change. `_select()`'s own scoring
formula (`necessity_rank`, `adjusted_score`, `cost` — verified real,
unchanged) is not touched. No new decision logic, no new selection path
— one more number added to a dict that already flows through code proven
to work.

## Phase 1 scope (this ADR's actual commitment)

**In scope:**
- New package `app/investigation_intelligence/` (`models.py`,
  `repository.py`, `service.py`) — mirrors `app/learning_engine/`'s shape.
- Two new tables: `investigation_provider_events`,
  `investigation_outcomes`. No FK to any Engineering Memory table.
- Collection: one `record_provider_outcome()` call added at the existing
  `Recorder.evidence()` call site in `engine.py`'s loop; one
  `record_investigation_outcome()` call at `investigate()`'s clean exit
  (`READY`/`PARTIAL`/`BLOCKED`); one more at the outer `try`/`except`
  added around `discover()`/`resume()`'s call site in `context_discovery/
  agent.py` (`FAILED` — the only path that can produce it, since a crash
  never reaches `investigate()`'s own exit). All three behind a
  `service: InvestigationIntelligenceService | None = None` parameter,
  defaulting to a no-op — existing callers/tests are unaffected if it's
  never passed, and the `except` block re-raises unchanged, so the real
  error path to the orchestrator (`test_run_coordinator.py`'s own
  coverage of it) is untouched.
- Exposure: `InvestigationIntelligenceService`'s read methods, fully
  testable in isolation against the two new tables.
- The single capped priority-boost heuristic in section 7.
- `state_snapshot` is captured in full on every event (section 3a) — at
  zero extra retrieval cost, since both its contents are already in
  memory at the existing call site. It is written and stored; nothing
  in Phase 1 reads it. This is the deliberate mechanism for future
  policy work to proceed without a schema migration, not a commitment
  to build that work.

**Explicitly deferred, not forgotten:**
- `investigation_evidence_cache` (reusable fetched items) — Phase 2.
- `investigation_corrections`, wired to `_verify_claim`/`_settle_claims`'s
  refuted-claim path (verified real in the audit) — Phase 3.
- Any UI surface for this data.
- Retention/pruning job (bounded reads make this non-urgent for Phase 1;
  KAN-24's own precedent is the template when it's needed).
- Changing `_select()`'s scoring formula itself, or any planner logic
  beyond the one capped additive heuristic above.

## Post-Phase-1 addendum: provider-level adaptive retrieval (shipped)

Raised as a deliberate Phase 1 scope exclusion above, then picked up as a
small, contained follow-up (not full ADR Phase 2 — see the observability
addendum below for why that's still gated on real usage data first):
`recent_repeated_failure()` is now wired into `ConfluenceProvider.
resolve_for_issue` (`app/context_pipeline/providers.py`).

One correction to the exclusion note's own worked example above: the
provider value actually recorded for every Confluence documentation
action is `"confluence"` (`ConfluenceInvestigator.name`, see
`investigators.py`) — MCP and REST are never recorded as distinct
`provider` values, since `resolve_for_issue` already tries both
internally in a single action. `recent_repeated_failure(provider=
"confluence_mcp", ...)` as originally sketched would never have matched
anything; the real call reads `provider="confluence"`, answering "did
this connection's *whole* Confluence pipeline fail recently" rather than
"did MCP specifically fail" — still exactly the signal the ADR's org-wide
API-token-access-toggle scenario needs, since that failure mode blocks
MCP regardless of which issue is being searched.

Behavior: before attempting MCP, checks
`recent_repeated_failure(scope=InvestigationScope(scope_type=
"knowledge_connection", scope_id=<connection id>), provider="confluence",
capability="documentation", within=timedelta(hours=1))`. A recent failure
found → try REST first; if REST succeeds, MCP is skipped entirely (the
"1 less cycle" outcome from the original worked example). If REST also
comes up empty, MCP still runs as normal — a skipped-once hint never
becomes a permanent block, and the later MCP-failed→REST-fallback branch
reuses the already-fetched REST result instead of calling it a second
time for the same issue. `intelligence=None` (no service wired, e.g. an
ad-hoc test fixture) reproduces the pre-existing behavior exactly —
`recent_repeated_failure` is never called at all.

`ResolvedKnowledgeAccess` gained a `connection_id: str | None = None`
field (`app/knowledge/access_resolver.py`) to carry the `KnowledgeConnection.id`
already loaded by `resolve_knowledge_access` through to this call site,
rather than a second duplicate query.

## Consequences

- Two new tables, one new package, three new call sites (two in `engine.py`, one in `context_discovery/agent.py` — all additive, all behind an optional parameter), one capped read into an existing dict. Nothing existing is modified in place.
- The Confluence MCP scenario from tonight becomes: investigation 2 against the same Atlassian site sees `provider_effectiveness` return MCP at ~0% weighted success, REST tiers at high weighted success, and starts cycle 1 with REST already mildly preferred — without ever querying a table from planner code, and without any single failure being able to permanently blacklist a provider (decay guarantees recoverability).
- Explicitly not delivered by Phase 1: cross-investigation evidence reuse, human-correction learning, or any change to how `_select()` itself scores actions beyond the one additive input.
- What Phase 1 *does* leave genuinely open for later, verified rather than assumed (section 3a): a future policy-learning pass over `investigation_provider_events` has the full candidate set, explicit step ordering, whole-state context, and both a priori and empirical cost for every decision ever made from the moment Phase 1 ships — without needing to wait for a second collection phase to start accumulating the right data first.

## Post-Phase-1 addendum: observability endpoint

Added after Phase 1 landed, in response to an explicit ask to validate
the collected signal's quality before Phase 2 lets it influence
retrieval more aggressively — not part of the originally-approved scope
above, and deliberately still just read-only visibility, not a decision
surface: `GET /api/v1/investigation-intelligence/summary` (admin-only,
`app/api/v1/routers/investigation_intelligence.py`) aggregates directly
over the two tables — provider outcome counts, provider success rate,
confidence-improvement distribution, latency distribution, investigation
cycles by terminal outcome, priority-boost usage rate, memory-hit rate
(how often `priority_boost_source` shows Investigation Intelligence
actually contributed, not just whether a boost fired at all), and
repeated-failure grouping (the same pattern the deferred
ConfluenceProvider integration above would act on, surfaced here purely
for a human to look at first). Nothing in `engine.py`'s own reads changes
— this endpoint queries the tables directly, the same way
`calibration.py` queries `ConfidenceCalibration` directly rather than
through a write-oriented service.
