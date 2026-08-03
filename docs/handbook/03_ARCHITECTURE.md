# Section 3 — Complete Architecture

A note on sourcing before the subsystem list: `docs/graphforge/ARCHITECTURE.md`
is written as a **proposal document** ("Today: ... GraphForge evolution: ...").
Reading only that file risks understating what actually exists — `app/orchestrator/`
(`registry.py`, `selector.py`, `run_coordinator.py`, `preflight.py`,
`background_execution.py`) and every agent's `manifest.py` are real,
implemented code, considerably further along than that document's "new
component" framing suggests. Where this section says "implemented," it
means verified against actual files under `backend/app/`, not the proposal
document alone.

## Subsystem: Indexer (`app/indexer/`)

- **Purpose**: deterministically discover a repository's architecture and
  produce a graph.
- **Responsibilities**: clone (`app.indexer.services`), detect language
  (`parsers/registry.py`), parse into an `ArchitectureModel`
  (`SpringBootJavaParser`, `PythonParser`), extract evidence
  (`app/indexer/evidence/`), generate hypotheses (`app/indexer/hypotheses/`),
  build a `GraphPayload` (`graph/builder.py`).
- **Inputs**: a git URL/clone.
- **Outputs**: `ArchitectureModel`, `EngineeringEvidencePack`, `GraphPayload`.
- **Dependencies**: `tree-sitter` + `tree-sitter-java`/`tree-sitter-python`,
  `app.graph.IGraphRepository`.
- **Trade-offs**: full-clone-per-index, not incremental — `ROADMAP.md`
  Technical Debt names this as not scaling "past a handful of repos per
  org," a Phase-2 prerequisite, not yet resolved.
- **Failure modes**: unsupported language/framework → `422
  unsupported_repository`; a `HypothesisGenerator`'s own failure is logged
  and swallowed, never blocking another generator (ADR 0018 invariant 7).
- **Why designed this way**: ADR 0007 — tree-sitter over JavaParser because
  the backend is Python and JavaParser has no usable Python bindings.
- **Alternatives considered**: shelling out to a JVM subprocess (rejected —
  slow, extra runtime dependency, larger failure surface).
- **Future evolution**: RFC-07 (first language with no dedicated parser,
  promoted through the generic Knowledge Engine path instead).

## Subsystem: Knowledge Engine (`app/knowledge_engine/`)

Detailed in [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md). One-line
summary here: the five-stage pipeline (Evidence → Hypothesis → Validation
→ Confidence → Knowledge) and its supporting subpackages
(`materializer.py`, `memory_service.py`, `confidence/`, `validators/`,
`parity/`, `explainability.py`, `evidence_curation.py`).

## Subsystem: Engineering Memory (`app/repositories/engineering_memory_repository.py`, `memory_service.py`)

Detailed in [04_ENGINEERING_MEMORY.md](04_ENGINEERING_MEMORY.md). The
append-only Postgres store everything else in the Knowledge Engine reads
from and writes to.

## Subsystem: Frontier Agents (`app/agents/frontier/`)

Detailed in [06_FRONTIER_AI.md](06_FRONTIER_AI.md) and
[08_AGENTS.md](08_AGENTS.md). `BaseFrontierAgent` — the shared run loop for
every Engineering Intelligence Agent (Repository Understanding, Dependency
Query, Impact Analysis, and siblings). Three hooks per subclass
(`build_service_requests`, `build_prompt`, `render_response`); everything
else (pulling context, calling services, calling the LLM, timing,
assembling `AgentOutput`) lives once in the base class.

## Subsystem: Engineering Intelligence Service Layer (`app/services/engineering_intelligence/`)

Detailed in [07_ENGINEERING_INTELLIGENCE.md](07_ENGINEERING_INTELLIGENCE.md).
Plain, agent-agnostic dataclasses (`contracts.py`) — deliberately free of
`AgentOutput`/`Subject`/`Evidence` types, so this layer stays usable
outside any one agent's prompt/UI concerns.

## Subsystem: Orchestrator (`app/orchestrator/`)

- **Purpose**: agent registration, selection, and run lifecycle.
- **Responsibilities**: `registry.py` — `AgentManifest` registry, one file
  per agent read to understand what it does without reading its
  implementation. `selector.py` — Goal → agent-list resolution.
  `run_coordinator.py` — dispatches agent `run()`, persists `Run`/
  `AgentStep`, injects `graph_repository`/`db` into `AgentContext.extras`
  when a manifest's `max_graph_hops > 0`, and — per `BaseFrontierAgent`'s
  own docstring — "never swallows exceptions: errors are persisted as a
  failed `Run`/`AgentStep`." `preflight.py` — dependency gating
  (`DEPENDENCY_LLM`, `DEPENDENCY_NEO4J`) so an agent whose required
  dependency isn't configured fails fast and explicitly (ADR 0011).
  `background_execution.py` — async dispatch.
- **Why designed this way**: `AGENT_FRAMEWORK.md`'s explicit test of the
  plugin-architecture claim — adding agent #N should touch only that
  agent's own module plus one registry line and one Selector rule, never
  the Orchestrator's core logic. Every current agent manifest (12+ under
  `app/agents/*/manifest.py`) follows this shape.
- **Gap vs. the proposal doc**: `ARCHITECTURE.md`'s Shared Memory
  (`RunContext`, meant to be Redis-backed) is explicitly documented there
  as implemented only as an in-memory, single-process stand-in — "a
  deliberate, temporary substitution... required before any multi-process/
  multi-replica deployment."

## Subsystem: Context Discovery (`app/context_pipeline/`)

- **Purpose**: turn a free-text engineering request into grounded context
  for Planning — implemented, not the proposal-stage "Context Builder"
  described in `ARCHITECTURE.md`. See ADRs 0007 (predecessor: deterministic
  investigation loop for Review), 0010, 0013–0017.
- **Responsibilities**: `reasoning/engine.py` runs a deterministic
  Plan→Select→Execute→Observe→Decide investigation loop
  (`reasoning/investigators.py`, `reasoning/ledger.py` — the only place a
  `Fact` can be written); `reasoning/curation.py` tiers and confidence-
  scores evidence (ADR 0014); `reasoning/understanding.py` runs one
  strictly-grounded LLM synthesis call producing `EngineeringUnderstanding`
  (ADR 0015), with an optional, budget-capped mid-loop checkpoint (ADR
  0016, `MAX_MID_LOOP_SYNTHESIS_CALLS = 1`) letting a live hypothesis
  redirect the rest of the run without making selection itself
  non-deterministic — `capability_priority()` only re-weights within an
  already-decided necessity tier.
- **Note on naming**: `app/context/` (Entry Resolvers) is the separate,
  much thinner proposal-stage package from `ARCHITECTURE.md` — as of this
  audit it contains only a `freetext` resolver, not the `GitHubEntryResolver`/
  `JiraEntryResolver` set the architecture doc describes as Phase 1/2 work.
  Do not conflate it with `app.context_pipeline`, which is the real,
  heavily-ADR'd, implemented reasoning system.

## Subsystem: Engineering Session (`app/models/engineering_session.py` et al.)

- **Purpose**: RFC-001's aggregate for structured collaborative reasoning
  between humans and agents — `EngineeringSession`, `Timeline`,
  `WorkingUnderstanding`, `Belief`, `Hypothesis`, `Evidence`,
  `Recommendation`, `Decision`, `Contradiction`.
- **Why separate from the Knowledge Engine**: different unit of work — a
  Knowledge Engine `Hypothesis` is about a graph relationship in one
  repository's code; an Engineering Session `Hypothesis` is about "why does
  the current behavior exist," scoped to one engineer's line of reasoning,
  resolving into exactly one `Belief` or being rejected.
- **Trade-off named explicitly in the RFC**: no promotion (copy-with-
  provenance) from a Session `Belief` into any org-wide "System Model" —
  that's a distinct future aggregate (Phase 3), not built.
- Full detail: RFC-001, summarized accurately in this handbook's system
  prompt context and not re-derived here to avoid drift from the RFC text
  itself — read `docs/rfcs/RFC-001.md` directly for the schema/API/test
  breakdown.

## Subsystem: Learning Engine (`app/learning_engine/`)

Detailed in [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md) § Learning.
Sibling package to `app.knowledge_engine`, never imported by it — one-way
dependency, by design (RFC-06D).

## Subsystem: Validation Framework (`graphforge-validation/`)

Detailed in [09_VALIDATION_FRAMEWORK.md](09_VALIDATION_FRAMEWORK.md). Not
part of the backend deployable — a separate, external black-box test
harness that only calls GraphForge's real REST API and
`EngineeringMemoryService` in-process.

## Cross-cutting: what every subsystem shares

- **Structured logging** (`loguru`) with mandatory `run_id`/`agent_id`/
  `subject_id` fields on agent-related log lines (`AGENT_FRAMEWORK.md`).
- **Never-swallow-errors discipline**, applied consistently: `RunCoordinator`
  persists failures rather than hiding them; `GraphWriter`'s proposed schema
  validation "rejects... rather than coercing"; a `HypothesisGenerator`'s
  failure is "logged and swallowed" *for that generator only*, never
  silently absorbed into a false success for the run as a whole.
- **Additive-only migrations**: every RFC in ADR 0018's roadmap states its
  own migration as purely additive, verified upgrade→downgrade→upgrade
  against real Postgres, never touching a pre-existing table.

## Diagram: end-to-end request flow (Repository Understanding Agent example)

```
Client → POST /agent-runs {goal: analyze_repository_understanding}
       → Orchestrator.registry resolves manifest → preflight checks
         DEPENDENCY_LLM + DEPENDENCY_NEO4J are configured
       → RunCoordinator injects graph_repository into AgentContext.extras
         (manifest.max_graph_hops=1 > 0)
       → BaseFrontierAgent.run()
            → build_service_requests()  → [ServiceCall(repository_profile)]
            → ServiceExecutor.execute() → RepositoryProfileService.get_profile()
                 → IGraphRepository.get_full_graph()  (reads materialized Neo4j)
            → build_prompt(execution)   → PromptSpec | None
            → LLM call (if PromptSpec present) → narrative text
            → render_response()         → RepositoryUnderstandingReport
       → AgentOutput persisted as Run/AgentStep (Postgres)
       → Client reads Run result
```

Every fact in the `RepositoryUnderstandingReport` traces back through this
chain to a `RepositoryProfile`, which traces back to Neo4j, which — per
ADR 0018 — is itself a rebuildable projection of the append-only
`EngineeringMemory` log. Nothing in this path lets the LLM originate a
fact; it narrates facts the deterministic service layer already computed.
