"""Planning Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest

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
)
