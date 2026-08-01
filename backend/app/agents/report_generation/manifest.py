"""Report Generation Agent manifest — registered with the Orchestrator at startup."""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM

REPORT_GENERATION_MANIFEST = AgentManifest(
    agent_id="report_generation",
    purpose=(
        "Given every completed stage of an approved Planning workflow, synthesize a "
        "single, self-contained, high-level HTML report a stakeholder can read without "
        "opening the workflow itself — no new analysis, no new claims beyond what each "
        "stage already produced."
    ),
    goals=frozenset({"generate_report"}),
    accepted_subject_types=frozenset({"freetext"}),
    cost_class="standard",
    max_graph_hops=0,
    output_schema_name="WorkflowReportOutput",
    # ADR 0011, OD-3 — LLM only; max_graph_hops=0 means no Neo4j dependency.
    required_dependencies=frozenset({DEPENDENCY_LLM}),
)
