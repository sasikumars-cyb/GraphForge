"""Documentation Agent manifest — registered with the Orchestrator at startup.

Standalone AI Workspace capability (goal `review_documentation`), the same
shape as the PR Review agent (`app.agents.review_adapter`) — an
independently-invokable agent reached through `POST /agent-runs`, never a
stage inside the SDLC Workflow pipeline (`app.services.workflow_service.
STAGE_GOALS`, which this manifest is deliberately not added to). Do not
confuse this with `documentation_planning` (goal `plan_documentation`),
the existing Workflow-stage agent that plans documentation *work* as part
of a Planning blueprint — this agent reviews and generates Markdown
*content* against a repository's actual indexed graph, on demand, outside
any workflow.
"""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM, DEPENDENCY_NEO4J

DOCUMENTATION_REVIEW_MANIFEST = AgentManifest(
    agent_id="documentation_review",
    purpose=(
        "Discover a repository's Markdown documentation, compare it against "
        "the indexed architecture graph, report outdated/missing/duplicate "
        "documentation and broken internal links, and propose Markdown "
        "updates and new documents — never applied automatically."
    ),
    goals=frozenset({"review_documentation"}),
    accepted_subject_types=frozenset({"repository"}),
    cost_class="standard",
    max_graph_hops=2,
    output_schema_name="DocumentationReviewResult",
    # ADR 0011, OD-3 — LLM (synthesis) + Neo4j (comparison against the
    # indexed architecture graph is a stated capability, not optional).
    required_dependencies=frozenset({DEPENDENCY_LLM, DEPENDENCY_NEO4J}),
)
