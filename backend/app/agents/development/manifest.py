"""Development Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

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
    # ADR 0011, OD-3 — LLM (this agent's own reasoning), Neo4j (max_graph_hops
    # > 0, above).
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)
