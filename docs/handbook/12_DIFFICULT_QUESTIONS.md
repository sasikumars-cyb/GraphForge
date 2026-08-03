# Section 12 — Difficult Questions

The "why not X" gauntlet. Each answer is grounded; where the honest answer
is "we haven't measured that" or "that's a real gap," it says so.

## Why not use Neo4j as the source of truth?

Because "source of truth" for this platform means append-only,
audit-able, never-silently-mutated history — and a graph database
optimized for fast traversal is not naturally shaped for that. ADR 0018's
own consequence list states the choice directly and its cost: Postgres
carries unbounded row growth for `Hypothesis`/`ValidationResult`/
correction/confidence-history by design; Neo4j becomes disposable and
rebuildable. **Honest counterpoint**: this inversion is proven (materializer
replay test, RFC-05B) but not yet load-bearing in production — Neo4j is
still written directly by `replace_repository_graph` today. If asked "so
is Neo4j actually derived right now," the accurate answer is "provably
*can be*, not yet *is*."

## Why not vector search / embeddings?

Not addressed by any ADR read for this handbook as a rejected alternative
with stated reasoning — there is no embedding-based retrieval anywhere in
the indexed codebase paths this audit covered. The closest adjacent
decision is ADR 0014's explicit deferral of *semantic* relevance scoring:
a documented, real limitation ("SCD2" vs. `SCDType2Merger` can't be
connected by prefix matching) is left unresolved specifically because
closing it "needs semantic/embedding similarity... an explicit, separate
trade-off (adds new infra, and introduces genuine non-determinism into a
codebase whose stated precedent is deterministic, no-guessing extraction)
that this ADR deliberately does not decide unilaterally." **Honest
answer**: embeddings were considered exactly once, for one narrow scoring
problem, and deliberately not adopted — not because vector search is
rejected on principle, but because it wasn't judged worth the
non-determinism trade-off for that specific gap, in that specific ADR.
Don't over-claim a platform-wide anti-embedding stance beyond what's
written.

## Why not GraphRAG?

Structurally, GraphForge's Knowledge Engine already resembles what
GraphRAG proposes at the retrieval layer (graph-grounded context), but
diverges sharply at the write layer: GraphRAG-style systems typically treat
LLM-extracted relationships as directly graph-writable; GraphForge's
central architectural rule is the opposite — a `HypothesisGenerator`
(LLM included) never writes to the graph, and an LLM-sourced relationship
cannot reach `Verified` from a single provider's agreement alone. If the
question means "why not just use an existing GraphRAG library instead of
building this," the implicit answer from the ADRs is that the platform's
core requirement — validator-gated, non-LLM confirmation before promotion
— is not what off-the-shelf GraphRAG tooling provides; it optimizes for
retrieval quality, not for the evidence-over-assertion guarantee
`PRODUCT_VISION.md` states as Core Principle 2.

## Why not JanusGraph / Amazon Neptune / CosmosDB?

Not discussed in any ADR read for this handbook. **This is not implemented
or decided in the current GraphForge codebase's documentation** — do not
answer this from general knowledge about graph database trade-offs as if
it were GraphForge's own reasoning. The honest answer in review: "the
architecture docs name Neo4j and don't comparison-shop alternatives in
writing; if pressed, the actual dependency is on `IGraphRepository`
(`app/graph/interfaces.py`), which ADR 0007 states explicitly is
graph-store-agnostic — 'the indexer never imports Neo4j directly... a
non-Neo4j graph store would only mean a new class here.'" That's a real,
verifiable architectural answer about *switching cost*, not a comparison
of Neo4j vs. alternatives.

## Why not Elasticsearch?

Same answer shape as above — not discussed. If the question is really
"how would full-text/fuzzy search fit," the closest existing capability is
`DependencyQueryService.search`'s keyword filter over already-materialized
relationships (deterministic substring matching, not a search index) —
not a general-purpose search engine, and no ADR proposes adding one.

## Why not Cursor / Sourcegraph / GitHub Copilot?

These are IDE-embedded, session-scoped code-intelligence tools with no
persistent, cross-session, cross-system knowledge store — exactly the gap
`PRODUCT_VISION.md`'s competitive-positioning table names generically
("chatbot-style AI... memory across sessions: No"). The honest, specific
distinction: those tools answer "what does this code do" from the code
alone, in the moment; GraphForge's thesis is that the answer to "what
breaks if I ship this" requires cross-system context (tickets, docs,
prior incidents, ownership) that a single-repo, session-scoped tool
structurally can't hold. GraphForge does not claim to replace code
completion or in-editor chat — it claims a different, complementary job.

## Why not simply ask Claude/an LLM over the repository directly?

This is the ChangeGuard-era founding question, and the answer is empirical,
not theoretical: `PRODUCT_VISION.md` states "ChangeGuard proved the thesis
at PR-review scale: a deterministic dependency graph plus a tool-using LLM
agent produces materially better change-impact analysis than prompting an
LLM with a diff alone." The mechanism for *why*: an LLM reading a diff cold
has no reliable way to know what's downstream of a changed file across
repositories it can't see in the prompt window, and no way to distinguish
a confirmed fact from a plausible-sounding guess — which is exactly what
the Evidence→Hypothesis→Validation→Confidence pipeline exists to
structurally prevent (§ [06_FRONTIER_AI.md](06_FRONTIER_AI.md), hallucination
protection layers).

## Why not use only MCP?

Not addressed in any document read for this handbook — GraphForge's own
tool-use pattern (`app.agents.llm.invoke_llm_json`, `ToolRegistry`
per-agent) predates and is independent of MCP as a protocol choice.
**Not implemented/decided in the current codebase's documentation** as a
rejected-alternative discussion; do not invent a rationale.

## Why not use only LLMs (no deterministic layer at all)?

Directly answered, repeatedly, across every ADR in this handbook:
`PRODUCT_VISION.md` Core Principle 3 — "deterministic before
probabilistic... reserve the LLM for judgment calls layered on top of
exact facts — never for facts themselves." The concrete cost of not doing
this is named directly: an LLM-only system has no mechanism to distinguish
a confirmed dependency from a hallucinated one, and no audit trail
explaining why a claim should be trusted. GraphForge's entire validator/
confidence architecture is the answer to this question in code form.

## How does GraphForge avoid hallucinations? (the compressed version)

Six concrete, testable mechanisms — full detail in
[06_FRONTIER_AI.md](06_FRONTIER_AI.md): (1) fixed, closed vocabulary for
LLM-generated relationship types; (2) no direct graph write from any
generator; (3) new LLM hypotheses default to `CANDIDATE`, the lowest
confidence state, until independently confirmed; (4) promotion requires a
deterministic validator matching the hypothesis's *own cited evidence*,
never the LLM's self-reported confidence; (5) `Verified` requires ≥2
independent confirming source types, structurally excluding "the LLM said
so, confidently" as a path to full trust; (6) the materializer never
surfaces an unpromoted candidate into the live graph projection.

## How does it scale?

Answered honestly, not optimistically. Indexing does **not** scale past "a
handful of repos per org" today — `ROADMAP.md` Technical Debt states this
directly, full-clone-per-index with no incremental re-indexing (ADR 0007
Consequences). Graph traversal is hop-bounded per agent
(`max_graph_hops`) specifically to keep Context Assembler/service-layer
latency predictable as the graph grows. Multi-tenancy via
`organization_id` scoping is described in `ARCHITECTURE.md` as a
requirement but this handbook did not independently verify its current
implementation depth — treat as partially verified, not confirmed
end-to-end. Run concurrency uses `asyncio.gather` with a per-run
concurrency cap in the architecture doc's design, not independently
re-verified here against production load.

## What breaks first (under real growth)?

The most defensible, code-grounded answer: the indexer's full-clone model
(no incremental indexing) and the same-repository traversal filter behind
Impact Analysis (Known Gap 3) — both are already-documented limits, not
speculation. A secondary, structurally-implied risk: `EngineeringMemory`'s
unbounded row growth for `Hypothesis`/`ValidationResult`/confidence-history
is accepted "by design," which means write/storage cost scales with
re-index frequency × repository count with no compaction yet — named in
ADR 0018 Consequences as accepted, not as risk-free.

## Biggest technical debt?

Per `ROADMAP.md`'s own explicit list: full-clone-per-index not scaling
past a handful of repos, and `GET .../ai-analysis` not exposing
`release_coordination_plan`. Per the validation suite: the Feign
cross-repository name-matching gap, since it silently zeroes out the
product's headline "cross-repository reasoning" claim for a completely
realistic naming convention.

## Hardest architectural decision?

Defensibly: the RFC-01 contract amendments found *during* RFC-03
implementation (`evidence_reliability_tier`, `confirming_source_types`,
`max_confirming_reliability_tier`) — because they demonstrate the team
found its own original contract was "literally uncomputable as specified"
only once they tried to implement the confidence formula against it, and
chose to fix and document the contract rather than quietly work around the
gap in the engine. That's a harder call than any single "which database"
decision: it required admitting a frozen, already-shipped contract (RFC-01)
was wrong.

## What would you redesign?

The most defensible, non-speculative answer draws directly from the
codebase's own self-criticism, not outside opinion: cut the materializer
over to be the actual Neo4j write path sooner, so "Neo4j is a derived
projection" stops being a proven-but-dormant capability and becomes what
actually happens on every index. The ADRs themselves flag the runner-up:
ADR 0015's self-review names the single biggest gap in Context Discovery
as the missing feedback loop from hypothesis to fresh retrieval — "a real,
deliberately deferred next step, not a silently dropped one."
