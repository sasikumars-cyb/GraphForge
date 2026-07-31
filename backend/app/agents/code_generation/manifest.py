"""Code Generation Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM

CODE_GENERATION_MANIFEST = AgentManifest(
    agent_id="code_generation",
    purpose=(
        "Given an approved Planning blueprint (via cross-workflow context), "
        "produce a structured code generation artifact containing files to "
        "create, modify, or delete, plus a commit message. Does NOT write "
        "to any external system."
    ),
    goals=frozenset({"generate_code"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="expensive",
    max_graph_hops=0,
    output_schema_name="GeneratedCodeResult",
    # ADR 0011, OD-3 — LLM only; max_graph_hops=0 means no Neo4j dependency.
    required_dependencies=frozenset({DEPENDENCY_LLM}),
)
