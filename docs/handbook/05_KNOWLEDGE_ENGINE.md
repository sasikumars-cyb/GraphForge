# Section 5 — Knowledge Engine

Source: ADR 0018 in full, `app/knowledge_engine/*`. This is the platform's
central pipeline; most of this handbook's other sections sit on top of it.

## The five stages, in order

```
Evidence  →  Hypothesis  →  Validation  →  Confidence  →  Knowledge
```

A deterministic-generator hypothesis with no contradicting validator
result still passes through the *same* pipeline — it clears the trivial
"no contradiction, generator class permits auto-verification" validator in
O(1) rather than the full suite. One mental model, one code path; cheap
cases stay cheap by filtering which validators apply, never by forking
the pipeline into a separate fast path.

## Evidence (`contracts/evidence.py`, `app/indexer/evidence/`)

`EvidenceItem` — content-addressed (`id = hash(kind, reference, raw_value)`),
carries a static `reliability_tier` looked up by `kind` (never computed
per-instance), a `reference` (repo/source_type/locator/line/key/commit),
and full `Provenance` (which generator produced it, when, what pack/run
version). `EngineeringEvidencePack` bundles items per `(repository_id,
commit_sha, schema_version)` as one unit; `is_delta=True` marks an
incremental append (RFC-09, roadmap — not yet built for any source).
`EvidenceItem.kind` and `EvidenceReference.source_type` are **open
registries**, not closed enums — the same pattern
`app.knowledge.registry.KnowledgeSourceSpec` and
`app.ai.providers.registry.ProviderSpec` already use elsewhere in the
codebase. Once used by any persisted record, a value is never renamed,
only deprecated.

## Hypothesis (`contracts/hypothesis.py`, `app/indexer/hypotheses/`)

`HypothesisGenerator.generate(pack) -> list[Hypothesis]` is the entire
interface. Three concrete generators exist:

- `deterministic_generator.py` — adapts `SpringBootJavaParser`/`PythonParser`
  output; `generator_confidence` implicit-Verified (RFC-02).
- `rule_generator.py` — adapts the feign/kafka/controller extractors.
- `llm_generator.py` — the Frontier Hypothesis Generator (RFC-06). See
  [06_FRONTIER_AI.md](06_FRONTIER_AI.md).

**Hard invariant, repeated verbatim across the ADR**: a `HypothesisGenerator`
proposes relationships only; it never writes to a graph repository or any
persistence layer directly. This applies identically to the LLM generator
— it is "one more generator kind," not a privileged writer.

`shadow_runner.py` currently hardcodes one call to
`DeterministicParserHypothesisGenerator` and contains **no**
`GeneratorRegistry` for it — a deliberately documented non-abstraction:
"a registry with one entry is not an interface earning its keep." RFC-06
introduced a second, parallel registry loop (`generator_registry.py`) for
every *other* generator (today: the LLM generator) with the same failure-
isolation guarantee, reached via two isolated loops rather than one
unified one — an explicit, reasoned trade-off, not an inconsistency.

## Validation (`contracts/validation.py`, `app/knowledge_engine/validators/`)

`KnowledgeValidator.validate(hypothesis, pack) -> ValidationResult` —
verdict is `confirms` / `contradicts` / `no_signal`. Three deterministic
invariants enforced by design, not just convention:

1. A validator reads only the `Hypothesis` and its own `EvidencePack` —
   no other input, no network I/O beyond what's already in the pack.
2. **Never calls an LLM.** Stated reasoning: "a validator that itself asks
   an LLM 'does this seem right' isn't validating, it's generating a
   second, uncoordinated hypothesis."
3. Pure and side-effect-free.

Three validator families compose into `ALL_VALIDATORS`
(`validators/registry.py`):

- `deterministic_structural.py` — the baseline family.
- `cross_repo.py` (RFC-05) — `CROSS_REPO_VALIDATORS`, reusing
  `cross_repo_linker.py`'s existing structural/heuristic distinction
  without modifying that module.
- `evidence_keyword.py` (RFC-06B) — `EvidenceKeywordValidator`, one
  reusable class instantiated four times (manifest, documentation,
  configuration, dependency domains), deterministic substring matching of
  a small per-relationship-type technology keyword table against a
  hypothesis's own cited evidence text. Reliability tier fixed at 1
  (heuristic); returns only `confirms`/`no_signal`, **never**
  `contradicts` — "absence of a keyword in incomplete evidence is not
  proof of absence," the same discipline every other validator follows.
  Adding a new technology is a keyword-table entry, never a new class —
  this is the concrete mechanism behind "self-improving without hardcoded
  language validators."

`run_validators` dispatches concurrently
(`asyncio.gather(..., return_exceptions=True)`) while staying provably
deterministic: every validator reads a frozen pack, never mutates shared
state, and results are reassembled in the fixed order validators were
selected — never completion order. One validator's exception never
discards another's result (tested directly under real concurrent
scheduling, not just sequential try/except).

## Confidence (`contracts/confidence.py`, `confidence/default_engine.py`)

Six states: `verified`, `highly_likely`, `likely`, `candidate`, `rejected`,
`conflicting`. `DefaultConfidenceEngine.aggregate(prior, new_result)` is
the reference implementation, and is:

- **Deterministic** — same inputs, same output, always, versioned by
  `formula_version`.
- **Incremental** — folds one `ValidationResult` at a time; never
  re-scans full history. (Required two additive contract amendments found
  during implementation, not foreseen in the original design:
  `ConfidenceModel.confirming_source_types: frozenset[str]` — a bare count
  can't answer "have I already counted this source_type"; and
  `ConfidenceModel.max_confirming_reliability_tier: int` — needed so a
  later, weaker confirmation can't appear to erase an earlier, stronger
  one. Both changes and their rejected alternatives are documented
  verbatim in ADR 0018, a genuine example of "real correctness issues
  found during implementation are fixed immediately and documented, not
  silently patched around.")
- **Monotonic** — a confirmation only ever strengthens state; a
  contradiction only ever weakens it; neither regresses the other's
  already-recorded effect. `REJECTED`/`CONFLICTING` never regress back to
  a stronger state once contradicted.

The formula is a direct, named generalization of `cross_repo_linker.py`'s
pre-existing two-tier `structural`/`heuristic` vocabulary into six states
— proven, not asserted, by RFC-03's parity test:
`test_all_validators_parity_for_deterministic_hypothesis` reproduces
`cross_repo_linker.py`'s existing labels exactly for every current
cross-repo edge type. `HIGH_RELIABILITY_TIER = 3` and
`MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED` are intentionally public
constants so `explainability.py` can cite the engine's real thresholds
instead of a second, hand-copied number.

**Critically**: `generator_confidence` (a hypothesis's own self-reported
confidence, including the LLM generator's) never enters this formula.
Confidence is derived only from independent `ValidationResult`s.

## Explanation (`explainability.py`, RFC-06C)

`explain_confidence(confidence, validation_results) -> ConfidenceExplanation`
— pure, deterministic, never recomputes state, only describes it. Built
because a "confidence explainability" ask was audited first and found that
real evidence-fusion logic *already existed* inside
`DefaultConfidenceEngine.aggregate` (frozen since RFC-03 approval) — the
genuine gap was human-readable narration, not a second fusion layer.
Persisted once (`knowledge_relationships.explanation`, nullable JSON,
additive migration `c7d8e9f0a1b2`) at the moment `ValidationResult`s are
still in scope, since they themselves are never persisted.

## Materializer (`materializer.py`, RFC-05B)

Covered in depth in [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md).
Pure projection: Engineering Memory + evidence packs → `GraphPayload`, no
inference, no LLM, no validators. Not yet wired into any live write path.

## Parity (`parity/comparator.py`, `parity/report.py`, `parity/ignore_rules.py`)

`compare_graphs(legacy, materialized) -> ParityReport` — pure, no I/O,
deterministic (every collection sorted by an explicit key, never dict/set
iteration order or Python's hash-randomized string hashing). Edge identity
is deliberately a **multiset** of full property signatures
(`collections.Counter`), not a bare `(source, type, target)` triple,
because a single legacy graph can legitimately contain two edges sharing
that triple with different properties. This is what powers Validation 7 in
the regression suite (§ [09_VALIDATION_FRAMEWORK.md](09_VALIDATION_FRAMEWORK.md))
and the Graph Parity dashboard in the frontend, comparing the live Neo4j
graph against a graph re-derived from Engineering Memory — the direct,
UI-visible proof that "the graph is rebuildable from history" isn't just a
claim.

## Learning (`app/learning_engine/`, RFC-06D)

Sibling package to `app.knowledge_engine`, never imported by it — a
one-directional dependency by design. `LearningEvent`
(`build_learning_event`, a pure deterministic mapping from a caller-stated
feedback kind, never inferred from a confidence-state diff — intent is
always explicit), `compute_statistics` (pure aggregation: approval/
rejection rate, per-relationship-type breakdown, repeated-false-positive
signal, two-halves rejection-rate trend — no ML, no LLM),
`LearningEventRecord` (new, append-only, `sequence`-ordered table),
`LearningEngineService` (always persists a `LearningEvent`; for the three
kinds that assert a relationship's state, also calls
`EngineeringMemoryService.apply_correction`, RFC-04's own method, reused
unmodified). This is the first thing in the codebase that lets a human
approve or reject a `KnowledgeRelationship` at all. **Explicitly not
built**, named directly in the RFC: automatic prompt evolution, validator/
confidence calibration, a recommendation engine, repository health
scoring, org-wide learning, model benchmarking — all of these are read-
shaped to consume `LearningStatistics` without a schema change, none is
implemented.

## Cross-repository reasoning (RFC-05, `app/indexer/graph/cross_repo_memory.py`)

Reframed mid-implementation from "cut `cross_repo_linker` over to the new
engine" to "additionally persist what it already produces into Engineering
Memory" — the live Neo4j edge-write path (`compute_edges`,
`replace_cross_repository_edges`) is untouched. A real concurrency bug was
found and fixed before shipping: `persist_cross_repo_relationships` must
run on its own independent `AsyncSession`, not `relink_account`'s own `db`,
because `relink_account` holds a `pg_advisory_xact_lock` for its entire
duration specifically so the lock survives until the *caller* commits —
committing early from inside `relink_account` would have silently reopened
a concurrent-relink race a prior fix (`Finding #3`) had already closed.
Verified by the pre-existing regression test for that finding still
passing unchanged, plus a fresh audit confirming all three
`CROSS_REPO_LINK_RULES` relationship types (`CALLS_SERVICE`,
`SHARES_TOPIC`, `DEPENDS_ON_REPOSITORY`) now have `Hypothesis`/validator
coverage. The real cutover (retiring `cross_repo_linker`'s own edge-writing
in favor of projecting from Engineering Memory) is deferred to RFC-05B —
explicitly a prerequisite relationship, not an oversight (RFC-05 had to
exist before there was any cross-repository knowledge in memory to project
from).

## Evidence curation for the LLM path (`evidence_curation.py`, RFC-06)

Audited before reuse: `context_pipeline.reasoning.curation.curate()` was
considered but rejected as a direct dependency because it ranks components
against a *ticket's own text* via a hop-bounded neighborhood seeded from
request-matching anchors — none of that applies here (no ticket, no
request, no seeded neighborhood; just one repository's own evidence). A
small, new, generic module was built instead, reusing only the *pattern*
(budgeted, honestly-counted exclusions) for a different scoring problem
(kind-diversity sampling). This is cited repeatedly in this handbook as a
model instance of "audit before reusing a pattern that looks similar but
solves a different problem."
