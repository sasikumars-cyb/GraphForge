# Section 13 — Whiteboard Exercises

Each exercise: draw the boxes named, in order, and narrate the one
sentence under each arrow. These are the same flows already detailed in
earlier sections — this page is the "stand at the whiteboard" compression.

## 1. Indexing

```
[git clone] → [language detector: pom.xml→Spring Boot? / Python?]
            → [ILanguageParser: SpringBootJavaParser | PythonParser]
            → [ArchitectureModel]
            → [graph/builder.py] → [GraphPayload]
            → [IGraphRepository.replace_repository_graph] → [Neo4j]
```
Narrate: "Everything up to `ArchitectureModel` is tree-sitter, literal
matching only — a Kafka topic passed as a variable is silently skipped,
never guessed. `IGraphRepository` means Neo4j is the only thing that
would change if we swapped graph stores." (ADR 0007)

Side branch, drawn alongside, not before: `ArchitectureModel` also feeds
`app/indexer/evidence/` → `EngineeringEvidencePack`, which is the actual
input to the Knowledge Engine pipeline below — indexing produces *two*
outputs from one parse, not one.

## 2. Engineering Memory

```
[EngineeringEvidencePack] → [HypothesisGenerator(s)] → [Hypothesis, immutable]
   → [KnowledgeValidator(s), concurrent] → [ValidationResult, immutable, never persisted long-term]
   → [ConfidenceEngine.aggregate, incremental+monotonic] → [ConfidenceModel + transition, immutable]
   → [KnowledgeRelationship row appended] → knowledge_relationships (Postgres, append-only)
```
Narrate: "Nothing in this row is ever updated — a repeat run appends a new
row even with unchanged confidence. 'Current' is `MAX(sequence)` per
`relationship_key`, read at query time, not a stored flag." (ADR 0018 RFC-04)

## 3. Materializer

```
knowledge_relationships (Postgres, append-only)
engineering_evidence_packs (Postgres blobs)
        │  materialize_repository_graph (pure, no LLM, no validators, no source access)
        ▼
   GraphPayload  →  (would write to) Neo4j
```
Narrate: "This box is fully built and replay-tested — delete Neo4j,
rebuild from Postgres alone, diff against the original: identical node/
edge counts, ids, properties. But draw the dotted line to Neo4j as dotted,
not solid — no production write path calls this today. `replace_repository_graph`
still writes Neo4j directly." (ADR 0018 RFC-05B; see 16_REALITY_CHECK.md)

## 4. Cross-Repo

```
Repo A evidence pack ─┐
                       ├─► build_candidate_pack_and_hypotheses
Repo B evidence pack ─┘         │
                                 ▼
                    CROSS_REPO_VALIDATORS (Feign name match / Kafka topic overlap / dependency coordinate)
                                 │
                                 ▼
                    DefaultConfidenceEngine (same engine as single-repo)
                                 │
              ┌──────────────────┴───────────────────┐
              ▼                                        ▼
   Neo4j (compute_edges, direct write —          knowledge_relationships
   untouched by RFC-05, still the live path)      (RFC-05 persistence, additive)
```
Narrate: "Two independent outputs from one validated hypothesis — the
live graph write nobody changed, and the new Engineering Memory
persistence layered alongside it. Draw the honest gap here: Feign name
matching only strips a trailing `-service`/`-client`/`-api` suffix, so
`inventory-service-python` never matches `inventory-service` — zero
`CALLS_SERVICE` edges for that naming convention, live in the validation
suite today." (ADR 0018 RFC-05; validation guide Known Gap 2)

## 5. Validator Pipeline

```
Hypothesis ──► [Validator 1] ──► confirms/contradicts/no_signal ─┐
           ──► [Validator 2] ──► confirms/contradicts/no_signal ─┼─► fixed-order results
           ──► [Validator N] ──► confirms/contradicts/no_signal ─┘   (asyncio.gather, one failure
                                                                       never discards another's result)
```
Narrate: "Concurrent, but the output order is fixed by which validators
were selected, never by which finished first — that's what keeps this
provably deterministic under real concurrency, tested directly, not
assumed." (ADR 0018 RFC-06B)

## 6. Confidence Pipeline

```
prior ConfidenceModel (or None) ──┐
new ValidationResult ─────────────┼──► aggregate() ──► new ConfidenceModel
                                   │        │
                                   │        └── monotonic: contradiction never regresses
                                   │            a stronger already-recorded confirmation, and
                                   │            vice versa
                                   └── incremental: never re-scans prior ValidationResults,
                                       only the running set/max carried in ConfidenceModel
```
Narrate: "The two fields that make this incremental — `confirming_source_types`
(a set, not a count) and `max_confirming_reliability_tier` — were both
added *during* RFC-03 implementation because the original contract
couldn't actually compute a correct answer without them. Worth saying out
loud: that's the platform catching its own design bug before shipping,
not after."

## 7. Learning Pipeline

```
RelationshipFeedback{kind: approve|reject|correct_confidence, ...}
        │  build_learning_event (pure, deterministic mapping)
        ▼
   LearningEvent ──► learning_events table (append-only)
        │
        └─(for approve/reject/correct_confidence only)─► EngineeringMemoryService.apply_correction
                                                                 │
                                                                 ▼
                                                   re-enters validator/confidence pipeline
                                                   (unless kind="human" with trust_level=1.0
                                                    → direct override, still a new transition)
```
Narrate: "Two tables, two purposes: `learning_events` is the raw feedback
log; `apply_correction` is RFC-04's original method, reused unmodified —
this package adds zero new correction logic, only the first caller that
ever invokes it." (ADR 0018 RFC-06D)

## 8. Agent Pipeline (Frontier Agent)

```
POST /agent-runs → Orchestrator.registry (manifest lookup)
                  → preflight (DEPENDENCY_LLM / DEPENDENCY_NEO4J check)
                  → RunCoordinator (inject graph_repository if max_graph_hops>0)
                  → BaseFrontierAgent.run()
                        build_service_requests()  [pure]
                        ServiceExecutor.execute() [Engineering Intelligence Services, concurrent]
                        build_prompt()            [pure, may return None → skip LLM]
                        prompt_builder.run()       [invoke_llm_json, degrades to failed Evidence on error]
                        render_response()          [pure]
                        result_mapper.to_agent_output()
                  → Run/AgentStep persisted (Postgres) — even on failure
```
Narrate: "Three pure hooks per agent; everything with I/O lives once in
the base class. If `build_prompt` returns `None`, this agent never calls
an LLM at all for that run — that's a legitimate branch, not a failure."

## 9. End-to-end request flow (composed)

```
User request (Jira ticket text, free-text goal, or a direct repository query)
        │
   ┌────┴─────────────────────────────┐
   │ Context Discovery (ADR 0007/14-17)│  ← only for free-text/ticket-shaped requests
   │  deterministic investigation loop │
   │  → curated EvidencePackage        │
   │  → EngineeringUnderstanding (1 LLM call, grounded, graceful-degrade)
   └────┬─────────────────────────────┘
        │
   Planning / other SDLC agents consume EngineeringUnderstanding
        │
   For a direct repository/blast-radius/dependency question:
        │
   Frontier Agent (§8 above) → Engineering Intelligence Service Layer
        → reads materialized Neo4j (itself provably rebuildable from
          Engineering Memory, §3 above, though not yet built that way live)
```
Narrate close: "Two separate reasoning systems share one discipline —
deterministic gathering before any LLM call, LLM output that only narrates
or hypothesizes, never asserts unverified fact directly into either the
graph or the response. That discipline, not any single component, is
GraphForge's actual architecture."
