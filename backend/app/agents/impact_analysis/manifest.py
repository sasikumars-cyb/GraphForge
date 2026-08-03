"""Impact Analysis Agent manifest — registered at startup.

Standalone AI Workspace capability (goal `analyze_impact_analysis`), the
same shape as `repository_understanding`: invoked directly through
`POST /agent-runs`, never a stage inside the SDLC Workflow pipeline (see
`app.services.workflow_service.STAGE_GOALS`, which this is deliberately
absent from).

Read-only. It never writes to Neo4j, Postgres, or the repository — it
only reads an already-computed `BlastRadius`
(`app.services.engineering_intelligence.impact_analysis_service`).

`max_graph_hops=2` matches `ImpactAnalysisService.compute_blast_radius`'s
own default `max_hops` — the manifest's job is only to signal "this agent
needs a `graph_repository`" to `RunCoordinator` (see
`app.orchestrator.run_coordinator`: the slot is only filled when
`manifest.max_graph_hops > 0`); the actual hop bound the agent requests is
a `ImpactAnalysisCall` argument, set in `agent.py`.
"""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

IMPACT_ANALYSIS_MANIFEST = AgentManifest(
    agent_id="impact_analysis",
    purpose=(
        "Compute a repository's blast radius: which repositories, APIs, "
        "databases, and queues it impacts, and how strong the confidence "
        "is behind each relationship — read-only, computed entirely from "
        "the Engineering Intelligence Service Layer's ImpactAnalysisService."
    ),
    goals=frozenset({"analyze_impact_analysis"}),
    accepted_subject_types=frozenset({"repository"}),
    cost_class="cheap",
    max_graph_hops=2,
    output_schema_name="ImpactAnalysisReport",
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)
