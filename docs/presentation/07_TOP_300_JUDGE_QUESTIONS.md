# 07 — Top 300 Judge Questions

Format per question: **S** (short, 30s) · **D** (detailed, grounding) ·
**W** (whiteboard cue) · **Refs** (code/ADR/RFC). 26 categories, ~12 each,
312 total. Every answer traces to `docs/handbook/`, an ADR/RFC, or actual
source under `backend/app/` — none are invented. Where nothing is
measured/built, that's stated explicitly rather than guessed.

## A. Product (1–12)

**1. What problem does GraphForge solve?**
S: "Does this PR break anything" asked of the same senior engineer ten
times a week. D: Knowledge lives in people's heads and evaporates with
attrition; GraphForge makes it queryable, persistent, evidence-backed.
W: draw a person → graph → next engineer. Refs: `PRODUCT_VISION.md`.

**2. Who is the target user?**
S: IC engineers, tech leads, EMs, platform teams. D: Four named personas
(Priya, Marcus, Ana, Devon) each with a distinct want-statement. Refs:
`PRODUCT_VISION.md` § Personas.

**3. What's explicitly out of scope?**
S: Chatbot, Jira replacement, standalone PR bot, autonomous auto-merge,
observability platform. D: Five stated non-goals, each with its own
one-line reasoning. Refs: `PRODUCT_VISION.md` § Out of Scope.

**4. How do you define success?**
S: Time-to-context drops from "ask a senior engineer" to "assembled
before you finish reading the ticket." D: Four criteria — time-to-context,
traceable AI output, additive agent shipping, graph outlives any one
feature. Refs: `PRODUCT_VISION.md` § Definition of Success.

**5. What's the product philosophy in one sentence?**
S: "The graph is the product. The agents are features of the graph." D:
Every feature must name a graph node/edge type it reads or writes, or
it's scoped out. Refs: `PRODUCT_VISION.md`.

**6. Why not just build a better Jira?**
S: Explicit non-goal — we enrich ticket systems, not replace them. D:
Reimplementing sprint planning/capacity tracking is out of scope by
design; the value is cross-system reasoning, not ticket management.

**7. What's the minimum viable version of this product?**
S: Deterministic indexing + one grounded agent (Review) — proven at
ChangeGuard scale already. D: The generalization to full SDLC is the
expansion; the core bet was retired before this hackathon began.

**8. How do you know engineers actually want this?**
S: Not measured with real users yet — honest answer. D: The predecessor
(ChangeGuard) validated the narrower PR-review thesis; broader validation
is roadmap.

**9. What's your product's biggest current weakness?**
S: Cross-repository reasoning has real, documented precision gaps. D:
Four numbered known gaps from our own validation suite — see
`12_REALITY_CHECK_PRESENTATION.md`.

**10. What's the pricing/business model?**
S: Not defined — hackathon scope. D: Don't invent one; this is honestly
unaddressed in the current product docs.

**11. Who are your competitors?**
S: Generic LLM chatbots, PR-bot point solutions, Jira AI add-ons. D: None
combine persistent cross-system memory + deterministic grounding — the
stated competitive positioning table. Refs: `PRODUCT_VISION.md`.

**12. What's the single most important feature?**
S: The Knowledge Engine's evidence-gating — everything else is built on
top of it. D: Without validator-gated promotion, this is just another
AI-wrapper product.

## B. UX (13–24)

**13. Describe your design system.**
S: Dark-themed Tailwind, no component library, 4px spacing scale. D: No
visual reset from the ChangeGuard-era UI — new surfaces must be
indistinguishable in craft from existing pages. Refs: `UI_GUIDELINES.md`.

**14. How do you show AI confidence in the UI?**
S: Always a percentage next to the claim, never a bare adjective. D:
Evidence-over-assertion applied to the UI itself — `ConfidenceBadge`,
color-neutral, paired with `EvidencePanel`. Refs: `UI_GUIDELINES.md`.

**15. What's your error-state convention?**
S: One real error message in a fixed rose-colored banner, never "something
went wrong." D: Same convention across every page — no per-page bespoke
error UI. Refs: `UI_GUIDELINES.md`.

**16. How do color choices communicate meaning?**
S: Color maps to action category (primary/agentic/publish/danger), never
decoration. D: A new button color must map to a defined category or it
doesn't ship. Refs: `UI_GUIDELINES.md`.

**17. How accessible is the UI?**
S: Real `<button>`/`<a>` elements only, color never the sole signal,
`disabled` attribute respected by screen readers. D: Not independently
audited — state that honestly if asked for a WCAG score.

**18. Walk me through the user journey.**
S: Connect repo → indexed deterministically → ask a question or review a
PR → graph-grounded, evidence-linked answer. W: draw the 4-step funnel.

**19. How do long-running agent runs communicate progress to the user?**
S: The Agents run-detail panel shows steps appearing as they complete,
never a blank spinner. D: A deliberate anti-pattern rejection — "watch it
reason," not "wait blindly." Refs: `UI_GUIDELINES.md` § Loading States.

**20. What happens in the UI when an agent partially fails?**
S: `status=partial`/`status=failed` surfaced verbatim, never silently
omitted. D: Matches `ARCHITECTURE.md`'s Error Handling principle exactly.

**21. Have you done usability testing?**
S: No — hackathon scope, honest answer. D: Don't claim NPS/usability
metrics that don't exist.

**22. Is the product mobile-responsive?**
S: Sidebar collapses on mobile via existing `AppLayout` pattern. D:
Reused, not redesigned — this is a dense engineering tool, not
mobile-first by intent.

**23. Why no animations/marketing polish?**
S: Deliberate — "this is a dense engineering tool, not a marketing
site." D: Minimal by design; only functional transitions (disabled state,
incremental step fade-in). Refs: `UI_GUIDELINES.md` § Animations.

**24. How would a brand-new user know what "candidate confidence" means?**
S: Honestly, not confirmed — no in-UI explainer verified in this audit.
D: State this as an open UX question, not a solved one.

## C. Architecture (25–37)

**25. Draw the system architecture.**
S: Indexer → Knowledge Engine → Engineering Memory (Postgres) →
Materializer → Neo4j → Service Layer → Agents → API. W: five-stage
pipeline + two-store split. Refs: `docs/handbook/03_ARCHITECTURE.md`.

**26. What are the major subsystems?**
S: Indexer, Knowledge Engine, Engineering Memory, Frontier (generator +
agents), Engineering Intelligence Service Layer, Orchestrator, Context
Discovery, Engineering Session, Learning Engine, Validation Framework.

**27. Is the Orchestrator actually built?**
S: Yes — real code (`registry.py`, `selector.py`, `run_coordinator.py`),
not just a proposal. D: We found and corrected a doc-drift where
`ARCHITECTURE.md` undersold this as "new."

**28. What's the hexagonal layering convention?**
S: `api → services → engine/agent → integrations/graph`. D: Reused
wholesale from the ChangeGuard predecessor (ADR 0001/0003).

**29. How does a new integration get added?**
S: Implement `IKnowledgeSource`, register once — zero orchestrator/agent
changes. D: One of three additive extensibility axes named in
`PRODUCT_VISION.md`.

**30. What's `RunContext` / Shared Memory, really?**
S: A run-scoped scratch store for intermediate agent outputs. D:
Documented as in-memory/single-process today, not the Redis-backed
version the design calls for — a stated, temporary substitution.

**31. What's the difference between `app.context` and
`app.context_pipeline`?**
S: `app.context` is the thin, mostly-unbuilt Entry Resolver concept;
`app.context_pipeline` is the real, heavily-tested Context Discovery
engine. D: Don't conflate them — a real naming trap in this codebase.

**32. How is the graph schema kept from sprawling as more teams add types?**
S: Additive-only evolution + a proposed `GraphWriter` schema registry
choke point. D: Named directly as a "High" impact risk in `ROADMAP.md`'s
Risk Register.

**33. What's the domain model for a Run?**
S: `Subject → AgentRun → AgentStep → GraphFact`, Postgres for the audit
trail, Neo4j for the knowledge itself. D: Mirrors the pre-existing
`PullRequestAnalysis` (Postgres) vs. dependency graph (Neo4j) precedent.

**34. Why FastAPI + async SQLAlchemy?**
S: Reused wholesale from the ChangeGuard predecessor. D: Production
lineage, not a hackathon-weekend choice.

**35. What would you redesign if starting over?**
S: Cut the materializer over to be the live write path sooner. D: Named
directly as this handbook's own top redesign candidate, paired with
Context Discovery's missing feedback loop.

**36. How does a request actually flow end to end?**
S: Client → Orchestrator manifest lookup → preflight → service calls →
LLM narration → persisted Run/AgentStep. W: draw the Repository
Understanding Agent's exact call chain. Refs: `docs/handbook/03_ARCHITECTURE.md`.

**37. What's the single most defensible architectural decision?**
S: The Postgres-source-of-truth / Neo4j-derived-projection inversion. D:
Proven by a real replay test, not just asserted.

## D. AWS (38–49)

**38. Why Fargate?**
S: No GPU/bin-packing/AMI need for 2 lightweight services. D: Evaluated
against EC2, EKS, App Runner, Lambda explicitly — each rejected for a
specific, stated reason. Refs: `09_AWS_DEFENSE.md`.

**39. How are AWS credentials handled?**
S: Zero static keys anywhere. D: Bedrock via boto3's default credential
chain through the ECS Task Role — confirmed in the provider's own
docstring.

**40. What IAM roles exist?**
S: Five — execution, backend task, frontend task, CI/CD deploy (OIDC),
optional rotation Lambda. D: Each scoped to the literal minimum
operations it performs.

**41. How is TLS handled?**
S: Terminated at the ALB via ACM cert. D: DB connections separately use
`sslmode=require`/`neo4j+s://`.

**42. What's your DR posture?**
S: Multi-AZ RDS failover within a region; no cross-region DR. D: A
considered decision — no current requirement justifies the added
cost/complexity.

**43. How do you scale the backend?**
S: You currently don't, past 1 replica — a stated, honest constraint. D:
In-process background execution doesn't survive multi-replica
deployment yet.

**44. What triggers a CloudWatch alarm you'd actually care about?**
S: A metric filter on the literal string `"recovered_orphaned_runs"`. D:
Turns a silent data-loss event into a paged, trackable incident.

**45. How are secrets rotated?**
S: Per-secret strategy — RDS via native rotation Lambda, JWT safe
anytime, token encryption key requires a migration that doesn't exist
yet. D: Stated as a known pre-requisite, not hidden.

**46. What's your cost optimization story?**
S: Not modeled with real numbers. D: Architecturally cost-aware choices
exist (Fargate over EC2, no speculative Redis/S3) but no cost dashboard.

**47. Is your CD pipeline live?**
S: No — CI is real, CD is a specification. D: State this distinction
precisely, don't imply automated AWS deploys exist.

**48. How would a Bedrock model swap work operationally?**
S: Config change + matching IAM policy update, same deploy. D:
Access-denied on mismatch is intended behavior, not a bug.

**49. What's not in your AWS design, and why?**
S: No ElastiCache/Redis (nothing to cache/no session store), no SQS yet
(app-architecture change needed first), no multi-region. D: Each is a
stated, deliberate omission, not an oversight.

## E. AI (50–62)

**50. Why use AI at all here?**
S: Two narrow roles — hypothesis generation and narration — never fact
decision. D: `PRODUCT_VISION.md` Core Principle 3.

**51. Why deterministic-first?**
S: An LLM-only system can't distinguish confirmed fact from a plausible
guess. D: The repeated invariant across every ADR in this codebase.

**52. What LLM providers do you support?**
S: Bedrock, OpenAI, Gemini, Groq via one registry pattern. D: No vendor
string-comparisons scattered through the app.

**53. How do you control LLM cost?**
S: `cost_class` per manifest, opt-in generator policies, measured hard
caps (e.g. mid-loop synthesis capped at 1 extra call after measuring a
92s→165s test-suite regression at a budget of 2).

**54. What's your prompt engineering approach?**
S: Curated, budgeted, kind-diverse evidence sampling — never a raw
context dump. D: Seven numbered ground rules for Context Discovery's
synthesis prompt specifically.

**55. How do you prevent prompt injection from repo content?**
S: Not addressed as a named threat model in our ADRs — honest gap,
don't invent a defense.

**56. What's the LLM's actual job in the Knowledge Engine?**
S: One more `HypothesisGenerator` — proposes, never writes.

**57. How is AI output explained to the user?**
S: `ConfidenceExplanation`, computed once, deterministic, references the
engine's own real thresholds directly.

**58. What happens when the LLM call fails?**
S: Graceful degradation to deterministic fallback text — never a crash,
never a fabricated success.

**59. Do you fine-tune any models?**
S: No. D: Not proposed anywhere in our ADRs — the architecture solves
trust structurally, independent of the model's own tuning.

**60. How would you add a new LLM provider?**
S: One new `ProviderSpec` registry entry.

**61. What's your stance on AI safety broadly?**
S: Evidence-over-assertion as a structural, not just ethical, principle
— every claim traces to real evidence or it doesn't ship.

**62. What's the weakest link in your AI pipeline?**
S: `EvidenceKeywordValidator`'s substring matching — narrow, explainable,
but not semantic; a misleading text match is a real theoretical risk.

## F. Knowledge Engine (63–74)

**63. What are the five pipeline stages?**
S: Evidence → Hypothesis → Validation → Confidence → Knowledge. Refs: ADR
0018.

**64. What's a `HypothesisGenerator`, precisely?**
S: `generate(pack) -> list[Hypothesis]`, nothing else — never writes to
the graph.

**65. What's a `KnowledgeValidator`, precisely?**
S: A pure function of `(Hypothesis, EvidencePack)` — never calls an LLM,
never does extra I/O.

**66. Why is the vocabulary "open," not a closed enum?**
S: A new evidence source or relationship type is a registry entry, not a
schema migration — but once used, never renamed, only deprecated.

**67. What's `generator_confidence` used for?**
S: Advisory display only — structurally forbidden from influencing
validation or confidence aggregation.

**68. How do generators get added without breaking others?**
S: Isolated failure — one generator's crash is logged and swallowed,
never blocks another's output for the same run.

**69. What's the difference between RFC-02 and RFC-06?**
S: RFC-02 adapted the existing deterministic parsers as generators;
RFC-06 added the first LLM generator, with its own evidence source and
policy gate.

**70. How do you keep the pipeline cheap for the common case?**
S: A deterministic hypothesis with no contradiction clears a trivial
O(1) validator rather than the full suite — same pipeline, not a fork.

**71. What's `EngineeringEvidencePack`?**
S: One immutable, content-addressed blob per `(repository_id, commit_sha,
schema_version)` — the shared input every generator/validator reads.

**72. Why is evidence a blob, not per-row rows?**
S: Cardinality — tens of thousands of items per run would be a
write-throughput failure as an OLTP table.

**73. What's a "delta pack"?**
S: An incremental evidence append — contract exists, no shipped source
uses it yet (RFC-09, roadmap).

**74. How do you guarantee reproducibility?**
S: Same pack + same generator/validator/confidence-engine versions →
same result, always — a stated engineering invariant.

## G. Neo4j (75–86)

**75. Why Neo4j specifically?**
S: Native traversal for blast-radius/dependency queries.

**76. Is Neo4j your source of truth?**
S: No — deliberately not, by ADR 0018's own inversion. Postgres is.

**77. Is the indexer Neo4j-specific code?**
S: No — `IGraphRepository` is graph-store-agnostic; Neo4j is the only
concrete implementation today.

**78. How do you prevent Cypher injection?**
S: Explicit, fixed, internally-controlled label/relationship-type
allowlists, never derived from request input.

**79. How are node IDs constructed?**
S: `f"{repository_id}:{kind}:{key}"` — re-indexing the same repo always
produces the same IDs, so `MERGE` upserts correctly in place.

**80. What's the bounded-hop traversal primitive?**
S: `get_neighborhood`, `[1,5]` hops, replacing an unbounded
`get_full_graph` read that had "no depth bound at all."

**81. Can two edges share the same source/type/target?**
S: Yes, legitimately — e.g. two Kafka-producer methods on one class,
same topic. This is why edge comparison uses a multiset, not a set.

**82. Is the graph versioned/historical at the Neo4j layer?**
S: No — full replace on every re-index. History lives in Engineering
Memory instead.

**83. What node/edge types exist today?**
S: `Repository`, `Component`, `Controller`, `Service`, `FeignClient`,
`Endpoint`, `KafkaTopic`, `MavenDependency` (+ Python equivalents);
`CONTAINS`/`EXPOSES`/`CALLS`/`PRODUCES_TO`/`CONSUMES_FROM`/`DEPENDS_ON`
plus cross-repo `CALLS_SERVICE`/`SHARES_TOPIC`/`DEPENDS_ON_REPOSITORY`.

**84. How would you swap Neo4j for another graph DB?**
S: One new `IGraphRepository` implementation — no indexer/service/agent
changes required, by design.

**85. What powers the Graph Parity dashboard?**
S: `app.knowledge_engine.parity.comparator.compare_graphs` — pure,
deterministic, multiset-based edge comparison.

**86. How does Neo4j handle multi-tenancy?**
S: Namespaced node IDs per repository; `organization_id` scoping is
specified at the architecture level — not independently re-verified at
every query path in this audit.

## H. Engineering Memory (87–98)

**87. What is Engineering Memory?**
S: The append-only Postgres log of every hypothesis, validation,
correction, and confidence transition ever computed.

**88. Why append-only?**
S: The audit trail is the actual capability — confidence history and
relationship evolution require nothing ever being silently overwritten.

**89. What's never edited, only superseded?**
S: `Hypothesis`, `ValidationResult`, `UserCorrection`, confidence-state
transitions.

**90. What IS mutable/archivable?**
S: Only raw `EvidencePack` blobs — regenerable by re-extraction at the
same commit.

**91. How is "current" state computed?**
S: Read-time — latest `sequence` per `relationship_key` over immutable
history, never a second mutable source of truth.

**92. Why does a repeat run append a new row even with unchanged
confidence?**
S: Intentional — Engineering Memory records what the pipeline concluded
each run, not whether it was "new information."

**93. What's the `sequence` column for?**
S: Monotonic ordering immune to same-transaction timestamp collisions —
found necessary by a real integration test, not designed preemptively.

**94. Why Postgres and not a document store?**
S: Mixed cardinality — blob storage for huge evidence packs, real
relational rows for lower-cardinality hypotheses/validations/relationships.

**95. How is a human correction different from an agent correction?**
S: Only human corrections carry unconditional override authority
(`trust_level=1.0`); agent corrections re-enter the same validation
pipeline as any other hypothesis.

**96. What happens to old evidence packs?**
S: Archivable after the last N successful runs — regenerable, unlike
everything else in the log.

**97. Is there a UI for confidence history?**
S: The Graph Parity dashboard is the closest live surface; a dedicated
"as-of" query UI wasn't confirmed in this audit.

**98. What's the biggest risk of this design?**
S: Unbounded row growth by design — accepted explicitly, not hidden, in
ADR 0018's own Consequences section.

## I. Validators (99–108)

**99. What does a validator return?**
S: `confirms` / `contradicts` / `no_signal` — never a bare boolean.

**100. Why can a validator never call an LLM?**
S: "A validator that itself asks an LLM 'does this seem right' isn't
validating, it's generating a second, uncoordinated hypothesis."

**101. What validator families exist?**
S: Deterministic-structural, cross-repository, evidence-keyword.

**102. How does `EvidenceKeywordValidator` work?**
S: Deterministic substring matching of a small technology keyword table
against a hypothesis's own cited evidence — one class, instantiated four
times per domain.

**103. Why does it never return `contradicts`?**
S: Absence of a keyword in incomplete evidence isn't proof of absence —
the same discipline every validator follows.

**104. How is validator execution parallelized safely?**
S: `asyncio.gather`, results reassembled in selection order (never
completion order) — proven deterministic under real concurrency, not
just sequential try/except.

**105. What happens if one validator throws?**
S: Isolated — never discards another validator's result for the same
hypothesis.

**106. How do you add a new validator?**
S: One registry entry — zero changes to existing validators or dispatch
logic.

**107. What validators are deliberately NOT built yet?**
S: Runtime, Ownership, API-contract validators — no evidence source
exists yet to validate against; building one anyway would be speculative
infrastructure.

**108. Can a validator's confirmation alone promote to Verified?**
S: No — Verified requires ≥2 independent confirming source types, never
one validator alone.

## J. Confidence (109–118)

**109. What are the six confidence states?**
S: verified, highly_likely, likely, candidate, rejected, conflicting.

**110. What makes the engine "incremental"?**
S: Folds one new `ValidationResult` at a time into a prior model — never
re-scans full history.

**111. What makes it "monotonic"?**
S: A confirmation only strengthens state, a contradiction only weakens
it, neither regresses the other's already-recorded effect.

**112. What's `HIGH_RELIABILITY_TIER`?**
S: The constant (3) marking a deterministic parser's own literal finding
as the highest-trust evidence class — public specifically so the
explainer can cite it directly.

**113. How was the formula validated?**
S: A parity test reproducing the pre-existing hand-assigned
structural/heuristic labels exactly, for every current cross-repo edge
type.

**114. What contract gap was found during implementation?**
S: The original formula was literally uncomputable without
`confirming_source_types` and `max_confirming_reliability_tier` — found
and fixed, documented with rejected alternatives, during RFC-03.

**115. Can confidence regress?**
S: Only downward via contradiction, never via a merely-absent expected
validator report.

**116. Does the LLM's own confidence score count toward the formula?**
S: Never — confidence is derived only from independent
`ValidationResult`s.

**117. What's `formula_version` for?**
S: Versioning the formula itself so a change is auditable, never a silent
redefinition of historical scores.

**118. Could you swap in an ML-based confidence engine?**
S: Architecturally possible (it's an ABC with one reference
implementation) — not proposed or built.

## K. Learning Engine (119–128)

**119. What does the Learning Engine actually do today?**
S: Captures human feedback (approve/reject/correct) as an append-only
event log and applies corrections via Engineering Memory's existing
method.

**120. Why is it a sibling package, never imported by the Knowledge
Engine?**
S: One-directional dependency by design — a feedback loop can never
quietly become a second confidence-influencing input.

**121. What was the audit finding that motivated it?**
S: `UserCorrection`/`apply_correction` existed since RFC-04 but had zero
callers anywhere — no API let a human actually use it.

**122. What's explicitly NOT built?**
S: Automatic prompt evolution, validator/confidence calibration, a
recommendation engine, repository health scoring, org-wide learning,
model benchmarking.

**123. What statistics does it compute?**
S: Approval/rejection rate, per-relationship-type breakdown, repeated-
false-positive signal, two-halves rejection-rate trend — no ML, no LLM.

**124. Does feedback ever mutate history?**
S: No — proven directly by comparing row ids/state before and after;
append-only, same as everything else in Engineering Memory.

**125. How would calibration tracking get built on top of this?**
S: `LearningStatistics`/`learning_events` already keyed by
`relationship_type` and `generator_names` — the exact dimensions a
calibration feature needs, zero schema change required.

**126. What REST endpoints exist?**
S: `POST/GET /repositories/{id}/learning/feedback|events|statistics`.

**127. What's the performance impact of the Learning Engine?**
S: Negligible — no import path into indexing, generation, validation, or
confidence computation at all.

**128. Is this reinforcement learning?**
S: No — pure deterministic aggregation and correction application, no ML
model involved anywhere in this package.

## L. Agents (129–140)

Full detail: `10_AGENT_DEFENSE.md`.

**129. What's an `AgentManifest`?**
S: id, purpose, accepted subject types, goals, cost class, max graph
hops, output schema — the one file that describes an agent without
reading its implementation.

**130. What are the three hooks a Frontier agent implements?**
S: `build_service_requests`, `build_prompt`, `render_response` — all
pure, no I/O.

**131. How many agents are registered?**
S: 12+, confirmed by direct audit of `app/agents/*/manifest.py`.

**132. What's a "standalone AI Workspace" agent vs. a workflow-stage
agent?**
S: Standalone = reachable directly via `POST /agent-runs`, absent from
the Workflow pipeline's stage table; workflow-stage = part of the
sequential SDLC chain.

**133. How do agents collaborate?**
S: Sequential handoff (same-run) or graph-mediated (default, loose,
asynchronous).

**134. Can an agent write to the graph directly?**
S: No agent writes to the graph directly — that's the Knowledge Engine's
job, gated by validators.

**135. How is agent confidence computed?**
S: Generically, by `result_mapper`, as "how many service calls
succeeded" — deliberately minimal, not domain judgment.

**136. What happens on agent failure?**
S: Persisted as `status="failed"` with the real error, never silently
swallowed.

**137. How would you add agent #13?**
S: New manifest + registry line + Selector rule — zero changes to
existing agents or the orchestrator core, by the framework's own stated
test.

**138. What's the cheapest agent to run?**
S: Repository Understanding — `cost_class="cheap"`, reads an
already-materialized profile, no traversal.

**139. Do agents ever call each other directly?**
S: No — only via `RunContext` handoff or graph-mediated discovery.

**140. What's the Frontier Hypothesis Generator vs. a Frontier Agent?**
S: One is inside the Knowledge Engine (proposes graph facts); the other
narrates already-computed service results. Different layers, same name
by coincidence of vocabulary.

## M. Testing (141–150)

**141. What's your testing philosophy?**
S: Real Postgres/Neo4j in integration tests, no mocked DB; mock only the
external HTTP boundary.

**142. How many backend tests exist?**
S: 236 test files audited directly; RFC-001 alone added 68.

**143. How is non-determinism itself tested for?**
S: Directly — e.g. a regression test inserting the same two candidates in
both orders, asserting insertion-order (not hash-order) behavior.

**144. How is the materializer verified?**
S: A real replay test — delete Neo4j, rebuild from Postgres alone, diff
node/edge/property equality against the original.

**145. How is confidence-engine correctness proven, not just asserted?**
S: Parity testing against a pre-existing, trusted implementation's exact
labels.

**146. How is validator concurrency-safety tested?**
S: Under real concurrent scheduling, not just sequential try/except.

**147. What's a real bug your test suite caught before shipping?**
S: A `pg_advisory_xact_lock` early-commit bug in cross-repo memory
persistence — found and fixed pre-ship, verified by a targeted regression
test.

**148. How do you test graceful degradation?**
S: Explicit failure-path tests alongside every happy-path test for
LLM-touching components (LLM failure, malformed JSON, both proven to
degrade without raising).

**149. What's NOT tested that should worry a reviewer?**
S: The Frontier LLM generator's real-world precision/recall, and
end-to-end browser verification of Evidence Package/Understanding
rendering.

**150. How is the 24-repo suite different from unit/integration tests?**
S: Black-box, external, against real deployed APIs — never reimplements
GraphForge logic.

## N. Performance (151–160)

**151. What's your biggest measured performance win?**
S: Bounded neighborhood traversal replacing an unbounded full-graph read
— "previously O(every indexed repo); now O(1)" once a repo is known.

**152. How is LLM cost/latency bounded per run?**
S: Hard, measured caps (e.g. mid-loop synthesis capped at 1 call after
observing a real 92s→165s regression at a budget of 2).

**153. Is validator execution parallel?**
S: Yes, `asyncio.gather`, with provable determinism preserved.

**154. What's the cost of the parity comparator?**
S: Pure, in-memory, O(nodes+edges) — negligible next to the Neo4j
round-trip that produced the inputs.

**155. What's the biggest unaddressed performance risk?**
S: Full-clone-per-index — doesn't scale past a handful of repos per org.

**156. Do you cache LLM responses?**
S: Not independently verified in this audit — a metric field exists for
cache hits, but the mechanism wasn't traced.

**157. How does curation stay cheap?**
S: O(components) in Python over already-fetched data — negligible next
to the graph read.

**158. What's the Learning Engine's performance footprint?**
S: Zero on any hot path — no import path into indexing/generation/
validation/confidence at all.

**159. How would a slow LLM provider affect a run's total latency?**
S: It dominates — LLM-call timing is tracked separately from service-call
timing via `AgentMetrics`, visible per-run.

**160. What's the retrieval-breadth fix, concretely?**
S: A `repository_filter` scoping "scope"/"verify" actions to one repo
instead of surveying every indexed repository.

## O. Scalability (161–170)

**161. How does GraphForge scale to a 1000-repo org today?**
S: It doesn't yet — full-clone-per-index is the named, honest ceiling.

**162. What's the multi-tenancy model?**
S: `organization_id` scoping on every node/edge/Run — design intent
confirmed in writing, not independently re-verified per query path.

**163. Does the confidence formula scale with evidence volume?**
S: Yes — O(1) per new result, never O(history), by design.

**164. What's the Orchestrator's scaling risk?**
S: Named directly in the Risk Register as a Medium-impact concern,
mitigated by bounded hops and per-run concurrency caps.

**165. Is Engineering Memory's growth bounded?**
S: No, by design — unbounded growth for hypothesis/validation/
correction/confidence history; only evidence blobs are archivable.

**166. How would you scale the backend horizontally today?**
S: You wouldn't — fixed at 1 replica until background-execution
durability is redesigned.

**167. What's the plan for LLM cost scaling as agent count grows?**
S: `cost_class` + a budget-aware Selector, named as Phase-3 work, not
built yet.

**168. Does cross-repository reasoning scale with repo count per org?**
S: Bounded more by correctness gaps today than by measured throughput
limits.

**169. What's cursor-based pagination, and why isn't it built?**
S: Deferred until offset pagination's query cost is actually measured at
real org scale — not built preemptively.

**170. What breaks first at real scale?**
S: The indexer (no incremental re-indexing) and Impact Analysis's
same-repository traversal filter — both already-documented limits.

## P. Security (171–182)

**171. How are integration tokens stored?**
S: Encrypted at rest via Fernet (`app.core.crypto`).

**172. How is Cypher injection prevented?**
S: Fixed, internally-controlled label/type allowlists, never derived
from request input.

**173. Is there rate limiting?**
S: Not present in the codebase today — a named, honest gap; WAF at the
edge is the recommended, not-yet-implemented mitigation.

**174. How is password storage handled?**
S: bcrypt, defensively truncated to 72 bytes so a long password never
silently weakens the hash.

**175. What prevents an OAuth-state token from being reused as a session
token?**
S: `purpose`-scoped JWT claims — `get_current_user` explicitly rejects
any `purpose`-carrying token as a general bearer token.

**176. What's the production secrets safety net?**
S: `Settings._reject_insecure_defaults_in_production` — fails startup
loudly if a secret still holds its public dev default.

**177. Is there row-level multi-tenancy enforcement?**
S: Designed for at the GraphWriter/repository-query layer — design
intent, not independently re-verified end-to-end here.

**178. What's the single riskiest unencrypted secret category?**
S: None — AWS credentials for Bedrock are handled via IAM Task Role, the
one category that should never even touch Secrets Manager as a stored
value.

**179. How would a security reviewer verify "no direct graph write from a
generator"?**
S: Grep for `IGraphRepository` imports across `hypotheses/` and
`knowledge_engine/` — a concrete, falsifiable check.

**180. What's your stance on prompt injection from indexed repo content?**
S: Not addressed as a named threat model — an honest gap.

**181. How is RDS encryption at rest handled?**
S: Must be enabled at instance creation — cannot be retrofitted without a
restore cycle; a real operational constraint we know to flag.

**182. What's the TOKEN_ENCRYPTION_KEY rotation risk?**
S: Rotating it without a (currently nonexistent) re-encryption migration
makes every stored credential silently undecryptable — documented, not
hidden.

## Q. Reliability (183–192)

**183. What's the "never swallow an error" rule?**
S: An agent that can't reach a conclusion returns `failed`/`partial` with
a reason — never a plausible-looking default.

**184. What's your single most honest reliability gap?**
S: Background execution via in-process `asyncio.Task` doesn't survive a
process restart; recovery is deferred to the next startup, arbitrarily
delayed.

**185. Do you have real evidence this gap has happened?**
S: Yes — historical rows in our own dev database show exactly this
pattern (recovery delays from seconds to over 64 hours), tied directly
to `uvicorn --reload` during active development.

**186. How does one generator's failure affect others in the same run?**
S: Fully isolated — logged and swallowed, never blocks or corrupts
another's output.

**187. What's the retry policy for low-confidence results?**
S: One retry with an adjusted plan before accepting a low-confidence
result — generalized from the original Review agent to every agent.

**188. How does the system recover from a mid-run crash today?**
S: `recover_orphaned_runs()` sweeps any `queued`/`running` row to
`failed` at the *next* process startup — not at the moment of crash.

**189. What monitors this specific gap in production?**
S: A CloudWatch alarm on the `recovered_orphaned_runs` log line —
designed to make it a trackable metric, not silent.

**190. What's the blast radius of one validator having a bug?**
S: Bounded to hypotheses of the relationship types in that validator's
own `applies_to` — other validators unaffected.

**191. How does a degraded LLM narrative avoid corrupting a deterministic
fact?**
S: Structurally separate fields — narration never overwrites the
service-computed facts it was handed.

**192. What's the reliability posture of the materializer replay?**
S: Proven in testing, not yet operationally relied upon in production
(since it isn't the live write path).

## R. Deployment (193–202)

**193. Is this deployed to AWS right now?**
S: No — the blueprint is specified and code-verified, not deployed for
this hackathon.

**194. What's the first-deploy sequence?**
S: Networking → IAM → Secrets → RDS/Neo4j → ALB/Route53/ACM → ECS cluster
→ ECS services → migration → monitoring, in that dependency order.

**195. How are migrations run in production?**
S: A one-off ECS task running `alembic upgrade head`, before the backend
service's desired count goes above 0.

**196. What's the rollback mechanism?**
S: Automatic (ECS deployment circuit breaker) or manual (`update-service
--task-definition <previous-revision>`), safe because images are always
SHA-tagged, never `:latest`.

**197. Do database migrations auto-rollback?**
S: No — expand/contract pattern is the stated convention so an app
rollback never forces a schema rollback.

**198. What's the smoke-test gate after every deploy?**
S: Readiness endpoint, login, `/auth/me`, a real Planning-workflow run
reaching `completed` (the one check exercising real Bedrock + IAM), SPA
shell render.

**199. How do you know a deploy failed vs. is just slow?**
S: `aws ecs wait services-stable`, then hit `/health/ready` directly
through the ALB before DNS even finishes propagating.

**200. What's the local dev deployment story?**
S: Docker Compose, `uvicorn --reload` hot-reloading — explicitly dev-only,
not used in the production Dockerfile stage.

**201. How is the demo environment isolated from real GitHub?**
S: `docker-compose.demo.yml` + `VCS_PROVIDER=local_git`, PRs resolve to
local git branches — normal dev usage unaffected.

**202. What's the deployment checklist's single most-repeated caution?**
S: Never reuse a secret value that ever appeared in checked-in code —
generate every production secret fresh.

## S. Validation (203–212)

**203. What is the validation framework?**
S: A 24-repository, external, black-box regression suite against real
GraphForge APIs.

**204. What does it explicitly NOT do?**
S: Reimplement any GraphForge logic — no Cypher, no manual SQL, no
relationship-matching logic of its own.

**205. How many validations does it run?**
S: 10 — repository graph, cross-repo relationships, 3 live agent runs,
Engineering Memory, parity, frontier confidence distribution, performance
(informational), overall score.

**206. Why keyword-match narrative fields instead of exact text?**
S: LLM phrasing varies run to run for equally-correct output — exact-text
assertion would be flaky for reasons unrelated to correctness.

**207. What's the fixture-update discipline?**
S: Never "fix" a FAIL by editing the fixture repos — a FAIL means either
GraphForge changed or the fixtures are stale, nothing else.

**208. What are the four known gaps it found?**
S: Kafka literal-only detection, Feign suffix-only name matching,
same-repository-only Impact Analysis, intra-repository-only Dependency
Query counts.

**209. Is it wired into CI?**
S: Designed to be (exit code gates on it) — not confirmed as an active CI
step in this audit.

**210. What powers Validation 7 (Parity)?**
S: The same `compare_graphs` comparator behind the live Graph Parity
dashboard.

**211. What's the most valuable output of building this suite?**
S: The four documented gaps themselves — found by testing against a
realistic, intentionally polyglot fixture set, not invented.

**212. How would you recapture a fixture after a legitimate change?**
S: Re-index, pull fresh ground truth from the same real APIs the suite
itself uses, update the YAML, re-run to confirm PASS.

## T. Demo (213–222)

**213. Is the demo using real repositories?**
S: Yes — four real, hand-written, multi-commit Spring Boot repos with
real git history, same pipeline as production.

**214. What scenario will you show?**
S: A breaking Kafka schema change (field rename + type change) in
`order-service`, risk=HIGH, cross-service impact.

**215. Why does the impacted set include both Kafka topics, not just the
one that changed?**
S: Impact analysis is file-level, not field/topic-level — the producer
file touches both topics, so both are correctly reported. Not a bug.

**216. What if the demo fails live?**
S: Pre-loaded fallback (second tab / recording), one sentence, no
apology, immediate switch — rehearsed, not improvised.

**217. How do you know the deterministic risk rating didn't need AI?**
S: Zero LLM calls in that computation — a pure graph rule (changed file
already produces to a Kafka topic).

**218. What happens if the AI tab is slow/empty during the demo?**
S: Pivot to the Architecture/Graph view — the deterministic layer has
zero external dependency and is unaffected.

**219. Can you demo it against our repo right now?**
S: Answer honestly based on real constraints — don't promise an
unrehearsed live index of an unknown repo during Q&A.

**220. What would a judge see if they opened Run History?**
S: A real, timestamped list of every agent run this session, including
confidence scores and evidence links.

**221. How do you prove the validation suite's claims live?**
S: Show the guide directly or a pre-generated `reports/latest.html` —
don't regenerate it live (real time + real LLM cost).

**222. What's the closing line of the demo?**
S: "The graph is the product — every agent you saw is a feature of it, not
a new silo."

## U. Business (223–232)

**223. What's the market size?**
S: Not modeled — honest gap, hackathon scope.

**224. Who would pay for this?**
S: Engineering orgs with real cross-repository/cross-team knowledge loss
pain — inferred from the persona set, not from customer interviews.

**225. What's the ROI pitch?**
S: Time-to-context reduction, translatable to engineer-hours saved per
incident/PR — a real, defensible framing, not a validated number.

**226. How does this reduce incident response time?**
S: A persistent graph answers "what does this touch, who owns it" without
reconstructing it from four tools during an active incident.

**227. What's the total cost of ownership story?**
S: Not modeled with real infra cost numbers — architecturally
cost-conscious choices exist (no speculative Redis/S3, Fargate not EC2),
but no dashboard.

**228. How does this integrate into an existing engineering org's
workflow?**
S: Starts where engineers already are (a PR, a repo, a ticket) — not a
new destination tool to adopt separately.

**229. What's the retention story — why would a team keep using this
after month one?**
S: The graph compounds — the tenth question answered benefits from the
first nine; a static tool doesn't build this.

**230. Would this replace any headcount?**
S: Not the framing — it reduces re-derivation time for senior engineers,
not a replacement claim we'd make.

**231. What's the biggest business risk?**
S: LLM cost scaling faster than measured value, if agent count grows
without the planned budget-aware Selector — named directly in our own
Risk Register.

**232. How would you price this?**
S: Not defined — don't invent a number under pressure.

## V. Innovation (233–242)

**233. What's actually novel here vs. table-stakes "AI code review"?**
S: Structural evidence-gating — an LLM claim can't become trusted
knowledge without independent, deterministic corroboration, enforced by
interface design, not convention.

**234. What's your most creative architectural solution?**
S: The Postgres-as-truth / Neo4j-as-derived-projection inversion, proven
by a real replay test rather than asserted.

**235. What's novel about your confidence model?**
S: Six states with monotonic, incremental aggregation from independent
sources — not a bare float threshold.

**236. What's novel about your validator design?**
S: One reusable class (`EvidenceKeywordValidator`) recognizing new
technology via keyword-table entries, never a new class per language.

**237. Is any of this patentable/defensible IP?**
S: Not evaluated — an honest non-answer, not a claim either way.

**238. What would you show a technical co-founder to convince them this
is real engineering, not a demo?**
S: The RFC-01 contract amendment story — the team found their own
shipped contract was uncomputable, fixed it, and documented the rejected
alternatives, mid-implementation.

**239. What's the most "hackathon-unusual" thing about this codebase?**
S: The volume and rigor of self-documented trade-offs — ADRs stating
"what this deliberately does not do" as a first-class section.

**240. What idea did you deliberately NOT build, and why?**
S: `RuntimeValidator`/`OwnershipValidator`/`ApiContractValidator` —
building a validator with nothing to validate against yet would be
speculative infrastructure.

**241. How is your approach different from a typical hackathon AI demo?**
S: The deterministic core existed before any LLM call was added — AI is
layered onto proven infrastructure, not the whole product.

**242. What's your "unfair advantage" as a team?**
S: Inherited, production-tested deterministic infrastructure
(ChangeGuard) to build the AI layer on top of, rather than starting from
zero.

## W. Creativity (243–250)

**243. What's the most creative UX decision?**
S: Rendering long-running agent runs as visible reasoning steps
appearing incrementally, not a blank spinner — "watch it reason."

**244. What's the most creative testing technique you used?**
S: Parity-testing a new confidence formula against pre-existing,
hand-assigned labels to prove equivalence, not just correctness in
isolation.

**245. What's the most creative failure-handling pattern?**
S: Shadow-mode delivery — every new pipeline stage runs alongside
production, provably not affecting it, before any cutover.

**246. How did you creatively solve the "trust an LLM without trusting
it" problem?**
S: Treating the LLM as one more `HypothesisGenerator`, subject to the
exact same validator gate as a deterministic parser — no special-cased
trust path.

**247. What's a creative constraint you imposed on yourselves?**
S: A validator may never call an LLM, even to "double check" — a rule
that forecloses an entire class of easier-but-weaker designs.

**248. What's a creative naming/versioning trick in this codebase?**
S: Content-addressed hypothesis IDs (`hash(generator, type, source,
target, evidence)`) — re-running the same generator against the same
pack is naturally idempotent.

**249. What's a creative way you reused existing code instead of
rebuilding?**
S: `ServiceExecutor` was built as a separate superset dispatcher instead
of reopening a frozen `OrganizationKnowledgeService` contract — respecting
a boundary by building alongside it.

**250. What's the most creative part of your demo?**
S: Showing the deterministic risk badge BEFORE the AI tab — proving the
non-AI layer is real and independently trustworthy.

## X. Problem Solving (251–262)

Scenario answers in depth: `08_PROBLEM_SOLVING_SCENARIOS.md`.

**251. Neo4j crashes — what do you do?**
S: Nothing is lost (Postgres is the source of truth); restore/restart
Neo4j, re-run indexing or the materializer.

**252. Bedrock is unavailable — what do you do?**
S: Check IAM policy vs. configured model first, then credential expiry;
every agent already degrades gracefully rather than blocking.

**253. The LLM hallucinates a relationship — what do you do?**
S: Check its confidence state (should be CANDIDATE); if wrongly
confirmed, a human correction overrides it via the Learning Engine,
recorded as a new transition.

**254. Validation disagrees with the AI's claim — what do you do?**
S: Nothing to "fix" — this is the system correctly refusing to over-trust
an unconfirmed claim; the state machine has `CONFLICTING` exactly for
this.

**255. Two repos produce contradicting relationship claims — what do you
do?**
S: Reliability-tier-weighted aggregation; a genuine standoff lands at
`CONFLICTING`, surfaced, not silently resolved.

**256. A repo has stale documentation — what do you do?**
S: The Documentation Review Agent flags it; proposes updates, never
applies them automatically — human-in-the-loop by design.

**257. Cross-repo relationships silently fail to appear — what do you
do?**
S: Check against the two documented gaps (Feign naming, Kafka literal-
matching) before assuming a new bug.

**258. A run gets stuck and never completes — what do you do?**
S: Check its actual DB row status directly (queued/running/failed) rather
than trusting the UI alone; this maps to a known, documented durability
gap if it's a process-restart timing issue.

**259. A judge finds a bug live during your demo — what do you do?**
S: Acknowledge it precisely, cite it if it matches a known/documented gap,
otherwise say "we'll verify that, not guessing live" — never improvise an
explanation.

**260. How would you debug a "confidence never rises above CANDIDATE"
report?**
S: Check whether any validator's `applies_to` even covers that
relationship type — a real, correct outcome if no validator has a
keyword table for that vocabulary yet.

**261. Someone asks you to add caching under time pressure — how do you
decide?**
S: Check whether there's an existing cache layer to extend first (there
isn't) — don't introduce new infrastructure for a problem not yet
measured.

**262. How do you decide whether a gap is worth fixing before demo day?**
S: Weigh product-visible impact (cross-repo matching) against
operational risk (background-execution durability) — pick one, justify
it, don't hand-wave both.

## Y. Leadership (263–272)

**263. How did you divide work across a 5-person team?**
S: By architectural layer, matching the actual codebase boundaries
(Product/UX, Architecture, AI, Engineering Excellence, Demo) — not by
arbitrary feature slicing.

**264. How do you make a decision when two team members disagree on
approach?**
S: Model it on the codebase's own discipline — write down the options
and the rejected alternative's reasoning, don't just pick and move on
silently.

**265. What's your process for reviewing a teammate's claim before
presenting it?**
S: Trace it to a real file/ADR/test before repeating it on stage — the
exact discipline this document itself follows.

**266. How do you handle a teammate being unavailable during Q&A?**
S: Backup ownership is pre-assigned per topic (`01_TEAM_RESPONSIBILITIES.md`)
— never guess outside your lane if the owner is present elsewhere.

**267. How do you prioritize what to fix vs. present as a known gap?**
S: Fix what's cheap and demo-visible; document everything else honestly
rather than attempting a risky last-minute patch.

**268. What's your escalation path if the demo breaks mid-presentation?**
S: Pre-agreed trigger and fallback script (`00_PRESENTATION_FLOW.md`) —
decided before presenting, not improvised under pressure.

**269. How do you keep 300+ pages of prep material from being
overwhelming?**
S: Cheat sheets — one page per presenter, everything else is reference
material, not memorization material.

**270. How did you decide which gaps to disclose proactively?**
S: All of them — a team that names its own gaps first controls the
narrative; a team caught hiding one loses more credibility than the gap
itself costs.

**271. What would you do differently with one more week as a team?**
S: Close the Feign/Kafka cross-repo matching gaps — highest
product-visible leverage, already root-caused, not vague uncertainty.

**272. How do you keep the whole team aligned on one honest narrative?**
S: One shared reality-check document (`12_REALITY_CHECK_PRESENTATION.md`)
everyone reads before presenting — no presenter improvises their own
version of "what's done."

## Z. Decision Making (273–284)

**273. Why deterministic-first over LLM-first?**
S: An LLM-only system has no mechanism to distinguish a confirmed fact
from a plausible guess — deterministic-first is the answer to "how do we
trust this," not a stylistic preference.

**274. Why did you choose Postgres over a pure graph-database-only
design?**
S: Cardinality and durability requirements diverge sharply between "what
we observed" (blob-appropriate) and "what we currently believe"
(relational) — one store forced to serve both compromises one side.

**275. Why not build a real task queue before the hackathon deadline?**
S: A conscious sequencing decision — the durable-queue redesign is an
application-architecture change first, infrastructure second; not worth
building infra for application code that doesn't use it yet.

**276. Why gate the LLM generator off by default?**
S: Unmeasured cost and precision — the responsible default is opt-in
until real evaluation exists, not "ship it on and see what happens."

**277. Why choose shadow-mode delivery for every new pipeline stage?**
S: Prove correctness before betting production writes on it — a real,
repeated engineering discipline across every RFC, not a one-off choice.

**278. Why Fargate over EKS, given Kubernetes is more "impressive"?**
S: Two services doesn't earn Kubernetes' complexity budget — a
deliberately unglamorous, correct decision, not a knowledge gap.

**279. Why did you decide NOT to build calibration tracking yet?**
S: The prerequisite feedback data didn't exist until the Learning Engine
shipped — sequencing, not neglect.

**280. Why accept unbounded row growth in Engineering Memory?**
S: The audit trail is the actual product capability — compaction is a
future optimization decision, deferred deliberately, not an oversight.

**281. Why use a registry pattern instead of a dispatch function for
validators/generators?**
S: A growing `if/elif` chain becomes an unreviewable merge-conflict
magnet; a registry is data, not control flow — proven safe by parity
tests on every addition.

**282. Why did you correct `ARCHITECTURE.md`'s "new component" framing of
the Orchestrator during this audit instead of leaving it?**
S: Because presenting stale documentation as current state would be
exactly the kind of overclaiming this whole prep material set exists to
prevent.

**283. How do you decide what NOT to say when time is short?**
S: Lead with the highest-likelihood judge question first (architecture,
AI trust mechanism, known gaps) — cut business/innovation framing before
cutting evidence-backed technical claims.

**284. What's the single hardest trade-off decision in this codebase, and
why?**
S: The RFC-01 contract amendments — admitting a already-shipped, frozen
contract was computationally wrong, fixing it immediately mid-
implementation, and documenting the rejected alternatives rather than
quietly patching around it.

## AA. Cross-cutting wildcard (285–312)

**285. If I gave you a random repo right now, what would GraphForge tell
me about it in 30 seconds?**
S: Its exposed APIs, owned databases, messaging usage, and most important
dependencies — via the Repository Understanding Agent, computed entirely
by a deterministic service, narrated by a cheap LLM call.

**286. What's one thing you're proud of that no one asked about yet?**
S: Pick genuinely — e.g. "the validation suite documents its own
precision gaps with root causes, not vague caveats."

**287. What's the weirdest bug you found while preparing this?**
S: A real one, if asked — e.g. the `_primary_repository` Python
set-iteration-order non-determinism bug, caught by a same-process,
insertion-order-vs-hash-order regression test.

**288. What's the difference between a Hypothesis in the Knowledge Engine
and a Hypothesis in Engineering Session?**
S: Different unit of work entirely — one is about a graph relationship in
code; the other is about "why does this behavior exist," scoped to one
engineer's reasoning session.

**289. Who commits a Decision in an Engineering Session — can an agent?**
S: Never — enforced at both the service layer (`ForbiddenError`) and the
API schema layer (no `agent_role` field exists on the commit request at
all).

**290. What's the propose/commit boundary, and why does it matter?**
S: Only a human can commit a Decision — a structural, not just
policy-level, authorization boundary.

**291. How would a new engineer figure out what's real vs. proposed in
this codebase?**
S: Check each document's own stated Status field (Accepted/Implemented/
Proposed) with a dated note — this codebase is unusually disciplined
about saying this explicitly.

**292. What's your favorite ADR, and why?**
S: Pick genuinely and be ready to summarize it — e.g. ADR 0018 for the
scope, or ADR 0014 for its self-review section naming its own residual
limitations.

**293. What would break if you removed the deterministic Java parser?**
S: The calibration reference for every other generator's precision
measurement disappears — it's permanently retained by explicit
invariant, not deprecated even as the platform matures.

**294. What's the smallest possible unit of "new knowledge" in this
system?**
S: One `EvidenceItem` — content-addressed, immutable, the atomic input to
everything downstream.

**295. If your demo repo had 1000 PRs, would anything behave
differently?**
S: Not for a single PR's analysis — but full re-indexing cost and
Postgres row growth would both scale linearly with history, a real,
named limitation.

**296. What's the difference between `app.knowledge` and
`app.knowledge_engine`?**
S: External-source connection registry vs. the Evidence→Knowledge
pipeline — named differently on purpose to prevent exactly this
confusion.

**297. How do you personally verify a claim before repeating it on
stage?**
S: Trace it to the actual file/test/ADR — this is the standard this
entire document set was held to.

**298. What's the single most quotable line from your own
documentation?**
S: "The graph is the product. The agents are features of the graph."

**299. If a judge says "this sounds like just RAG with extra steps," how
do you respond?**
S: The "extra steps" are the entire point — RAG optimizes retrieval; we
optimize for evidence-gated trust before a claim is ever surfaced as
fact, structurally, not by prompt instruction.

**300. What's the one thing you want a judge to remember after this
presentation?**
S: Every claim this system makes can be traced to real evidence — and
every gap in it can be traced to a real, named, already-diagnosed reason.

**301. How is a Recommendation different from a Decision in Engineering
Session?**
S: A Recommendation is proposed by anyone (human or agent) and can be
accepted/declined; a Decision requires a human commit — the
propose/commit boundary applies specifically at the Decision layer.

**302. What happens when two Recommendations compete for the same
Belief?**
S: Auto-detected and turned into a `Contradiction` — no separate conflict
mechanism was invented for this case.

**303. How would you explain "Evidence Pack" to a non-engineer judge?**
S: "A sealed, timestamped snapshot of everything we observed about a
repository at one commit — nothing after that point can quietly change
what conclusions were drawn from it."

**304. What's the difference between a "candidate" and a "verified"
relationship, in plain language?**
S: "Candidate" means one source suggested it and nothing's confirmed or
denied it yet; "verified" means at least two independent, high-trust
signals agree.

**305. Why does your system say "not measured" instead of guessing, and
why does that matter for a hackathon judge?**
S: Because the entire product's credibility rests on evidence-over-
assertion — guessing on stage would contradict the system's own design
principle in front of the people evaluating it.

**306. What's the relationship between ADRs, RFCs, and actual code in
your process?**
S: ADRs record intent and rationale; RFCs (embedded in ADR 0018) record
implementation status per phase with real test evidence; code is the
final source of truth when they disagree — and this audit found and
flagged one real instance of docs drifting ahead of/behind code.

**307. How do you handle a judge who keeps pushing past your prepared
depth?**
S: Go one layer deeper honestly — cite the exact file or test by name; if
truly past what's documented, say "that's genuinely past what we've
built/measured" rather than fabricating depth.

**308. What's the most important thing this document set does NOT cover?**
S: Real user validation and real production operation — both explicitly
out of scope for a hackathon build, and both honestly named as such.

**309. If you had to cut one presenter's section for time, which and
why?**
S: A real team decision, not scripted here — but the architecture and AI
trust-mechanism sections are the least cuttable, since they're what
differentiates this from a generic AI demo.

**310. What single sentence would you want on a judge's scorecard?**
S: "This team can tell me exactly what's real, what's proven, and what's
next — without me having to catch them at it."

**311. How does this whole document set itself demonstrate engineering
excellence?**
S: It's grounded the same way the product is — every claim traces to a
real file, ADR, RFC, or test, and gaps are stated explicitly rather than
smoothed over. That consistency is itself the pitch.

**312. Final question: why should this team win?**
S: Not because nothing is unfinished — because everything unfinished is
named, understood, and root-caused, which is the actual signal of a team
that will keep building this correctly after the hackathon ends.
