"""Planning Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

PLANNING_MANIFEST = AgentManifest(
    agent_id="planning",
    purpose=(
        "Given a free-text engineering goal, produce a grounded implementation plan "
        "informed by the live Engineering Knowledge Graph — affected components, "
        "Kafka topics, cross-repository dependencies, and ordered implementation steps."
    ),
    goals=frozenset({"plan_freeform"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="standard",
    max_graph_hops=2,
    output_schema_name="PlanningResult",
    # ADR 0011, OD-3 — LLM (this agent's own reasoning), Neo4j (max_graph_hops
    # > 0, above).
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)
