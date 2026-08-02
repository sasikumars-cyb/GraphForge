# ADR 0018: Engineering Intelligence Platform (Evidence → Hypothesis → Validation → Confidence → Knowledge)

## Status

Proposed. This is the frozen architecture for GraphForge's long-term knowledge
discovery platform, superseding the language-centric indexing model as the
target design. It is not yet implemented — implementation proceeds only
through the RFC roadmap below, each RFC independently reviewed and merged.

Numbered 0018 in this repository's actual ADR sequence (the design
conversation that produced this referred to it informally as "ADR-0001 for
the Engineering Intelligence Platform" — that label described its conceptual
role as the platform's founding document, not its number in this repo).

## Context

Today, GraphForge discovers a repository's architecture through a fixed
pipeline: `parsers/registry.py` maps a detected language to one
`ILanguageParser`, which parses directly into an `ArchitectureModel`, which
`graph/builder.py` converts unconditionally into a `GraphPayload`. This is
fully deterministic (ADR 0007) and correct for what it covers, but every new
language or framework requires a new hand-written parser and extractor set,
and the pipeline has no seam for anything non-deterministic (an LLM, a future
runtime-telemetry source) to contribute without either bypassing validation
or being bolted on as a separate, uncoordinated subsystem.

Two existing subsystems already prove a better pattern works in production,
for a narrower problem: `app.agents.component_grounding`
(ADR 0013) independently re-validates LLM-adjacent claims against indexed
graph evidence before they're trusted, and
`app.context_pipeline.reasoning.curation` (ADR 0014) tiers and
confidence-scores evidence before it reaches an LLM prompt. Neither writes a
non-deterministic inference back into the graph as fact. This ADR
generalizes that proven pattern into the core indexing pipeline itself, so
that adding a new language or evidence source becomes a matter of writing a
new evidence extractor and/or hypothesis generator, never a new
graph-writing code path that has to re-earn trust from scratch.

## Decision

GraphForge's knowledge-discovery pipeline becomes five stages, one pipeline,
no parallel graph-insertion paths:

```
Evidence  →  Hypothesis  →  Validation  →  Confidence  →  Knowledge
```

A deterministic-generator hypothesis with no contradicting validator result
still passes through this same pipeline — it simply clears the trivial
"no contradiction, generator class permits auto-verification" validator in
O(1), rather than the full validator suite. One mental model, one code path,
cheap cases stay cheap by filtering which validators apply, not by forking
the pipeline.

### Core contracts

```python
# app/knowledge_engine/contracts/provenance.py

@dataclass(frozen=True)
class GeneratorIdentity:
    kind: Literal["deterministic", "rule", "llm", "runtime", "docs", "infra"]
    name: str            # "spring_boot_java_parser", "claude-sonnet-5"
    version: str          # pinned — never "latest"

@dataclass(frozen=True)
class Provenance:
    generator: GeneratorIdentity
    produced_at: datetime
    pack_version: str
    run_id: str


# app/knowledge_engine/contracts/evidence.py

@dataclass(frozen=True)
class EvidenceReference:
    repository_id: str
    source_type: str      # open registry, see "Open vocabularies" below
    locator: str           # file_path / manifest_path / doc URL
    line: int | None = None
    key: str | None = None
    commit_sha: str | None = None

@dataclass(frozen=True)
class EvidenceItem:
    id: str                # content-addressed: hash(kind, reference, raw_value)
    kind: str               # open registry
    source_type: str
    reliability_tier: int   # static, looked up by kind — never computed per-instance
    reference: EvidenceReference
    raw_value: str
    provenance: Provenance

@dataclass(frozen=True)
class EngineeringEvidencePack:
    id: str
    repository_id: str
    commit_sha: str
    schema_version: str
    items: tuple[EvidenceItem, ...]
    produced_at: datetime
    is_delta: bool = False   # True for incremental appends (e.g. runtime telemetry
                              # observed between index runs) — see Event flow


# app/knowledge_engine/contracts/hypothesis.py

@dataclass(frozen=True)
class Hypothesis:
    id: str                 # content-addressed: hash(generator.name, relationship_type,
                              # source_entity, target_entity, evidence_refs)
    relationship_type: str   # open registry
    source_entity: str
    target_entity: str
    evidence_refs: tuple[str, ...]      # must all resolve within the same pack
    generator_confidence: float | None  # advisory only, never authoritative
    explanation: str
    provenance: Provenance

class HypothesisGenerator(ABC):
    identity: GeneratorIdentity
    consumes: set[str]       # EvidenceItem.source_type values

    @abstractmethod
    async def generate(self, pack: EngineeringEvidencePack) -> list[Hypothesis]: ...
    # A generator's own failure (timeout, crash) must never block or roll back
    # any other generator's output for the same run — logged and swallowed,
    # same discipline app.indexer.services.indexing_service.run_indexing
    # already applies to relink_account failures.


# app/knowledge_engine/contracts/validation.py

@dataclass(frozen=True)
class ValidationResult:
    hypothesis_id: str
    validator_name: str
    verdict: Literal["confirms", "contradicts", "no_signal"]
    evidence_used: tuple[str, ...]
    source_type: str
    explanation: str
    provenance: Provenance

class KnowledgeValidator(ABC):
    name: str
    applies_to: set[str]     # relationship_types this validator has an opinion on

    @abstractmethod
    async def validate(self, hypothesis: Hypothesis, pack: EngineeringEvidencePack) -> ValidationResult: ...


# app/knowledge_engine/contracts/confidence.py

class ConfidenceState(StrEnum):
    VERIFIED = "verified"
    HIGHLY_LIKELY = "highly_likely"
    LIKELY = "likely"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    CONFLICTING = "conflicting"

@dataclass(frozen=True)
class ConfidenceModel:
    state: ConfidenceState
    distinct_confirming_source_types: int
    contradiction_count: int
    computed_at: datetime
    formula_version: str

class ConfidenceEngine(ABC):
    @abstractmethod
    def aggregate(self, prior: ConfidenceModel | None, new_result: ValidationResult) -> ConfidenceModel: ...
    # Incremental, not batch: called once per ValidationResult as it arrives
    # (distributed validators report at independent latencies — there is no
    # "wait for all validators" checkpoint). Monotonic: a new confirmation
    # may only strengthen state; a new contradiction may reduce it to
    # Conflicting/Rejected; the absence of an expected validator's report so
    # far must never regress an already-computed state.


# app/knowledge_engine/contracts/knowledge.py

@dataclass(frozen=True)
class KnowledgeRelationship:
    id: str
    relationship_type: str
    source_entity: str
    target_entity: str
    confidence: ConfidenceModel
    hypothesis_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]


# app/knowledge_engine/contracts/correction.py

@dataclass(frozen=True)
class CorrectionSource:
    kind: Literal["human", "agent"]
    identity: str            # user id, or agent GeneratorIdentity.name
    trust_level: float        # human corrections default to 1.0 (unconditional
                                # override); agent corrections default lower and
                                # flow through the same validator/confidence
                                # machinery as any other hypothesis — never an
                                # unconditional override reserved for agents

@dataclass(frozen=True)
class UserCorrection:
    id: str
    relationship_id: str
    source: CorrectionSource
    corrected_state: ConfidenceState | None   # None = "reject entirely"
    reason: str
    created_at: datetime


# app/knowledge_engine/memory.py

class EngineeringMemory(ABC):
    async def record_pack(self, pack: EngineeringEvidencePack) -> None: ...
    async def append_evidence(self, repository_id: str, items: list[EvidenceItem]) -> str: ...
    async def record_hypotheses(self, hypotheses: list[Hypothesis]) -> None: ...
    async def record_validation(self, result: ValidationResult) -> None: ...
    async def record_relationship(self, rel: KnowledgeRelationship) -> None: ...
    async def record_correction(self, correction: UserCorrection) -> None: ...
    async def current_knowledge(self, repository_id: str) -> list[KnowledgeRelationship]: ...
    async def history(self, relationship_id: str) -> RelationshipHistory: ...
```

`KnowledgeGraphWriter` is deliberately **not** a separate interface —
`IGraphRepository` (already graph-store-agnostic) is extended with the one
method needed to sync a `CurrentKnowledgeProjection` incrementally; wrapping
it a second time adds a layer with no independent reason to exist.

### Open vocabularies, not closed enums

`EvidenceItem.kind`, `EvidenceReference.source_type`, and
`Hypothesis.relationship_type` are declarative registries — the same pattern
`app.knowledge.registry.KnowledgeSourceSpec` and
`app.ai.providers.registry.ProviderSpec` already use — not `StrEnum`s
requiring a schema change per new framework or evidence source. A value,
once used by any persisted `Hypothesis`/`EvidenceItem`, is never renamed —
only deprecated and superseded — because `EngineeringMemory` promises
reproducibility, and renaming a taxonomy entry retroactively breaks every
historical record that used it.

### Persistence: EngineeringMemory is Postgres, Neo4j is a derived projection

`EngineeringEvidencePack` persists as one compressed blob (JSONB or object
storage) keyed by `(repository_id, commit_sha, schema_version)` —
**never** one relational row per `EvidenceItem**. A large monorepo can
produce tens of thousands of evidence items per run; normalizing that into
per-row storage in an append-only OLTP table is a write-throughput failure
waiting to happen. `Hypothesis`, `ValidationResult`, and
`KnowledgeRelationship` — orders of magnitude lower cardinality — get real
relational rows, queryable and indexed.

`CurrentKnowledgeProjection` (what `IGraphRepository` syncs into Neo4j) is
derived and rebuildable from the `EngineeringMemory` log at any time — a
cache with a rebuild procedure, not something requiring its own independent
backup or migration story. Neo4j moves from "system of record" (today) to
"synced index optimized for graph traversal" — a deliberate inversion from
the current `replace_repository_graph` model, made explicit so nobody
mistakes Neo4j for the durable history store going forward.

Retention: `EvidencePack` blobs older than the last N successful runs per
repository may be archived/compacted (they're regenerable by re-extraction
against the same commit). `HypothesisLog`, `ValidationLog`,
`CorrectionLog`, and confidence-state transition history are **never**
compacted or deleted — they are the audit trail this platform's
explainability promise depends on.

## Package structure

```
app/knowledge_engine/                 # new — deliberately not app/knowledge,
                                        # which already means something else
                                        # (the external-source connection
                                        # registry: Jira/Confluence/GitHub
                                        # transport+auth catalog)
    contracts/
        provenance.py
        evidence.py
        hypothesis.py
        validation.py
        confidence.py
        knowledge.py
        correction.py
    memory.py                          # EngineeringMemory ABC
    memory_postgres.py                  # concrete implementation
    generators/
        registry.py                    # plugin registry, same shape as
                                         # app.indexer.graph.cross_repo_linker
                                         # .CROSS_REPO_LINK_RULES
    validators/
        registry.py
    confidence/
        default_engine.py               # generalized from
                                         # app.context_pipeline.reasoning
                                         # .curation.curate()

app/indexer/                           # existing, largely unchanged
    evidence/                          # new: tree-sitter + infra/docs
                                        # extractors → EvidenceItem
    hypotheses/
        deterministic_generator.py     # adapts SpringBootJavaParser/PythonParser
        rule_generator.py              # adapts feign/kafka/controller extractors
        llm_generator.py               # calls app.ai.providers.factory,
                                        # input curated via
                                        # app.context_pipeline.reasoning.curation
    parsers/                           # unchanged — remain the calibration
                                        # reference, see Engineering invariants
    graph/
        builder.py                     # extended: KnowledgeRelationship → GraphPayload

app/graph/                             # unchanged: IGraphRepository, Neo4jGraphRepository,
                                        # GraphNode/GraphEdge/GraphPayload
app/ai/providers/                      # unchanged: multi-provider LLM infra, reused as-is
app/context_pipeline/reasoning/        # unchanged initially; curation.py becomes a
                                        # candidate to import app/knowledge_engine/contracts
                                        # directly once proven equivalent (not before)
```

## Dependency diagram

```
app/knowledge_engine/contracts   (leaf — no dependencies on the rest of GraphForge)
        ▲
        │ implements / depends on
        │
app/indexer/evidence  ──┐
app/indexer/hypotheses  ─┼──► app/knowledge_engine/{generators,validators,confidence,memory}
app/ai/providers        ─┘             │
                                        ▼
                              app/knowledge_engine/memory_postgres
                                        │
                                        ▼
                              app/graph (IGraphRepository) ──► Neo4j
                                        │
                                        ▼
              app/agents/* , app/context_pipeline/* , app/analysis/*
              (unchanged — read GraphNode/GraphEdge, indifferent to origin)
```

`contracts/` has zero dependencies on Neo4j, Postgres, or any specific
generator/validator — it is the one package every other package in this
list may depend on, and which depends on none of them. This is what keeps
the interfaces reusable by `context_pipeline.reasoning` later without a
circular import.

## Event flow

**Full-repository run** (a new commit indexed): extract full
`EngineeringEvidencePack` → all applicable `HypothesisGenerator`s run
concurrently, independently, over the same pack (one generator's failure
logged and swallowed, never blocking another's output) → each `Hypothesis`
persisted → each applicable `KnowledgeValidator` runs, independently,
against each `Hypothesis` → each `ValidationResult` triggers an incremental
`ConfidenceEngine.aggregate()` call updating that relationship's state →
`KnowledgeRelationship`s crossing the promotion threshold sync into
`CurrentKnowledgeProjection` → `IGraphRepository` writes the delta to Neo4j.

**Incremental evidence ingestion** (runtime telemetry, a new Confluence page
— arriving between index runs, no new commit): `EngineeringMemory
.append_evidence()` creates a delta pack (`is_delta=True`) referencing the
current full pack's `id` as its base → only generators that `consumes` that
evidence's `source_type` re-run, against the delta, not the full repository
→ validation/confidence/promotion proceed identically from there. This is
the mechanism that makes "support runtime telemetry" true without requiring
a full re-extraction per telemetry event.

**Correction** (human or agent flags a relationship): `record_correction`
persists a `UserCorrection` → for `kind="human"`, `trust_level=1.0`
overrides the relationship's state directly, recorded as a new confidence
transition (never an edit to prior history) → for `kind="agent"`, the
correction is itself treated as a new `Hypothesis`-shaped input to the same
validator/confidence pipeline, never an unconditional override.

## Lifecycle

**Evidence** — extracted once per full run or delta; immutable once
persisted; retained per the retention policy above (compactable, since
regenerable from source at the same commit).

**Hypothesis** — generated, content-addressed (so re-running the same
generator against the same pack is idempotent — same id, no duplicate row);
immutable; never edited, only ever superseded by a new hypothesis with a
new id from a later run or different generator; permanently retained (audit
trail, never compacted).

**Validation** — computed per hypothesis per applicable validator;
immutable once recorded; permanently retained. A validator re-run (e.g.
after a validator bug fix) produces a new `ValidationResult`, not an edit —
old results stay in history, `formula_version`/validator identity
disambiguate which run produced which verdict.

**Confidence** — recomputed incrementally on every new `ValidationResult`
or `UserCorrection`; every transition permanently retained as history (this
is the literal answer to "confidence history" from the original ask); the
*current* state is the latest transition, never the only one kept.

**Knowledge** — a `KnowledgeRelationship` exists once at least one
hypothesis clears the promotion threshold; its `confidence` field always
reflects the latest aggregation; superseded on every commit's re-index
(consistent with today's `replace_repository_graph` semantics) but its full
history remains queryable via `EngineeringMemory.history()` even after
supersession — this is the actual capability upgrade over today's
full-replace-with-no-history model.

**Engineering Memory** — append-only for `Hypothesis`/`Validation`/
`Correction`/confidence transitions, forever; `EvidencePack` blobs subject
to the archival policy above; `CurrentKnowledgeProjection` fully rebuildable
from the log at any time, treated as disposable/derived, never a second
source of truth.

## Implementation principles every future contributor must follow

1. A `HypothesisGenerator` never writes to `IGraphRepository` directly —
   its only output is `list[Hypothesis]`, full stop.
2. A `KnowledgeValidator` is a pure function of `(Hypothesis, EvidencePack)`
   — no network I/O beyond what's needed to re-check evidence that's
   already in the pack; no calls to an LLM (a validator that itself asks an
   LLM "does this seem right" isn't validating, it's generating a second,
   uncoordinated hypothesis).
3. `ConfidenceEngine.aggregate()` is pure and deterministic — same inputs,
   same output, every time, versioned by `formula_version` so a formula
   change is itself auditable.
4. Every new `EvidenceItem.kind`, `EvidenceReference.source_type`, or
   `Hypothesis.relationship_type` is a registry entry, reviewed once, never
   silently introduced inline by a generator.
5. Every new `HypothesisGenerator`/`KnowledgeValidator` ships with a
   precision/recall test against a held-out fixture set as a merge
   requirement — a generator with no measured false-positive rate does not
   merge.
6. `SpringBootJavaParser`/`PythonParser` remain permanently in place as the
   platform's calibration reference — every new generator's precision is
   measured against what they produce on the same fixtures, for as long as
   this platform exists.
7. A generator's failure never blocks another generator's output for the
   same run — logged and swallowed, matching the existing
   `run_indexing`/`relink_account` failure-isolation pattern.
8. LLM generator input is curated (relevance/budget-tiered), never a raw
   evidence-pack dump — reuse `context_pipeline.reasoning.curation`'s
   pattern rather than re-deriving it.

## Engineering invariants

- Graph relationships are never created without evidence.
- Every `Hypothesis`'s `evidence_refs` must resolve within its own
  `EvidencePack`; an unresolvable reference is rejected at ingestion,
  before any validator runs.
- Every relationship carries provenance back to the generator(s) and
  evidence that produced it.
- Every relationship is reproducible: the same evidence pack, run through
  the same generator/validator/confidence-engine versions, produces the
  same result.
- Validators are deterministic; no validator ever calls an LLM.
- A `KnowledgeValidator` operates solely on `(Hypothesis,
  EngineeringEvidencePack)` — no other input, no network I/O beyond the
  pack it was given, and (restating the line above for emphasis) never a
  call to an LLM.
- A `HypothesisGenerator` proposes relationships only — it never writes to
  a graph repository or any persistence layer directly. LLMs never write
  directly to the graph, as one instance of this — they produce hypotheses
  only, same as every other generator kind.
- A hypothesis's own `generator_confidence` is advisory and never
  authoritative — it must never influence a `KnowledgeValidator`'s verdict
  or a `ConfidenceEngine`'s aggregation. Graph-facing confidence is
  computed by the `ConfidenceEngine`, always, from `ValidationResult`s
  alone — never from `generator_confidence`, never from raw evidence the
  engine re-reads itself (it has no pack access by design).
- A `ConfidenceEngine` implementation must be deterministic, incremental
  (folds one `ValidationResult` at a time into a prior `ConfidenceModel`,
  never re-scanning full history), and monotonic (a confirmation only ever
  strengthens state, a contradiction only ever weakens it, and neither
  regresses the other's already-recorded effect). `DefaultConfidenceEngine`
  (RFC-03) is the reference implementation proving this is achievable,
  verified by dedicated tests covering both monotonic directions.
- Existing deterministic implementations (`SpringBootJavaParser`,
  `PythonParser`, `cross_repo_linker.py`) remain the source of truth for
  their respective domains until a future RFC explicitly cuts them over —
  RFC-03 proved confidence parity against `cross_repo_linker.py` without
  modifying it; RFC-05 is the only RFC authorized to change that.
- `app.knowledge_engine.validators.registry.run_validators` (the
  validator-registry pattern) is the canonical extension point for adding
  a new validator — a new validator is one registry entry, never a second
  dispatch mechanism, same discipline as `CROSS_REPO_LINK_RULES` and the
  deferred-but-eventual `GeneratorRegistry` (see RFC-02B's note above).
- Engineering Memory is append-only: `Hypothesis`, `ValidationResult`,
  `UserCorrection`, and confidence-state transitions are never edited or
  deleted, only superseded.
- The current graph (`CurrentKnowledgeProjection` → Neo4j) is always
  derived from Engineering Memory, never the other way around, and is
  fully rebuildable from it at any time.
- A taxonomy value (`kind`/`source_type`/`relationship_type`), once used by
  any persisted record, is never renamed — only deprecated and superseded.
- One generator's or one validator's failure never blocks or corrupts
  another's output for the same run.
- An agent-sourced correction is never an unconditional override — it
  flows through the same validation/confidence pipeline as any other
  hypothesis; only a human-sourced correction carries unconditional
  override authority, and even that is recorded as a new transition, never
  a silent edit to history.

### Frozen at RFC-03 approval (2026-08-01)

The seven invariants below were confirmed frozen — unless ADR-0018 is
explicitly amended — at RFC-03's approval. Each maps onto the invariant
list above; listed here verbatim for traceability, not as a second copy:

1. `HypothesisGenerator` proposes relationships only.
2. Generator confidence is advisory and must never influence validation.
3. `KnowledgeValidator`s operate solely on `(Hypothesis,
   EngineeringEvidencePack)`.
4. `DefaultConfidenceEngine` is deterministic, incremental, and monotonic.
5. Confidence is derived only from `ValidationResult`s.
6. Existing deterministic implementations remain the source of truth
   until a future RFC replaces them.
7. The validator registry is the canonical extension point for future
   validators.

## RFC-01 contract amendments (made during RFC-03, 2026-08-01)

Implementing RFC-03's confidence formula surfaced two gaps in RFC-01's
original contracts that made the formula literally uncomputable as
specified. Both are additive, both were required before any RFC-03
business logic could be written, and both are recorded here per the
review discipline this project follows: real correctness issues found
during implementation are fixed immediately and documented, not silently
patched around.

### `ValidationResult.evidence_reliability_tier: int`

**Why the original contract was insufficient**: `ValidationResult` had no
way to express *how reliable* the evidence behind a `confirms` verdict
was — only its category (`source_type`) and count. A literal
`@FeignClient(name=...)` annotation match and a dependency-coordinate name
match are both exactly one confirming result of one source type; without
a reliability signal, `distinct_confirming_source_types` alone cannot
distinguish them, and RFC-03's success criterion (reproduce
`cross_repo_linker.py`'s `structural` vs `heuristic` distinction) is
mathematically unreachable.

**Why the new field is required**: it lets a validator — which already
reads the evidence it used, at zero extra I/O — summarize that evidence's
reliability at the moment it produces its verdict, giving the confidence
engine the one input it actually needs to preserve the structural/
heuristic distinction.

**Alternatives considered**:
- *Have the `ConfidenceEngine` look up reliability itself.* Rejected: the
  engine's signature (`aggregate(prior, new_result)`) has no access to the
  `EngineeringEvidencePack`, and giving it one would mean re-fetching
  evidence the validator already read — redundant I/O and a bigger
  interface change than adding one field.
- *Encode reliability into `source_type` naming instead of a new field*
  (e.g. treat `"code_annotation_literal"` as inherently "high" by a fixed
  string convention). Rejected: this would require the engine to hardcode
  a source_type -> reliability lookup table, directly contradicting ADR
  0018's open-vocabulary requirement for `source_type`.

**Compatibility impact**: breaking for any code constructing
`ValidationResult` directly (the field has no default) — the only
existing callers were RFC-01's own tests, all updated in the same change.
No persisted data exists yet (RFC-04 hasn't shipped), so there is nothing
to migrate.

**Future RFCs that depend on it**: RFC-04 (persists `ValidationResult` as-is,
this field included, from day one — no later migration needed) and every
future validator (RFC-06's LLM-backed validator, RFC-09's infra/docs
validators) must populate it honestly for the confidence formula to remain
meaningful across source types it doesn't yet know about.

### `ConfidenceModel.confirming_source_types: frozenset[str]`

**Why the original contract was insufficient**: RFC-01 requires
`ConfidenceEngine.aggregate` to be **incremental** — folding one new
`ValidationResult` into a prior `ConfidenceModel`, never re-scanning full
history. But the prior model only carried an integer count
(`distinct_confirming_source_types`), and a bare count cannot answer "is
this newly-arriving source_type one I've already counted?" — the specific
question incremental distinct-counting requires an answer to.

**Why the new field is required**: retaining the actual set is the
minimum information that makes incremental, correct distinct-counting
possible at all. `distinct_confirming_source_types` remains present as
the derived, at-a-glance count, enforced equal to `len
(confirming_source_types)` — most callers still only need the count.

**Alternatives considered**:
- *Pass the engine full `ValidationResult` history instead of a single
  prior model.* Rejected: a bigger, more invasive signature change than
  adding one field, and works against RFC-01's stated design ("a later
  transition can be computed incrementally without re-scanning every
  prior `ValidationResult`").
- *Don't track distinctness at all; just count every confirming result.*
  Rejected: this was RFC-01's original, actually-broken behavior — it
  would let the same validator's repeated confirmation count as multiple
  independent corroborations, directly undermining `VERIFIED`'s meaning
  ("multiple *independent* confirmations").

**Compatibility impact**: breaking for any direct `ConfidenceModel`
construction (no default; all existing call sites, all in RFC-01's own
tests, updated in the same change). No persisted data yet.

**Future RFCs that depend on it**: RFC-08 (multi-provider consensus)
depends on this directly — "cross-provider agreement counts as at most
one `distinct_confirming_source_type`" is only checkable because the set,
not just a count, is retained.

### `ConfidenceModel.max_confirming_reliability_tier: int`

**Why the original contract was insufficient**: same incremental
constraint as above, applied to reliability rather than distinctness.
Monotonicity (RFC-01: "a new `confirms` result may only strengthen
`state`") requires remembering the *strongest* evidence ever confirmed,
not just the most recent result — otherwise a later, weaker confirmation
arriving after an earlier strong one could incorrectly appear to leave
nothing for the engine to compare against, or a naive re-implementation
could regress state based on only the latest result's tier.

**Why the new field is required**: without it, "structural" (tier-3)
trust earned by an earlier confirmation would be invisible to the engine
by the time a later, weaker result arrives — silently violating
monotonicity the same way the un-set-based source-type count did.

**Alternatives considered**:
- *Recompute from `EvidenceItem.reliability_tier` via the pack, each
  call.* Rejected for the same reason as the `evidence_reliability_tier`
  alternative above — the engine has no pack access, and re-deriving
  reliability from scratch every call means either widening the engine's
  signature or duplicating the validator's own lookup.
- *Store per-source-type tiers instead of a single running max* (a
  `dict[str, int]` rather than one integer). Considered more capable but
  rejected as unneeded complexity for RFC-03's actual formula, which only
  ever compares against one threshold (`_HIGH_RELIABILITY_TIER`) — a
  single running max is the minimum sufficient statistic; a per-type
  breakdown has no consumer yet and would be exactly the kind of
  speculative decomposition the RFC-01 pre-merge review already rejected
  once.

**Compatibility impact**: same as `confirming_source_types` — breaking
only for direct construction, all sites already updated, nothing
persisted yet to migrate.

**Future RFCs that depend on it**: RFC-06 (LLM generator) and RFC-09
(infra/docs evidence sources) both introduce new reliability tiers into
the same formula; this field is what lets a stronger confirmation from
either of those sources correctly outrank an existing weaker one, and
vice versa, without regressing state either direction.

## RFC roadmap

Every RFC below preserves full backward compatibility for the current
Java/Spring Boot and Python indexing paths through at least RFC-05; nothing
before RFC-06 changes what any existing user observes.

**RFC-01 — Core contracts, no behavior change.**
Introduce `app/knowledge_engine/contracts/*` exactly as specified above.
No other package imports them yet.
*Tests*: dataclass invariants only (e.g. `evidence_refs` non-empty).
*Rollback*: delete the package.
*Migration*: none — no persisted data yet.
*Success criteria*: merges cleanly, zero runtime behavior change anywhere.

**RFC-02 — Deterministic parsers as HypothesisGenerators, shadow mode.**
Adapter wrapping `SpringBootJavaParser`/`PythonParser` output as
`list[Hypothesis]`, `generator_confidence` implicit-`Verified`. Runs
alongside `index_repository`, logs only, writes nothing.
*Tests*: round-trip losslessness against every existing fixture repo.
*Rollback*: stop invoking the adapter.
*Migration*: none.
*Success criteria*: 100% lossless round-trip on all current fixtures.

**RFC-02B — Shadow execution of RFC-02's generator during real indexing.**
Implemented and approved 2026-08-01. `app/indexer/hypotheses/
shadow_runner.py`'s `run_shadow_hypothesis_generation` executes
`DeterministicParserHypothesisGenerator` once, after `index_repository`
has already built and persisted the real graph — never before, so shadow
execution structurally cannot affect what gets committed. Logs execution
time, evidence count, and hypothesis count on success; logs and swallows
any exception on failure (`except Exception`, matching the isolation
pattern `run_indexing` already uses around `relink_account`). No graph
writes, no validator, no confidence engine, no `EngineeringMemory` — those
remain out of scope until their own RFCs.
*Tests*: `tests/integration/test_shadow_hypothesis_generation.py`, against
real Neo4j — indexing succeeds when the generator succeeds; indexing
succeeds when the generator throws; the persisted `GraphPayload` is
byte-for-byte identical to `build_graph` computed with no shadow code
involved at all; the persisted graph is topologically identical whether
shadow generation runs or is a no-op.
*Rollback*: revert the two-line change in `indexing_service.index_repository`
(the import and the one `await` call); `shadow_runner.py` can be left
unimported with zero effect.
*Migration*: none.
*Success criteria*: met — all four properties above verified against real
Neo4j; zero change to `index_repository`'s return value, exception
behavior, or persisted graph in either the success or failure case.

**Deliberate deferral, recorded here so it isn't mistaken for an
oversight**: `shadow_runner.py` executes exactly one hardcoded
generator (`DeterministicParserHypothesisGenerator`) and contains no
`GeneratorRegistry`. This is correct as of RFC-02B, not a gap — a
registry (iterate `list[HypothesisGenerator]`, filter by `consumes`
against the pack, isolate each generator's failure independently) has
nothing to select between until a second generator exists, and building
one now would be exactly the kind of speculative abstraction ADR 0018's
implementation rules reject ("every new interface should have at least
one real implementation immediately" — a registry with one entry is not
an interface earning its keep). **The explicit trigger to evolve
`shadow_runner.py` into a registry-driven pipeline is RFC-06** (the first
additional generator, LLM-backed): at that point the loop-and-isolate
shape becomes real infrastructure serving ≥2 real implementations, not
a shape justified in advance. Until RFC-06, `shadow_runner.py` stays
scoped to deterministic generation only, with no architectural changes.

**RFC-03 — Validator + Confidence engine, shadow mode, scored against
`cross_repo_linker`.**
Generalizes `component_grounding`/`curation` into
`app/knowledge_engine/{validators,confidence}`, including the incremental
(not batch) aggregation trigger and monotonic-state guarantee.
*Tests*: regression parity — new engine reproduces `cross_repo_linker.py`'s
existing `structural`/`heuristic` labels exactly, for every current
cross-repo edge type.
*Rollback*: stop invoking; `cross_repo_linker.py` untouched.
*Migration*: none.
*Success criteria*: 100% label parity with today's hand-assigned confidence.

**RFC-04 — Engineering Memory, additive persistence, no consumers required.**
`app/knowledge_engine/memory_postgres.py` + migration for the append-only
tables (blob-based `EvidencePack` storage, relational `Hypothesis`/
`ValidationResult`/`KnowledgeRelationship`/correction/confidence-history
tables), fed by RFC-02/03's shadow output.
*Tests*: every hypothesis/validation from RFC-02/03's shadow runs is
durably recorded and independently queryable via `history()`.
*Rollback*: drop the new tables; nothing else references them yet.
*Migration*: new Alembic migration, purely additive, no existing table
touched.
*Success criteria*: shadow-run data fully recoverable from
`EngineeringMemory` alone, verified against the RFC-02/03 logs.

**Implemented and approved 2026-08-02**, with the following clarified as
permanent design decisions (not implementation details subject to later
casual change):

- **Engineering Memory is an immutable, append-only store.** No table it
  owns (`engineering_evidence_packs`, `knowledge_relationships`,
  `user_corrections`) has an update path — every write is an insert.
  Persisting the same evidence pack or relationship again produces a new
  row, never a mutation of an existing one. This is the mechanism, not
  just the intent, behind "confidence history" and "relationship
  evolution."
- **Current relationship state is a derived projection, not a
  responsibility Engineering Memory owns separately.** There is no
  "current state" column or table anywhere in this schema. `EngineeringMemoryRepository
  .get_current_relationships` computes "latest per `relationship_key`" by
  reading the append-only log and taking the most recent version —
  exactly the same relationship this ADR already established between
  `CurrentKnowledgeProjection` and the append-only log for the eventual
  Neo4j sync (see "Persistence" above), now confirmed to hold one layer
  earlier as well: even *within* Postgres, "current" is a read-time
  computation over immutable history, never a second, independently
  mutable source of truth.
- **The `sequence` column (a Postgres `GENERATED ALWAYS AS IDENTITY`
  column on `knowledge_relationships`) is the permanent ordering
  mechanism for relationship versions — not a workaround.** Found
  necessary by a real integration test against real Postgres: `now()`/
  `CURRENT_TIMESTAMP` is transaction-scoped, so relationship versions
  written in the same commit (e.g. one indexing run's whole output,
  persisted via one batched commit) can share an identical `created_at`,
  making a timestamp-only ordering undefined. `sequence` is monotonic
  by construction regardless of transaction boundaries and batching
  strategy, so it remains correct even if write batching changes later —
  `created_at` stays for human/audit readability only, never for
  ordering.
- **A repository producing the same relationship on a later indexing run —
  even with an unchanged confidence outcome — appends a new version row.
  This is intentional append-only behavior, not a deduplication bug.**
  Engineering Memory's job is to record what the pipeline concluded on
  each run, not to decide whether that conclusion was "new information."
  Any future deduplication or compaction (e.g. collapsing runs of
  identical confidence states) is an optimization layered on top of the
  log, to be decided by a future RFC if write volume warrants it — not a
  correction to this RFC's behavior.

**RFC-05 — Cross-Repository Knowledge Integration.**
Reframed from the original "cut `cross_repo_linker` over to the new
engine" wording once RFC-04 existed: rather than replacing
`cross_repo_linker`'s edge-writing code path, RFC-05 additionally persists
every cross-repository relationship it produces (`CALLS_SERVICE`,
`SHARES_TOPIC`, `DEPENDS_ON_REPOSITORY`) into Engineering Memory, reusing
RFC-03's already-parity-tested `CROSS_REPO_VALIDATORS`/
`DefaultConfidenceEngine`/`build_candidate_pack_and_hypotheses`
(`app.knowledge_engine.validators.cross_repo`) unchanged. Neo4j's edge
output (`compute_edges`, `replace_cross_repository_edges`) is not touched
at all — this is additive persistence, the same shadow-mode discipline
RFC-02B/RFC-04 already established for single-repository relationships,
not the reasoning cutover the original wording implied. That cutover
(retiring `cross_repo_linker`'s own rule-based edge writing in favor of
projecting Neo4j edges from Engineering Memory) is deferred to RFC-05B
(Engineering Knowledge Materialization) — Engineering Memory did not
contain any cross-repository knowledge before RFC-05, so projection had
nothing to read; RFC-05 is the prerequisite, not the projection itself.
**Implemented 2026-08-02.** `app.indexer.graph.cross_repo_memory
.persist_cross_repo_relationships` runs after `relink_account`'s Neo4j
edge-write loop, on its own independent `AsyncSession` (`AsyncSessionLocal`,
not `relink_account`'s own `db`) — found and fixed before shipping, not
assumed safe: `EngineeringMemoryService`'s store methods commit
internally, and `relink_account` holds a `pg_advisory_xact_lock` on `db`
for its entire duration specifically so the lock survives until the
*caller* (`run_indexing_job`) commits (`tests/integration
/test_finding3_concurrent_relink_repro.py`). Committing on `db` from
inside `relink_account` would have released that lock early, silently
reopening the exact concurrent-relink race Finding #3 closed. Verified via
`test_finding3_concurrent_relink_repro.py` still passing unchanged after
this RFC, plus a fresh audit against 100% of `CROSS_REPO_LINK_RULES`'
three relationship types (all three already had `Hypothesis`/validator
coverage from RFC-03's parity work — none bypass Engineering Memory now).
*Tests*: existing cross-repo-linker integration/unit tests pass with zero
diff in produced edges (untouched code path); new
`tests/integration/test_cross_repo_memory_persistence.py` proves
persistence directly against real Postgres, matching versus non-matching
pairs.
*Rollback*: remove the one `persist_cross_repo_relationships` call site in
`relink_account`; Neo4j behavior is unaffected either way.
*Migration*: none — same edges, same Neo4j shape; Engineering Memory gains
rows via the already-existing `knowledge_relationships`/
`engineering_evidence_packs` tables (RFC-04), no schema change.
*Success criteria*: zero-diff edge output for the full existing
test-fixture account (met, code path unchanged); every cross-repository
relationship type persisted into Engineering Memory (met, 3/3).

**RFC-05B — Engineering Knowledge Materialization.**
Numbered out of the original sequence — same precedent as RFC-02B — since
this need only became concrete once RFC-05 finished (referred to as
"RFC-06" in the implementation conversation this entry was written from;
renamed here so it doesn't collide with the roadmap's pre-existing RFC-06,
below). Rebuilds Neo4j entirely from Engineering Memory +
`EngineeringEvidencePack`, via a new, pure projection layer:
`app.knowledge_engine.materializer.materialize_repository_graph`/
`rematerialize_repository_graph`. No validators, no confidence
computation, no generators, no LLMs, no source access — every property it
writes already exists in Postgres, recovered verbatim or via one
deterministic step (e.g. a set intersection recovering `SHARES_TOPIC`'s
`topics` list from raw per-side evidence — not a new inference, since the
relationship's existence and confidence are already established before
this ever runs).
**Audit before implementation** confirmed the evidence pack captures every
`GraphNode`'s full `properties` dict verbatim
(`deterministic_generator.py`'s `_node_evidence_item` stores
`json.dumps(node.properties)`) and every single-repository `GraphEdge`'s
full properties likewise — lossless already, no gap. The one real gap
found: `CALLS_SERVICE`'s `via` property (the Feign client's own `name`)
was never captured anywhere in the cross-repository evidence pack.
Smallest additive fix: `_feign_candidate`
(`app.knowledge_engine.validators.cross_repo`) now also records `via` on
each `feign_client_target_match` evidence item — no change to the
validator's own verdict logic, confidence, or persistence.
**Two design decisions made during implementation, not before:**
1. Single-repository edges are reconstructed from the evidence pack's
   `graph_edge:*` items directly, not from `KnowledgeRelationship` — found
   that `graph/builder.py` can legitimately emit two edges sharing one
   `(source_id, type, target_id)` triple with different properties (e.g.
   two Kafka-producer methods on one class, same topic), which
   `KnowledgeRelationship`'s per-triple identity would silently collapse
   to one. `KnowledgeRelationship` is used only to attach `confidence` to
   each matching edge, not to source topology, for single-repository
   edges. Cross-repository relationships have no such multiplicity
   (`build_candidate_pack_and_hypotheses` produces at most one hypothesis
   per type per repository pair), so `KnowledgeRelationship` is sufficient
   as the sole topology source there.
2. `confidence` is a new edge property, additive relative to both direct-
   write paths: `graph/builder.py`'s edges never carried one, and
   `cross_repo_linker.compute_edges` (still the live write path, untouched
   by RFC-05) writes the legacy `structural`/`heuristic` strings, not the
   new `ConfidenceState` vocabulary. Materialized graphs carry the new
   vocabulary on every edge — the intended end state ("Neo4j becomes a
   synced, rebuildable projection" of Engineering Memory, confidence
   included), not an attempt to byte-match the legacy write path for that
   one field.
*Tests*: `tests/integration/test_materializer_replay.py` — real clone,
parse, index, Neo4j delete, replay, and comparison against the original
graph (node/edge counts, ids, labels, properties all asserted equal
excluding `confidence`; `confidence` asserted separately to match
Engineering Memory's current state for every edge).
*Rollback*: the materializer is never called by any existing write path
(`replace_repository_graph`/`replace_cross_repository_edges` are
untouched) — deleting `materializer.py` and its one new
`EngineeringMemoryService.list_evidence_packs` passthrough method fully
reverts this with no other change.
*Migration*: none — no schema change; `list_evidence_packs` is a
passthrough to the repository layer's already-existing method.
*Success criteria*: identical node count/ids/labels/properties and
identical edge count/topology/non-confidence-properties, proven against a
real fixture repository; materialized confidence matches Engineering
Memory's current state for every edge (met — see test above). Full
reconstruction from Engineering Memory + evidence packs alone, with no
source-code re-reading, confirmed for every property this RFC's audit
found (`via` gap closed; TestRail's separate graph remains explicitly out
of ADR-0018's scope, never claimed reconstructable).

**RFC-06 — LLM HypothesisGenerator (the Frontier Hypothesis Generator),
shadow mode.**
**Implemented 2026-08-02**, with two deliberate deviations from this
entry's original sketch, both explained rather than silently substituted:

1. Input curation does NOT reuse `context_pipeline.reasoning.curation
   .curate()` — audited first, not assumed. That function ranks
   components against a ticket's own text via a hop-bounded neighborhood
   traversal seeded from request-matching anchors; none of that exists
   here (no ticket, no request, no seeded neighborhood — just one
   repository's own evidence). Built a small, new, generic module instead
   (`app.knowledge_engine.evidence_curation`), reusing only the *pattern*
   (budgeted, honest excluded-count) for a different scoring problem
   (kind-diversity sampling).
2. `shadow_runner.py` does NOT fully replace its one hardcoded
   deterministic-generator call with a registry loop, per this RFC's own
   explicit instruction not to modify existing generators (nor,
   transitively, their call site) — the deterministic generator stays
   directly instantiated, unchanged, since its identity depends on the
   parsed `ArchitectureModel`'s language/framework, known only at that
   call site. `app.indexer.hypotheses.generator_registry` is a second,
   parallel registry loop for every OTHER generator (the Frontier LLM
   generator today; Runtime/Documentation/Infrastructure/API/Security/Git
   History/Human generators are its intended future entries) — same
   isolation guarantee RFC-02B proved for one generator (a failing
   generator never affects another's output or persistence), reached via
   two isolated loops instead of one, not a smaller guarantee.

**Architectural finding, resolved before implementation** (Finding 1):
`HypothesisGenerator.generate(pack)` takes only the evidence pack —
correct, unchanged — but no evidence of README/manifest/config content
existed anywhere, and the cloned repository is torn down before any
generator runs. Resolved additively: `app.indexer.hypotheses
.repository_evidence.extract_repository_evidence` runs inside
`index_repository`'s existing clone-lifetime block, producing new,
generator-agnostic evidence kinds (`repository_readme`,
`repository_manifest`, `repository_architecture_doc`,
`repository_config`, `repository_metadata`) via a small, explicit,
safety-conscious filename allowlist (never `.env`/key/credential-shaped
files) merged into the same pack every generator already reads — not
LLM-specific, and the deterministic generator is unaffected (it only ever
reads `graph_node:*`/`graph_edge:*` kinds).

**`GeneratorPolicy` (Finding 2's resolution)**: `app.knowledge_engine
.contracts.generator_policy.GeneratorPolicy` — a single async
`should_run(context) -> bool` decision point, not a plain `enabled: bool`
field, so `manual`/`scheduled`/`webhook-triggered`/`budget-limited`/
`premium-only` execution modes are each a new `GeneratorPolicy`
implementation later, never a rewrite of `shadow_runner.py`'s loop.
`StaticGeneratorPolicy` (the only concrete implementation this RFC needs)
reads `Settings.enable_frontier_llm_generator` (default `False`) — off by
default specifically so a real LLM call, its cost, and its new external-
dependency failure mode are opt-in, never silently added to every
production indexing run. The registry's generator `factory` is called
only after the policy passes, so `create_llm_provider()` (which validates
API-key configuration) never runs, and never fails, while the feature is
off.

**Vocabulary, deliberately fixed and small** (not LLM-invented): 13
`OWNS_*`/`CONTAINS_*`/`INTEGRATES_WITH_*` capability relationship types,
source always the repository's own node, target a synthetic
`{repository_id}:capability:{slug}` entity — kept fixed so the same
repository re-analyzed on a later commit converges on the same entity id
(RFC-04's `relationship_key` versioning depends on this), and scoped to
single-repository claims only for v1 (no cross-repository vocabulary —
this generator's only input is one repository's own pack, with no
knowledge of what other repositories exist; a cross-repository LLM
generator, given two repositories' evidence the way RFC-05's
`build_candidate_pack_and_hypotheses` is, is natural future work, not
this RFC's).
*Tests*: `tests/unit/indexer/test_repository_evidence.py` (extraction,
including the secret-filename exclusion guard),
`tests/unit/knowledge_engine/test_evidence_curation.py`,
`tests/unit/knowledge_engine/test_generator_policy.py`,
`tests/unit/indexer/test_llm_generator.py` (fake `ILLMProvider`, no
network/API key), and
`tests/integration/test_frontier_llm_generator_pipeline.py` — real clone/
parse/Neo4j/Postgres, fake provider, proving the generator's hypotheses
land at `ConfidenceState.CANDIDATE` (no validator recognizes this
vocabulary yet — correct, not a gap to close here), persist into
Engineering Memory, are never surfaced by the materializer (proven
directly, not assumed), and leave the deterministic pipeline's own output
unchanged.
*Rollback*: `Settings.enable_frontier_llm_generator=False` (already the
default) — the registry then never constructs the provider at all.
*Migration*: none — no schema change; new evidence/relationship
vocabulary only.
*Success criteria met*: generator produces hypotheses from real evidence;
existing validators correctly decline to confirm an unrecognized
vocabulary rather than crashing or false-confirming; Engineering Memory
stores the results; materializer and deterministic pipeline provably
unaffected. Precision/recall measurement and cost-per-run budgeting
(this entry's original success criteria) remain explicitly deferred until
the feature is actually enabled for real repositories — out of this RFC's
scope, which was proving the plugin mechanism, not evaluating it.

**RFC-06B — Evidence-Semantic Validator Architecture.**
Closed the gap RFC-06 left open by design: the Frontier LLM generator's
13-type capability vocabulary had zero validator coverage, so every
hypothesis landed at `ConfidenceState.CANDIDATE` regardless of how good
the evidence actually was. **Implemented 2026-08-02**, entirely additive —
no existing validator, the `KnowledgeValidator` interface, or any
persistence/confidence code changed.

**Design**: one reusable class, `EvidenceKeywordValidator`
(`app.knowledge_engine.validators.evidence_keyword`), configured per
evidence domain (manifest, documentation, configuration, dependency) and
instantiated four times — never one class per language or framework.
Confirmation is deterministic substring matching of a small, per-
relationship-type TECHNOLOGY keyword table (`postgres`, `kafka`, `redis`,
`jwt`, ... — never a language name) against the hypothesis's own cited
evidence text, the same "narrow, explainable pattern match" precedent
`curation.py`'s `_REUSE_NAME_RE` and `classification.py`'s `is_test`
detection already established elsewhere in this codebase. Recognizing a
new technology is a keyword-table entry, never a new class — the
"self-improving without hardcoded language validators" property this RFC
was scoped to deliver. Reliability tier fixed at 1 (heuristic) for every
instance, matching `cross_repo.py`'s `DependencyCoordinateValidator`'s own
tier for a comparable name-similarity signal; only ever returns `confirms`
or `no_signal`, never `contradicts` (absence of a keyword in incomplete
evidence is not proof of absence, the same discipline every existing
validator already follows).

**`ALL_VALIDATORS`** (`app.knowledge_engine.validators.registry`) — the
union of deterministic-structural, cross-repository, and evidence-keyword
validator families, now `to_knowledge_relationship`'s default `validators`
parameter. Safe to combine unconditionally: `run_validators` only ever
dispatches a validator to a hypothesis whose type is in that validator's
own `applies_to`, so combining changes nothing for any existing
relationship type — proven, not assumed
(`test_all_validators_parity_for_deterministic_hypothesis`).

**Parallel execution** — `run_validators` now runs every applicable
validator concurrently via `asyncio.gather(..., return_exceptions=True)`
rather than a sequential loop, while remaining provably deterministic:
every validator reads a frozen `pack` and never mutates shared state, and
results are reassembled in the fixed order validators were selected in,
never completion order. Isolation is unchanged — one validator's exception
still never discards another's result, verified under actual concurrent
scheduling, not just sequential try/except (`test_one_validator_raising
_does_not_discard_the_others`).

**Extension point (Phase 4's requirement)**: a new validator is one class
(or, more often, one more `EvidenceKeywordValidator(...)` instance with
its own keyword table) plus one tuple entry in `EVIDENCE_KEYWORD_VALIDATORS`
or a sibling family — no change to `run_validators`, `ALL_VALIDATORS`'s
composition logic, `to_knowledge_relationship`, or any existing validator.
`RuntimeValidator`/`OwnershipValidator`/`ApiContractValidator` were
deliberately not built now — no evidence source for runtime traces, git
history/ownership, or API contracts exists yet (those are the Runtime/Git
History/API generators' own future work, per RFC-06's "Future Generators"
list); building a validator with nothing to validate against would be
exactly the speculative infrastructure ADR 0018's implementation rules
reject.
*Tests*: `tests/unit/knowledge_engine/test_evidence_keyword_validator.py`
(per-instance confirm/no_signal/citation-scoping/contradiction-never),
`tests/unit/knowledge_engine/test_validator_registry_parallel.py`
(determinism under concurrency, failure isolation, `ALL_VALIDATORS`
parity), and a new integration test in
`test_frontier_llm_generator_pipeline.py` proving an LLM hypothesis whose
cited manifest evidence a validator can confirm rises from `CANDIDATE` to
`LIKELY` — the concrete fix this RFC exists to prove, not just its
mechanism.
*Rollback*: revert `to_knowledge_relationship`'s default back to
`DETERMINISTIC_STRUCTURAL_VALIDATORS`, or drop `EVIDENCE_KEYWORD_VALIDATORS`
from `ALL_VALIDATORS`'s composition — either is a one-line, isolated diff.
*Migration*: none.
*Success criteria met*: existing deterministic/cross-repository validators
unaffected (parity-tested); Frontier hypotheses remain `CANDIDATE` when no
validator's evidence domain is cited, and rise above it when one is,
proven against real evidence, not asserted; adding
`EVIDENCE_KEYWORD_VALIDATORS`' four instances required zero changes to any
existing validator or the pipeline; validator failures still never fail
indexing; execution is deterministic under real concurrency.

**RFC-06C — Confidence Explainability.**
Requested as "Evidence Fusion"; audited first, not built as asked.
`DefaultConfidenceEngine.aggregate` already performs evidence fusion —
combines every confirming `ValidationResult` across validators into one
`ConfidenceModel`, already deduplicated by domain (`confirming_source_types`
is a *set*, so two validators reporting the same `source_type` never
double-count), already cross-domain-weighted (`max_confirming_reliability
_tier`), already capable of promoting multiple weak signals into a
stronger conclusion (two independent tier-3 confirmations -> `VERIFIED`,
one alone -> only `HIGHLY_LIKELY`). Building a second "fusion" layer in
that same slot would have duplicated a component this ADR's own RFC-03
approval declared frozen ("`DefaultConfidenceEngine` is deterministic,
incremental, and monotonic"; "confidence is derived only from
`ValidationResult`s") — flagged before writing any code, and redirected to
the one thing that genuinely didn't exist: turning an already-final
`ConfidenceModel` into a human-readable account of *why*.
**Implemented 2026-08-02.** `app.knowledge_engine.explainability
.explain_confidence(confidence, validation_results) -> ConfidenceExplanation`
— pure, deterministic (every list built via `sorted(...)` over real
strings, never `frozenset` iteration order, which is not stable across
Python processes). Never recomputes `ConfidenceState`; reads
`confidence.state` and describes it. `DefaultConfidenceEngine.HIGH
_RELIABILITY_TIER`/`MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED` were made
public (rename only, zero logic change) so the explainer references the
engine's own real thresholds in its text rather than a second, hand-copied
constant that could drift out of sync.
Persisted alongside `KnowledgeRelationship`, per this RFC's explicit ask:
`ValidationResult`s are never persisted anywhere (transient, folded into
`ConfidenceModel` and discarded), so a `ConfidenceExplanation` computed
later from a stored row alone would already have lost information (which
specific domains confirmed vs. merely a count) — it must be computed once,
where `to_knowledge_relationship` still holds the full `ValidationResult`
list, then stored. `knowledge_relationships` gained one nullable JSON
column (`explanation`); `EngineeringMemoryService.store_relationship(s)`
gained an optional, positionally-aligned `explanation(s)` parameter,
defaulting to `None` — every pre-existing caller that omits it is
byte-identical to before this RFC.
*Tests*: `tests/unit/knowledge_engine/test_explainability.py` (domain
extraction, tie-break determinism, duplicate-source-type non-inflation,
repeated-call determinism, unrecognized-domain fallback rendering);
`tests/integration/test_confidence_explanation_persistence.py` (real
Postgres round-trip, batch alignment, backward-compatible omission);
`test_frontier_llm_generator_pipeline.py` extended to assert the
persisted explanation for a real `LIKELY`-confirmed hypothesis.
*Rollback*: stop passing `explanation`/`explanations` at the two call
sites (`shadow_runner.py`, `cross_repo_memory.py`) — every row simply gets
`explanation=None` again, no schema rollback required (column stays,
nullable, harmless if unused).
*Migration*: `c7d8e9f0a1b2` — additive nullable column, no backfill,
downgrade drops it cleanly (verified via upgrade/downgrade/upgrade against
real Postgres).
*Success criteria met*: deterministic and identical output across runs
(proven directly, including under reversed input order); no validator or
confidence-engine regression (full suite green); provenance preserved
(domain-level, from real `ValidationResult.source_type`, not a lossy
count); confidence only increases when `DefaultConfidenceEngine` itself
already decided it should (this module never influences that); duplicate
evidence (same `source_type` reported twice) does not inflate
`confirming_domains` (proven directly) — matching the engine's own
existing non-inflation guarantee, not a new one.

**RFC-06D — Learning & Feedback Engine.** *(Implemented 2026-08-02.)*
Audit found: `UserCorrection`/`EngineeringMemoryRepository.apply_correction`
existed since RFC-04 but had no caller anywhere in the codebase — users
could not approve, reject, or correct a relationship's confidence through
any API, and there was no feedback loop of any kind. `app.learning_engine`
(new package, sibling to `app.knowledge_engine`, never imported by it)
adds one: `LearningEvent` (new contract, reuses `CorrectionSource`
unmodified rather than duplicating its identity/trust shape),
`build_learning_event` (pure, deterministic mapping from a caller-stated
`RelationshipFeedback.kind` to an event type — never inferred from a
confidence-state diff, so intent is always explicit), `compute_statistics`
(pure aggregation: approval/rejection rate, per-relationship-type
breakdown, threshold-based repeated-false-positive signals, two-halves
rejection-rate trend detection — no ML, no LLM), `LearningEventRecord`
(new `learning_events` table, append-only, `sequence`-ordered exactly
like `KnowledgeRelationshipRecord`), and `LearningEngineService`
(orchestrates: always persists a `LearningEvent`; for the three kinds
that assert a relationship's state — approve/reject/correct_confidence —
also calls `EngineeringMemoryService.apply_correction`, RFC-04's own
method, reused unmodified, never duplicated). Three new REST endpoints
(`POST/GET /repositories/{id}/learning/feedback|events|statistics`) are
the first thing in this codebase that lets a human approve or reject a
`KnowledgeRelationship` at all.
*Explicitly not built here, per this RFC's own scope*: automatic prompt
evolution, validator/confidence calibration, a recommendation engine,
repository health scoring, org-wide learning, model benchmarking — every
one of these reads `LearningStatistics`/`learning_events` (keyed by
`relationship_type` and `generator_names`, the exact dimensions each of
them needs) without any schema change; none is implemented.
*Tests*: 23 unit (contract validation, event-mapping determinism per
feedback kind, statistics determinism/reproducibility, repeated-false-
positive threshold behavior, trend detection) + 8 integration against
real Postgres (feedback persists both rows, flags persist no correction,
approving/rejecting a nonexistent relationship 404s, repeated feedback is
provably append-only, statistics match real persisted rows, feedback
never mutates `KnowledgeRelationshipRecord` history — proven directly by
comparing row ids/state before and after).
*Rollback*: stop registering `learning_router`; `learning_events` table
stays, inert, harmless if unused. Downgrade migration drops it cleanly
(verified upgrade/downgrade/upgrade against real Postgres).
*Migration*: `b3c4d5e6f7a8` — one new, wholly additive table; no existing
table touched.
*Performance impact*: negligible — one small insert per feedback
submission (plus RFC-04's existing correction insert, unchanged);
statistics are computed over one repository's events on demand, not on
any indexing hot path. Zero impact on indexing, generation, validation,
or confidence computation — this package has no import path into any of
them.

**RFC-07 — First graph promotion for a language with no existing parser.**
`graph/builder.py` gains a `KnowledgeRelationship → GraphPayload` path;
`parsers/registry.py`'s hard `422` gate relaxed to fall through to the
generic pipeline when no dedicated parser exists. Promotion gated to
`Verified`/`Highly Likely` only.
*Tests*: end-to-end against a real fixture repo in the new language; every
promoted node/edge carries `discovery_source` for UI filtering.
*Rollback*: revert the language to `unsupported_repository`.
*Migration*: none for existing languages; new language routes through the
new path only.
*Success criteria*: non-empty, spot-checked-correct graphs for real repos
in the new language; tracked false-positive rate (via a user-facing
flag-as-wrong affordance) below an agreed bar before widening further.

**RFC-08 — Multi-provider consensus.**
Fan out `llm_generator.py` to 2+ `ILLMProvider`s; cross-provider agreement
counts as at most one `distinct_confirming_source_type` in the confidence
formula (never two, per the correlated-training-data caveat).
*Tests*: measurable precision lift over RFC-06's single-provider baseline
on the same held-out fixtures.
*Rollback*: config flag back to single provider.
*Migration*: none.
*Success criteria*: precision lift justifies the cost multiplier, reviewed
as an explicit go/no-go before becoming default.

**RFC-09 — Incremental evidence ingestion (runtime, docs, infra sources).**
`EngineeringMemory.append_evidence()` + delta-pack generators for one new
source type at a time (start with infrastructure manifests, lowest
staleness risk; runtime telemetry last, highest operational complexity).
*Tests*: a delta pack triggers only the generators that `consumes` its
source type, verified by asserting other generators are not invoked.
*Rollback*: stop calling `append_evidence` for that source; full-run
extraction continues to cover it on the next full re-index regardless.
*Migration*: none.
*Success criteria*: a relationship's confidence measurably updates between
full index runs when new delta evidence confirms or contradicts it,
without a full re-index being triggered.

Each RFC lands independently; Java/Spring Boot and Python indexing behavior
is unchanged in every observable way through RFC-05, and byte-for-byte
verified unchanged at RFC-05 specifically via the zero-diff success
criterion above.

## Consequences

- Neo4j stops being the durable system of record and becomes a synced,
  rebuildable projection — a deliberate, permanent shift from today's
  `replace_repository_graph` model, not a temporary state.
- `EngineeringMemory`'s Postgres footprint grows without bound for
  `Hypothesis`/`ValidationResult`/correction/confidence-history rows by
  design (the audit trail is the point); only raw `EvidencePack` blobs are
  subject to archival.
- Every new evidence source or relationship type is a registry entry, not
  a schema migration — but the registries themselves become a
  long-term-maintained public surface, and a value entered into them can
  never be renamed once used, only deprecated.
- `SpringBootJavaParser`/`PythonParser` are permanently retained as the
  platform's calibration reference, not deprecated once the generic path
  matures — removing them would remove the only ground truth the rest of
  the system is measured against.
- LLM-derived relationships never reach `Verified` confidence from a
  single provider's agreement alone; multi-source or multi-provider
  corroboration is structurally required, which bounds LLM API cost growth
  but also bounds how much of the graph can be LLM-sourced without
  additional deterministic evidence (infra manifests, config, docs) to
  corroborate against.
