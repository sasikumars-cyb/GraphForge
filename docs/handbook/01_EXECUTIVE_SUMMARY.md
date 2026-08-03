# Section 1 — Executive Summary

Every version below is the same claim at different resolution. None
contradicts another; each adds detail the shorter one omitted.

## 30 seconds

GraphForge indexes an organization's repositories into a knowledge graph,
then runs a pipeline — **Evidence → Hypothesis → Validation → Confidence →
Knowledge** — that only promotes a relationship into "known" once it has
been corroborated by independent, deterministic evidence. Deterministic
parsing (tree-sitter over Java/Spring Boot and Python) is the backbone; an
LLM can propose additional relationships, but only as one more hypothesis
that has to survive the same validators as everything else — it never
writes to the graph directly. Every fact is explainable and traceable back
to source evidence. (ADR 0007, ADR 0018.)

## 2 minutes

GraphForge grew out of ChangeGuard, a deterministic PR-impact tool: given a
diff, traverse a dependency graph and tell you what's downstream. That
proved the core thesis — a real graph plus grounded reasoning beats an LLM
reading a diff cold. GraphForge generalizes it: instead of one hardcoded
graph-writing path, there are now five explicit pipeline stages
(`app/knowledge_engine/`), each with its own typed contract
(`contracts/provenance.py`, `evidence.py`, `hypothesis.py`, `validation.py`,
`confidence.py`, `knowledge.py`). A `HypothesisGenerator` — deterministic
parser, rule-based extractor, or LLM — only ever proposes; a
`KnowledgeValidator` — always deterministic, never an LLM — checks a
hypothesis against its own cited evidence; a `ConfidenceEngine` aggregates
validator verdicts into one of six states (`verified` down to `rejected`),
incrementally and monotonically. The result is `EngineeringMemory`: an
append-only Postgres log of every hypothesis, validation, and confidence
transition ever computed, which Neo4j is *derived from* — a rebuildable
projection, not the system of record. On top of this sits an Engineering
Intelligence Service Layer (repository profiles, blast-radius impact
analysis, dependency queries) and a set of read-only agents that render
those services' output through an LLM narrative layer, never inventing
facts the services didn't compute.

## 5 minutes

Add: this is not one finished system, it's a still-in-progress RFC
sequence (ADR 0018), and the document says so explicitly — RFC-01 through
RFC-06D are implemented (core contracts, shadow-mode deterministic and LLM
generators, validators, confidence, Engineering Memory persistence,
cross-repo integration, materialization, explainability, a learning/
feedback loop); RFC-07 through RFC-09 (first non-parser language, multi-
provider consensus, incremental evidence ingestion) are roadmap, not built.
Two things keep this honest rather than aspirational: (1) a 24-repository
regression validation suite (`graphforge-validation/`) that runs GraphForge's
real APIs against captured expected state and documents its own findings —
including real, current gaps like "0 cross-repository CALLS_SERVICE edges
because Feign name-matching can't bridge a `<domain>-service-<language>`
naming convention" — as the baseline, not swept under the rug; (2) an
explicit architectural invariant that LLM output can never reach `Verified`
confidence alone — it requires independent, non-LLM corroboration, which
structurally bounds both hallucination risk and LLM cost growth. The
tradeoff this buys: slower to "the graph knows everything" than a
pure-LLM/GraphRAG approach, in exchange for every claim being falsifiable
and reproducible.

## 10 minutes

Add the two supporting layers this shorter framing skips: **Context
Discovery** (ADRs 0007, 0010, 0013–0017), a separate but related reasoning
pipeline that takes a free-text engineering request (a Jira ticket, a plain
question), runs a deterministic Plan→Select→Execute→Observe→Decide
investigation loop over the graph and integrations, curates the result into
budget-bounded evidence tiers, and only then runs a single, strictly-
grounded LLM synthesis call to produce `EngineeringUnderstanding` — with a
mid-loop checkpoint (ADR 0016) letting a hypothesis redirect the rest of
the investigation, bounded to exactly one extra LLM call per run for
measured cost reasons. And **Engineering Session** (RFC-001), a separate
aggregate for structured human/agent collaborative reasoning — Beliefs,
Hypotheses, Evidence, Recommendations, Decisions, Contradictions — with a
hard-enforced propose/commit boundary (only a human can commit a Decision,
enforced at both the service layer and the API schema). Both reuse the same
underlying discipline as the Knowledge Engine: deterministic before
probabilistic, structured evidence before assertion, append-only history.
The org-level thesis (`PRODUCT_VISION.md`): "the graph is the product, the
agents are features of the graph" — every shippable feature must name a
graph node/edge type it reads or writes, or it doesn't ship.

---

## By audience

**Developer** — "You write one `KnowledgeValidator` and it applies to
every hypothesis whose `relationship_type` is in `applies_to`, forever, no
dispatch code to touch (`app.knowledge_engine.validators.registry`). Same
for a `HypothesisGenerator` — one class, register it, its own failure never
breaks anyone else's output (`run_indexing`/`relink_account`'s existing
failure-isolation pattern, reused, not reinvented)."

**Architect** — "The interesting decision is the persistence inversion:
Neo4j moves from system-of-record to derived, rebuildable projection; the
append-only Postgres log is the actual source of truth. That's what makes
`materializer.py`'s replay-and-diff test possible at all, and it's what
gives you real confidence history instead of a single mutable `confidence`
column."

**VP Engineering** — "Every AI claim carries a confidence state and a
pointer to the evidence that produced it — verified, not asserted. The
24-repo validation suite is the acceptance gate; it fails loudly, in CI,
the moment a change regresses precision, and its own documented gaps (see
[16_REALITY_CHECK.md](16_REALITY_CHECK.md)) are the actual current risk
register, not a hidden one."

**CTO** — "The bet is that deterministic-first, evidence-gated knowledge
compounds where prompt-only tools reset every session. The cost is real
engineering discipline — every new evidence source is a registry entry
that, once used, can never be renamed, only deprecated — and real latency
to full coverage: LLM-sourced relationships are structurally capped below
`Verified` until a second, independent source corroborates them."

**AI Researcher** — "The system enforces a strict separation that a lot of
RAG/agent architectures blur: `generator_confidence` (whatever a
`HypothesisGenerator` — including the LLM one — reports about itself) is
advisory and, by written invariant, must never influence a validator's
verdict or the `ConfidenceEngine`'s aggregation. Confidence is derived
solely from independent `ValidationResult`s. `KnowledgeValidator`s are pure
functions of `(Hypothesis, EvidencePack)` with a stated rule that a
validator calling an LLM 'isn't validating, it's generating a second,
uncoordinated hypothesis.'"

**Hackathon Judge** — "Ask to see [16_REALITY_CHECK.md](16_REALITY_CHECK.md)
first. This team documents its own gaps with the same rigor as its
features — the validation suite ships four *named, numbered, root-caused*
current limitations (Kafka topic literal-only detection, Feign
cross-repo name matching, impact analysis never leaving the seed
repository, dependency-query's direct-dependency count being intra-
repository noise) instead of hiding them. That's the signal to weight
heavily: does the demo match what the docs admit, or does it paper over
it?"
