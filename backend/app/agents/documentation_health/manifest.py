"""Documentation Health Agent manifest — registered at startup.

Standalone AI Workspace capability (goal `analyze_documentation_health`),
the same shape as PR Review (`app.agents.review_adapter`): invoked
directly through `POST /agent-runs`, never a stage inside the SDLC
Workflow pipeline. It is deliberately absent from
`app.services.workflow_service.STAGE_GOALS`, which is what structurally
keeps it out of the workflow engine.

Read-only MVP. It never edits Markdown, commits, opens pull requests, or
modifies the repository in any way — there is no write path in this
package at all (see schemas.py: no field carries proposed content).
Distinct from the two neighbouring agents it is easily confused with:

- `documentation_planning` (goal `plan_documentation`) — a Workflow stage
  that plans documentation *work* as part of a Planning blueprint.
- `documentation_review` (goal `review_documentation`) — also a standalone
  Workspace capability, but it *proposes* Markdown changes and can open a
  PR. This agent measures health and stops there.

max_graph_hops=0 / no Neo4j dependency is intentional: every check in
`analysis.py` is derived from the repository's own files, which is what
keeps this MVP lightweight. Comparing documentation against the indexed
architecture graph is a listed future extension, and adding it here would
mean declaring DEPENDENCY_NEO4J at that point.
"""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM

DOCUMENTATION_HEALTH_MANIFEST = AgentManifest(
    agent_id="documentation_health",
    purpose=(
        "Discover a repository's Markdown documentation, measure its health "
        "against deterministic checks (missing README/architecture docs, empty "
        "and placeholder pages, duplicates, broken internal links, navigability, "
        "undocumented areas), and produce a scored Documentation Health Report. "
        "Read-only: never modifies the repository."
    ),
    goals=frozenset({"analyze_documentation_health"}),
    accepted_subject_types=frozenset({"repository"}),
    cost_class="cheap",
    max_graph_hops=0,
    output_schema_name="DocumentationHealthReport",
    # ADR 0011, OD-3 — LLM only (narrative prose around deterministic
    # findings); max_graph_hops=0 means no Neo4j dependency.
    required_dependencies=frozenset({DEPENDENCY_LLM}),
)
