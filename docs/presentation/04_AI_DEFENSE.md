# 04 — AI Defense (Presenter 3)

## Why AI

Two roles, never blurred: **hypothesis generation** (one more generator
alongside deterministic parsers, proposing what the deterministic path
can't reach) and **narrative synthesis** (turning already-computed facts
into prose). Neither role lets AI decide what's true —
`PRODUCT_VISION.md` Core Principle 3.

## Why deterministic-first

The repeated, load-bearing invariant across every ADR: a
`HypothesisGenerator` never writes to the graph; a `KnowledgeValidator`
never calls an LLM; `generator_confidence` is advisory and structurally
forbidden from influencing validation or confidence aggregation; an
LLM-sourced relationship can't reach `Verified` from one provider's
agreement alone. Say the origin: ADR 0007's Java parser was "fully
deterministic — no heuristics beyond literal matching" before any LLM
existed in the system; ADR 0018 generalized that one parser's discipline
into a platform-wide rule.

## Why validators

The anti-hallucination mechanism, structurally, not by policy. A
validator is a pure function of `(Hypothesis, EvidencePack)` — no network
I/O beyond the pack, and never an LLM call: "a validator that itself asks
an LLM 'does this seem right' isn't validating, it's generating a second,
uncoordinated hypothesis." One reusable `EvidenceKeywordValidator` class,
instantiated per evidence domain, deterministic substring matching against
a small keyword table — recognizing a new technology is a keyword-table
entry, never a new class.

## Why confidence (six states, not a float)

A bare `[0,1]` score can't distinguish "one weak signal" from "two
independent strong signals" without an arbitrary mapping. Six named
states (`verified` → `rejected`), computed by `DefaultConfidenceEngine`:
deterministic, incremental (folds one new result at a time, never
re-scans history), monotonic (a confirmation only strengthens, a
contradiction only weakens, neither regresses the other). Proven — not
just designed — via a parity test that reproduces the pre-existing
`cross_repo_linker.py`'s hand-assigned `structural`/`heuristic` labels
exactly.

## Why explainability

Audited before building: the confidence engine already performs real
evidence fusion (deduplicated by domain, cross-domain-weighted). The
actual gap was narration — turning a final `ConfidenceModel` into a
human-readable "why." `explain_confidence()` is pure and deterministic,
never recomputes state, references the engine's own public thresholds
directly so the explanation can never drift out of sync with the real
formula.

## Why Bedrock

Multi-provider by design (`app.ai.providers` registry — Bedrock, OpenAI,
Gemini, Groq, one entry each, no vendor string-comparisons scattered
through the app). Bedrock specifically for the credential story: zero
static AWS keys, resolved through boto3's default credential chain via
the ECS Task Role — confirmed directly in the provider's own module
docstring.

## Prompt strategy

Curated, budgeted, kind-diverse evidence sampling — never a raw evidence-
pack dump (`app.knowledge_engine.evidence_curation`, RFC-06). For Context
Discovery's synthesis prompt specifically: seven numbered system-prompt
ground rules (ADR 0015) — never invent an entity not named in the
evidence; generate and actively try to falsify competing hypotheses;
synthesize across sources; keep facts/conclusions/assumptions/unknowns
strictly separate; per-category confidence, not one flat number;
self-critique before finalizing.

## How hallucinations are reduced — the six-layer answer

1. Fixed, closed relationship vocabulary (13 types) — an LLM can't invent
   a new relationship type.
2. No direct graph write from any generator, LLM included.
3. New LLM hypotheses default to `CANDIDATE` — the lowest state — until
   independently confirmed.
4. Promotion requires a deterministic validator matching the hypothesis's
   *own cited evidence*, never the LLM's self-reported confidence.
5. `Verified` requires ≥2 independent confirming source types —
   structurally excludes "confidently asserted by one model" as a path to
   full trust.
6. The materializer never surfaces an unpromoted candidate into the
   projected graph.

## How Engineering Memory works (one paragraph)

Append-only Postgres log. `Hypothesis`/`ValidationResult`/`UserCorrection`/
confidence transitions are never edited, only superseded. "Current" state
is a read-time computation (latest `sequence` per `relationship_key`) over
immutable history — never a second, independently mutable source of
truth. Full detail: `docs/handbook/04_ENGINEERING_MEMORY.md`.

## How the Learning Engine works

`app.learning_engine` — a sibling package to the Knowledge Engine, never
imported by it (one-directional dependency, by design, so a feedback loop
can never quietly become a second confidence-influencing input). A human
approving/rejecting/correcting a relationship reuses `EngineeringMemoryService.apply_correction`
(RFC-04's original method, unmodified). Explicitly not built:
automatic prompt evolution, calibration, health scoring — all read-ready,
none implemented. Say this proactively if asked "does it learn over
time" — the honest answer is "it captures the data to learn from; the
learning loop itself is roadmap."

---

## Comparisons

**vs. Copilot**: different layer entirely — in-editor completion vs.
cross-repository, persistent, evidence-graded reasoning. Not a
replacement, no overlap in job.

**vs. Claude/raw LLM chat**: a raw LLM reading a diff cold has no reliable
way to know what's downstream across repositories it can't see in a
prompt window, and no mechanism to separate a confirmed fact from a
plausible guess. That's exactly the gap the validator/confidence pipeline
closes.

**vs. Cursor**: session-scoped, single-repo code intelligence. No
persistent cross-session knowledge store — the exact gap
`PRODUCT_VISION.md`'s competitive table names.

**vs. GraphRAG**: the critical divergence is the write path. GraphRAG-
style systems generally let LLM-extracted relationships become graph
facts directly. We require independent, non-LLM confirmation before
promotion — retrieval quality isn't the optimization target, evidence-
gated trust is.

**vs. vector search / embeddings**: not adopted platform-wide. The one
place it was seriously considered (ADR 0014, ticket-to-component
relevance scoring) was deliberately deferred — closing that specific gap
"needs semantic/embedding similarity... introduces genuine
non-determinism into a codebase whose stated precedent is deterministic,
no-guessing extraction," and that trade-off wasn't made unilaterally.
Don't claim a platform-wide anti-embedding stance beyond this one
documented instance.

**vs. Knowledge Graph tools generally (Neptune/JanusGraph/etc.)**: not a
documented comparison in our own ADRs — say so honestly if asked. The
real, verifiable answer is about switching cost, not a feature
comparison: `IGraphRepository` is graph-store-agnostic by design (ADR
0007) — swapping Neo4j for another graph store is one new class, not a
rearchitecture.

---

## Expected AI questions with answers

**Q: What stops the LLM from just making up a dependency?**
A: It can propose one (as a `Hypothesis`), but it lands at `CANDIDATE`
confidence and is invisible to the materialized graph unless a
deterministic validator independently confirms it against the evidence
the LLM itself cited — and even then it can't reach `Verified` alone.

**Q: How do you measure hallucination rate / precision?**
A: Not yet measured for the LLM generator specifically — it's shipped
gated off by default (`enable_frontier_llm_generator=False`), and
RFC-06 explicitly deferred precision/recall measurement to a future,
real-usage evaluation. Say this directly — it's the correct, honest
answer, not a gap to paper over.

**Q: Why not just fine-tune a model on your codebase?**
A: Not attempted or proposed in any ADR — the deterministic-first
architecture solves the "how do we trust this" problem structurally,
without needing a fine-tuned model's behavior to be independently
verified. Fine-tuning would still need the same validator gate to be
trustworthy, so it doesn't remove the need for this architecture.

**Q: Does confidence ever go down?**
A: Yes — `CONFLICTING`/`REJECTED` states exist precisely for
contradicted hypotheses, and this is monotonic and tested in both
directions (a contradiction can weaken state; it never spuriously
regresses an unrelated confirmation).

**Q: What's your actual LLM cost control?**
A: `cost_class` per agent manifest, opt-in `GeneratorPolicy` gating for
the LLM generator (off by default), and a measured, hard cap on Context
Discovery's mid-loop synthesis calls (`MAX_MID_LOOP_SYNTHESIS_CALLS = 1`,
chosen after literally measuring test-suite runtime impact — 92s → 165s
at a budget of 2).
