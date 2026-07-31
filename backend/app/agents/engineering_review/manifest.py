"""Engineering Review Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM

ENGINEERING_REVIEW_MANIFEST = AgentManifest(
    agent_id="engineering_review",
    purpose=(
        "Given a Planning workflow's Planning, Development, and Testing outputs, "
        "validate implementation completeness, repository/component selection, "
        "identified risks, dependencies, and test strategy, and produce an "
        "Engineering Readiness Report a human reviews before approving the "
        "blueprint. Reviews planning artifacts — never a git diff."
    ),
    goals=frozenset({"review_readiness"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="EngineeringReadinessReport",
    # ADR 0011, OD-3 — LLM only; max_graph_hops=0 means no Neo4j dependency.
    required_dependencies=frozenset({DEPENDENCY_LLM}),
)
