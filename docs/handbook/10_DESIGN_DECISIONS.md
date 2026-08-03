# Section 10 — Design Decisions

Format per decision: Decision · Problem · Options considered · Why selected
· Benefits · Drawbacks · Future evolution. Sourced from ADRs/RFCs already
cited in earlier sections — cross-references point back rather than
re-deriving.

## Why append-only (Engineering Memory)?

- **Problem**: `replace_repository_graph`'s full-replace model had no
  history; you couldn't ask "what did we believe last week."
- **Options**: (a) mutable rows with a `confidence` column, (b) versioned
  rows with soft-delete, (c) append-only log, current-state derived at
  read time.
- **Why (c)**: reproducibility and audit-trail requirements are absolute,
  not "usually" — a taxonomy rename or a silent edit breaks every
  historical record depending on it.
- **Benefits**: real confidence history, real materializer replay tests,
  no second mutable source of truth to keep in sync with itself.
- **Drawbacks**: unbounded row growth by design for `Hypothesis`/
  `ValidationResult`/correction/confidence-history tables (ADR 0018 §
  Consequences, stated directly, not hidden).
- **Future evolution**: compaction/deduplication of identical-confidence
  repeat runs, explicitly deferred to "a future RFC if write volume
  warrants it." Detail: [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md).

## Why a materializer?

- **Problem**: two independent write paths to Neo4j (direct writes today,
  a hypothetical Engineering-Memory-derived path) risk silent divergence.
- **Options**: (a) cut over immediately, (b) build and prove the
  projection in shadow mode first, never wired into production writes.
- **Why (b)**: RFC-05B's own audit found a real gap (`CALLS_SERVICE`'s
  `via` property was never captured) before implementation — proving the
  cheaper, lower-risk path is right before betting the live write path on
  it.
- **Benefits**: Parity dashboard, Validation 7, and a provable "the graph
  is derivable from history" claim, all without production risk.
- **Drawbacks**: the inversion isn't real yet in production — Neo4j is
  still written directly today. See [16_REALITY_CHECK.md](16_REALITY_CHECK.md).
- **Future evolution**: cut over `replace_repository_graph` itself to the
  materializer, not yet scheduled to a specific RFC.

## Why Neo4j?

Detail: [02_STORY.md](02_STORY.md), [12_DIFFICULT_QUESTIONS.md](12_DIFFICULT_QUESTIONS.md).
Summary: native traversal for blast-radius/dependency queries; accepted
as a synced index, not system of record, specifically to avoid making a
graph store carry append-only audit-log responsibilities it isn't built
for.

## Why validators (as a distinct stage from generation)?

- **Problem**: if a `HypothesisGenerator`'s own confidence could influence
  the graph, an LLM asserting something with high self-confidence would be
  indistinguishable from a deterministically-proven fact.
- **Options**: (a) trust generator-reported confidence directly, (b)
  require independent, deterministic re-validation of every hypothesis
  regardless of generator kind.
- **Why (b)**: this is the single load-bearing anti-hallucination
  mechanism in the whole platform (§ [06_FRONTIER_AI.md](06_FRONTIER_AI.md)).
- **Benefits**: one hypothesis can be produced by a cheap, wrong generator
  and still get promoted if independently confirmed; conversely an
  expensive, confident LLM claim with no corroboration stays at
  `CANDIDATE` forever.
- **Drawbacks**: a real relationship with only one plausible evidence
  domain (nothing else could independently confirm it) may sit below
  `Verified` indefinitely — an honest ceiling, not a bug.
- **Future evolution**: `RuntimeValidator`/`OwnershipValidator`/
  `ApiContractValidator`, deferred until their evidence sources exist
  (RFC-06B).

## Why an explanation engine?

- **Problem**: a confidence *state* alone doesn't tell a user why they
  should trust it.
- **Options**: (a) build a second "evidence fusion" layer, (b) audit
  whether fusion already exists and, if so, build only a narration layer.
- **Why (b)**: RFC-06C audited first and found `DefaultConfidenceEngine
  .aggregate` already does real fusion — building a second one would have
  duplicated a component frozen at RFC-03 approval. This decision is
  itself a case study in "audit before building," cited multiple times in
  this handbook.
- **Benefits**: explanation is deterministic, reproducible, and can never
  drift from the actual confidence formula's thresholds (public constants
  referenced directly, not hand-copied).
- **Drawbacks**: only computed once, at write time — recomputing an
  explanation later from a stored relationship alone is impossible,
  because `ValidationResult`s themselves are never persisted (an accepted
  trade-off, not an oversight).

## Why parity (as a dashboard/API, not just an internal test)?

- **Problem**: "Neo4j is a rebuildable projection" is an unfalsifiable
  claim without a way to actually compare the two.
- **Options**: (a) trust the materializer's own tests only, (b) expose a
  live, user-facing comparison.
- **Why (b)**: makes the architecture's central claim inspectable by
  anyone, not just by reading test code — directly serves
  `PRODUCT_VISION.md`'s "every AI output is traceable" principle extended
  to the graph itself.
- **Benefits**: Validation 7 in the regression suite is this exact
  mechanism reused as a gate.
- **Drawbacks**: today's parity failures are diagnostic of real upstream
  gaps (Known Gap 4), not of the comparator itself — a parity dashboard
  that shows failures is doing its job correctly even when the failures
  are unwelcome.

## Why a registry (for validators/generators), not a growing if/elif?

- **Problem**: dispatch logic that grows a branch per new validator/
  generator becomes an unreviewable bottleneck and a merge-conflict magnet.
- **Options**: (a) central dispatch function with a branch per type, (b)
  a registry — data, not control flow.
- **Why (b)**: "a new validator is one registry entry, never a second
  dispatch mechanism" — stated as an explicit ADR 0018 invariant, matching
  a pattern already used elsewhere (`CROSS_REPO_LINK_RULES`,
  `KnowledgeSourceSpec`, `ProviderSpec`).
- **Benefits**: adding a validator/generator requires zero changes to
  existing ones — proven directly by parity tests asserting existing
  behavior is unchanged after each addition.
- **Drawbacks**: none named directly; the closest is `shadow_runner.py`'s
  deliberate one-entry non-registry for the deterministic generator (see
  next entry) — a case where a registry was correctly judged premature.

## Why shadow mode (for every new RFC in ADR 0018's roadmap)?

- **Problem**: any change to graph-writing logic risks silently corrupting
  production data.
- **Options**: (a) ship behind a feature flag but wired into the real
  write path, (b) run alongside the real pipeline, logging or persisting
  to a separate store, provably not affecting production output.
- **Why (b)**: every RFC from RFC-02 through RFC-06D states a rollback
  plan that is a one- or two-line revert with zero cascading effect,
  because nothing downstream ever depended on the shadow output.
- **Benefits**: RFC-02B's test suite proves byte-for-byte identical
  `GraphPayload` output whether shadow generation runs or not — not
  assumed, verified.
- **Drawbacks**: slower time-to-value — a capability can be fully built,
  tested, and merged, and still contribute zero production behavior until
  a later RFC flips it on (RFC-06's LLM generator is the clearest current
  example, off by default with no scheduled cutover date).

## Why a hypothesis generator (rather than writing relationships directly)?

Covered in depth: [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md),
[06_FRONTIER_AI.md](06_FRONTIER_AI.md). Summary: separating "propose" from
"commit to the graph" is what makes every non-deterministic source
(today: one LLM generator; future: runtime telemetry, docs, infra
manifests) subject to the identical validation/confidence gate as
deterministic parsing, with no special-cased trust path.

## Why Engineering Memory as a concept distinct from "the graph"?

Covered: [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md). Summary:
cardinality and durability requirements diverge sharply between "what we
observed" (huge, regenerable, blob-appropriate) and "what we currently
believe" (small, relational, needs real indexing) — one store forced to
serve both would compromise one side or the other.

## Why confidence as a computed, six-state model rather than a float?

- **Problem**: a bare `[0,1]` score can't distinguish "one weak signal"
  from "two independent strong signals" without an arbitrary, undocumented
  mapping.
- **Options**: (a) a single float, thresholded ad hoc per consumer, (b) a
  named, ordered state enum with a public, versioned formula.
- **Why (b)**: matches `cross_repo_linker.py`'s pre-existing
  `structural`/`heuristic` distinction, generalized rather than replaced —
  continuity with a proven, already-trusted vocabulary, not a fresh
  invention.
- **Benefits**: monotonicity is checkable and tested in both directions;
  UI/API consumers get a stable, explainable vocabulary instead of
  interpreting a raw number themselves.
- **Drawbacks**: six states is a real design commitment — widening or
  narrowing the state machine later is a formula-version bump with
  downstream consumer impact, not a free change.

## Why a learning engine as a sibling package, never imported by the Knowledge Engine?

- **Problem**: feedback/learning concerns risk creeping into the
  Knowledge Engine's core pipeline and complicating its "pure, deterministic,
  no LLM" invariants.
- **Options**: (a) add feedback handling inside `app.knowledge_engine`, (b)
  a separate package with a one-directional dependency.
- **Why (b)**: keeps the Knowledge Engine's own invariants (validators
  never call an LLM, confidence never reads generator self-reports)
  structurally impossible to violate from the learning side — there's no
  import path for a feedback loop to reach back in and quietly become a
  second confidence-influencing input.
- **Benefits**: `LearningEngineService` reuses `EngineeringMemoryService
  .apply_correction` (RFC-04's own method) rather than duplicating
  correction logic — one write path for corrections regardless of origin.
- **Drawbacks**: none named; the explicit non-goals (automatic prompt
  evolution, calibration, health scoring) are scope decisions, not
  drawbacks of the split itself.

## Why a service layer with an explicit "no LLM, no NL parsing" rule?

Covered: [07_ENGINEERING_INTELLIGENCE.md](07_ENGINEERING_INTELLIGENCE.md).
Summary: keeps the same fact computable identically by any future
consumer (a new agent, a future non-agent API caller) without re-deriving
or re-prompting for it — "if two agents need the same fact, that fact
belongs in the graph [or this layer], not duplicated in two prompts"
(`PRODUCT_VISION.md` Guiding Principle 5, applied literally).

## Why agents with (almost) no business logic?

Covered: [08_AGENTS.md](08_AGENTS.md). Summary: pushes every fact-producing
decision down into layers that are independently testable without a live
LLM (Knowledge Engine, Engineering Intelligence Services), leaving the
agent layer responsible only for orchestration and narration — the
concrete enabler of "adding agent #11 touches its own module and one
registry line, never the orchestrator's core logic."

## Why Bedrock / why Claude (multi-provider posture)

`app.ai.providers` is a factory/registry pattern (`ProviderSpec`), reused
unmodified by the Frontier LLM generator and every agent's `invoke_llm_json`
call. The validation guide's own operational notes confirm both Bedrock
and other providers are real, live configurations (`AI_PROVIDER=bedrock`
is explicitly handled, including a documented expired-STS-token failure
mode) — this is a working multi-provider posture, not a single-vendor
hardcode. No ADR read for this handbook argues for one provider over
another as an architectural stance; the registry pattern is the actual
answer to "why not lock in one provider" — swapping is a config change,
not a rearchitecture.

## Why deterministic parser first, not LLM first?

Covered at length: [02_STORY.md](02_STORY.md), [06_FRONTIER_AI.md](06_FRONTIER_AI.md).
The single clearest one-line summary, quoted directly from ADR 0018:
"Existing deterministic implementations... remain the source of truth for
their respective domains until a future RFC explicitly cuts them over" —
frozen as an invariant at RFC-03 approval, not a temporary bootstrapping
choice.

## Why evidence packs (rather than re-reading source on demand)?

- **Problem**: hypothesis generation, validation, and re-analysis all need
  the same underlying facts; re-fetching/re-cloning/re-parsing per
  consumer is wasteful and non-reproducible (a re-clone days later may see
  different file content if the branch moved).
- **Options**: (a) each stage reads source independently, (b) one
  immutable, content-addressed evidence pack per `(repository_id,
  commit_sha, schema_version)`, all stages read from it.
- **Why (b)**: reproducibility again — "the same evidence pack, run
  through the same generator/validator/confidence-engine versions,
  produces the same result" is a stated engineering invariant, only
  possible if the input itself is frozen and shared.
- **Benefits**: a `KnowledgeValidator`'s "no I/O beyond the pack" rule
  becomes cheap to enforce (there's nothing else to reach for).
- **Drawbacks**: a pack can go stale relative to a fast-moving repository
  between full re-index runs — the exact gap RFC-09's (roadmap-only)
  incremental/delta evidence ingestion targets.
