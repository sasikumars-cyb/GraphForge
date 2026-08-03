# Section 8 — Agents

Source: `app/agents/_framework` concepts (`AGENT_FRAMEWORK.md`),
`app/agents/frontier/*`, `app/orchestrator/*`, actual `manifest.py` files
across `app/agents/*`.

## Repository Understanding, Dependency Query, Impact Analysis — one shared base

All three (and their siblings — Architecture Insights via
`OrganizationKnowledgeService`, Documentation Review, API Intelligence,
Engineering Review, Documentation Planning, Testing, Planning) extend
`BaseFrontierAgent` (`app/agents/frontier/base_frontier_agent.py`). Each
subclass implements exactly three pure hooks:

```python
build_service_requests(context) -> list[ServiceCall]   # no I/O
build_prompt(context, execution) -> PromptSpec | None    # no I/O; None = skip the LLM entirely
render_response(context, execution, narrative) -> dict   # no I/O
```

Everything else — pulling `db`/`graph_repository` from `context.extras`,
calling services, calling the LLM, timing, assembling the final
`AgentOutput` — lives once, in the base class's `run()` method. This is
the literal, verified truth behind "agents contain almost no business
logic": the base class's own docstring states the framework "implements
`IAgent`... `RunCoordinator` calls `run()` exactly as it calls every other
agent — no orchestrator-side change of any kind," and exceptions from any
hook propagate uncaught (`RunCoordinator.execute_run` already persists
failures as a failed `Run`/`AgentStep` — catching twice would just
duplicate that handling, so the base class deliberately doesn't).

## Framework (`app/agents/_framework`, `AGENT_FRAMEWORK.md`)

Every agent is defined by an `AgentManifest` (`agent_id`, `purpose`,
`accepted_subject_types`, `goals`, `cost_class`, `max_graph_hops`,
`output_schema`) — "the single file a reviewer reads to understand what an
agent does without reading its implementation." Registered once per agent,
imported at startup by the Agent Registry (`app.orchestrator.registry`).

Real manifests audited for this handbook (`app/agents/*/manifest.py`)
confirm the pattern holds across every current agent, not just the
Frontier ones — e.g. `TESTING_MANIFEST`, `PLANNING_MANIFEST`,
`ENGINEERING_REVIEW_MANIFEST` all declare `required_dependencies` against
`app.orchestrator.preflight`'s `DEPENDENCY_LLM`/`DEPENDENCY_NEO4J` — ADR
0011's preflight-validation gate, so a misconfigured deployment fails an
agent run explicitly and immediately rather than partway through
execution.

`max_graph_hops` does double duty, precisely documented in the manifests
themselves: it's the signal `RunCoordinator` uses to decide whether to
inject a `graph_repository` into `AgentContext.extras` at all (`if
"graph_repository" not in ctx_extras and manifest_entry is not None`) —
*not* necessarily the actual hop bound a service call uses. Example:
`REPOSITORY_UNDERSTANDING_MANIFEST` declares `max_graph_hops=1`, but
`RepositoryProfileService.get_profile` calls `get_full_graph` directly
(no bounded walk) — the manifest's "nonzero" signal is load-bearing, its
specific value is not.

## Prompt Builder

`app/agents/frontier/prompt_builder.py` — "the one place a Frontier agent
calls the LLM." Wraps `app.agents.llm.invoke_llm_json` unmodified (not
`StageAwareLLMProvider` directly) so every Frontier agent inherits the
same ADR-0012 LLM-invocation persistence every other agent already gets,
with zero new persistence code. On any failure (provider error, malformed
JSON) it degrades to an empty narrative plus a `status="failed"` `Evidence`
entry rather than raising — "a report's deterministic facts must never
depend on the model succeeding," the same discipline
`documentation_health._synthesize` already established and the same
degrade-gracefully precedent used throughout Context Discovery (ADR 0015).

## Response Renderer

`response_renderer.py` — pure formatting over an already-built dict of
sections (`str` → paragraph, `list[str]` → bullet list — the two shapes
`documentation_health`'s report narrative already used, generalized so
every Frontier agent shares one rendering path instead of hand-writing
Markdown per agent). Never touches `AgentContext`, a database, or a graph.

## Executor

`service_executor.py` — the one dispatch point for a Frontier agent's
service calls, hitting the Engineering Intelligence Services directly and
concurrently (`asyncio.gather`, no ordering dependency between
independent reads). **Deliberately not routed through**
`OrganizationKnowledgeService.compose` — `compose` only covers four of the
six services and its contracts (`ComposedAnswer`/`ServiceRequest`) are
explicitly frozen for its own RFC. `ServiceExecutor` is a separate,
frontier-package-local superset dispatcher rather than an extension of a
frozen contract — a concrete example of respecting a "frozen" boundary by
building alongside it instead of reopening it.

`service_request_builder.py`'s `ServiceRequestBuilder` Protocol is worth
noting as a structural (not just documented) constraint: a
`build_service_requests` implementation's signature has no `AsyncSession`
or `IGraphRepository` parameter available at all — it *cannot* query a
database or traverse a graph even by accident, only decide what to ask
for.

## Metrics

`agent_metrics.py` — `AgentMetrics` measures wall-run/service-call/LLM-call
duration via `time.monotonic()` (immune to clock adjustments mid-run) and
folds caller-supplied token/cache numbers in verbatim. Deliberately **not**
persisted by this module — `invoke_llm_json` already persists LLM
invocation metadata independently (ADR 0012); `AgentMetrics` is the
in-run, in-memory counterpart folded into `AgentOutput.result["metrics"]`
for the UI, not a second persistence path.

`result_mapper.py` maps all these pieces into the one frozen
`AgentOutput` envelope every agent returns — reusing `AgentOutput`/
`Confidence`/`Evidence` unmodified. Its confidence scoring is intentionally
minimal and generic ("how many service calls succeeded," not domain
judgment) — a documented, deliberate ceiling: "a future agent that wants a
smarter confidence signal computes it in its own `render_response`," this
shared mapper does not guess at semantics it can't know.

## Registration

Every agent's manifest is imported once, at startup, by
`app.orchestrator.registry` — the same static-import, no-dynamic-plugin-
loading discipline `ARCHITECTURE.md` § Plugin Architecture specifies for
Phase 1–2 ("no dynamic code loading, no plugin marketplace security
surface, yet"). Standalone AI Workspace agents (Repository Understanding,
Impact Analysis, Dependency Query, API Intelligence, Documentation Review)
are explicitly *not* added to `app.services.workflow_service.STAGE_GOALS`
— each manifest's own docstring states this directly, distinguishing an
independently-invokable `POST /agent-runs` capability from an SDLC
Workflow pipeline stage. Both categories exist in the same registry and
share the same `BaseAgent`/`RunCoordinator` machinery; they differ only in
whether a `Goal` maps to them from the Workflow pipeline's stage table.

## Why agents contain almost no business logic (the actual, verifiable claim)

Every fact an agent's output states is computed by a lower layer the agent
did not write: the Engineering Intelligence Services compute blast radii,
profiles, and findings; the agent's only jobs are (1) decide which
service calls to make, (2) decide what to ask the LLM to narrate, and (3)
shape the response dict. None of the three is "business logic" in the
sense of deciding what's true — that determination happened in the
Knowledge Engine and the Engineering Intelligence Service Layer, both
covered in earlier sections. This is the concrete mechanism behind
`AGENT_FRAMEWORK.md`'s design goal: "new agent → implement `BaseAgent` +
`AgentManifest`... zero changes required to: the Orchestrator's Run
Coordinator, other agents, the GraphWriter, or the frontend Agents page."
