# 10 — Agent Defense

## Why "Agent"?

Not marketing language — a specific contract. Every agent is a subclass
implementing three parts at minimum: an `AgentManifest` (id, purpose,
accepted subject types, goals, cost class, max graph hops), a run loop
producing a typed `AgentOutput` with confidence + evidence, and (for most)
its own tool selection. `AGENT_FRAMEWORK.md` generalizes this from the
original Change Investigation Agent's real
Plan→Select→Execute→Observe→Decide loop — the term describes an actual
execution shape, not a single LLM call with a system prompt.

## Why "Frontier Agent" specifically?

Two distinct things share this name, and the distinction matters if
pressed:
1. **Frontier Hypothesis Generator** — an LLM `HypothesisGenerator` inside
   the Knowledge Engine, proposing capability relationships.
2. **Frontier Agents** (`app.agents.frontier.BaseFrontierAgent`) — the
   shared run loop for read-only Engineering Intelligence Agents
   (Repository Understanding, Impact Analysis, Dependency Query, etc.).
   These consume already-computed service results and narrate them — they
   do not generate hypotheses or write to the graph.
"Frontier" names "the newest layer of agents built on top of the
Engineering Intelligence Service Layer," not a marketing synonym for
"advanced."

## Qualities of our agents

- **Almost no business logic of their own.** Every fact-producing
  decision happens in a lower layer (Knowledge Engine or Engineering
  Intelligence Service Layer) that's independently testable without a
  live LLM. An agent decides which service calls to make, what to ask the
  LLM to narrate, and how to shape the response — nothing more.
- **Three pure hooks per Frontier agent**: `build_service_requests`,
  `build_prompt` (may return `None` — a legitimate "skip the LLM
  entirely" branch), `render_response`. Everything with I/O (session
  handling, timing, error handling, `AgentOutput` assembly) lives once,
  in the shared base class.
- **Manifest-declared cost class and dependencies** — `cost_class`
  (cheap/standard/expensive) and `required_dependencies` (LLM/Neo4j) are
  explicit, checked by ADR 0011 preflight validation before any LLM call
  happens.

## How are they different from each other?

By **manifest**, not by hand-written dispatch code — `accepted_subject_types`,
`goals`, `max_graph_hops`, and `output_schema_name` fully describe an
agent's scope. Some are Workflow-pipeline stages (Planning, Testing,
Documentation Planning); others are standalone AI Workspace capabilities
reachable directly via `POST /agent-runs` (Repository Understanding,
Impact Analysis, Dependency Query, Documentation Review, API
Intelligence) — each manifest's own docstring states which category it
is, and standalone agents are deliberately absent from the Workflow
pipeline's stage-goal table.

## How do they collaborate?

Two mechanisms, matched to coupling tightness (`AGENT_FRAMEWORK.md`):
1. **Sequential handoff** — agent N's output is written to `RunContext`
   and passed verbatim to agent N+1 in the same run (e.g. Development/
   Testing reading Planning's result). Used only when the downstream
   agent genuinely can't proceed without this run's fresh output.
2. **Graph-mediated** — agent B's tools traverse the graph and find agent
   A's fact like any other graph data, no shared run, no direct
   dependency. This is the **default** collaboration mode — loose,
   asynchronous, durable.

## How are they deterministic?

They aren't, individually — the LLM call inside an agent is inherently
non-deterministic. What's deterministic is everything the agent's output
is *grounded in*: the Engineering Intelligence Services it calls (zero
LLM, zero NL parsing, deterministic sort order so two calls against the
same data return byte-identical results) and the Knowledge Engine facts
those services read. The agent's job is bounded to narration and
orchestration of already-deterministic facts — that boundary is the
actual determinism guarantee, not the LLM call itself.

## How do they avoid hallucinations?

Structurally, not by prompt instruction alone — see
`04_AI_DEFENSE.md`'s six-layer answer. The agent-specific piece: a
Frontier agent's `render_response` only ever narrates the
`ExecutionResult` it was handed; it cannot originate a fact the service
layer didn't already compute, because the shared base class's `run()`
loop controls exactly what gets passed to that hook.

## How are failures handled?

`RunCoordinator` never swallows an error — persists `status="failed"`
with the real error message for both agent-body exceptions and
pre-flight dependency failures (checked before the LLM/graph is ever
touched). A `PromptBuilder` failure specifically (bad LLM response)
degrades to an empty narrative plus a `status="failed"` Evidence entry
rather than raising, so an agent's *deterministic* facts never depend on
the model succeeding. One agent's failure is fully isolated — never
corrupts another agent's run or the graph.

## How is confidence calculated?

Two different "confidence" concepts, don't conflate them if asked:
1. **Knowledge Engine confidence** (`ConfidenceState`, six-tier,
   monotonic, computed by `DefaultConfidenceEngine` from independent
   validator confirmations) — this is about trusting a *graph fact*.
2. **Agent output confidence** (`AgentOutput.confidence`, a
   `Confidence{score, reasoning}` pair) — for Frontier agents, computed
   generically by `result_mapper` as "how many service calls succeeded,"
   deliberately minimal and domain-agnostic. Named directly as a
   deliberate ceiling: "a future agent that wants a smarter confidence
   signal computes it in its own `render_response`" — the shared mapper
   doesn't guess at semantics it can't know.

## Expected agent-design questions with answers

**Q: Could an agent call another agent directly?**
A: Not directly — collaboration goes through `RunContext` (same-run
sequential handoff) or the graph (asynchronous, graph-mediated). No
agent-to-agent RPC exists; this keeps every collaboration auditable
through one of two well-understood paths.

**Q: What happens if two agents run concurrently against the same
subject?**
A: Each produces its own independent `Run`/`AgentStep` — no shared
mutable state between them beyond the Postgres/Neo4j data both read.
Graph-mediated collaboration is designed for exactly this: no
coordination required, no race condition to reason about, because
neither agent writes to the graph directly (Knowledge Engine's validator
gate mediates any actual graph write).

**Q: How would you add a new agent without breaking existing ones?**
A: New manifest + registry line + Selector rule — the framework's own
stated test of the plugin-architecture claim: "if a new agent requires
touching another agent or the orchestrator core, the framework has a
leak that must be fixed before the next agent ships."
