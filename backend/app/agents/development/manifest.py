"""Development Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest

DEVELOPMENT_MANIFEST = AgentManifest(
    agent_id="development",
    purpose=(
        "Given a high-level engineering request, produce a structured implementation "
        "blueprint: affected repositories, services, components, dependencies, "
        "implementation phases, risks, and reuse candidates — all grounded in the "
        "live Engineering Knowledge Graph."
    ),
    goals=frozenset({"develop_change_plan"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="standard",
    max_graph_hops=3,
    output_schema_name="DevelopmentPlan",
)
