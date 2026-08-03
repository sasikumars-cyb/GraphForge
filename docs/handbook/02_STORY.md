# Section 2 — The Story

## Why GraphForge exists

`PRODUCT_VISION.md`'s framing: every engineering org already contains the
answer to "what breaks if I ship this," "who should review this," "what
caused this incident" — it's just scattered across GitHub, Jira,
Confluence, CI logs, and senior engineers' heads. GraphForge's bet is to
make that answer queryable by putting it in one graph every agent reads
from and writes to, so the organization's own history becomes its own
intelligence, instead of evaporating with attrition (`PRODUCT_VISION.md`
§ Long-Term Vision, § Persona "Ana — Staff Architect": "wants the
Architecture Agent and Knowledge Graph to encode what she knows so it
survives her being on vacation — or leaving").

## What problem it solves, concretely

Not an abstract knowledge-management problem — a specific, named workflow
pain: "does this affect X" asked of the same staff engineer ten times a
week (`PRODUCT_VISION.md` § Target Users). ChangeGuard, the predecessor
product, proved the narrow version of this at PR-review scale: given a
diff, a deterministic dependency graph plus a tool-using LLM agent beats
prompting an LLM with the diff alone (`PRODUCT_VISION.md` § Why GraphForge
Exists). GraphForge generalizes that proof from "review this PR" to the
full SDLC.

## Why current tooling isn't enough

The competitive-positioning table in `PRODUCT_VISION.md` names the gap
directly: generic LLM chatbots have no memory across sessions; PR-bot point
solutions have memory per-PR only and no cross-system reasoning; Jira AI
add-ons are per-ticket only. None of them keep a persistent, queryable,
evidence-backed record that the *next* PR, incident, or engineer inherits.
GraphForge's stated non-goals reinforce this isn't scope creep dressed up
as differentiation — it explicitly is **not** a Jira/Linear replacement,
**not** a standalone PR bot sold independent of the graph, and **not**
(in this phase) an autonomous auto-merge system (`PRODUCT_VISION.md` §
Out of Scope).

## Why Engineering Memory exists

Before ADR 0018, GraphForge's `replace_repository_graph` model fully
replaced Neo4j on every re-index — no history of how a repository's
architecture changed, no way to say "we used to be 92% confident this edge
existed, now we're not" (ADR 0007 § Consequences: "there's no history of
how a repository's architecture changed over time"). Engineering Memory is
the direct fix: an append-only Postgres log where `Hypothesis`,
`ValidationResult`, `UserCorrection`, and every confidence-state transition
are permanently retained, never edited or deleted, only superseded (ADR
0018, "Frozen at RFC-03 approval"). This is also what makes a materializer
replay test possible at all — you can't prove a graph is reconstructable
from history if the history itself was never kept.

## Why Neo4j exists

Because graph traversal — "what depends on this," blast radius, cross-
repository call chains — is a native operation in a graph store and an
expensive one to fake relationally. But ADR 0018 makes a deliberate,
named inversion: Neo4j stops being the system of record and becomes "a
synced index optimized for graph traversal," rebuildable at any time from
the Postgres log (`app.knowledge_engine.materializer`). This is stated as
"a deliberate, permanent shift... not a temporary state" — the tradeoff
being accepted is a second store to keep synced, in exchange for cheap
traversal without making Postgres carry graph-shaped queries and without
making Neo4j responsible for being the thing nobody can ever afford to
lose.

## Why AI is used

Two distinct roles, not one blurred "AI" bucket:

1. **Hypothesis generation** (`app.knowledge_engine.contracts.hypothesis
   .HypothesisGenerator`) — an LLM is one more generator alongside the
   deterministic parser and rule-based extractors, proposing relationships
   the deterministic path can't reach (e.g. RFC-06's 13-type "capability"
   vocabulary — `OWNS_*`/`CONTAINS_*`/`INTEGRATES_WITH_*` — inferred from a
   README/manifest an annotation-matcher would never see).
2. **Narrative synthesis** — turning already-computed, deterministic facts
   (a `BlastRadius`, a `RepositoryProfile`) into human-readable prose
   (`app.agents.frontier.prompt_builder`), or synthesizing an
   `EngineeringUnderstanding` from curated evidence in Context Discovery
   (ADR 0015).

Neither role lets AI *decide* what is true. `PRODUCT_VISION.md` Core
Principle 3 states this as policy, not just an implementation detail:
"deterministic before probabilistic... reserve the LLM for judgment calls
layered on top of exact facts — never for facts themselves."

## Why deterministic reasoning comes first

This is the single most-repeated architectural invariant across every ADR
touched in this handbook, stated identically each time: a
`HypothesisGenerator` never writes to the graph; a `KnowledgeValidator` is
always deterministic and never calls an LLM ("a validator that itself asks
an LLM 'does this seem right' isn't validating, it's generating a second,
uncoordinated hypothesis" — ADR 0018); `generator_confidence` is advisory
and structurally forbidden from influencing a validator's verdict or the
`ConfidenceEngine`'s aggregation; an LLM-sourced relationship cannot reach
`Verified` from a single provider's agreement alone. The reason is spelled
out in ADR 0018 § Consequences: this bounds LLM API cost growth, but it
also *bounds how much of the graph can be LLM-sourced without additional
deterministic evidence to corroborate against* — an honestly-stated
limitation, not a hidden one. The origin of this discipline predates ADR
0018: ADR 0007's Java/Spring Boot parser was "fully deterministic — no
heuristics beyond literal matching," explicitly rejecting guessing at a
Kafka topic name passed as a variable, "the alternative would require real
data-flow analysis... and guessing would violate 'everything should be
deterministic.'" ADR 0018 generalizes that one parser's discipline into a
platform-wide rule.
