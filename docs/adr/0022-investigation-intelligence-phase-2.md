# ADR 0022: Investigation Intelligence — Phase 2 (design only)

## Status

**Approved — design only, implementation deliberately not started.**
Builds on [ADR 0021](0021-investigation-intelligence.md), whose Phase 1
(collection + one capped heuristic) and its Confluence-provider follow-up
are both shipped and live. Explicitly parked here by direction: no
further Phase 2 expansion, and no implementation of what's already
designed, until the observability endpoint (`GET /api/v1/
investigation-intelligence/summary`) shows real production usage — the
self-gating properties below mean that's a data-driven decision, not a
calendar one. Product focus moves to user-facing capabilities in the
meantime (Impact Check, Engineering Decisions, UX/explainability,
architecture visualization, workflow experience, planning agents).

## Why this ships as a design, not code

Checked against the real database before writing a word of this: as of
this document, `investigation_provider_events` holds **200 rows** and
`investigation_outcomes` holds **45 rows**, spanning about **34 minutes**
of wall-clock time — almost entirely this session's own manual testing,
not organic multi-session usage. ADR 0021's own author (the user)
gated Phase 2 explicitly: *"Once we've observed the data for a while, we
can begin Phase 2."* Thirty-four minutes of synthetic data is not that.

Rather than either (a) blocking on an arbitrary wait, or (b) designing
something that has to be manually re-gated later once real data exists,
every mechanism below is **self-gating on sample size** — see "Self-gating,
not a manual switch" — so this design can be implemented whenever it's
convenient, and will provably behave identically to Phase 1 (no additional
influence at all) until real usage actually accumulates enough samples to
justify it. The gate isn't a calendar date or a feature flag someone has to
remember to flip; it's a property of the formulas themselves.

## Context — what Phase 1 actually shipped, verified against the real code

Three things exist and are live today, not assumed from ADR 0021's own
text:

1. **Collection** — `record_provider_outcome()`/`record_investigation_outcome()`,
   wired at the three call sites ADR 0021 §"Phase 1 scope" specified
   (`engine.py`'s evidence point, `investigate()`'s clean exit,
   `context_discovery/agent.py`'s outer `except`).
2. **One capped heuristic** — `engine.py:364`:
   `blended[capability] = min(1.0, live_value + memory_value * 0.15)`.
   Fixed ±0.15 cap, no sample-size awareness at all — a cold-start
   `repository_provider_preference()` of `0.0` (verified:
   `service.py`'s own cold-start guarantee) contributes nothing, but a
   *single* real success or failure already contributes up to the full
   ±0.15, same as a hundred of them would. This is the concrete gap
   Phase 2's tiered cap (below) closes.
3. **One provider-specific application** — `ConfluenceProvider.resolve_for_issue`
   reads `recent_repeated_failure()` (`providers.py:121`,
   `_RECENT_MCP_FAILURE_WINDOW = timedelta(hours=1)`) to skip a doomed MCP
   attempt in favor of REST. Scoped, contained, already shipped — not
   part of this document's scope, cited here only as the existing
   precedent Phase 2's cooldown mechanism (below) generalizes.

Also verified still true: `_select()`'s own formula
(`necessity_rank`, `adjusted_score = score - boost`, `cost`, `key`) is
**unchanged** by any of the above, and remains unchanged by everything
proposed in this document too — see "Non-goals."

## Decision

Three items, each independently shippable, each self-gated:

### 1. Tiered priority-boost cap (the concrete "more aggressive" ask)

The ±0.15 cap exists purely as an anti-runaway safety valve for a heuristic
whose input (`repository_provider_preference`) had no sample-size
guarantee behind it — a legitimate concern on day one, less so once a
`(scope, capability)` pair has real history. Replace the fixed cap with a
tiered one, keyed on `sample_count` (already returned by
`ProviderEffectiveness`, already computed by `provider_effectiveness()` —
no new query, no schema change):

```python
# app/investigation_intelligence/service.py

def _boost_cap_for(sample_count: int) -> float:
    """How much repository_provider_preference() may move a capability's
    priority score, as a function of how much real history backs it.
    Below the first tier, behaves byte-for-byte like Phase 1's fixed
    ±0.15 — the whole reason this design can ship as a no-op today's
    ~34-minute-old dataset can't yet justify anything stronger."""
    if sample_count < 10:
        return 0.15   # Phase 1's own cap, unchanged
    if sample_count < 50:
        return 0.25
    return 0.35        # ceiling — still additive, still never overrides
                        # necessity_rank (see Non-goals)
```

`repository_provider_preference()` gains a second return value (or a
sibling method — exact shape is an implementation choice at build time,
not a design commitment here) exposing the `sample_count` behind its
scalar, so `engine.py`'s `_apply_memory_priority_boost` can look up the
right tier per capability:

```python
# app/context_pipeline/reasoning/engine.py — _apply_memory_priority_boost,
# conceptual diff against the Phase 1 version
memory_value, sample_count = await session.intelligence.repository_provider_preference_with_confidence(
    scope=scope, capability=capability
)
cap = _boost_cap_for(sample_count)
blended[capability] = min(1.0, live_value + memory_value * cap)
```

**Self-gating property**: at `sample_count < 10` (true for every
`(scope, capability)` pair in the database today — 200 events spread
across an unknown number of distinct scopes is nowhere near 10 per pair
yet), this is exactly Phase 1's existing formula. Nothing changes in
observed behavior until a `(scope, capability)` pair has genuinely been
exercised ten times.

### 2. Provider cooldown — generalizing the Confluence-specific wiring

`ConfluenceProvider` today hardcodes its own `recent_repeated_failure`
check. Phase 2 lifts the same pattern into the engine loop itself, so any
investigator gets it for free without writing provider-specific plumbing:

```python
# app/context_pipeline/reasoning/engine.py — inside _candidate_actions,
# conceptual diff
async def _candidate_actions(
    state: WorkingContext, investigators: list[Investigator], session: SessionContext
) -> list[tuple[InvestigationAction, Investigator]]:
    candidates = [...]  # unchanged proposal logic
    if session.intelligence is None:
        return candidates
    kept = []
    for action, investigator in candidates:
        scope = await _investigation_scope(state, session, action)
        if scope is None:
            kept.append((action, investigator))
            continue
        recent_failure = await session.intelligence.recent_repeated_failure(
            scope=scope, provider=action.provider, capability=action.targets,
            within=_COOLDOWN_WINDOW,
        )
        # A single recent failure never removes a candidate — only
        # `_select()`'s existing scoring is touched, and only a small
        # amount, via the SAME priority_boost mechanism as (1) above, not
        # a second parallel exclusion path. See Non-goals: nothing here
        # ever drops an action from the candidate list outright.
        kept.append((action, investigator))
    return kept
```

On reflection while drafting this: the ADR 0021 "never blacklist a
provider" principle argues *against* actually removing a candidate here,
even temporarily — that already lives in `ConfluenceProvider`'s own
narrower, provider-owned decision (try REST first, still try MCP as a
fallback if REST also fails) precisely because a provider-level adapter
can make a same-cycle recovery decision the engine's own candidate-list
filtering cannot. **Generalizing this to the engine loop is downgraded
from "in scope" to "reconsider at implementation time"** — it may turn
out `ConfluenceProvider`'s pattern is best left provider-specific and
copied by hand into the next provider that needs it (Jira, GitHub),
rather than centralized. Flagging the open question rather than
resolving it prematurely; see "Open questions."

### 3. `investigation_evidence_cache` — reusable fetched items

The one item from ADR 0021's deferred list that's pure efficiency, not
policy: a Jira ticket's description or a Confluence page's content
doesn't change every few minutes, so re-fetching it from scratch on every
investigation against the same anchor is wasted latency and API quota —
the same `evidence_entries` a `ProviderOutcomeEvent` already
records-the-shape-of could be served from a short-TTL cache keyed on
`(scope, action_key)` instead of a live call.

```python
@dataclass(frozen=True)
class CachedEvidence:
    scope: InvestigationScope
    action_key: str
    text: str
    fetched_at: datetime
    ttl: timedelta = timedelta(hours=6)  # conservative default; a work
                                          # item can genuinely change

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) - self.fetched_at > self.ttl
```

**Self-gating property**: this one isn't sample-gated (it's not a
learning signal, just a cache) — it's *correctness*-gated instead: reads
only ever serve a cache hit for the exact same `(scope, action_key)`
already fetched within its TTL, so cold-start behavior (today, every key
is a miss) is identical to no cache existing at all. Zero risk of
serving stale-and-wrong data past the TTL, and zero risk of ever
serving something to a scope that didn't ask for it.

## Self-gating, not a manual switch

The property that makes this document safe to implement now, whenever
convenient, without a second "is it time yet" conversation:

| Mechanism | Inert until | Behaves like Phase 1 (or "no cache") until then |
|---|---|---|
| Tiered boost cap | 10 samples for a `(scope, capability)` pair | Exactly Phase 1's fixed ±0.15 |
| Cooldown (if built) | A real repeated-failure pattern within the window | Exactly Phase 1 — no cooldown ever applied |
| Evidence cache | N/A (correctness-gated, not sample-gated) | Exactly no-cache — every read is a fetch |

Whoever implements this can *watch* the gates open via the observability
endpoint Phase 1 already shipped
(`GET /api/v1/investigation-intelligence/summary`) — `priority_boost_usage`
and `memory_hit_rate` climbing over real days of usage is the same signal
that tells you tier 2/3 of the boost cap has started actually firing,
with no separate instrumentation to build.

## Non-goals (restated and extended from ADR 0021)

- **No ML model, no black-box scoring.** Every number in this document is
  a named, hand-written formula over named fields — exactly ADR 0021's
  own "hint, not decision" framing, at a higher cap.
- **`_select()`'s `necessity_rank` ordering is never touched.** A required
  capability still always beats a recommended one, at any tier, at any
  sample count. Phase 2 only ever adjusts the score *within* a necessity
  tier.
- **No permanent exclusion.** Every mechanism here decays (the existing
  `HALF_LIFE_DAYS = 30.0` weighting) or is time-boxed (`within` windows) —
  nothing added by this document introduces a persistent blacklist.
- **No schema change.** `sample_count` already exists on
  `ProviderEffectiveness`; the evidence cache is new *storage* (a fourth
  table, or a bounded in-memory/Redis cache — an implementation choice,
  not a design commitment) but touches no existing table.

## Open questions (deliberately unresolved here)

1. **Cooldown: engine-level or stays provider-owned?** See item 2 above —
   leaning toward "stays provider-owned, copy the pattern by hand" but not
   settled.
2. **Evidence cache storage** — a new table (consistent with this
   package's existing shape) vs. an in-process/Redis cache (faster, but a
   new operational dependency this codebase doesn't otherwise have). Needs
   a look at whether Redis (or similar) is already provisioned anywhere
   before deciding.
3. **`repository_provider_preference_with_confidence`'s exact return
   shape** — a tuple, a small dataclass, or folding `sample_count` into
   the existing method's signature as an out-parameter. Implementation
   detail, deferred to build time.

## Consequences

- Nothing changes today. This document alone has zero runtime effect.
- Once implemented, `(scope, capability)` pairs with real history get
  proportionally more say in `_select()`'s ordering — bounded, decaying,
  and provably inert on the current dataset until that history exists.
- The evidence cache, if built, reduces redundant Confluence/Jira calls
  for repeated investigations against the same anchor — a latency and
  API-quota win independent of anything learning-related.
- Still explicitly deferred past Phase 2: `investigation_corrections`
  (the human-feedback loop wired to `_verify_claim`/`_settle_claims`'s
  refuted-claim path — ADR 0021's own Phase 3 item, untouched by this
  document), the retention/pruning job (not urgent at 200 rows; KAN-24's
  precedent is the template whenever it is), and any actual UI beyond the
  read-only observability endpoint that already exists.
