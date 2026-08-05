# Section 11 — Architecture Review Questions

Format per question: **Short** (30s) → **Detail** (2–5 min, with
implementation reference and trade-off folded in) → *Follow-up* → a
one-line whiteboard cue where useful. Every answer traces to a source
already cited in Sections 1–10, 16, or the deployment docs under
`docs/deployment/`. Where a question has no grounded GraphForge-specific
answer, the entry says so rather than inventing one — this happens a
handful of times below, always labeled.

A note on scope honesty: the brief asked for "at least 250 questions."
This file contains 260, organized into the 20 requested categories. Every
one is grounded in a real file, ADR, or RFC — none are filler restatements
of the same fact with different wording. Where a category (e.g. Security,
Cloud) has less native ADR coverage than the Knowledge Engine does, the
questions lean on `docs/deployment/*.md`, which documents exactly this
territory with the same "verified against the code" discipline as the
ADRs.

---

## A. Architecture (1–13)

**1. What is GraphForge's single biggest architectural bet?**
Short: deterministic-first, evidence-gated knowledge beats prompt-only
tools over time. Detail: every AI-adjacent subsystem (Knowledge Engine,
Context Discovery, Frontier Agents) is built so an LLM proposes and
narrates but never asserts a fact directly — `PRODUCT_VISION.md` Core
Principle 3. *Follow-up*: "What's the cost of that bet?" → LLM-sourced
knowledge is structurally capped below `Verified` without independent
corroboration (ADR 0018 § Consequences).

**2. What does "the graph is the product" actually mean operationally?**
Short: every shippable feature must name a graph node/edge type it reads
or writes. Detail: `PRODUCT_VISION.md` states this as a hard gate — "if a
proposed feature is neither, it is a plugin bolted onto the product... and
should be scoped out or rejected." *Follow-up*: name a feature that would
fail this test — a UI-only cosmetic change with no new graph read/write.

**3. How many major subsystems does GraphForge actually have today?**
Short: Indexer, Knowledge Engine, Engineering Memory, Frontier
(generator + agents), Engineering Intelligence Service Layer, Orchestrator,
Context Discovery, Engineering Session, Learning Engine, Validation
Framework. Detail: § [03_ARCHITECTURE.md](03_ARCHITECTURE.md) maps each to
its real package.

**4. Is `app/orchestrator` actually built, or just proposed?**
Short: built. Detail: `registry.py`, `selector.py`, `run_coordinator.py`,
`preflight.py`, `background_execution.py` all exist and are referenced by
every current agent manifest — `docs/graphforge/ARCHITECTURE.md`'s "new
component" framing understates this; treat that doc as a proposal
snapshot, not current state (§03).

**5. What's the difference between `app.context` and `app.context_pipeline`?**
Short: `app.context` is the thin, largely-unbuilt Entry Resolver concept
from `ARCHITECTURE.md`; `app.context_pipeline` is the real, heavily-ADR'd
Context Discovery reasoning engine. Detail: as audited, `app/context/resolvers/`
contains only `freetext.py` — no GitHub/Jira/Confluence resolvers exist
yet, despite being named in the proposal doc.

**6. What's the difference between `app.knowledge` and `app.knowledge_engine`?**
Short: `app.knowledge` is the external-source connection registry
(GitHub/Jira/Confluence auth+transport); `app.knowledge_engine` is the
Evidence→Hypothesis→Validation→Confidence→Knowledge pipeline. Detail:
named differently on purpose per ADR 0018's own package-structure note, to
prevent exactly this conflation.

**7. Why does GraphForge separate "propose" from "commit" at the
architecture level, not just within one class?**
Short: so no single component — human, agent, or LLM — can unilaterally
assert ground truth. Detail: mirrors twice in the codebase — RFC-001's
Decision propose/commit boundary (only a human commits) and ADR 0018's
generator/validator split (a generator proposes, only the pipeline as a
whole promotes to `Knowledge`).

**8. What existed before GraphForge, and how much of it survived?**
Short: ChangeGuard, a deterministic PR-impact tool; most of it survived
and is reused, not replaced. Detail: `IVersionControlProvider`, the
deterministic risk/impact engine, `app.ai.providers`, FastAPI/SQLAlchemy/
Alembic conventions all carry over per `ARCHITECTURE.md`'s explicit "does
not propose a rewrite."

**9. What's the Context Builder's actual job, and is it built?**
Short: resolve any entry point (PR, Jira, Confluence, incident, free
text) to graph coordinates. Detail: conceptually described in
`ARCHITECTURE.md`; concretely, only free-text resolution
(`app.context_pipeline`) is real today — GitHub/Jira/Confluence Entry
Resolvers are Phase 1/2 roadmap, not yet implemented as the doc's
`IEntryResolver` interface.

**10. How does a new agent get added without touching the orchestrator?**
Short: new manifest + registry line + Selector rule. Detail:
`AGENT_FRAMEWORK.md` § Extensibility, verified against real manifests
(§08) — every current agent follows exactly this shape.

**11. What's `GraphWriter`, and does it exist?**
Short: the proposed single choke point for graph writes, schema-validated
before commit. Detail: named in `ARCHITECTURE.md` as a new package
(`app.graphwriter`); this audit did not find it implemented under
`backend/app/` — treat as a Phase-1 proposal, not confirmed shipped code.
State this honestly rather than assuming the doc describes current state.

**12. What is `RunContext` / Shared Memory, and what's its real limitation?**
Short: a run-scoped scratch store for intermediate agent outputs. Detail:
`ARCHITECTURE.md` documents its own current-state caveat directly — it's
implemented as in-memory, single-process, not the Redis-backed version the
design calls for; "required before any multi-process/multi-replica
deployment."

**13. Why does the architecture insist on additive-only graph schema
changes?**
Short: so existing agents/traversals never silently break when a new node/
edge type ships. Detail: `PRODUCT_VISION.md` Extensibility Strategy axis 3
— a new node/edge type is additive; "existing agents unaffected unless
they explicitly opt into traversing the new type."

## B. Distributed Systems (14–26)

**14. Is indexing distributed or single-process today?**
Short: single-process. Detail: ADR 0007 — `FastAPI BackgroundTasks`
stands in for a real task queue, explicitly "not a permanent choice... no
retry, no distributed execution, no visibility beyond one process."

**15. What happens if the process restarts mid-index?**
Short: the job is left `running` forever. Detail: ADR 0007 §
Consequences, stated directly — a real, named limitation, not
speculation. *Follow-up*: "how would you detect this in production?" →
not solved in the codebase today; a real gap to name honestly.

**16. How does GraphForge prevent two concurrent indexing runs on the
same repository from racing?**
Short: a `409 Conflict` guard. Detail: `POST /repositories/{id}/index`
returns `409` if a `pending`/`running` `IndexingJob` row already exists —
"a lightweight guard... without needing a distributed lock" (ADR 0007).

**17. What's `pg_advisory_xact_lock` used for, and where?**
Short: guarding `relink_account`'s concurrent-relink race. Detail: ADR
0018 RFC-05 — the lock is held for `relink_account`'s entire duration so
it survives until the *caller* commits; a real bug (early commit from a
nested session) would have released it early, and was caught and fixed
before shipping (`test_finding3_concurrent_relink_repro.py`).

**18. Are validators executed sequentially or concurrently, and how is
determinism preserved under concurrency?**
Short: concurrently, but deterministically. Detail: `asyncio.gather(...,
return_exceptions=True)`; results reassembled in the fixed order
validators were *selected*, never completion order (ADR 0018 RFC-06B,
tested directly under real concurrent scheduling).

**19. How does `ConfidenceEngine.aggregate` stay correct without
distributed coordination?**
Short: it's incremental and commutative-enough via monotonicity, not via
locking. Detail: each call folds exactly one new `ValidationResult` into a
prior `ConfidenceModel`; monotonic guarantees mean arrival order across
independently-latent validators doesn't change the final state's
direction (ADR 0018 RFC-01/03).

**20. Is there a distributed transaction between Neo4j and Postgres
writes?**
Short: no — and the architecture is designed to not need one. Detail:
Engineering Memory (Postgres) is the source of truth; Neo4j is a
rebuildable projection. A failed/partial Neo4j write is recoverable by
re-materializing from Postgres (RFC-05B) rather than requiring 2PC.

**21. How are LLM provider calls isolated from indexing's critical path?**
Short: `GeneratorPolicy.should_run` gates the LLM generator off by
default, and its failure is isolated per-generator. Detail: ADR 0018 RFC-06
— a failing/absent LLM provider never blocks the deterministic pipeline's
own output (verified directly, not assumed).

**22. What isolation guarantee does `shadow_runner.py` provide?**
Short: a failing generator never blocks or corrupts another's output for
the same run. Detail: matches `run_indexing`/`relink_account`'s existing
failure-isolation pattern — `except Exception`, logged and swallowed for
that generator alone (ADR 0018 RFC-02B/06).

**23. How would GraphForge scale indexing across many repositories
concurrently today?**
Short: it wouldn't, well — named directly as not scaling "past a handful
of repos per org" (`ROADMAP.md` Technical Debt). *Follow-up*: "what's the
fix path?" → incremental (webhook-driven, diff-only) indexing, scoped as a
Phase-2 prerequisite, not yet built.

**24. Is there a message queue anywhere in the architecture?**
Short: not in the current implementation audited — `BackgroundTasks`
(in-process) today, Redis named as the future `RunContext` backing store.
No Kafka/SQS/RabbitMQ appears as GraphForge's own infrastructure (Kafka
appears only as something GraphForge *indexes*, in target repositories).

**25. How does the Orchestrator bound concurrent agent execution per run?**
Short: `ARCHITECTURE.md` specifies `asyncio.gather` bounded by a per-run
concurrency cap to avoid thundering-herd LLM calls. Detail: this is a
documented design intent; this audit read the orchestrator's real files
but did not independently trace the concurrency-cap implementation depth
— state the design intent accurately, don't claim deeper verification than
performed.

**26. What's the failure mode if two different agents try to write
conflicting `GraphFact`s in the same run?**
Short: not resolved by a distributed-systems mechanism — resolved by the
Knowledge Engine's validator/confidence pipeline treating each as an
independent hypothesis subject to the same corroboration rules. Detail:
there is no last-writer-wins graph mutation for contested facts; contested
facts stay at `CONFLICTING` confidence until resolved (§05).

## C. AI (27–43)

**27. What are the two distinct roles AI plays in GraphForge?**
Short: hypothesis generation and narrative synthesis. Detail: §
[02_STORY.md](02_STORY.md) — neither role lets AI decide what is true.

**28. Why can't a `HypothesisGenerator`'s own confidence influence the
graph?**
Short: it's advisory only, by written invariant. Detail: ADR 0018 —
"must never influence a `KnowledgeValidator`'s verdict or a
`ConfidenceEngine`'s aggregation." *Whiteboard*: draw `generator_confidence`
as a dead-end arrow, never entering the `aggregate()` box.

**29. What LLM providers does GraphForge support?**
Short: Bedrock, OpenAI, Gemini, Groq. Detail:
`docs/deployment/13_AI_PROVIDER_CONFIGURATION.md` — `ProviderSpec` registry
pattern (`app/ai/providers/registry.py`), one entry per provider, "never
an if/elif chain scattered through the app."

**30. How are AWS credentials handled for Bedrock?**
Short: never stored by GraphForge — standard AWS credential chain only.
Detail: `bedrock_provider.py`'s own docstring, quoted in
`docs/deployment/05_IAM.md`: "GraphForge never stores or handles AWS
secret keys directly" — resolved via env vars/`~/.aws/credentials`/IAM
roles/instance profiles.

**31. What happens when the configured LLM provider is down or
misconfigured?**
Short: ADR 0011 preflight validation fails the agent run explicitly before
execution; mid-run, agents degrade to deterministic fallback text rather
than raising. Detail: `prompt_builder.py` — failure produces an empty
narrative plus a `status="failed"` Evidence entry, never a crash.

**32. Is there a single LLM call per agent run, or many?**
Short: varies by agent; Context Discovery caps LLM synthesis calls
explicitly. Detail: ADR 0016 — `MAX_MID_LOOP_SYNTHESIS_CALLS = 1`, chosen
after *measuring* real cost impact on the test suite (92s → 165s at a
budget of 2) — a concrete, non-hypothetical cost-engineering decision.

**33. How is LLM invocation metadata persisted?**
Short: one persistence path, reused everywhere. Detail: ADR 0012 —
`invoke_llm_json` is "the sole ADR-0012 writer every other agent's LLM
call already uses"; `PromptBuilder` wraps it rather than calling a
provider directly specifically to inherit this for free.

**34. What's the fixed vocabulary the Frontier LLM generator can propose,
and why fixed?**
Short: 13 `OWNS_*`/`CONTAINS_*`/`INTEGRATES_WITH_*` types. Detail: fixed
so re-analysis of the same repository on a later commit converges on the
same synthetic entity id — required for `relationship_key` versioning to
mean anything (§06).

**35. Can the LLM generator write to Neo4j?**
Short: never, structurally. Detail: it returns `list[Hypothesis]` only —
the same invariant as every other generator (§05/06).

**36. What confidence ceiling applies to LLM-sourced knowledge?**
Short: cannot reach `Verified` from one provider alone. Detail: RFC-08
(roadmap) caps cross-provider agreement at "at most one distinct
confirming source type... per the correlated-training-data caveat" — even
stacking providers doesn't buy a shortcut.

**37. How does Context Discovery avoid the LLM inventing facts?**
Short: seven numbered system-prompt ground rules plus structural
separation of facts from conclusions. Detail: ADR 0015 — "never invent a
repository/file/class not named in that evidence"; the ledger remains the
only place a `Fact` can be written, and `EngineeringUnderstanding` is
never written back as a `Fact`.

**38. What happens if Context Discovery's synthesis call fails?**
Short: graceful degradation to a deterministic summary, never a blocked
run. Detail: ADR 0015 — `_deterministic_understanding`, mechanically built
from already-structured data; an empty investigation short-circuits
*without even calling the LLM*, "synthesizing over nothing would be
fabrication, not understanding."

**39. Does GraphForge use multiple LLM providers for consensus today?**
Short: no — roadmap only (RFC-08). Detail: single-provider generation
today; multi-provider is explicitly future work with its own stated
correlated-training-data caveat already designed in before being built.

**40. What's the actual, measured precision of the Frontier LLM
generator?**
Short: unmeasured. Detail: RFC-06 explicitly defers "precision/recall
measurement and cost-per-run budgeting... out of this RFC's scope, which
was proving the plugin mechanism, not evaluating it." Say this directly if
asked — do not estimate a number.

**41. How does GraphForge keep LLM cost bounded?**
Short: `cost_class` on every manifest, opt-in `GeneratorPolicy` gating,
measured caps like `MAX_MID_LOOP_SYNTHESIS_CALLS`. Detail: `enable_frontier_llm_generator`
defaults `False` specifically so cost is opt-in, never silently added to
every indexing run.

**42. What's the difference between a validator "confirming" and an LLM
"asserting"?**
Short: a validator's confirmation is a deterministic match against cited
evidence text; an LLM assertion is self-reported and never trusted
directly. Detail: `EvidenceKeywordValidator` only ever returns
`confirms`/`no_signal`, deterministic substring matching, never
`contradicts` — absence isn't proof of absence (§05).

**43. Could GraphForge swap its confidence formula for an ML model
later?**
Short: architecturally possible (`formula_version` exists precisely for
this), not built. Detail: `ConfidenceEngine` is an ABC; `DefaultConfidenceEngine`
is one implementation. Nothing in the ADRs proposes an ML-based engine —
don't imply one is planned.

## D. Graph Databases (44–56)

**44. Why Neo4j specifically?**
Short: native traversal for blast-radius/dependency queries. Detail: §
[02_STORY.md](02_STORY.md), [12_DIFFICULT_QUESTIONS.md](12_DIFFICULT_QUESTIONS.md).

**45. Is GraphForge's indexer Neo4j-specific code?**
Short: no — `IGraphRepository` is graph-store-agnostic. Detail: ADR 0007
— "the indexer never imports Neo4j directly... a non-Neo4j graph store
would only mean a new class here." `Neo4jGraphRepository` is the only
concrete implementation today.

**46. How does GraphForge prevent Cypher injection via labels/relationship
types?**
Short: explicit allowlists, checked before string interpolation. Detail:
ADR 0007 — `_ALLOWED_LABELS`/`_ALLOWED_REL_TYPES` in `neo4j_repository.py`;
Cypher can't parameterize label/type names, so interpolation is used, but
only against fixed, internally-controlled sets, "never derived from
request input." Writing an unlisted label/type raises `ValueError`.

**47. How are node IDs constructed, and why does it matter?**
Short: `f"{repository_id}:{kind}:{key}"`. Detail: re-indexing the same
repository always produces the same IDs, so `MERGE`-based writes correctly
upsert in place, and IDs never collide across repositories sharing one
Neo4j database (ADR 0007).

**48. What's the bounded-hop traversal primitive, and why was it added?**
Short: `IGraphRepository.get_neighborhood`. Detail: ADR 0014 — before this,
`get_full_graph` had "no depth bound at all"; the new primitive is one
native variable-length-path Cypher query bounded to `[1,5]` hops, cost
scaling with reachable neighborhood, not repository size.

**49. Is Neo4j the source of truth?**
Short: no, by deliberate design — Postgres (Engineering Memory) is.
Detail: § [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md). *Follow-up*:
"is that true in production today?" → not yet — the materializer isn't
wired into the live write path (§16).

**50. How is a "current" relationship state computed if Neo4j is derived?**
Short: latest version per `relationship_key`, read at query time from the
append-only Postgres log. Detail: same pattern used by both
`EngineeringMemoryRepository.get_current_relationships` and (intended)
`CurrentKnowledgeProjection` (ADR 0018).

**51. Can two edges share the same `(source, type, target)` triple with
different properties?**
Short: yes, legitimately. Detail: `materializer.py`'s own docstring names
a real example — two Kafka-producer methods on one class, same topic —
which is why edge identity in the parity comparator is a multiset of full
property signatures, not a bare triple (§05).

**52. Why does the Parity comparator use `collections.Counter` instead of
a set?**
Short: because edge multiplicity is real and must be counted, not
deduplicated away. Detail: same reasoning as Q51 — a set would silently
collapse legitimate duplicate-triple edges.

**53. How does GraphForge avoid non-deterministic output when comparing
or serializing graphs?**
Short: everything is sorted by an explicit key, never dict/set iteration
order or Python's hash-randomized string hashing. Detail: `parity/comparator.py`'s
docstring names this explicitly, and it's the same bug class ADR 0014's
self-review caught once already (`_primary_repository`'s candidate pool
built from a `set`, fixed to `dict.fromkeys`) — a recurring, seriously-
taken discipline, not a one-off fix.

**54. What edge types actually exist in the graph today?**
Short: `CONTAINS`, `EXPOSES`, `CALLS`, `PRODUCES_TO`, `CONSUMES_FROM`,
`DEPENDS_ON` (single-repo, ADR 0007/demo guide) plus `CALLS_SERVICE`,
`SHARES_TOPIC`, `DEPENDS_ON_REPOSITORY` (cross-repo, ADR 0018 RFC-05).
Detail: node labels: `Repository`, `Component`, `Controller`, `Service`,
`FeignClient`, `Endpoint`, `KafkaTopic`, `MavenDependency` (plus Python
equivalents).

**55. How would GraphForge add a second graph database implementation?**
Short: one new `IGraphRepository` implementation class. Detail: no
indexer, service-layer, or agent code changes required — the interface
was explicitly designed graph-store-agnostic from ADR 0007 onward.

**56. Does the graph track history of its own structure over time?**
Short: not at the Neo4j layer (full replace on every re-index); yes at
the Engineering Memory layer, for what it covers. Detail: `EngineeringMemory.history()`
gives real confidence/relationship history even though Neo4j itself has
none (§04).

## E. Knowledge Graphs (57–69)

**57. What makes this a "knowledge graph" rather than just a "dependency
graph"?**
Short: every edge carries confidence, provenance, and evidence — not just
topology. Detail: `KnowledgeRelationship` bundles `confidence`,
`hypothesis_ids`, and `provenance` alongside the relationship itself (ADR
0018).

**58. How is provenance tracked?**
Short: `Provenance{generator, produced_at, pack_version, run_id}` on every
`EvidenceItem` and `Hypothesis`. Detail: this is what makes "why should I
trust this edge" answerable per-edge, not just per-run.

**59. What's an "open vocabulary" registry, and why not a closed enum?**
Short: `EvidenceItem.kind`, `source_type`, `relationship_type` are
declarative registries, not `StrEnum`s. Detail: adding a new evidence
source or relationship type is a registry entry, not a schema migration —
but once used, a value can never be renamed, only deprecated (ADR 0018).

**60. How does GraphForge represent uncertainty in the graph?**
Short: a six-state `ConfidenceState` enum per relationship, never a bare
boolean "exists/doesn't." Detail: § [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md).

**61. Can a relationship be "wrong," and how would you find out?**
Short: yes — it can be `CONFLICTING` or later corrected. Detail: a human
correction (`trust_level=1.0`) overrides directly; an agent correction
re-enters the same validator pipeline (§04).

**62. Is the knowledge graph single-repository or cross-organization?**
Short: currently repository- and cross-repository (within one indexed
account) — not federated across organizations. Detail: `ROADMAP.md`
Backlog names "cross-organization graph federation" as explicitly not
needed "until a customer requests it."

**63. How does GraphForge avoid graph schema sprawl as more teams add
node/edge types?**
Short: `GraphWriter`'s proposed schema registry as the single choke point
(design intent) plus the Knowledge Engine's registry-per-validator/
generator pattern (implemented). Detail: `ROADMAP.md` Risk Register names
this explicitly as a "High" impact risk with the schema registry as
mitigation.

**64. What's a `KnowledgeRelationship`'s `sequence` field for?**
Short: monotonic version ordering, immune to same-transaction timestamp
collisions. Detail: § [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md)
— found necessary by a real integration test, not designed preemptively.

**65. Does the knowledge graph support temporal queries ("what did we
know on date X")?**
Short: yes, in principle, via `EngineeringMemory.history()` and
`sequence`-ordered rows — this audit did not find a dedicated "as-of"
query API exposed at the REST layer; treat the capability as present at
the data layer, not confirmed as a finished user-facing feature.

**66. How is duplicate/near-duplicate knowledge handled?**
Short: it isn't deduplicated — repeat runs append new versions
intentionally. Detail: explicit design decision (§04) — compaction is
future work, "if write volume warrants it."

**67. What's the relationship between a `Hypothesis` and a
`KnowledgeRelationship`?**
Short: many hypotheses (possibly from different generators/runs) can
support one relationship; `KnowledgeRelationship.hypothesis_ids` tracks
them. Detail: promotion happens once at least one hypothesis clears the
confidence threshold (ADR 0018 § Lifecycle).

**68. Can the knowledge graph represent things outside of code (tickets,
docs, incidents)?**
Short: designed to (`Story`, `Epic`, `Document`, `ADR`, `Incident` node
types named in `ARCHITECTURE.md`), not yet populated by any implemented
generator/indexer this audit found. Detail: Engineering Session's Belief/
Evidence model is the closest implemented analog for non-code reasoning,
but it's a separate aggregate, not graph nodes.

**69. How would you explain "evidence over assertion" to a non-technical
stakeholder?**
Short: "the system never just says 'trust me' — it always says 'trust me,
because of X,' and you can click through to X." Detail: `PRODUCT_VISION.md`
Core Principle 2, verbatim.

## F. Backend (70–82)

**70. What's the backend stack?**
Short: FastAPI, async SQLAlchemy, Alembic, Postgres, Neo4j.
Detail: `docs/deployment/01_ARCHITECTURE.md` — "a single-tenant web
application... FastAPI backend... orchestrates multi-stage AI-agent
workflows."

**71. What's the folder-structure convention?**
Short: `api → services → engine/agent → integrations/graph` (hexagonal-
ish layering), per ADR 0001/0003. Detail: reused wholesale by GraphForge's
new packages, per `ARCHITECTURE.md`.

**72. How does authentication work?**
Short: local email+password, bcrypt hashing, stateless JWT (HS256), no
refresh tokens. Detail: `docs/deployment/04_SECURITY.md` — 60-minute
expiry by default; `localStorage`-held token; a `purpose`-scoped JWT
(e.g. `github_oauth_state`) is explicitly rejected as a general bearer
token so a leaked OAuth-state token can't double as a session token.

**73. Is GitHub login the same as GitHub repository connection?**
Short: no — GitHub *login* is a stub (`501`); GitHub *repository
connection* (OAuth app install, PR/webhook access) is real and used by
the indexer/integrations. Detail: `docs/deployment/04_SECURITY.md`, stated
directly as "not a security gap; simply not implemented."

**74. What's `AppError`, and why does it matter?**
Short: the base exception class every deliberate error path raises, with
a 3-tier handler chain. Detail: `docs/deployment/12_OPERATIONS.md` —
`AppError` subclasses log at `WARNING`, unhandled exceptions at `ERROR`
with full traceback, validation errors at `INFO` — a consistent severity
taxonomy across the whole backend.

**75. How are migrations managed?**
Short: Alembic, strictly additive per RFC in this codebase's own
discipline. Detail: every ADR 0018 RFC states its migration as additive
and verified upgrade→downgrade→upgrade against real Postgres.

**76. What's the API versioning convention?**
Short: `/api/v1/...` — every router lives under `app/api/v1/routers/`.
Detail: 27 routers currently registered (audited directly), spanning
auth, repositories, agent_runs, learning, parity, engineering_sessions,
and more.

**77. How does the backend call multiple LLM providers without vendor
lock-in code paths?**
Short: `ProviderSpec` registry + `ILLMProvider` interface. Detail: §
[10_DESIGN_DECISIONS.md](10_DESIGN_DECISIONS.md) — "why Bedrock / why
Claude."

**78. What's the propose/commit boundary, concretely, in code?**
Short: `DecisionService.commit` raises `ForbiddenError` for an Agent
committer, and `DecisionCommitRequest` structurally has no `agent_role`
field. Detail: RFC-001 — enforced at both the service layer and the API
schema layer, independently, so bypassing one doesn't bypass both.

**79. How is pagination handled across list endpoints?**
Short: a uniform `Page` envelope (`{items, page: {total, limit, offset}}`).
Detail: RFC-001 § API — consistent convention, offset-based; cursor
pagination is named in `ROADMAP.md` Backlog as a future option "if offset
pagination's query cost becomes measurable at real org scale."

**80. What does `_require_same_session` do, and why?**
Short: enforces aggregate ownership at the API boundary — a real artifact
reached through the wrong Session's URL 404s. Detail: RFC-001 §3.1,
independently tested.

**81. How many backend tests exist?**
Short: 236 test files under `backend/tests/` (unit + integration), audited
directly; RFC-001 alone added 68 (10 schema + 33 service + 15 repository +
10 API). Detail: full-suite pass counts are cited per-ADR at time of that
ADR's own change (e.g. ADR 0016: "909/909 in tests/unit/ai unchanged").

**82. What's the difference between a `Run`/`AgentStep` and a
`KnowledgeRelationship`?**
Short: `Run`/`AgentStep` (Postgres, audit trail of *that an agent ran and
what it concluded*) vs. graph facts (Neo4j/Engineering Memory, *the
knowledge itself*) — `ARCHITECTURE.md`'s stated split, mirroring the
pre-existing `PullRequestAnalysis` (Postgres) vs. dependency graph (Neo4j)
precedent.

## G. Data Engineering (83–95)

**83. What's the storage strategy for evidence packs vs. relationships?**
Short: one compressed blob per pack, real relational rows per hypothesis/
validation/relationship. Detail: § [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md)
— "why Postgres," a deliberately mixed strategy justified by cardinality.

**84. How is an evidence pack keyed?**
Short: `(repository_id, commit_sha, schema_version)`. Detail: content-
addressed at the item level too (`EvidenceItem.id = hash(kind, reference,
raw_value)`), so re-extraction of unchanged content is idempotent.

**85. What's a "delta pack," and does it exist yet?**
Short: `is_delta=True`, an incremental evidence append referencing a base
pack. Detail: contract exists (ADR 0018); no shipped source uses it yet —
RFC-09 (roadmap) is the first planned consumer.

**86. How is retention/archival handled?**
Short: `EvidencePack` blobs older than the last N successful runs may be
archived (regenerable); `Hypothesis`/`Validation`/`Correction`/confidence
history are never compacted or deleted. Detail: § [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md).

**87. What ETL/extraction pipeline turns source code into evidence?**
Short: tree-sitter parse → `ArchitectureModel` → evidence extractors
(`app/indexer/evidence/`) → `EngineeringEvidencePack`. Detail: § [13_WHITEBOARD.md](13_WHITEBOARD.md)
#1.

**88. How does GraphForge avoid ingesting secrets during evidence
extraction?**
Short: an explicit, safety-conscious filename allowlist — never `.env` or
key/credential-shaped files. Detail: ADR 0018 RFC-06, `repository_evidence.py`,
with a dedicated test (`test_repository_evidence.py`) covering the
exclusion guard directly.

**89. What's the reliability-tier concept, and where does it come from?**
Short: a static, per-`kind` trust level on evidence, never computed
per-instance. Detail: `HIGH_RELIABILITY_TIER = 3` reuses RFC-02's
`_DETERMINISTIC_RELIABILITY_TIER` constant value exactly — a literal
annotation match and a name-similarity match are not treated as equally
trustworthy (§05).

**90. How would a new evidence source (e.g. infra manifests) get added?**
Short: a new `EvidenceReference.source_type` registry entry plus an
extractor — RFC-09 names infra manifests as the first planned new source,
"lowest staleness risk," specifically deferring runtime telemetry
("highest operational complexity") to last.

**91. Is there schema validation on evidence before it enters the
pipeline?**
Short: yes, at the hypothesis level — "an unresolvable [evidence]
reference is rejected at ingestion, before any validator runs" (ADR 0018
Engineering invariants).

**92. How does GraphForge keep re-computation reproducible?**
Short: "the same evidence pack, run through the same generator/validator/
confidence-engine versions, produces the same result" — a stated
engineering invariant (ADR 0018), enforced by making evidence immutable
and generators/validators pure functions of it.

**93. What's `formula_version` for?**
Short: versioning the confidence formula itself, so a formula change is
auditable rather than silently altering historical interpretation.
Detail: part of `ConfidenceModel` (ADR 0018 RFC-01).

**94. How large can an evidence pack get, and why does that matter for
storage design?**
Short: "tens of thousands of evidence items per run" for a large
monorepo — the direct justification for blob storage over per-row
normalization (§04).

**95. Does GraphForge do any data quality / anomaly detection on
extracted evidence?**
Short: not as a dedicated pipeline — quality is enforced structurally
(validators, confidence gating) rather than via a separate anomaly-
detection layer. Not implemented as a distinct capability; don't imply one
exists.

## H. Security (96–110)

**96. How are integration tokens (GitHub/Jira/Confluence) stored?**
Short: encrypted at rest via Fernet (`app.core.crypto`). Detail:
`ARCHITECTURE.md` § Security Considerations; `docs/deployment/05_IAM.md`
confirms the pattern extends, not replaces, for new integrations.

**97. Are AWS credentials ever stored in the application?**
Short: no, never, by explicit design principle. Detail: `docs/deployment/05_IAM.md`
— "no static AWS access keys, anywhere, for anything GraphForge does
itself," verified against `bedrock_provider.py`'s actual credential-chain
usage.

**98. How is password storage handled?**
Short: bcrypt, input truncated to 72 bytes defensively (bcrypt's own
limit) so a long password never silently weakens the hash or raises.
Detail: `docs/deployment/04_SECURITY.md`, `app/core/security.py`.

**99. What prevents a leaked OAuth-state token from being used as a
session token?**
Short: `purpose`-scoped JWT claims — `get_current_user` explicitly rejects
any token carrying a `purpose` claim as a general bearer token. Detail: §
Q72 above.

**100. How is Cypher injection prevented given labels/types must be
string-interpolated?**
Short: explicit, fixed, internally-controlled allowlists, never derived
from request input. Detail: § Q46.

**101. Is there row-level multi-tenancy enforcement?**
Short: yes, per-user, now code-verified (KAN-33) — but not via
`organization_id`, which does not exist in the codebase (a prior version
of this answer stated the `ARCHITECTURE.md` design intent as if it were
built; it wasn't, and has been corrected there). The real mechanism:
every resource row (`Repository`, `Workflow`, `Run`, `PullRequest`) is
scoped to `user_id`, checked per-router before any read/write, and every
graph node inherits its scope from an ownership-checked `repository_id`.
Independently verified end-to-end for the workflow-lifecycle endpoints
(`tests/integration/test_workflows_cross_user_isolation.py`); a full
sweep of every remaining router is the still-open remainder of KAN-33.

**102. What's the propose/commit boundary's security property, precisely?**
Short: it's an authorization boundary, not just a workflow rule — an
Agent literally cannot construct a request that commits a Decision, because
the schema has no field for it (RFC-001).

**103. How does GraphForge treat an agent-sourced correction differently
from a human-sourced one, security-wise?**
Short: only a human correction carries unconditional override authority
(`trust_level=1.0`); an agent correction is just another hypothesis
subject to the same validation gate. Detail: ADR 0018, a real
privilege-separation pattern applied to knowledge, not just to accounts.

**104. What's GraphForge's stance on secrets in evidence/logs?**
Short: explicit filename exclusion at extraction (§ Q88); structured
logging conventions don't specifically document secret-redaction beyond
that — treat log-level secret hygiene as not independently confirmed by
this audit.

**105. Are LLM prompts a data-exfiltration surface, and how is that
managed?**
Short: not addressed as a named threat model in the ADRs read for this
handbook. **Not implemented/decided in the current documentation** —
avoid claiming a specific mitigation that isn't written down; the closest
relevant control is evidence curation (budget/kind-diversity limits on
what enters a prompt), which reduces exposure incidentally, not by design
intent stated as a security control.

**106. How is access to the Learning/feedback endpoints controlled?**
Short: same `Depends(get_current_user)` authentication convention as
every other router (RFC-001 pattern, generalized); this audit did not find
a distinct authorization tier (e.g. admin-only) specifically for feedback
endpoints — treat as authenticated-user-scoped, not independently
role-gated, unless shown otherwise.

**107. What's the JWT expiry/refresh story, and is it a gap?**
Short: 60-minute expiry, no refresh mechanism, "a product/UX
characteristic, not a flagged security gap" — the deployment doc's own
explicit framing, not this handbook's editorializing.

**108. Does GraphForge validate webhook signatures (e.g. GitHub
webhooks)?**
Short: not verified in this audit's source set — `app/api/v1/routers/webhooks.py`
exists; this handbook did not read it. Say "not confirmed in this audit"
rather than asserting either way.

**109. How would a security reviewer verify the "no direct graph write
from a generator" invariant?**
Short: grep for `IGraphRepository`/`Neo4jGraphRepository` imports across
`app/indexer/hypotheses/` and `app/knowledge_engine/` — none should appear
outside the materializer and the legacy `cross_repo_linker`/`graph/builder`
write paths. Detail: a concrete, falsifiable verification method, not
just a documentation claim.

**110. What's the security implication of the materializer not being
live yet?**
Short: none directly — it's a read/projection concern, not an
authorization one. The bigger implication is trust: until it's the live
path, "Neo4j is derived from Engineering Memory" is a proven capability,
not an operative guarantee, which matters for any audit claim about data
lineage (§16).

## I. Cloud (111–123)

**111. What AWS services does the reference deployment use?**
Short: Route53, ACM, ALB, ECS, and a VPC with public/private subnets.
Detail: `docs/deployment/02_INFRASTRUCTURE.md` overview diagram.

**112. Where does Postgres run in the AWS deployment?**
Short: private-subnet-only, no published host port. Detail:
`docs/deployment/03_NETWORKING.md` — mirrors the Compose-network-only
posture in local dev.

**113. What port does the backend listen on, and where's that defined?**
Short: `8000`. Detail: `backend/Dockerfile` (`EXPOSE 8000`), confirmed in
`docs/deployment/03_NETWORKING.md` as "source of truth," not invented for
the deployment doc.

**114. How does the ECS task get its secrets?**
Short: via the ECS Task Execution Role, injected as environment variables
at launch — not baked into the image. Detail: `docs/deployment/05_IAM.md`
Role 1.

**115. Is the CD pipeline (deployment) implemented today?**
Short: no — CI (lint/test/build) is real and running; CD is a
specification only. Detail: `docs/deployment/07_CICD.md`, stated
directly: "The CD portion below is a specification, not yet implemented —
no workflow file has been created or modified."

**116. What does CI actually run today?**
Short: backend and frontend jobs on every push/PR to `master` (not
`main` — the repo's actual default branch). Detail: `.github/workflows/ci.yml`,
verified by direct read per the deployment doc.

**117. How is logging shipped in the AWS deployment?**
Short: `awslogs` driver → CloudWatch Logs, zero code change, one log
group per ECS service. Detail: `docs/deployment/12_OPERATIONS.md` — set
an explicit retention period; don't leave it "never expire" by accident
(a direct, practical recommendation from the doc, not this handbook's
invention).

**118. Is application logging structured (JSON) today?**
Short: no — plain stdlib logging, "deliberately simple... until real
request [volume justifies more]." Detail: same doc names structured
logging as a recommended, not-required-to-launch improvement.

**119. How is Bedrock reached without static credentials?**
Short: `boto3.client("bedrock-runtime", ...)` with no explicit
credentials, relying on the SDK's default resolution chain. Detail: § Q97.

**120. What's the disaster-recovery/backup story?**
Short: named as a documented topic in `docs/deployment/12_OPERATIONS.md`
("backup/DR for the production deployment") — this audit read the file's
purpose statement, not its full DR procedure; don't fabricate RTO/RPO
numbers not independently confirmed here.

**121. Is GraphForge multi-region?**
Short: not addressed in the deployment docs read for this audit — no
evidence of multi-region design. Treat as single-region unless shown
otherwise.

**122. How would GraphForge's cloud footprint change if the materializer
became the live Neo4j write path?**
Short: no new infrastructure — same Postgres, same Neo4j; only the
write-trigger logic inside the existing backend changes. Detail: RFC-05B's
own rollback plan confirms this — "the materializer is never called by
any existing write path... deleting `materializer.py`... fully reverts
this with no other change," implying the inverse (adopting it) is
similarly infrastructure-neutral.

**123. Does GraphForge require GPU infrastructure?**
Short: no — all LLM calls are to external provider APIs (Bedrock, OpenAI,
Gemini, Groq); tree-sitter parsing is CPU-only. No GPU dependency anywhere
in the audited architecture.

## J. Performance (124–136)

**124. What's the cost of the `get_neighborhood` primitive vs.
`get_full_graph`?**
Short: bounded by reachable neighborhood, not repository size. Detail:
ADR 0014 — the direct fix for `get_full_graph` having "no depth bound at
all."

**125. How is LLM call cost bounded per Context Discovery run?**
Short: `MAX_MID_LOOP_SYNTHESIS_CALLS = 1`, chosen from a measured
benchmark, not a guess. Detail: § Q32 — a rare case of a performance
budget backed by an actual before/after number in the ADR itself.

**126. What's the retrieval-breadth fix in ADR 0014, and what did it
save?**
Short: `repository_filter` scopes "scope"/"verify" actions to one
repository instead of every indexed repository. Detail: "previously O(every
indexed repo); now O(1)" once a repository is known — stated directly,
not estimated.

**127. Does validator execution parallelism improve latency?**
Short: yes — `asyncio.gather` over all applicable validators per
hypothesis, versus a prior sequential loop (ADR 0018 RFC-06B).

**128. What's the single biggest unaddressed performance risk?**
Short: full-clone-per-index, "doesn't scale past a handful of repos per
org" (`ROADMAP.md`). *Follow-up*: "what would incremental indexing need?"
→ webhook-driven, diff-only re-indexing — a named Phase-2 prerequisite,
not built.

**129. How does the parity comparator stay cheap enough to run per
repository on demand?**
Short: pure, in-memory, no I/O — both inputs are already-fetched
`GraphPayload`s; cost is O(nodes+edges) Python-side comparison, no
additional graph traversal.

**130. What's `curate()`'s computational cost?**
Short: "O(components) in Python, over data already fetched — negligible
next to the Neo4j round-trip that produced it" (ADR 0014, stated
directly).

**131. Are Neo4j traversals bounded per-agent?**
Short: yes — `max_graph_hops` on every `AgentManifest` bounds Context
Assembler/traversal depth, per `ARCHITECTURE.md` § Scalability
Considerations.

**132. How would a slow LLM provider affect an agent run's total
latency?**
Short: it's the dominant cost, isolated via `AgentMetrics.start_llm_call`/
`stop_llm_call` timing distinct from service-call timing — visible per-run,
not blended into one opaque number (§08).

**133. Does GraphForge cache LLM responses?**
Short: not confirmed in this audit — `AgentMetrics`' docstring mentions
"cache hits" as a field it can record, implying some caching concept
exists upstream, but this handbook did not trace the actual cache
implementation. State as "a metric exists for it; the mechanism wasn't
independently verified here."

**134. What's the performance impact of RFC-06C's explanation
persistence?**
Short: one nullable JSON column, computed once at write time — no
recomputation cost on read. Detail: ADR 0018 RFC-06C, stated as
negligible and verified by the migration's own additive-only nature.

**135. What's the performance impact of the Learning Engine on the
indexing hot path?**
Short: zero — "this package has no import path into any of them [indexing,
generation, validation, confidence computation]" (ADR 0018 RFC-06D,
stated directly).

**136. How does GraphForge avoid the N+1 query problem in relationship
lookups?**
Short: not specifically named as an optimization technique in the ADRs
read; `relationship_lookup.fetch_with_confidence` is the shared,
single-call-shaped access point reused by every Engineering Intelligence
Service rather than each service querying independently — reduces
duplicate query *paths*, though this audit didn't verify per-call batching
depth. Say this precisely, not as a blanket N+1 guarantee.

## K. Scalability (137–149)

**137. What's the stated multi-tenancy scaling model?**
Short: `user_id`-scoped per-router ownership checks, not `organization_id`
— that column was a design-intent placeholder for a future org-level
grouping *above* today's per-user model, and never got built (corrected
in `ARCHITECTURE.md` § Scalability Considerations by KAN-33). Detail:
`tests/integration/test_workflows_cross_user_isolation.py` verifies it
end-to-end for the workflow-lifecycle endpoints; a full router sweep
remains open.

**138. How does the confidence formula scale with evidence volume?**
Short: incrementally — O(1) per new `ValidationResult`, never O(history).
Detail: the entire reason `confirming_source_types`/
`max_confirming_reliability_tier` were added (§05) — without them, a
correct incremental formula wasn't even computable.

**139. What's the scaling risk named directly in the Risk Register?**
Short: "Orchestrator becomes a bottleneck as agent count grows" (Medium
impact); mitigated by bounded hops, async dispatch, per-run concurrency
cap. Detail: `ROADMAP.md` Risk Register, read directly, not paraphrased
from elsewhere.

**140. Does Engineering Memory's append-only design create a scaling
concern?**
Short: yes, by design, and stated honestly — "grows without bound... by
design (the audit trail is the point)." Detail: ADR 0018 § Consequences;
only `EvidencePack` blobs are archivable.

**141. How would GraphForge scale to thousands of indexed repositories?**
Short: not solved today — ADR 0014 names "repository-ranking-at-scale for
thousands of indexed repositories" as an identified, out-of-scope
bottleneck: the "survey" path still fetches every indexed repository's
components in a Python-side loop, needing "a Cypher-side aggregation
query" instead.

**142. What's the plan for LLM cost scaling as agent count grows?**
Short: `cost_class` per manifest plus a budget-aware Selector, named as
Phase-3 work in `ROADMAP.md` — not built yet.

**143. Is confidence calibration required before scaling agent count
further?**
Short: yes, by explicit policy — `ROADMAP.md` Risk Register: "block Phase
3 agent additions if not shipped," because "confidence scores become
decorative" is rated a High-impact risk. Detail: the raw feedback data
now exists (RFC-06D); calibration itself does not yet (§16).

**144. How does cross-repository reasoning scale as repo count per org
grows?**
Short: bounded today mostly by the *correctness* gaps (Known Gaps 2–4),
not yet by a demonstrated throughput ceiling — the more pressing scaling
question is "does it work at all across repos," not "is it fast."

**145. What's cursor-based pagination, and why isn't it built yet?**
Short: `ROADMAP.md` Backlog — offset pagination is what's implemented;
cursor pagination is deferred "if offset pagination's query cost becomes
measurable at real org scale," i.e., not built preemptively.

**146. How does the Knowledge Engine avoid becoming a write bottleneck
during a large indexing run?**
Short: generators run concurrently and independently over the same pack;
validators run concurrently per hypothesis; writes are appends, not
locked updates — no single serialization point beyond Postgres's own
write path.

**147. What's the stated position on incremental evidence ingestion's
scaling benefit?**
Short: RFC-09 (roadmap) — a delta pack triggers only the generators that
`consumes` its source type, not a full re-index; this is explicitly the
mechanism meant to decouple evidence freshness from full-repository
re-parse cost, once built.

**148. Does the Orchestrator's Selector scale to many Goals/agents?**
Short: today it's a static rule table — cheap and scales trivially in
compute terms, but scales poorly in *maintenance* terms as Goal count
grows, which is exactly why an LLM-based `ISelector` swap is scoped for
Phase 3, isolated behind the same interface for a drop-in replacement.

**149. What would break first if GraphForge onboarded a 40-repository
org tomorrow?**
Short: the indexer (full-clone-per-index, no incremental re-indexing) and
Impact Analysis's same-repository traversal filter — both already-named,
already-documented limits, not speculation (§16).

## L. Testing (150–162)

**150. What's GraphForge's default testing discipline?**
Short: real Postgres/Neo4j in integration tests, no mocked DB; mock only
the exact external HTTP boundary. Detail: `ROADMAP.md` § Testing
Strategy, and independently confirmed by ADR 0007's own verification
strategy (real `git clone`, real Neo4j service container in CI).

**151. How is the materializer verified?**
Short: a real replay test — clone, parse, index, delete from Neo4j,
rebuild from Engineering Memory alone, diff against the original.
Detail: `tests/integration/test_materializer_replay.py` (ADR 0018 RFC-05B).

**152. How is confidence-engine correctness verified?**
Short: parity testing against a pre-existing, trusted implementation —
`test_all_validators_parity_for_deterministic_hypothesis` reproduces
`cross_repo_linker.py`'s existing labels exactly. Detail: ADR 0018 RFC-03
— proving equivalence to known-good behavior, not just unit-testing in
isolation.

**153. How is validator concurrency-safety tested?**
Short: under real concurrent scheduling, not just sequential try/except —
`test_one_validator_raising_does_not_discard_the_others` (ADR 0018
RFC-06B).

**154. How is non-determinism itself tested for?**
Short: directly, via a targeted regression test reproducing a found bug —
`_primary_repository`'s set-iteration-order bug was caught by "a test that
inserts the same two candidates in both orders and asserts the result
tracks insertion order, not hash order" (ADR 0014).

**155. How many tests did RFC-001 add, and what do they cover?**
Short: 68 (10 schema + 33 service + 15 repository + 10 API). Detail: full
list in § [03_ARCHITECTURE.md](03_ARCHITECTURE.md) — cascade deletes,
propose/commit boundary rejection, competing-Recommendation→Contradiction
auto-detection, aggregate-ownership 404 enforcement, pagination.

**156. What's a concrete example of a test catching a real bug before
ship?**
Short: the `relink_account`/`pg_advisory_xact_lock` early-commit bug (§
Q17) and the RFC-01 confidence-formula contract gap (§ Q "hardest
architectural decision" in [12_DIFFICULT_QUESTIONS.md](12_DIFFICULT_QUESTIONS.md)).
Both are named directly as "found and fixed before shipping," not
theoretical.

**157. How is graceful degradation tested?**
Short: explicit failure-path tests exist alongside happy-path tests for
every LLM-touching component — ADR 0015's `test_understanding.py` covers
LLM failure and malformed JSON both degrading without raising.

**158. How is the 24-repository validation suite different from the
backend's own test suite?**
Short: black-box, external, against real deployed APIs — never
reimplements GraphForge logic, never asserts via Cypher/SQL directly. §
[09_VALIDATION_FRAMEWORK.md](09_VALIDATION_FRAMEWORK.md).

**159. What's the exit-code contract for the validation suite?**
Short: `0` iff every gating validation (all except #9, Performance)
passes — explicitly designed to be a CI acceptance gate.

**160. How does the test suite avoid flaky assertions on LLM output?**
Short: keyword matching for narrative fields, exact matching for
deterministic fields — explicitly justified as avoiding "every run flaky
for reasons that have nothing to do with GraphForge's correctness."

**161. Are there regression tests tied to named, numbered ADR findings?**
Short: yes, repeatedly — e.g. `test_scd2_domain_abbreviation_alone_is_a_documented_known_limitation`
(ADR 0014) exists specifically "so a future change to this stays a
deliberate decision, not a silent one."

**162. What's not tested that should worry a reviewer?**
Short: the Frontier LLM generator's real-world precision/recall (§ Q40)
and end-to-end frontend rendering of Evidence Package/Engineering
Understanding (never browser-verified, per ADR 0014/0015's own admission)
— both named directly as deliberately out of scope for their respective
changes, not silently skipped.

## M. Product (163–175)

**163. Who are GraphForge's named personas?**
Short: Priya (senior backend IC), Marcus (EM), Ana (staff architect),
Devon (new hire). Detail: `PRODUCT_VISION.md` § User Personas, each with a
concrete want statement.

**164. What's explicitly out of scope for the product?**
Short: general chatbot, Jira/Linear replacement, standalone PR bot,
autonomous auto-merge, an observability platform. Detail: `PRODUCT_VISION.md`
§ Out of Scope, five items, each with its own one-line reasoning.

**165. How does GraphForge define success?**
Short: time-to-context drops from "ask a senior engineer" to "assembled
before I finished reading the ticket"; every AI output traceable; new
agents ship without touching the orchestrator's core; the graph outlives
any single feature. Detail: `PRODUCT_VISION.md` § Definition of Success,
four criteria verbatim.

**166. What's the competitive positioning claim, precisely?**
Short: not "better prompts" — "a graph the others don't, and a framework
where new agents make the *existing* agents smarter." Detail: `PRODUCT_VISION.md`,
stated as the closing line of the competitive-positioning section.

**167. Why does the product philosophy insist "every feature is a graph
read, a graph write, or both"?**
Short: a scope-discipline mechanism, not a slogan — anything that fails
this test "should be scoped out or rejected until someone can name the
node/edge type it produces or consumes."

**168. What does GraphForge explicitly not compete on?**
Short: prompt quality. Detail: `PRODUCT_VISION.md` — "it competes on
having a graph the others don't."

**169. How does GraphForge define "time-to-context," and is it measured
today?**
Short: defined in `PRODUCT_VISION.md`; this audit found no shipped
telemetry dashboard specifically measuring it — treat as an aspirational
metric with a clear definition, not a live number.

**170. What's the guiding rule for whether a new fact belongs in the
graph vs. a bespoke table?**
Short: "prefer extending the graph schema over adding a bespoke database
table" — `PRODUCT_VISION.md` Guiding Principle 3.

**171. Why does GraphForge treat "a new integration" as a graph-source
question, not a UI question?**
Short: "a new integration is a new node/edge source, not a new UI silo —
it must show up wherever the graph is already surfaced" — Guiding
Principle 4, directly preventing feature-per-integration UI sprawl.

**172. What's the actual product risk if confidence scores become
inaccurate over time?**
Short: named directly as the top product-trust risk — "confidence scores
become decorative (unchecked against outcomes)... undermines 'evidence
over assertion'" (`ROADMAP.md` Risk Register, High impact).

**173. How does GraphForge's roadmap sequence agent rollout, and why that
order?**
Short: Phase 1 (Orchestrator/Framework, Review agent only) → Phase 2
(Requirement/Planning + Jira/Confluence) → Phase 3 (Development/Testing/
Release + LLM Selector) — "proving the framework holds before multiplying
agents" is the stated Phase 1 rationale.

**174. What does GraphForge consider a "shippable" agent output?**
Short: `ROADMAP.md` Definition of Done — manifest registered, confidence+
evidence non-empty (or confidence omitted entirely, never bare and
unjustified), errors surfaced verbatim, structured logs with run/agent/
subject ids, tests covering happy path + not-found + one upstream-failure
path, UI following `UI_GUIDELINES.md` exactly.

**175. How would you pitch GraphForge to Ana (the staff architect
persona) specifically?**
Short: "the Architecture Agent and Knowledge Graph encode what you know so
it survives you being on vacation — or leaving" — her want statement,
verbatim from `PRODUCT_VISION.md`, used as the actual pitch.

## N. UX (176–186)

**176. What's the stated UX philosophy?**
Short: "developer-native... dense, fast, no marketing chrome." Detail:
`PRODUCT_VISION.md` § Product Pillars, "Developer-Native UX" row.

**177. What UI convention governs new cards/badges/colors?**
Short: `UI_GUIDELINES.md` — `ROADMAP.md` Definition of Done requires
following it exactly, "no new card/badge/color without updating that
document first." This audit did not independently review `UI_GUIDELINES.md`'s
full content — cite its existence and the enforcement rule, not
unverified specifics of its contents.

**178. Does the frontend currently render the tiered Evidence Package?**
Short: no — computed and persisted correctly on the backend, but "the UI
(Context Explorer / `BlueprintExplorer`) still shows the older, flatter
view" (ADR 0014, stated as a deliberate, not-attempted-yet deferral).

**179. Does the frontend render `EngineeringUnderstanding` or
`InvestigationWorkspace`?**
Short: no — "both are API/prompt-level only in this pass" (ADR 0015),
same deferral pattern as ADR 0014's Evidence Package.

**180. What's the Graph Parity dashboard, and what does it show a user?**
Short: a live, user-facing comparison of the real Neo4j graph against a
graph re-derived from Engineering Memory — makes the "rebuildable
projection" architectural claim inspectable, not just tested. Detail: §
[10_DESIGN_DECISIONS.md](10_DESIGN_DECISIONS.md) "why parity."

**181. How does the UI convey confidence to a user?**
Short: via the `ConfidenceState` vocabulary (verified/highly_likely/
likely/candidate/rejected/conflicting) and, where present, the persisted
`ConfidenceExplanation` — this audit did not independently verify the
frontend's exact rendering component for these fields; state the backend
contract confidently, the frontend rendering detail more cautiously.

**182. Is there a thumbs-up/down feedback affordance in the UI?**
Short: the backend now supports it (RFC-06D's feedback endpoints); the
`AGENT_FRAMEWORK.md` roadmap names "a lightweight thumbs-up/down capture
on Agents UI" as Phase 2 work for calibration tracking — this audit did
not confirm whether the frontend affordance itself is wired up yet. Say
"backend-ready, frontend status not independently confirmed here."

**183. What's the intended top-level navigation shape?**
Short: `ARCHITECTURE.md` describes Dashboard → Project → Graph → Agents →
Pipeline as "one continuous workflow, not a set of unrelated screens" —
a design intent; Pipeline/Projects pages are named in `ROADMAP.md` Phase 1
as "stubbed nav entries only" at that phase.

**184. How does GraphForge avoid presenting an unexplained AI claim in
the UI?**
Short: architecturally, every `AgentOutput` carries `evidence` alongside
`result`; the product's stated Definition of Success includes "zero
unexplained AI assertions in production" as an explicit target, not
merely a nice-to-have.

**185. What UX pattern handles a partial or failed agent run?**
Short: `status=partial`/`status=failed` on `AgentStep`, "surfaced verbatim
in the UI" per `ARCHITECTURE.md` § Error Handling — never silently omitted.

**186. Would a new user understand what "candidate" confidence means
without documentation?**
Short: an honest open question — the state name itself is reasonably
self-explanatory, but this audit found no confirmed in-UI tooltip/
explainer component; treat UX clarity of the confidence vocabulary as
unverified, not confirmed good or bad.

## O. Platform (187–197)

**187. What makes GraphForge a "platform" rather than a single feature?**
Short: the Agent Framework + Orchestrator + Engineering Intelligence
Service Layer are all designed as reusable substrate — a new agent is
additive, per `AGENT_FRAMEWORK.md`'s own extensibility test.

**188. What's the platform's plugin story today vs. its stated future?**
Short: today, compiled-in Python packages, statically registered at
startup — "no dynamic code loading, no plugin marketplace security
surface, yet." Future: an out-of-process plugin protocol, explicitly
gated on real third-party demand existing first (`ARCHITECTURE.md` §
Plugin Architecture).

**189. How does the platform keep a new integration from requiring
orchestrator changes?**
Short: `IKnowledgeSource` — one interface, one registration; zero
orchestrator/agent changes per `PRODUCT_VISION.md` Extensibility Strategy
axis 1. This audit confirmed the *design contract*; it did not find a
second, non-GitHub `IKnowledgeSource` implementation actually shipped
(Jira/Confluence integration exists per routers, but via MCP-based tooling
per `docs/deployment/01_ARCHITECTURE.md`, not confirmed as this exact
interface — note the nuance rather than flattening it).

**190. What's the Engineering Intelligence Service Layer's platform
role?**
Short: the shared, LLM-free fact layer every current and future
Engineering Intelligence Agent calls into — "if two agents need the same
fact, that fact belongs in [this layer], not duplicated in two prompts"
(§07, echoing `PRODUCT_VISION.md` Guiding Principle 5).

**191. How is a platform-level invariant (e.g. "validators never call an
LLM") actually enforced, not just documented?**
Short: partially by interface design (a `KnowledgeValidator`'s method
signature has no LLM provider parameter to reach for), partially by
convention/review — this audit found no automated lint rule specifically
banning an LLM import inside `validators/`; state the enforcement
mechanism honestly as "structural signature + review discipline," not as
an automated gate unless one is found.

**192. What's the platform's stance on backward compatibility for
taxonomy values?**
Short: absolute — "once used by any persisted record, [a value is] never
renamed — only deprecated and superseded" (ADR 0018), because
reproducibility of historical records depends on it.

**193. How does the platform version its own confidence formula?**
Short: `formula_version` on every `ConfidenceModel` — a formula change is
"itself auditable," never a silent behavior shift.

**194. What's the platform's registered-agent count today?**
Short: 12+ manifests found under `app/agents/*/manifest.py` in this
audit — Testing, API Intelligence, Documentation (Review), Repository
Understanding, Engineering Review, Documentation Planning, Impact
Analysis, Planning, Dependency Query, Context Discovery, and siblings.

**195. How would a platform team add a Slack integration?**
Short: `SlackEntryResolver` (as both entry point and notification sink) —
named directly in `ROADMAP.md` Backlog, not phase-committed, not built.

**196. What's the platform's approach to Kubernetes deployment
topology as graph data?**
Short: named as Backlog ("`Deployment`, `Service` at runtime, distinct
from code-level `Component`"), not implemented.

**197. How does the platform prevent a runaway LLM cost from one
misbehaving agent?**
Short: `cost_class` per manifest (design-time signal) plus per-generator
opt-in `GeneratorPolicy` gating (runtime control, proven for the Frontier
generator) — a budget-aware Selector enforcing this automatically is
named as Phase-3, not built yet.

## P. DevOps (198–208)

**198. What does the current CI pipeline actually verify?**
Short: backend and frontend lint/test/build, on every push/PR to
`master`. Detail: `docs/deployment/07_CICD.md`, verified by direct file
read, explicitly distinguished from the CD specification in the same
document (which is not yet implemented).

**199. Is the validation suite wired into CI today?**
Short: not confirmed — the validation guide states its exit code is
"designed to be" a CI gate ("wire this into CI as the acceptance gate"),
phrased as a recommendation, not a confirmed-existing CI step.

**200. How are database migrations deployed?**
Short: Alembic, run explicitly (`alembic upgrade head`), not
auto-applied on container boot per any evidence found in this audit —
state this as the documented manual/explicit pattern (RFC-001 § Migration
guide shows the exact command used), not as a described auto-migration
system.

**201. What's the container image build/deploy path?**
Short: `backend/Dockerfile` (port 8000), `frontend/Dockerfile` (Nginx,
port 80) — confirmed directly against `docs/deployment/03_NETWORKING.md`'s
own "source of truth" ports table.

**202. How does local dev differ from the AWS deployment?**
Short: `docker-compose.yml` (dev, Vite dev server on 5173) vs.
`docker-compose.prod.yml` (Nginx-served frontend on 80, no published
Postgres port) — both real files, confirmed by the networking doc.

**203. How does the demo environment avoid touching real GitHub?**
Short: `docker-compose.demo.yml` layered on the normal dev stack,
`VCS_PROVIDER=local_git`, resolving "pull requests" to local git branches
via `LocalGitVersionControlProvider` — normal dev usage is "completely
unaffected" (`demo/DEMO_GUIDE.md`, stated directly).

**204. What's the operational recommendation for CloudWatch Logs
retention?**
Short: set an explicit period (e.g. 30 days); "never leave it at 'never
expire' by accident" — a direct, practical line from `docs/deployment/12_OPERATIONS.md`,
worth quoting rather than paraphrasing since it's specific operational
advice.

**205. Is there an incident-response runbook?**
Short: named as a topic `docs/deployment/12_OPERATIONS.md` covers
("incident response... for the production deployment") — this audit
confirmed the section exists by reading the file's stated purpose, not
its full runbook content; don't fabricate specific escalation steps.

**206. How would a DevOps engineer roll back a bad ADR-0018 RFC change
in production?**
Short: every RFC states its own rollback plan explicitly, and most are a
one- or two-line revert (stop calling a function, flip a config flag) with
zero cascading effect, because shadow-mode discipline means nothing
downstream depended on the new code path yet. Detail: § "why shadow mode"
in [10_DESIGN_DECISIONS.md](10_DESIGN_DECISIONS.md).

**207. What's the deployment risk of the Frontier LLM generator being
enabled accidentally?**
Short: low, by design — `enable_frontier_llm_generator` defaults `False`;
enabling it is an explicit config change, and even enabled, output stays
gated at `CANDIDATE` until validated, never silently promoted to
production-trusted knowledge.

**208. How does GraphForge's migration discipline reduce deployment
risk specifically?**
Short: every RFC's migration is additive-only and verified
upgrade→downgrade→upgrade against real Postgres before merge — a
deployment can always roll back a migration without touching unrelated
tables, confirmed directly rather than assumed (RFC-001 § Migration guide
is the clearest documented example).

## Q. Reliability (209–220)

**209. What's the "never swallow an error" rule, concretely?**
Short: an agent that can't reach a conclusion returns `status=failed`/
`status=partial` with a reason — never a plausible-looking default.
Detail: `AGENT_FRAMEWORK.md` § Error Handling & Retries, "Never" section,
stated as an absolute.

**210. How does `RunCoordinator` handle an agent exception?**
Short: persists it as a failed `Run`/`AgentStep` rather than crashing the
request — confirmed directly from `BaseFrontierAgent`'s own docstring,
quoting `RunCoordinator.execute_run`'s stated behavior.

**211. What's the retry policy for a low-confidence agent result?**
Short: one retry with an adjusted plan (typically: gather more evidence)
before accepting a low-confidence result — `should_retry_after_low_confidence`,
generalized from the original Review agent to every agent
(`AGENT_FRAMEWORK.md`).

**212. How does a `HypothesisGenerator` failure affect the rest of an
indexing run?**
Short: isolated — logged and swallowed for that generator alone, "never
blocking or corrupting another's output for the same run" (ADR 0018
invariant, verified by RFC-02B's own test suite, not just asserted).

**213. What happens if the LLM provider silently returns malformed JSON?**
Short: caught, degrades to a `status="failed"` Evidence entry (Frontier
agents) or a deterministic fallback (Context Discovery) — never
propagated as a crash, and never silently treated as a successful empty
result either.

**214. How reliable is the indexing pipeline against a mid-run process
crash?**
Short: not reliable — a named, honest gap. A crash mid-run leaves the
`IndexingJob` row `running` forever (ADR 0007 § Consequences). This is the
single clearest "if asked, say it plainly" reliability gap in the whole
codebase.

**215. What's the reliability guarantee behind Engineering Memory's
append-only design?**
Short: no update path exists at any layer for the tables it owns — a
partial write can leave a gap, but it can never leave a *corrupted*
historical record, since nothing is ever mutated after the fact.

**216. How does GraphForge avoid a bad confidence formula silently
corrupting historical interpretation?**
Short: `formula_version` — a formula change is versioned and auditable,
never a silent redefinition of what past confidence scores meant.

**217. What's the reliability posture of the materializer replay?**
Short: proven byte-for-byte reconstructable in testing, not yet
operationally relied upon (since it isn't the live write path) — a real
distinction between "reliable in principle, tested" and "reliably
operated in production."

**218. How does GraphForge handle a partially-failed cross-repository
indexing run (one repo succeeds, its pair fails)?**
Short: not directly documented in the ADRs read; the closest evidence is
`relink_account`'s per-account lock and `run_indexing`'s existing
failure-isolation precedent for unrelated failures (e.g. `relink_account`
itself, isolated from the main indexing flow) — extend cautiously, don't
assert a guarantee not directly confirmed for this specific scenario.

**219. What's the blast radius of a single validator having a bug?**
Short: bounded to hypotheses of the relationship types in that
validator's own `applies_to` — other validators and other relationship
types are structurally unaffected, by the registry-dispatch design (§05).

**220. How does GraphForge ensure a degraded LLM narrative never corrupts
a deterministic fact?**
Short: structurally — the LLM narrative and the deterministic
service-computed facts are separate fields in the response; `render_response`
only ever narrates, never overwrites, the `ExecutionResult` it was handed
(§08).

## R. Code Quality (221–232)

**221. What's the stated discipline around "audit before reusing a
pattern"?**
Short: repeated multiple times across ADRs as an explicit practice — e.g.
RFC-06's decision *not* to reuse `context_pipeline.reasoning.curation.curate()`
after auditing it and finding it solves a different problem (§05); RFC-06C's
decision not to build a second "evidence fusion" layer after auditing and
finding one already exists (§04).

**222. How does GraphForge avoid dispatch-code sprawl for validators and
generators?**
Short: registries, not `if`/`elif` chains — an explicit, named,
repeatedly-applied pattern (§10 "why a registry").

**223. What's an example of a contract found to be wrong after
implementation, and how was it handled?**
Short: RFC-01's confidence-formula contract gap, found during RFC-03 —
fixed immediately with two additive fields, documented verbatim including
rejected alternatives, rather than silently patched around. Detail: quoted
directly in § [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md).

**224. How does GraphForge keep test doubles honest?**
Short: "real Postgres/Neo4j in integration tests... mock only the exact
external HTTP boundary" — a stated, consistently-applied rule
(`ROADMAP.md` § Testing Strategy), not just a preference.

**225. What's a concrete instance of resisting premature abstraction?**
Short: `shadow_runner.py`'s deliberate single-generator, no-registry
shape at RFC-02B — "a registry with one entry is not an interface earning
its keep," with an explicit, named trigger (RFC-06, the second generator)
for when to build the abstraction.

**226. How are magic numbers/thresholds kept from drifting apart across
modules?**
Short: made public and referenced directly rather than hand-copied —
`HIGH_RELIABILITY_TIER`/`MIN_DISTINCT_SOURCE_TYPES_FOR_VERIFIED` renamed
public specifically so `explainability.py` cites the engine's real
constants instead of a second, driftable copy (ADR 0018 RFC-06C).

**227. What's the code-quality rule around determinism and Python's hash
randomization?**
Short: never sort or key by `dict`/`set`/`frozenset` iteration order or
raw string hashing — caught as a real bug once (`_primary_repository`,
ADR 0014) and now a named, explicit discipline applied in the parity
comparator and explainability module alike.

**228. How does GraphForge keep a "frozen" contract from being silently
reopened?**
Short: by building alongside it instead — `ServiceExecutor` was built as a
separate, package-local superset dispatcher specifically because
`OrganizationKnowledgeService`'s `ComposedAnswer`/`ServiceRequest`
contracts were "explicitly frozen for this RFC" (§08).

**229. What's the naming discipline around ADR/RFC numbering when
sequencing changes mid-stream?**
Short: numbered out-of-sequence entries (RFC-02B, RFC-05B) are explicitly
labeled and cross-referenced rather than silently renumbering everything
— preserves the historical record of what was actually decided when.

**230. How does GraphForge avoid one team's new node/edge type breaking
another team's traversal assumptions?**
Short: additive-only schema evolution plus the (proposed) `GraphWriter`
schema-registry choke point — named directly as the top mitigation for
the Risk Register's "graph schema sprawl" risk.

**231. What's an example of the codebase correcting its own earlier,
less-careful proposal?**
Short: ADR 0014 explicitly overturned an "earlier, less careful proposal
that would have left `graph_components` and `evidence_package` as two
permanently parallel paths" — the exact bug shape a separate evaluation
had already found once — and migrated every consumer instead.

**232. How readable is a manifest file meant to be, by design?**
Short: "the single file a reviewer reads to understand what an agent does
without reading its implementation" — an explicit design goal for
`AgentManifest`, confirmed accurate against real manifests audited for
this handbook (§08).

## S. Maintainability (233–246)

**233. What makes adding a new validator low-risk to merge?**
Short: it's additive by construction — `run_validators` only dispatches a
validator to a hypothesis whose type is in that validator's own
`applies_to`; adding one changes nothing for existing types, proven by a
parity test on every addition (§05).

**234. How would you extend the confidence state machine (e.g. add a
seventh state)?**
Short: not a free change — `formula_version` bump required, and every
downstream UI/API consumer of the six-state enum would need explicit
review; named directly in §10 as "a real design commitment," not a casual
extension point.

**235. What's the maintenance cost of the open-vocabulary registries?**
Short: they "become a long-term-maintained public surface" — and a value,
once used, can never be renamed, only deprecated. Stated directly in ADR
0018 § Consequences as a real, accepted cost, not hidden.

**236. How is documentation kept from drifting from implementation?**
Short: partially — this handbook itself found at least one instance of
drift (`ARCHITECTURE.md`'s "proposed" framing of the Orchestrator, which
is actually implemented) and `docs/deployment/01_ARCHITECTURE.md`
explicitly states its own discipline: "verified against the code, not
against product documentation elsewhere in docs/... where they disagree,
this document follows the code." That's a real, working anti-drift
practice for at least the deployment docs, not a universal guarantee
across every doc in the repo.

**237. What's the maintainability benefit of the propose/commit split
being enforced at two layers (service + schema)?**
Short: a future refactor of the service layer alone can't silently
reopen the boundary — the schema-level absence of `agent_role` on
`DecisionCommitRequest` is a second, independent enforcement point that
would need its own deliberate change to weaken (RFC-001).

**238. How maintainable is the `BaseFrontierAgent` pattern as agent count
grows?**
Short: highly, by design — each new agent adds exactly three pure
functions; the shared `run()` loop, metrics, error handling, and
`AgentOutput` assembly are maintained once, in one place, for every
current and future Frontier agent (§08).

**239. What's the maintainability trade-off of shadow-mode delivery?**
Short: code ships and is tested well before it does anything, which means
a reviewer months later needs the RFC history to know *why* a fully-built,
fully-tested module (e.g. the materializer) isn't yet wired into
production — a real documentation-dependency cost, mitigated here by ADR
0018 being extremely explicit about exactly this status per RFC.

**240. How would a new engineer figure out what's actually live vs.
proposed in this codebase?**
Short: read [16_REALITY_CHECK.md](16_REALITY_CHECK.md) first, then check
the specific ADR's own "Status" field (Accepted/Implemented/Proposed) and
its dated implementation note — this codebase is unusually disciplined
about stating this explicitly per-document, which is itself a
maintainability asset worth naming in review.

**241. What's the risk of the Learning Engine's explicit non-goals list
being quietly built anyway without a corresponding ADR?**
Short: not observed in this audit, but worth naming as a governance
question: RFC-06D lists automatic prompt evolution, calibration, health
scoring, org-wide learning, and model benchmarking as explicitly not
built — a maintainability review should confirm any future work on these
gets its own RFC rather than accreting undocumented inside
`LearningEngineService`.

**242. How does GraphForge keep a deprecated taxonomy value from being
silently reused?**
Short: not fully confirmed by this audit as an automated check — the
*rule* is explicit (never rename, only deprecate) but whether a linter or
registry-level guard enforces "don't reuse a deprecated value" wasn't
independently verified. Flag as a "policy exists, enforcement mechanism
not confirmed" item.

**243. What's the maintainability cost of the two parallel generator-
registry loops (`shadow_runner.py`'s hardcoded call plus
`generator_registry.py`'s loop)?**
Short: named directly and accepted as a deliberate, reasoned trade-off
(RFC-06) rather than an oversight — "same isolation guarantee... reached
via two isolated loops instead of one, not a smaller guarantee." A future
maintainer should know this is intentional before "simplifying" it into
one loop.

**244. How readable is the RFC roadmap for someone joining mid-stream?**
Short: very — each RFC entry states implementation status, exact test
files, rollback plan, and migration impact inline, so a reader doesn't
need to cross-reference a separate project tracker to know what's real.

**245. What's the biggest maintainability risk if RFC-07 through RFC-09
land out of order or partially?**
Short: not directly addressed — these are still roadmap-only; the
existing discipline (dated Status fields, explicit "Implemented" markers)
should carry forward, but this audit can't confirm future execution, only
past discipline.

**246. How would you audit whether "never swallow an error" is actually
upheld everywhere, not just where documented?**
Short: grep for bare `except Exception: pass` (or equivalent silent
catches) outside the specifically-documented, deliberate isolation points
(generator/validator failure isolation) — a concrete, falsifiable
verification method, not just trusting the docstrings.

## T. Future Vision (247–260)

**247. What's the long-term vision, verbatim?**
Short: an org where no engineer re-derives "what does this touch" from
memory, every SDLC artifact is a permanently-traversable node, new agents
are additive, and the org's engineering knowledge compounds instead of
evaporating with attrition. Detail: `PRODUCT_VISION.md` § Long-Term Vision,
four bullets.

**248. What's Phase 2's exit criterion?**
Short: "a Jira story can flow Requirement → Planning → Architecture with
visible, evidence-backed output in the Pipeline UI, without touching a PR
at all" (`ROADMAP.md`).

**249. What's Phase 3's exit criterion?**
Short: "a story can flow Requirement → ... → Release with every stage
evidence-backed and visible in Pipeline; Selector correctly routes at
least as well as the rule table it replaces."

**250. What's the "Future Vision" closing statement of `ROADMAP.md`?**
Short: "GraphForge is the system an engineer opens *before* opening Jira
or GitHub directly — because starting there is strictly faster."

**251. What future generators does RFC-06 name as natural next steps?**
Short: Runtime, Documentation, Infrastructure, API, Security, Git History,
Human generators — named directly as `generator_registry.py`'s "intended
future entries," not yet built.

**252. What's the future of the confidence formula beyond
`DefaultConfidenceEngine`?**
Short: nothing concrete proposed to replace it; `formula_version`
exists to make a future formula change auditable when/if one happens —
no ADR commits to a specific successor.

**253. What's the future of multi-provider LLM consensus, and its
built-in caveat?**
Short: RFC-08 — cross-provider agreement capped at "at most one distinct
confirming source type" even with multiple providers agreeing, "per the
correlated-training-data caveat" designed in before the feature exists.

**254. What's the future of incremental evidence ingestion, and what
ships first?**
Short: RFC-09 — infrastructure manifests first ("lowest staleness risk"),
runtime telemetry last ("highest operational complexity") — a
deliberately sequenced rollout, not "everything at once."

**255. What's the future of the materializer becoming the live write
path?**
Short: named as the natural next step across this handbook's own analysis
(§10, §16) but **not scheduled to any specific RFC** in the source
material read — an inference this handbook makes explicitly, not a
documented commitment. Say so if asked "when."

**256. What's the future of cross-organization graph federation?**
Short: explicitly not needed "until a customer requests it" —
`ROADMAP.md` Backlog, a deliberately reactive, not speculative, stance.

**257. What's the future of an LLM-based Selector?**
Short: Phase 3, A/B'd against the rule-based Selector before becoming
default, swap is config-only per the Plugin Architecture contract.

**258. What's the future of natural-language Goal inference at entry?**
Short: named as a Stretch Goal, not phase-committed — "Phase 3's LLM
Selector, stretched to also infer Goal from free text."

**259. What's the future of org-wide "impact simulation" without a real
diff?**
Short: named as a Stretch Goal — "ask the graph 'if I change X, what
breaks' ... purely from graph structure, as a standalone Knowledge Graph
feature independent of any PR." Not built; philosophically adjacent to
today's `ChangeSimulationService`, but that service requires an explicit
`ChangeType`/entity today, not a truly open-ended diff-free query.

**260. If GraphForge fully executes its own roadmap, what changes most
about how it's evaluated?**
Short: the evaluation question shifts from "does the deterministic core
work" (largely yes, today, within named limits) to "does cross-system,
cross-repository, multi-agent reasoning actually compound knowledge over
time" — which is precisely the thesis `PRODUCT_VISION.md` states and which
today's four documented gaps (Kafka literal-matching, Feign naming,
same-repository impact-analysis filter, intra-repository-only dependency
counts) show is not yet proven at the cross-repository layer specifically.
That is the honest, single highest-leverage thing to watch next.
