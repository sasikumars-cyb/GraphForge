# ADR 0015: Engineering Understanding — a cognitive reasoning layer above retrieval

## Status

Accepted.

## Context

ADR 0014 closed the retrieval-quality gap: Context Discovery now produces a
bounded, tiered, composite-scored `EvidencePackage` instead of a raw
component dump. That fixed *what gets retrieved and how it's ranked*. It did
not address a separate problem, named explicitly in this phase's brief:
Context Discovery still hands Planning a pile of curated facts, not
engineering *understanding*. Nothing in the pipeline generated competing
hypotheses about why the current behavior exists, related the ticket's own
words to specific evidence items, distinguished a validated conclusion from
an unvalidated assumption, or challenged its own leading explanation before
handing it off. Planning quality is bounded by Context Discovery quality —
a better-ranked list of facts is still a list of facts.

The instruction for this phase was explicit and narrow: do **not** redesign
retrieval, graph traversal, the evidence package, the capability system, the
investigation loop, composite scoring, or repository scoping — all of that
is production-ready and reused as-is. Build a reasoning layer *above* it.

## Decision

### 1. Two new models, two audiences

- **`InvestigationWorkspace`** (`app/context_pipeline/reasoning/
  understanding.py`) — scratch reasoning: competing `Hypothesis` objects
  (each with supporting/contradicting evidence, a confidence, and a
  `supported`/`rejected`/`unknown` status), open questions, unknowns, dead
  ends, candidate repositories/architecture, and free-form reasoning notes.
  **Never consumed by Planning.** It is written to `state.derived
  ["investigation_workspace"]` for traceability/debugging only and is
  deliberately absent from `ContextDiscoveryResult` (see `schemas.py`'s
  comment on the field it doesn't have).
- **`EngineeringUnderstanding`** — the validated conclusion: business
  objective, current/desired behavior, primary/supporting repositories,
  implementation ownership, architecture relationships, reusable
  components, dependencies, risks, constraints, validated/rejected
  assumptions, remaining unknowns, per-category confidence, and engineering
  insights. This is the only object Planning's prompt is built from.

### 2. One targeted, strictly-grounded LLM call

Everything upstream of this (the investigation loop, the ledger, capability
scoring, curation) is deterministic by design (ADR 0007). Synthesis is not:
"why does this behave this way" and "does this duplicate an existing
abstraction" are judgment calls a token-overlap score cannot make. So this
is a single `invoke_llm_json` call (`app.agents.llm`, the same seam every
other freeform-JSON agent uses — Planning, Development, Testing), run once
per discovery run, in `engine.investigate()` immediately after
`curate_evidence` (both `discover()`'s first pass and `resume()`'s
continuation funnel through the same call site).

The call receives *only* what investigation already gathered — ticket
sections, the enriched retrieved text, live repository-candidate
inferences, the curated evidence package's own rendering, open gaps, and
unvalidated assumptions — and is explicitly instructed (system prompt, 7
numbered ground rules) to: never invent a repository/file/class not named
in that evidence; generate multiple competing hypotheses and actively try
to falsify each one rather than confirm the first; synthesize across
sources instead of listing them independently; keep facts/conclusions/
assumptions/unknowns strictly separate; give per-category confidence, not
one flat number; and self-critique before finalizing.

### 3. Facts vs. conclusions stays structurally enforced, not just documented

The ledger remains the only place a `Fact` can be written (`Ledger.
add_fact` is unchanged and untouched). `EngineeringUnderstanding` is
computed into `state.derived`, the same "rendered artifact, not atomic
knowledge" bucket `evidence_package`/`enriched_text` already live in — it
is never written back as a `Fact` or `Inference`, so a wrong or
low-confidence conclusion from synthesis can never corrupt what the
deterministic capability/confidence system reads.

### 4. Graceful degradation, never a blocked run

If the LLM call fails, times out, or returns invalid JSON,
`synthesize_engineering_understanding` catches the failure and falls back
to `_deterministic_understanding` — a purely mechanical summary built from
already-structured data (evidence package tiers → implementation ownership/
dependencies/reusable components, ticket sections → business objective/
desired behavior, open gaps → remaining unknowns), with an explicit
`remaining_unknowns` entry stating synthesis didn't run. An empty
investigation (no facts, no ticket sections, no evidence items) short-
circuits to the same deterministic path *without calling the LLM at all* —
synthesizing over nothing would be fabrication, not understanding. Neither
path raises; Context Discovery's readiness/confidence verdict is entirely
unaffected by whether synthesis succeeded (mirrors `curate_evidence`'s own
degrade-on-graph-failure precedent).

### 5. Planning migration

`_graph_context_text_from` (`app/agents/planning/agent.py`) now renders
`engineering_understanding` as the primary prompt text when present and
non-empty, with the curated evidence package appended afterward under an
explicit "Supporting evidence (for traceability — not the primary basis for
this plan)" heading. Falls back to evidence-package-only, then to the
oldest raw `graph_context_text`, for any run that predates this field or
where rendering produced nothing — so an already-persisted `AgentStep.
result` (this workflow's most common state) keeps working unchanged.
Development/Testing/Documentation Planning are **not** touched in this
pass — they continue reading `evidence_package` as ADR 0014 left them;
migrating them to `EngineeringUnderstanding` too is a natural follow-on,
not attempted here to keep this change reviewable.

### 6. ADR 0012 persistence, for free

`SessionContext` (`reasoning/investigation.py`) gained one new field,
`agent_context: AgentContext | None`, populated from the real `AgentContext`
by `ContextDiscoveryAgent.run()`. `invoke_llm_json(context=session.
agent_context, purpose="synthesis", sequence=1, ...)` therefore persists an
`LLMInvocation` row automatically, through the same, sole ADR-0012 writer
every other agent's LLM call already uses — no new persistence path
introduced.

## Self-review

Ran the explicit self-critique this phase's brief demanded before calling
this done. Findings:

- **Does it behave like an investigator rather than a search engine?**
  Partially, honestly. It generates and challenges competing hypotheses,
  synthesizes across sources, and separates facts from conclusions — but it
  does so in a *single post-hoc synthesis pass* over evidence the
  deterministic loop already decided was sufficient (by capability
  satisfaction, not by whatever the hypotheses turn out to need). A
  hypothesis that flags "no ingestion evidence exists to rule this out"
  cannot, today, cause the engine to go fetch ingestion evidence — there is
  no feedback path from `InvestigationWorkspace` back into `engine.py`'s
  action-selection loop. A true iterative hypothesis-driven investigator
  (per the brief's own flow diagram: hypothesize → collect → challenge →
  repeat, with each round capable of triggering *new* retrieval) would
  require a deeper change: either a new capability that isn't satisfied
  until synthesis's own unknowns are addressed, or letting synthesis itself
  propose `InvestigationAction`s the engine can pick up next cycle. That is
  a real, deliberately deferred next step, not a silently dropped one — it
  is the single biggest gap between this implementation and the brief's
  full ambition, and the reason this ADR says "a cognitive reasoning layer
  above retrieval" rather than "a fully iterative investigator."
- **Cross-source synthesis** — present: the system prompt requires relating
  ticket requirements to specific evidence items, and the grounding text
  gives the model ticket sections, retrieved prose (Jira/Confluence/PR),
  repository relationships, and curated components together, not as
  separately-labelled dumps.
- **Facts vs. conclusions** — structurally enforced (see Decision §3), not
  merely a naming convention.
- **Would a Principal Engineer investigate anything else?** Development/
  Testing/Documentation Planning still read the older `evidence_package`
  directly rather than `EngineeringUnderstanding` — so today only Planning
  actually benefits from the new reasoning layer. Flagged in Decision §5 as
  an intentional, narrow first migration rather than an oversight.

## What this deliberately does not do

- No feedback loop from `InvestigationWorkspace` back into the
  deterministic investigation loop (see Self-review above) — synthesis
  reasons over whatever evidence gathering already produced; it cannot
  trigger a new retrieval round of its own.
- Development, Testing, and Documentation Planning are not migrated to
  `EngineeringUnderstanding` in this pass — they keep reading
  `evidence_package`, unchanged from ADR 0014.
- No UI rendering of `EngineeringUnderstanding` or `InvestigationWorkspace`
  — both are API/prompt-level only in this pass, same deferral as ADR
  0014's Task 14 (frontend evidence-package rendering).
- Personalized PageRank for proximity scoring remains deferred, per ADR
  0014 — untouched here.

## Files

**New**
- `backend/app/context_pipeline/reasoning/understanding.py` —
  `InvestigationWorkspace`, `EngineeringUnderstanding`, `Hypothesis`,
  grounding-text builder, the synthesis call, deterministic fallback,
  Planning-facing renderer.
- `backend/tests/unit/ai/test_understanding.py`
- `backend/tests/unit/ai/test_planning_engineering_understanding_prompt.py`

**Modified**
- `backend/app/context_pipeline/reasoning/investigation.py` —
  `SessionContext.agent_context`.
- `backend/app/context_pipeline/reasoning/engine.py` — synthesis call after
  `curate_evidence` in `investigate()`.
- `backend/app/context_pipeline/reasoning/projection.py` — `build_result`
  gains `engineering_understanding`.
- `backend/app/agents/context_discovery/agent.py` — threads `context` into
  `SessionContext`.
- `backend/app/agents/context_discovery/schemas.py` — new field.
- `backend/app/agents/planning/agent.py` — `_graph_context_text_from`
  prefers `engineering_understanding`, falls back through
  `evidence_package` to `graph_context_text`.

## Migration / performance / rollback

- **Migration**: additive only. Every new field defaults to `{}`/absent for
  a persisted `AgentStep.result` that predates it; every fallback chain
  (Planning's prompt text, this ADR's own schema fields) resolves to
  exactly the pre-existing behavior when the new field is empty.
- **Performance**: one additional LLM call per discovery run (not per
  investigation cycle) — the same cost class as Planning's own single
  synthesis call, not a multiplier on the investigation loop's cost.
- **Rollback**: delete the `await synthesize_engineering_understanding(...)`
  call in `engine.py`; every consumer already degrades to its pre-existing
  behavior when the field is empty, so no other file needs to change.

## Test plan

- `test_understanding.py`: empty-investigation short-circuit (no LLM call),
  successful synthesis (workspace + understanding both populate, hypothesis
  statuses preserved), LLM failure degrades without raising, malformed JSON
  degrades without raising, renderer empty/non-empty.
- `test_planning_engineering_understanding_prompt.py`: understanding-first
  ordering with evidence appended, understanding-alone, fallback to
  evidence-package when understanding is empty or renders to nothing,
  malformed understanding dict falls back without raising.
- Full backend unit suite (1239 passed, 8 pre-existing unrelated DB-fixture
  errors) — zero regressions from this change.
