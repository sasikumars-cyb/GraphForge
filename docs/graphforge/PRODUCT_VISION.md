# PRODUCT_VISION.md — GraphForge

> AI Engineering Intelligence Platform

## Vision

Every engineering organization already contains the answer to "what will happen if I ship this,"
"who should review this," "what broke this incident," and "what should we build next." That
answer is scattered across GitHub, Jira, Confluence, CI logs, and the heads of senior engineers.
GraphForge's vision is to make that answer queryable: a single Engineering Knowledge Graph that
every AI agent reads from and writes to, so the organization's own history becomes its own
intelligence.

## Mission

GraphForge helps engineering teams understand, navigate, and automate the Software Development
Lifecycle using AI powered by a unified Engineering Knowledge Graph.

## Product Philosophy

**The graph is the product. The agents are features of the graph.**

A chatbot answers one question and forgets it. A PR reviewer reviews one diff and forgets it.
GraphForge's agents write structured facts back into a persistent graph — a breaking-change
finding, a dependency edge, a rollout risk, a reviewer's actual ownership — so the next agent,
next PR, next incident, and next engineer inherits that knowledge instead of re-deriving it from
scratch. This is the difference between a system that answers questions and a system that
*understands*.

Concretely: **every feature is a graph read, a graph write, or both.** If a proposed feature is
neither, it is a plugin bolted onto the product, not part of the product, and should be scoped
out or rejected until someone can name the node/edge type it produces or consumes.

## Core Principles

1. **Graph-first, not prompt-first.** Context for an LLM call is assembled by traversing the
   graph, not by string-concatenating whatever the user happened to paste in.
2. **Evidence over assertion.** Every AI claim (a breaking change, a suggested reviewer, a risk
   rating) carries a confidence score and a pointer to the graph nodes/tool outputs that
   produced it. "Trust me" is not an acceptable agent output; "trust me, because of X" is.
3. **Deterministic before probabilistic.** Where a fact can be computed exactly (dependency
   traversal, CODEOWNERS resolution, test coverage delta), compute it exactly. Reserve the LLM
   for judgment calls layered on top of exact facts — never for facts themselves.
4. **One graph, many agents, no silos.** Agents do not maintain private state. Two agents
   reasoning about the same pull request see the same graph, at the same consistency point,
   through the same read APIs.
5. **Entry point agnostic.** A user can start from a Jira story, a PR, an incident, a Confluence
   page, or a plain question. The system's job is to resolve *any* starting point down to the
   same graph coordinates, not to force the user into "the PR view" or "the ticket view."
6. **Composable, not monolithic.** Every agent, tool, and integration is independently
   addable/removable without a redesign — see `AGENT_FRAMEWORK.md` and the Plugin Architecture
   section of `ARCHITECTURE.md`.

## Product Pillars

| Pillar | What it means | Existing proof point |
|---|---|---|
| **Unified Knowledge Graph** | One graph schema spans code, docs, tickets, tests, releases | Neo4j dependency graph (repos, components, APIs, Kafka topics) |
| **Multi-Agent Reasoning** | Specialized agents collaborate through shared context, not a single mega-prompt | Change Investigation Agent's Plan→Tool→Observe→Decide loop |
| **Deterministic Grounding** | AI output is always anchored to a computed, inspectable fact | Deterministic risk classifier + dependency path builder |
| **Continuous SDLC Workflow** | The product mirrors how work actually flows (idea → design → code → review → test → release → operate), not six disconnected tools | Release Coordination Plan already sequences cross-repo rollout |
| **Developer-Native UX** | Every surface reads like a tool built by engineers for engineers — dense, fast, no marketing chrome | Existing dark-themed dashboard/graph UI |

## Target Users

- **IC engineers** shipping changes who need to know blast radius, reviewers, and regression risk
  before they open a PR, not after CI fails.
- **Tech leads / staff engineers** who are the de facto human knowledge graph today and are asked
  "does this affect X" ten times a week.
- **Engineering managers** who need a truthful, always-current picture of what's in flight, what's
  blocked, and what's risky — without asking for a status update.
- **Platform / DevEx teams** who own the tooling and need an extensible substrate, not another
  point solution to maintain.

## User Personas

### Priya — Senior Backend Engineer
Owns `order-service`. Reviews 15+ PRs/week across 4 repositories she doesn't fully remember the
internals of. Wants: "what does this PR actually touch, who else needs to know, and what tests
actually matter" — in the PR, not in a separate tab.

### Marcus — Engineering Manager
Owns delivery for 3 squads. Wants a truthful project/release view without a standup. Cares about
risk surfacing (what's about to break) more than task tracking (which already exists in Jira).

### Ana — Staff Architect
The human who currently *is* the dependency graph for a 40-repo org. Wants the Architecture Agent
and Knowledge Graph to encode what she knows so it survives her being on vacation — or leaving.

### Devon — New Hire (Week 3)
Has zero tribal knowledge. Wants to start from a Jira story and have GraphForge build the context
a 5-year veteran would have assembled from memory: relevant ADRs, prior incidents, owning teams,
existing patterns to follow.

## Product Scope

- SDLC-spanning AI agents (requirement → plan → design → build → review → test → release →
  monitor) operating over one graph.
- Deep integrations with GitHub, Jira, Confluence, Neo4j-backed code/dependency graph, CODEOWNERS,
  OpenAPI specs, ADRs, release metadata, test results.
- A Context Builder that resolves any entry point into graph coordinates and assembles agent
  context automatically.
- An Agent Orchestrator that selects, sequences, and hands off between agents.
- A UI that presents this as one continuous workflow (Dashboard → Project → Graph → Agents →
  Pipeline), not a set of unrelated screens.

## Out of Scope

- **Not** a general-purpose chatbot / "ask me anything" assistant with no graph grounding.
- **Not** a Jira/Linear replacement — GraphForge reads and enriches ticket systems, it does not
  reimplement ticket management, sprint planning, or capacity tracking.
- **Not** a standalone PR review bot sold independently of the graph — Review Agent output is
  only meaningful because it's grounded in the same graph every other agent uses.
- **Not**, in this phase, an autonomous code-writing/auto-merge system. Development Agent assists;
  it does not commit unattended.
- **Not** an observability platform (Datadog/Grafana/Splunk are *sources* GraphForge consumes,
  never features it re-implements).

## Competitive Positioning

| | Chatbot-style AI (generic LLM wrappers) | PR-bot point solutions | Jira AI add-ons | **GraphForge** |
|---|---|---|---|---|
| Memory across sessions | No | Per-PR only | Per-ticket only | Persistent graph, org-wide |
| Cross-system reasoning | No | No | No | Yes — code ↔ tickets ↔ docs ↔ releases |
| Deterministic grounding | No | Partial (diff-only) | No | Yes — graph traversal backs every claim |
| Extensible to new agents | N/A | No | No | Yes — typed agent framework |
| SDLC-continuous | No | No (review-only) | No (planning-only) | Yes — idea to operate |

GraphForge does not compete on "better prompts." It competes on having a graph the others don't,
and a framework where new agents make the *existing* agents smarter (more graph edges), rather
than living as isolated features.

## Why GraphForge Exists

ChangeGuard proved the thesis at PR-review scale: a deterministic dependency graph plus a tool-using
LLM agent produces materially better change-impact analysis than prompting an LLM with a diff
alone. GraphForge is the generalization of that proof across the entire SDLC — the same graph,
the same agent framework, applied to requirements, architecture, testing, and release, not just
review.

## Long-Term Vision

An engineering organization where:
- No engineer re-derives "what does this touch" or "who owns this" from memory — the graph knows.
- Every SDLC artifact (a story, a PR, an ADR, a test, an incident) is a node, so "why did we do
  this" is always one traversal away, permanently.
- New agents (Monitoring, Documentation, and beyond) are additive: shipping one doesn't require
  touching the other nine.
- The org's engineering knowledge compounds instead of evaporating with attrition.

## Definition of Success

- **Time-to-context** for a new task (any entry point) drops from "ask a senior engineer" to
  "GraphForge assembled it before I finished reading the ticket."
- **Every AI output is traceable**: a user can always click through a claim to the graph facts
  and tool calls that produced it. Zero unexplained AI assertions in production.
- **Net-new agents ship without backend rework**: adding agent #11 touches the agent framework
  and its own module, not the orchestrator's core logic or the graph schema's core types.
- **The graph outlives any single feature**: deleting a UI surface never deletes the knowledge it
  contributed.

## Guiding Principles (for anyone building a new feature)

1. Name the node/edge type your feature reads or writes before writing any code.
2. If your agent's output can't be traced to a graph fact or tool call, it is not shippable.
3. Prefer extending the graph schema over adding a bespoke database table.
4. A new integration is a new node/edge source, not a new UI silo — it must show up wherever the
   graph is already surfaced (dependency view, agent context, search).
5. If two agents need the same fact, that fact belongs in the graph, not duplicated in two prompts.
