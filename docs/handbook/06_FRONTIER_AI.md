# Section 6 — Frontier AI

Source: ADR 0018 RFC-06/06B/06C, `app/indexer/hypotheses/llm_generator.py`,
`app/knowledge_engine/contracts/generator_policy.py`,
`app/agents/frontier/`.

Two unrelated things share the name "Frontier" in this codebase and must
not be conflated:

1. **The Frontier Hypothesis Generator** (RFC-06) — an LLM
   `HypothesisGenerator` inside the Knowledge Engine, proposing capability
   relationships from a repository's own evidence.
2. **Frontier Agents** (`app.agents.frontier`) — the shared
   `BaseFrontierAgent` run loop for the read-only Engineering Intelligence
   Agents (Repository Understanding, Impact Analysis, Dependency Query).
   These consume already-computed service results and narrate them; they
   do not generate hypotheses or write to the graph. See
   [08_AGENTS.md](08_AGENTS.md).

This section is primarily about (1).

## LLM Generator

`llm_generator.py` calls `app.ai.providers.factory` (multi-provider LLM
infrastructure, reused unchanged) to produce `Hypothesis` objects from a
repository's own `EngineeringEvidencePack`. Scoped deliberately narrow for
v1: **single-repository claims only** — the generator's only input is one
repository's own pack, with no knowledge of what other repositories exist.
A cross-repository LLM generator (given two repositories' evidence the way
RFC-05's `build_candidate_pack_and_hypotheses` already is) is named as
natural future work, not built.

## Vocabulary: deliberately fixed and small, not LLM-invented

13 `OWNS_*` / `CONTAINS_*` / `INTEGRATES_WITH_*` capability relationship
types. Source is always the repository's own node; target is a synthetic
`{repository_id}:capability:{slug}` entity. Kept fixed so the same
repository, re-analyzed on a later commit, converges on the same entity id
— required for RFC-04's `relationship_key` versioning to mean anything.

## Why the generator needed a new evidence source first (Finding 1)

`HypothesisGenerator.generate(pack)` correctly takes only the evidence
pack — but no evidence of README/manifest/config content existed anywhere
in the pack, and the cloned repository is torn down before any generator
runs. Resolved additively, not by reworking the interface:
`app.indexer.hypotheses.repository_evidence.extract_repository_evidence`
runs inside `index_repository`'s existing clone-lifetime block, producing
new, generator-agnostic evidence kinds (`repository_readme`,
`repository_manifest`, `repository_architecture_doc`, `repository_config`,
`repository_metadata`) via a small, explicit, safety-conscious filename
allowlist — **never** `.env` or key/credential-shaped files. Not
LLM-specific: it's merged into the same pack every generator already
reads, and the deterministic generator is provably unaffected (it only
ever reads `graph_node:*`/`graph_edge:*` kinds).

## Why the generator is opt-in, not automatic (Finding 2 — `GeneratorPolicy`)

`GeneratorPolicy.should_run(context) -> bool` is a single async decision
point, deliberately not a plain `enabled: bool` field — so
manual/scheduled/webhook-triggered/budget-limited/premium-only execution
modes are each a *new implementation* later, never a rewrite of
`shadow_runner.py`'s loop. `StaticGeneratorPolicy` (the only concrete
implementation needed so far) reads `Settings.enable_frontier_llm_generator`,
**default `False`** — specifically so a real LLM call, its cost, and its
new external-dependency failure mode are opt-in, never silently added to
every production indexing run. The registry only calls the generator
`factory` after the policy passes, so `create_llm_provider()` — which
validates API-key configuration — never runs, and never fails, while the
feature is off.

## Hallucination protection — the actual mechanism, not a slogan

Layered, and each layer is independently verifiable:

1. **Structural**: the generator can only propose relationships in the
   13-type fixed vocabulary; it cannot invent a new relationship type.
2. **No direct write**: same invariant as every other generator — it
   returns `list[Hypothesis]`, full stop; it never touches
   `IGraphRepository`.
3. **Confidence ceiling**: proven directly in
   `test_frontier_llm_generator_pipeline.py` — with **zero** validator
   coverage (RFC-06's initial state), every LLM hypothesis lands at
   `ConfidenceState.CANDIDATE`, "no validator recognizes this vocabulary
   yet — correct, not a gap to close here." A hallucinated or unsupported
   claim simply never rises above `CANDIDATE`.
4. **Evidence-grounded promotion, not self-assertion**: RFC-06B's
   `EvidenceKeywordValidator` family only promotes a hypothesis from
   `CANDIDATE` toward `LIKELY` when the hypothesis's own *cited evidence
   text* deterministically contains a recognized technology keyword —
   promotion is earned from the evidence the hypothesis pointed at, not
   from the LLM's self-reported confidence, which never enters the
   formula at all (§ [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md)).
5. **Structural ceiling on trust from one source, period**: no
   LLM-sourced relationship can reach `Verified` from a single provider's
   agreement alone (multi-source/multi-provider corroboration required —
   ADR 0018 § Consequences).
6. **Materializer never surfaces unpromoted candidates**: proven directly,
   not assumed, in the RFC-06 test suite — a `CANDIDATE`-state LLM
   hypothesis is stored in Engineering Memory but never appears in the
   materialized graph.
7. **Never surfaced by the deterministic pipeline's own output**: the
   deterministic parser path is provably unaffected by whether the LLM
   generator ran at all.

## Why validators (again, specific to this generator)

Before RFC-06B, every Frontier hypothesis sat at `CANDIDATE` regardless of
evidence quality — a real, acknowledged limitation, not framed as
acceptable permanently. RFC-06B closed it with zero changes to any
existing validator, the `KnowledgeValidator` interface, or persistence/
confidence code — purely additive. The fix is proven with a real
before/after test: an LLM hypothesis whose cited manifest evidence a
validator can confirm rises from `CANDIDATE` to `LIKELY`.

## Why candidate relationships exist at all (rather than rejecting anything unconfirmed)

Because `CANDIDATE` is informative, not an error state — a repository
capability the LLM found real signal for, that no deterministic validator
yet has a keyword table for, is still worth surfacing distinctly from
"we found nothing" or "we found something and it's wrong." The state
machine (§ [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md)) treats
`CANDIDATE` as the honest default for "no confirmations, no
contradictions" — visible in the UI/API, clearly labeled, never presented
with the same trust as `VERIFIED`.

## Why verified relationships require evidence (restated precisely)

Not "the LLM said so with high confidence" — `VERIFIED` requires ≥2
distinct confirming source types at the highest reliability tier, computed
entirely from independent `ValidationResult`s the `ConfidenceEngine`
aggregated. This is the same rule for every generator, LLM included — no
special-cased path to `Verified` exists for LLM-sourced hypotheses, and
none is planned; RFC-08 (multi-provider consensus, roadmap) explicitly
caps cross-provider agreement at "at most one `distinct_confirming_source_type`,
never two, per the correlated-training-data caveat" — even stacking two
LLM providers doesn't buy a shortcut to `Verified` on its own.

## Prompt strategy

Not detailed at the LLM-prompt-template level in the ADRs read for this
handbook beyond the evidence-curation discipline (§
[05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md) § Evidence curation for
the LLM path) — curated, budgeted, kind-diverse sampling of the evidence
pack, never a raw dump. For the separate Context Discovery synthesis
prompt (a different system — see [03_ARCHITECTURE.md](03_ARCHITECTURE.md)),
ADR 0015 documents seven numbered system-prompt ground rules directly:
never invent a repository/file/class not named in the evidence; generate
multiple competing hypotheses and actively try to falsify each rather than
confirm the first; synthesize across sources rather than list them
independently; keep facts/conclusions/assumptions/unknowns strictly
separate; give per-category confidence, not one flat number; self-critique
before finalizing.

## Status as of this audit

Implemented (2026-08-02) and gated off by default
(`enable_frontier_llm_generator=False`). Precision/recall measurement and
cost-per-run budgeting are explicitly deferred until the feature is
actually enabled for real repositories — named directly as "out of this
RFC's scope, which was proving the plugin mechanism, not evaluating it."
Treat any claim of measured LLM-generator precision as unverified until
that evaluation exists.
