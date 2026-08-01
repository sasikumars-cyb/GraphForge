"""API Intelligence Agent manifest — registered with the Orchestrator at
startup.

Standalone AI Workspace capability (goal `analyze_api_intelligence`), same
shape as the Documentation Agent (`app.agents.documentation`) — an
independently-invokable agent reached through `POST /agent-runs`, never a
stage inside the SDLC Workflow pipeline
(`app.services.workflow_service.STAGE_GOALS`, which this manifest is
deliberately not added to).

Phase 1 scope, enforced by this manifest rather than left to the agent's
own discipline: `max_graph_hops=0` and `DEPENDENCY_NEO4J` is deliberately
absent from `required_dependencies` — this agent reads only Markdown file
content (via the same repository clone every Markdown-consuming agent
already uses), never the indexed architecture graph and never source code.
"Do not analyze source code, do not scan repositories" from the agent's
own spec is a statement about *what it reads inside a clone*, not about
avoiding the clone step itself — cloning is how Markdown content is
fetched (see `app.agents.documentation.agent` for the identical pattern).
"""

from app.agents._contract import AgentManifest
from app.orchestrator.preflight import DEPENDENCY_LLM

API_INTELLIGENCE_MANIFEST = AgentManifest(
    agent_id="api_intelligence",
    purpose=(
        "Discover a repository's Markdown documentation, extract its "
        "documented API surface (endpoints, auth, rate limits, versioning), "
        "and produce a visual API catalog plus a security review — derived "
        "only from Markdown content, never source code or the graph."
    ),
    goals=frozenset({"analyze_api_intelligence"}),
    accepted_subject_types=frozenset({"repository"}),
    cost_class="standard",
    max_graph_hops=0,
    output_schema_name="ApiIntelligenceResult",
    # ADR 0011, OD-3 — LLM only. No DEPENDENCY_NEO4J: this agent never
    # reads the indexed graph (see module docstring).
    required_dependencies=frozenset({DEPENDENCY_LLM}),
)
