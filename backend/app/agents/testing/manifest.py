"""Test Planning Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

TESTING_MANIFEST = AgentManifest(
    agent_id="testing",
    purpose=(
        "Given an engineering change request, produce a structured testing strategy: "
        "test scope, regression tests, integration tests, edge cases, execution order, "
        "automation candidates, and manual validation — all grounded in the live "
        "Engineering Knowledge Graph."
    ),
    goals=frozenset({"plan_tests"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="standard",
    max_graph_hops=3,
    output_schema_name="TestPlan",
    # ADR 0011, OD-3 — LLM (this agent's own reasoning), Neo4j (max_graph_hops
    # > 0, above).
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)
