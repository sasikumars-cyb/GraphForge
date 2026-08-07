# ROADMAP.md — GraphForge

## Phase 1 — Foundation (Orchestrator + Framework, on top of existing ChangeGuard)

**Goal**: introduce the Agent Orchestrator, generalized Context Builder, and Agent Framework
without breaking any existing ChangeGuard capability. Ship with exactly the agents that already
exist (Review) plus stubs for the rest, proving the framework holds before multiplying agents.

- `app/agents/_framework`: `BaseAgent`, `AgentManifest`, `AgentOutput`, `Evidence` — migrate the
  existing Review agent onto this base with zero behavior change (regression-tested against
  today's `investigate`/`ai-analysis` test suite).
- `app/orchestrator`: Registry, rule-based Selector, Run Coordinator, `Run`/`AgentStep` Postgres
  tables, `RunContext` (Redis) for Shared Memory.
- `app/context`: generalize `ContextBuilder` into Entry Resolver + Context Assembler; ship
  `GitHubEntryResolver` (wraps existing PR resolution) only — Jira/Confluence resolvers are Phase 2.
- `GraphWriter` choke point + schema registry, retrofitted under the existing Neo4j writes (code
  graph facts flow through it unchanged in effect, changed in path).
- Frontend: `Agents` page (run history + `ReasoningLogPanel` reused for detail) using the new
  `agent-runs` API. `Pipeline`/`Projects`/`Knowledge Graph` pages are stubbed nav entries only.
- **Exit criterion**: existing PR review flow (Run AI / Investigate / Publish Review) works
  identically end-to-end, now recorded as `Run`s, with zero regression in the existing test suite.

## Phase 2 — Requirement & Planning Agents + Jira/Confluence

**Goal**: prove sequential multi-agent handoff and extend the graph beyond code.

- `JiraEntryResolver`, `ConfluenceEntryResolver`, `IKnowledgeSource` implementations for both,
  new graph node types (`Story`, `Epic`, `Document`, `ADR`).
- Requirement Agent: clarifies a story against existing ADRs/docs in the graph, writes
  `ClarifiedRequirement` facts.
- Planning Agent: consumes Requirement's output via sequential handoff, produces a task breakdown,
  writes `Plan`/`TaskBreakdown` facts.
- Architecture Agent: reuses the existing deterministic dependency-graph traversal (already built
  for Review) against a *story's* linked repositories rather than a PR's diff — same tool,
  different Subject.
- `Projects` and `Pipeline` UI pages go live, backed by the new `/projects/*` API.
- Confidence calibration tracking begins (requires a lightweight thumbs-up/down capture on Agents
  UI — new, small).
- **Exit criterion**: a Jira story can flow Requirement → Planning → Architecture with visible,
  evidence-backed output in the Pipeline UI, without touching a PR at all.

## Phase 3 — Development, Testing, Release Agents + LLM-based Selector

**Goal**: close the loop from planning to shipped code, and start relaxing the rule-based
Orchestrator now that there's real usage data to design an LLM-based Selector against.

- Development Agent: scaffolds implementation guidance grounded in the Architecture Agent's
  output + existing patterns found via graph search (not autonomous code-writing — assistive).
- Testing Agent: consumes existing test-result integration, correlates failures against the
  dependency graph to point at the likely-responsible change (`TestRun`/`TestCase` nodes from
  Phase 1's schema, populated for the first time here).
- Release Agent: formalizes the existing Release Coordination Plan logic (already built inside
  the AI analysis pipeline) as its own agent module — no new capability, an extraction.
- `ISelector` gets an LLM-based implementation, A/B'd against the rule-based one before becoming
  default — swap is config-only per the Plugin Architecture contract in `ARCHITECTURE.md`.
- Evaluate: out-of-process plugin protocol for agents (only if third-party/customer-authored
  agent demand is real by this point — do not build speculatively).
- **Exit criterion**: a story can flow Requirement → ... → Release with every stage evidence-backed
  and visible in Pipeline; Selector correctly routes at least as well as the rule table it replaces.

## Backlog (not phase-committed)

- Monitoring Agent (Datadog/Grafana/Splunk `IKnowledgeSource`s, `Incident` node type, incident ↔
  PR correlation).
- Documentation Agent (detects stale docs by diffing graph facts against `Document` node content).
- Slack integration as both entry point (`SlackEntryResolver`) and notification sink.
- Kubernetes deployment topology as graph nodes (`Deployment`, `Service` at runtime, distinct from
  code-level `Component`).
- Cursor-based pagination for graph-scale list endpoints, if offset pagination's query cost becomes
  measurable at real org scale.
- Cross-organization graph federation (explicitly not needed until a customer requests it).

## Technical Debt (carried in, must not be forgotten)

- `GET .../ai-analysis` still doesn't expose `release_coordination_plan` (documented gap from
  ChangeGuard's AI-enrichment work) — fold into Phase 1's `AgentOutput` envelope migration so it's
  fixed as a byproduct, not a separate ticket.
- The one pre-existing failing test (`test_connect_returns_503_when_not_configured`) conflicts
  with real `GITHUB_CLIENT_ID`/`SECRET` now present in the dev `.env` from the earlier real-GitHub
  setup — needs an explicit "unset for this test" fixture (`monkeypatch.delenv`), not a rewrite of
  the assertion. Fix in Phase 1 alongside the Review-agent migration regression pass.
- Full-clone-per-index in `app/indexer` does not scale past a handful of repos per org — flagged
  in `ARCHITECTURE.md` § Scalability, must be resolved before Phase 2's multi-repo Architecture
  Agent use case is exercised at more than demo scale. KAN-32 (ADR 0020) implemented the
  incremental mechanism itself — a safe diff re-parses and merges only the changed files, with a
  full re-index as the automatic fallback — but the GitHub `push` webhook that would trigger it on
  a real push is still a separate, scoped follow-up (today it runs via the existing manual/API
  index trigger only); this line stays open until that lands.

## Stretch Goals

- Live confidence calibration dashboard (prompt version vs. human-agreement rate over time).
- Natural-language Goal inference at entry (Phase 3's LLM Selector, stretched to also infer Goal
  from free text rather than requiring an explicit trigger).
- Org-wide "impact simulation" — ask the graph "if I change X, what breaks" without an actual diff,
  purely from graph structure, as a standalone Knowledge Graph feature independent of any PR.

## Demo Strategy

Each phase demos against the **existing 4 seeded demo repositories** (order-service,
payment-service, inventory-service, notification-service) already connected to real GitHub under
the project's demo account — no new demo data investment required for Phase 1. Phase 2's Jira
demo introduces a small (3–5 story) seeded Jira project mirroring the existing PR scenarios
(`pr-1`..`pr-4`) so the demo narrative stays "one connected story," not a fresh dataset per phase.

## Team Responsibilities

| Track | Owns |
|---|---|
| Platform/Framework | `app/orchestrator`, `app/agents/_framework`, `app/context`, `GraphWriter` |
| Knowledge Graph | Schema evolution, indexer extensions, new `IKnowledgeSource` integrations |
| Agents (rotating per phase) | One agent module per engineer/pair — Requirement, Planning, Architecture, etc. |
| Frontend Platform | `Agents`, `Pipeline`, `Knowledge Graph`, `Projects` pages + shared component library |
| DevEx/Reliability | Telemetry, logging conventions, CI, migration discipline |

Ownership boundaries follow the folder structure in `ARCHITECTURE.md` exactly — a team's PRs stay
within their folder; cross-folder changes (e.g. a new graph node type another team's agent needs)
go through the GraphWriter schema registry as an explicit, reviewed addition, not an
ad hoc edit by whichever agent needed it first.

## Milestones

| Milestone | Phase | Signal |
|---|---|---|
| M1: Orchestrator parity | 1 | Existing Review flow unchanged, now run-tracked |
| M2: First cross-agent chain | 2 | Requirement → Planning → Architecture visible end-to-end |
| M3: Full SDLC pipeline | 3 | Requirement → Release, one story, evidence-backed at every stage |
| M4: Selector upgrade | 3 | LLM-based Selector matches/beats rule-based baseline |

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Graph schema sprawl (uncontrolled node/edge types per team) | High — breaks cross-agent traversal assumptions | GraphWriter schema registry is the only write path; new types are reviewed additions, not silent |
| Orchestrator becomes a bottleneck as agent count grows | Medium | Bounded `max_graph_hops` per agent, async run dispatch, per-run concurrency cap (already in `ARCHITECTURE.md`) |
| LLM cost scales linearly with agent count per run | Medium | `cost_class` on every manifest, budget-aware Selector in Phase 3 |
| Confidence scores become decorative (unchecked against outcomes) | High — undermines "evidence over assertion" thesis | Calibration tracking is not optional past Phase 2; block Phase 3 agent additions if not shipped |
| Full-repo-clone indexer doesn't scale to multi-repo Architecture Agent use in Phase 2 | Medium | Flagged as Technical Debt above; incremental indexing scoped as a Phase 2 prerequisite, not deferred silently |

## Definition of Done

For any agent, integration, or UI surface shipped under this roadmap:

1. Manifest/interface registered per `AGENT_FRAMEWORK.md` / `ARCHITECTURE.md` conventions — no
   ad hoc registration path.
2. Every output carries confidence + non-empty evidence, or confidence is omitted entirely (never
   a bare unjustified score).
3. Errors surface verbatim; nothing is silently swallowed or defaulted.
4. Structured logs carry `run_id`/`agent_id`/`subject_id` (or integration/source equivalent).
5. Automated tests cover: happy path, the "not found"/"not connected" precondition, and one
   upstream-failure path — matching the existing test rigor already applied to the Review agent
   and Publish Review feature.
6. UI surfaces follow `UI_GUIDELINES.md` exactly — no new card/badge/color without updating that
   document first.

## Testing Strategy

Unchanged discipline from the existing codebase, applied to every new module: real Postgres/Neo4j
in integration tests (no mocked DB), mock only the exact external HTTP boundary (existing
`httpx.MockTransport` convention), unit tests for pure logic (Selector rules, GraphWriter schema
validation, prompt builders) with no I/O. No new testing framework or convention introduced.

## Release Plan

Each phase ships behind existing patterns — no new deployment infrastructure required. Phase 1
ships as a set of additive migrations + new routers, deployed the same way existing ChangeGuard
features have shipped (Alembic migration, `docker-compose` service reuse, no new infra
provisioning). Phases 2–3 add integration credentials (Jira, Confluence) via the existing
encrypted-connection pattern (`app.core.crypto`) — no new secrets-handling mechanism.

## Future Vision

By the end of Phase 3, GraphForge is the system an engineer opens *before* opening Jira or GitHub
directly — because starting there is strictly faster: the graph already knows what a manual search
across four tools would take fifteen minutes to reconstruct. Every subsequent agent (Monitoring,
Documentation, and whatever an org needs next) is additive proof of the thesis in
`PRODUCT_VISION.md`: the graph is the product, and every agent just makes it smarter.
