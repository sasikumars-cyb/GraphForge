# Section 7 — Engineering Intelligence Service Layer

Source: `app/services/engineering_intelligence/*` (`contracts.py` docstring:
"the Engineering Intelligence Service Layer" — an approved design with
explicit constraints quoted verbatim below, each enforced by the actual
module docstrings, not just described in a design doc).

## Why services exist separately (the layer's own design rule)

Stated identically across every service file: **no service in this layer
calls an LLM, and no service parses natural language.** Classification
("which service does this question need") and narrative synthesis are the
calling agent's job, never this layer's. `OrganizationKnowledgeService
.compose` takes an explicit, already-decided `list[ServiceRequest]` — the
boundary that keeps prompt/classification logic out of the service layer
entirely. This is the same discipline as `contracts.py`'s own top-of-file
rule: no `AgentOutput`/`Subject`/`Evidence` types appear here; translating
a service result into an agent's output is the calling agent's job.

Every collection field is built in a deterministic sort order by the
producing service — "two calls against the same underlying data always
return byte-identical results," the same discipline `app.knowledge_engine.parity`
already applies (§ [05_KNOWLEDGE_ENGINE.md](05_KNOWLEDGE_ENGINE.md)).

## Repository Profile (`repository_profile_service.py`)

- **Purpose**: the graph-shaped, evidence-shaped summary of one repository.
- **Inputs**: `IGraphRepository.get_full_graph` (structure — `Endpoint`,
  `DataTable`, `KafkaTopic`, `FeignClient`, `MavenDependency`,
  `PythonDependency` node labels, confirmed by audit to have exactly one
  writer, `app.indexer.graph.builder`) + `evidence_curation.curate_for_prompt`
  (narrative evidence).
- **Outputs**: `RepositoryProfile` — apis, databases, queues, integrations,
  dependencies, architecture_summary.
- **Named, honest scope cut**: the approved design asked for "feature
  flags" and "cloud services" as profile categories. Audited against the
  graph's actual node-label vocabulary and found: nothing produces those
  labels. Rather than fabricate placeholder fields, the service reports
  only categories the graph actually models. This is cited elsewhere in
  this handbook as a model instance of designing to what evidence actually
  supports, not to what a spec asked for in the abstract.
- **No LLM calls** — narrative synthesis stays in the calling agent
  (Repository Understanding Agent, § [08_AGENTS.md](08_AGENTS.md)).

## Impact Analysis (`impact_analysis_service.py`)

- **Purpose**: blast-radius computation.
- **Owns no traversal logic of its own** — every hop goes through
  `graph_traversal.traverse`; every confidence/explanation lookup through
  `relationship_lookup.fetch_with_confidence`. `ChangeSimulationService`
  calls this service rather than duplicating any of its logic (an explicit
  constraint of the approved design, not an accident of reuse).
- **Edge types followed**: `CALLS`, `CALLS_SERVICE`, `EXPOSES`,
  `PRODUCES_TO`, and siblings — the same vocabulary
  `app.indexer.graph.builder`/`cross_repo_linker` write, named explicitly
  in the module so the traversal never drifts from what's actually written.
- **Known, documented gap** (validation guide Known Gap #3): the
  underlying Cypher query filters *both* endpoints of a candidate edge to
  the same `repository_id` — a cross-repository edge, by definition, has
  a target in a different repository, so it structurally cannot pass that
  filter. Blast radius for every repository in the 24-repo validation
  suite is currently `impacted_repositories = [itself]`, regardless of how
  many real `DEPENDS_ON_REPOSITORY` edges exist in Neo4j. See
  [16_REALITY_CHECK.md](16_REALITY_CHECK.md).

## Dependency Query (`dependency_query_service.py`)

- **Purpose**: filtered relationship search — "what depends on this," "what
  does this depend on."
- **Reuses** `relationship_lookup.fetch_with_confidence`; does not query
  Postgres directly and adds no new method to the frozen
  `EngineeringMemoryRepository`.
- **Scoping note, named directly**: `EngineeringMemoryRepository
  .get_current_relationships` is repository-scoped — there is no existing
  "every relationship across the whole org" query to build true org-wide
  search on without new persistence-layer code, out of this service's
  scope. `search` therefore takes an explicit `repository_ids` list rather
  than an implicit "empty means everything."
- **Resolves a named duplication risk**: both the Dependency Explorer and
  Engineering Search agents needed "the same underlying capability" — both
  now call this one function instead of two divergent implementations.
- **Known, documented gap** (validation guide Known Gap #4): because
  Engineering Memory currently only persists *intra-repository* structural
  relationships (cross-repository edges are written to Neo4j only, never
  to `KnowledgeRelationship`), every current relationship's source matches
  the queried repository's own id prefix. Net effect: `direct_dependencies_count`
  equals a repository's *total* relationship count, and
  `downstream_consumers_count` is provably always 0. This same root cause
  produces the Validation 7 (Parity) failures for repositories with an
  outgoing `DEPENDS_ON_REPOSITORY` edge — one root cause, two visible
  symptoms, not two separate bugs.

## Architecture Insights (`architecture_insight_service.py`)

- **Purpose**: dependency cycles, shared databases, tight coupling,
  repeated-rejection ownership gaps.
- **Reuses** `relationship_lookup.fetch_with_confidence` (confidence-
  bearing findings) and `LearningEngineService.get_statistics` (ownership-
  gap signals) — never re-derives either.
- **Deliberately does not call `graph_traversal`**: cycle/coupling
  detection works over an already-materialized, org-scale-bounded
  relationship list (`DEPENDS_ON_REPOSITORY`/`CALLS_SERVICE`), not a
  node-level neighborhood walk. The module docstring names the reasoning
  directly: "reserving `graph_traversal` for genuine node-level traversal
  keeps this service from growing a second traversal implementation under
  a different name" — a real instance of resisting an unnecessary
  abstraction.

## Change Simulation (`change_simulation_service.py`)

- **Purpose**: "if I remove this endpoint / topic / rename this API /
  upgrade this dependency / migrate this database, what breaks."
- **Never performs traversal itself** — every `simulate` call is a
  `change_type → direction` mapping (`_DIRECTION_BY_CHANGE_TYPE`) followed
  by exactly one call to `impact_analysis_service.compute_blast_radius`.
  `remove_endpoint`/`remove_topic`/`rename_api`/`migrate_database` map to
  `downstream` ("who depends on this, that breaks if it's gone");
  `upgrade_dependency` maps to `upstream` ("what's underneath, that's the
  actual risk surface for an upgrade"). This is the layer's clearest
  example of composition-over-duplication: five semantically distinct
  "change types" collapse to one shared traversal, differing only by
  direction.
- **Inherits Impact Analysis's known gap** — a simulated change whose
  blast radius should cross repositories is subject to the same
  same-repository traversal filter limitation named above.

## Organization Knowledge (`organization_knowledge_service.py`)

- **Purpose**: compose results from multiple services in this layer into
  one `ComposedAnswer`.
- **Never parses natural language, never calls an LLM** — takes an
  explicit `list[ServiceRequest]` the calling agent has already decided
  on. This is the layer's outermost composition point and the clearest
  statement of the classification/service boundary: "which service(s) does
  this question need" is prompt logic that belongs to the agent, not to
  this layer.

## How the services compose (summary)

```
RepositoryProfileService  ─┐
DependencyQueryService     ├─► OrganizationKnowledgeService.compose(requests)
ArchitectureInsightService ┘        → ComposedAnswer{results, errors}

ImpactAnalysisService ◄── ChangeSimulationService.simulate()
        │
        └── graph_traversal.traverse() / relationship_lookup.fetch_with_confidence()
```

Every arrow above is a plain function/service call — no LLM, no natural-
language parsing, anywhere in this layer. The LLM only enters one layer up,
inside the calling agent's `build_prompt`/narrative step (§
[08_AGENTS.md](08_AGENTS.md)).
