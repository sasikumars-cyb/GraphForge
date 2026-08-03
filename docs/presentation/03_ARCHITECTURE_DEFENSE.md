# 03 — Architecture Defense (Presenter 2)

## Architecture diagram (whiteboard-ready)

```
Indexer (tree-sitter, deterministic)
  → EngineeringEvidencePack
  → HypothesisGenerator(s) [deterministic | rule | LLM]
  → KnowledgeValidator(s) [always deterministic, never LLM]
  → ConfidenceEngine [incremental, monotonic, 6-state]
  → KnowledgeRelationship  →  Engineering Memory (Postgres, append-only)
                                   │
                                   ▼ (materializer, replay-tested)
                              Neo4j (derived, rebuildable projection)
                                   │
                                   ▼
                     Engineering Intelligence Service Layer (LLM-free)
                                   │
                                   ▼
                      Frontier Agents (narrate, never assert)
                                   │
                                   ▼
                            REST API → React SPA
```

## Component responsibilities (one line each — memorize)

- **Indexer**: deterministic parsing only, tree-sitter, no AI.
- **Knowledge Engine**: the 5-stage pipeline; owns the evidence→knowledge
  promotion rules.
- **Engineering Memory**: append-only Postgres log; the actual source of
  truth.
- **Neo4j**: a synced, rebuildable index optimized for traversal — not
  the source of truth.
- **Engineering Intelligence Service Layer**: 6 services, zero LLM calls,
  zero NL parsing — pure fact computation.
- **Frontier Agents**: 3 pure hooks per agent; narrate computed facts,
  never originate them.
- **Orchestrator**: manifest registry + selector + run coordinator; adding
  agent #N touches only its own module.

## End-to-end flow (say this out loud, don't just point at the diagram)

"A repository gets cloned once, parsed deterministically into evidence.
Every hypothesis about what that evidence means — whether from a
deterministic parser or an LLM — has to pass through the same validator
gate before it's trusted. What survives gets appended, never overwritten,
into Postgres. Neo4j is rebuilt from that log, proven by a real replay
test. Everything above that — services, agents, the API — reads from
Neo4j and never touches the LLM to decide what's true, only to explain
it."

## AWS deployment (Presenter 2's second slide)

- **ECS Fargate** ×2 services (backend, frontend) — chosen over EC2 (no
  GPU/bin-packing need), EKS (2 services doesn't earn Kubernetes'
  complexity), App Runner (no VPC-native Postgres/Neo4j access, weak IAM
  granularity), Lambda (agent runs are long-running and stateful
  mid-flight — wrong shape). `docs/deployment/02_INFRASTRUCTURE.md` §
  Compute decision record.
- **RDS PostgreSQL** (Multi-AZ) + **Neo4j** (Aura or self-hosted EC2, no
  AWS-managed option exists).
- **No static AWS keys anywhere** — Bedrock via boto3's default credential
  chain through the ECS Task Role; confirmed directly in
  `bedrock_provider.py`'s own docstring.
- **Backend `desiredCount` fixed at 1** — a real, honest constraint (not
  hidden): background agent execution uses in-process `asyncio.Task`,
  which doesn't survive a restart or scale across replicas yet. State
  this proactively if asked about scaling — see `12_REALITY_CHECK_PRESENTATION.md`.

## Neo4j / Postgres split — the single best "why" to have ready

**Why is Neo4j not your source of truth?** Because "source of truth" here
means append-only, auditable, never-silently-mutated history — and a
graph database optimized for traversal isn't naturally shaped for that.
`replace_repository_graph` used to fully replace the graph on every
re-index with zero history. ADR 0018 inverts this deliberately: Postgres
(`EngineeringMemory`) is the append-only log; Neo4j is derived and
rebuildable from it — proven, not asserted, by a real replay test
(`tests/integration/test_materializer_replay.py`) that deletes the graph,
rebuilds it from Postgres alone, and diffs node/edge/property equality.

**Honest caveat to state proactively**: the materializer is tested and
proven, but not yet the *live* write path — `replace_repository_graph`
still writes Neo4j directly today. Say this before a judge finds it —
`docs/handbook/16_REALITY_CHECK.md`.

## Engineering Memory — why append-only

Because the audit trail *is* the capability. Nothing in `Hypothesis`,
`ValidationResult`, `UserCorrection`, or confidence-state transitions is
ever edited or deleted, only superseded. This is what makes "confidence
history" a real, queryable feature instead of a promise with nothing
behind it.

## Materializer — the concrete proof point

`app.knowledge_engine.materializer.materialize_repository_graph` — pure
projection, zero LLM, zero validators, zero source access. Every property
it writes already exists in Postgres, recovered verbatim or via one
deterministic step. This is your strongest "we don't just claim it, we
tested it" moment — reference the replay test by name if pressed.

## Cross-repository reasoning — say the honest version unprompted

Cross-repo relationships (`CALLS_SERVICE`, `SHARES_TOPIC`,
`DEPENDS_ON_REPOSITORY`) are computed and persisted into Engineering
Memory (RFC-05), but the 24-repo validation suite found and documents two
real, current precision gaps: Feign name-matching can't bridge a
`<domain>-service-<language>` naming convention, and Kafka topic
detection is literal-string-only with no shared-SDK-wrapper support. State
these as "known, numbered, root-caused" — that phrase itself signals
engineering maturity to a judge.

## Scaling — the honest ceiling

- Graph traversal is hop-bounded per agent (`max_graph_hops`) —
  predictable latency as the graph grows.
- Indexing does **not** scale past "a handful of repos per org" today —
  full-clone-per-index, no incremental re-indexing (`ROADMAP.md`
  Technical Debt). Say this directly if asked "how does this scale to a
  1000-repo org" — don't improvise a number.
- Backend fixed at `desiredCount=1` (see AWS section above) — the honest,
  named reason to not scale horizontally yet.

## Trade-offs / decision rationale — the "why not X" quick table

| Question | One-line answer |
|---|---|
| Why not Neo4j as source of truth | Traversal-optimized stores aren't append-only-shaped; Postgres log + Neo4j projection gives both traversal speed and real history |
| Why not a single mutable confidence column | Can't answer "was this ever wrong, when did we find out" — six-state, transition-logged confidence can |
| Why not EKS | 2 services, no CRD/operator need — complexity not earned yet |
| Why not Lambda for agents | Long-running, stateful mid-flight LLM calls fight Lambda's execution model |
| Why not cut over the materializer immediately | Shadow-mode discipline — prove it's correct before betting production writes on it |

## Whiteboard explanation script

Draw the pipeline left to right, five boxes. At "Hypothesis," say: "this
is the only place an LLM's opinion enters the system — as a proposal, not
a fact." At "Validation," say: "always deterministic, never calls an LLM
— a validator that asks an LLM 'does this seem right' isn't validating,
it's generating a second, uncoordinated guess." At "Confidence," say:
"six states, computed only from independent confirmations, never from the
generator's own self-reported confidence." Land on: "everything to the
right of this line is Postgres. Neo4j is a rebuildable cache of it."

## Expected architecture questions with answers

**Q: What happens if Neo4j goes down?**
A: Read-path queries (blast radius, dependency lookups) fail until it's
back, but nothing is lost — it's rebuildable from Engineering Memory.
This is architecturally why the materializer exists, even pre-cutover.

**Q: What happens if Postgres goes down?**
A: This is the real outage — it's the source of truth. RDS Multi-AZ gives
automatic failover within a region; that's the current DR posture
(`docs/deployment/04_SECURITY.md` § Disaster recovery). Cross-region DR
is explicitly not justified yet by any current requirement.

**Q: How many services/repos have you actually indexed?**
A: Point to the 24-repository validation suite as the real, running
proof — not a toy demo count.

**Q: Why FastAPI + async SQLAlchemy?**
A: Reused wholesale from the ChangeGuard predecessor — not a hackathon
choice, a production-lineage choice (`ARCHITECTURE.md`).

**Q: Is this multi-tenant?**
A: Designed for it — `organization_id` scoping is specified at the
GraphWriter/repository-query layer in the architecture docs. State
honestly: this handbook's audit confirmed the design intent in writing,
not every query path's enforcement depth in code — don't overclaim
verification you haven't done.
