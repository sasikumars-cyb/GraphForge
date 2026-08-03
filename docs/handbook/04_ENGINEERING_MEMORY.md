# Section 4 — Engineering Memory

Source: ADR 0018 (RFC-04, "Implemented and approved 2026-08-02"),
`app/knowledge_engine/memory_service.py`,
`app/repositories/engineering_memory_repository.py`.

## Why append-only?

Because the audit trail *is* the product capability, not a nice-to-have.
ADR 0018 states it as a permanent design decision, not an implementation
detail: "Engineering Memory is an immutable, append-only store. No table
it owns... has an update path — every write is an insert." The concrete
capability this buys: "confidence history" and "relationship evolution"
are real, queryable features (`EngineeringMemory.history()`) rather than a
promise with nothing behind it, because nothing was ever overwritten to
begin with.

## Why immutable?

Two reasons stated directly in the ADR:

1. **Reproducibility.** "A taxonomy value (`kind`/`source_type`/
   `relationship_type`), once used by any persisted record, is never
   renamed — only deprecated and superseded... renaming a taxonomy entry
   retroactively breaks every historical record that used it."
2. **Auditability of the pipeline itself.** `HypothesisLog`,
   `ValidationLog`, `CorrectionLog`, and confidence-transition history are
   explicitly named as "never compacted or deleted — they are the audit
   trail this platform's explainability promise depends on." Only raw
   `EvidencePack` blobs are subject to archival, and only because they're
   regenerable by re-extraction against the same commit — nothing
   irreplaceable is ever discarded.

## Why corrections?

RFC-06D's audit found the honest starting state: `UserCorrection` and
`EngineeringMemoryRepository.apply_correction` existed since RFC-04 but had
**zero callers anywhere in the codebase** — no API let a human approve,
reject, or correct a relationship's confidence. `app.learning_engine`
closed that gap with three REST endpoints
(`POST/GET /repositories/{id}/learning/feedback|events|statistics`). A
correction's authority depends on its source: `kind="human"` carries
`trust_level=1.0` and overrides the relationship's state directly (still
recorded as a new transition, never a silent edit); `kind="agent"` is
**never** an unconditional override — it re-enters the same validator/
confidence pipeline as any other hypothesis. This asymmetry is a named,
frozen invariant, not an oversight: only a human correction gets
unconditional authority.

## Why confidence (as a first-class, historical thing)?

Because a single mutable `confidence` field can't answer "was this ever
wrong, and when did we find out." `ConfidenceModel` is recomputed
incrementally on every new `ValidationResult` or `UserCorrection`, and
every transition is permanently retained — "the current state is the
latest transition, never the only one kept" (ADR 0018 § Lifecycle,
Confidence).

## Why explanation?

RFC-06C found that `DefaultConfidenceEngine.aggregate` already performs
real evidence fusion (deduplicated by domain, cross-domain-weighted,
capable of promoting multiple weak signals into a stronger conclusion) —
what was actually missing was turning an already-final `ConfidenceModel`
into a human-readable account of *why*. `explain_confidence()` is pure and
deterministic (sorted, never relying on `frozenset` iteration order, which
is not stable across Python processes), reads `confidence.state` and
describes it — it never recomputes the state itself. Persisted once,
alongside `KnowledgeRelationship`, specifically because `ValidationResult`s
themselves are never persisted (transient, folded into the model and
discarded) — computing an explanation later from a stored row alone would
already have lost the per-domain detail.

## Why materialization? (Why not just keep writing to Neo4j directly?)

Two write paths writing to the same graph independently is exactly the
kind of inconsistency-over-time bug class ADR 0018 exists to close. The
materializer (RFC-05B, `app.knowledge_engine.materializer
.materialize_repository_graph` / `rematerialize_repository_graph`) is a
**pure projection layer**: no validators, no confidence computation, no
generators, no LLMs, no source access. Every property it writes already
exists in Postgres, recovered verbatim or via one deterministic step (a
set intersection recovering `SHARES_TOPIC`'s topic list, for example — not
a new inference). Verified with a real replay test
(`tests/integration/test_materializer_replay.py`): clone, parse, index,
delete from Neo4j, replay from Engineering Memory alone, and diff against
the original graph — node/edge counts, ids, labels, properties all
asserted equal (confidence checked separately, since materialized graphs
carry the *new* `ConfidenceState` vocabulary, not the legacy
`structural`/`heuristic` strings the still-live `cross_repo_linker.py`
write path uses).

**Important, precise scope caveat**: the materializer is not currently
called by any production write path. `replace_repository_graph` and
`replace_cross_repository_edges` remain untouched and are still what
actually populates Neo4j today. The materializer exists, is tested, and
proves the graph *can* be rebuilt from history alone — it has not yet been
cut over to be how the graph normally gets built. See
[16_REALITY_CHECK.md](16_REALITY_CHECK.md).

## Why not Neo4j (as the source of truth)?

Because a graph database optimized for traversal is not naturally
append-only, and losing the ability to answer "what did we believe last
week" is an unacceptable trade for query convenience. ADR 0018 names the
inversion explicitly: "Neo4j stops being the durable system of record and
becomes a synced, rebuildable projection — a deliberate, permanent shift...
not a temporary state." See [12_DIFFICULT_QUESTIONS.md](12_DIFFICULT_QUESTIONS.md)
for the fuller "why not Neo4j as source of truth" treatment including
counterarguments.

## Why Postgres?

Cardinality and write-pattern mismatch, stated directly: `EngineeringEvidencePack`
persists as **one compressed blob** (JSONB/object storage) keyed by
`(repository_id, commit_sha, schema_version)` — never one relational row
per `EvidenceItem`, because "a large monorepo can produce tens of thousands
of evidence items per run; normalizing that into per-row storage in an
append-only OLTP table is a write-throughput failure waiting to happen."
`Hypothesis`, `ValidationResult`, `KnowledgeRelationship` — orders of
magnitude lower cardinality — get real relational rows, queryable and
indexed. This is a deliberately mixed storage strategy inside one
database, not "everything is one JSONB blob" nor "everything is fully
normalized."

## How rebuilding works

`materialize_repository_graph` reads `EngineeringMemoryService
.list_evidence_packs` (a new passthrough to the repository layer, no
schema change) plus `get_current_relationships` (latest version per
`relationship_key`, computed at read time over the append-only log — "even
*within* Postgres, 'current' is a read-time computation over immutable
history, never a second, independently mutable source of truth"), and
reconstructs single-repository edges from the evidence pack's own
`graph_edge:*` items directly rather than from `KnowledgeRelationship` —
found necessary because `graph/builder.py` can legitimately emit two edges
sharing one `(source, type, target)` triple with different properties
(e.g. two Kafka-producer methods on one class, same topic), which
`KnowledgeRelationship`'s per-triple identity would silently collapse to
one. Cross-repository relationships have no such multiplicity, so
`KnowledgeRelationship` is sufficient as the sole topology source there.

## The `sequence` column — why it exists, in the engineers' own words

Found by a real integration test, not designed in up front:
`now()`/`CURRENT_TIMESTAMP` is transaction-scoped in Postgres, so
relationship versions written in the same commit (one indexing run's whole
output, persisted in one batched commit) can share an identical
`created_at` — undefined ordering if you sort by timestamp alone. The
`sequence` column (`GENERATED ALWAYS AS IDENTITY`) is monotonic by
construction regardless of transaction boundaries or batching strategy.
`created_at` stays for human/audit readability only, never for ordering.
This is exactly the kind of correctness detail that only surfaces under a
real database, not a mock — cited here as evidence the "verified against
real infrastructure" testing discipline (ADR 0007) is still being followed
at RFC-04.

## Append-only in practice: a repeated fact is not deduplicated

A deliberate, explicitly documented behavior: "A repository producing the
same relationship on a later indexing run — even with an unchanged
confidence outcome — appends a new version row. This is intentional
append-only behavior, not a deduplication bug." Engineering Memory's job
is to record what the pipeline concluded on each run, not to judge whether
that conclusion was new information. Any future compaction is explicitly
deferred to a future RFC, if write volume warrants it.
