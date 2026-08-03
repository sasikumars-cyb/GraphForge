"""Repository Understanding Agent manifest — registered at startup.

Standalone AI Workspace capability (goal `analyze_repository_understanding`),
the same shape as `documentation_health`: invoked directly through
`POST /agent-runs`, never a stage inside the SDLC Workflow pipeline (see
`app.services.workflow_service.STAGE_GOALS`, which this is deliberately
absent from).

Read-only. It never writes to Neo4j, Postgres, or the repository — it only
reads an already-materialized `RepositoryProfile`
(`app.services.engineering_intelligence.repository_profile_service`).

`max_graph_hops=1` is what makes `RunCoordinator` inject a `graph_repository`
into `AgentContext.extras` at all (see `app.orchestrator.run_coordinator`:
the slot is only filled `if "graph_repository" not in ctx_extras and
manifest_entry is not None`, gated on `manifest.max_graph_hops`) —
`RepositoryProfileService.get_profile` calls `IGraphRepository.get_full_graph`
directly rather than a bounded neighborhood walk, so the hop count itself
is not load-bearing, only its "nonzero" signal is.
"""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

REPOSITORY_UNDERSTANDING_MANIFEST = AgentManifest(
    agent_id="repository_understanding",
    purpose=(
        "Explain what a repository does: its exposed APIs, owned databases, "
        "messaging usage, external integrations, and most important "
        "dependencies — read-only, computed entirely from the Engineering "
        "Intelligence Service Layer's RepositoryProfileService."
    ),
    goals=frozenset({"analyze_repository_understanding"}),
    accepted_subject_types=frozenset({"repository"}),
    cost_class="cheap",
    max_graph_hops=1,
    output_schema_name="RepositoryUnderstandingReport",
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)
