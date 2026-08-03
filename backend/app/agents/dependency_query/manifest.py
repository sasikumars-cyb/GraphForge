"""Dependency Query Agent manifest — registered at startup.

Standalone AI Workspace capability (goal `analyze_dependency_query`), the
same shape as `repository_understanding`/`impact_analysis`: invoked
directly through `POST /agent-runs`, never a stage inside the SDLC
Workflow pipeline (see `app.services.workflow_service.STAGE_GOALS`, which
this is deliberately absent from).

Read-only. It never writes to Neo4j, Postgres, or the repository — it
only reads an already-computed `QueryResult`
(`app.services.engineering_intelligence.dependency_query_service`).

`max_graph_hops=0`, no `DEPENDENCY_NEO4J` — `DependencyQueryService.search`
never touches `IGraphRepository` at all (it's a pure
`relationship_lookup.fetch_with_confidence` filter over Engineering
Memory), so no `graph_repository` needs to be injected. Same "no Neo4j
dependency" signal `documentation_health`'s manifest already uses.
"""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM

DEPENDENCY_QUERY_MANIFEST = AgentManifest(
    agent_id="dependency_query",
    purpose=(
        "Answer dependency questions about a repository: what depends on "
        "it, what it depends on, and which relationships are verified "
        "versus low-confidence — read-only, computed entirely from the "
        "Engineering Intelligence Service Layer's DependencyQueryService."
    ),
    goals=frozenset({"analyze_dependency_query"}),
    accepted_subject_types=frozenset({"repository"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="DependencyQueryReport",
    required_dependencies=frozenset({DEPENDENCY_LLM}),
)
