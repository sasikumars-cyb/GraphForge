"""Documentation Planning Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest

DOCUMENTATION_PLANNING_MANIFEST = AgentManifest(
    agent_id="documentation_planning",
    purpose=(
        "Given a Planning workflow's Planning, Development, and Testing outputs plus the "
        "repository's existing documentation, determine which documentation must be "
        "created, updated, or left unchanged once implementation is complete — ownership, "
        "priority, effort, and dependencies for each item — without writing any "
        "documentation itself."
    ),
    goals=frozenset({"plan_documentation"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="DocumentationPlan",
)
